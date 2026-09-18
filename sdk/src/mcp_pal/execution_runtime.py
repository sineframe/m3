"""In-memory asynchronous execution controller for the public SDK.

The controller owns execution lifecycle and event visibility.  Agent turns are
delegated to the kit's session factory, which resolves a harness adapter and
owns the server group for the lifetime of the execution.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from ._check_recording import (
    bind_execution as _bind_execution,
)
from ._check_recording import (
    bind_subject as _bind_subject,
)
from .direct_trace import DirectTraceBridge
from .errors import (
    MCPError,
    ModelValidationError,
    OperationCancelled,
    OperationTimeout,
    ProtocolError,
    TransportError,
    UnsupportedFeature,
)
from .events import EventFactory, EventSequence
from .execution_trace import ExecutionTraceRecorder
from .harness.contracts import HarnessStartupError
from .services.profiles import ProfileResolutionError, resolve_execution_spec
from .storage import (
    ArtifactStore,
    ExecutionStore,
    InMemoryExecutionStore,
    ProfileResolver,
)
from .trace.redaction import RedactionConfig
from .transport.local import LocalTransportError
from .types import (
    ActivityHealth,
    AgentSpec,
    CallTool,
    CallToolResult,
    DirectResult,
    DirectSpec,
    ErrorCode,
    ErrorInfo,
    Event,
    EventDirection,
    EventKind,
    EventOrigin,
    EventSource,
    EvidenceRef,
    ExecutionId,
    ExecutionOutcome,
    ExecutionResult,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
    GetPrompt,
    GetPromptResult,
    HTTPServer,
    LifecyclePhase,
    ListPrompts,
    ListPromptsResult,
    ListResources,
    ListResourcesResult,
    ListTemplates,
    ListTemplatesResult,
    ListTools,
    ListToolsResult,
    Ping,
    PingResult,
    ReadResource,
    ReadResourceResult,
    RequestLink,
    ServerBinding,
    SSEServer,
    TraceResult,
)


class _PersistentExecutionStore(ExecutionStore, ProfileResolver, Protocol):
    def enqueue_command(self, *args: Any, **kwargs: Any) -> Any: ...

    def request_cancel(self, *args: Any, **kwargs: Any) -> Any: ...


from .workspace import WorkspaceError, WorkspaceManager

_DIRECT_RESULT_ADAPTER: TypeAdapter[DirectResult] = TypeAdapter(DirectResult)
_CLEANUP_TIMEOUT_SECONDS = 10.0


def _direct_result_payload(result: DirectResult | None) -> Mapping[str, Any] | None:
    if result is None:
        return None
    payload = result.model_dump(mode="json")
    payload.pop("raw", None)
    return payload


def _direct_result_from_trace(trace: TraceResult | None) -> DirectResult | None:
    return _direct_result_from_events(trace.events if trace is not None else ())


def _direct_result_from_events(events: Sequence[Event]) -> DirectResult | None:
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.kind is EventKind.EXECUTION_FINISHED
        ),
        None,
    )
    payload = terminal.payload.get("direct_result") if terminal is not None else None
    if not isinstance(payload, Mapping):
        return None
    try:
        return _DIRECT_RESULT_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


class DirectExecutionKit(Protocol):
    def direct(self, server: Any, **options: Any) -> Any: ...


class AgentRunner(Protocol):
    async def run(
        self,
        spec: AgentSpec,
        *,
        on_event: Callable[[Event], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> Any: ...


def _error_info(error: BaseException) -> ErrorInfo:
    if isinstance(error, OperationTimeout):
        return ErrorInfo(
            code=ErrorCode.TIMEOUT, message="execution timed out", retryable=True
        )
    if isinstance(error, (OperationCancelled, asyncio.CancelledError)):
        return ErrorInfo(
            code=ErrorCode.CANCELLED, message="execution cancelled", retryable=False
        )
    if isinstance(error, UnsupportedFeature):
        return ErrorInfo(
            code=ErrorCode.UNSUPPORTED,
            message="execution feature is unsupported",
            retryable=False,
        )
    if isinstance(error, HarnessStartupError):
        return ErrorInfo(
            code=ErrorCode.UNSUPPORTED,
            message="requested harness is unavailable",
            retryable=False,
        )
    if isinstance(error, ModelValidationError):
        return ErrorInfo(
            code=ErrorCode.INVALID_ARGUMENT,
            message="execution validation failed",
            retryable=False,
        )
    if isinstance(error, ProtocolError):
        return ErrorInfo(
            code=ErrorCode.PROTOCOL_ERROR,
            message="MCP protocol operation failed",
            retryable=False,
        )
    if isinstance(error, TransportError):
        return ErrorInfo(
            code=ErrorCode.TRANSPORT_ERROR,
            message="MCP transport operation failed",
            retryable=True,
        )
    if isinstance(error, LocalTransportError):
        return ErrorInfo(
            code=ErrorCode.TRANSPORT_ERROR,
            message="MCP transport operation failed",
            retryable=True,
        )
    if isinstance(error, MCPError):
        return ErrorInfo(
            code=ErrorCode.INVALID_ARGUMENT, message="execution failed", retryable=False
        )
    return ErrorInfo(
        code=ErrorCode.INVALID_ARGUMENT, message="execution failed", retryable=False
    )


def _outcome(error: BaseException | None) -> ExecutionOutcome:
    if error is None:
        return ExecutionOutcome.COMPLETED
    if isinstance(error, (OperationTimeout, asyncio.TimeoutError, TimeoutError)):
        return ExecutionOutcome.TIMED_OUT
    if isinstance(error, (OperationCancelled, asyncio.CancelledError)):
        return ExecutionOutcome.CANCELLED
    return ExecutionOutcome.FAILED


def _activity_health(trace: TraceResult | None) -> ActivityHealth:
    """Classify tool activity from committed stable wire events only."""

    if trace is None:
        return ActivityHealth.NO_CALLS
    pending: dict[str, bool] = {}
    outcomes: list[bool] = []
    has_wire_evidence = any(
        event.provenance.origin is EventOrigin.WIRE_OBSERVED for event in trace.events
    )
    for event in trace.events:
        correlation = event.correlation
        if correlation is not None and correlation.request_sequence is not None:
            connection = (
                event.connection_id.root
                if event.connection_id is not None
                else "<unknown>"
            )
            key = f"wire:{connection}:{correlation.request_sequence}"
        elif not has_wire_evidence and event.kind in {
            EventKind.TOOL_CALL_REQUESTED,
            EventKind.TOOL_RESULT_RECEIVED,
        }:
            if event.session_id is None or event.turn_id is None:
                continue
            index = event.payload.get("tool_index", 0)
            key = f"agent:{event.session_id.root}:{event.turn_id.root}:{index}"
        else:
            continue
        if event.kind is EventKind.TOOL_CALL_REQUESTED:
            pending[key] = False
        elif event.kind is EventKind.TOOL_RESULT_RECEIVED and key in pending:
            result = event.payload.get("result", event.payload.get("tool_call"))
            is_error = isinstance(result, Mapping) and bool(
                result.get("isError", result.get("is_error", False))
            )
            pending.pop(key, None)
            outcomes.append(not is_error)
        elif event.kind is EventKind.MCP_ERROR and key in pending:
            pending.pop(key, None)
            outcomes.append(False)
    # An unanswered tool request is evidence of a failed/incomplete call, not
    # a successful call. This keeps lifecycle outcome and activity health
    # independent while avoiding a false ALL_SUCCEEDED classification.
    outcomes.extend(pending.values())
    if not outcomes:
        return ActivityHealth.NO_CALLS
    if all(outcomes):
        return ActivityHealth.ALL_SUCCEEDED
    if not any(outcomes):
        return ActivityHealth.ALL_FAILED
    return ActivityHealth.MIXED


class AsyncExecutionHandle:
    """Live, immutable view of one submitted execution."""

    def __init__(
        self,
        controller: AsyncExecutionController,
        spec: ExecutionSpec,
        *,
        store: ExecutionStore | None = None,
        persistent: bool = False,
        execution_id: ExecutionId | str | None = None,
        run_id: str | None = None,
        resolution_provenance: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._controller = controller
        self._spec = spec
        self._execution_id = (
            execution_id
            if isinstance(execution_id, ExecutionId)
            else ExecutionId(str(execution_id))
            if execution_id is not None
            else ExecutionId(f"execution-{uuid4().hex}")
        )
        self._store: ExecutionStore = store or InMemoryExecutionStore()
        # Persistent stores expose their artifact store alongside execution
        # metadata.  Keep this handle on the submitted execution so every
        # workspace owner publishes bytes into the same durable namespace.
        # In-memory stores intentionally retain the existing ephemeral
        # fallback because they do not own a durable artifact backend.
        candidate_artifacts = getattr(self._store, "artifacts", None)
        if candidate_artifacts is not None:
            required_methods = (
                "put",
                "get",
                "get_ref",
                "iter_refs",
                "delete",
                "cleanup",
            )
            if not all(
                callable(getattr(candidate_artifacts, name, None))
                for name in required_methods
            ):
                raise ModelValidationError(
                    "execution store exposes an incomplete artifact store",
                    details={"operation": "execution.artifacts"},
                )
        if persistent and candidate_artifacts is None:
            raise ModelValidationError(
                "persistent execution store must expose artifacts",
                details={"operation": "execution.artifacts"},
            )
        self._artifact_store: ArtifactStore | None = candidate_artifacts
        self._persistent = persistent
        server_bindings = []
        provenance_values = tuple(resolution_provenance)
        by_ordinal = {
            int(item["_ordinal"]): item
            for item in provenance_values
            if item.get("kind") == "server_profile" and "_ordinal" in item
        }
        for index, binding in enumerate(spec.servers):
            value = binding.model_dump(mode="json")
            marker = by_ordinal.get(index)
            if marker is not None:
                value.update(
                    profile_id=marker.get("profile_id"),
                    revision_id=marker.get("revision_id"),
                )
            server_bindings.append(value)
        harness_binding = next(
            (
                {
                    "profile_id": item.get("profile_id"),
                    "revision_id": item.get("revision_id"),
                    "kind": item.get("kind"),
                }
                for item in provenance_values
                if item.get("kind") == "harness_profile"
            ),
            None,
        )
        self._recorder = ExecutionTraceRecorder(
            self._store,
            self._execution_id,
            redaction_config=controller.redaction_config,
            # Every execution store implements the typed specification slot;
            # retain the immutable submission even for ordinary in-memory
            # executions so history/clone clients see one consistent shape.
            specification=spec.model_dump(mode="json"),
            # An explicit spec run ID has precedence over the controller
            # default while preserving the caller's immutable spec object.
            run_id=(spec.run_id.root if spec.run_id is not None else run_id),
            server_bindings=tuple(server_bindings),
            harness_binding=harness_binding,
            provenance=(
                {
                    "profiles": tuple(
                        {key: value for key, value in item.items() if key != "_ordinal"}
                        for item in provenance_values
                    )
                }
                if provenance_values
                else None
            ),
        )
        if getattr(controller.kit, "_record_checks", False):
            _bind_execution(
                self._execution_id,
                self._store,
                controller.redaction_config,
            )
        self._terminal = asyncio.Event()
        self._cancel_requested = False
        # Cancellation may be requested concurrently by the public handle
        # and the durable-store watcher.  Keep the transition atomic so the
        # owner task receives one cancellation request and its finally path
        # remains the sole cleanup authority.
        self._task_cancel_issued = False
        self._bridge: DirectTraceBridge | None = None
        self._workspace: WorkspaceManager | None = None
        self._workspace_artifacts: tuple[Any, ...] = ()
        self._workspace_limitations: tuple[str, ...] = ()
        self._direct_result: DirectResult | None = None
        self._agent_outcome: ExecutionOutcome | None = None
        self._agent_error: ErrorInfo | None = None
        self._result: ExecutionResult | None = None
        self._state_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._callback_tasks: set[asyncio.Task[Any]] = set()
        # A persistent handle may be submitted by one process while another
        # process owns the worker task.  The submitting process can only set
        # the durable cancellation flag; this watcher runs beside the owner
        # task and turns that flag into the same task cancellation used by
        # local callers.  Keeping this at the execution boundary makes it
        # apply equally to direct MCP waits and agent/native adapter turns.
        self._cancel_watcher: asyncio.Task[None] | None = None
        self._deadline_task: asyncio.Task[None] | None = None
        self._timeout_expired = False
        if not persistent:
            self._task = asyncio.create_task(self._run())

    @property
    def execution_id(self) -> ExecutionId:
        return self._execution_id

    @property
    def spec(self) -> ExecutionSpec:
        return self._spec.model_copy()

    @property
    def submitted_spec(self) -> ExecutionSpec:
        return self.spec

    async def snapshot(self) -> ExecutionState:
        snapshot = self._store.get_snapshot(self._execution_id)
        if snapshot is None:
            raise RuntimeError("execution snapshot is unavailable")
        return snapshot

    async def _wait_terminal(self, timeout: float | None) -> None:
        if timeout is not None and timeout <= 0:
            raise ModelValidationError(
                "wait timeout must be positive",
                details={"operation": "execution.result"},
            )

        async def wait_for_terminal() -> None:
            while not self._terminal.is_set():
                if self._persistent:
                    await self._hydrate_terminal()
                if not self._terminal.is_set():
                    await asyncio.sleep(0.05)

        try:
            if timeout is None:
                await wait_for_terminal()
            else:
                await asyncio.wait_for(wait_for_terminal(), timeout)
        except asyncio.TimeoutError as exc:
            raise OperationTimeout(
                "execution result wait timed out",
                details={"operation": "execution.result", "wait_only": True},
            ) from exc

    async def result(self, timeout: float | None = None) -> ExecutionResult:
        await self._wait_terminal(timeout)
        result = self._result
        if result is None:
            raise RuntimeError("execution completed without a terminal result")
        return result.model_copy()

    async def cancel(self) -> None:
        async with self._state_lock:
            if self._terminal.is_set():
                return
            self._cancel_requested = True
        if self._persistent:
            request_cancel = getattr(self._store, "request_cancel", None)
            if callable(request_cancel):
                request_cancel(
                    self._execution_id, reason="caller requested cancellation"
                )
            if self._task is None:
                await self._hydrate_terminal()
                return
        # Give the coroutine a chance to enter its guarded body before
        # cancelling it. This makes queued cancellation terminal and durable.
        await asyncio.sleep(0)
        task = self._task
        if task is not None and not task.done():
            async with self._state_lock:
                # The transition is recorded immediately beside the actual
                # task cancellation. If the caller is interrupted above, or
                # durable persistence fails, a later cancel() can retry.
                issue_task_cancel = not self._task_cancel_issued
                if issue_task_cancel:
                    self._task_cancel_issued = True
            if issue_task_cancel:
                task.cancel()
        await self._terminal.wait()

    async def _hydrate_terminal(self) -> None:
        """Project a terminal state committed by a persistent worker/store."""
        snapshot = self._store.get_snapshot(self._execution_id)
        if snapshot is None or snapshot.lifecycle is not ExecutionStatus.FINISHED:
            return
        events = tuple(self._store.iter_events(self._execution_id))
        outcome = snapshot.outcome or ExecutionOutcome.FAILED
        try:
            trace = self._recorder.finalize(outcome)
        except Exception:
            trace = None
        self._direct_result = (
            _direct_result_from_trace(trace)
            if trace is not None
            else _direct_result_from_events(events)
        )
        if self._artifact_store is not None:
            # Artifact refs are part of the durable terminal result.  Do not
            # replace a storage failure with an empty tuple: that would make
            # a successful execution appear to have no artifacts and hide
            # corruption/unavailability from the caller.
            self._workspace_artifacts = tuple(
                self._artifact_store.iter_refs(self._execution_id)
            )
        self._result = ExecutionResult(
            snapshot=snapshot,
            trace=trace,
            artifacts=self._workspace_artifacts,
            direct_result=self._direct_result,
            activity_health=_activity_health(trace),
            error=_error_info(OperationCancelled("execution cancelled"))
            if outcome is ExecutionOutcome.CANCELLED
            else None,
        )
        self._terminal.set()
        self._controller._finished(self)

    async def _start_from_worker(self) -> None:
        """Start exactly once after the durable worker claims this command."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        if self._persistent and self._cancel_watcher is None:
            self._cancel_watcher = asyncio.create_task(
                self._watch_durable_cancellation()
            )
        await self._task

    async def _watch_durable_cancellation(self) -> None:
        """Interrupt an owned task when another process requests cancel.

        SQLite is deliberately the only cross-process signal. Polling is
        short enough to make cancellation responsive while avoiding a second
        persistence or IPC mechanism, and the synchronous store read runs in
        a worker thread so SQLite I/O or lock waits cannot stall the owner
        event loop. A request is acted on once; the execution's normal
        ``finally`` path then owns adapter/process cleanup and durable
        terminalization.
        """

        task = self._task
        if task is None:
            return
        cancellation_requested = getattr(self._store, "cancellation_requested", None)
        while not task.done():
            try:
                requested = (
                    bool(
                        await asyncio.to_thread(
                            cancellation_requested,
                            self._execution_id,
                        )
                    )
                    if callable(cancellation_requested)
                    else False
                )
            except Exception:
                requested = False
            if requested:
                async with self._state_lock:
                    if self._terminal.is_set():
                        return
                    self._cancel_requested = True
                    issue_task_cancel = not self._task_cancel_issued
                    self._task_cancel_issued = True
                if issue_task_cancel and not task.done():
                    task.cancel()
                return
            await asyncio.sleep(0.02)

    async def _watch_execution_deadline(self, timeout_seconds: float) -> None:
        """Cancel the owner task after the full execution deadline."""

        started = asyncio.get_running_loop().time()
        await asyncio.sleep(timeout_seconds)
        elapsed_seconds = asyncio.get_running_loop().time() - started
        async with self._state_lock:
            if self._terminal.is_set() or self._task is None or self._task.done():
                return
            self._timeout_expired = True
            events = tuple(self._store.iter_events(self._execution_id))
            phase = next(
                (
                    event.lifecycle_phase
                    for event in reversed(events)
                    if event.lifecycle_phase is not LifecyclePhase.UNKNOWN
                ),
                LifecyclePhase.UNKNOWN,
            )
            active_stage: Mapping[str, Any] | None = None
            open_stages: list[Mapping[str, Any]] = []
            for event in events:
                if event.kind is not EventKind.DIAGNOSTIC:
                    continue
                payload = event.payload
                code = payload.get("code")
                key = (payload.get("stage"), payload.get("operation"))
                if code == "stage_started":
                    open_stages.append(payload)
                elif code == "stage_completed":
                    for index in range(len(open_stages) - 1, -1, -1):
                        candidate = open_stages[index]
                        if (candidate.get("stage"), candidate.get("operation")) == key:
                            del open_stages[index]
                            break
            if open_stages:
                active_stage = open_stages[-1]
            harness_kind = (
                getattr(self._spec.harness, "kind", "harness")
                if isinstance(self._spec, AgentSpec)
                else "direct"
            )
            if isinstance(active_stage, Mapping):
                stage = str(active_stage.get("stage", "execution"))
                operation = str(active_stage.get("operation", "execution"))
            elif phase in {LifecyclePhase.TURN, LifecyclePhase.MCP_CALL}:
                operation = (
                    "opencode.session_message"
                    if harness_kind == "opencode"
                    else "harness.response"
                )
                stage = "waiting_for_harness_response"
            elif phase in {LifecyclePhase.STARTUP, LifecyclePhase.INITIALIZATION}:
                operation = "harness.startup"
                stage = "harness_startup"
            elif phase is LifecyclePhase.CLEANUP:
                operation = "execution.cleanup"
                stage = "cleanup"
            else:
                operation = "execution.startup"
                stage = "execution_startup"
            self._recorder.emit(
                EventKind.DIAGNOSTIC,
                payload={
                    "code": "operation_timeout",
                    "stage": stage,
                    "operation": operation,
                    "elapsed_seconds": round(elapsed_seconds, 6),
                    "timeout_seconds": timeout_seconds,
                    "message": f"Timed out during {stage}",
                },
                lifecycle_phase=phase,
            )
            self._task.cancel()

    def on_event(self, callback: Callable[[Event], Any]) -> Callable[[], None]:
        """Subscribe after commit; callback failures cannot affect execution."""

        loop = asyncio.get_running_loop()

        def observe(event: Event) -> None:
            try:
                value = callback(event.model_copy())
                if hasattr(value, "__await__"):
                    task = loop.create_task(value)
                    self._callback_tasks.add(task)

                    def finish_callback(completed: asyncio.Task[Any]) -> None:
                        self._callback_tasks.discard(completed)
                        if not completed.cancelled():
                            completed.exception()

                    task.add_done_callback(finish_callback)
            except Exception:
                return

        return self._store.subscribe(self._execution_id, observe)

    async def events(self, *, after_sequence: int = -1) -> AsyncIterator[Event]:
        """Yield committed events in sequence order and finish at terminal."""

        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        queue: asyncio.Queue[Event] = asyncio.Queue()

        def enqueue(event: Event) -> None:
            queue.put_nowait(event)

        unsubscribe = self._store.subscribe(self._execution_id, enqueue)
        cursor = after_sequence
        try:
            for event in self._store.iter_events(
                self._execution_id, after_sequence=cursor
            ):
                cursor = event.sequence
                yield event.model_copy()
                if event.kind is EventKind.EXECUTION_FINISHED:
                    return
            while True:
                event = await queue.get()
                if event.sequence <= cursor:
                    continue
                cursor = event.sequence
                yield event.model_copy()
                if event.kind is EventKind.EXECUTION_FINISHED:
                    return
        finally:
            unsubscribe()

    async def _run(self) -> None:
        failure: BaseException | None = None
        trace: TraceResult | None = None
        try:
            await asyncio.sleep(0)
            timeout_seconds = self._spec.timeout_seconds
            if timeout_seconds is not None:
                self._deadline_task = asyncio.create_task(
                    self._watch_execution_deadline(timeout_seconds)
                )
            if self._cancel_requested:
                raise OperationCancelled("execution cancelled")
            if isinstance(self._spec, AgentSpec) and self._spec.message is None:
                raise ModelValidationError(
                    "submitted agent execution requires a message",
                    details={"operation": "execution.run"},
                )
            # AgentSession is the workspace owner for agent executions. Direct
            # executions are owned here because the direct client has no
            # session lifecycle to delegate to.
            if isinstance(self._spec, DirectSpec):
                self._workspace = WorkspaceManager(
                    self._spec.workspace,
                    self._execution_id,
                    artifact_policy=self._spec.artifact_policy,
                    declared_artifacts=self._spec.declared_artifacts,
                    artifact_store=self._artifact_store,
                )
                await asyncio.to_thread(self._workspace.create)
            self._recorder.emit(
                EventKind.EXECUTION_STATE_CHANGED,
                payload={"lifecycle": ExecutionStatus.STARTING.value},
            )
            if isinstance(self._spec, DirectSpec):
                self._direct_result = await self._run_direct(self._spec)
            else:
                await self._run_agent(self._spec)
        except asyncio.CancelledError:
            if self._timeout_expired:
                failure = OperationTimeout("execution deadline expired")
                self._agent_outcome = ExecutionOutcome.TIMED_OUT
                self._agent_error = _error_info(failure)
            else:
                failure = OperationCancelled("execution cancelled")
        except BaseException as exc:
            failure = exc
        finally:
            outcome = (
                ExecutionOutcome.CANCELLED
                if isinstance(failure, (OperationCancelled, asyncio.CancelledError))
                else (
                    ExecutionOutcome.TIMED_OUT
                    if self._timeout_expired
                    else self._agent_outcome or _outcome(failure)
                )
            )
            workspace_cleanup_failed = False
            if self._workspace is not None:
                try:
                    capture = await asyncio.wait_for(
                        asyncio.to_thread(self._workspace.capture, outcome),
                        timeout=_CLEANUP_TIMEOUT_SECONDS,
                    )
                    self._workspace_artifacts = capture.artifacts
                    self._recorder.emit(
                        EventKind.WORKSPACE_CHANGED,
                        payload={
                            "diff": capture.diff.as_payload(),
                            "artifacts": tuple(
                                ref.model_dump(mode="json") for ref in capture.artifacts
                            ),
                            "limitations": capture.limitations
                            + self._workspace_limitations,
                        },
                        lifecycle_phase=LifecyclePhase.CLEANUP,
                        provenance=EventSource(
                            origin=EventOrigin.DERIVED, source="mcp_pal.workspace"
                        ),
                    )
                except BaseException:
                    # Evidence is best effort; do not expose filesystem errors.
                    workspace_cleanup_failed = True
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self._workspace.cleanup),
                        timeout=_CLEANUP_TIMEOUT_SECONDS,
                    )
                except (WorkspaceError, OSError, asyncio.TimeoutError):
                    workspace_cleanup_failed = True
            trace_limitations = (
                ("capture_incomplete",)
                if outcome in {ExecutionOutcome.CANCELLED, ExecutionOutcome.TIMED_OUT}
                else ()
            )
            try:
                trace = self._finalize(
                    outcome,
                    cleanup_succeeded=not workspace_cleanup_failed,
                    limitations=trace_limitations,
                )
            except BaseException:
                # Preserve a terminal result if secondary finalization fails.
                trace = None
            if trace is None:
                try:
                    trace = self._recorder.finalize(
                        outcome,
                        cleanup_succeeded=False,
                        limitations=trace_limitations,
                    )
                except BaseException:
                    trace = None
            snapshot = self._store.get_snapshot(self._execution_id)
            if snapshot is not None and snapshot.lifecycle is ExecutionStatus.FINISHED:
                result_error = (
                    _error_info(failure)
                    if isinstance(failure, (OperationCancelled, asyncio.CancelledError))
                    else self._agent_error
                    or (_error_info(failure) if failure is not None else None)
                )
                if workspace_cleanup_failed and result_error is None:
                    result_error = ErrorInfo(
                        code=ErrorCode.CLEANUP_FAILED,
                        message="execution cleanup failed",
                        retryable=False,
                    )
                self._result = ExecutionResult(
                    snapshot=snapshot,
                    trace=trace,
                    artifacts=self._workspace_artifacts,
                    direct_result=self._direct_result,
                    activity_health=_activity_health(trace),
                    error=result_error,
                )
                if getattr(self._controller.kit, "_record_checks", False):
                    _bind_subject(
                        self._result,
                        self._execution_id,
                        self._store,
                        self._controller.redaction_config,
                    )
                    if trace is not None:
                        _bind_subject(
                            trace,
                            self._execution_id,
                            self._store,
                            self._controller.redaction_config,
                        )
                    for turn in self._result.turns:
                        _bind_subject(
                            turn,
                            self._execution_id,
                            self._store,
                            self._controller.redaction_config,
                        )
            self._terminal.set()
            self._controller._finished(self)
            watcher = self._cancel_watcher
            self._cancel_watcher = None
            if (
                watcher is not None
                and watcher is not asyncio.current_task()
                and not watcher.done()
            ):
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            deadline = self._deadline_task
            self._deadline_task = None
            if (
                deadline is not None
                and deadline is not asyncio.current_task()
                and not deadline.done()
            ):
                deadline.cancel()
                await asyncio.gather(deadline, return_exceptions=True)

    @staticmethod
    def _direct_binding_selector(binding: ServerBinding) -> str | None:
        if binding.alias is not None:
            return binding.alias
        if binding.server is not None:
            return binding.server.name
        if binding.profile is not None:
            return binding.profile.profile_id.root
        return None

    @classmethod
    def _select_direct_binding(cls, spec: DirectSpec) -> tuple[ServerBinding, str]:
        selector = spec.operation.server
        binding: ServerBinding | None = None
        if selector is None:
            # The model guarantees this is the only binding for a selector-less
            # direct operation. Keep the fallback explicit for runtime callers
            # that may construct a model through a custom validator path.
            if len(spec.servers) != 1:
                raise ModelValidationError(
                    "direct operation server selector is required",
                    details={"operation": "execution.direct"},
                )
            binding = spec.servers[0]
        else:
            candidate = next(
                (
                    item
                    for item in spec.servers
                    if cls._direct_binding_selector(item) == selector
                ),
                None,
            )
            if candidate is None:
                raise ModelValidationError(
                    "direct operation server selector does not match a configured server",
                    details={"operation": "execution.direct"},
                )
            binding = candidate
        if binding is None:
            raise ModelValidationError(
                "direct execution server binding is unavailable",
                details={"operation": "execution.direct"},
            )
        if binding.server is None:
            raise UnsupportedFeature(
                "execution server profile resolution is not available"
            )
        effective_selector = cls._direct_binding_selector(binding)
        if effective_selector is None:
            raise ModelValidationError(
                "direct execution server binding has no selector",
                details={"operation": "execution.direct"},
            )
        return binding, effective_selector

    @staticmethod
    async def _direct_pages(
        client: Any,
        method_name: str,
        *,
        cursor: str | None,
        all_pages: bool,
    ) -> tuple[tuple[Any, ...], Any]:
        """Collect direct list pages while retaining official responses."""

        pages: list[Any] = []
        seen: set[str] = {cursor} if cursor is not None else set()
        next_cursor = cursor
        while True:
            page = await getattr(client, method_name)(cursor=next_cursor)
            pages.append(page)
            if not all_pages or page.next_cursor is None:
                break
            if page.next_cursor in seen:
                raise ProtocolError(
                    "MCP pagination cursor repeated",
                    details={"phase": "pagination", "retryable": False},
                )
            seen.add(page.next_cursor)
            next_cursor = page.next_cursor
        raw_values = tuple(page.raw for page in pages)
        raw = raw_values[0] if len(raw_values) == 1 else raw_values
        return tuple(pages), raw

    async def _run_direct(self, spec: DirectSpec) -> DirectResult:
        binding, effective_selector = self._select_direct_binding(spec)
        if isinstance(binding.server, (HTTPServer, SSEServer)):
            self._workspace_limitations = ("remote_transport_workspace_not_applicable",)
        events = self._recorder.events()
        next_sequence = events[-1].sequence + 1 if events else 1
        factory = EventFactory(
            self._execution_id,
            allocator=EventSequence(start=next_sequence),
            source="mcp_pal.execution.direct",
        )
        self._bridge = DirectTraceBridge(
            execution_id=self._execution_id,
            event_factory=factory,
            recorder=self._recorder,
            server_binding=effective_selector,
            redaction_config=self._controller.redaction_config,
        )
        options: dict[str, Any] = {
            "protocol": spec.protocol,
            "timeout": spec.timeout_seconds,
            "validate_schemas": spec.validate_schemas,
            "trace_bridge": self._bridge,
            "trace_owner": False,
            "workspace_root": str(self._workspace.root)
            if self._workspace is not None
            else None,
        }
        async with self._controller.kit.direct(binding.server, **options) as client:
            if self._cancel_requested:
                raise OperationCancelled("execution cancelled")
            operation = spec.operation
            if isinstance(operation, ListTools):
                pages, raw = await self._direct_pages(
                    client,
                    "list_tools",
                    cursor=operation.cursor,
                    all_pages=operation.all_pages,
                )
                return ListToolsResult(
                    raw=raw,
                    tools=tuple(tool for page in pages for tool in page.tools),
                    next_cursor=pages[-1].next_cursor,
                )
            if isinstance(operation, ListResources):
                pages, raw = await self._direct_pages(
                    client,
                    "list_resources",
                    cursor=operation.cursor,
                    all_pages=operation.all_pages,
                )
                return ListResourcesResult(
                    raw=raw,
                    resources=tuple(
                        resource for page in pages for resource in page.resources
                    ),
                    next_cursor=pages[-1].next_cursor,
                )
            if isinstance(operation, ListTemplates):
                pages, raw = await self._direct_pages(
                    client,
                    "list_resource_templates",
                    cursor=operation.cursor,
                    all_pages=operation.all_pages,
                )
                return ListTemplatesResult(
                    raw=raw,
                    resource_templates=tuple(
                        template
                        for page in pages
                        for template in page.resource_templates
                    ),
                    next_cursor=pages[-1].next_cursor,
                )
            if isinstance(operation, ListPrompts):
                pages, raw = await self._direct_pages(
                    client,
                    "list_prompts",
                    cursor=operation.cursor,
                    all_pages=operation.all_pages,
                )
                return ListPromptsResult(
                    raw=raw,
                    prompts=tuple(prompt for page in pages for prompt in page.prompts),
                    next_cursor=pages[-1].next_cursor,
                )
            if isinstance(operation, CallTool):
                result = await client.call_tool(operation.name, operation.arguments)
                return CallToolResult(
                    raw=getattr(result, "raw", None),
                    content=getattr(result, "content", ()),
                    structured_content=getattr(result, "structured_content", None),
                    is_error=bool(getattr(result, "is_error", False)),
                )
            if isinstance(operation, ReadResource):
                result = await client.read_resource(operation.uri)
                return ReadResourceResult(
                    raw=getattr(result, "raw", None),
                    contents=getattr(result, "contents", ()),
                )
            if isinstance(operation, GetPrompt):
                result = await client.get_prompt(operation.name, operation.arguments)
                return GetPromptResult(
                    raw=getattr(result, "raw", None),
                    description=getattr(result, "description", None),
                    messages=getattr(result, "messages", ()),
                )
            if isinstance(operation, Ping):
                result = await client.ping()
                return PingResult(
                    raw=getattr(result, "raw", None),
                    result_type=getattr(result, "result_type", None),
                )
            raise UnsupportedFeature("direct operation is not implemented")

    async def _run_agent(self, spec: AgentSpec) -> None:
        """Run one session-backed agent execution and preserve its turns."""

        # The kit factory is the single ownership boundary: it resolves the
        # adapter, starts the required/optional server group, and closes both
        # together after the conversation.  The execution recorder remains
        # the event/trace authority for the submitted handle.
        session_options: dict[str, Any] = {
            "_event_sink": self._record_agent_event,
            "_trace_recorder": self._recorder,
            "_trace_owner": False,
            "_execution_id": self._execution_id,
        }
        # Keep injected test-kit factories that implement the earlier private
        # hook compatible for ephemeral executions. Persistent submissions
        # always carry the durable artifact store through this boundary.
        if self._artifact_store is not None:
            session_options["_artifact_store"] = self._artifact_store
        self._recorder.emit(
            EventKind.DIAGNOSTIC,
            payload={
                "code": "stage_started",
                "stage": "harness_startup",
                "operation": "harness.startup",
                "message": "Starting the selected harness",
            },
            lifecycle_phase=LifecyclePhase.STARTUP,
        )
        session = self._controller.kit.agent_session(spec, **session_options)
        failure: BaseException | None = None
        try:
            async with session:
                self._recorder.emit(
                    EventKind.DIAGNOSTIC,
                    payload={
                        "code": "stage_completed",
                        "stage": "harness_startup",
                        "operation": "harness.startup",
                        "message": "Harness session started",
                    },
                    lifecycle_phase=LifecyclePhase.STARTUP,
                )
                if self._cancel_requested:
                    raise OperationCancelled("execution cancelled")
                if spec.message is not None:
                    await session.send(spec.message, timeout=spec.timeout_seconds)
        except BaseException as exc:
            # Preserve the exception exposed by the session factory/lifecycle
            # (notably typed unsupported startup) if the provisional session
            # result carries a more generic internal startup error.
            failure = exc
            self._agent_error = _error_info(exc)
            raise
        finally:
            # Session cleanup may raise after it has already committed its
            # terminal result (for example, an owned workspace cleanup
            # failure). Preserve any artifacts collected on that path.
            try:
                session_result = session.result
                self._workspace_artifacts = tuple(session_result.artifacts)
                self._agent_outcome = session_result.snapshot.outcome
                if session_result.error is not None and not isinstance(
                    failure, UnsupportedFeature
                ):
                    self._agent_error = session_result.error
            except BaseException:
                pass
        if self._agent_outcome is ExecutionOutcome.COMPLETED:
            self._recorder.emit(
                EventKind.EXECUTION_STATE_CHANGED,
                payload={"lifecycle": ExecutionStatus.IDLE.value},
            )
        return None

    def _record_agent_event(
        self,
        kind: EventKind,
        payload: Mapping[str, Any],
        session_id: Any,
        turn_id: Any,
        phase: LifecyclePhase,
    ) -> None:
        event_payload = dict(payload)
        is_wire = event_payload.get("evidence_mode") == "wire_observed"
        connection_value = event_payload.pop("_mcp_connection_id", None)
        direction_value = event_payload.pop("_mcp_direction", None)
        server_binding = event_payload.pop("_mcp_server_binding", None)
        raw_ref = event_payload.pop("_mcp_raw_evidence_ref", None)
        request_sequence = event_payload.get("request_sequence")
        jsonrpc_id = event_payload.get("jsonrpc_id")
        correlation = None
        if is_wire and isinstance(direction_value, str):
            try:
                direction = EventDirection(direction_value)
                correlation = RequestLink(
                    jsonrpc_id=jsonrpc_id
                    if isinstance(jsonrpc_id, (int, str))
                    and not isinstance(jsonrpc_id, bool)
                    else None,
                    direction=direction,
                    request_sequence=request_sequence
                    if isinstance(request_sequence, int)
                    else None,
                )
            except ValueError:
                correlation = None
        raw_evidence = None
        if is_wire and isinstance(raw_ref, str) and raw_ref:
            raw_evidence = EvidenceRef(
                evidence_id=raw_ref, media_type="application/json"
            )
        origin = (
            EventOrigin.NORMALIZED
            if kind in {EventKind.TRANSPORT_CONNECTED, EventKind.TRANSPORT_DISCONNECTED}
            else EventOrigin.WIRE_OBSERVED
            if is_wire
            else EventOrigin.DERIVED
            if kind is EventKind.WORKSPACE_CHANGED
            else EventOrigin.HARNESS_REPORTED
        )
        source = (
            "mcp_pal.server_group"
            if kind in {EventKind.TRANSPORT_CONNECTED, EventKind.TRANSPORT_DISCONNECTED}
            else "mcp_pal.capture"
            if is_wire
            else "mcp_pal.workspace"
            if kind is EventKind.WORKSPACE_CHANGED
            else "mcp_pal.agent.adapter"
        )
        self._recorder.emit(
            kind,
            payload=event_payload,
            session_id=session_id,
            turn_id=turn_id,
            lifecycle_phase=phase,
            server_binding=server_binding if isinstance(server_binding, str) else None,
            connection_id=connection_value
            if isinstance(connection_value, str)
            else None,
            correlation=correlation,
            raw_evidence_ref=raw_evidence,
            provenance=EventSource(
                origin=origin,
                source=source,
            ),
        )

    def _finalize(
        self,
        outcome: ExecutionOutcome,
        *,
        cleanup_succeeded: bool = True,
        limitations: Sequence[str] = (),
    ) -> TraceResult:
        direct_result = _direct_result_payload(self._direct_result)
        if self._bridge is not None:
            return self._bridge.finalize(
                outcome,
                cleanup_succeeded=cleanup_succeeded,
                limitations=tuple(limitations),
                direct_result=direct_result,
            )
        return self._recorder.finalize(
            outcome,
            cleanup_succeeded=cleanup_succeeded,
            limitations=tuple(limitations),
            direct_result=direct_result,
        )


