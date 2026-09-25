"""Native Codex App Server JSON-RPC harness adapter."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent_session import AdapterTurn
from ..elicitation import ElicitationPlan
from ..errors import ElicitationExpectationError, UnsupportedFeature
from ..interaction_handlers import PermissionRequest
from ..types import (
    Codex,
    ErrorCode,
    ErrorInfo,
    NativeToolPolicy,
    Readiness,
    SecretReference,
    TurnOutcome,
)
from ._codex_managed_mrtr import CodexManagedMRTRCoordinator
from ._codex_mrtr import CodexMRTRAction
from ._rpc_native import JsonRpcProcess, NativeRPCAdapter
from .contracts import (
    HarnessAdapterCapabilities,
    HarnessInteractionCapabilities,
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


@dataclass(frozen=True, slots=True)
class _NativeMcpToolItem:
    """Codex-owned item identity eligible for one on-request approval."""

    server: str
    arguments_json: str


def codex_configuration(launch: HarnessLaunch) -> dict[str, Any]:
    """Return the isolated App Server MCP configuration shape.

    Codex accepts stdio and Streamable HTTP servers.
    """
    servers: dict[str, dict[str, Any]] = {}
    for config in launch.configurations:
        if not config.available:
            if config.required:
                raise HarnessStartupError("required MCP server is unavailable")
            continue
        if config.transport.value == "stdio":
            if not config.command:
                raise HarnessStartupError("MCP server command is unavailable")
            protocol_marker = config.environment.get("CODEX_MCP_PROTOCOL_VERSION")
            if (
                protocol_marker is not None
                and not isinstance(protocol_marker, SecretReference)
                and _config_value(protocol_marker) != "2026-07-28"
            ):
                raise HarnessStartupError("Codex MCP protocol version is unsupported")
            literal_env = {
                k: _config_value(v)
                for k, v in config.environment.items()
                if not isinstance(v, SecretReference)
            }
            # All Codex stdio servers must use the modern protocol. An explicit
            # marker remains visible after capture instrumentation for this
            # check, while the real child gets the same value via handoff.
            if "CODEX_MCP_PROTOCOL_VERSION" not in config.environment:
                literal_env["CODEX_MCP_PROTOCOL_VERSION"] = "2026-07-28"
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
                "required": config.required,
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
                "required": config.required,
                **({"http_headers": literal_headers} if literal_headers else {}),
                **({"env_http_headers": env_headers} if env_headers else {}),
            }
    return {"features": {"mcp_2026_07_28": True}, "mcp_servers": servers}


def render_codex_config(launch: HarnessLaunch) -> str:
    """Render a minimal TOML config without embedding credential values."""
    lines = ["[features]", "mcp_2026_07_28 = true", "", "[mcp_servers]"]
    for name, server in codex_configuration(launch)["mcp_servers"].items():
        # TOML quoted keys preserve arbitrary valid server aliases verbatim.
        # json.dumps emits the required escapes for quotes, backslashes, and
        # control characters (and is valid TOML basic-string syntax).
        lines.append(f"\n[mcp_servers.{json.dumps(name)}]")
        lines.append(f"required = {str(server['required']).lower()}")
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


def _canonical_native_json(value: Any) -> str | None:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        return None


def _toml_inline(values: Mapping[str, Any]) -> str:
    return (
        "{ "
        + ", ".join(
            f"{json.dumps(str(key))} = {json.dumps(str(value))}"
            for key, value in values.items()
        )
        + " }"
    )


def _failed_action_turn(
    error: BaseException, *, prior: AdapterTurn | None = None
) -> AdapterTurn:
    details = getattr(error, "details", {})
    details = dict(details) if isinstance(details, Mapping) else {}
    reason = details.get("reason")
    if not isinstance(reason, str):
        reason = "elicitation_protocol_error"
    details.setdefault("reason", reason)
    return AdapterTurn(
        response=None,
        error=ErrorInfo(
            code=ErrorCode.PROTOCOL_ERROR,
            message="Codex could not complete action-bound elicitation",
            details=details,
        ),
        terminal=True,
        outcome=TurnOutcome.FAILED,
        tool_calls=prior.tool_calls if prior is not None else (),
        evidence=prior.evidence if prior is not None else {},
        trace_limitations=prior.trace_limitations if prior is not None else (),
        turn_evidence=prior.turn_evidence if prior is not None else None,
    )


class CodexHarnessAdapter(NativeRPCAdapter):
    """One persistent ``codex app-server`` process and thread."""

    harness_kind = "codex"
    executable_name = "codex"
    interaction_capabilities = HarnessInteractionCapabilities(retry_owner="harness")

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
        self._write_lock = asyncio.Lock()
        self._active_mrtr_action: CodexMRTRAction | None = None
        self._managed_input_runtime: Any = None
        self._mrtr_capability_identity: (
            tuple[str, int | None, int | None, int | None, int | None] | None
        ) = None
        self._action_interrupt_sent = False
        self._unscoped_elicitation_failure = False
        self._unapproved_mcp_tool_items: dict[str, _NativeMcpToolItem] = {}
        self._approved_mcp_tool_items: dict[str, _NativeMcpToolItem] = {}
        self._mcp_protocol_markers: dict[str, str] = {}

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        # AgentSession checks capabilities before preflight, so establish the
        # version gate lazily at the same point the declaration is inspected.
        self._ensure_mrtr_capability()
        return self._capabilities

    def _executable_identity(
        self,
    ) -> tuple[str, int | None, int | None, int | None, int | None]:
        executable = self.executable
        resolved = (
            executable
            if os.path.isabs(executable)
            else shutil.which(executable) or executable
        )
        try:
            stat = os.stat(resolved)
        except OSError:
            return (resolved, None, None, None, None)
        return (
            resolved,
            stat.st_dev,
            stat.st_ino,
            stat.st_mtime_ns,
            stat.st_size,
        )

    def _ensure_mrtr_capability(self) -> None:
        executable_identity = self._executable_identity()
        if self._mrtr_capability_identity == executable_identity:
            return
        version = probe_help(self.executable, ("--version",))
        version_line = version.splitlines()[0].strip() if version else ""
        supported = version_line == "codex-cli 0.156.1"
        interaction = (
            HarnessInteractionCapabilities(
                supports_elicitation=True,
                preserves_request_keys=True,
                preserves_multi_request_rounds=True,
                supports_interaction_cancellation=True,
                supports_interaction_resume=False,
                supports_idempotent_response_delivery=False,
                retry_owner="harness",
            )
            if supported
            else HarnessInteractionCapabilities(retry_owner="harness")
        )
        self._capabilities = replace(self._capabilities, interaction=interaction)
        self._mrtr_capability_identity = executable_identity

    def stdio_environment_defaults(self, spec: Any) -> dict[str, dict[str, str]]:
        """Supply Codex's modern MCP marker to captured stdio children.

        ServerGroupManager applies these only when the matching server config
        does not already define the variable. Explicit literal and secret
        references therefore retain their normal precedence.
        """

        defaults: dict[str, dict[str, str]] = {}
        for binding in getattr(spec, "servers", ()):
            server = getattr(binding, "server", None)
            if server is not None and getattr(server, "kind", None) != "stdio":
                continue
            key = (
                getattr(binding, "alias", None)
                or getattr(server, "name", None)
                or "profile"
            )
            if isinstance(key, str) and key:
                defaults[key] = {"CODEX_MCP_PROTOCOL_VERSION": "2026-07-28"}
        return defaults

    def _set_managed_input_runtime(self, runtime: Any) -> None:
        """Bind the controller-owned managed-input runtime to this adapter."""

        self._managed_input_runtime = runtime

    async def send(
        self,
        message: Any,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
        elicitation: ElicitationPlan | None = None,
        elicitation_round_limit: int = 10,
    ) -> AdapterTurn:
        """Bind planned answers to one turn while Codex owns retries."""

        if self._session is None or self._launch is None:
            raise HarnessStartupError("Codex App Server session is unavailable")
        if (
            elicitation is not None or self._managed_input_runtime is not None
        ) and not self.capabilities.interaction.supports_elicitation:
            raise UnsupportedFeature(
                "Codex MCP elicitation requires the characterized Codex 0.156.1 App Server"
            )
        managed_coordinator = (
            CodexManagedMRTRCoordinator(self._managed_input_runtime)
            if self._managed_input_runtime is not None
            else None
        )
        action: CodexMRTRAction | None = None
        if elicitation is not None or managed_coordinator is not None:
            action = CodexMRTRAction(
                launch=self._launch,
                plan=elicitation,
                round_limit=elicitation_round_limit,
                thread_id=lambda: self._thread_id,
                turn_id=lambda: self._turn_id,
                write_native_response=lambda request_id, result: self._write_frame(
                    self._process,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": result,
                    },
                ),
                managed_round_handler=(
                    managed_coordinator.handle_round
                    if managed_coordinator is not None
                    else None
                ),
                managed_round_completed=(
                    managed_coordinator.round_completed
                    if managed_coordinator is not None
                    else None
                ),
                managed_round_abort=(
                    managed_coordinator.abort
                    if managed_coordinator is not None
                    else None
                ),
                protocol_error_for_server=self._mrtr_protocol_error_for_server,
            )
            try:
                await action.start()
            except BaseException as error:
                await action.close()
                return _failed_action_turn(error)
        self._active_mrtr_action = action
        self._action_interrupt_sent = False
        self._unscoped_elicitation_failure = False
        try:
            result = await super().send(message, timeout=timeout, metadata=metadata)
            if result.outcome is TurnOutcome.COMPLETED and action is not None:
                await action.finish()
            if action is not None and action.failure is not None:
                return _failed_action_turn(action.failure, prior=result)
            if self._unscoped_elicitation_failure:
                return _failed_action_turn(
                    ElicitationExpectationError(
                        "Codex requested elicitation without an action-bound plan",
                        details={"reason": "unexpected_elicitation"},
                    ),
                    prior=result,
                )
            return result
        finally:
            if action is not None:
                await action.close()
            if self._active_mrtr_action is action:
                self._active_mrtr_action = None

    async def _write_frame(
        self, process: JsonRpcProcess | None, payload: Mapping[str, Any]
    ) -> None:
        if process is None:
            raise HarnessStartupError("Codex App Server process is unavailable")
        async with self._write_lock:
            await process.write(payload)

    def process_argv(self, launch: HarnessLaunch) -> tuple[str, ...]:
        return ("app-server",)

    def environment_for_launch(
        self, launch: HarnessLaunch, root: Path
    ) -> Mapping[str, str]:
        home = root / "codex-home"
        home.mkdir(mode=0o700, exist_ok=True)
        config = home / "config.toml"
        config.write_text(
            "check_for_update_on_startup = false\n\n" + render_codex_config(launch),
            encoding="utf-8",
        )
        config.chmod(0o600)
        # Preserve an existing native ChatGPT login when no API-key mapping is
        # configured. The isolated home is temporary and cleaned with the
        # execution workspace; auth material never enters the serializable spec.
        if (
            isinstance(launch.spec.harness, Codex)
            and not launch.spec.harness.credential_references
        ):
            source_home = Path(
                os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
            )
            source_auth = source_home / "auth.json"
            target_auth = home / "auth.json"
            if source_auth.is_file():
                try:
                    if source_auth.is_symlink():
                        raise OSError
                    descriptor = os.open(
                        target_auth, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                    )
                    try:
                        with (
                            source_auth.open("rb") as source,
                            os.fdopen(descriptor, "wb") as destination,
                        ):
                            descriptor = -1
                            shutil.copyfileobj(source, destination)
                    finally:
                        if descriptor != -1:
                            os.close(descriptor)
                except (OSError, ValueError) as exc:
                    try:
                        target_auth.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise HarnessStartupError(
                        "Codex native login is unavailable"
                    ) from exc
        environment = dict(self.environment)
        runtime_secrets: set[str] = set()
        protocol_markers: dict[str, str] = {}
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
                    if key == "CODEX_MCP_PROTOCOL_VERSION":
                        if resolved != "2026-07-28":
                            raise HarnessStartupError(
                                "Codex MCP protocol version is unsupported"
                            )
                        protocol_markers[configuration.key] = resolved
                elif key == "CODEX_MCP_PROTOCOL_VERSION":
                    protocol_markers[configuration.key] = _config_value(value)
            for value in configuration.headers.values():
                if isinstance(value, SecretReference):
                    resolved = environment.get(value.name) or os.environ.get(value.name)
                    if not resolved:
                        raise HarnessStartupError("Codex MCP credential is unavailable")
                    environment[value.name] = resolved
                    runtime_secrets.add(resolved)
        self._runtime_secrets = runtime_secrets
        self._mcp_protocol_markers = protocol_markers
        add_secrets = getattr(launch.capture, "add_secrets", None)
        if callable(add_secrets) and runtime_secrets:
            add_secrets(runtime_secrets)
        return environment

    async def preflight(self, launch: HarnessLaunch) -> Readiness:
        self._ensure_mrtr_capability()
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
        await self._write_frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "m3", "version": "0.2"},
                    "capabilities": {},
                },
            },
        )
        while True:
            frame = await process.next(5.0)
            if frame is None or frame.get("__invalid_frame__"):
                raise HarnessStartupError("Codex App Server initialization failed")
            if frame.get("id") == self._request_id:
                break
            self._deferred_frames.append(frame)
        await self._write_frame(
            process, {"jsonrpc": "2.0", "method": "initialized", "params": {}}
        )
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
            # MCP tools require Codex's approval-capable mode even when MCP
            # Pal has already granted the selected tool policy. ``never``
            # makes the App Server reject otherwise valid tool calls.
            "approvalPolicy": "on-request",
            "sandbox": sandbox,
            "ephemeral": True,
        }
        await self._write_frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "thread/start",
                "params": params,
            },
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
        self._action_interrupt_sent = False
        self._unscoped_elicitation_failure = False
        self._observed_tool_call_ids.clear()
        self._streamed_item_ids.clear()
        self._unapproved_mcp_tool_items.clear()
        self._approved_mcp_tool_items.clear()
        text = "".join(
            block.text for block in request.message.content if hasattr(block, "text")
        )
        await self._write_frame(
            process,
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
            },
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
                    action = self._active_mrtr_action
                    if action is not None:
                        action.set_turn_identity()
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
                    if method == "item/started":
                        self._remember_unapproved_mcp_tool_item(item)
                    else:
                        self._finish_native_mcp_tool_item(item)
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
                                error_message=_tool_error_message(error),
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
                    if self._unscoped_elicitation_failure:
                        self._terminal_status = "failed"
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
        frame = await self._next_action_frame(process, timeout)
        if frame is None:
            return None
        if frame.get("method") == "mcpServer/elicitation/request":
            params = frame.get("params")
            params = params if isinstance(params, Mapping) else {}
            metadata = params.get("_meta")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            approval_item_id = self._native_approval_item(params)
            if approval_item_id is not None:
                # Codex App Server tool approvals are separate from MCP MRTR.
                # Only a Codex-owned, not-yet-approved mcpToolCall item may
                # consume one approval decision. MCP servers can copy the
                # approval marker into inputRequired metadata, so the marker
                # alone is never evidence of an approval request.
                approval_item = self._unapproved_mcp_tool_items.pop(approval_item_id)
                self._approved_mcp_tool_items[approval_item_id] = approval_item
                await self._answer_mcp_elicitation(process, frame)
            elif self._active_mrtr_action is not None:
                # The action coordinator batches all native prompts for one
                # observed keyed round before writing any response. Returning
                # immediately keeps the App Server reader live while it waits.
                self._reject_incompatible_mrtr_server(params)
                self._active_mrtr_action.submit_native_prompt(frame)
            else:
                self._unscoped_elicitation_failure = True
        if self._unscoped_elicitation_failure:
            await self._interrupt_turn(process)
        action = self._active_mrtr_action
        if (
            action is not None
            and action.failure is not None
            and action.requires_turn_interrupt
        ):
            await self._interrupt_turn(process)
        return frame

    def _remember_unapproved_mcp_tool_item(self, item: Mapping[str, Any]) -> None:
        item_id = item.get("id")
        server = item.get("server")
        tool = item.get("tool")
        arguments = item.get("arguments", item.get("input"))
        arguments_json = (
            _canonical_native_json(arguments)
            if isinstance(arguments, Mapping)
            else None
        )
        if (
            isinstance(item_id, str)
            and isinstance(server, str)
            and isinstance(tool, str)
            and arguments_json is not None
        ):
            self._unapproved_mcp_tool_items[item_id] = _NativeMcpToolItem(
                server, arguments_json
            )

    def _finish_native_mcp_tool_item(self, item: Mapping[str, Any]) -> None:
        item_id = item.get("id")
        if isinstance(item_id, str):
            self._unapproved_mcp_tool_items.pop(item_id, None)
            self._approved_mcp_tool_items.pop(item_id, None)

    def _native_approval_item(self, params: Mapping[str, Any]) -> str | None:
        metadata = params.get("_meta")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("codex_approval_kind") != "mcp_tool_call"
            or params.get("threadId") != self._thread_id
            or params.get("turnId") != self._turn_id
        ):
            return None
        server = params.get("serverName")
        tool_params = metadata.get("tool_params")
        tool_params_json = (
            _canonical_native_json(tool_params)
            if isinstance(tool_params, Mapping)
            else None
        )
        if not isinstance(server, str) or tool_params_json is None:
            return None

        # An item remains active while Codex waits on its tool server. If it
        # already received its one approval, a later same-server prompt may be
        # a server-originated inputRequired response with forged metadata. A
        # second concurrent item on that server is ambiguous without an
        # app-server correlation field, so fail closed instead of borrowing
        # the first item's approval authority. `thread/start` sets
        # approvalPolicy=on-request; Codex 0.156.1 emits item/started before
        # that item's approval prompt and does not dispatch tools/call until
        # the approval response. Thus the unique unapproved item is the
        # Codex-owned pre-invocation state; a server cannot originate its
        # inputRequired prompt until after this one-shot decision is consumed.
        if any(
            item.server == server for item in self._approved_mcp_tool_items.values()
        ):
            return None
        matches = [
            item_id
            for item_id, item in self._unapproved_mcp_tool_items.items()
            if item.server == server and item.arguments_json == tool_params_json
        ]
        return matches[0] if len(matches) == 1 else None

    def _reject_incompatible_mrtr_server(self, params: Mapping[str, Any]) -> None:
        action = self._active_mrtr_action
        server_name = params.get("serverName")
        if action is None or not isinstance(server_name, str):
            return
        error = self._mrtr_protocol_error_for_server(server_name)
        if error is not None:
            action.fail(error)

    def _mrtr_protocol_error_for_server(
        self, server_name: str
    ) -> ElicitationExpectationError | None:
        if self._launch is None:
            return None
        configuration = next(
            (
                config
                for config in self._launch.configurations
                if config.key == server_name and config.available
            ),
            None,
        )
        if configuration is None or configuration.transport.value != "stdio":
            return None
        marker = configuration.environment.get("CODEX_MCP_PROTOCOL_VERSION")
        if isinstance(marker, SecretReference):
            value = self._mcp_protocol_markers.get(server_name)
        else:
            value = _config_value(marker) if marker is not None else "2026-07-28"
        if value != "2026-07-28":
            return ElicitationExpectationError(
                "planned Codex elicitation targets a server using the legacy MCP protocol",
                details={
                    "reason": "legacy_mcp_protocol",
                    "server": server_name,
                },
            )
        return None

    async def _next_action_frame(
        self, process: JsonRpcProcess, timeout: float | None
    ) -> Mapping[str, Any] | None:
        action = self._active_mrtr_action
        if self._deferred_frames:
            frame = self._deferred_frames.pop(0)
            if action is not None:
                self._observe_native_tool_item(action, frame)
            return frame
        if action is None:
            return await process.next(timeout)
        if action.failure is not None and not action.requires_turn_interrupt:
            return await process.next(timeout)
        frame_task = asyncio.create_task(process.next(timeout))
        failure_task = asyncio.create_task(action.failure_event.wait())
        try:
            done, _ = await asyncio.wait(
                (frame_task, failure_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failure_task in done and action.failure is not None:
                if action.requires_turn_interrupt:
                    await self._interrupt_turn(process)
                    done_after_interrupt, _ = await asyncio.wait(
                        (frame_task,), timeout=5.0
                    )
                    if not done_after_interrupt:
                        await process.close()
                        return None
                    native_frame = frame_task.result()
                else:
                    native_frame = await frame_task
            else:
                native_frame = await frame_task
            if native_frame is not None:
                self._observe_native_tool_item(action, native_frame)
            return native_frame
        finally:
            for task in (frame_task, failure_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(frame_task, failure_task, return_exceptions=True)

    @staticmethod
    def _observe_native_tool_item(
        action: CodexMRTRAction, frame: Mapping[str, Any]
    ) -> None:
        method = frame.get("method")
        if method not in {"item/started", "item/completed"}:
            return
        params = frame.get("params")
        item = params.get("item") if isinstance(params, Mapping) else None
        if isinstance(item, Mapping) and item.get("type") == "mcpToolCall":
            if method == "item/started":
                action.observe_native_tool_start(item)
            else:
                action.observe_native_tool_item(item)

    async def _answer_mcp_elicitation(
        self, process: JsonRpcProcess, frame: Mapping[str, Any]
    ) -> None:
        request_id = frame.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            raise HarnessStartupError("Codex MCP elicitation identity is invalid")
        params = frame.get("params")
        params = params if isinstance(params, Mapping) else {}
        metadata = params.get("_meta")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        server_name = params.get("serverName")
        turn_id = params.get("turnId")
        launch = self._launch
        allowed = False
        if (
            launch is not None
            and launch.interactions is not None
            and metadata.get("codex_approval_kind") == "mcp_tool_call"
            and isinstance(server_name, str)
            and any(
                config.key == server_name and config.available
                for config in launch.configurations
            )
            and params.get("threadId") == self._thread_id
            and (turn_id is None or turn_id == self._turn_id)
        ):
            permission = await launch.interactions.permission(
                PermissionRequest("mcp_tool", server_name)
            )
            allowed = permission.allowed
        await self._write_frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "action": "accept" if allowed else "decline",
                    **({"content": {}} if allowed else {}),
                },
            },
        )

    async def _cancel(self, process: JsonRpcProcess) -> None:
        self._cancel_requested = True
        self._terminal_status = "cancelled"
        try:
            await self._interrupt_turn(process)
            if self._action_interrupt_sent:
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

    async def _interrupt_turn(self, process: JsonRpcProcess) -> None:
        if not self._thread_id or not self._turn_id or self._action_interrupt_sent:
            return
        self._request_id += 1
        await self._write_frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "turn/interrupt",
                "params": {"threadId": self._thread_id, "turnId": self._turn_id},
            },
        )
        self._action_interrupt_sent = True


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


def _tool_error_message(error: Any) -> str | None:
    if isinstance(error, str):
        return error
    if isinstance(error, Mapping):
        message = error.get("message")
        return message if isinstance(message, str) else None
    return None


__all__ = ["CodexHarnessAdapter", "codex_configuration", "render_codex_config"]
