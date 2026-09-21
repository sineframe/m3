"""Native Pi RPC harness adapter.

Pi has no portable MCP configuration surface.  The adapter therefore loads a
private bundled extension which proxies MCP tools into Pi RPC tools; Pi itself
remains a first-class native harness, not an ACP wrapper.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..types import (
    NativeToolPolicy,
    Pi,
    Readiness,
    SecretReference,
)
from ._rpc_native import JsonRpcProcess, NativeRPCAdapter
from .contracts import (
    HarnessLaunch,
    HarnessSession,
    HarnessStartupError,
    HarnessTurnRequest,
)
from .native import probe_help
from .observations import (
    HarnessObservation,
    MessageChunkObservation,
    MetadataObservedObservation,
    ReasoningChunkObservation,
    ToolCallObservedObservation,
    ToolResultObservedObservation,
    UsageObservedObservation,
)
from .pi_extension.bridge import qualified_tool_name


class PiHarnessAdapter(NativeRPCAdapter):
    """One persistent ``pi --mode rpc`` conversation."""

    harness_kind = "pi"
    executable_name = "pi"

    def __init__(
        self, *, executable: str = "pi", environment: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(executable=executable, environment=environment)
        self._provider: str | None = None
        self._prompt_id = 0
        self._tool_identities: dict[str, tuple[str, str]] = {}
        self._launch_environment: dict[str, str] | None = None
        self._session_metadata: dict[str, str] = {}
        self._prompt_wire_id: str | None = None
        self._stop_reason: str | None = None
        self._tool_map_path: str | None = None
        self._tool_servers: set[str] = set()

    def process_argv(self, launch: HarnessLaunch) -> tuple[str, ...]:
        harness = launch.spec.harness
        extension = Path(__file__).with_name("pi_extension") / "extension.ts"
        argv = [
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(extension),
        ]
        if isinstance(harness, Pi):
            if harness.provider:
                argv.extend(("--provider", harness.provider))
            argv.extend(("--model", harness.model))
        return tuple(argv)

    async def preflight(self, launch: HarnessLaunch) -> Readiness:
        ready = await super().preflight(launch)
        if not ready.ready:
            return ready
        help_text = await asyncio.to_thread(probe_help, self.executable, ("--help",))
        if help_text is None or "rpc" not in help_text.lower():
            return Readiness(ready=False, reason="Pi RPC capability is unavailable")
        self._tool_identities = {}
        self._tool_servers = set()
        for configuration in launch.configurations:
            if configuration.available:
                self._tool_servers.add(configuration.key)
                for tool in configuration.tools:
                    self._tool_identities[
                        qualified_tool_name(configuration.key, tool)
                    ] = (configuration.key, tool)
        for record in launch.servers.records:
            if record.available:
                self._tool_servers.add(record.key)
                for tool in record.tools:
                    self._tool_identities[qualified_tool_name(record.key, tool)] = (
                        record.key,
                        tool,
                    )
        # Server startup may not have populated its initial tool inventory yet
        # (notably for remote HTTP MCP). A restrictive policy is still an
        # explicit advertised identity, so retain it as a deterministic bridge
        # mapping until the catalog is available.
        for allowed in getattr(launch.tool_policy, "allowed_tools", ()):
            if isinstance(allowed, str) and allowed.count(":") == 1:
                server, tool = allowed.split(":", 1)
                self._tool_identities[qualified_tool_name(server, tool)] = (
                    server,
                    tool,
                )
        if (
            isinstance(launch.tool_policy, NativeToolPolicy)
            and launch.tool_policy.harness != "pi"
        ):
            return Readiness(ready=False, reason="Pi native tool policy is unsupported")
        harness = launch.spec.harness
        if isinstance(harness, Pi):
            self._provider = harness.provider
            for reference in harness.credential_references.values():
                if (
                    not isinstance(reference, SecretReference)
                    or reference.source != "environment"
                ):
                    return Readiness(
                        ready=False, reason="Pi credential reference is invalid"
                    )
            model = harness.model
            if (
                "/" in model
                and harness.provider
                and model.split("/", 1)[0] != harness.provider
            ):
                return Readiness(
                    ready=False, reason="Pi provider and model do not agree"
                )
        return ready

    async def open(self, launch: HarnessLaunch) -> HarnessSession:
        # The extension spawns exactly this package-owned bridge command. MCP
        # Pal's capture layer has already instrumented these descriptors.
        base_environment = dict(self.environment)
        runtime_secrets: set[str] = set()
        config: dict[str, Any] = {}
        for item in launch.configurations:
            if not item.available:
                if item.required:
                    raise HarnessStartupError("required MCP server is unavailable")
                continue
            if item.transport.value == "stdio":
                values = {
                    k: _runtime_value(v, base_environment)
                    for k, v in item.environment.items()
                    if _runtime_value(v, base_environment) is not None
                }
                for key, value in values.items():
                    if (
                        isinstance(item.environment.get(key), SecretReference)
                        and value is not None
                    ):
                        runtime_secrets.add(value)
                config[item.key] = {
                    "transport": "stdio",
                    "command": item.command,
                    "args": list(item.args),
                    "cwd": item.cwd,
                    "env": values,
                }
            else:
                headers = {
                    k: _runtime_value(v, base_environment)
                    for k, v in item.headers.items()
                    if _runtime_value(v, base_environment) is not None
                }
                for key, value in headers.items():
                    if (
                        isinstance(item.headers.get(key), SecretReference)
                        and value is not None
                    ):
                        runtime_secrets.add(value)
                endpoint = _runtime_value(item.endpoint, base_environment)
                if isinstance(item.endpoint, SecretReference) and endpoint is not None:
                    runtime_secrets.add(endpoint)
                config[item.key] = {
                    "transport": item.transport.value,
                    "url": endpoint,
                    "headers": headers,
                }
        environment = dict(self.environment)
        harness = launch.spec.harness
        if isinstance(harness, Pi):
            for target, reference in harness.credential_references.items():
                if (
                    not isinstance(reference, SecretReference)
                    or reference.source != "environment"
                ):
                    raise HarnessStartupError("Pi credential reference is invalid")
                value = environment.get(reference.name) or os.environ.get(
                    reference.name
                )
                if not value:
                    raise HarnessStartupError("Pi credential is unavailable")
                environment[target] = value
                runtime_secrets.add(value)
        environment["M3_PI_BRIDGE_COMMAND"] = sys.executable
        environment["M3_PI_BRIDGE_ARGV"] = json.dumps(
            [str(Path(__file__).with_name("pi_extension") / "bridge.py")]
        )
        environment["M3_MCP_CONFIG"] = json.dumps(config, separators=(",", ":"))
        self._launch_environment = environment
        self._runtime_secrets = runtime_secrets
        add_secrets = getattr(launch.capture, "add_secrets", None)
        if callable(add_secrets) and runtime_secrets:
            add_secrets(runtime_secrets)
        return await super().open(launch)

    def environment_for_launch(
        self, launch: HarnessLaunch, root: Path
    ) -> Mapping[str, str]:
        del launch
        path = root / "pi-tool-map.json"
        flags = os.O_CREAT | os.O_TRUNC | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self._tool_map_path = str(path)
        environment = dict(self._launch_environment or self.environment)
        environment["PI_SKIP_VERSION_CHECK"] = "1"
        environment["PI_TELEMETRY"] = "0"
        environment["M3_PI_TOOL_MAP"] = str(path)
        self._launch_environment = environment
        return environment

    async def initialize(self, process: JsonRpcProcess, launch: HarnessLaunch) -> str:
        # Pi emits session_start/state events without requiring a JSON-RPC
        # initialize request. Ask for state when supported, but tolerate older
        # versions that only begin after the first prompt.
        await process.write({"type": "get_state"})
        deadline = asyncio.get_running_loop().time() + 5.0
        frame: Mapping[str, Any] | None = None
        while asyncio.get_running_loop().time() < deadline:
            remaining = max(0.01, deadline - asyncio.get_running_loop().time())
            try:
                candidate = await process.next(remaining)
            except asyncio.TimeoutError:
                raise HarnessStartupError("Pi state response timed out") from None
            if not isinstance(candidate, Mapping):
                raise HarnessStartupError("Pi state response was unavailable")
            if candidate.get("type") != "response":
                # Startup notifications such as session_start and extension
                # events may precede the correlated get_state response.
                continue
            if candidate.get("command") != "get_state":
                continue
            if candidate.get("success") is False:
                raise HarnessStartupError("Pi get_state request failed")
            frame = candidate
            break
        if frame is None:
            raise HarnessStartupError("Pi state response timed out")
        raw_data = frame.get("data")
        data: Mapping[str, Any] = raw_data if isinstance(raw_data, Mapping) else {}
        session = data.get("sessionId")
        if not isinstance(session, str) or not session:
            raise HarnessStartupError("Pi session identity was unavailable")
        self._session_metadata = {"session_id": session}
        harness = launch.spec.harness
        if isinstance(harness, Pi):
            if harness.provider:
                self._session_metadata["provider"] = harness.provider
            self._session_metadata["model"] = harness.model
        model = data.get("model")
        if isinstance(model, Mapping):
            provider_id = model.get("provider")
            model_id = model.get("id")
            if isinstance(provider_id, str) and provider_id:
                self._session_metadata["provider"] = provider_id
            if isinstance(model_id, str) and model_id:
                self._session_metadata["model"] = model_id
        for source, target in (("provider", "provider"), ("model", "model")):
            value = data.get(source)
            if isinstance(value, str) and value:
                self._session_metadata[target] = value
        return session

    def initial_observations(
        self, sequence: int, wall: datetime, started: float
    ) -> tuple[HarnessObservation, ...]:
        return tuple(
            MetadataObservedObservation(
                observation_id=f"pi-{sequence}-init-{name}",
                harness_kind="pi",
                turn_sequence=sequence,
                wall_time=wall,
                monotonic_offset_ms=max(
                    0.0, (asyncio.get_event_loop().time() - started) * 1000
                ),
                name=name,
                value=value,
            )
            for name, value in self._session_metadata.items()
        )

    async def send_turn(
        self, process: JsonRpcProcess, request: HarnessTurnRequest, sequence: int
    ) -> None:
        text = "".join(
            block.text for block in request.message.content if hasattr(block, "text")
        )
        self._prompt_id += 1
        self._stop_reason = None
        self._prompt_wire_id = f"prompt-{self._prompt_id}"
        await process.write(
            {"type": "prompt", "id": self._prompt_wire_id, "message": text}
        )

    def consume_frame(
        self,
        frame: Mapping[str, Any],
        sequence: int,
        wall: datetime,
        started: float,
        observations: list[HarnessObservation],
    ) -> tuple[bool, str, list[Mapping[str, Any]]]:
        typ = str(frame.get("type") or frame.get("event") or "")
        payload = frame.get("data", frame)
        if not isinstance(payload, Mapping):
            payload = frame
        text = ""
        calls: list[Mapping[str, Any]] = []
        if typ == "message_update":
            raw_usage = frame.get("usage")
            usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
            if usage:
                observations.append(
                    UsageObservedObservation(
                        observation_id=f"pi-{sequence}-usage-{len(observations)}",
                        harness_kind="pi",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        **_usage_kwargs(usage),
                    )
                )
        if frame.get("type") == "response" and frame.get("command") == "prompt":
            if (
                self._prompt_wire_id is not None
                and frame.get("id") != self._prompt_wire_id
            ):
                return False, "", calls
            if frame.get("success") is False:
                raise HarnessStartupError("Pi prompt request failed")
            return False, "", calls
        event = payload.get("assistantMessageEvent", payload.get("event"))
        if isinstance(event, Mapping):
            event_type = str(event.get("type") or "")
            if event_type in {"text_delta", "text"}:
                value = event.get("delta", event.get("text", ""))
                if isinstance(value, str):
                    text = value
                    observations.append(
                        MessageChunkObservation(
                            observation_id=f"pi-{sequence}-message-{len(observations)}",
                            harness_kind="pi",
                            turn_sequence=sequence,
                            wall_time=wall,
                            monotonic_offset_ms=max(
                                0.0, (asyncio.get_event_loop().time() - started) * 1000
                            ),
                            text=value,
                            complete=event_type == "text",
                        )
                    )
            elif event_type in {"thinking_delta", "reasoning_delta"}:
                value = event.get("delta", event.get("text"))
                if isinstance(value, str):
                    observations.append(
                        ReasoningChunkObservation(
                            observation_id=f"pi-{sequence}-reasoning-{len(observations)}",
                            harness_kind="pi",
                            turn_sequence=sequence,
                            wall_time=wall,
                            monotonic_offset_ms=max(
                                0.0, (asyncio.get_event_loop().time() - started) * 1000
                            ),
                            text=value,
                            complete=False,
                        )
                    )
            # toolcall_* events only build the assistant's content block. The
            # actual invocation/result arrives as top-level execution events;
            # do not report a duplicate call or mistake toolcall_end for a
            # result.
            pass
        elif typ == "message_update":
            value = payload.get("delta", payload.get("text"))
            if isinstance(value, str):
                text = value
                observations.append(
                    MessageChunkObservation(
                        observation_id=f"pi-{sequence}-message-{len(observations)}",
                        harness_kind="pi",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        text=value,
                        complete=False,
                    )
                )
        elif typ in {"session_start", "state", "agent_start"}:
            for key in ("session_id", "sessionId", "provider", "model"):
                if key in payload:
                    observations.append(
                        MetadataObservedObservation(
                            observation_id=f"pi-{sequence}-meta-{key}",
                            harness_kind="pi",
                            turn_sequence=sequence,
                            wall_time=wall,
                            monotonic_offset_ms=max(
                                0.0, (asyncio.get_event_loop().time() - started) * 1000
                            ),
                            name=key,
                            value=_json(payload[key]),
                        )
                    )
        elif typ in {"tool_execution_start", "tool_execution_end"}:
            call_id = str(payload.get("toolCallId") or "pi-call")
            name = str(payload.get("toolName") or "mcp_tool")
            if typ == "tool_execution_start":
                args = payload.get("args")
                server, tool = self._tool_identity(name)
                calls.append(
                    {
                        "call_id": call_id,
                        "server": server,
                        "tool": tool,
                        "qualified_name": name,
                        "arguments": args,
                    }
                )
                observations.append(
                    ToolCallObservedObservation(
                        observation_id=f"pi-{sequence}-tool-{len(observations)}",
                        harness_kind="pi",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        call_id=call_id,
                        server=server,
                        tool=tool,
                        arguments=_json(args),
                    )
                )
            else:
                result = payload.get("result")
                is_error = bool(payload.get("isError", False))
                observations.append(
                    ToolResultObservedObservation(
                        observation_id=f"pi-{sequence}-tool-result-{len(observations)}",
                        harness_kind="pi",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        call_id=call_id,
                        result=_json(result),
                        is_error=is_error,
                        status="tool_error" if is_error else "success",
                    )
                )
        elif typ in {"message_end", "turn_end"}:
            message = payload.get("message")
            stop_reason = (
                message.get("stopReason")
                if isinstance(message, Mapping)
                else payload.get("stopReason")
            )
            if isinstance(stop_reason, str) and stop_reason:
                self._stop_reason = stop_reason
                if (
                    stop_reason.lower() == "error"
                    and self._terminal_status != "cancelled"
                ):
                    # message_end/turn_end is authoritative for provider
                    # outcome; agent_settled is only the lifecycle boundary.
                    # Keep the error sanitized at the shared contract.
                    self._terminal_status = "failed"
        elif typ == "agent_settled":
            if self._stop_reason:
                observations.append(
                    MetadataObservedObservation(
                        observation_id=f"pi-{sequence}-meta-finish",
                        harness_kind="pi",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        name="finish_reason",
                        value=self._stop_reason,
                    )
                )
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                observations.append(
                    UsageObservedObservation(
                        observation_id=f"pi-{sequence}-usage",
                        harness_kind="pi",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        **_usage_kwargs(usage),
                    )
                )
            return True, text, calls
        return False, text, calls

    async def _cancel(self, process: JsonRpcProcess) -> None:
        self._cancel_requested = True
        self._terminal_status = "cancelled"
        try:
            await process.write({"type": "abort"})
            # A successful protocol abort preserves the persistent Pi
            # process, allowing the next prompt to continue the session.
            return
        except Exception:
            pass
        if process.owner is not None and process.owner.process is not None:
            try:
                await asyncio.wait_for(process.owner.process.wait(), timeout=0.25)
                return
            except asyncio.TimeoutError:
                pass
        await process.close()

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            self._launch_environment = None
            self._session_metadata.clear()
            if self._tool_map_path:
                try:
                    os.unlink(self._tool_map_path)
                except OSError:
                    pass
                self._tool_map_path = None

    def _tool_identity(self, value: str) -> tuple[str | None, str]:
        identity = self._tool_identities.get(value)
        if identity is not None:
            return identity
        if self._tool_map_path:
            try:
                flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(self._tool_map_path, flags)
                try:
                    raw = os.read(fd, 512 * 1024 + 1)
                finally:
                    os.close(fd)
                if len(raw) > 512 * 1024:
                    raise ValueError("tool map too large")
                mapping = json.loads(raw.decode("utf-8"))
                if not isinstance(mapping, dict):
                    raise ValueError("invalid tool map")
                for name, pair in mapping.items():
                    if (
                        isinstance(name, str)
                        and isinstance(pair, list)
                        and len(pair) == 2
                        and all(isinstance(item, str) for item in pair)
                        and pair[0] in self._tool_servers
                        and qualified_tool_name(pair[0], pair[1]) == name
                    ):
                        self._tool_identities[name] = (pair[0], pair[1])
            except (OSError, ValueError, TypeError, UnicodeDecodeError):
                pass
            identity = self._tool_identities.get(value)
            if identity is not None:
                return identity
        return _split_tool_name(value)


def _int(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _usage_kwargs(value: Mapping[str, Any]) -> dict[str, Any]:
    mapping = {
        "input_tokens": ("input", "input_tokens"),
        "output_tokens": ("output", "output_tokens"),
        "cache_read_tokens": ("cacheRead", "cache_read_tokens"),
        "cache_write_tokens": ("cacheWrite", "cache_write_tokens"),
        "total_tokens": ("totalTokens", "total", "total_tokens"),
    }
    return {
        target: _int(next((value.get(key) for key in keys if key in value), None))
        for target, keys in mapping.items()
        if any(key in value for key in keys)
    }


def _split_tool_name(value: str) -> tuple[str | None, str]:
    parts = value.split("__", 2)
    return (
        (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "mcp" else (None, value)
    )


def _runtime_value(value: Any, environment: Mapping[str, str]) -> str | None:
    if isinstance(value, SecretReference):
        return environment.get(value.name) or os.environ.get(value.name)
    if isinstance(value, str):
        return value
    return None


def _json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json(v) for v in value]
    return (
        value
        if value is None or isinstance(value, (str, int, float, bool))
        else {"capture": "unavailable"}
    )


__all__ = ["PiHarnessAdapter"]
