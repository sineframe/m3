"""Shared, redacted MCP wire capture for harness-owned server groups.

The capture manager is deliberately a transport observer, not a JSON-RPC
client.  Stdio traffic is relayed by :mod:`stdio_proxy`; HTTP traffic is
forwarded by :class:`McpHttpProxy`; and loopback servers can attach the same
writer at their ASGI boundary.  The official harness and MCP protocol
engines remain responsible for framing, dispatch, retries, and callbacks.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..trace.capture import CaptureWriter, read_capture
from ..types import SecretReference, TransportKind
from .http_proxy import McpHttpProxy


def _typed_id(value: Any) -> tuple[type[Any], Any] | None:
    """Return a JSON-RPC identity without coercing ``7`` and ``"7"``."""

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return type(value), value


def _safe_payload(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _resolve_runtime_value(value: Any, *, secrets: set[str]) -> str:
    """Resolve an explicit MCP credential for a short-lived proxy handoff."""

    if isinstance(value, SecretReference):
        if value.source != "environment":
            raise ValueError("MCP credential resolution is unavailable")
        resolved = os.environ.get(value.name)
        if not resolved:
            raise ValueError("MCP credential is unavailable")
        secrets.add(resolved)
        return resolved
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("MCP configuration value is invalid")
    output = value
    start = 0
    while True:
        begin = output.find("${", start)
        if begin < 0:
            break
        end = output.find("}", begin + 2)
        if end < 0:
            break
        name = output[begin + 2 : end]
        if not name or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_" for character in name):
            raise ValueError("MCP configuration reference is invalid")
        resolved = os.environ.get(name)
        if not resolved:
            raise ValueError("MCP credential is unavailable")
        secrets.update((resolved, "${" + name + "}"))
        output = output[:begin] + resolved + output[end + 1 :]
        start = begin + len(resolved)
    return output


@dataclass(frozen=True, slots=True)
class McpWireEvent:
    """One redacted, decoded MCP exchange observed at a transport boundary."""

    connection_id: str
    transport: str
    direction: str
    kind: str
    offset_ms: float
    request_sequence: int | None = None
    jsonrpc_id: int | str | None = None
    method: str | None = None
    tool: str | None = None
    arguments: Mapping[str, Any] | None = None
    result: Any = None
    error: Any = None
    response_to_sequence: int | None = None
    latency_ms: float | None = None
    provenance: str = "wire_observed"
    raw_evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class McpCaptureSnapshot:
    """Immutable structured capture projection for an instrumented server."""

    connection_id: str
    transport: str
    events: tuple[McpWireEvent, ...] = ()
    complete: bool = False
    limitations: tuple[str, ...] = ()

    @property
    def tool_calls(self) -> tuple[McpWireEvent, ...]:
        return tuple(event for event in self.events if event.method == "tools/call")

    def evidence(self) -> dict[str, str | int | bool]:
        return {
            "capture_provenance": "wire_observed",
            "capture_transport": self.transport,
            "capture_connection_id": self.connection_id,
            "capture_event_count": len(self.events),
            "capture_tool_call_count": len(self.tool_calls),
            "capture_complete": self.complete,
        }


@dataclass(slots=True)
class _CaptureTarget:
    connection_id: str
    transport: str
    path: Path
    writer: CaptureWriter
    proxy: McpHttpProxy | None = None
    original_endpoint: str | None = None
    instrumented_endpoint: str | None = None


def _json_id(payload: Mapping[str, Any]) -> int | str | None:
    value = payload.get("id")
    return value if _typed_id(value) is not None else None


def _event_kind(payload: Mapping[str, Any], direction: str) -> str:
    if payload.get("error") is not None:
        return "error"
    if payload.get("method") is not None:
        return "request" if payload.get("id") is not None else "notification"
    return "response"


def _project_events(target: _CaptureTarget) -> tuple[McpWireEvent, ...]:
    records = read_capture(str(target.path))
    sequence = 0
    pending: dict[tuple[type[Any], Any, str], tuple[int, float, Mapping[str, Any], str | None]] = {}
    output: list[McpWireEvent] = []
    for index, record in enumerate(records, 1):
        payload = _safe_payload(record.get("payload")) or {}
        direction = str(record.get("direction", "unknown"))
        transport = str(record.get("transport", target.transport))
        offset = record.get("offset_ms", 0.0)
        offset_ms = float(offset) if isinstance(offset, (int, float)) else 0.0
        kind = _event_kind(payload, direction)
        ident = _json_id(payload)
        method = payload.get("method") if isinstance(payload.get("method"), str) else None
        params = _safe_payload(payload.get("params")) or {}
        tool = params.get("name") if isinstance(params.get("name"), str) else None
        raw_ref = f"{target.path.name}#{index}"
        request_sequence: int | None = None
        response_to: int | None = None
        latency: float | None = None
        if kind == "request" and ident is not None:
            sequence += 1
            request_sequence = sequence
            # Requests in the opposite direction are separate protocol roles.
            pending[(type(ident), ident, direction)] = (sequence, offset_ms, payload, raw_ref)
        elif kind in {"response", "error"} and ident is not None:
            opposite = "server_to_client" if direction == "client_to_server" else "client_to_server"
            pending_item = pending.pop((type(ident), ident, opposite), None)
            if pending_item is not None:
                response_to, started, request, _ = pending_item
                latency = max(0.0, offset_ms - started)
                request_method = request.get("method")
                method = request_method if isinstance(request_method, str) else method
                request_params = _safe_payload(request.get("params")) or {}
                request_tool = request_params.get("name")
                tool = request_tool if isinstance(request_tool, str) else tool
        result = payload.get("result")
        error = payload.get("error")
        arguments = params.get("arguments")
        output.append(
            McpWireEvent(
                connection_id=target.connection_id,
                transport=transport,
                direction=direction,
                kind=kind,
                offset_ms=offset_ms,
                request_sequence=request_sequence if request_sequence is not None else response_to,
                jsonrpc_id=ident,
                method=method,
                tool=tool,
                arguments=arguments if isinstance(arguments, Mapping) else None,
                result=result,
                error=error,
                response_to_sequence=response_to,
                latency_ms=latency,
                raw_evidence_ref=raw_ref,
            )
        )
    return tuple(output)


class McpCaptureManager:
    """Own one capture context and transport proxy set for a server group."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        baseline_ns: int | None = None,
        trusted_private_keys: Iterable[str] = (),
    ) -> None:
        self._owns_root = root is None
        self.root = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix="mcp-pal-capture-"))
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.baseline_ns = baseline_ns if baseline_ns is not None else time.perf_counter_ns()
        self._trusted_private = frozenset(trusted_private_keys)
        self._targets: dict[str, _CaptureTarget] = {}
        self._instrumented: dict[str, Any] = {}
        self._closed = False
        self._closed_snapshots: dict[str, McpCaptureSnapshot] = {}
        self._limitations: dict[str, tuple[str, ...]] = {}

    def _target(self, connection_id: str, transport: str) -> _CaptureTarget:
        target = self._targets.get(connection_id)
        if target is not None:
            return target
        safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in connection_id)
        path = self.root / f"{safe_name or uuid4().hex}.jsonl"
        target = _CaptureTarget(connection_id, transport, path, CaptureWriter(str(path), self.baseline_ns))
        self._targets[connection_id] = target
        return target

    async def instrument(self, configurations: Iterable[Any]) -> tuple[Any, ...]:
        """Return configs rewritten to use owned capture proxies.

        This method never starts an MCP protocol engine.  Stdio wrappers are
        launched later by the harness; HTTP proxies only forward requests.
        """

        output: list[Any] = []
        for configuration in configurations:
            if not getattr(configuration, "available", False):
                output.append(configuration)
                continue
            key = str(configuration.connection_id)
            transport = str(getattr(configuration.transport, "value", configuration.transport))
            target = self._target(key, transport)
            resolved_secrets: set[str] = set()
            if transport == TransportKind.STDIO.value:
                command = getattr(configuration, "command", None)
                if not isinstance(command, str) or not command:
                    output.append(configuration)
                    continue
                env_path = self.root / f"{key}.env.json"
                environment = getattr(configuration, "environment", {})
                safe_env = {
                    str(name): _resolve_runtime_value(value, secrets=resolved_secrets)
                    for name, value in dict(environment).items()
                    if isinstance(name, str)
                }
                descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
                    output_file.write(json.dumps(safe_env, separators=(",", ":")))
                target.writer.add_secrets(resolved_secrets)
                args: tuple[str, ...] = (
                    "-m", "mcp_pal.transport.stdio_proxy",
                    "--capture", str(target.path),
                    "--baseline", str(self.baseline_ns),
                    "--env-file", str(env_path),
                )
                cwd = getattr(configuration, "cwd", None)
                if isinstance(cwd, str) and cwd:
                    args = (*args, "--cwd", cwd)
                args += ("--", command, *tuple(getattr(configuration, "args", ())))
                self._instrumented[key] = replace(
                    configuration,
                    command=sys.executable,
                    args=args,
                    environment={},
                )
            elif transport in {TransportKind.STREAMABLE_HTTP.value, TransportKind.SSE.value}:
                endpoint = getattr(configuration, "endpoint", None)
                if not isinstance(endpoint, str) or not endpoint:
                    output.append(configuration)
                    continue
                headers = getattr(configuration, "headers", {})
                safe_headers = {
                    str(name): _resolve_runtime_value(value, secrets=resolved_secrets)
                    for name, value in dict(headers).items()
                    if isinstance(name, str)
                }
                resolved_endpoint = _resolve_runtime_value(endpoint, secrets=resolved_secrets)
                target.writer.add_secrets(resolved_secrets)
                proxy = McpHttpProxy(
                    upstream_url=resolved_endpoint,
                    configured_headers=safe_headers,
                    transport=transport,
                    capture_path=str(target.path),
                    baseline_ns=self.baseline_ns,
                    allow_private=key in self._trusted_private,
                )
                target.proxy = proxy
                target.original_endpoint = endpoint
                try:
                    target.instrumented_endpoint = await proxy.start()
                except Exception:
                    self._limitations[key] = ("capture_proxy_start_failed",)
                    raise
                self._instrumented[key] = replace(configuration, endpoint=target.instrumented_endpoint)
            else:
                self._instrumented[key] = configuration
            output.append(self._instrumented[key])
        return tuple(output)

    def writer_for(self, connection_id: str, transport: str = "stdio") -> CaptureWriter:
        """Return the redacting writer for a configured connection."""

        return self._target(connection_id, transport).writer

    def attach_loopback(self, connection_id: str, endpoint: Any, transport: str = "in_process") -> None:
        """Attach an existing loopback ASGI endpoint to a capture writer."""

        target = self._target(connection_id, transport)
        attach = getattr(endpoint, "attach_capture", None)
        if callable(attach):
            attach(target.writer)

    def snapshot(self, connection_id: str) -> McpCaptureSnapshot:
        cached = self._closed_snapshots.get(connection_id)
        if cached is not None:
            return cached
        target = self._targets.get(connection_id)
        if target is None:
            return McpCaptureSnapshot(connection_id, "unknown", limitations=("capture_not_started",))
        return McpCaptureSnapshot(
            connection_id,
            target.transport,
            _project_events(target),
            complete=self._closed,
            limitations=self._limitations.get(connection_id, ()),
        )

    def snapshots(self) -> tuple[McpCaptureSnapshot, ...]:
        return tuple(self.snapshot(connection_id) for connection_id in self._targets)

    def evidence(self) -> dict[str, str | int | bool]:
        snapshots = self.snapshots()
        return {
            "capture_provenance": "wire_observed",
            "capture_server_count": len(snapshots),
            "capture_event_count": sum(len(item.events) for item in snapshots),
            "capture_complete": self._closed,
        }

    async def close(self) -> None:
        if self._closed:
            return
        failures: list[BaseException] = []
        for target in tuple(self._targets.values()):
            if target.proxy is not None:
                try:
                    await target.proxy.stop()
                except BaseException as error:
                    failures.append(error)
        self._closed = True
        self._closed_snapshots = {
            connection_id: McpCaptureSnapshot(
                connection_id,
                target.transport,
                _project_events(target),
                complete=True,
                limitations=self._limitations.get(connection_id, ())
                + (("capture_root_removed",) if self._owns_root else ()),
            )
            for connection_id, target in self._targets.items()
        }
        if self._owns_root:
            shutil.rmtree(self.root, ignore_errors=True)
        else:
            # Explicit capture roots are caller-owned, but one-shot launch
            # handoffs are still ours to remove because they may contain
            # resolved credentials.
            for path in self.root.glob("*.env.json"):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        if failures:
            raise RuntimeError("MCP capture proxy cleanup failed") from None


__all__ = ["McpCaptureManager", "McpCaptureSnapshot", "McpWireEvent"]
