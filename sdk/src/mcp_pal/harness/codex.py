"""Native Codex App Server JSON-RPC harness adapter."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..types import (
    Capability,
    CapabilityStatus,
    Codex,
    NativeToolPolicy,
    Readiness,
    SecretReference,
)
from ._rpc_native import JsonRpcProcess, NativeRPCAdapter
from .contracts import (
    HarnessLaunch,
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


def codex_configuration(launch: HarnessLaunch) -> dict[str, Any]:
    """Return the isolated App Server MCP configuration shape.

    Codex accepts stdio and Streamable HTTP servers.  SSE is intentionally
    rejected because presenting it as supported would make policy/capture
    evidence misleading.
    """
    servers: dict[str, dict[str, Any]] = {}
    for config in launch.configurations:
        if not config.available:
            if config.required:
                raise HarnessStartupError("required MCP server is unavailable")
            continue
        if config.transport.value == "sse":
            raise HarnessStartupError(
                "Codex App Server does not support SSE MCP servers"
            )
        if config.transport.value == "stdio":
            if not config.command:
                raise HarnessStartupError("MCP server command is unavailable")
            literal_env = {
                k: _config_value(v)
                for k, v in config.environment.items()
                if not isinstance(v, SecretReference)
            }
            # Codex's env_vars names are the child-process target variables;
            # values are resolved into those names by environment_for_launch.
            env_vars = [
                name
                for name, value in config.environment.items()
                if isinstance(value, SecretReference)
            ]
            servers[config.key] = {
                "command": config.command,
                "args": list(config.args),
                **({"env": literal_env} if literal_env else {}),
                **({"env_vars": env_vars} if env_vars else {}),
            }
        else:
            if not config.endpoint:
                raise HarnessStartupError("MCP server endpoint is unavailable")
            literal_headers = {
                k: _config_value(v)
                for k, v in config.headers.items()
                if not isinstance(v, SecretReference)
            }
            env_headers = {
                k: v.name
                for k, v in config.headers.items()
                if isinstance(v, SecretReference)
            }
            servers[config.key] = {
                "url": _config_value(config.endpoint),
                **({"http_headers": literal_headers} if literal_headers else {}),
                **({"env_http_headers": env_headers} if env_headers else {}),
            }
    return {"mcp_servers": servers}


def render_codex_config(launch: HarnessLaunch) -> str:
    """Render a minimal TOML config without embedding credential values."""
    lines = ["[mcp_servers]"]
    for name, server in codex_configuration(launch)["mcp_servers"].items():
        # TOML quoted keys preserve arbitrary valid server aliases verbatim.
        # json.dumps emits the required escapes for quotes, backslashes, and
        # control characters (and is valid TOML basic-string syntax).
        lines.append(f"\n[mcp_servers.{json.dumps(name)}]")
        if "command" in server:
            lines.append(f"command = {json.dumps(server['command'])}")
            lines.append(f"args = {json.dumps(server.get('args', []))}")
            if server.get("env"):
                lines.append(f"env = {_toml_inline(server['env'])}")
            if server.get("env_vars"):
                lines.append(f"env_vars = {json.dumps(server['env_vars'])}")
        else:
            lines.append(f"url = {json.dumps(server['url'])}")
            if server.get("http_headers"):
                lines.append(f"http_headers = {_toml_inline(server['http_headers'])}")
            if server.get("env_http_headers"):
                lines.append(
                    f"env_http_headers = {_toml_inline(server['env_http_headers'])}"
                )
    return "\n".join(lines) + "\n"


def _config_value(value: Any) -> str:
    if isinstance(value, SecretReference):
        return "${" + value.name + "}"
    return str(value)


def _toml_inline(values: Mapping[str, Any]) -> str:
    return (
        "{ "
        + ", ".join(
            f"{json.dumps(str(key))} = {json.dumps(str(value))}"
            for key, value in values.items()
        )
        + " }"
    )


class CodexHarnessAdapter(NativeRPCAdapter):
    """One persistent ``codex app-server`` process and thread."""

    harness_kind = "codex"
    executable_name = "codex"

    def __init__(
        self, *, executable: str = "codex", environment: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(executable=executable, environment=environment)
        self._request_id = 0
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._pending_turn_request: int | None = None
        self._session_metadata: dict[str, str] = {}
        self._deferred_frames: list[Mapping[str, Any]] = []
        self._observed_tool_call_ids: set[str] = set()
        self._streamed_item_ids: set[str] = set()

    def process_argv(self, launch: HarnessLaunch) -> tuple[str, ...]:
        return ("app-server",)

    def environment_for_launch(
        self, launch: HarnessLaunch, root: Path
    ) -> Mapping[str, str]:
        home = root / "codex-home"
        home.mkdir(mode=0o700, exist_ok=True)
        config = home / "config.toml"
        config.write_text(render_codex_config(launch), encoding="utf-8")
        config.chmod(0o600)
        environment = dict(self.environment)
        runtime_secrets: set[str] = set()
        environment["CODEX_HOME"] = str(home)
        harness = launch.spec.harness
        if isinstance(harness, Codex):
            for target, reference in harness.credential_references.items():
                value = environment.get(reference.name) or os.environ.get(
                    reference.name
                )
                if not value:
                    raise HarnessStartupError("Codex credential is unavailable")
                environment[target] = value
                runtime_secrets.add(value)
        for configuration in launch.configurations:
            for key, value in configuration.environment.items():
                if isinstance(value, SecretReference):
                    resolved = environment.get(value.name) or os.environ.get(value.name)
                    if not resolved:
                        raise HarnessStartupError("Codex MCP credential is unavailable")
                    environment[key] = resolved
                    runtime_secrets.add(resolved)
            for value in configuration.headers.values():
                if isinstance(value, SecretReference):
                    resolved = environment.get(value.name) or os.environ.get(value.name)
                    if not resolved:
                        raise HarnessStartupError("Codex MCP credential is unavailable")
                    environment[value.name] = resolved
                    runtime_secrets.add(resolved)
        self._runtime_secrets = runtime_secrets
        add_secrets = getattr(launch.capture, "add_secrets", None)
        if callable(add_secrets) and runtime_secrets:
            add_secrets(runtime_secrets)
        return environment

    async def preflight(self, launch: HarnessLaunch) -> Readiness:
        ready = await super().preflight(launch)
        if not ready.ready:
            return ready
        help_text = await asyncio.to_thread(
            probe_help, self.executable, ("app-server", "--help")
        )
        if help_text is None or "app-server" not in help_text.lower():
            return Readiness(
                ready=False, reason="Codex App Server capability is unavailable"
            )
        if any(config.transport.value == "sse" for config in launch.configurations):
            return Readiness(
                ready=False,
                capabilities=(
                    Capability(
                        name="harness:codex",
                        status=CapabilityStatus.UNAVAILABLE,
                        reason="SSE MCP unsupported",
                    ),
                ),
                reason="Codex App Server does not support SSE MCP servers",
            )
        if (
            isinstance(launch.tool_policy, NativeToolPolicy)
            and launch.tool_policy.harness != "codex"
        ):
            return Readiness(
                ready=False, reason="Codex native tool policy is unsupported"
            )
        try:
            codex_configuration(launch)
        except HarnessStartupError as exc:
            return Readiness(ready=False, reason=str(exc))
        harness = launch.spec.harness
        if isinstance(harness, Codex):
            for reference in harness.credential_references.values():
                if (
                    not isinstance(reference, SecretReference)
                    or reference.source != "environment"
                ):
                    return Readiness(
                        ready=False, reason="Codex credential reference is invalid"
                    )
        return ready

    async def initialize(self, process: JsonRpcProcess, launch: HarnessLaunch) -> str:
        self._request_id = 1
        await process.write(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "mcp-pal", "version": "0.2"},
                    "capabilities": {},
                },
            }
        )
        while True:
            frame = await process.next(5.0)
            if frame is None or frame.get("__invalid_frame__"):
                raise HarnessStartupError("Codex App Server initialization failed")
            if frame.get("id") == self._request_id:
                break
            self._deferred_frames.append(frame)
        await process.write({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        self._request_id += 1
        workspace = launch.workspace_root or str(process.root or Path.cwd())
        sandbox = (
            "read-only"
            if getattr(launch.spec.workspace.kind, "value", "") == "read_only"
            else "workspace-write"
        )
        params = {
            "model": getattr(launch.spec.harness, "model", None),
            "cwd": workspace,
            "approvalPolicy": "never",
            "sandbox": sandbox,
            "ephemeral": True,
        }
        await process.write(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "thread/start",
                "params": params,
            }
        )
        while True:
            frame = await process.next(5.0)
            if frame is None or frame.get("__invalid_frame__"):
                raise HarnessStartupError("Codex thread could not be started")
            if frame.get("id") == self._request_id:
                result = frame.get("result")
                if isinstance(result, Mapping):
                    thread = result.get("thread", result)
                    if isinstance(thread, Mapping) and isinstance(
                        thread.get("id"), str
                    ):
                        self._thread_id = thread["id"]
                break
            self._deferred_frames.append(frame)
        if not self._thread_id:
            raise HarnessStartupError("Codex thread identity was unavailable")
        self._session_metadata = {
            "thread_id": self._thread_id,
            "model": str(params["model"])
            if isinstance(params.get("model"), str)
            else "",
            "sandbox": sandbox,
        }
        return self._thread_id

    def initial_observations(
        self, sequence: int, wall: datetime, started: float
    ) -> tuple[HarnessObservation, ...]:
        return tuple(
            MetadataObservedObservation(
                observation_id=f"codex-{sequence}-init-{name}",
                harness_kind="codex",
                turn_sequence=sequence,
                wall_time=wall,
                monotonic_offset_ms=max(
                    0.0, (asyncio.get_event_loop().time() - started) * 1000
                ),
                name=name,
                value=value,
            )
            for name, value in self._session_metadata.items()
            if value
        )

    async def send_turn(
        self, process: JsonRpcProcess, request: HarnessTurnRequest, sequence: int
    ) -> None:
        if not self._thread_id:
            raise HarnessStartupError("Codex thread is unavailable")
        self._request_id += 1
        self._pending_turn_request = self._request_id
        self._turn_id = None
        self._observed_tool_call_ids.clear()
        self._streamed_item_ids.clear()
        text = "".join(
            block.text for block in request.message.content if hasattr(block, "text")
        )
        await process.write(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "turn/start",
                "params": {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": text}],
                    "model": getattr(self._launch.spec.harness, "model", None)
                    if self._launch
                    else None,
                },
            }
        )

    def consume_frame(
        self,
        frame: Mapping[str, Any],
        sequence: int,
        wall: datetime,
        started: float,
        observations: list[HarnessObservation],
    ) -> tuple[bool, str, list[Mapping[str, Any]]]:
        if frame.get("error") is not None:
            raise HarnessStartupError("Codex App Server returned a protocol error")
        method = str(frame.get("method") or frame.get("type") or "")
        params = (
            frame.get("params") if isinstance(frame.get("params"), Mapping) else frame
        )
        text = ""
        calls: list[Mapping[str, Any]] = []
        if (
            self._pending_turn_request is not None
            and frame.get("id") == self._pending_turn_request
        ):
            result = frame.get("result")
            if isinstance(result, Mapping):
                turn = result.get("turn", result)
                if isinstance(turn, Mapping) and isinstance(turn.get("id"), str):
                    self._turn_id = turn["id"]
            else:
                raise HarnessStartupError("Codex turn/start response was invalid")
            self._pending_turn_request = None
            if frame.get("error") is not None:
                return True, "", calls
            return False, "", calls
        if method in {
            "item/agentMessage/delta",
            "agent_message_delta",
            "message_delta",
        }:
            delta = (
                params.get("delta", params.get("text", ""))
                if isinstance(params, Mapping)
                else ""
            )
            if isinstance(delta, str):
                if isinstance(params, Mapping):
                    item_id = params.get("itemId", params.get("item_id"))
                    if isinstance(item_id, str):
                        self._streamed_item_ids.add(item_id)
                text = delta
                observations.append(
                    MessageChunkObservation(
                        observation_id=f"codex-{sequence}-message-{len(observations)}",
                        harness_kind="codex",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        text=delta,
                        complete=False,
                    )
                )
        elif method in {
            "item/reasoning/textDelta",
            "item/reasoning/summaryTextDelta",
            "item/reasoning/delta",
        }:
            delta = (
                params.get("delta", params.get("text"))
                if isinstance(params, Mapping)
                else None
            )
            if isinstance(delta, str):
                observations.append(
                    ReasoningChunkObservation(
                        observation_id=f"codex-{sequence}-reasoning-{len(observations)}",
                        harness_kind="codex",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        text=delta,
                        complete=False,
                    )
                )
        elif method in {"item/completed", "item/started"}:
            item = params.get("item", params) if isinstance(params, Mapping) else {}
            if isinstance(item, Mapping):
                kind = str(item.get("type") or item.get("kind") or "")
                if kind == "mcpToolCall":
                    call_id = (
                        str(item.get("id"))
                        if isinstance(item.get("id"), str)
                        else "codex-call"
                    )
                    name = (
                        str(item.get("tool"))
                        if isinstance(item.get("tool"), str)
                        else "mcp_tool"
                    )
                    server = (
                        str(item.get("server"))
                        if isinstance(item.get("server"), str)
                        else None
                    )
                    args = item.get("arguments", item.get("input"))
                    if call_id not in self._observed_tool_call_ids:
                        calls.append(
                            {
                                "call_id": call_id,
                                "server": server,
                                "tool": name,
                                "arguments": args,
                            }
                        )
                        observations.append(
                            ToolCallObservedObservation(
                                observation_id=f"codex-{sequence}-tool-{len(observations)}",
                                harness_kind="codex",
                                turn_sequence=sequence,
                                wall_time=wall,
                                monotonic_offset_ms=max(
                                    0.0,
                                    (asyncio.get_event_loop().time() - started) * 1000,
                                ),
                                call_id=call_id,
                                server=server,
                                tool=name,
                                arguments=_json(args),
                            )
                        )
                        self._observed_tool_call_ids.add(call_id)
                    if method == "item/completed":
                        error = item.get("error")
                        status = str(item.get("status", "completed"))
                        is_error = status == "failed" or error is not None
                        result = item.get("result")
                        observations.append(
                            ToolResultObservedObservation(
                                observation_id=f"codex-{sequence}-tool-result-{len(observations)}",
                                harness_kind="codex",
                                turn_sequence=sequence,
                                wall_time=wall,
                                monotonic_offset_ms=max(
                                    0.0,
                                    (asyncio.get_event_loop().time() - started) * 1000,
                                ),
                                call_id=call_id,
                                result=_json(result),
                                is_error=is_error,
                                status="tool_error" if is_error else "success",
                                error_message=str(error)
                                if isinstance(error, str)
                                else None,
                            )
                        )
                else:
                    candidate = item.get("text", item.get("content"))
                    if isinstance(candidate, str):
                        item_id = item.get("id")
                        if (
                            method == "item/completed"
                            and isinstance(item_id, str)
                            and item_id in self._streamed_item_ids
                        ):
                            # The completed item repeats the already streamed
                            # aggregate text; it confirms completion only.
                            candidate = None
                        if candidate is not None:
                            text = candidate
                            observations.append(
                                MessageChunkObservation(
                                    observation_id=f"codex-{sequence}-message-{len(observations)}",
                                    harness_kind="codex",
                                    turn_sequence=sequence,
                                    wall_time=wall,
                                    monotonic_offset_ms=max(
                                        0.0,
                                        (asyncio.get_event_loop().time() - started)
                                        * 1000,
                                    ),
                                    text=candidate,
                                    complete=method == "item/completed",
                                )
                            )
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") if isinstance(params, Mapping) else None
            if isinstance(usage, Mapping):
                aggregate = usage.get("total")
                if not isinstance(aggregate, Mapping):
                    aggregate = usage
                observations.append(
                    UsageObservedObservation(
                        observation_id=f"codex-{sequence}-usage-{len(observations)}",
                        harness_kind="codex",
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (asyncio.get_event_loop().time() - started) * 1000
                        ),
                        **_usage_kwargs(aggregate),
                    )
                )
        elif method in {"turn/completed", "turn/complete", "turn_finished", "result"}:
            if isinstance(params, Mapping):
                turn = params.get("turn", params)
                if isinstance(turn, Mapping):
                    for key, value in (
                        ("thread_id", params.get("threadId", self._thread_id)),
                        ("turn_id", turn.get("id", self._turn_id)),
                    ):
                        if isinstance(value, str):
                            observations.append(
                                MetadataObservedObservation(
                                    observation_id=f"codex-{sequence}-meta-{key}",
                                    harness_kind="codex",
                                    turn_sequence=sequence,
                                    wall_time=wall,
                                    monotonic_offset_ms=max(
                                        0.0,
                                        (asyncio.get_event_loop().time() - started)
                                        * 1000,
                                    ),
                                    name=key,
                                    value=value,
                                )
                            )
                    for key in ("status", "finishReason", "model", "sessionId"):
                        value = turn.get(key)
                        if value is not None:
                            observations.append(
                                MetadataObservedObservation(
                                    observation_id=f"codex-{sequence}-meta-{key}",
                                    harness_kind="codex",
                                    turn_sequence=sequence,
                                    wall_time=wall,
                                    monotonic_offset_ms=max(
                                        0.0,
                                        (asyncio.get_event_loop().time() - started)
                                        * 1000,
                                    ),
                                    name=key,
                                    value=_json(value),
                                )
                            )
                    state = str(turn.get("status", "completed")).lower()
                    observations.append(
                        MetadataObservedObservation(
                            observation_id=f"codex-{sequence}-meta-finish",
                            harness_kind="codex",
                            turn_sequence=sequence,
                            wall_time=wall,
                            monotonic_offset_ms=max(
                                0.0, (asyncio.get_event_loop().time() - started) * 1000
                            ),
                            name="finish_reason",
                            value=state,
                        )
                    )
                    if state not in {"completed", "complete", "success"}:
                        self._terminal_status = (
                            "cancelled"
                            if state in {"cancelled", "canceled"}
                            else "interrupted"
                            if state in {"interrupted", "aborted"}
                            else "failed"
                        )
                    usage = turn.get("usage")
                    if isinstance(usage, Mapping):
                        usage_kwargs = _usage_kwargs(usage)
                        observations.append(
                            UsageObservedObservation(
                                observation_id=f"codex-{sequence}-usage",
                                harness_kind="codex",
                                turn_sequence=sequence,
                                wall_time=wall,
                                monotonic_offset_ms=max(
                                    0.0,
                                    (asyncio.get_event_loop().time() - started) * 1000,
                                ),
                                **usage_kwargs,
                            )
                        )
            return True, text, calls
        return False, text, calls

    async def next_frame(
        self, process: JsonRpcProcess, timeout: float | None
    ) -> Mapping[str, Any] | None:
        if self._deferred_frames:
            return self._deferred_frames.pop(0)
        return await process.next(timeout)

    async def _cancel(self, process: JsonRpcProcess) -> None:
        self._cancel_requested = True
        self._terminal_status = "cancelled"
        if self._thread_id and self._turn_id:
            try:
                self._request_id += 1
                await process.write(
                    {
                        "jsonrpc": "2.0",
                        "id": self._request_id,
                        "method": "turn/interrupt",
                        "params": {
                            "threadId": self._thread_id,
                            "turnId": self._turn_id,
                        },
                    }
                )
                # The interrupt is scoped to this turn; retain the app-server
                # process so later turns continue the same thread.
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


def _int(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _usage_kwargs(value: Mapping[str, Any]) -> dict[str, Any]:
    mapping = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_tokens": (
            "reasoning_tokens",
            "reasoningTokens",
            "reasoningOutputTokens",
        ),
        "cache_read_tokens": (
            "cache_read_tokens",
            "cacheReadTokens",
            "cachedInputTokens",
        ),
        "cache_write_tokens": (
            "cache_write_tokens",
            "cacheWriteTokens",
            "cacheWriteInputTokens",
        ),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    return {
        target: _int(next((value.get(key) for key in keys if key in value), None))
        for target, keys in mapping.items()
        if any(key in value for key in keys)
    }


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


__all__ = ["CodexHarnessAdapter", "codex_configuration", "render_codex_config"]
