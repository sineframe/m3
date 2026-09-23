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
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..agent_session import AdapterTurn
from ..elicitation import ElicitationPlan, PendingElicitationRound
from ..errors import OperationCancelled, OperationTimeout
from ..types import (
    ErrorCode,
    ErrorInfo,
    NativeToolPolicy,
    Pi,
    Readiness,
    SecretReference,
    TurnOutcome,
)
from ._rpc_native import JsonRpcProcess, NativeRPCAdapter
from .contracts import (
    HarnessInteractionCapabilities,
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
from .pi_control import (
    MAX_CONTROL_ROUND_LIMIT,
    PiControlChannel,
    PiControlClosed,
)
from .pi_extension.bridge import (
    ActionContextChannel,
    BridgeActionStatus,
    qualified_tool_name,
)


class PiHarnessAdapter(NativeRPCAdapter):
    """One persistent ``pi --mode rpc`` conversation."""

    harness_kind = "pi"
    managed_input_round_limit_max = MAX_CONTROL_ROUND_LIMIT
    executable_name = "pi"
    interaction_capabilities = HarnessInteractionCapabilities(
        supports_elicitation=True,
        preserves_request_keys=True,
        preserves_multi_request_rounds=True,
        supports_interaction_cancellation=True,
        supports_interaction_resume=False,
        supports_idempotent_response_delivery=False,
        retry_owner="m3",
    )

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
        self._action_channel: ActionContextChannel | None = None
        self._action_generation: str | None = None
        self._control_channel: PiControlChannel | None = None
        self._managed_input_runtime: Any = None
        self._buffered_native_frame: Mapping[str, Any] | None = None
        self._buffered_native_ready = False
        self._buffered_control_frame: Mapping[str, Any] | None = None
        self._managed_cancel_event = asyncio.Event()

    @property
    def control_channel(self) -> PiControlChannel | None:
        """The session-scoped managed-control transport, if opened."""

        return self._control_channel

    def _set_managed_input_runtime(self, runtime: Any) -> None:
        self._managed_input_runtime = runtime

    def _discard_turn_buffers(self) -> None:
        """Drop frames that can only belong to the current managed turn."""

        self._buffered_native_frame = None
        self._buffered_native_ready = False
        self._buffered_control_frame = None

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
        version = await asyncio.to_thread(probe_help, self.executable, ("--version",))
        interaction = (
            self.interaction_capabilities
            if version is not None and version.strip() == "0.85.1"
            else HarnessInteractionCapabilities(retry_owner="m3")
        )
        self._capabilities = replace(
            self._capabilities,
            interaction=interaction,
        )
        # Ordinary Pi RPC remains usable. Only action-bound elicitation is
        # withheld until the installed version has evidence for this gate.
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
        control = PiControlChannel()
        await control.start()
        self._control_channel = control
        self._launch_environment.update(control.environment)
        self._runtime_secrets.add(control.token)
        if callable(add_secrets):
            add_secrets({control.token})
        try:
            session = await super().open(launch)
            try:
                await control.wait_connected()
            except PiControlClosed as error:
                raise HarnessStartupError(
                    "Pi managed-control extension did not authenticate"
                ) from error
            return session
        except BaseException:
            await super().close()
            await control.close("adapter_open_failed")
            self._control_channel = None
            raise

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
        self._action_channel = ActionContextChannel(
            root / "pi-action-context.json", root / "pi-action-status.json"
        )
        environment = dict(self._launch_environment or self.environment)
        environment["PI_SKIP_VERSION_CHECK"] = "1"
        environment["PI_TELEMETRY"] = "0"
        environment["M3_PI_TOOL_MAP"] = str(path)
        environment["M3_PI_ACTION_CONTEXT"] = str(self._action_channel.context_path)
        environment["M3_PI_ACTION_STATUS"] = str(self._action_channel.status_path)
        self._launch_environment = environment
        return environment

    async def send(
        self,
        message: Any,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
        elicitation: ElicitationPlan | None = None,
        elicitation_round_limit: int = 10,
    ) -> AdapterTurn:
        if self._session is None or self._action_channel is None:
            raise HarnessStartupError("Pi action channel is unavailable")
        if self._control_channel is not None:
            self._control_channel.set_scope(None)
        self._discard_turn_buffers()
        generation = uuid4().hex
        sequence = self._session.turn_count + 1
        self._action_generation = generation
        self._managed_cancel_event.clear()
        identity = self._managed_identity()
        self._action_channel.write_context(
            generation=generation,
            turn_sequence=sequence,
            plan=elicitation,
            round_limit=elicitation_round_limit,
            execution_id=(
                str(self._managed_input_runtime.execution_id)
                if self._managed_input_runtime is not None
                else None
            ),
            session_id=identity[0] if identity is not None else None,
            turn_id=identity[2] if identity is not None else None,
        )
        self._action_channel.write_status(
            BridgeActionStatus(generation, sequence, "idle")
        )
        try:
            result = await super().send(message, timeout=timeout, metadata=metadata)
            status = self._action_channel.read_status()
            if result.outcome is not TurnOutcome.COMPLETED:
                return result
            if (
                status is None
                or status.generation != generation
                or status.turn_sequence != sequence
            ):
                return AdapterTurn(
                    response=None,
                    error=ErrorInfo(
                        code=ErrorCode.PROTOCOL_ERROR,
                        message="Pi bridge status was unavailable",
                    ),
                    terminal=True,
                    outcome=TurnOutcome.FAILED,
                    evidence=result.evidence,
                    turn_evidence=result.turn_evidence,
                )
            if status.state == "failed" or (
                elicitation is not None and status.state != "completed"
            ):
                return AdapterTurn(
                    response=None,
                    error=ErrorInfo(
                        code=ErrorCode.PROTOCOL_ERROR,
                        message="Pi bridge could not complete elicitation",
                        details={"bridge_error": status.error_code}
                        if status.error_code
                        else {},
                    ),
                    terminal=True,
                    outcome=TurnOutcome.FAILED,
                    tool_calls=result.tool_calls,
                    evidence=result.evidence,
                    trace_limitations=result.trace_limitations,
                    turn_evidence=result.turn_evidence,
                )
            return result
        finally:
            self._discard_turn_buffers()
            self._action_channel.clear()
            self._action_generation = None

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

    def _managed_identity(self) -> tuple[str, str, str] | None:
        value = getattr(self._managed_input_runtime, "bound_identity", None)
        return value if isinstance(value, tuple) and len(value) == 3 else None

    async def _deliver_control_pending(self, frame: Mapping[str, Any]) -> None:
        runtime = self._managed_input_runtime
        control = self._control_channel
        if runtime is None or control is None:
            raise HarnessStartupError("Pi managed elicitation runtime is unavailable")
        liveness_tasks: list[tuple[str, asyncio.Task[Any]]] = []
        round_failure_recorded = False

        async def fail_pending_round(error: BaseException) -> None:
            nonlocal round_failure_recorded
            if round_failure_recorded:
                return
            round_failure_recorded = True
            await runtime.fail_round(error)

        def peer_failure(source: str) -> HarnessStartupError:
            if source == "process":
                message = "Pi process exited during managed elicitation"
            else:
                message = "Pi managed-control connection disconnected"
            return HarnessStartupError(message)

        async def stop_liveness() -> None:
            for _, task in liveness_tasks:
                if not task.done():
                    task.cancel()
            if liveness_tasks:
                await asyncio.gather(
                    *(task for _, task in liveness_tasks), return_exceptions=True
                )
                liveness_tasks.clear()

        async def await_while_live(awaitable: Any) -> Any:
            operation_task = asyncio.create_task(awaitable)
            try:
                done, _ = await asyncio.wait(
                    (operation_task, *(task for _, task in liveness_tasks)),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Peer death wins a simultaneous completion: a response that
                # raced process exit cannot be delivered to the bridge.
                for source, task in liveness_tasks:
                    if task in done:
                        cause_error: BaseException | None
                        try:
                            task.result()
                        except BaseException as cause:
                            cause_error = cause
                        else:
                            cause_error = None
                        failure = peer_failure(source)
                        # Failing the durable record first wakes the real
                        # coordinator. Cancelling afterward also handles
                        # lightweight runtime implementations without a waiter.
                        await fail_pending_round(failure)
                        if not operation_task.done():
                            operation_task.cancel()
                        await asyncio.gather(operation_task, return_exceptions=True)
                        if cause_error is not None:
                            raise failure from cause_error
                        raise failure
                return operation_task.result()
            finally:
                if not operation_task.done():
                    operation_task.cancel()
                await asyncio.gather(operation_task, return_exceptions=True)

        try:
            if frame.get("generation") != self._action_generation:
                raise HarnessStartupError(
                    "Pi managed elicitation generation mismatched"
                )
            if (
                self._session is None
                or frame.get("turn_sequence") != self._session.turn_count
            ):
                raise HarnessStartupError("Pi managed elicitation turn mismatched")
            pending = PendingElicitationRound.model_validate(
                {
                    key: frame[key]
                    for key in (
                        "round_id",
                        "execution_id",
                        "logical_operation_id",
                        "server",
                        "operation_kind",
                        "operation_name",
                        "request_state",
                        "requests",
                        "created_at",
                        "deadline",
                    )
                }
            )
            expected_execution = str(getattr(runtime, "execution_id", ""))
            if pending.execution_id != expected_execution:
                raise HarnessStartupError(
                    "Pi managed elicitation execution identity mismatched"
                )
            process_owner = getattr(self._process, "owner", None)
            process = getattr(process_owner, "process", None)
            process_wait = getattr(process, "wait", None)
            if callable(process_wait):
                liveness_tasks.append(("process", asyncio.create_task(process_wait())))
            wait_disconnected = getattr(control, "wait_disconnected", None)
            if callable(wait_disconnected):
                liveness_tasks.append(
                    ("control", asyncio.create_task(wait_disconnected()))
                )
            responses = await await_while_live(
                runtime.await_round(
                    pending,
                    frame.get("operation_parameters")
                    if isinstance(frame.get("operation_parameters"), Mapping)
                    else None,
                )
            )
            wire_responses = {
                key: response.model_dump(mode="json")
                for key, response in responses.items()
            }
            await await_while_live(
                control.send_response(
                    generation=str(frame["generation"]),
                    turn_sequence=int(frame["turn_sequence"]),
                    execution_id=pending.execution_id,
                    logical_operation_id=pending.logical_operation_id,
                    round_id=pending.round_id,
                    responses=wire_responses,
                    response_idempotency_key=f"m3-{pending.round_id}",
                )
            )
            receive_task = control.receive()
            cancel_task = asyncio.create_task(self._managed_cancel_event.wait())
            try:
                terminal_task = asyncio.create_task(receive_task)
                done, _ = await asyncio.wait(
                    (
                        terminal_task,
                        cancel_task,
                        *(task for _, task in liveness_tasks),
                    ),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task in done:
                    raise OperationCancelled("managed elicitation cancelled")
                # Peer liveness is checked before accepting a simultaneous
                # control frame, just as it is while awaiting human input.
                for source, task in liveness_tasks:
                    if task in done:
                        cause_error: BaseException | None
                        try:
                            task.result()
                        except BaseException as cause:
                            cause_error = cause
                        else:
                            cause_error = None
                        failure = peer_failure(source)
                        await fail_pending_round(failure)
                        if cause_error is not None:
                            raise failure from cause_error
                        raise failure
                terminal = terminal_task.result()
            finally:
                for task in (terminal_task, cancel_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(terminal_task, cancel_task, return_exceptions=True)
            while True:
                if terminal.get("type") == "pending":
                    # A next round is the bridge's acceptance evidence for
                    # this response. Preserve it for the native turn loop.
                    await stop_liveness()
                    await runtime.resolve_round(
                        pending.round_id, operation_complete=False
                    )
                    self._buffered_control_frame = terminal
                    return
                if terminal.get("type") != "terminal":
                    raise HarnessStartupError(
                        "Pi bridge did not acknowledge managed response"
                    )
                if terminal.get("state") != "delivered":
                    failure = HarnessStartupError("Pi bridge rejected managed response")
                    await fail_pending_round(failure)
                    raise failure
                await stop_liveness()
                await runtime.resolve_round(pending.round_id, operation_complete=True)
                return
        except asyncio.CancelledError:
            self._discard_turn_buffers()
            raise
        except (OperationCancelled, OperationTimeout):
            self._discard_turn_buffers()
            try:
                await control.send_cancel(
                    generation=str(frame["generation"]),
                    turn_sequence=int(frame["turn_sequence"]),
                    execution_id=str(frame["execution_id"]),
                    logical_operation_id=str(frame["logical_operation_id"]),
                    round_id=str(frame["round_id"]),
                    reason="managed_input_cancelled",
                )
            except Exception:
                pass
            raise
        except Exception as error:
            self._discard_turn_buffers()
            await fail_pending_round(error)
            try:
                await control.send_cancel(
                    generation=str(frame["generation"]),
                    turn_sequence=int(frame["turn_sequence"]),
                    execution_id=str(frame["execution_id"]),
                    logical_operation_id=str(frame["logical_operation_id"]),
                    round_id=str(frame["round_id"]),
                    reason="managed_input_failed",
                )
            except Exception:
                pass
            raise
        finally:
            await stop_liveness()

    async def next_frame(
        self, process: JsonRpcProcess, timeout: float | None
    ) -> Mapping[str, Any] | None:
        control = self._control_channel
        if control is None or self._managed_input_runtime is None:
            return await super().next_frame(process, timeout)
        while True:
            if self._buffered_native_ready:
                frame, self._buffered_native_frame = self._buffered_native_frame, None
                self._buffered_native_ready = False
                return frame
            process_task = asyncio.create_task(process.next(timeout))
            control_task = asyncio.create_task(
                control.receive(timeout)
                if self._buffered_control_frame is None
                else asyncio.sleep(0, result=self._buffered_control_frame)
            )
            try:
                done, _ = await asyncio.wait(
                    (process_task, control_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if process_task in done and control_task in done:
                    self._buffered_native_frame = process_task.result()
                    self._buffered_native_ready = True
                    try:
                        frame = control_task.result()
                    except PiControlClosed:
                        frame = None
                    if frame is None:
                        frame, self._buffered_native_frame = (
                            self._buffered_native_frame,
                            None,
                        )
                        self._buffered_native_ready = False
                        return frame
                elif process_task in done:
                    control_task.cancel()
                    await asyncio.gather(control_task, return_exceptions=True)
                    return process_task.result()
                else:
                    frame = control_task.result()
                self._buffered_control_frame = None
                if frame.get("type") == "pending":
                    try:
                        await self._deliver_control_pending(frame)
                    except BaseException:
                        self._discard_turn_buffers()
                        raise
                    continue
                if frame.get("type") == "cancel":
                    raise OperationCancelled("managed elicitation cancelled")
                continue
            finally:
                for task in (process_task, control_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(process_task, control_task, return_exceptions=True)

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
        self._managed_cancel_event.set()
        if self._managed_input_runtime is not None:
            try:
                await self._managed_input_runtime.cancel(
                    OperationCancelled("managed elicitation cancelled")
                )
            except Exception:
                pass
        control = self._control_channel
        scope = control.scope if control is not None else None
        if control is not None and scope is not None:
            try:
                await control.send_cancel(
                    generation=scope.generation,
                    turn_sequence=scope.turn_sequence,
                    execution_id=scope.execution_id,
                    logical_operation_id=scope.logical_operation_id,
                    round_id=scope.round_id,
                    reason="turn_cancelled",
                )
            except Exception:
                pass
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
            self._managed_input_runtime = None
            if self._control_channel is not None:
                await self._control_channel.close()
                self._control_channel = None
            if self._action_channel is not None:
                self._action_channel.clear()
                self._action_channel = None
            self._action_generation = None
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
