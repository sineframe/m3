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
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
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
    workspace_for_launch,
)
from .observations import (
    HarnessObservation,
    ProcessObservedObservation,
    RawEvidenceInput,
    RawFrameObservation,
    TurnEvidence,
)


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
    ) -> None:
        self.root = root or Path(tempfile.mkdtemp(prefix="m3-native-"))
        self.owner = ProcessOwner(self.root)
        env = _isolated_environment(self.root, environment)
        await self.owner.spawn([self.executable, *self.argv], env, cwd=cwd)
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
        self._closed = True
        if self.owner is not None:
            await self.owner.close()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)


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
        self._capabilities = HarnessAdapterCapabilities(
            name=self.harness_kind,
            supports_streaming=True,
            supports_tool_policy=False,
            interaction=self.interaction_capabilities,
        )
        self._launch: HarnessLaunch | None = None
        self._session: NativeRPCSession | None = None
        self._process: JsonRpcProcess | None = None
        self._cancel_requested = False
        self._terminal_status: Literal["failed", "cancelled", "interrupted"] | None = (
            None
        )
        self.last_policy_evidence: ToolPolicyEvidence | None = None
        self._runtime_secrets: set[str] = set()
        self._process_observed = False

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
        if (
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

    async def open(self, launch: HarnessLaunch) -> HarnessSession:
        readiness = await self.preflight(launch)
        if not readiness.ready:
            raise HarnessStartupError(f"{self.name} executable is unavailable")
        if self._session is not None:
            raise HarnessStartupError(f"{self.name} session is already open")
        root = Path(tempfile.mkdtemp(prefix=f"m3-{self.name}-"))
        process = JsonRpcProcess(self.executable, self.process_argv(launch))
        try:
            # Keep provider state isolated while allowing only explicit adapter
            # overrides and credentials to reach the child.
            await process.open(
                environment=self.environment_for_launch(launch, root),
                cwd=workspace_for_launch(launch, root),
                root=root,
            )
            session_id = await self.initialize(process, launch)
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
            return self._session
        except Exception:
            await process.close()
            raise

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
        result = await self._session.send(
            HarnessTurnRequest.from_message(message, timeout_seconds=timeout)
        )
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
            evidence=result.evidence,
            trace_limitations=result.trace_limitations,
            turn_evidence=result.turn_evidence,
        )

    async def cancel(self) -> None:
        if self._session is not None:
            await self._session.cancel()

    def process_argv(self, launch: HarnessLaunch) -> tuple[str, ...]:
        return ()

    def environment_for_launch(
        self, launch: HarnessLaunch, root: Path
    ) -> Mapping[str, str]:
        return self.environment

    async def initialize(self, process: JsonRpcProcess, launch: HarnessLaunch) -> str:
        return str(uuid4())

    async def _cancel(self, process: JsonRpcProcess) -> None:
        self._cancel_requested = True
        await process.close()

    async def _send(
        self, request: HarnessTurnRequest, sequence: int, process: JsonRpcProcess
    ) -> HarnessTurnResult:
        started = time.monotonic()
        self._cancel_requested = False
        self._terminal_status = None
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
                    raw = redact_for_api(
                        raw,
                        config=RedactionConfig.from_environment(
                            secrets=self._runtime_secrets
                        ),
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
                safe_frame = (
                    raw if isinstance(raw, Mapping) else {"__invalid_frame__": True}
                )
                terminal, text, calls = self.consume_frame(
                    safe_frame, sequence, wall, started, observations
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
            )
            return HarnessTurnResult(
                sequence=sequence,
                status=status,
                response=response if status == "completed" else None,
                error=None
                if status == "completed"
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
        except Exception:
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
            )
            return HarnessTurnResult(
                sequence=sequence,
                status=failure_status,
                error=ErrorInfo(code=code, message="harness turn failed"),
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
        if self._session is not None:
            await self._session.close()
        self._session = None
        self._process = None
        self._runtime_secrets.clear()
        self._process_observed = False
