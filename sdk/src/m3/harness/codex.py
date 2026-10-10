"""Native Codex App Server JSON-RPC harness adapter."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent_session import AdapterTurn
from ..elicitation import ElicitationPlan
from ..errors import ElicitationExpectationError, UnsupportedFeature
from ..interaction_handlers import PermissionRequest
from ..policy import ToolDescriptor
from ..trace.redaction import is_sensitive_key
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
    InteractionObservedObservation,
    MessageChunkObservation,
    MetadataObservedObservation,
    ReasoningChunkObservation,
    ToolCallObservedObservation,
    ToolResultObservedObservation,
    UsageObservedObservation,
)

# Covers Codex's own per-server MCP startup timeout (10 seconds by default).
_THREAD_START_TIMEOUT_SECONDS = 30.0
# Codex wording for a required MCP server that failed during thread/start.
_MCP_STARTUP_FAILURE = "required MCP servers failed to initialize:"

# Credential targets that hold an OpenAI API key, in the order Codex itself
# prefers them. The App Server reads neither from its environment, so M3 signs
# it in with the key over the protocol.
_CODEX_API_KEY_TARGETS = ("CODEX_API_KEY", "OPENAI_API_KEY")
# Bounds one account request during startup; both are local to the App Server.
_ACCOUNT_REQUEST_TIMEOUT_SECONDS = 15.0

# App Server requests that wait for a user to approve a native command, file
# change, or sandbox widening. Codex blocks the turn until each is answered.
_NATIVE_APPROVAL_OPERATIONS = {
    "item/commandExecution/requestApproval": "command_execution",
    "item/fileChange/requestApproval": "file_change",
    "item/permissions/requestApproval": "permissions",
    "execCommandApproval": "command_execution",
    "applyPatchApproval": "file_change",
}


def _approval_target(
    params: Mapping[str, Any], item_paths: Sequence[str] = ()
) -> tuple[str, dict[str, Any]]:
    """Return the resource and structured context of one native approval.

    Modern file-change approvals name only the item; its paths come from the
    earlier ``item/started`` file-change item and arrive as ``item_paths``.
    """
    context: dict[str, Any] = {}
    command = params.get("command")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if isinstance(command, str):
        context["command"] = command
    for key, name in (
        ("cwd", "cwd"),
        ("grantRoot", "grant_root"),
        ("reason", "reason"),
    ):
        value = params.get(key)
        if isinstance(value, str):
            context[name] = value
    network = params.get("networkApprovalContext")
    if isinstance(network, Mapping):
        context["network"] = _plain_json(network)
    changes = params.get("fileChanges")
    if isinstance(changes, Mapping):
        context["paths"] = sorted(str(path) for path in changes)
    elif item_paths:
        context["paths"] = sorted(item_paths)
    permissions = params.get("permissions")
    if isinstance(permissions, Mapping):
        context["permissions"] = _plain_json(permissions)
    # `command` may not describe a managed-network request, so the host is
    # the resource whenever Codex names one.
    if isinstance(network, Mapping) and isinstance(network.get("host"), str):
        resource = network["host"]
    elif isinstance(command, str):
        resource = command
    elif "grant_root" in context:
        resource = context["grant_root"]
    elif "paths" in context:
        resource = ",".join(context["paths"])
    elif isinstance(permissions, Mapping):
        resource = ",".join(sorted(str(key) for key in permissions))
    else:
        resource = ""
    return resource, context


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _within_request(granted: Any, requested: Any) -> bool:
    """Whether a granted permission profile asks for nothing beyond the request."""
    if isinstance(granted, Mapping):
        return isinstance(requested, Mapping) and all(
            key in requested and _within_request(value, requested[key])
            for key, value in granted.items()
        )
    if isinstance(granted, (list, tuple)):
        return isinstance(requested, (list, tuple)) and all(
            any(_within_request(item, offered) for offered in requested)
            for item in granted
        )
    return bool(granted == requested or granted is None or granted is False)


def _login_values(value: Any, sensitive: bool = False) -> set[str]:
    """Every credential-like string in a Codex ``auth.json`` document.

    Values under credential-named keys (and everything under ``tokens``)
    count whatever their length; any other long string counts too, so an
    unrecognized token field is still covered.
    """
    if isinstance(value, Mapping):
        return {
            item
            for key, nested in value.items()
            for item in _login_values(
                nested,
                sensitive or key == "tokens" or is_sensitive_key(str(key)),
            )
        }
    if isinstance(value, list):
        return {item for nested in value for item in _login_values(nested, sensitive)}
    if isinstance(value, str) and value and (sensitive or len(value) >= 16):
        return {value}
    return set()


@dataclass(frozen=True, slots=True)
class _NativeMcpToolItem:
    """Codex-owned item identity eligible for one on-request approval."""

    server: str
    tool: str
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
        # API key for account/login/start, held only between launch and login.
        self._api_key_login: str | None = None
        self._deferred_frames: list[Mapping[str, Any]] = []
        self._observed_tool_call_ids: set[str] = set()
        self._streamed_item_ids: set[str] = set()
        self._write_lock = asyncio.Lock()
        self._active_mrtr_action: CodexMRTRAction | None = None
        self._managed_input_runtime: Any = None
        # Interaction capabilities per executable identity, from successful
        # version probes only.
        self._interaction_by_executable: dict[
            tuple[str, int | None, int | None, int | None, int | None],
            HarnessInteractionCapabilities,
        ] = {}
        self._action_interrupt_sent = False
        self._unscoped_elicitation_failure = False
        self._unapproved_mcp_tool_items: dict[str, _NativeMcpToolItem] = {}
        self._approved_mcp_tool_items: dict[str, _NativeMcpToolItem] = {}
        self._mcp_protocol_markers: dict[str, str] = {}
        # Native approvals answered by next_frame, keyed by request id until
        # consume_frame records them as interaction observations.
        self._answered_approvals: dict[
            str | int, tuple[dict[str, Any], dict[str, Any]]
        ] = {}
        # Paths of started fileChange items, for the approval that follows.
        self._file_change_paths: dict[str, list[str]] = {}

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        # Never probes: reading this from async code must not block the event
        # loop. Until prepare_capabilities() has probed the current binary,
        # MRTR/elicitation is reported as unsupported.
        interaction = self._interaction_by_executable.get(
            self._executable_identity(),
            HarnessInteractionCapabilities(retry_owner="harness"),
        )
        return replace(self._capabilities, interaction=interaction)

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

    async def prepare_capabilities(self) -> None:
        """Probe the current binary's version off the event loop.

        A successful probe is kept for that executable identity; a failed one
        (for example a timeout under load) is not, so the next call retries.
        """
        identity = self._executable_identity()
        if identity in self._interaction_by_executable:
            return
        version = await asyncio.to_thread(probe_help, self.executable, ("--version",))
        if version is None:
            return
        supported = (version.splitlines() or [""])[0].strip() == "codex-cli 0.156.1"
        self._interaction_by_executable[identity] = (
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
        root_settings = "check_for_update_on_startup = false\n"
        if isinstance(launch.spec.harness, Codex) and any(
            target in _CODEX_API_KEY_TARGETS
            for target in launch.spec.harness.credential_references
        ):
            # account/login/start would otherwise write the key to auth.json.
            root_settings += 'cli_auth_credentials_store = "ephemeral"\n'
        config.write_text(
            root_settings + "\n" + render_codex_config(launch),
            encoding="utf-8",
        )
        config.chmod(0o600)
        # Preserve an existing native ChatGPT login when no API-key mapping is
        # configured. The isolated home is temporary and cleaned with the
        # execution workspace; auth material never enters the serializable spec.
        login_secrets: set[str] = set()
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
                            auth = source.read()
                            destination.write(auth)
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
                # Login tokens are runtime secrets, redacted from trace evidence.
                try:
                    login_secrets = _login_values(json.loads(auth))
                except ValueError:
                    login_secrets = set()
        environment = dict(self.environment)
        runtime_secrets: set[str] = set()
        protocol_markers: dict[str, str] = {}
        environment["CODEX_HOME"] = str(home)
        self._api_key_login = None
        harness = launch.spec.harness
        if isinstance(harness, Codex):
            api_keys: dict[str, str] = {}
            for target, reference in harness.credential_references.items():
                value = environment.get(reference.name) or os.environ.get(
                    reference.name
                )
                if not value:
                    raise HarnessStartupError("Codex credential is unavailable")
                environment[target] = value
                runtime_secrets.add(value)
                if target in _CODEX_API_KEY_TARGETS and value.strip():
                    api_keys[target] = value.strip()
                    runtime_secrets.add(value.strip())
            self._api_key_login = next(
                (
                    api_keys[target]
                    for target in _CODEX_API_KEY_TARGETS
                    if target in api_keys
                ),
                None,
            )
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
        runtime_secrets |= login_secrets
        # Credentials the adapter passes to Codex directly (for example an
        # OPENAI_API_KEY in its environment) are runtime secrets as well.
        runtime_secrets |= {
            value
            for key, value in environment.items()
            if value and is_sensitive_key(key)
        }
        self._runtime_secrets = runtime_secrets
        self._mcp_protocol_markers = protocol_markers
        add_secrets = getattr(launch.capture, "add_secrets", None)
        if callable(add_secrets) and runtime_secrets:
            add_secrets(runtime_secrets)
        return environment

    async def preflight(self, launch: HarnessLaunch) -> Readiness:
        await self.prepare_capabilities()
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
        api_key, self._api_key_login = self._api_key_login, None
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
        await self._authenticate(process, api_key)
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
            # MCP tools require Codex's approval-capable mode; ``never`` makes
            # the App Server reject otherwise valid tool calls. M3 answers
            # each scoped approval from the tool policy its proxy enforces.
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
        # thread/start replies only after Codex has started the thread's MCP
        # servers, which regularly takes several seconds for a stdio server.
        while True:
            frame = await process.next(_THREAD_START_TIMEOUT_SECONDS)
            if frame is None or frame.get("__invalid_frame__"):
                raise HarnessStartupError("Codex thread could not be started")
            if frame.get("id") == self._request_id:
                error = frame.get("error")
                if isinstance(error, Mapping):
                    raise self._thread_start_failure(error, launch)
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

    async def _startup_request(
        self, process: JsonRpcProcess, method: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Send one startup request and return its response frame.

        Frames that arrive first are kept for the turn loop, as during
        initialize and thread/start.
        """

        self._request_id += 1
        request_id = self._request_id
        await self._write_frame(
            process,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
        while True:
            try:
                frame = await process.next(_ACCOUNT_REQUEST_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                raise HarnessStartupError(f"Codex {method} timed out") from None
            if frame is None or frame.get("__invalid_frame__"):
                raise HarnessStartupError(f"Codex {method} failed")
            if "method" not in frame and frame.get("id") == request_id:
                return frame
            self._deferred_frames.append(frame)

    async def _authenticate(self, process: JsonRpcProcess, api_key: str | None) -> None:
        """Sign Codex in with a mapped API key and require a usable account.

        ``codex app-server`` reads neither ``OPENAI_API_KEY`` nor
        ``CODEX_API_KEY`` from its environment, so a mapped key takes effect
        only through account/login/start. Without a login every model request
        fails with HTTP 401 mid-turn, so a missing account fails startup.
        """

        if api_key is not None:
            response = await self._startup_request(
                process, "account/login/start", {"type": "apiKey", "apiKey": api_key}
            )
            error = response.get("error")
            if error is not None or not isinstance(response.get("result"), Mapping):
                # Codex's free text is never passed on (see _thread_start_failure).
                code = error.get("code") if isinstance(error, Mapping) else None
                summary = "Codex API key login failed"
                if isinstance(code, int) and not isinstance(code, bool):
                    summary += f" (JSON-RPC {code})"
                raise HarnessStartupError(summary)
        response = await self._startup_request(
            process, "account/read", {"refreshToken": False}
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            # An App Server that cannot report its account is not blocked here;
            # a missing login then surfaces as a failed turn.
            return
        if result.get("account") is None and result.get("requiresOpenaiAuth") is True:
            raise HarnessStartupError(
                "Codex API key login was not applied"
                if api_key is not None
                else "Codex has no login: map an OpenAI API key with "
                "credential_env or sign in to Codex"
            )

    @staticmethod
    def _thread_start_failure(
        error: Mapping[str, Any], launch: HarnessLaunch
    ) -> HarnessStartupError:
        """Describe a thread/start error without Codex's free text.

        Codex can echo any credential it was started with (environment, native
        login, MCP environment or headers), so its message is never passed on.
        The error is built from M3-known values only: the JSON-RPC code and the
        configured MCP servers the message names as failing to start.
        """

        code = error.get("code")
        summary = "Codex thread/start failed"
        if isinstance(code, int) and not isinstance(code, bool):
            summary += f" (JSON-RPC {code})"
        message = error.get("message")
        if isinstance(message, str):
            _, marker, rest = message.partition(_MCP_STARTUP_FAILURE)
            failed = sorted(
                {
                    config.key
                    for config in launch.configurations
                    if marker
                    and re.search(rf"(?<![\w-]){re.escape(config.key)}:", rest)
                }
            )
            if failed:
                summary += (
                    f": required MCP server failed to initialize: {', '.join(failed)}"
                )
        return HarnessStartupError(summary)

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
        self._answered_approvals.clear()
        self._file_change_paths.clear()
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
        request_id = frame.get("id")
        answered = (
            self._answered_approvals.pop(request_id, None)
            if isinstance(request_id, (str, int)) and "method" in frame
            else None
        )
        if answered is not None:
            approval_request, approval_response = answered
            offset = max(0.0, (asyncio.get_event_loop().time() - started) * 1000)
            observations.append(
                InteractionObservedObservation(
                    observation_id=f"codex-{sequence}-approval-{request_id}",
                    harness_kind="codex",
                    turn_sequence=sequence,
                    wall_time=wall,
                    monotonic_offset_ms=offset,
                    interaction_kind="permission.request",
                    request=approval_request,
                )
            )
            observations.append(
                InteractionObservedObservation(
                    observation_id=f"codex-{sequence}-approval-{request_id}-response",
                    harness_kind="codex",
                    turn_sequence=sequence,
                    wall_time=wall,
                    monotonic_offset_ms=offset,
                    interaction_kind="permission.response",
                    response=approval_response,
                )
            )
            return False, "", []
        # Server requests carry their own id space, which can collide with the
        # client's turn/start id.
        if (
            self._pending_turn_request is not None
            and "method" not in frame
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
                        if self._terminal_status == "failed":
                            self._terminal_error = _turn_failure(turn.get("error"))
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
        if frame.get("method") == "item/started":
            self._remember_file_change_paths(frame.get("params"))
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
                await self._answer_mcp_elicitation(process, frame, approval_item)
            elif self._active_mrtr_action is not None:
                # The action coordinator batches all native prompts for one
                # observed keyed round before writing any response. Returning
                # immediately keeps the App Server reader live while it waits.
                self._reject_incompatible_mrtr_server(params)
                self._active_mrtr_action.submit_native_prompt(frame)
            else:
                self._unscoped_elicitation_failure = True
        elif "method" in frame and "id" in frame:
            # Codex waits for every server request to be answered, so none may
            # be left pending: an unanswered approval stalls the turn forever.
            await self._answer_server_request(process, frame)
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
                server, tool, arguments_json
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
        self,
        process: JsonRpcProcess,
        frame: Mapping[str, Any],
        item: _NativeMcpToolItem,
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
            and item.server == server_name
        ):
            # Selected-server MCP access is decided by the session's tool
            # policy; the permission policy is reserved for prompts outside
            # that scope. The policy is applied to the item's tool here because
            # not every transport has a proxy gate before the server.
            servers = getattr(launch, "servers", None)
            advertised = tuple(
                ToolDescriptor(server=record.key, name=name)
                for record in getattr(servers, "records", ())
                if record.available
                for name in record.tools
            )
            try:
                tool = ToolDescriptor(server=item.server, name=item.tool)
            except ValueError:
                tool = None
            if tool is not None:
                permission = await launch.interactions._approve_selected_mcp_tool(
                    launch.tool_policy, tool, advertised, harness_name="codex"
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

    def _remember_file_change_paths(self, params: Any) -> None:
        item = params.get("item") if isinstance(params, Mapping) else None
        if not isinstance(item, Mapping) or item.get("type") != "fileChange":
            return
        item_id = item.get("id")
        changes = item.get("changes")
        if isinstance(item_id, str) and isinstance(changes, list):
            self._file_change_paths[item_id] = [
                change["path"]
                for change in changes
                if isinstance(change, Mapping) and isinstance(change.get("path"), str)
            ]

    async def _answer_server_request(
        self, process: JsonRpcProcess, frame: Mapping[str, Any]
    ) -> None:
        request_id = frame.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return
        method = str(frame.get("method"))
        operation = _NATIVE_APPROVAL_OPERATIONS.get(method)
        if operation is None:
            await self._write_frame(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"M3 does not handle Codex request {method}",
                    },
                },
            )
            return
        params = frame.get("params")
        params = params if isinstance(params, Mapping) else {}
        item_id = params.get("itemId")
        resource, context = _approval_target(
            params,
            self._file_change_paths.get(item_id, ())
            if isinstance(item_id, str)
            else (),
        )
        # Native approvals are outside the MCP tool selection, so they follow
        # the session's permission policy, which denies by default.
        interactions = self._launch.interactions if self._launch else None
        allowed = False
        reason = "default_deny"
        grant: Mapping[str, Any] | None = None
        if interactions is not None:
            permission = await interactions.permission(
                PermissionRequest(f"codex.{operation}", resource, context=context)
            )
            allowed = permission.allowed
            reason = permission.receipt.reason
            grant = permission.grant
        if method in {"execCommandApproval", "applyPatchApproval"}:
            result: dict[str, Any] = {
                "decision": "approved"
                if allowed
                else {"denied": {"rejection": "declined by M3 permission policy"}}
            }
        elif operation == "permissions":
            requested = params.get("permissions")
            requested = requested if isinstance(requested, Mapping) else {}
            granted: Any = {}
            if allowed:
                granted = requested if grant is None else grant
                # A handler may narrow the request but never widen it.
                if not _within_request(granted, requested):
                    granted, reason = {}, "grant_exceeds_request"
            result = {"permissions": _plain_json(granted), "scope": "turn"}
        else:
            result = {"decision": "accept" if allowed else "decline"}
        await self._write_frame(
            process, {"jsonrpc": "2.0", "id": request_id, "result": result}
        )
        # The raw frame already carries the (redacted) command; the
        # observation records only the decision.
        self._answered_approvals[request_id] = (
            {"method": method, "operation": operation},
            {
                "outcome": "allow" if allowed else "deny",
                "reason": reason,
                "reply": result,
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


# Codex's codexErrorInfo variants (app-server-protocol CodexErrorInfo) and the
# M3-authored text reported for each.
_CODEX_TURN_FAILURES = {
    "contextWindowExceeded": "context window exceeded",
    "sessionBudgetExceeded": "session budget exceeded",
    "usageLimitExceeded": "usage limit exceeded",
    "rateLimitExceeded": "rate limit exceeded",
    "flexUnavailable": "flex processing unavailable",
    "serverOverloaded": "model server overloaded",
    "cyberPolicy": "blocked by provider policy",
    "misalignmentPolicyViolation": "blocked by provider policy",
    "tooManyDenials": "too many denied approvals",
    "httpConnectionFailed": "model request failed",
    "responseStreamConnectionFailed": "model response stream could not connect",
    "internalServerError": "model provider internal error",
    "unauthorized": "model provider rejected the credentials",
    "badRequest": "model provider rejected the request",
    "threadRollbackFailed": "thread rollback failed",
    "sandboxError": "sandbox error",
    "responseStreamDisconnected": "model response stream disconnected",
    "responseTooManyFailedAttempts": "model request retries exhausted",
    "activeTurnNotSteerable": "active turn cannot be steered",
    "other": "unclassified error",
}


def _turn_failure(error: Any) -> ErrorInfo:
    """Describe a failed Codex turn from its structured error fields only.

    Codex's ``message`` can quote request details and any credential it was
    started with, so it is never passed on (see ``_thread_start_failure``).
    The reason is a known ``codexErrorInfo`` variant and the HTTP status a
    bounded integer.
    """

    info = error.get("codexErrorInfo") if isinstance(error, Mapping) else None
    reason: str | None = None
    status: int | None = None
    if isinstance(info, str):
        reason = info
    elif isinstance(info, Mapping) and len(info) == 1:
        ((reason, payload),) = info.items()
        if isinstance(payload, Mapping):
            code = payload.get("httpStatusCode")
            if isinstance(code, int) and not isinstance(code, bool):
                status = code if 100 <= code <= 599 else None
    if reason not in _CODEX_TURN_FAILURES:
        reason = None
    details: dict[str, Any] = {"harness": "codex"}
    if reason is None:
        message = "Codex turn failed"
    else:
        details["reason"] = reason
        description = _CODEX_TURN_FAILURES[reason]
        if status in {401, 403}:
            description = "model provider rejected the credentials"
        message = f"Codex turn failed: {description}"
    if status is not None:
        details["http_status"] = status
        message += f" (HTTP {status})"
    return ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message=message, details=details)


def _tool_error_message(error: Any) -> str | None:
    if isinstance(error, str):
        return error
    if isinstance(error, Mapping):
        message = error.get("message")
        return message if isinstance(message, str) else None
    return None


__all__ = ["CodexHarnessAdapter", "codex_configuration", "render_codex_config"]