class AsyncExecutionController:
    """Own submitted handles and their bounded async lifecycle."""

    def __init__(
        self,
        kit: Any,
        *,
        redaction_config: RedactionConfig | None = None,
        store: ExecutionStore | None = None,
        worker: bool = True,
    ) -> None:
        self.kit = kit
        self.redaction_config = redaction_config or RedactionConfig.from_environment()
        self._handles: set[AsyncExecutionHandle] = set()
        self._persistent_store: _PersistentExecutionStore | None = cast(
            _PersistentExecutionStore | None, store
        )
        self._persistent_worker: Any = None
        self._worker_thread: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._worker_enabled = worker
        self._worker_loop: asyncio.AbstractEventLoop | None = None
        self._handles_by_id: dict[str, AsyncExecutionHandle] = {}

    def start_embedded_worker(self) -> None:
        """Start this toolkit's owned stable-store worker if configured."""
        self._ensure_persistent_worker()

    def _ensure_persistent_worker(self) -> None:
        if self._persistent_store is None or not self._worker_enabled:
            return
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._worker_loop = asyncio.get_running_loop()
        self._worker_stop.clear()

        def run_command(command: Any, _store: Any, _lease: Any) -> None:
            identifier = str(command.execution_id)
            handle = self._handles_by_id.get(identifier)
            if handle is None:
                payload = (
                    command.payload.get("spec")
                    if isinstance(command.payload, Mapping)
                    else None
                )
                if not isinstance(payload, Mapping):
                    raise RuntimeError(
                        "persistent command has no portable execution specification"
                    )
                kind = payload.get("kind")
                spec = (
                    AgentSpec.model_validate(payload)
                    if kind == "agent"
                    else DirectSpec.model_validate(payload)
                    if kind == "direct"
                    else None
                )
                if spec is None:
                    raise RuntimeError(
                        "persistent command specification kind is invalid"
                    )
                handle = AsyncExecutionHandle(
                    self,
                    spec,
                    store=self._persistent_store,
                    persistent=True,
                    execution_id=command.execution_id,
                    run_id=str(command.payload.get("run_id"))
                    if command.payload.get("run_id")
                    else None,
                )
                self._handles_by_id[identifier] = handle
            loop = self._worker_loop
            if loop is None or loop.is_closed():
                raise RuntimeError("persistent toolkit event loop is closed")
            future = asyncio.run_coroutine_threadsafe(handle._start_from_worker(), loop)
            future.result()

        # Keep storage optional at import time; this concrete worker is loaded
        # only when a persistent toolkit explicitly starts its worker.
        from .services.persistent import SQLiteStoreWorker

        self._persistent_worker = SQLiteStoreWorker(self._persistent_store, run_command)

        def worker_main() -> None:
            assert self._persistent_worker is not None
            while not self._worker_stop.is_set():
                try:
                    claimed = self._persistent_worker.run_once()
                except Exception:
                    # The store has already durably marked a claimed command
                    # failed. Keep the embedded worker available for later
                    # submissions instead of losing the owning thread.
                    claimed = True
                if not claimed:
                    self._worker_stop.wait(0.05)

        self._worker_thread = threading.Thread(
            target=worker_main,
            name="mcp-pal-embedded-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def submit(
        self, spec: ExecutionSpec, *, run_id: str | None = None
    ) -> AsyncExecutionHandle:
        if not isinstance(spec, (DirectSpec, AgentSpec)):
            raise ModelValidationError(
                "execution spec is invalid", details={"operation": "execution.submit"}
            )
        explicit_run_id = getattr(spec.run_id, "root", spec.run_id)
        resolution_provenance: tuple[Mapping[str, Any], ...] = ()
        try:
            spec, resolution_provenance = resolve_execution_spec(
                spec, self._persistent_store
            )
        except ProfileResolutionError:
            raise
        effective_run_id = (
            str(explicit_run_id or run_id) if (explicit_run_id or run_id) else None
        )
        if self._persistent_store is None:
            handle = AsyncExecutionHandle(
                self,
                spec,
                run_id=effective_run_id,
                resolution_provenance=resolution_provenance,
            )
        else:
            if not callable(getattr(self._persistent_store, "enqueue_command", None)):
                raise ModelValidationError(
                    "persistent execution store does not support durable commands",
                    details={"operation": "execution.submit"},
                )
            handle = AsyncExecutionHandle(
                self,
                spec,
                store=self._persistent_store,
                persistent=True,
                run_id=effective_run_id,
                resolution_provenance=resolution_provenance,
            )
            handle._recorder.emit(
                EventKind.EXECUTION_STATE_CHANGED,
                payload={"lifecycle": ExecutionStatus.QUEUED.value},
            )
            try:
                payload = spec.model_dump(mode="json")
                enqueue_command = self._persistent_store.enqueue_command
                enqueue_command(
                    handle.execution_id,
                    payload={"spec": payload, "run_id": effective_run_id},
                    command_id=f"command-{handle.execution_id.root}",
                )
            except Exception:
                # Leave no orphaned metadata when a non-serializable runtime
                # specification cannot enter the durable queue.
                try:
                    self._persistent_store.request_cancel(
                        handle.execution_id,
                        reason="persistent submission failed",
                    )
                except Exception:
                    pass
                raise
        self._handles.add(handle)
        if self._persistent_store is not None:
            self._handles_by_id[str(handle.execution_id)] = handle
            self._ensure_persistent_worker()
        return handle

    async def run(
        self, spec: ExecutionSpec, *, run_id: str | None = None
    ) -> ExecutionResult:
        return await self.submit(spec, run_id=run_id).result()

    async def close(self) -> None:
        handles = tuple(self._handles)
        for handle in handles:
            await handle.cancel()
        if self._persistent_store is not None:
            self._worker_stop.set()
            if self._persistent_worker is not None:
                self._persistent_worker.stop()
            thread = self._worker_thread
            if thread is not None and thread.is_alive():
                await asyncio.to_thread(thread.join)
            self._worker_thread = None
            self._persistent_worker = None
            self._worker_loop = None

    def _finished(self, handle: AsyncExecutionHandle) -> None:
        self._handles.discard(handle)
        self._handles_by_id.pop(str(handle.execution_id), None)


__all__ = ["AgentRunner", "AsyncExecutionController", "AsyncExecutionHandle"]
