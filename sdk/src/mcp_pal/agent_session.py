"""Harness-neutral multi-turn agent session state machine.

Concrete ACP, Claude, and OpenCode adapters are deliberately not imported
here.  They implement the small :class:`HarnessAdapter` protocol and keep
their process, conversation, and MCP connection state alive for the lifetime
of one session.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from .errors import (
    CleanupError,
    KitClosed,
    MCPError,
    OperationCancelled,
    OperationTimeout,
    SessionBusy,
    SessionStillOpen,
    TransportError,
    UnsupportedFeature,
)
from .execution_trace import ExecutionTraceRecorder
from .interaction_handlers import InteractionController
from .policy import (
    ToolDescriptor,
    ToolPolicyDecision,
    ToolPolicyEvaluator,
    ToolPolicyEvidence,
)
from .storage import ArtifactStore, InMemoryExecutionStore
from .trace.redaction import redact_for_api
from .types import (
    ActivityHealth,
    AgentExecutionSpec,
    ErrorCode,
    ErrorInfo,
    EventDirection,
    EventKind,
    EventOrigin,
    EventProvenance,
    ExecutionId,
    ExecutionOutcome,
    ExecutionResult,
    ExecutionSnapshot,
    FullToolPolicy,
    LifecyclePhase,
    LifecycleState,
    NativeToolPolicy,
    OpaqueContent,
    RestrictiveToolPolicy,
    SessionForkRequest,
    SessionId,
    SessionProvenance,
    TurnId,
    TurnLifecycle,
    TurnOutcome,
    TurnResponse,
    TurnResult,
    UserMessage,
)
from .workspace import WorkspaceCapture, WorkspaceError, WorkspaceManager

if TYPE_CHECKING:
    from .harness.observations import TurnEvidence


class HarnessAdapter(Protocol):
    """Minimal injected adapter contract for one continuing conversation.

    ``start`` and ``close`` are called at most once.  ``send`` must use the
    already-open conversation and MCP connections; it must not implement a
    one-shot resume fallback.  Adapters may optionally expose
    ``supported_content_kinds`` or ``supports_content`` for preflight.
    """

    async def start(self, spec: AgentExecutionSpec) -> None: ...

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse | "AdapterTurn": ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AdapterTurn:
    """Optional adapter return envelope for tool errors and terminal loss."""

    response: TurnResponse | None = None
    error: ErrorInfo | None = None
    terminal: bool = False
    outcome: TurnOutcome = TurnOutcome.COMPLETED
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    evidence: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)
    trace_limitations: tuple[str, ...] = ()
    # Provider adapters may carry the closed R5 typed observation envelope
    # without making this core state machine import provider modules.
    turn_evidence: TurnEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        object.__setattr__(self, "trace_limitations", tuple(str(item) for item in self.trace_limitations))


class HarnessTurnError(MCPError):
    """Value-free adapter error with explicit session-terminal semantics."""

    code = "harness_turn_error"

    def __init__(self, message: str = "harness turn failed", *, terminal: bool = False) -> None:
        super().__init__(message)
        self.terminal = terminal


_EventSink = Callable[
    [EventKind, Mapping[str, Any], SessionId, TurnId | None, LifecyclePhase],
    None,
]


@dataclass(slots=True)
class QueuedTurn:
    """A FIFO turn handle returned by :meth:`AsyncAgentSession.enqueue_turn`."""

    turn_id: TurnId
    _future: asyncio.Future[TurnResult]

    async def result(self) -> TurnResult:
        return await asyncio.shield(self._future)

    async def wait(self) -> TurnResult:
        return await self.result()


@dataclass(slots=True)
class _QueueItem:
    message: UserMessage
    timeout: float | None
    metadata: Mapping[str, object] | None
    handle: QueuedTurn


class AsyncAgentSession:
    """Lifecycle-safe async session over one injected harness adapter."""

    _CANCEL_GRACE_SECONDS = 0.25

    def __init__(
        self,
        spec: AgentExecutionSpec,
        adapter: HarnessAdapter,
        *,
        server_manager: Any = None,
        server_manager_factory: Callable[[], Any] | None = None,
        interaction_controller: InteractionController | None = None,
        provenance: SessionProvenance | None = None,
        on_close: Callable[["AsyncAgentSession"], None] | None = None,
        event_sink: _EventSink | None = None,
        trace_recorder: ExecutionTraceRecorder | None = None,
        trace_owner: bool = True,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.spec = spec
        self.adapter = adapter
        # The manager owns the server descriptors and any SDK-created
        # loopback resources for this conversation.  ``Any`` keeps this core
        # state machine independent from the richer harness contract module.
        self._server_manager = server_manager
        self._server_manager_factory = server_manager_factory
        self._interactions = interaction_controller or InteractionController(
            permission_policy=spec.permission_policy,
            elicitation_policy=spec.elicitation_policy,
            sampling_policy=spec.sampling_policy,
            filesystem_policy=spec.filesystem_policy,
            terminal_policy=spec.terminal_policy,
        )
        self._provenance = provenance
        self._on_close = on_close
        self._event_sink = event_sink
        self._trace_recorder = trace_recorder or ExecutionTraceRecorder(
            InMemoryExecutionStore(),
            ExecutionId(str(uuid4())),
        )
        self._trace_owner = trace_owner
        self._execution_id = self._trace_recorder.execution_id
        self._session_id = SessionId(str(uuid4()))
        self._session_created_emitted = False
        self._snapshot = ExecutionSnapshot(execution_id=self._execution_id, provenance=provenance)
        self._turns: list[TurnResult] = []
        self._tool_outcomes: list[bool] = []
        # Keep wire-derived tool outcomes after the server manager releases
        # its capture object during close; adapter reports cannot represent
        # MCP's application-level ``isError`` result faithfully.
        self._captured_tool_outcomes: list[bool] = []
        self._terminal_result: ExecutionResult | None = None
        self._terminal_outcome: ExecutionOutcome | None = None
        self._terminal_error: ErrorInfo | None = None
        self._entered = False
        self._closed = False
        self._closing = False
        self._close_requested_outcome: ExecutionOutcome | None = None
        self._active = False
        self._active_turn_task: asyncio.Task[Any] | None = None
        self._operation_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._queue: asyncio.Queue[_QueueItem | None] = asyncio.Queue()
        self._queue_task: asyncio.Task[None] | None = None
        self._adapter_started = False
        self._adapter_closed = False
        self._server_manager_closed = False
        self._startup_task: asyncio.Task[Any] | None = None
        self._tool_policy_evidence: ToolPolicyEvidence | None = None
        self._workspace = WorkspaceManager(
            spec.workspace,
            self._execution_id,
            artifact_policy=spec.artifact_policy,
            declared_artifacts=spec.declared_artifacts,
            artifact_store=artifact_store,
        )
        self._workspace_capture: WorkspaceCapture | None = None
        self._workspace_artifacts: tuple[Any, ...] = ()
        self._capture_seen: dict[str, int] = {}
        # Cleanup ownership may remain retryable after a terminal outcome.
        # Keep the first failed attempt in the eventual terminal trace even
        # when a later close succeeds; otherwise a retry would falsely claim
        # complete evidence.
        self._cleanup_failed = False
        # A terminal adapter failure can leave the provider's final protocol
        # exchange unknowable even when our own cleanup succeeds.  Preserve
        # that distinction in the finalized trace instead of calling it
        # complete merely because child processes were reaped.
        self._trace_limitations: tuple[str, ...] = ()

    def _emit_event(
        self,
        kind: EventKind,
        payload: Mapping[str, Any],
        *,
        turn_id: TurnId | None = None,
        phase: LifecyclePhase = LifecyclePhase.UNKNOWN,
    ) -> None:
        if self._event_sink is not None:
            self._event_sink(kind, payload, self._session_id, turn_id, phase)
            if kind is EventKind.SESSION_CREATED:
                self._session_created_emitted = True
            return
        event_payload = dict(payload)
        connection_id = event_payload.pop("_mcp_connection_id", None)
        direction_value = event_payload.pop("_mcp_direction", None)
        server_binding = event_payload.pop("_mcp_server_binding", None)
        raw_ref = event_payload.pop("_mcp_raw_evidence_ref", None)
        correlation = None
        if isinstance(direction_value, str):
            try:
                direction = EventDirection(direction_value)
                from .types import RawEvidenceRef, RequestCorrelation

                correlation = RequestCorrelation(
                    jsonrpc_id=event_payload.get("jsonrpc_id")
                    if isinstance(event_payload.get("jsonrpc_id"), (int, str))
                    and not isinstance(event_payload.get("jsonrpc_id"), bool)
                    else None,
                    direction=direction,
                    request_sequence=event_payload.get("request_sequence")
                    if isinstance(event_payload.get("request_sequence"), int)
                    else None,
                )
            except ValueError:
                correlation = None
        raw_evidence = None
        if isinstance(raw_ref, str) and raw_ref:
            from .types import RawEvidenceRef

            raw_evidence = RawEvidenceRef(evidence_id=raw_ref, media_type="application/json")
        origin = EventOrigin.WIRE_OBSERVED if payload.get("evidence_mode") == "wire_observed" else (
            EventOrigin.DERIVED if kind is EventKind.WORKSPACE_CHANGED else EventOrigin.HARNESS_REPORTED
        )
        self._trace_recorder.emit(
            kind,
            payload=event_payload,
            session_id=self._session_id,
            turn_id=turn_id,
            lifecycle_phase=phase,
            server_binding=server_binding if isinstance(server_binding, str) else None,
            connection_id=connection_id if isinstance(connection_id, str) else None,
            correlation=correlation,
            raw_evidence_ref=raw_evidence,
            provenance=EventProvenance(
                origin=origin,
                source="mcp_pal.capture" if origin is EventOrigin.WIRE_OBSERVED else (
                    "mcp_pal.workspace" if kind is EventKind.WORKSPACE_CHANGED else "mcp_pal.agent.adapter"
                ),
            ),
        )
        if kind is EventKind.SESSION_CREATED:
            self._session_created_emitted = True

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"spec", "adapter"} and name in self.__dict__:
            raise AttributeError(f"{name} is immutable for an open agent session")
        object.__setattr__(self, name, value)

    async def __aenter__(self) -> "AsyncAgentSession":
        async with self._state_lock:
            if self._closed:
                raise KitClosed("agent session is closed")
            if self._entered:
                return self
            current = asyncio.current_task()
            startup = self._startup_task
            if startup is None:
                self._snapshot = self._snapshot.transition(LifecycleState.STARTING)
                self._startup_task = current
                startup = current
                self._emit_event(EventKind.SESSION_CREATED, {"lifecycle": "created"})
                self._emit_event(
                    EventKind.SESSION_STATE_CHANGED,
                    {"lifecycle": LifecycleState.STARTING.value},
                    phase=LifecyclePhase.STARTUP,
                )
        if startup is not asyncio.current_task():
            assert startup is not None
            try:
                await asyncio.shield(startup)
            except asyncio.CancelledError:
                raise
            return self
        try:
            await asyncio.to_thread(self._workspace.create)
            await self._start_adapter()
            async with self._state_lock:
                if self._closing or self._closed:
                    raise KitClosed("agent session is closing")
                self._entered = True
                if self._snapshot.lifecycle is LifecycleState.STARTING:
                    self._snapshot = self._snapshot.transition(LifecycleState.IDLE)
                    self._emit_event(
                        EventKind.SESSION_STATE_CHANGED,
                        {"lifecycle": LifecycleState.IDLE.value},
                        phase=LifecyclePhase.IDLE,
                    )
        except BaseException as exc:
            # A close may race startup.  Startup still owns the workspace and
            # server resources until this cleanup attempt finishes; do not
            # short-circuit here or the later close cannot retry them.
            cleanup_failure = await self._collect_workspace(
                self._close_requested_outcome
                or (ExecutionOutcome.CANCELLED if isinstance(exc, asyncio.CancelledError) else ExecutionOutcome.FAILED),
                cleanup=False,
            )
            try:
                await self._close_adapter()
            except BaseException:
                cleanup_failure = True
            outcome = self._close_requested_outcome or (
                ExecutionOutcome.CANCELLED if isinstance(exc, asyncio.CancelledError) else ExecutionOutcome.FAILED
            )
            try:
                await asyncio.to_thread(self._workspace.cleanup)
            except Exception:
                cleanup_failure = True
            code = ErrorCode.CANCELLED if outcome is ExecutionOutcome.CANCELLED else ErrorCode.TRANSPORT_ERROR
            await self._finish(outcome, code, "session startup cancelled" if outcome is ExecutionOutcome.CANCELLED else "session startup failed")
            if cleanup_failure:
                # Keep the terminal result and all unfinished owners visible
                # for a later aclose()/kit close retry.  _record_cleanup_failure
                # preserves the sanitized startup error and adds only generic
                # cleanup evidence.
                self._cleanup_failed = True
                self._record_cleanup_failure()
            else:
                self._finalize_result(cleanup_succeeded=True)
                self._closed = True
                if self._on_close:
                    self._on_close(self)
            if isinstance(exc, asyncio.CancelledError):
                if self._close_requested_outcome is not None:
                    raise KitClosed("agent session is closing") from None
                raise
            if isinstance(exc, UnsupportedFeature):
                # Policy/attachment capability rejection is a caller-visible
                # preflight result, not an opaque transport failure.  Do not
                # propagate provider-controlled exception text.
                raise UnsupportedFeature("session startup unsupported") from None
            if not isinstance(exc, Exception):
                raise
            raise TransportError("agent session startup failed") from None
        finally:
            async with self._state_lock:
                if self._startup_task is asyncio.current_task():
                    self._startup_task = None
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.aclose()

    async def _preflight_launch(self, launch: Any) -> Any:
        """Run adapter policy preflight before opening or starting a harness."""

        preflight = getattr(self.adapter, "preflight", None)
        if not callable(preflight):
            if self._requires_policy_preflight():
                raise UnsupportedFeature("harness cannot prove tool policy enforcement")
            return launch
        readiness = preflight(launch)
        if inspect.isawaitable(readiness):
            readiness = await readiness
        if not getattr(readiness, "ready", True):
            raise UnsupportedFeature("requested harness policy is unavailable")
        evidence = getattr(self.adapter, "last_policy_evidence", None)
        if not isinstance(evidence, ToolPolicyEvidence):
            evidence = getattr(launch, "tool_policy_evidence", None)
        if self._requires_policy_preflight() and not isinstance(evidence, ToolPolicyEvidence):
            raise UnsupportedFeature("harness cannot prove tool policy enforcement")
        if isinstance(evidence, ToolPolicyEvidence):
            expected = (
                "native"
                if isinstance(self.spec.tool_policy, NativeToolPolicy)
                else "full"
                if isinstance(self.spec.tool_policy, FullToolPolicy)
                else "restrictive"
            )
            if evidence.requested != expected:
                raise UnsupportedFeature("harness policy evidence does not match the requested policy")
            if expected == "native":
                valid_evidence = evidence.enforced == "native" and not evidence.portable
            else:
                valid_evidence = evidence.enforced == "portable" and evidence.portable
            if not valid_evidence:
                raise UnsupportedFeature("harness policy evidence is not enforceable")
            self._tool_policy_evidence = evidence
            return launch.with_tool_policy_evidence(evidence)
        return launch

    async def _start_adapter(self) -> None:
        if self._adapter_started:
            return
        if self._server_manager is not None:
            # Import lazily: the contract module aliases this core adapter
            # protocol, so importing it at module load would create a cycle.
            await self._server_manager.start()
            async with self._state_lock:
                if self._closing or self._closed:
                    raise asyncio.CancelledError()
            opener = getattr(self.adapter, "open", None)
            if callable(opener):
                from .harness.contracts import HarnessLaunch

                launch = HarnessLaunch(
                    self.spec,
                    self._server_manager.snapshot(),
                    self._server_manager.configurations(),
                    self.spec.tool_policy,
                    self._interactions,
                    str(self._workspace.root),
                    capture=self._server_manager.capture,
                )
                launch = await self._preflight_launch(launch)
                result = opener(launch)
            else:
                preflight = getattr(self.adapter, "preflight", None)
                if callable(preflight):
                    from .harness.contracts import HarnessLaunch

                    launch = HarnessLaunch(
                        self.spec,
                        self._server_manager.snapshot(),
                        self._server_manager.configurations(),
                        self.spec.tool_policy,
                        self._interactions,
                        str(self._workspace.root),
                        capture=self._server_manager.capture,
                    )
                    await self._preflight_launch(launch)
                elif self._requires_policy_preflight():
                    raise UnsupportedFeature("harness cannot prove tool policy enforcement")
                result = None
                starter = getattr(self.adapter, "start", None)
                if callable(starter):
                    result = starter(self.spec)
        else:
            opener = getattr(self.adapter, "open", None)
            if callable(opener):
                from .harness.contracts import HarnessLaunch
                from .server_group import ServerGroupSnapshot

                launch = HarnessLaunch(
                    self.spec,
                    ServerGroupSnapshot(),
                    (),
                    self.spec.tool_policy,
                    self._interactions,
                    str(self._workspace.root),
                )
                launch = await self._preflight_launch(launch)
                result = opener(launch)
            else:
                preflight = getattr(self.adapter, "preflight", None)
                if callable(preflight):
                    from .harness.contracts import HarnessLaunch
                    from .server_group import ServerGroupSnapshot

                    launch = HarnessLaunch(
                        self.spec,
                        ServerGroupSnapshot(),
                        (),
                        self.spec.tool_policy,
                        self._interactions,
                        str(self._workspace.root),
                    )
                    await self._preflight_launch(launch)
                elif self._requires_policy_preflight():
                    raise UnsupportedFeature("harness cannot prove tool policy enforcement")
                starter = getattr(self.adapter, "start", None)
                if callable(starter):
                    result = starter(self.spec)
                else:
                    enter = getattr(self.adapter, "__aenter__", None)
                    result = enter() if enter is not None else None
        if inspect.isawaitable(result):
            await result
        self._emit_captured_wire_events(None)
        async with self._state_lock:
            if self._closing or self._closed:
                raise asyncio.CancelledError()
        self._adapter_started = True

    def _requires_policy_preflight(self) -> bool:
        """Require explicit capability evidence for policies that can allow tools."""

        policy = self.spec.tool_policy
        if isinstance(policy, (FullToolPolicy, NativeToolPolicy)):
            return True
        return isinstance(policy, RestrictiveToolPolicy) and bool(
            policy.allowed_tools or policy.denied_tools
        )

    async def _close_adapter(self) -> None:
        failure: BaseException | None = None
        if not self._adapter_closed:
            try:
                close = getattr(self.adapter, "close", None) or getattr(self.adapter, "aclose", None)
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                else:
                    exit_method = getattr(self.adapter, "__aexit__", None)
                    if exit_method is not None:
                        result = exit_method(None, None, None)
                        if inspect.isawaitable(result):
                            await result
                self._adapter_closed = True
            except Exception as exc:
                failure = exc
        if self._server_manager is not None and not self._server_manager_closed:
            try:
                await self._server_manager.close()
                self._server_manager_closed = True
            except Exception as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    def _emit_captured_wire_events(self, turn_id: TurnId | None) -> None:
        """Project newly observed MCP wire events into the session trace.

        Adapter-reported tool calls remain separate events.  Wire events are
        authoritative for activity health and carry their own provenance,
        typed correlation, and raw-evidence reference.
        """

        manager = self._server_manager
        capture = getattr(manager, "capture", None) if manager is not None else None
        snapshots = getattr(capture, "snapshots", None)
        if not callable(snapshots):
            return
        try:
            records = tuple(getattr(manager.snapshot(), "records", ()))
            by_connection = {
                str(getattr(record, "connection_id", "")): str(getattr(record, "key", ""))
                for record in records
            }
            observed_snapshots = tuple(snapshots())
        except Exception:
            return
        for snapshot in observed_snapshots:
            connection_id = str(getattr(snapshot, "connection_id", ""))
            events = tuple(getattr(snapshot, "events", ()))
            start = self._capture_seen.get(connection_id, 0)
            self._capture_seen[connection_id] = len(events)
            server_binding = by_connection.get(connection_id) or None
            for event in events[start:]:
                method = getattr(event, "method", None)
                tool = getattr(event, "tool", None)
                event_kind = str(getattr(event, "kind", ""))
                error = getattr(event, "error", None)
                if event_kind == "request":
                    kind = EventKind.TOOL_CALL_REQUESTED if method == "tools/call" else EventKind.MCP_REQUEST
                elif event_kind == "notification":
                    kind = EventKind.MCP_NOTIFICATION
                elif event_kind == "error":
                    kind = EventKind.MCP_ERROR
                elif method == "tools/call":
                    kind = EventKind.TOOL_RESULT_RECEIVED
                elif method == "initialize":
                    kind = EventKind.MCP_INITIALIZED
                else:
                    kind = EventKind.MCP_RESPONSE
                direction_value = str(getattr(event, "direction", ""))
                direction = (
                    EventDirection.CLIENT_TO_SERVER
                    if direction_value == "client_to_server"
                    else EventDirection.SERVER_TO_CLIENT
                )
                payload: dict[str, Any] = {
                    "evidence_mode": "wire_observed",
                    "transport": str(getattr(event, "transport", "unknown")),
                    "method": method,
                    "tool": tool,
                    "request_sequence": getattr(event, "request_sequence", None),
                    "response_to_sequence": getattr(event, "response_to_sequence", None),
                    "jsonrpc_id": getattr(event, "jsonrpc_id", None),
                    "latency_ms": getattr(event, "latency_ms", None),
                    "wire_offset_ms": getattr(event, "offset_ms", None),
                    "policy_denied": getattr(event, "provenance", None) == "policy_denied",
                    "dedup_key": f"{connection_id}:{getattr(event, 'request_sequence', None)}",
                    "_mcp_connection_id": connection_id,
                    "_mcp_direction": direction.value,
                    "_mcp_server_binding": server_binding,
                    "_mcp_raw_evidence_ref": getattr(event, "raw_evidence_ref", None),
                }
                arguments = getattr(event, "arguments", None)
                result = getattr(event, "result", None)
                if arguments is not None:
                    payload["arguments"] = arguments
                if result is not None:
                    payload["result"] = result
                if error is not None:
                    payload["error"] = error
                if method == "tools/call" and event_kind in {"error", "response"}:
                    failed = error is not None or (
                        isinstance(result, Mapping)
                        and bool(result.get("is_error", result.get("isError", False)))
                    )
                    self._captured_tool_outcomes.append(not failed)
                phase = LifecyclePhase.MCP_CALL if method == "tools/call" else (
                    LifecyclePhase.INITIALIZATION if method == "initialize" else LifecyclePhase.IDLE
                )
                self._emit_event(kind, payload, turn_id=turn_id, phase=phase)

    async def _collect_workspace(self, outcome: ExecutionOutcome, *, cleanup: bool = True) -> bool:
        """Collect workspace evidence before removing the owned root."""

        try:
            self._workspace.root
        except WorkspaceError:
            # A session closed before startup owns no workspace to collect.
            return False
        capture_failure = False
        try:
            capture = await asyncio.to_thread(self._workspace.capture, outcome)
            self._workspace_capture = capture
            self._workspace_artifacts = capture.artifacts
            self._emit_event(
                EventKind.WORKSPACE_CHANGED,
                {
                    "diff": capture.diff.as_payload(),
                    "artifacts": tuple(ref.model_dump(mode="json") for ref in capture.artifacts),
                    "limitations": capture.limitations,
                },
                phase=LifecyclePhase.CLEANUP,
            )
        except Exception:
            capture_failure = True
        if cleanup:
            try:
                await asyncio.to_thread(self._workspace.cleanup)
            except Exception:
                return True
        return capture_failure

    async def _cancel_adapter(self) -> None:
        cancel = getattr(self.adapter, "cancel", None)
        if cancel is None:
            cancel = getattr(self.adapter, "acancel", None)
        if cancel is not None:
            result = cancel()
            if inspect.isawaitable(result):
                await result

    def _ensure_live(self) -> None:
        if self._closed or self._closing:
            raise KitClosed("agent session is closed")
        if self._terminal_result is not None:
            raise KitClosed("agent session is terminal")
        if not self._entered:
            raise SessionStillOpen("agent session has not been opened")

    @staticmethod
    def _message(message: str | UserMessage, metadata: Mapping[str, object] | None) -> UserMessage:
        try:
            value = message if isinstance(message, UserMessage) else UserMessage.model_validate({"content": message})
            if metadata is not None:
                value = value.model_copy(update={"metadata": dict(metadata)})
            return value
        except Exception:
            raise UnsupportedFeature("message contains unsupported content") from None

    async def _validate_content(self, message: UserMessage) -> None:
        try:
            supported = getattr(self.adapter, "supported_content_kinds", None)
            checker = getattr(self.adapter, "supports_content", None)
            for block in message.content:
                kind = str(getattr(block, "kind", ""))
                provider = str(getattr(block, "provider", "")) if isinstance(block, OpaqueContent) else None
                allowed = True
                if supported is not None:
                    allowed = kind in supported
                if checker is not None:
                    result = checker(block)
                    allowed = bool(await result) if inspect.isawaitable(result) else bool(result)
                if isinstance(block, OpaqueContent) and provider == "":
                    allowed = False
                if not allowed:
                    raise UnsupportedFeature(f"harness does not support {kind} message content")
        except Exception:
            raise UnsupportedFeature("message contains unsupported content") from None

    @staticmethod
    def _validate_timeout(timeout: float | None) -> None:
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be positive and finite")

    async def send(
        self,
        message: str | UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResult:
        self._ensure_live()
        self._validate_timeout(timeout)
        value = self._message(message, metadata)
        await self._validate_content(value)
        async with self._state_lock:
            if self._closed or self._closing or self._terminal_result is not None:
                raise KitClosed("agent session is closed")
            if self._active:
                raise SessionBusy("another turn is already running")
            self._active = True
            self._active_turn_task = asyncio.current_task()
        try:
            async with self._operation_lock:
                return await self._run_turn(value, timeout=timeout, metadata=value.metadata)
        finally:
            async with self._state_lock:
                self._active = False
                if self._active_turn_task is asyncio.current_task():
                    self._active_turn_task = None

    async def enqueue_turn(
        self,
        message: str | UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> QueuedTurn:
        self._ensure_live()
        self._validate_timeout(timeout)
        value = self._message(message, metadata)
        await self._validate_content(value)
        turn_id = TurnId(str(uuid4()))
        future: asyncio.Future[TurnResult] = asyncio.get_running_loop().create_future()
        handle = QueuedTurn(turn_id, future)
        async with self._state_lock:
            if self._closed or self._closing or self._terminal_result is not None:
                raise KitClosed("agent session is closed")
            await self._queue.put(_QueueItem(value, timeout, metadata, handle))
            if self._queue_task is None or self._queue_task.done():
                self._queue_task = asyncio.create_task(self._drain_queue())
        return handle

    async def _drain_queue(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            try:
                self._ensure_live()
                async with self._state_lock:
                    self._active = True
                    self._active_turn_task = asyncio.current_task()
                async with self._operation_lock:
                    result = await self._run_turn(item.message, timeout=item.timeout, metadata=item.message.metadata, turn_id=item.handle.turn_id)
            except asyncio.CancelledError:
                if not item.handle._future.done():
                    item.handle._future.set_result(self._cancelled_turn(item.handle.turn_id))
                raise
            except Exception:
                if not item.handle._future.done():
                    item.handle._future.set_result(self._cancelled_turn(item.handle.turn_id))
            else:
                if not item.handle._future.done():
                    item.handle._future.set_result(result)
            finally:
                async with self._state_lock:
                    self._active = False
                    if self._active_turn_task is asyncio.current_task():
                        self._active_turn_task = None

    async def _run_turn(
        self,
        message: UserMessage,
        *,
        timeout: float | None,
        metadata: Mapping[str, object] | None,
        turn_id: TurnId | None = None,
    ) -> TurnResult:
        self._validate_timeout(timeout)
        turn_id = turn_id or TurnId(str(uuid4()))
        async with self._state_lock:
            if self._snapshot.lifecycle is LifecycleState.IDLE:
                self._snapshot = self._snapshot.transition(LifecycleState.RUNNING_TURN)
        self._emit_event(
            EventKind.TURN_CREATED,
            {"number": len(self._turns) + 1},
            turn_id=turn_id,
            phase=LifecyclePhase.TURN,
        )
        self._emit_event(
            EventKind.AGENT_MESSAGE,
            {"content": message.model_dump(mode="json")},
            turn_id=turn_id,
            phase=LifecyclePhase.TURN,
        )
        self._emit_event(
            EventKind.SESSION_STATE_CHANGED,
            {"lifecycle": LifecycleState.RUNNING_TURN.value},
            phase=LifecyclePhase.TURN,
        )
        try:
            sender = getattr(self.adapter, "send", None) or getattr(self.adapter, "send_turn", None)
            if sender is None:
                raise UnsupportedFeature("harness adapter does not implement turn sending")
            operation = sender(message, timeout=timeout, metadata=metadata)
            raw = await asyncio.wait_for(operation, timeout=timeout) if timeout is not None else await operation
            canonical_tool_calls = self._pending_captured_tool_calls()
            self._emit_captured_wire_events(turn_id)
            self._record_tool_outcomes(raw)
            policy_violations = self._evaluate_reported_tool_calls(
                raw,
                canonical_tool_calls=canonical_tool_calls,
            )
            self._emit_adapter_events(raw, turn_id, policy_violations)
            if policy_violations:
                self._emit_policy_violations(policy_violations, turn_id)
                details = {"policy_violations": tuple(policy_violations)}
                result = self._failure_turn(
                    turn_id,
                    TurnOutcome.FAILED,
                    ErrorCode.UNSUPPORTED,
                    "tool policy violation",
                ).model_copy(
                    update={
                        "error": ErrorInfo(
                            code=ErrorCode.UNSUPPORTED,
                            message="tool policy violation",
                            details=details,
                        )
                    }
                )
                self._emit_turn_finished(turn_id, result)
                self._turns.append(result)
                if self._terminal_requested(raw):
                    await self._finish(ExecutionOutcome.FAILED, ErrorCode.UNSUPPORTED, "tool policy violation")
                elif self._snapshot.lifecycle is LifecycleState.RUNNING_TURN:
                    self._snapshot = self._snapshot.transition(LifecycleState.IDLE)
                    self._emit_event(
                        EventKind.SESSION_STATE_CHANGED,
                        {"lifecycle": LifecycleState.IDLE.value},
                        phase=LifecyclePhase.IDLE,
                    )
                return result
            result = self._turn_result(turn_id, raw)
            self._emit_turn_finished(turn_id, result)
            if not self._terminal_requested(raw):
                async with self._state_lock:
                    if self._snapshot.lifecycle is LifecycleState.RUNNING_TURN:
                        self._snapshot = self._snapshot.transition(LifecycleState.IDLE)
                        self._emit_event(
                            EventKind.SESSION_STATE_CHANGED,
                            {"lifecycle": LifecycleState.IDLE.value},
                            phase=LifecyclePhase.IDLE,
                        )
            self._turns.append(result)
            if self._terminal_requested(raw):
                await self._finish(ExecutionOutcome.FAILED, ErrorCode.TRANSPORT_ERROR, "session lost during turn")
            return result
        except asyncio.TimeoutError:
            await self._cancel_adapter_safely()
            result = self._failure_turn(turn_id, TurnOutcome.TIMED_OUT, ErrorCode.TIMEOUT, "turn timed out")
            self._emit_turn_finished(turn_id, result)
            self._turns.append(result)
            await self._finish(ExecutionOutcome.TIMED_OUT, ErrorCode.TIMEOUT, "session timed out")
            return result
        except asyncio.CancelledError:
            await self._cancel_adapter_safely()
            result = self._failure_turn(turn_id, TurnOutcome.CANCELLED, ErrorCode.CANCELLED, "turn cancelled")
            self._emit_turn_finished(turn_id, result)
            self._turns.append(result)
            await self._finish(ExecutionOutcome.CANCELLED, ErrorCode.CANCELLED, "session cancelled")
            raise
        except Exception as exc:
            terminal = bool(getattr(exc, "terminal", False)) or isinstance(
                exc, (TransportError, OperationTimeout, OperationCancelled)
            )
            result = self._failure_turn(
                turn_id,
                self._outcome_for(exc),
                self._code_for(exc),
                self._safe_failure_message(exc),
            )
            self._emit_turn_finished(turn_id, result)
            self._turns.append(result)
            if terminal:
                await self._finish(
                    self._execution_outcome_for(exc),
                    self._code_for(exc),
                    self._safe_failure_message(exc),
                )
            elif self._snapshot.lifecycle is LifecycleState.RUNNING_TURN:
                self._snapshot = self._snapshot.transition(LifecycleState.IDLE)
                self._emit_event(
                    EventKind.SESSION_STATE_CHANGED,
                    {"lifecycle": LifecycleState.IDLE.value},
                    phase=LifecyclePhase.IDLE,
                )
            return result

    @staticmethod
    def _safe_tool_label(value: object) -> str | None:
        if not isinstance(value, str) or not value or len(value) > 256:
            return None
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            return None
        return value

    @classmethod
    def _reported_tool_identity(cls, call: Mapping[str, Any]) -> tuple[ToolDescriptor | None, str | None]:
        """Extract only a qualified MCP identity from adapter evidence."""

        try:
            qualified = call.get("qualified_name")
            server = call.get("server", call.get("server_name"))
            tool = call.get("tool", call.get("tool_name"))
            name = call.get("name")
        except Exception:
            return None, "tool_call_invalid"
        if qualified is not None and server is None and tool is None:
            qualified_text = cls._safe_tool_label(qualified)
            if qualified_text is None or qualified_text.count(":") != 1:
                return None, "tool_identity_invalid"
            server, tool = qualified_text.split(":", 1)
        elif tool is None and isinstance(name, str) and server is None and name.count(":") == 1:
            server, tool = name.split(":", 1)
        elif tool is None:
            tool = name
        if server is None and tool is None:
            return None, None
        server_text = cls._safe_tool_label(server)
        tool_text = cls._safe_tool_label(tool)
        if server_text is None or tool_text is None:
            return None, "tool_identity_invalid"
        try:
            destructive = call.get("destructive") is True
            return ToolDescriptor(server=server_text, name=tool_text, destructive=destructive), None
        except Exception:
            return None, "tool_identity_invalid"

    def _policy_harness_name(self) -> str:
        value = getattr(self.adapter, "name", None)
        if isinstance(value, str) and value:
            return value
        harness = self.spec.harness
        kind = getattr(harness, "kind", None)
        return kind if isinstance(kind, str) and kind else "unknown"

    def _policy_violation(
        self,
        descriptor: ToolDescriptor | None,
        reason: str,
        evidence: ToolPolicyEvidence,
    ) -> dict[str, object]:
        return {
            "server": descriptor.server if descriptor is not None else "unavailable",
            "tool": descriptor.name if descriptor is not None else "unavailable",
            "reason": reason,
            "policy_requested": evidence.requested,
            "policy_enforced": evidence.enforced,
            "policy_observed": "runtime",
            "policy_portable": evidence.portable,
        }

    def _evaluate_reported_tool_calls(
        self,
        raw: object,
        *,
        canonical_tool_calls: tuple[ToolDescriptor, ...] = (),
    ) -> tuple[dict[str, object], ...]:
        calls = getattr(raw, "tool_calls", ())
        if not isinstance(calls, (tuple, list)) or not calls:
            return ()
        # Proxy enforcement already handled the empty restrictive policy's
        # deny-by-default behavior.  Do not reinterpret an advisory provider
        # update as a second, post-hoc enforcement decision.
        if not self._requires_policy_preflight():
            return ()
        evidence = self._tool_policy_evidence or ToolPolicyEvidence(
            requested="restrictive", enforced="default_deny", observed="runtime"
        )
        policy = self.spec.tool_policy
        records: tuple[Any, ...] = ()
        if self._server_manager is not None:
            try:
                records = tuple(self._server_manager.snapshot().records)
            except Exception:
                records = ()
        advertised = tuple(
            ToolDescriptor(server=record.key, name=tool)
            for record in records
            if getattr(record, "available", False)
            for tool in getattr(record, "tools", ())
            if isinstance(tool, str)
        )
        evaluator_tools = advertised
        if not evaluator_tools:
            evaluator_tools = tuple(
                descriptor
                for call in calls
                if isinstance(call, Mapping)
                for descriptor, identity_error in (self._reported_tool_identity(call),)
                if descriptor is not None and identity_error is None
            )
        violations: list[dict[str, object]] = []
        # Preserve the pre-R6 positional association for adapters that emit
        # entirely anonymous updates, but never use it to repair malformed or
        # ambiguous identities. Exact cardinality is required and the adapter
        # must omit every identity field.
        ordered_canonical = canonical_tool_calls if len(canonical_tool_calls) == len(calls) else ()
        for call_index, call in enumerate(calls):
            if not isinstance(call, Mapping):
                violations.append(self._policy_violation(None, "tool_call_invalid", evidence))
                continue
            descriptor, identity_error = self._reported_tool_identity(call)
            if ordered_canonical and not any(
                call.get(key) is not None
                for key in (
                    "server",
                    "server_name",
                    "tool",
                    "tool_name",
                    "name",
                    "qualified_name",
                )
            ):
                descriptor = ordered_canonical[call_index]
                identity_error = None
            if descriptor is None:
                try:
                    reported_name = call.get("tool", call.get("tool_name", call.get("name")))
                except Exception:
                    reported_name = None
                safe_name = self._safe_tool_label(reported_name)
                canonical_candidates = tuple(
                    item
                    for item in canonical_tool_calls
                    if safe_name is None or item.name == safe_name
                )
                if len(canonical_candidates) == 1:
                    descriptor = canonical_candidates[0]
                    identity_error = None
                elif len(canonical_candidates) > 1:
                    violations.append(self._policy_violation(None, "ambiguous_tool", evidence))
                    continue
            if descriptor is None and identity_error is None and records:
                try:
                    reported_name = call.get("tool", call.get("tool_name", call.get("name")))
                except Exception:
                    reported_name = None
                safe_name = self._safe_tool_label(reported_name)
                advertised_candidates = tuple(
                    record
                    for record in records
                    if getattr(record, "available", False) and safe_name in getattr(record, "tools", ())
                ) if safe_name is not None else ()
                if len(advertised_candidates) > 1:
                    violations.append(self._policy_violation(None, "ambiguous_tool", evidence))
                    continue
                if len(advertised_candidates) == 1:
                    descriptor = ToolDescriptor(server=advertised_candidates[0].key, name=safe_name or "")
                elif safe_name is not None:
                    violations.append(self._policy_violation(None, "tool_unavailable", evidence))
                    continue
            if descriptor is None and identity_error is None:
                violations.append(self._policy_violation(None, "tool_identity_unavailable", evidence))
                continue
            if descriptor is None:
                violations.append(self._policy_violation(None, identity_error or "tool_identity_invalid", evidence))
                continue
            if records:
                matching = tuple(record for record in records if record.key == descriptor.server)
                if not matching or not matching[0].available:
                    violations.append(self._policy_violation(descriptor, "server_unavailable", evidence))
                    continue
                if matching[0].tools and descriptor.name not in matching[0].tools:
                    violations.append(self._policy_violation(descriptor, "tool_unavailable", evidence))
                    continue
            try:
                evaluator = ToolPolicyEvaluator(evaluator_tools or (descriptor,))
                supports = evidence.enforced in {"portable", "native"} or not self._requires_policy_preflight()
                decision: ToolPolicyDecision = evaluator.decide(
                    policy,
                    descriptor,
                    harness_name=self._policy_harness_name(),
                    supports_enforcement=supports,
                )
            except Exception:
                violations.append(self._policy_violation(descriptor, "policy_unavailable", evidence))
                continue
            if not decision.allowed:
                violations.append(self._policy_violation(descriptor, decision.reason, decision.evidence))
        return tuple(violations)

    def _emit_policy_violations(self, violations: tuple[dict[str, object], ...], turn_id: TurnId) -> None:
        for violation in violations:
            self._emit_event(
                EventKind.TOOL_RESULT_RECEIVED,
                {"policy_violation": True, "evidence_mode": "policy_evaluator", **violation},
                turn_id=turn_id,
                    phase=LifecyclePhase.MCP_CALL,
                )

    def _pending_captured_tool_calls(self) -> tuple[ToolDescriptor, ...]:
        """Return this turn's canonical forwarded calls before advancing capture."""

        manager = self._server_manager
        capture = getattr(manager, "capture", None) if manager is not None else None
        snapshots = getattr(capture, "snapshots", None)
        if not callable(snapshots):
            return ()
        try:
            records = tuple(getattr(manager.snapshot(), "records", ()))
            aliases = {
                str(getattr(record, "connection_id", "")): str(getattr(record, "key", ""))
                for record in records
            }
            output: list[ToolDescriptor] = []
            for snapshot in snapshots():
                connection_id = str(getattr(snapshot, "connection_id", ""))
                start = self._capture_seen.get(connection_id, 0)
                for event in tuple(getattr(snapshot, "events", ()))[start:]:
                    if (
                        getattr(event, "method", None) == "tools/call"
                        and getattr(event, "direction", None) == "client_to_server"
                        and getattr(event, "provenance", None) != "policy_denied"
                    ):
                        alias = aliases.get(connection_id)
                        tool = self._safe_tool_label(getattr(event, "tool", None))
                        if alias and tool:
                            output.append(ToolDescriptor(server=alias, name=tool))
            return tuple(output)
        except Exception:
            return ()

    def _emit_adapter_events(
        self,
        raw: object,
        turn_id: TurnId,
        policy_violations: tuple[dict[str, object], ...] = (),
    ) -> None:
        if getattr(raw, "turn_evidence", None) is not None:
            # Typed R5 observations have already been persisted by
            # ``_turn_result``; emitting the legacy adapter envelope too would
            # duplicate reported tool calls in the finalized projector.
            return
        calls = getattr(raw, "tool_calls", ())
        if not isinstance(calls, (tuple, list)):
            return
        for index, call in enumerate(calls):
            if not isinstance(call, Mapping):
                continue
            descriptor, identity_error = self._reported_tool_identity(call)
            blocked = bool(policy_violations) and (
                any(
                    descriptor is not None
                    and item.get("server") == descriptor.server
                    and item.get("tool") == descriptor.name
                    for item in policy_violations
                )
                or (descriptor is None and identity_error is not None)
                or (descriptor is None and identity_error is None and any(item.get("server") == "unavailable" for item in policy_violations))
            )
            if blocked:
                # Adapter payloads may contain arguments or provider output.
                # Preserve only the qualified identity and an explicit marker;
                # policy evidence must never become a raw-value bypass.
                safe_call: dict[str, object] = {
                    "server": descriptor.server if descriptor is not None else "unavailable",
                    "tool": descriptor.name if descriptor is not None else "unavailable",
                    "policy_blocked": True,
                    "raw_evidence": "redacted_by_policy_boundary",
                }
            else:
                try:
                    safe_call = dict(call)
                except Exception:
                    safe_call = {"raw_evidence": "unavailable"}
            payload = {
                "tool_call": safe_call,
                "tool_index": index,
                "evidence_mode": "adapter_reported",
            }
            self._emit_event(
                EventKind.TOOL_CALL_REQUESTED,
                payload,
                turn_id=turn_id,
                phase=LifecyclePhase.MCP_CALL,
            )
            self._emit_event(
                EventKind.TOOL_RESULT_RECEIVED,
                payload,
                turn_id=turn_id,
                phase=LifecyclePhase.MCP_CALL,
            )

    def _emit_turn_finished(self, turn_id: TurnId, result: TurnResult) -> None:
        payload: dict[str, Any] = {"lifecycle": TurnLifecycle.FINISHED.value}
        if result.snapshot.outcome is not None:
            payload["outcome"] = result.snapshot.outcome.value
        self._emit_event(
            EventKind.TURN_STATE_CHANGED,
            payload,
            turn_id=turn_id,
            phase=LifecyclePhase.TURN,
        )
        if result.response is not None:
            self._emit_event(
                EventKind.ASSISTANT_CONTENT,
                {"content": result.response.model_dump(mode="json")},
                turn_id=turn_id,
                phase=LifecyclePhase.TURN,
            )

    @staticmethod
    def _safe_failure_message(exc: BaseException) -> str:
        if isinstance(exc, HarnessTurnError):
            return "harness turn failed"
        if isinstance(exc, OperationTimeout):
            return "turn timed out"
        if isinstance(exc, OperationCancelled):
            return "turn cancelled"
        if isinstance(exc, TransportError):
            return "transport failed during turn"
        return "turn failed"

    @staticmethod
    def _code_for(exc: BaseException) -> ErrorCode:
        if isinstance(exc, OperationTimeout):
            return ErrorCode.TIMEOUT
        if isinstance(exc, OperationCancelled):
            return ErrorCode.CANCELLED
        if isinstance(exc, TransportError):
            return ErrorCode.TRANSPORT_ERROR
        return ErrorCode.PROTOCOL_ERROR

    @staticmethod
    def _outcome_for(exc: BaseException) -> TurnOutcome:
        if isinstance(exc, OperationTimeout):
            return TurnOutcome.TIMED_OUT
        if isinstance(exc, OperationCancelled):
            return TurnOutcome.CANCELLED
        return TurnOutcome.FAILED

    @staticmethod
    def _execution_outcome_for(exc: BaseException) -> ExecutionOutcome:
        if isinstance(exc, OperationTimeout):
            return ExecutionOutcome.TIMED_OUT
        if isinstance(exc, OperationCancelled):
            return ExecutionOutcome.CANCELLED
        return ExecutionOutcome.FAILED

    @staticmethod
    def _terminal_requested(raw: object) -> bool:
        return bool(getattr(raw, "terminal", False))

    @staticmethod
    def _safe_turn_evidence(value: object) -> Mapping[str, Any]:
        """Project adapter evidence before it becomes a public result.

        Evidence is observational and must never be a raw-value escape hatch.
        A hostile adapter object or failed redaction therefore becomes a
        value-free availability marker rather than propagating raw data.
        """

        try:
            projected = redact_for_api(value)
            if not isinstance(projected, Mapping):
                return {"evidence_state": "unavailable"}
            safe: dict[str, Any] = {}
            for key, item in projected.items():
                if not isinstance(key, str):
                    return {"evidence_state": "unavailable"}
                safe[key] = item
            return safe
        except Exception:
            return {"evidence_state": "unavailable"}

    def _turn_result(self, turn_id: TurnId, raw: object) -> TurnResult:
        typed_evidence = getattr(raw, "turn_evidence", None)
        if typed_evidence is not None:
            # Provider adapters use the R5 typed boundary. Persist those
            # observations through the same failure-safe sink as direct
            # capture, so finalized TraceView contains provider output
            # without making the session state machine understand schemas.
            from .harness.observation_sink import HarnessObservationSink
            from .harness.observations import TurnEvidence

            if isinstance(typed_evidence, TurnEvidence):
                sink = HarnessObservationSink(self._trace_recorder, turn_id=turn_id)
                for observation in typed_evidence.observations:
                    sink.emit(observation)
                for limitation in (*typed_evidence.limitations, *sink.limitations):
                    if limitation not in self._trace_limitations:
                        self._trace_limitations = (
                            *self._trace_limitations,
                            limitation,
                        )
        if isinstance(raw, AdapterTurn):
            response, error, outcome = raw.response, raw.error, raw.outcome
        elif isinstance(raw, TurnResponse):
            response, error, outcome = raw, None, TurnOutcome.COMPLETED
        elif isinstance(raw, TurnResult):
            response, error, outcome = raw.response, raw.error, raw.snapshot.outcome or TurnOutcome.FAILED
        else:
            raise UnsupportedFeature("harness returned an unsupported turn result")
        limitations = getattr(raw, "trace_limitations", ())
        if isinstance(limitations, (tuple, list)):
            merged = list(self._trace_limitations)
            for limitation in limitations:
                if isinstance(limitation, str) and limitation not in merged:
                    merged.append(limitation)
            self._trace_limitations = tuple(merged)
        snapshot = self._turn_snapshot(turn_id, outcome)
        if error is not None:
            error = ErrorInfo(code=error.code, message="tool error" if not self._terminal_requested(raw) else "turn failed", retryable=error.retryable)
        return TurnResult(
            snapshot=snapshot,
            response=response,
            error=error,
            evidence=self._safe_turn_evidence(getattr(raw, "evidence", {})),
        )

    def _record_tool_outcomes(self, raw: object) -> None:
        """Consume optional adapter tool-call evidence without trusting it for lifecycle."""

        calls = getattr(raw, "tool_calls", ())
        if not isinstance(calls, (tuple, list)):
            return
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            result = call.get("result")
            failed = bool(call.get("is_error", call.get("isError", False)))
            if isinstance(result, Mapping):
                failed = failed or bool(result.get("is_error", result.get("isError", False)))
            status = call.get("status", call.get("outcome"))
            if isinstance(status, str) and status.lower() in {
                "failed", "error", "timed_out", "cancelled", "interrupted"
            }:
                failed = True
            self._tool_outcomes.append(not failed)

    def _activity_health(self) -> ActivityHealth:
        wire_outcomes = self._captured_activity_outcomes()
        if wire_outcomes:
            if all(wire_outcomes):
                return ActivityHealth.ALL_SUCCEEDED
            if not any(wire_outcomes):
                return ActivityHealth.ALL_FAILED
            return ActivityHealth.MIXED
        if not self._tool_outcomes:
            return ActivityHealth.NO_CALLS
        if all(self._tool_outcomes):
            return ActivityHealth.ALL_SUCCEEDED
        if not any(self._tool_outcomes):
            return ActivityHealth.ALL_FAILED
        return ActivityHealth.MIXED

    def _captured_activity_outcomes(self) -> list[bool]:
        manager = self._server_manager
        capture = getattr(manager, "capture", None) if manager is not None else None
        snapshots = getattr(capture, "snapshots", None)
        if not callable(snapshots):
            return list(self._captured_tool_outcomes)
        outcomes: list[bool] = []
        try:
            for snapshot in snapshots():
                pending: dict[int, bool] = {}
                for event in getattr(snapshot, "events", ()):
                    sequence = getattr(event, "request_sequence", None)
                    if not isinstance(sequence, int):
                        continue
                    method = getattr(event, "method", None)
                    kind = getattr(event, "kind", None)
                    if kind == "request" and method == "tools/call":
                        pending[sequence] = False
                    elif sequence in pending and kind in {"error", "response"}:
                        result = getattr(event, "result", None)
                        error = getattr(event, "error", None)
                        failed = error is not None or (
                            isinstance(result, Mapping)
                            and bool(result.get("is_error", result.get("isError", False)))
                        )
                        outcomes.append(not failed)
                        pending.pop(sequence, None)
                outcomes.extend(pending.values())
        except Exception:
            return list(self._captured_tool_outcomes)
        return outcomes

    def _turn_snapshot(self, turn_id: TurnId, outcome: TurnOutcome) -> Any:
        from .types import TurnSnapshot

        return TurnSnapshot(turn_id=turn_id, session_id=self._session_id, number=len(self._turns) + 1).transition(
            TurnLifecycle.RUNNING
        ).transition(TurnLifecycle.FINISHED, outcome)

    def _failure_turn(self, turn_id: TurnId, outcome: TurnOutcome, code: ErrorCode, message: str) -> TurnResult:
        return TurnResult(snapshot=self._turn_snapshot(turn_id, outcome), error=ErrorInfo(code=code, message=message))

    def _cancelled_turn(self, turn_id: TurnId) -> TurnResult:
        return self._failure_turn(turn_id, TurnOutcome.CANCELLED, ErrorCode.CANCELLED, "turn cancelled")

    async def _cancel_adapter_safely(self) -> None:
        try:
            await asyncio.wait_for(self._cancel_adapter(), timeout=self._CANCEL_GRACE_SECONDS)
        except Exception:
            pass

    async def _wait_for_active_turn(self) -> None:
        """Wait for graceful cancellation, then force only the active turn."""

        task = self._active_turn_task
        current = asyncio.current_task()
        if task is None or task is current:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._CANCEL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await asyncio.shield(task)
            except BaseException:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _wait_for_startup(self) -> None:
        task = self._startup_task
        if task is None or task is asyncio.current_task():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._CANCEL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await asyncio.shield(task)
            except BaseException:
                pass
        except BaseException:
            # The startup owner performs its own terminalization.  Cleanup
            # continues here and remains idempotent through the resource flags.
            pass

    async def _finish(self, outcome: ExecutionOutcome, code: ErrorCode, message: str) -> None:
        async with self._state_lock:
            if self._terminal_outcome is not None:
                return
            if not self._session_created_emitted:
                self._emit_event(EventKind.SESSION_CREATED, {"lifecycle": LifecycleState.CREATED.value})
            if outcome is not ExecutionOutcome.COMPLETED:
                self._closing = True
            self._emit_event(
                EventKind.SESSION_STATE_CHANGED,
                {"lifecycle": LifecycleState.CLOSING.value},
                phase=LifecyclePhase.CLEANUP,
            )
            if self._snapshot.lifecycle is not LifecycleState.FINISHED:
                if self._snapshot.lifecycle is LifecycleState.CREATED:
                    self._snapshot = self._snapshot.transition(LifecycleState.FINISHED, outcome)
                else:
                    if self._snapshot.lifecycle is not LifecycleState.CLOSING:
                        self._snapshot = self._snapshot.transition(LifecycleState.CLOSING)
                    self._snapshot = self._snapshot.transition(LifecycleState.FINISHED, outcome)
            self._terminal_outcome = outcome
            self._terminal_error = None if outcome is ExecutionOutcome.COMPLETED else ErrorInfo(code=code, message=message)
            self._emit_event(
                EventKind.SESSION_STATE_CHANGED,
                {"lifecycle": LifecycleState.FINISHED.value, "outcome": outcome.value},
                phase=LifecyclePhase.CLEANUP,
            )

            # Preserve the established API: a terminal turn makes the result
            # readable before context exit.  This is deliberately provisional
            # and has no trace yet; close must first collect wire/workspace
            # evidence and complete cleanup before publishing execution.finished.
            self._terminal_result = ExecutionResult(
                snapshot=self._snapshot,
                turns=tuple(self._turns),
                activity_health=self._activity_health(),
                artifacts=self._workspace_artifacts,
                error=self._terminal_error,
                provenance=self._provenance,
            )

    def _finalize_result(self, *, cleanup_succeeded: bool) -> None:
        """Commit the execution terminal event before exposing the result."""

        outcome = self._terminal_outcome
        if outcome is None:
            return
        trace = None
        if self._trace_owner:
            trace = self._trace_recorder.finalize(
                outcome,
                cleanup_succeeded=cleanup_succeeded,
                limitations=self._trace_limitations,
            )
        self._terminal_result = ExecutionResult(
            snapshot=self._snapshot,
            turns=tuple(self._turns),
            trace=trace,
            activity_health=self._activity_health(),
            artifacts=self._workspace_artifacts,
            error=self._terminal_error,
            provenance=self._provenance,
        )

    def _record_cleanup_failure(self) -> None:
        """Attach safe cleanup evidence without replacing a primary failure."""

        result = self._terminal_result
        if result is None:
            return
        if result.error is None:
            error = ErrorInfo(code=ErrorCode.CLEANUP_FAILED, message="session cleanup failed")
        else:
            details = dict(result.error.details)
            details["cleanup"] = "failed"
            error = ErrorInfo(
                code=result.error.code,
                message=result.error.message,
                retryable=result.error.retryable,
                details=details,
            )
        self._terminal_error = error
        self._terminal_result = result.model_copy(update={"error": error})

    async def _complete_close(self, outcome: ExecutionOutcome) -> None:
        await self._wait_for_startup()
        # Startup owns terminalization and cleanup when it loses a race with
        # close.  Once it has finished those duties, the waiting closer must
        # not collect evidence again after execution.finished.
        if self._closed:
            return
        async with self._state_lock:
            active = self._active
        if active:
            await self._cancel_adapter_safely()
            await self._wait_for_active_turn()
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item is not None and not item.handle._future.done():
                item.handle._future.set_result(self._cancelled_turn(item.handle.turn_id))
        if self._queue_task is not None and not self._queue_task.done():
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass
        # Capture while the native harness still has its SDK workspace.  The
        # adapter's close path may terminate a provider that removes files;
        # cleanup of our workspace itself happens only after that close.
        cleanup_failure = await self._collect_workspace(outcome, cleanup=False)
        # Project completed MCP exchanges before releasing the server group's
        # capture object.  The manager intentionally drops that object after
        # close, while activity health still needs its wire-level outcomes.
        self._emit_captured_wire_events(None)
        try:
            await self._close_adapter()
        except Exception:
            cleanup_failure = True
        self._emit_captured_wire_events(None)
        try:
            await asyncio.to_thread(self._workspace.cleanup)
        except Exception:
            cleanup_failure = True
        if self._terminal_result is not None and self._workspace_artifacts:
            self._terminal_result = self._terminal_result.model_copy(
                update={"artifacts": self._workspace_artifacts}
            )
        if cleanup_failure:
            self._cleanup_failed = True
            if self._terminal_outcome is None:
                await self._finish(ExecutionOutcome.FAILED, ErrorCode.CLEANUP_FAILED, "session cleanup failed")
        elif self._terminal_outcome is None:
            await self._finish(outcome, ErrorCode.CANCELLED, "session cancelled" if outcome is ExecutionOutcome.CANCELLED else "session closed")
        if cleanup_failure:
            self._record_cleanup_failure()
            # Keep ownership until every child has actually closed.  The
            # terminal result remains readable, while ``_closing`` prevents
            # reopening or accepting new turns.  A later close retries only
            # the components whose completion flags are still false.
            raise CleanupError("session cleanup failed") from None
        self._finalize_result(cleanup_succeeded=not self._cleanup_failed)
        self._closed = True
        if self._on_close:
            self._on_close(self)

    async def _run_close_safely(self, outcome: ExecutionOutcome) -> None:
        task = asyncio.create_task(self._complete_close(outcome))
        try:
            await asyncio.shield(task)
        except BaseException:
            try:
                await asyncio.shield(task)
            except BaseException:
                pass
            raise

    async def cancel(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            if not self._entered and self._startup_task is None and self._terminal_outcome is None:
                raise SessionStillOpen("agent session has not been opened")
            async with self._state_lock:
                self._closing = True
                self._close_requested_outcome = ExecutionOutcome.CANCELLED
            await self._run_close_safely(ExecutionOutcome.CANCELLED)

    async def snapshot(self) -> ExecutionSnapshot:
        return self._snapshot

    @property
    def result(self) -> ExecutionResult:
        if self._terminal_result is None:
            if self._terminal_outcome is None:
                raise SessionStillOpen("agent session is still open")
            # Preserve the historical in-context view after a terminal turn.
            # This is deliberately provisional: cleanup and workspace/wire
            # evidence still belong to the close lifecycle, after which this
            # value is replaced with the recorder-finalized result.
            return ExecutionResult(
                snapshot=self._snapshot,
                turns=tuple(self._turns),
                activity_health=self._activity_health(),
                artifacts=self._workspace_artifacts,
                error=self._terminal_error,
                provenance=self._provenance,
            )
        return self._terminal_result

    @property
    def provenance(self) -> SessionProvenance | None:
        """Immutable source linkage for a portable fork/replay session."""

        return self._provenance

    @property
    def interactions(self) -> InteractionController:
        """Policy-gated permission, filesystem, terminal, and model handlers."""

        return self._interactions

    async def fork(
        self,
        request: SessionForkRequest,
        *,
        adapter_factory: Callable[
            [AgentExecutionSpec, SessionProvenance], HarnessAdapter | Awaitable[HarnessAdapter]
        ],
    ) -> "AsyncAgentSession":
        """Create a fresh child execution from this terminal session.

        The factory is mandatory: a child never reuses this session's adapter
        or its closed provider conversation.  The returned child is unopened
        and receives a new execution/session identity.
        """

        if not isinstance(request, SessionForkRequest):
            raise UnsupportedFeature("invalid fork request")
        source = self._terminal_result
        if source is None:
            raise SessionStillOpen("agent session must be terminal before forking")
        source_turn_id = request.source_turn_id
        if source_turn_id is not None and all(
            turn.snapshot.turn_id != source_turn_id for turn in source.turns
        ):
            raise UnsupportedFeature("fork source turn is not part of the source result")
        try:
            provenance = SessionProvenance(
                mode=request.mode,
                source_execution_id=source.snapshot.execution_id,
                source_session_id=(source_turn_id and next(
                    turn.snapshot.session_id for turn in source.turns if turn.snapshot.turn_id == source_turn_id
                )) or (source.turns[-1].snapshot.session_id if source.turns else self._session_id),
                source_turn_id=source_turn_id,
            )
        except Exception:
            raise UnsupportedFeature("source provenance is unavailable") from None
        try:
            child_adapter = adapter_factory(self.spec, provenance)
            if inspect.isawaitable(child_adapter):
                child_adapter = await child_adapter
        except Exception:
            raise UnsupportedFeature("fork adapter factory failed") from None
        if child_adapter is self.adapter:
            raise UnsupportedFeature("fork adapter factory must create a fresh adapter")
        child_manager = None
        if self._server_manager_factory is not None:
            try:
                child_manager = self._server_manager_factory()
            except Exception:
                raise UnsupportedFeature("fork server manager factory failed") from None
            if child_manager is self._server_manager:
                raise UnsupportedFeature("fork server manager factory must create a fresh manager")
        return type(self)(
            self.spec,
            child_adapter,
            server_manager=child_manager,
            server_manager_factory=self._server_manager_factory,
            provenance=provenance,
        )

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            async with self._state_lock:
                self._closing = True
                self._close_requested_outcome = ExecutionOutcome.COMPLETED
            await self._run_close_safely(ExecutionOutcome.COMPLETED)


__all__ = ["AdapterTurn", "AsyncAgentSession", "HarnessAdapter", "HarnessTurnError", "QueuedTurn"]
