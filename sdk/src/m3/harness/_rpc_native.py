"""Small bounded JSON-lines process core used by native harness adapters.

This module deliberately contains no ACP translation.  It owns one provider
process, correlates its input/output stream, and leaves provider dialect
messages to the Codex and Pi adapters.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import JsonValue

from ..agent_session import AdapterTurn
from ..policy import ToolDescriptor, ToolPolicyEvaluator, ToolPolicyEvidence
from ..trace.redaction import RedactionConfig, redact_for_api
from ..types import (
    Capability,
    CapabilityStatus,
    ErrorCode,
    ErrorInfo,
    FullToolPolicy,
    NativeToolPolicy,
    Readiness,
    RestrictiveToolPolicy,
    TextContent,
    TurnOutcome,
    TurnResponse,
    UserMessage,
)
from ._native_errors import (
    NativeHarnessStartupError,
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
    MAX_FRAME_BYTES,
    NativeSessionBase,
    ProcessOwner,
    _executable,
    _isolated_environment,
    read_bounded_line,
    startup_spawn_options,
    workspace_for_launch,
)
from .observations import (
    HarnessObservation,
    ProcessObservedObservation,
    RawEvidenceInput,
    RawFrameObservation,
    TurnEvidence,
)

if TYPE_CHECKING:
    from ._launch_credentials import LaunchCredentialBundle, NativeCredentialMaterial
    from ._native_preparation import NativePreparation
    from .admission import StartupLease


def _json_safe(value: Any, *, limit: int = 8_388_608) -> JsonValue | None:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, limit=limit) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > limit:
            return {"capture": "unavailable", "reason": "oversized_source"}
        return value
    return {"capture": "unavailable", "reason": "unsupported_source"}


class JsonRpcProcess:
    """One process with bounded JSONL reads and request correlation."""

    def __init__(self, executable: str, argv: tuple[str, ...] = ()) -> None:
        self.executable = _executable(executable, executable)
        self.argv = argv
        self.root: Path | None = None
        self.owner: ProcessOwner | None = None
        self._reader: asyncio.Task[None] | None = None
        self._frames: asyncio.Queue[Mapping[str, Any] | None] = asyncio.Queue(
            maxsize=128
        )
        self._closed = False

    async def open(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        root: Path | None = None,
        startup_lease: StartupLease | None = None,
        owned_startup: bool = False,
        startup_deadline: float | None = None,
        prepared: bool = False,
    ) -> None:
        self.root = root or Path(tempfile.mkdtemp(prefix="m3-native-"))
        self.owner = ProcessOwner(self.root)
        env = _isolated_environment(self.root, environment, prepared=prepared)
        await self.owner.spawn(
            [self.executable, *self.argv],
            env,
            cwd=cwd,
            **startup_spawn_options(
                startup_lease,
                owned_startup=owned_startup,
                startup_deadline=startup_deadline,
            ),
        )
        self._reader = asyncio.create_task(self._read_stdout())

    async def _read_stdout(self) -> None:
        owner = self.owner
        if owner is None or owner.process is None or owner.process.stdout is None:
            return
        try:
            while True:
                line = await read_bounded_line(owner.process.stdout)
                if line is None:
                    break
                try:
                    value = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    value = {"__invalid_frame__": True}
                if not isinstance(value, Mapping):
                    value = {"__invalid_frame__": True}
                await self._frames.put(dict(value))
        except (HarnessStartupError, asyncio.CancelledError):
            if not self._closed:
                await self._frames.put({"__invalid_frame__": True})
            return
        if not self._closed:
            await self._frames.put(None)

    async def write(self, payload: Mapping[str, Any]) -> None:
        if self._closed:
            raise HarnessAdapterError("harness process is closed")
        if (
            self.owner is None
            or self.owner.process is None
            or self.owner.process.stdin is None
        ):
            raise HarnessAdapterError("harness process is not open")
        try:
            encoded = (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            if len(encoded) > MAX_FRAME_BYTES:
                raise HarnessStartupError("harness request exceeded safe frame size")
            self.owner.process.stdin.write(encoded)
            await self.owner.process.stdin.drain()
        except (BrokenPipeError, ConnectionError, asyncio.IncompleteReadError):
            raise HarnessAdapterError("harness process closed its input") from None

    async def next(self, timeout: float | None = None) -> Mapping[str, Any] | None:
        if self._closed:
            return None
        try:
            return await asyncio.wait_for(self._frames.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise

    async def close(self) -> None:
        if self._closed:
            return
        if self.owner is not None:
            await self.owner.close()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self.root is not None and (
            self.owner is None or self.owner._owned_preparation is None
        ):
            shutil.rmtree(self.root, ignore_errors=True)
        self._closed = True


class NativeRPCSession(NativeSessionBase):
    def __init__(
        self, adapter: NativeRPCAdapter, process: JsonRpcProcess, session_id: str
    ) -> None:
        if process.owner is None:
            raise HarnessAdapterError("harness process owner is unavailable")
        super().__init__(
            process.owner,
            adapter.capabilities,
            session_id,
            server_configuration_count=len(adapter._launch.configurations)
            if adapter._launch
            else 0,
            capture=adapter._launch.capture if adapter._launch else None,
        )
        self._adapter = adapter
        self._process = process

    @property
    def turn_count(self) -> int:
        """Number of turns already delivered through this session."""

        return self._turns

    def _set_managed_input_runtime(self, runtime: Any) -> None:
        """Bind the controller-owned runtime to this native session."""

        setter = getattr(self._adapter, "_set_managed_input_runtime", None)
        if not callable(setter):
            raise HarnessAdapterError(
                "native harness does not implement managed interaction delivery"
            )
        setter(runtime)

    async def send(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        if self._closed:
            raise HarnessAdapterError("harness session is closed")
        self._turns += 1
        return await self._adapter._send(request, self._turns, self._process)

    async def cancel(self) -> None:
        await self._adapter._cancel(self._process)

    async def close(self) -> None:
        if self._closed:
            return
        await self._process.close()
        self._closed = True


class NativeRPCAdapter:
    """Provider-neutral lifecycle shell; subclasses provide wire dialects."""

    managed_runtime_supported = True

    harness_kind = "native"
    executable_name = "native"
    interaction_capabilities = HarnessInteractionCapabilities()

    def __init__(
        self, *, executable: str, environment: Mapping[str, str] | None = None
    ) -> None:
        self.executable = _executable(executable, self.executable_name)
        self.environment = dict(environment or {})
        self._resolver_environment = environment
        self._credential_source: HarnessLaunch | None = None
        self._prepared_credentials: LaunchCredentialBundle | None = None
        self._capabilities = HarnessAdapterCapabilities(
            name=self.harness_kind,
            supports_streaming=True,
            supports_tool_policy=False,
            interaction=self.interaction_capabilities,
        )
        self._launch: HarnessLaunch | None = None
        self._session: NativeRPCSession | None = None
        self._process: JsonRpcProcess | None = None
        self._failed_preparation: NativePreparation | None = None
        self._startup_cleanup_failed = False
        self._startup_cleanup_task: asyncio.Task[None] | None = None
        self._cancel_requested = False
        self._terminal_status: Literal["failed", "cancelled", "interrupted"] | None = (
            None
        )
        # Why the provider failed the turn, when the adapter can say so from
        # structured protocol fields; otherwise the failure stays generic.
        self._terminal_error: ErrorInfo | None = None
        self.last_policy_evidence: ToolPolicyEvidence | None = None
        self._runtime_secrets: set[str] = set()
        self._native_error_redaction: RedactionConfig | None = None
        self._native_error_registry_unavailable = False
        self._process_observed = False
        self._partial_capture: Callable[[], TurnEvidence] | None = None

    @property
    def name(self) -> str:
        return self._capabilities.name

    @property
    def capabilities(self) -> HarnessAdapterCapabilities:
        return self._capabilities

    @property
    def supported_content_kinds(self) -> frozenset[str]:
        return self._capabilities.supported_content_kinds

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
                        name=f"harness:{self.name}",
                        status=CapabilityStatus.UNAVAILABLE,
                        reason="executable unavailable",
                    ),
                ),
                reason="executable unavailable",
            )
        unsupported = {
            block.kind
            for block in (
                launch.spec.message.content if launch.spec.message is not None else ()
            )
            if block.kind not in self._capabilities.supported_content_kinds
        }
        if unsupported:
            return self._capabilities.readiness(
                ready=False, reason="attachment_unsupported"
            )
        self.last_policy_evidence = None
        self._capabilities = replace(self._capabilities, supports_tool_policy=False)
        policy = launch.tool_policy
        # Native policies are deliberately not accepted by this shared shell:
        # subclasses must opt in with a real provider-specific implementation.
        if isinstance(policy, NativeToolPolicy):
            return self._capabilities.readiness(
                ready=False, reason="tool_policy_unsupported"
            )
        policy_requested = isinstance(policy, FullToolPolicy) or (
            isinstance(policy, RestrictiveToolPolicy)
            and bool(policy.allowed_tools or policy.denied_tools)
        )
        if policy_requested:
            available_connections = tuple(
                str(config.connection_id)
                for config in launch.configurations
                if config.available
            )
            enforcement = getattr(launch.capture, "can_enforce_portable_policy", None)
            if not callable(enforcement):
                enforcement = getattr(launch.capture, "enforces_portable_policy", None)
            if not callable(enforcement) or not enforcement(available_connections):
                return self._capabilities.readiness(
                    ready=False, reason="tool_policy_unsupported"
                )
            descriptors = tuple(
                ToolDescriptor(server=record.key, name=tool)
                for record in launch.servers.records
                if record.available
                for tool in record.tools
            )
            try:
                self.last_policy_evidence = ToolPolicyEvaluator(descriptors).preflight(
                    policy,
                    harness_name=self.name,
                    supports_enforcement=True,
                )
            except Exception:
                self.last_policy_evidence = None
                return self._capabilities.readiness(
                    ready=False, reason="tool_policy_unsupported"
                )
            self._capabilities = replace(self._capabilities, supports_tool_policy=True)
        return self._capabilities.readiness()

    async def prepare_launch_credentials(self, launch: HarnessLaunch) -> HarnessLaunch:
        from ._launch_credentials import activate_native_launch

        source = launch
        if launch._credentials is None:
            launch = replace(
                launch, _credentials=self._credentials_for_preparation(launch)
            )
        effective = await activate_native_launch(
            launch,
            resolver_environment=self._resolver_environment,
            child_environment=self.environment,
        )
        self._credential_source = source
        self._prepared_credentials = effective._credentials
        return effective

    def _credentials_for_preparation(
        self, launch: HarnessLaunch, *, material: NativeCredentialMaterial | None = None
    ) -> LaunchCredentialBundle:
        """Retain compatibility with direct native environment preparation."""

        from ._launch_credentials import collect_native_credentials

        if launch._credentials is not None:
            launch._credentials.validate_selection(launch)
            launch._credentials.validate_inputs(
                launch.configurations,
                effective=launch._credentials.effective_inputs_bound,
            )
            return launch._credentials
        if self._credential_source is launch and self._prepared_credentials is not None:
            self._prepared_credentials.validate_selection(launch)
            self._prepared_credentials.validate_inputs(
                launch.configurations,
                effective=self._prepared_credentials.effective_inputs_bound,
            )
            return self._prepared_credentials
        if self._session is not None:
            raise HarnessStartupError("native credential session is already open")
        if self._prepared_credentials is not None:
            from ..transport.capture_proxy import McpCaptureManager

            previous_capture = getattr(self._credential_source, "capture", None)
            if isinstance(
                previous_capture, McpCaptureManager
            ) and previous_capture.owns_credential_bundle(self._prepared_credentials):
                raise HarnessStartupError("native credential owner is still active")
            self._prepared_credentials.close()
        credentials = collect_native_credentials(
            launch,
            resolver_environment=self._resolver_environment,
            child_environment=self.environment,
            material=material
            if material is not None
            else self._credential_material(launch),
        )
        self._credential_source = launch
        self._prepared_credentials = credentials
        return credentials

    def _credential_material(self, launch: HarnessLaunch) -> NativeCredentialMaterial:
        from ._launch_credentials import NativeCredentialMaterial

        return NativeCredentialMaterial()

    async def _close_credential_operations(self) -> None:
        pass

    async def open(self, launch: HarnessLaunch) -> HarnessSession:
        from ._native_preparation import validate_owned_startup

        validate_owned_startup(launch)
        readiness = await self.preflight(launch)
        if not readiness.ready:
            raise HarnessStartupError(f"{self.name} executable is unavailable")
        if self._session is not None:
            raise HarnessStartupError(f"{self.name} session is already open")
        launch = await self.prepare_launch_credentials(launch)
        from ._native_preparation import NativePreparation, owned_native_preparation

        preparation = None
        if owned_native_preparation(launch):
            preparation = await NativePreparation.create(
                launch, self.harness_kind, self.native_configuration_files(launch)
            )
            root = preparation.root
        else:
            root = Path(tempfile.mkdtemp(prefix=f"m3-{self.name}-"))
        process = JsonRpcProcess(self.executable, self.process_argv(launch))
        self._process = process
        self._launch = launch
        self._failed_preparation = preparation
        try:
            # Keep provider state isolated while allowing only explicit adapter
            # overrides and credentials to reach the child.
            from ._launch_credentials import check_credential_deadline

            environment = self.environment_for_launch(launch, root)
            workspace = workspace_for_launch(launch, root)
            check_credential_deadline(launch)
            if preparation is not None and launch._startup_clock is not None:
                launch._startup_clock.clear_owned_preparation()
                launch._startup_clock.begin_native()
            await process.open(
                environment=environment,
                cwd=workspace,
                root=root,
                **startup_spawn_options(
                    launch._startup_lease,
                    owned_startup=preparation is not None,
                    startup_deadline=launch._startup_clock.deadline
                    if launch._startup_clock is not None
                    and (preparation is not None or launch._startup_lease is not None)
                    else None,
                ),
                **({"prepared": True} if preparation is not None else {}),
            )
            assert process.owner is not None
            if preparation is not None:
                preparation.adopt(process.owner)
            session_id = await self.initialize(process, launch)
            await process.owner.startup_ready()
            if launch._startup_clock is not None:
                launch._startup_clock.finish()
            effective_launch = launch
            if (
                launch.tool_policy_evidence is None
                and self.last_policy_evidence is not None
            ):
                effective_launch = launch.with_tool_policy_evidence(
                    self.last_policy_evidence
                )
            self._launch, self._process = effective_launch, process
            self._session = NativeRPCSession(self, process, session_id)
            self._failed_preparation = None
            return self._session
        except BaseException as failure:
            from .._startup_deadlines import retain_cleanup, wait_cleanup

            if process.owner is not None and preparation is not None:
                preparation.adopt(process.owner)

            async def cleanup_failed_process() -> None:
                await process.close()
                if process.owner is None and preparation is not None:
                    await preparation.close()

            self._startup_cleanup_task = retain_cleanup(cleanup_failed_process)
            stopped = await wait_cleanup(
                self._startup_cleanup_task,
                launch._startup_clock,
                cancelled=isinstance(failure, asyncio.CancelledError),
            )
            self._startup_cleanup_failed = not stopped
            if stopped:
                self._failed_preparation = None
            raise

    def native_configuration_files(self, launch: HarnessLaunch) -> Mapping[str, bytes]:
        raise HarnessStartupError("owned startup is unavailable for this harness")

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AdapterTurn:
        del metadata
        if self._session is None:
            raise HarnessAdapterError("harness session is not open")
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
                "failed": TurnOutcome.FAILED,
            }[result.status],
            tool_calls=result.tool_calls,
            _private_tool_calls=result._private_tool_calls,
            evidence=result.evidence,
            trace_limitations=result.trace_limitations,
            turn_evidence=result.turn_evidence,
        )

    async def cancel(self) -> None:
        if self._session is not None:
            await self._session.cancel()

    def take_partial_turn_evidence(self) -> TurnEvidence | None:
        """Return observations of a turn cancelled before it returned, once."""

        capture, self._partial_capture = self._partial_capture, None
        if capture is None:
            return None
        evidence = capture()
        redaction = self._public_redaction()
        return evidence.model_copy(
            update={
                "observations": tuple(
                    project_native_observation(item, redaction, index)
                    for index, item in enumerate(evidence.observations)
                )
            }
        )

    def process_argv(self, launch: HarnessLaunch) -> tuple[str, ...]:
        return ()

    def environment_for_launch(
        self, launch: HarnessLaunch, root: Path
    ) -> Mapping[str, str]:
        return self._credentials_for_preparation(launch).child_environment()

    async def initialize(self, process: JsonRpcProcess, launch: HarnessLaunch) -> str:
        return str(uuid4())

    async def _cancel(self, process: JsonRpcProcess) -> None:
        self._cancel_requested = True
        await process.close()

    async def _send(
        self, request: HarnessTurnRequest, sequence: int, process: JsonRpcProcess
    ) -> HarnessTurnResult:
        result = await self._send_private(request, sequence, process)
        return project_native_turn(
            result,
            self._public_redaction(),
        )

    def _public_redaction(self) -> RedactionConfig | None:
        if self._native_error_registry_unavailable:
            return None
        return self._native_error_redaction or RedactionConfig(
            secrets=frozenset(self._runtime_secrets), include_environment=False
        )

    async def _send_private(
        self, request: HarnessTurnRequest, sequence: int, process: JsonRpcProcess
    ) -> HarnessTurnResult:
        started = time.monotonic()
        self._cancel_requested = False
        self._terminal_status = None
        self._terminal_error = None
        wall = datetime.now(timezone.utc)
        observations: list[HarnessObservation] = list(
            self.initial_observations(sequence, wall, started)
        )
        if not self._process_observed:
            pid = (
                process.owner.process.pid
                if process.owner is not None and process.owner.process is not None
                else None
            )
            observations.insert(
                0,
                ProcessObservedObservation(
                    observation_id=f"{self.name}-process-started",
                    harness_kind=self.name,
                    turn_sequence=sequence,
                    wall_time=wall,
                    monotonic_offset_ms=0,
                    phase="started",
                    executable=self.executable,
                    pid=pid,
                    stderr_state="unavailable",
                ),
            )
            self._process_observed = True
        tool_calls: list[Mapping[str, Any]] = []
        text_parts: list[str] = []
        limitations: list[str] = []
        partial_redaction = self._public_redaction()

        def partial_evidence() -> TurnEvidence:
            # The session deadline or an outer cancellation stopped this turn
            # before it could return; keep what was already observed.
            return TurnEvidence(
                sequence=sequence,
                status="cancelled",
                observations=tuple(
                    project_native_observation(item, partial_redaction, index)
                    for index, item in enumerate(observations)
                ),
                limitations=tuple(dict.fromkeys((*limitations, "capture_incomplete"))),
                monotonic_origin=started,
            )

        self._partial_capture = partial_evidence
        try:
            await self.send_turn(process, request, sequence)
            terminal = False
            while not terminal:
                remaining = (
                    None
                    if request.timeout_seconds is None
                    else max(
                        0.001, request.timeout_seconds - (time.monotonic() - started)
                    )
                )
                frame = await self.next_frame(process, remaining)
                if frame is None:
                    return_code = (
                        process.owner.process.returncode
                        if process.owner is not None
                        and process.owner.process is not None
                        else None
                    )
                    observations.append(
                        ProcessObservedObservation(
                            observation_id=f"{self.name}-process-exited-{sequence}",
                            harness_kind=self.name,
                            turn_sequence=sequence,
                            wall_time=wall,
                            monotonic_offset_ms=max(
                                0.0, (time.monotonic() - started) * 1000
                            ),
                            phase="exited",
                            executable=self.executable,
                            exit_code=return_code,
                            stderr_state="unavailable",
                        )
                    )
                    raise HarnessAdapterError(
                        "harness process ended before turn completion"
                    )
                if frame.get("__invalid_frame__"):
                    limitations.append("capture_incomplete")
                    raise HarnessAdapterError("harness response was invalid")
                raw = _json_safe(frame)
                try:
                    registry = self._public_redaction()
                    raw = (
                        redact_for_api(raw, config=registry)
                        if registry is not None
                        else {
                            "capture": "unavailable",
                            "reason": "unavailable_registry",
                        }
                    )
                except Exception:
                    raw = {"capture": "unavailable", "reason": "redaction_failed"}
                observations.append(
                    RawFrameObservation(
                        observation_id=f"{self.name}-{sequence}-frame-{len(observations)}",
                        harness_kind=self.name,
                        turn_sequence=sequence,
                        wall_time=wall,
                        monotonic_offset_ms=max(
                            0.0, (time.monotonic() - started) * 1000
                        ),
                        payload=raw,
                        raw_evidence=RawEvidenceInput(
                            content=json.dumps(raw, separators=(",", ":")),
                            media_type="application/json",
                        ),
                    )
                )
                # Native identities are decoded only in process memory.
                # Evidence projection must never change request correlation.
                terminal, text, calls = self.consume_frame(
                    frame, sequence, wall, started, observations
                )
                if text:
                    text_parts.append(text)
                tool_calls.extend(calls)
            status: Literal[
                "completed", "failed", "timed_out", "cancelled", "interrupted"
            ] = self._terminal_status or "completed"
            response = TurnResponse(content=(TextContent(text="".join(text_parts)),))
            evidence = TurnEvidence(
                sequence=sequence,
                status=status,
                observations=tuple(observations),
                limitations=tuple(dict.fromkeys(limitations)),
                monotonic_origin=started,
            )
            return HarnessTurnResult(
                sequence=sequence,
                status=status,
                response=response if status == "completed" else None,
                error=None
                if status == "completed"
                else self._terminal_error
                if status == "failed" and self._terminal_error is not None
                else ErrorInfo(
                    code=ErrorCode.CANCELLED
                    if status in {"cancelled", "interrupted"}
                    else ErrorCode.TRANSPORT_ERROR,
                    message="harness turn failed",
                ),
                tool_calls=tuple(tool_calls),
                turn_evidence=evidence,
                evidence={
                    "adapter": self.name,
                    "session_id": self._session.session_id if self._session else None,
                },
            )
        except asyncio.TimeoutError:
            limitations.append("capture_incomplete")
            try:
                await self._cancel(process)
            except Exception:
                # A provider-specific cancellation path may fail while the
                # turn is already timed out; preserve the typed timeout.
                limitations.append("cleanup_failed")
            # Provider cancellation is asynchronous.  Its acknowledgement
            # and terminal frames can remain queued after _cancel returns;
            # close the process so they cannot be consumed by a later turn.
            try:
                await process.close()
            except Exception:
                # Cancellation already determined the turn outcome.  Keep the
                # timeout result stable while recording failed cleanup.
                limitations.append("cleanup_failed")
            evidence = TurnEvidence(
                sequence=sequence,
                status="timed_out",
                observations=tuple(observations),
                limitations=tuple(dict.fromkeys(limitations)),
                monotonic_origin=started,
            )
            return HarnessTurnResult(
                sequence=sequence,
                status="timed_out",
                error=ErrorInfo(
                    code=ErrorCode.TIMEOUT, message="harness turn timed out"
                ),
                turn_evidence=evidence,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure_status: Literal["failed", "cancelled"] = (
                "cancelled" if self._cancel_requested else "failed"
            )
            code = (
                ErrorCode.CANCELLED
                if failure_status == "cancelled"
                else ErrorCode.PROTOCOL_ERROR
            )
            evidence = TurnEvidence(
                sequence=sequence,
                status=failure_status,
                observations=tuple(observations),
                limitations=tuple(dict.fromkeys(limitations or ["capture_incomplete"])),
                monotonic_origin=started,
            )
            return HarnessTurnResult(
                sequence=sequence,
                status=failure_status,
                error=exc.error
                if isinstance(exc, NativeHarnessStartupError)
                and failure_status == "failed"
                else ErrorInfo(code=code, message="harness turn failed"),
                turn_evidence=evidence,
            )

    async def send_turn(
        self, process: JsonRpcProcess, request: HarnessTurnRequest, sequence: int
    ) -> None:
        raise NotImplementedError

    async def next_frame(
        self, process: JsonRpcProcess, timeout: float | None
    ) -> Mapping[str, Any] | None:
        return await process.next(timeout)

    def initial_observations(
        self, sequence: int, wall: datetime, started: float
    ) -> tuple[HarnessObservation, ...]:
        del sequence, wall, started
        return ()

    def consume_frame(
        self,
        frame: Mapping[str, Any],
        sequence: int,
        wall: datetime,
        started: float,
        observations: list[HarnessObservation],
    ) -> tuple[bool, str, list[Mapping[str, Any]]]:
        raise NotImplementedError

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
        if self._session is not None:
            await self._session.close()
        elif self._process is not None:
            await self._process.close()
        if self._failed_preparation is not None:
            await self._failed_preparation.close()
            self._failed_preparation = None
        await self._close_credential_operations()
        from ..transport.capture_proxy import McpCaptureManager

        if self._prepared_credentials is not None:
            capture = getattr(self._credential_source, "capture", None)
            if not isinstance(
                capture, McpCaptureManager
            ) or not capture.owns_credential_bundle(self._prepared_credentials):
                self._prepared_credentials.close()
        self._prepared_credentials = None
        self._credential_source = None
        self._session = None
        self._process = None
        self._runtime_secrets.clear()
        self._native_error_redaction = None
        self._native_error_registry_unavailable = False
        self._process_observed = False
