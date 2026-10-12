"""Claude Code stream-JSON harness adapter.

This adapter starts one process per conversation and writes every turn to the
same stream.  It intentionally has no resume-based fallback: a binary that
cannot provide stream input/output is not a ready Claude adapter.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import JsonValue

from .._types.specs import AgentSpec
from ..agent_session import AdapterTurn
from ..policy import ToolPolicyEvidence
from ..server_group import HarnessServerConfig
from ..trace.redaction import RedactionConfig, RedactionError, redact_for_api
from ..types import (
    UNSET,
    Capability,
    CapabilityStatus,
    ClaudeCode,
    ErrorCode,
    ErrorInfo,
    NativeToolPolicy,
    Readiness,
    RestrictiveToolPolicy,
    SecretReference,
    TextContent,
    TurnOutcome,
    TurnResponse,
    UserMessage,
)
from ._native_errors import (
    launch_error_redaction,
    native_error_info,
    project_native_observation,
    project_native_turn,
)
from .contracts import (
    HarnessAdapterCapabilities,
    HarnessAdapterError,
    HarnessInteractionCapabilities,
    HarnessLaunch,
    HarnessSession,
    HarnessStartupError,
    HarnessTurnRequest,
    HarnessTurnResult,
)
from .native import (
    MAX_QUEUE_ITEMS,
    NativeSessionBase,
    ProcessOwner,
    _executable,
    _isolated_environment,
    _server_configuration,
    _text,
    probe_help,
    probe_help_for_launch,
    read_bounded_line,
    startup_spawn_options,
    workspace_for_launch,
    write_config,
)
from .native_capabilities import NativeRuntimeIdentity
from .native_settings import milliseconds
from .observations import (
    HarnessObservation,
    MessageChunkObservation,
    MetadataObservedObservation,
    ProcessObservedObservation,
    RawEvidenceInput,
    RawFrameObservation,
    ReasoningChunkObservation,
    ToolCallObservedObservation,
    ToolResultObservedObservation,
    TurnEvidence,
    UsageObservedObservation,
)

_MALFORMED = {"capture": "unavailable", "reason": "malformed_source"}
_MISSING = object()
_MAX_IDENTIFIER = 256


def _valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_IDENTIFIER
        and all(ord(char) >= 0x20 and ord(char) != 0x7F for char in value)
    )


def _safe_identifier(value: object) -> str | dict[str, str]:
    return cast(str, value) if _valid_identifier(value) else dict(_MALFORMED)


def _safe_json_value(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        return dict(_MALFORMED)
    if isinstance(value, str) and len(value) > 8_388_608:
        return dict(_MALFORMED)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return dict(_MALFORMED)


def _metadata(
    sequence: int,
    name: str,
    value: object,
    wall_time: datetime,
    offset: float,
    suffix: str = "",
) -> MetadataObservedObservation:
    return MetadataObservedObservation(
        observation_id=f"claude-{sequence}-metadata-{name}{('-' + suffix) if suffix else ''}",
        harness_kind="claude-code",
        turn_sequence=sequence,
        wall_time=wall_time,
        monotonic_offset_ms=offset,
        name=name,
        value=cast(JsonValue, value),
    )


def _normalize_tool_name(value: str) -> tuple[str | None, str]:
    parts = value.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp" and parts[1] and parts[2]:
        return parts[1], parts[2]
    return None, value


def _emit_usage(
    observations: list[HarnessObservation],
    sequence: int,
    wall_time: datetime,
    offset: Any,
    usage: Mapping[str, Any],
) -> bool:
    fields = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_creation_input_tokens": "cache_creation_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
        "reasoning_tokens": "reasoning_tokens",
        "total_tokens": "total_tokens",
    }
    valid: dict[str, Any] = {}
    malformed = False
    for source, target in fields.items():
        if source not in usage:
            continue
        value = usage[source]
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            valid[target] = value
        else:
            malformed = True
            observations.append(
                _metadata(
                    sequence,
                    f"usage_{target}_state",
                    dict(_MALFORMED),
                    wall_time,
                    offset(),
                )
            )
    if "cost" in usage:
        value = usage["cost"]
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
            and value >= 0
        ):
            valid["cost"] = value
        else:
            malformed = True
            observations.append(
                _metadata(
                    sequence, "usage_cost_state", dict(_MALFORMED), wall_time, offset()
                )
            )
    currency = usage.get("currency")
    if (
        currency is not None
        and isinstance(currency, str)
        and currency
        and len(currency) <= 16
    ):
        valid["currency"] = currency
    elif "currency" in usage:
        malformed = True
        observations.append(
            _metadata(
                sequence, "usage_currency_state", dict(_MALFORMED), wall_time, offset()
            )
        )
    if valid:
        observations.append(
            UsageObservedObservation(
                observation_id=f"claude-{sequence}-usage",
                harness_kind="claude-code",
                turn_sequence=sequence,
                wall_time=wall_time,
                monotonic_offset_ms=offset(),
                **valid,
            )
        )
    return malformed


def _process_observation(
    adapter: ClaudeCodeHarnessAdapter,
    sequence: int,
    wall_time: datetime,
    started: float,
    phase: str,
    owner: ProcessOwner,
) -> ProcessObservedObservation | None:
    process = owner.process
    if process is None:
        return None
    stderr: str | None = None
    stderr_state: (
        Literal["observed", "disabled", "unavailable", "truncated", "redacted"] | None
    ) = None
    if owner.stderr_task is not None and owner.stderr_task.done():
        try:
            raw = owner.stderr_task.result()
            stderr = raw.decode("utf-8", errors="replace")
            stderr_state = "observed"
        except Exception:
            stderr_state = "unavailable"
    process_kwargs: dict[str, Any] = {}
    if stderr is not None:
        process_kwargs["stderr"] = stderr
    if stderr_state is not None:
        process_kwargs["stderr_state"] = stderr_state
    return ProcessObservedObservation(
        observation_id=f"claude-{sequence}-process-{phase}",
        harness_kind="claude-code",
        turn_sequence=sequence,
        wall_time=wall_time,
        monotonic_offset_ms=max(0.0, (time.monotonic() - started) * 1000.0),
        phase=cast(Literal["started", "exited", "failed"], phase),
        executable=adapter.executable,
        pid=process.pid,
        exit_code=process.returncode if phase != "started" else None,
        **process_kwargs,
    )


def _claude_result(
    sequence: int,
    status: str,
    error: ErrorInfo | None,
    observations: list[HarnessObservation],
    limitations: list[str],
    started: float,
    tool_calls: list[Mapping[str, Any]],
    text: str = "",
) -> HarnessTurnResult:
    has_usage = any(isinstance(item, UsageObservedObservation) for item in observations)
    has_mcp = any(
        isinstance(item, ToolCallObservedObservation) and item.server is not None
        for item in observations
    )
    return HarnessTurnResult(
        sequence=sequence,
        status=status,  # type: ignore[arg-type]
        response=None
        if error is not None or status != "completed"
        else _response({}, text),
        error=error,
        tool_calls=tuple(tool_calls),
        evidence={
            "process_observed": any(
                isinstance(item, ProcessObservedObservation) for item in observations
            ),
            "transport_observed": "stream_json",
            "content_observed": bool(text),
            "tool_calls_observed": bool(tool_calls),
            "mcp_traffic_observed": has_mcp,
            "usage_requested": True,
            "usage_enforced": False,
            "usage_observed": has_usage,
            "usage_state": "observed" if has_usage else "unavailable",
            "usage_unavailable": not has_usage,
        },
        trace_limitations=tuple(limitations),
        turn_evidence=TurnEvidence(
            sequence=sequence,
            status=status,  # type: ignore[arg-type]
            observations=tuple(observations),
            limitations=tuple(limitations),
            monotonic_origin=started,
        ),
    )


def _response(output: Mapping[str, Any], text: str) -> TurnResponse:
    value = text or _text(
        output.get("result", output.get("message", output.get("content", "")))
    )
    return TurnResponse(content=(TextContent(text=value),))


class ClaudeCodeHarnessAdapter:
    """One continuous Claude Code stream-JSON conversation."""

    managed_runtime_supported = True
    interaction_capabilities = HarnessInteractionCapabilities()

    def __init__(
        self,
        *,
        executable: str = "claude",
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.executable = _executable(executable, "claude")
        self.environment = dict(environment or {})
        self._resolver_environment = environment
        self._capabilities = HarnessAdapterCapabilities(
            name="claude-code",
            supports_multiturn=True,
            supports_cancellation=True,
            supports_timeout=True,
            # This adapter currently does not expose a portable policy
            # receipt; AgentSession must reject explicit policies rather than
            # treating the provider's process as proof of enforcement.
            supports_tool_policy=False,
            supports_streaming=True,
            interaction=self.interaction_capabilities,
        )
        self.last_policy_evidence: ToolPolicyEvidence | None = None
        self._root: Path | None = None
        self._config: Path | None = None
        self._owner: ProcessOwner | None = None
        self._startup_cleanup_failed = False
        self._startup_cleanup_task: asyncio.Task[None] | None = None
        self._launch: HarnessLaunch | None = None
        self._startup_phase_deadline: float | None = None
        self._session: ClaudeCodeSession | None = None
        self._output: asyncio.Queue[Mapping[str, Any] | None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._process_started_emitted = False
        self._cancel_requested = False
        self._partial_capture: Callable[[], TurnEvidence] | None = None
        self._native_error_redaction: RedactionConfig | None = None

    @property
    def name(self) -> str:
        return self._capabilities.name

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        return self._capabilities

    @property
    def supported_content_kinds(self) -> frozenset[str]:
        return self._capabilities.supported_content_kinds

    async def validate_native_settings(
        self,
        spec: AgentSpec,
        configurations: tuple[HarnessServerConfig, ...],
        *,
        runtime_identity: NativeRuntimeIdentity | None = None,
        deadline: float | None = None,
    ) -> None:
        from .native_settings import validate_native_settings

        await validate_native_settings(
            self.executable, spec, configurations, runtime_identity, deadline=deadline
        )
        startup = next(
            (
                item.timeouts.startup_seconds
                for item in configurations
                if item.timeouts.startup_seconds is not UNSET
            ),
            UNSET,
        )
        if startup is not UNSET and "MCP_TIMEOUT" in self.environment:
            if self.environment["MCP_TIMEOUT"] != str(milliseconds(startup)):
                raise HarnessStartupError(
                    "Claude MCP timeout conflicts with explicit harness environment"
                )

    async def preflight(self, launch: HarnessLaunch) -> Readiness:
        from ._native_preparation import owned_native_preparation

        if not owned_native_preparation(launch) and (
            shutil.which(self.executable) is None
            and not Path(self.executable).is_file()
        ):
            return Readiness(
                ready=False,
                capabilities=(
                    Capability(
                        name="harness:claude-code",
                        status=CapabilityStatus.UNAVAILABLE,
                        reason="executable unavailable",
                    ),
                ),
                reason="executable unavailable",
            )
        help_text = (
            await probe_help_for_launch(launch, self.executable, ("--help",))
            if owned_native_preparation(launch)
            else await asyncio.to_thread(probe_help, self.executable, ("--help",))
        )
        if help_text is None or "stream-json" not in help_text:
            capability = Capability(
                name="harness:claude-code",
                status=CapabilityStatus.UNAVAILABLE,
                reason="stream-json unavailable",
            )
            return Readiness(
                ready=False,
                capabilities=(capability,),
                reason="stream-json unavailable",
            )
        policy = launch.tool_policy
        if policy is None or (
            isinstance(policy, RestrictiveToolPolicy)
            and not policy.allowed_tools
            and not policy.denied_tools
        ):
            self.last_policy_evidence = None
            return self._capabilities.readiness()
        if not isinstance(policy, NativeToolPolicy) or policy.harness != self.name:
            return Readiness(
                ready=False, reason="Claude Code requires a native tool policy"
            )
        mode = policy.policy.get("mode")
        server = policy.policy.get("server")
        if (
            mode not in {"mcp_only", "mcp_read_only", "full"}
            or not isinstance(server, str)
            or not server
        ):
            return Readiness(
                ready=False, reason="Claude Code native tool policy is invalid"
            )
        if server not in {configuration.key for configuration in launch.configurations}:
            return Readiness(
                ready=False, reason="Claude Code native tool policy is invalid"
            )
        if any(
            not isinstance(reference, SecretReference)
            or reference.source != "environment"
            for reference in getattr(
                launch.spec.harness, "credential_references", {}
            ).values()
        ):
            return Readiness(
                ready=False, reason="Claude Code credential reference is invalid"
            )
        self.last_policy_evidence = ToolPolicyEvidence(
            requested="native",
            enforced="native",
            observed="preflight",
            portable=False,
            nonportable_reason="Claude Code CLI tool allowlist",
        )
        return self._capabilities.readiness()

    async def prepare_launch_credentials(self, launch: HarnessLaunch) -> HarnessLaunch:
        from ._launch_credentials import activate_native_launch

        return await activate_native_launch(
            launch,
            resolver_environment=self._resolver_environment,
            child_environment=self.environment,
        )

    async def open(self, launch: HarnessLaunch) -> HarnessSession:
        from ._native_preparation import validate_owned_startup

        validate_owned_startup(launch)
        readiness = await self.preflight(launch)
        if not readiness.ready:
            raise HarnessStartupError("Claude Code streaming is unavailable")
        if self._session is not None:
            raise HarnessStartupError("Claude Code session is already open")
        launch = await self.prepare_launch_credentials(launch)
        self._launch = launch
        from ._native_preparation import NativePreparation, owned_native_preparation

        preparation = None
        assert launch._credentials is not None
        runtime_secrets: set[str] = set()
        if owned_native_preparation(launch):
            payload = json.dumps(
                _server_configuration(
                    launch,
                    resolve_credentials=True,
                    environment=launch._credentials.reference_environment(),
                    secrets=runtime_secrets,
                ),
                separators=(",", ":"),
            ).encode("utf-8")
            preparation = await NativePreparation.create(
                launch, "claude_code", {"mcp-config.json": payload}
            )
            root = preparation.root
        else:
            root = Path(tempfile.mkdtemp(prefix="m3-claude-"))
        owner = ProcessOwner(root)
        self._owner = owner
        self._root = root
        if preparation is not None:
            preparation.adopt(owner)
        try:
            credentials = launch._credentials
            assert credentials is not None
            environment = _isolated_environment(
                root, credentials.child_environment(), prepared=preparation is not None
            )
            environment["DISABLE_UPDATES"] = "1"
            harness = launch.spec.harness
            if harness is None:
                raise HarnessStartupError("Claude Code harness is unavailable")
            if isinstance(harness, ClaudeCode):
                startup = next(
                    (
                        item.timeouts.startup_seconds
                        for item in launch.configurations
                        if item.timeouts.startup_seconds is not UNSET
                    ),
                    UNSET,
                )
                if startup is not UNSET:
                    native_budget = str(milliseconds(startup))
                    if (
                        "MCP_TIMEOUT" in self.environment
                        and self.environment["MCP_TIMEOUT"] != native_budget
                    ):
                        raise HarnessStartupError(
                            "Claude MCP timeout conflicts with explicit harness environment"
                        )
                    environment["MCP_TIMEOUT"] = native_budget
            for target, reference in getattr(
                harness, "credential_references", {}
            ).items():
                if (
                    not isinstance(reference, SecretReference)
                    or reference.source != "environment"
                ):
                    raise HarnessStartupError(
                        "Claude Code credential reference is unavailable"
                    )
                value = credentials.credential_environment()[target]
                if not value:
                    raise HarnessStartupError("Claude Code credential is unavailable")
                environment[target] = value
                runtime_secrets.add(value)
            # The isolated child environment is deliberately smaller than the
            # resolver source.  A selected map takes precedence for each
            # named reference, while missing named references retain the
            # legacy ambient fallback.
            config = (
                root / "mcp-config.json"
                if preparation is not None
                else write_config(
                    root,
                    launch,
                    environment=credentials.reference_environment(),
                    secrets=runtime_secrets,
                )
            )
            self._native_error_redaction = launch_error_redaction(
                launch, self.environment, runtime_secrets
            )
            # ``write_config`` resolves selected server references and adds
            # their literals to the runtime-only set before the child exists.
            add_secrets = getattr(launch.capture, "add_secrets", None)
            if callable(add_secrets) and runtime_secrets:
                add_secrets(runtime_secrets)
            workspace = workspace_for_launch(launch, root)
            argv = [
                self.executable,
                "--print",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--verbose",
                "--strict-mcp-config",
                "--mcp-config",
                str(config),
                "--model",
                harness.model,
            ]
            policy = launch.tool_policy
            if isinstance(policy, NativeToolPolicy):
                mode = policy.policy.get("mode")
                server = str(policy.policy.get("server"))
                read_only = tuple(
                    str(item) for item in (policy.policy.get("read_only_tools") or ())
                )
                if mode == "mcp_only":
                    argv.extend(["--tools", ""])
                    if (
                        not isinstance(harness, ClaudeCode)
                        or harness.permissions.allowed_tools is UNSET
                    ):
                        argv.extend(["--allowedTools", f"mcp__{server}__*"])
                elif mode == "mcp_read_only":
                    argv.extend(
                        [
                            "--tools",
                            ",".join(read_only),
                        ]
                    )
                    if (
                        not isinstance(harness, ClaudeCode)
                        or harness.permissions.allowed_tools is UNSET
                    ):
                        argv.extend(
                            [
                                "--allowedTools",
                                ",".join((*read_only, f"mcp__{server}__*")),
                            ]
                        )
                elif mode == "full":
                    argv.extend(["--tools", "default"])
                    if (
                        not isinstance(harness, ClaudeCode)
                        or not harness.permissions.model_dump()
                    ):
                        argv.append("--dangerously-skip-permissions")
            if isinstance(harness, ClaudeCode):
                permissions = harness.permissions
                if permissions.permission_mode is not UNSET:
                    argv.extend(["--permission-mode", permissions.permission_mode])
                if permissions.allowed_tools is not UNSET:
                    argv.extend(["--allowedTools", ",".join(permissions.allowed_tools)])
                if permissions.denied_tools is not UNSET:
                    argv.extend(
                        ["--disallowedTools", ",".join(permissions.denied_tools)]
                    )
            from ._launch_credentials import check_credential_deadline

            check_credential_deadline(launch)
            if preparation is not None and launch._startup_clock is not None:
                launch._startup_clock.clear_owned_preparation()
                launch._startup_clock.begin_native()
                from .._startup_deadlines import earliest

                clock = launch._startup_clock
                self._startup_phase_deadline = earliest(
                    clock.phase("initialize", None).deadline,
                    clock.phase("readiness", None).deadline,
                    clock.phase("session", None).deadline,
                )
            await owner.spawn(
                argv,
                environment,
                cwd=workspace,
                **startup_spawn_options(
                    launch._startup_lease,
                    runtime_descriptors=(
                        launch._runtime_lease.descriptors
                        if launch._runtime_lease is not None
                        else ()
                    ),
                    owned_startup=preparation is not None,
                    startup_deadline=launch._startup_clock.deadline
                    if launch._startup_clock is not None
                    and (preparation is not None or launch._startup_lease is not None)
                    else None,
                ),
            )
            output: asyncio.Queue[Mapping[str, Any] | None] = asyncio.Queue(
                maxsize=MAX_QUEUE_ITEMS
            )
            process = owner.process
            assert process is not None
            stream = process.stdout
            assert stream is not None

            async def read_output() -> None:
                try:
                    while True:
                        line = await read_bounded_line(stream)
                        if line is None:
                            break
                        try:
                            decoded = line.decode("utf-8")
                            value = json.loads(decoded)
                        except (TypeError, ValueError):
                            await output.put(
                                {"__adapter_error__": "invalid_stream_frame"}
                            )
                            await owner.terminate()
                            break
                        if isinstance(value, Mapping):
                            await output.put(value)
                        else:
                            await output.put(
                                {"__adapter_error__": "invalid_stream_frame"}
                            )
                            await owner.terminate()
                            break
                except Exception:
                    await output.put({"__adapter_error__": "stream_frame_unavailable"})
                    try:
                        await owner.terminate()
                    except Exception:
                        pass
                finally:
                    await output.put(None)

            self._reader_task = asyncio.create_task(read_output())
            self._root = root
            # Claude may open --mcp-config after the child process has been
            # spawned.  Keep the mode-0600 file until the owned process and
            # its readers are reaped; deleting it here races startup.
            self._config = config
            self._owner = owner
            self._output = output
            self._launch = launch
            self._process_started_emitted = False
            self._cancel_requested = False
            self._session = ClaudeCodeSession(
                self, owner, self._capabilities, "claude-" + uuid4().hex
            )
            return self._session
        except BaseException as failure:
            from .._startup_deadlines import retain_cleanup, wait_cleanup

            self._startup_cleanup_task = retain_cleanup(owner.close)
            self._startup_cleanup_failed = not await wait_cleanup(
                self._startup_cleanup_task,
                launch._startup_clock,
                cancelled=isinstance(failure, asyncio.CancelledError),
            )
            raise

    async def _startup_ready(
        self, item: Mapping[str, Any], owner: ProcessOwner
    ) -> None:
        launch = self._launch
        if launch is None:
            return
        statuses = item.get("mcp_servers")
        required = {
            config.key
            for config in launch.configurations
            if config.required and config.available
        }
        if required and (
            (
                launch._startup_clock is not None
                and launch._startup_clock.requires_ownership
            )
            or launch.spec.admission.enabled
        ):
            if not isinstance(statuses, list):
                raise HarnessStartupError("Claude MCP readiness was unavailable")
            connected = {
                status.get("name")
                for status in statuses
                if isinstance(status, Mapping) and status.get("status") == "connected"
            }
            if not required <= connected:
                raise HarnessStartupError(
                    "required Claude MCP server failed to initialize"
                )
        await owner.startup_ready()
        self._startup_phase_deadline = None
        if launch._startup_clock is not None:
            launch._startup_clock.finish()

    async def start(self, spec: Any) -> None:
        raise HarnessStartupError("Claude Code requires a resolved harness launch")

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AdapterTurn:
        del metadata
        if self._session is None:
            raise HarnessAdapterError("Claude Code session is not open")
        self._partial_capture = None
        result = await self._session.send(
            HarnessTurnRequest.from_message(message, timeout_seconds=timeout)
        )
        self._partial_capture = None
        return AdapterTurn(
            response=result.response,
            error=result.error,
            terminal=result.status != "completed",
            outcome={
                "completed": TurnOutcome.COMPLETED,
                "timed_out": TurnOutcome.TIMED_OUT,
                "cancelled": TurnOutcome.CANCELLED,
                "interrupted": TurnOutcome.INTERRUPTED,
            }.get(result.status, TurnOutcome.FAILED),
            tool_calls=result.tool_calls,
            _private_tool_calls=result._private_tool_calls,
            evidence=result.evidence,
            trace_limitations=result.trace_limitations,
            turn_evidence=result.turn_evidence,
        )

    async def close(self) -> None:
        pending = self._startup_cleanup_task
        if pending is not None and not pending.done():
            from .._startup_deadlines import wait_cleanup
            from ..errors import CleanupError

            clock = self._launch._startup_clock if self._launch is not None else None
            if (
                not await wait_cleanup(pending, clock, cancelled=False)
                and not pending.done()
            ):
                raise CleanupError("native startup cleanup remains pending")
        self._startup_cleanup_task = None
        session = self._session
        if session is not None:
            await session.close()
        elif self._owner is not None:
            await self._owner.close()
        self._session = None
        if self._config is not None and (
            self._owner is None or self._owner._owned_preparation is None
        ):
            try:
                self._config.unlink(missing_ok=True)
            except OSError:
                # ProcessOwner cleanup already removes the isolated root; a
                # missing file is therefore an expected terminal state.
                pass
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._reader_task = None
        self._owner = None
        self._config = None
        self._output = None
        from ._launch_credentials import release_native_credentials

        release_native_credentials(self._launch)
        self._launch = None
        self._root = None
        self._native_error_redaction = None

    async def cancel(self) -> None:
        self._cancel_requested = True
        if self._session is not None:
            await self._session.cancel()

    def take_partial_turn_evidence(self) -> TurnEvidence | None:
        """Return observations of a turn cancelled before it returned, once."""

        capture, self._partial_capture = self._partial_capture, None
        if capture is None:
            return None
        evidence = capture()
        redaction = self._native_error_redaction or RedactionConfig(
            include_environment=False
        )
        return evidence.model_copy(
            update={
                "observations": tuple(
                    project_native_observation(item, redaction, index)
                    for index, item in enumerate(evidence.observations)
                )
            }
        )

    async def _send(
        self, request: HarnessTurnRequest, sequence: int
    ) -> HarnessTurnResult:
        result = await self._send_private(request, sequence)
        return project_native_turn(
            result,
            self._native_error_redaction or RedactionConfig(include_environment=False),
        )

    async def _send_private(
        self, request: HarnessTurnRequest, sequence: int
    ) -> HarnessTurnResult:
        owner, output = self._owner, self._output
        if (
            owner is None
            or owner.process is None
            or owner.process.stdin is None
            or output is None
        ):
            raise HarnessAdapterError("Claude Code session is not open")
        turn_started, turn_wall_time = time.monotonic(), datetime.now(timezone.utc)
        deadline = (
            None
            if request.timeout_seconds is None
            else turn_started + request.timeout_seconds
        )
        observations: list[HarnessObservation] = []
        limitations: list[str] = []
        frame_index = message_index = tool_result_index = 0
        text_parts: list[str] = []
        tool_calls: list[Mapping[str, Any]] = []
        emitted_calls: set[str] = set()
        emitted_results: set[str] = set()
        block_state: dict[tuple[str, int], dict[str, Any]] = {}
        current_message: object = _MISSING
        metadata_values: dict[str, object] = {}
        usage_values: dict[str, Any] = {}
        encrypted_reasoning = False
        configured_aliases = {
            str(getattr(item, "key", ""))
            for item in (self._launch.configurations if self._launch else ())
            if getattr(item, "key", None)
        }

        def offset() -> float:
            return max(0.0, (time.monotonic() - turn_started) * 1000.0)

        def limit() -> None:
            if "capture_incomplete" not in limitations:
                limitations.append("capture_incomplete")

        def merge_usage(value: object) -> None:
            if not isinstance(value, Mapping):
                return
            usage_values.update(value)
            if "service_tier" in value:
                metadata_values["service_tier"] = value["service_tier"]

        def safe_frame(item: Mapping[str, Any]) -> Mapping[str, Any]:
            def safe_value(value: Any) -> Any:
                if isinstance(value, Mapping):
                    return {str(k): safe_value(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [safe_value(v) for v in value]
                if isinstance(value, float) and not isfinite(value):
                    return dict(_MALFORMED)
                if isinstance(value, str) and len(value) > 8_388_608:
                    return dict(_MALFORMED)
                if value is None or isinstance(value, (str, int, float, bool)):
                    return value
                return dict(_MALFORMED)

            def walk(value: Any, key: str = "") -> Any:
                if key.lower() in {"signature", "signature_delta"}:
                    digest = hashlib.sha256(
                        str(value).encode("utf-8", errors="replace")
                    ).hexdigest()
                    return {"sha256": digest}
                if isinstance(value, Mapping):
                    return {str(k): walk(v, str(k)) for k, v in value.items()}
                if isinstance(value, list):
                    return [walk(v) for v in value]
                if isinstance(value, float) and not isfinite(value):
                    return dict(_MALFORMED)
                return safe_value(value)

            result = walk(item)
            return cast(Mapping[str, Any], result)

        def emit_frame(item: Mapping[str, Any]) -> None:
            nonlocal frame_index
            sanitized = safe_frame(item)
            if self._native_error_redaction is not None:
                try:
                    sanitized = redact_for_api(
                        sanitized, config=self._native_error_redaction
                    )
                except RedactionError:
                    sanitized = {"capture": "unavailable", "reason": "redaction_failed"}
            observations.append(
                RawFrameObservation(
                    observation_id=f"claude-{sequence}-frame-{frame_index}",
                    harness_kind="claude-code",
                    turn_sequence=sequence,
                    wall_time=turn_wall_time,
                    monotonic_offset_ms=offset(),
                    direction="inbound",
                    media_type="application/json",
                    payload=cast(JsonValue, sanitized),
                    raw_evidence=RawEvidenceInput(
                        content=json.dumps(
                            sanitized, ensure_ascii=False, separators=(",", ":")
                        ),
                        media_type="application/json",
                    ),
                )
            )
            frame_index += 1

        def set_meta(name: str, value: object, *, identifier: bool = False) -> None:
            if identifier:
                value = _safe_identifier(value)
                if isinstance(value, Mapping):
                    limit()
            elif (
                (isinstance(value, float) and (not isfinite(value) or value < 0))
                or (isinstance(value, str) and len(value) > 8_388_608)
                or not isinstance(
                    value, (str, int, float, bool, type(None), Mapping, list)
                )
            ):
                value = dict(_MALFORMED)
                limit()
            metadata_values[name] = _safe_json_value(value)

        def emit_message(value: str, ident: object, role: str, complete: bool) -> None:
            nonlocal message_index
            if not value:
                return
            kwargs: dict[str, Any] = {"complete": complete}
            if ident is not _MISSING:
                kwargs["message_id"] = ident if _valid_identifier(ident) else None
                if not _valid_identifier(ident):
                    limit()
            message_role = (
                cast(Literal["user", "assistant", "system", "tool"], role)
                if role in {"user", "assistant", "system", "tool"}
                else "assistant"
            )
            try:
                observations.append(
                    MessageChunkObservation(
                        observation_id=f"claude-{sequence}-message-{message_index}",
                        harness_kind="claude-code",
                        turn_sequence=sequence,
                        wall_time=turn_wall_time,
                        monotonic_offset_ms=offset(),
                        role=message_role,
                        text=value,
                        **kwargs,
                    )
                )
                message_index += 1
            except (TypeError, ValueError):
                limit()

        def parse_block(
            block: Mapping[str, Any],
            ident: object,
            index: int,
            *,
            complete: bool,
            role: str = "assistant",
        ) -> None:
            nonlocal encrypted_reasoning, tool_result_index
            kind = block.get("type")
            key = (str(ident) if ident is not _MISSING else "", index)
            if kind in {"text", "text_delta"}:
                value = block.get("text", block.get("delta"))
                if isinstance(value, str):
                    state = block_state.setdefault(key, {"text": "", "partial": False})
                    state["kind"] = "text"
                    # A complete assistant replay repeats the partial stream;
                    # compare the exact per-block aggregate, never a suffix.
                    replay = (
                        complete
                        and state.get("partial") is True
                        and state.get("text") == value
                    )
                    if not replay:
                        emit_message(value, ident, role, complete)
                        if role == "assistant":
                            text_parts.append(value)
                    if complete:
                        state["text"] = value
                        state["complete_emitted"] = True
                    else:
                        state["text"] = str(state.get("text", "")) + value
                        state["partial"] = True
                elif value is not None:
                    limit()
            elif kind in {
                "thinking",
                "thinking_delta",
                "reasoning",
                "redacted_thinking",
                "encrypted_thinking",
                "signature_delta",
            }:
                value = block.get("thinking", block.get("text"))
                signature = block.get("signature", block.get("signature_delta"))
                reasoning_state = block_state.setdefault(
                    key, {"reasoning": "", "partial": False}
                )
                reasoning_state["kind"] = "reasoning"
                if signature is not None:
                    encrypted_reasoning = True
                    set_meta(
                        f"reasoning_signature_{len(observations)}",
                        {
                            "sha256": hashlib.sha256(
                                str(signature).encode("utf-8", errors="replace")
                            ).hexdigest()
                        },
                    )
                replay = (
                    complete
                    and reasoning_state.get("partial") is True
                    and reasoning_state.get("reasoning") == value
                )
                if isinstance(value, str) and value and not replay:
                    try:
                        observations.append(
                            ReasoningChunkObservation(
                                observation_id=f"claude-{sequence}-reasoning-{len(observations)}",
                                harness_kind="claude-code",
                                turn_sequence=sequence,
                                wall_time=turn_wall_time,
                                monotonic_offset_ms=offset(),
                                text=value,
                                visibility="visible",
                                complete=complete,
                            )
                        )
                    except (TypeError, ValueError):
                        limit()
                elif (
                    signature is not None
                    or block.get("redacted") is True
                    or kind in {"redacted_thinking", "encrypted_thinking"}
                ) and not replay:
                    encrypted_reasoning = True
                    try:
                        observations.append(
                            ReasoningChunkObservation(
                                observation_id=f"claude-{sequence}-reasoning-{len(observations)}",
                                harness_kind="claude-code",
                                turn_sequence=sequence,
                                wall_time=turn_wall_time,
                                monotonic_offset_ms=offset(),
                                text=None,
                                visibility="encrypted"
                                if signature is not None
                                else "provider_hidden",
                                complete=complete,
                            )
                        )
                    except (TypeError, ValueError):
                        limit()
                elif value is not None and not isinstance(value, str):
                    limit()
                if isinstance(value, str):
                    reasoning_state["reasoning"] = (
                        value
                        if complete
                        else str(reasoning_state.get("reasoning", "")) + value
                    )
                    reasoning_state["partial"] = not complete
                    if complete:
                        reasoning_state["complete_emitted"] = True
            elif kind == "input_json_delta":
                # Reconstructed below at content_block_stop.
                return
            elif kind == "tool_use":
                call_id, name = block.get("id"), block.get("name")
                if not _valid_identifier(call_id) or not _valid_identifier(name):
                    limit()
                    return
                call_id, name = cast(str, call_id), cast(str, name)
                server, tool = _normalize_tool_name(name)
                if server not in configured_aliases:
                    server = None
                    tool = name
                state = block_state.setdefault(
                    key,
                    {"name": name, "server": server, "tool": tool, "json": ""},
                )
                if "input" in block:
                    state["input"] = block["input"]
                state["id"] = call_id
                if call_id not in emitted_calls and complete:
                    try:
                        observations.append(
                            ToolCallObservedObservation(
                                observation_id=f"claude-{sequence}-tool-call-{len(tool_calls)}",
                                harness_kind="claude-code",
                                turn_sequence=sequence,
                                wall_time=turn_wall_time,
                                monotonic_offset_ms=offset(),
                                call_id=call_id,
                                server=server,
                                tool=tool,
                                arguments=_safe_json_value(state.get("input")),
                                status="incomplete",
                            )
                        )
                        tool_calls.append(
                            {"tool": tool, "call_id": call_id, "server": server}
                        )
                        emitted_calls.add(call_id)
                    except (TypeError, ValueError):
                        limit()
            elif kind == "tool_result":
                call_id = block.get("tool_use_id")
                if not _valid_identifier(call_id):
                    limit()
                    return
                call_id = cast(str, call_id)
                result_key = call_id
                if result_key not in emitted_results:
                    raw_error = block.get("is_error", block.get("isError"))
                    if ("is_error" in block or "isError" in block) and not isinstance(
                        raw_error, bool
                    ):
                        limit()
                        raw_error = None
                    try:
                        observations.append(
                            ToolResultObservedObservation(
                                observation_id=f"claude-{sequence}-tool-result-{tool_result_index}",
                                harness_kind="claude-code",
                                turn_sequence=sequence,
                                wall_time=turn_wall_time,
                                monotonic_offset_ms=offset(),
                                call_id=call_id,
                                result=_safe_json_value(block.get("content"))
                                if "content" in block
                                else None,
                                is_error=raw_error,
                                status="tool_error" if raw_error is True else "success",
                            )
                        )
                        emitted_results.add(result_key)
                        tool_result_index += 1
                    except (TypeError, ValueError):
                        limit()

        def parse_content(
            content: object,
            ident: object = _MISSING,
            *,
            index: int = 0,
            complete: bool = True,
            role: str = "assistant",
        ) -> None:
            if isinstance(content, Mapping):
                parse_block(content, ident, index, complete=complete, role=role)
                return
            if isinstance(content, list):
                for block_index, block in enumerate(content):
                    if isinstance(block, Mapping):
                        parse_block(
                            block, ident, block_index, complete=complete, role=role
                        )
                    else:
                        limit()
            elif content is not None:
                limit()

        def block_index(item: Mapping[str, Any]) -> int:
            value = item.get("index", 0)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
            limit()
            return 0

        def add_metadata() -> None:
            for name, value in metadata_values.items():
                observations.append(
                    _metadata(sequence, name, value, turn_wall_time, offset())
                )

        finalized = False

        def finalize() -> None:
            nonlocal finalized
            if finalized:
                return
            finalized = True
            if encrypted_reasoning:
                set_meta("encrypted_reasoning", True)
            if "cost_usd" in metadata_values:
                usage_values.setdefault("cost", metadata_values["cost_usd"])
                usage_values.setdefault("currency", "USD")
            if usage_values and _emit_usage(
                observations, sequence, turn_wall_time, offset, usage_values
            ):
                limit()
            add_metadata()

        partial_redaction = self._native_error_redaction or RedactionConfig(
            include_environment=False
        )

        def partial_evidence() -> TurnEvidence:
            # The session deadline or an outer cancellation stopped this turn
            # before it could return; keep what was already observed.
            limit()
            finalize()
            return TurnEvidence(
                sequence=sequence,
                status="cancelled",
                observations=tuple(
                    project_native_observation(item, partial_redaction, index)
                    for index, item in enumerate(observations)
                ),
                limitations=tuple(limitations),
                monotonic_origin=turn_started,
            )

        self._partial_capture = partial_evidence

        async def terminate_and_observe(phase: str = "exited") -> None:
            try:
                await owner.terminate()
            except Exception:
                pass
            if owner.stderr_task is not None and not owner.stderr_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(owner.stderr_task), timeout=0.5
                    )
                except Exception:
                    pass
            exited = _process_observation(
                self, sequence, turn_wall_time, turn_started, phase, owner
            )
            if exited is not None:
                observations.append(exited)

        if not self._process_started_emitted:
            started_observation = _process_observation(
                self, sequence, turn_wall_time, turn_started, "started", owner
            )
            if started_observation is not None:
                observations.append(started_observation)
            self._process_started_emitted = True
        try:
            owner.process.stdin.write(
                (
                    json.dumps(
                        {
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": _text(
                                            request.message.model_dump(mode="python")
                                        ),
                                    }
                                ],
                            },
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
            )
            from .._startup_deadlines import earliest

            clock = self._launch._startup_clock if self._launch is not None else None
            request_deadline = (
                clock.phase("request", None).deadline if clock is not None else None
            )
            send_deadline = earliest(deadline, request_deadline)
            await asyncio.wait_for(
                owner.process.stdin.drain(),
                timeout=None
                if send_deadline is None
                else max(0.0, send_deadline - time.monotonic()),
            )
        except (BrokenPipeError, ConnectionError):
            limit()
            await terminate_and_observe("failed")
            finalize()
            return _claude_result(
                sequence,
                "failed",
                ErrorInfo(
                    code=ErrorCode.TRANSPORT_ERROR, message="Claude Code stream failed"
                ),
                observations,
                limitations,
                turn_started,
                tool_calls,
            )
        while True:
            try:
                from .._startup_deadlines import earliest

                startup_deadline = (
                    self._launch._startup_clock.deadline
                    if self._launch is not None
                    and self._launch._startup_clock is not None
                    else None
                )
                active_deadline = earliest(
                    deadline, startup_deadline, self._startup_phase_deadline
                )
                remaining = (
                    None
                    if active_deadline is None
                    else active_deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    raise asyncio.TimeoutError
                item = await asyncio.wait_for(output.get(), timeout=remaining)
            except asyncio.TimeoutError:
                limit()
                await terminate_and_observe()
                finalize()
                return _claude_result(
                    sequence,
                    "timed_out",
                    ErrorInfo(
                        code=ErrorCode.TIMEOUT, message="Claude Code turn timed out"
                    ),
                    observations,
                    limitations,
                    turn_started,
                    tool_calls,
                )
            if item is None:
                await terminate_and_observe("failed")
                finalize()
                return _claude_result(
                    sequence,
                    "cancelled" if self._cancel_requested else "failed",
                    ErrorInfo(
                        code=ErrorCode.CANCELLED
                        if self._cancel_requested
                        else ErrorCode.TRANSPORT_ERROR,
                        message="Claude Code turn cancelled"
                        if self._cancel_requested
                        else "Claude Code stream ended",
                    ),
                    observations,
                    limitations,
                    turn_started,
                    tool_calls,
                )
            if "__adapter_error__" in item:
                limit()
                await terminate_and_observe("failed")
                finalize()
                return _claude_result(
                    sequence,
                    "failed",
                    ErrorInfo(
                        code=ErrorCode.PROTOCOL_ERROR,
                        message="Claude Code stream was invalid",
                    ),
                    observations,
                    limitations,
                    turn_started,
                    tool_calls,
                )
            emit_frame(item)
            if item.get("type") == "stream_event":
                nested = item.get("event")
                if not isinstance(nested, Mapping):
                    limit()
                    continue
                item = cast(Mapping[str, Any], nested)
            event_type = item.get("type")
            if not isinstance(event_type, str):
                limit()
                continue
            if event_type == "message_start":
                message = item.get("message")
                if isinstance(message, Mapping):
                    current_message = message.get("id", _MISSING)
                    if "model" in message:
                        metadata_values["model"] = message.get("model")
                    if "usage" in message and isinstance(message.get("usage"), Mapping):
                        merge_usage(message["usage"])
                    elif "usage" in message:
                        limit()
                elif "message" in item:
                    limit()
            elif event_type in {"system", "init"}:
                if item.get("subtype") == "init" or event_type == "init":
                    await self._startup_ready(item, owner)
                if "session_id" in item:
                    metadata_values["session_id"] = item["session_id"]
                if "model" in item:
                    metadata_values["model"] = item["model"]
            elif event_type == "content_block_start":
                parse_content(
                    item.get("content_block"),
                    current_message,
                    index=block_index(item),
                    complete=False,
                )
            elif event_type == "content_block_delta":
                parse_content(
                    item.get("delta"),
                    current_message,
                    index=block_index(item),
                    complete=False,
                )
                delta = item.get("delta")
                if (
                    isinstance(delta, Mapping)
                    and delta.get("type") == "input_json_delta"
                ):
                    key = (str(current_message), block_index(item))
                    state = block_state.setdefault(key, {"json": ""})
                    fragment = delta.get("partial_json")
                    if (
                        isinstance(fragment, str)
                        and len(state.get("json", "")) <= 1_000_000
                    ):
                        state["json"] = str(state.get("json", "")) + fragment
                    elif fragment is not None:
                        limit()
            elif event_type == "content_block_stop":
                index = block_index(item)
                stop_state = block_state.get((str(current_message), index))
                if (
                    stop_state is not None
                    and stop_state.get("kind") == "text"
                    and stop_state.get("text")
                    and not stop_state.get("complete_emitted")
                ):
                    emit_message(
                        str(stop_state["text"]), current_message, "assistant", True
                    )
                    stop_state["complete_emitted"] = True
                elif (
                    stop_state is not None
                    and stop_state.get("kind") == "reasoning"
                    and stop_state.get("reasoning")
                    and not stop_state.get("complete_emitted")
                ):
                    observations.append(
                        ReasoningChunkObservation(
                            observation_id=f"claude-{sequence}-reasoning-{len(observations)}",
                            harness_kind="claude-code",
                            turn_sequence=sequence,
                            wall_time=turn_wall_time,
                            monotonic_offset_ms=offset(),
                            text=str(stop_state["reasoning"]),
                            visibility="visible",
                            complete=True,
                        )
                    )
                    stop_state["complete_emitted"] = True
                elif stop_state is not None and stop_state.get("name") is not None:
                    block: dict[str, Any] = {
                        "type": "tool_use",
                        "id": stop_state.get("id"),
                        "name": stop_state.get("name"),
                    }
                    raw_json = stop_state.get("json", "")
                    if raw_json:
                        try:
                            parsed = json.loads(raw_json)
                            if (
                                isinstance(
                                    parsed, (Mapping, list, str, int, float, bool)
                                )
                                or parsed is None
                            ):
                                block["input"] = parsed
                        except (TypeError, ValueError):
                            limit()
                    parse_block(block, current_message, index, complete=True)
            elif event_type == "message_delta":
                delta = item.get("delta")
                if isinstance(delta, Mapping):
                    if "stop_reason" in delta:
                        metadata_values["stop_reason"] = delta["stop_reason"]
                    if isinstance(delta.get("usage"), Mapping):
                        merge_usage(delta["usage"])
                    if isinstance(item.get("usage"), Mapping):
                        merge_usage(item["usage"])
            elif event_type == "message_stop":
                pass
            elif event_type in {"assistant", "assistant_result"}:
                message = item.get("message", item)
                if isinstance(message, Mapping):
                    current_message = message.get("id", current_message)
                    if "model" in message:
                        metadata_values["model"] = message["model"]
                    if isinstance(message.get("usage"), Mapping):
                        merge_usage(message["usage"])
                    parse_content(
                        message.get("content"),
                        current_message,
                        complete=True,
                        role=str(message.get("role", "assistant")),
                    )
                    delta_value = item.get("delta", message.get("delta"))
                    if isinstance(delta_value, str):
                        parse_block(
                            {"type": "text_delta", "text": delta_value},
                            current_message,
                            0,
                            complete=False,
                        )
                    elif isinstance(delta_value, Mapping):
                        parse_content(
                            delta_value,
                            current_message,
                            complete=False,
                            role="assistant",
                        )
            elif event_type == "user":
                message = item.get("message")
                if isinstance(message, Mapping):
                    parse_content(
                        message.get("content"),
                        message.get("id", _MISSING),
                        complete=True,
                        role=str(message.get("role", "tool")),
                    )
            elif event_type in {"tool_use", "tool_call", "tool_result"}:
                parse_content([item], current_message, complete=True)
            if event_type in {"result", "turn_complete", "assistant_result"}:
                from ._native_preparation import owned_native_preparation

                if (
                    self._launch is not None
                    and owned_native_preparation(self._launch)
                    and self._launch._startup_clock is not None
                    and not self._launch._startup_clock.finished
                ):
                    raise HarnessStartupError("Claude initialization was not confirmed")
                for source_key in (
                    "session_id",
                    "model",
                    "subtype",
                    "stop_reason",
                    "service_tier",
                    "duration_api_ms",
                    "api_duration_ms",
                    "total_cost_usd",
                    "cost_usd",
                    "result",
                ):
                    if source_key in item:
                        metadata_key = {
                            "subtype": "result_subtype",
                            "duration_api_ms": "api_duration_ms",
                            "cost_usd": "cost_usd",
                            "total_cost_usd": "cost_usd",
                        }.get(source_key, source_key)
                        metadata_values[metadata_key] = item[source_key]
                if "usage" in item and isinstance(item.get("usage"), Mapping):
                    merge_usage(item["usage"])
                raw_failed = item.get("is_error", item.get("isError", False))
                malformed_error = (
                    "is_error" in item or "isError" in item
                ) and not isinstance(raw_failed, bool)
                if malformed_error:
                    limit()
                failed = (
                    (raw_failed is True)
                    or (
                        isinstance(metadata_values.get("result_subtype"), str)
                        and metadata_values["result_subtype"] in {"error", "failure"}
                    )
                    or malformed_error
                )
                for name in (
                    "session_id",
                    "model",
                    "message_id",
                    "stop_reason",
                    "result_subtype",
                    "service_tier",
                ):
                    value = metadata_values.get(
                        name, current_message if name == "message_id" else _MISSING
                    )
                    if value is not _MISSING:
                        set_meta(name, value, identifier=True)
                for name in ("api_duration_ms", "cost_usd", "result"):
                    if name in metadata_values:
                        value = metadata_values[name]
                        if failed and name == "result":
                            value = native_error_info(
                                value,
                                summary="Claude Code turn failed",
                                harness="claude-code",
                                phase="turn/result",
                                redaction=self._native_error_redaction,
                            ).details.get(
                                "native_message", "native diagnostic unavailable"
                            )
                        set_meta(name, value)
                if failed:
                    await terminate_and_observe("failed")
                finalize()
                result_text = "".join(text_parts)
                if not result_text and isinstance(metadata_values.get("result"), str):
                    result_text = cast(str, metadata_values["result"])
                return _claude_result(
                    sequence,
                    "failed" if failed else "completed",
                    native_error_info(
                        _claude_native_error(item),
                        summary="Claude Code turn failed",
                        harness="claude-code",
                        phase="turn/result",
                        redaction=self._native_error_redaction,
                        code=ErrorCode.PROTOCOL_ERROR
                        if malformed_error
                        else ErrorCode.TRANSPORT_ERROR,
                    )
                    if failed
                    else None,
                    observations,
                    limitations,
                    turn_started,
                    tool_calls,
                    result_text,
                )


class ClaudeCodeSession(NativeSessionBase):
    def __init__(
        self,
        adapter: ClaudeCodeHarnessAdapter,
        owner: ProcessOwner,
        capabilities: HarnessAdapterCapabilities,
        session_id: str,
    ) -> None:
        super().__init__(
            owner,
            capabilities,
            session_id,
            server_configuration_count=len(adapter._launch.configurations)
            if adapter._launch is not None
            else 0,
            capture=adapter._launch.capture if adapter._launch is not None else None,
        )
        self._adapter = adapter

    async def send(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        if self._closed:
            raise HarnessAdapterError("Claude Code session is closed")
        self._turns += 1
        return await self._adapter._send(request, self._turns)


__all__ = ["ClaudeCodeHarnessAdapter", "ClaudeCodeSession"]


def _claude_native_error(item: Mapping[str, Any]) -> object:
    """Select the native result diagnostic without exposing its whole frame."""

    if item.get("error") is not None:
        return item["error"]
    errors = item.get("errors")
    if isinstance(errors, list):
        # Claude emits a list of textual causes for an error result. Bound
        # its size before joining; oversized diagnostics are omitted.
        if len(errors) <= 16 and all(
            isinstance(value, str) and len(value) <= 4096 for value in errors
        ):
            return {"code": item.get("subtype"), "message": "\n".join(errors)}
        return {"code": item.get("subtype")}
    return {"code": item.get("subtype"), "message": item.get("result")}
