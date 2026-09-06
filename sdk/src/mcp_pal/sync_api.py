"""Synchronous SDK configuration and capability boundary.

Execution controllers are intentionally deferred.  The kit implemented here
owns only immutable configuration, explicitly requested probes, and its own
idempotent lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable as _Callable, Iterable as _Iterable, Iterator as _Iterator, Mapping as _Mapping
import inspect as _inspect
import math as _math
from pathlib import Path as _Path
from uuid import uuid4 as _uuid4
from threading import Event as _ThreadEvent, RLock as _RLock
from typing import Any as _Any, NoReturn as _NoReturn, Protocol as _Protocol, cast as _cast

from anyio.from_thread import BlockingPortal as _BlockingPortal, start_blocking_portal as _start_blocking_portal

from .agent_session import AsyncAgentSession as _AsyncAgentSession, HarnessAdapter
from .interaction_handlers import (
    AllowlistedTerminalHandler,
    ElicitationRequest,
    ElicitationResult,
    ElicitationHandler,
    FilesystemHandler,
    FilesystemRequest,
    FilesystemResult,
    InteractionController,
    InteractionHandlers,
    InteractionReceipt,
    PermissionRequest,
    PermissionResult,
    PermissionHandler,
    SamplingRequest,
    SamplingResult,
    SamplingHandler,
    TerminalHandler,
    TerminalRequest,
    TerminalResult,
    WorkspaceFilesystemHandler,
)
from .configuration import (
    ConfigOrigin,
    ConfigSource,
    Configuration,
    ConfigurationError,
    MCPConfig,
    SDKConfig,
    load_config,
    resolve_config,
)
from .errors import (
    ExecutionNotFound as _ExecutionNotFound,
    KitClosed as _KitClosed,
    OperationCancelled as _OperationCancelled,
    UnsupportedFeature as _UnsupportedFeature,
    TraceUnavailable as _TraceUnavailable,
)
from .direct_client import (
    AsyncDirectClient as _AsyncDirectClient,
    CallToolResult,
    CompletionResult,
    DirectPrompt,
    DirectResource,
    DirectResourceTemplate,
    DirectTool,
    EmptyResult,
    GetPromptResult,
    InitializationResult,
    InputRequiredResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    PromptResult,
    ResourceReadResult,
    ToolCallResult,
    Tool,
    Resource,
    ResourceTemplate,
)
from .evaluations import EvaluationRunner as _EvaluationRunner, EvaluatorCallable as _EvaluatorCallable
from .services.probes import (
    CapabilityProbeService,
    ProbeEvidence,
    ProbeKind,
    ProbeReport,
    ProbeRequest,
    ProbeResult,
    ProbeService,
    ReadinessProbeService,
)
from .types import (
    AgentExecutionSpec as _AgentExecutionSpec,
    Capability as _Capability,
    CapabilityStatus as _CapabilityStatus,
    DirectExecutionSpec as _DirectExecutionSpec,
    ExecutionResult as _ExecutionResult,
    ExecutionSpec as _ExecutionSpec,
    ExecutionSnapshot as _ExecutionSnapshot,
    RunId as _RunId,
    EvaluationResult as _EvaluationResult,
    Readiness as _Readiness,
    ServerBinding as _ServerBinding,
    InProcessServer as _InProcessServer,
    StdioServer as _StdioServer,
    StreamableHTTPServer as _StreamableHTTPServer,
    SSEServer as _SSEServer,
    ProtocolConstraint as _ProtocolConstraint,
    TransportKind as _TransportKind,
    ServerValue as _ServerValue,
    TurnResult as _TurnResult,
    UserMessage as _UserMessage,
    CanonicalEvent as _CanonicalEvent,
    SessionForkRequest as _SessionForkRequest,
    SessionProvenance as _SessionProvenance,
    TraceResult as _TraceResult,
    RawEvidenceRef as _RawEvidenceRef,
    ExecutionId as _ExecutionId,
)
from .observability import *
from .observability import __all__ as _OBSERVABILITY_EXPORTS
from .execution_runtime import AsyncExecutionHandle as _AsyncExecutionHandle
from .storage import ExecutionStore as _ExecutionStore
from .harness.contracts import HarnessAdapterRegistry as _HarnessAdapterRegistry
from ._default_store import make_default_run_id as _make_default_run_id, make_default_store as _make_default_store


_CURRENT_MCP_PROTOCOL = "2025-11-25"
_DIRECT_SERVER_TYPES = (_InProcessServer, _StdioServer, _StreamableHTTPServer, _SSEServer)


def _adapt_callback(callback: _Any) -> _Any:
    """Adapt callbacks and keep callback failures value-free.

    The official client logs notification callback exceptions, including their
    traceback.  User callbacks may contain secrets in exception messages, so
    the exception crossing that boundary must be generic.  ``BaseException``
    is intentionally not caught: cancellation and process-control exceptions
    retain their normal semantics.
    """

    if callback is None:
        return callback

    async def invoke(*args: _Any, **kwargs: _Any) -> _Any:
        try:
            result = callback(*args, **kwargs)
            if _inspect.isawaitable(result):
                result = await result
        except Exception:
            # Raise after leaving the ``except`` block so the original
            # exception is not retained as ``__context__`` either.
            pass
        else:
            return result
        raise RuntimeError("MCP callback failed")

    return invoke


_DIRECT_METHODS = frozenset(
    {
        "initialize", "list_tools", "list_all_tools", "list_resources", "list_all_resources",
        "list_resource_templates", "list_all_resource_templates", "list_prompts", "list_all_prompts",
        "read_resource", "get_prompt", "call_tool", "complete", "subscribe_resource",
        "unsubscribe_resource", "ping", "set_logging_level", "send_progress_notification",
        "send_notification", "send_roots_list_changed", "register_callbacks",
    }
)


def _runtime_server_bindings(runtime_servers: _Iterable[_Any]) -> tuple[_ServerBinding, ...]:
    """Normalize loopback registrations without changing the frozen spec."""

    bindings: list[_ServerBinding] = []
    for value in runtime_servers:
        if isinstance(value, _InProcessServer):
            bindings.append(_ServerBinding(server=value, alias=value.name))
        elif isinstance(value, _ServerBinding) and isinstance(value.server, _InProcessServer):
            bindings.append(value)
        else:
            raise TypeError("runtime_servers accepts only InProcessServer or its ServerBinding")
    return tuple(bindings)


class _PortalRuntime:
    """Async state owned exclusively by the AnyIO portal thread."""

    def __init__(self, config: SDKConfig, probe_timeout_seconds: float, probe_output_limit: int, store: _ExecutionStore | None = None, embedded_worker: bool = True, adapter_registry: _HarnessAdapterRegistry | None = None, run_id: _RunId | str | None = None) -> None:
        from .async_api import AsyncMCPTestKit

        self.kit = AsyncMCPTestKit(
            config,
            probe_timeout_seconds=probe_timeout_seconds,
            probe_output_limit=probe_output_limit,
            store=store,
            embedded_worker=embedded_worker,
            adapter_registry=adapter_registry,
            run_id=run_id,
        )
        self.clients: dict[int, _AsyncDirectClient] = {}
        self.sessions: dict[int, _AsyncAgentSession] = {}
        self.closed_sessions: dict[int, _AsyncAgentSession] = {}
        self.executions: dict[int, _AsyncExecutionHandle] = {}
        self.event_iters: dict[int, _Any] = {}
        self.closing: set[int] = set()
        self._next_client = 0
        self._next_session = 0
        self._next_execution = 0
        self._closed_results: dict[int, _ExecutionResult] = {}

    def create_direct(self, server: _ServerValue | _ServerBinding, options: _Mapping[str, _Any]) -> int:
        client = self.kit.direct(server, **dict(options))
        handle = self._next_client
        self._next_client += 1
        self.clients[handle] = client
        return handle

    def client(self, handle: int) -> _AsyncDirectClient:
        try:
            return self.clients[handle]
        except KeyError:
            raise RuntimeError("direct client is closed") from None

    async def enter(self, handle: int) -> None:
        await self.client(handle).__aenter__()

    async def invoke(self, handle: int, name: str, args: tuple[_Any, ...], kwargs: dict[str, _Any]) -> _Any:
        if handle in self.closing:
            raise _OperationCancelled(
                "synchronous direct operation cancelled by client close",
                details={"operation": name, "cause": "client_close"},
            )
        value = getattr(self.client(handle), name)
        try:
            if name in _DIRECT_METHODS:
                return await value(*args, **kwargs)
            return value
        except _OperationCancelled:
            raise
        except BaseException:
            # The close marker is set in this same portal thread before the
            # teardown await. An error observed after that marker is caused by
            # the sync caller's close, while earlier transport failures retain
            # their authoritative type.
            if handle in self.closing:
                raise _OperationCancelled(
                    "synchronous direct operation cancelled by client close",
                    details={"operation": name, "cause": "client_close"},
                ) from None
            raise

    async def close_client(self, handle: int) -> tuple[_Any, _Any, _Any, _Any]:
        client = self.clients.get(handle)
        if client is not None:
            self.closing.add(handle)
            try:
                await client.aclose()
                return (
                    client.final_trace,
                    client.trace,
                    client.initialization,
                    getattr(client, "transport_evidence", None),
                )
            finally:
                self.clients.pop(handle, None)
                self.closing.discard(handle)
        return None, None, None, None

    def create_session(
        self,
        spec: _AgentExecutionSpec,
        adapter: HarnessAdapter | None = None,
        runtime_servers: _Iterable[_Any] = (),
        interaction_handlers: InteractionHandlers | None = None,
    ) -> int:
        session = self.kit.agent_session(
            spec,
            adapter=adapter,
            runtime_servers=runtime_servers,
            interaction_handlers=interaction_handlers,
        )
        handle = self._next_session
        self._next_session += 1
        self.sessions[handle] = session
        return handle

    async def fork_session(
        self,
        handle: int,
        request: _SessionForkRequest,
        adapter_factory: _Callable[..., _Any],
    ) -> tuple[int, _AgentExecutionSpec]:
        child = await self.session(handle).fork(request, adapter_factory=adapter_factory)
        child_handle = self._next_session
        self._next_session += 1
        self.sessions[child_handle] = child
        return child_handle, child.spec

    def session(self, handle: int) -> _AsyncAgentSession:
        try:
            return self.sessions[handle]
        except KeyError:
            try:
                return self.closed_sessions[handle]
            except KeyError:
                raise RuntimeError("agent session is closed") from None

    def session_interactions(self, handle: int) -> InteractionController:
        """Return the policy controller owned by a session's portal task."""

        return self.session(handle).interactions

    async def enter_session(self, handle: int) -> None:
        await self.session(handle).__aenter__()

    async def invoke_session(self, handle: int, name: str, args: tuple[_Any, ...], kwargs: dict[str, _Any]) -> _Any:
        value = getattr(self.session(handle), name)
        result = value(*args, **kwargs) if callable(value) else value
        if hasattr(result, "__await__"):
            return await result
        return result

    async def close_session(self, handle: int) -> _Any:
        session = self.sessions.get(handle)
        if session is None:
            session = self.closed_sessions.get(handle)
            return session.result if session is not None else None
        try:
            await session.aclose()
            self.sessions.pop(handle, None)
            self.closed_sessions[handle] = session
            return session.result
        finally:
            if handle in self.closed_sessions:
                self.kit._session_closed(session)

    async def queued_result(self, queued: _Any) -> _TurnResult:
        return _cast(_TurnResult, await queued.result())

    async def close(self) -> dict[int, _ExecutionResult]:
        await self.kit._execution_controller.close()
        for identifier, handle in tuple(self.executions.items()):
            try:
                self._closed_results[identifier] = await handle.result()
            except BaseException:
                # A terminal result is best effort during owner teardown; the
                # controller still owns cancellation and cleanup below.
                continue
        await self.kit.aclose()
        self.clients.clear()
        self.sessions.clear()
        self.closed_sessions.clear()
        self.executions.clear()
        self.event_iters.clear()
        self.closing.clear()
        return dict(self._closed_results)

    def create_execution(self, spec: _ExecutionSpec) -> int:
        handle = self.kit.submit(spec)
        identifier = self._next_execution
        self._next_execution += 1
        self.executions[identifier] = handle
        return identifier

    def execution(self, identifier: int) -> _AsyncExecutionHandle:
        try:
            return self.executions[identifier]
        except KeyError:
            raise RuntimeError("execution handle is closed") from None

    async def execution_result(self, identifier: int, timeout: float | None) -> _ExecutionResult:
        return await self.execution(identifier).result(timeout)

    async def execution_snapshot(self, identifier: int) -> _ExecutionSnapshot:
        return await self.execution(identifier).snapshot()

    async def execution_cancel(self, identifier: int) -> None:
        await self.execution(identifier).cancel()

    def execution_info(self, identifier: int) -> tuple[_Any, _ExecutionSpec]:
        handle = self.execution(identifier)
        return handle.execution_id, handle.spec

    async def next_execution_event(self, identifier: int, after_sequence: int) -> _CanonicalEvent | None:
        iterator = self.event_iters.get(identifier)
        if iterator is None:
            iterator = self.execution(identifier).events(after_sequence=after_sequence)
            self.event_iters[identifier] = iterator
        try:
            return await iterator.__anext__()
        except StopAsyncIteration:
            self.event_iters.pop(identifier, None)
            return None

    async def close_execution_events(self, identifier: int) -> None:
        iterator = self.event_iters.pop(identifier, None)
        if iterator is not None:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()

    def execution_callback(self, identifier: int, callback: _Callable[[_Any], _Any]) -> _Callable[[], None]:
        return self.execution(identifier).on_event(callback)


class _SyncPortal:
    def __init__(self, config: SDKConfig, probe_timeout_seconds: float, probe_output_limit: int, store: _ExecutionStore | None = None, embedded_worker: bool = True, adapter_registry: _HarnessAdapterRegistry | None = None, run_id: _RunId | str | None = None) -> None:
        self._lock = _RLock()
        self._context = _start_blocking_portal()
        try:
            self._portal: _BlockingPortal = self._context.__enter__()
            self._runtime = self._portal.call(
                _PortalRuntime,
                config,
                probe_timeout_seconds,
                probe_output_limit,
                store,
                embedded_worker,
                adapter_registry,
                run_id,
            )
        except BaseException:
            self._context.__exit__(None, None, None)
            raise
        self._closed = False
        self._closing = False
        self._close_done = _ThreadEvent()
        self._close_error: BaseException | None = None
        self._closed_results: dict[int, _ExecutionResult] = {}

    def call(self, function: _Callable[..., _Any], *args: _Any, **kwargs: _Any) -> _Any:
        with self._lock:
            if self._closed:
                raise RuntimeError("synchronous portal is closed")
            portal = self._portal
        return portal.call(function, *args, **kwargs)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                done = self._close_done
                owner = False
                error = self._close_error
            elif self._closing:
                done = self._close_done
                owner = False
                error = self._close_error
            else:
                done = self._close_done
                owner = True
                error = None
                self._close_error = None
                self._closing = True
                self._close_done.clear()
        if not owner:
            done.wait()
            with self._lock:
                error = self._close_error
            if error is not None:
                raise error
            return
        try:
            results = self._portal.call(self._runtime.close)
            if isinstance(results, dict):
                self._closed_results = {
                    int(identifier): result
                    for identifier, result in results.items()
                }
        except BaseException as exc:
            with self._lock:
                self._close_error = exc
                self._closing = False
                self._close_done.set()
            raise
        else:
            try:
                self._context.__exit__(None, None, None)
            finally:
                with self._lock:
                    self._closed = True
                    self._closing = False
                    self._close_done.set()

    def cached_execution_result(self, identifier: int) -> _ExecutionResult | None:
        with self._lock:
            result = self._closed_results.get(identifier)
            return result.model_copy() if result is not None else None


class DirectClient:
    """Synchronous proxy whose async protocol state remains in a portal thread."""

    def __init__(self, portal: _SyncPortal, server: _ServerValue | _ServerBinding, options: _Mapping[str, _Any]) -> None:
        self._portal = portal
        self._handle = _cast(int, portal.call(portal._runtime.create_direct, server, options))
        self._closed = False
        self._closing = False
        self._state_lock = _RLock()
        self._close_done = _ThreadEvent()
        self._close_error: BaseException | None = None
        self._snapshots: dict[str, _Any] = {}

    def _invoke(self, name: str, *args: _Any, **kwargs: _Any) -> _Any:
        with self._state_lock:
            if self._closed:
                if name in self._snapshots:
                    return self._snapshots[name]
                raise RuntimeError("direct client is closed")
            if self._closing:
                raise _OperationCancelled(
                    "synchronous direct operation cancelled by client close",
                    details={"operation": name, "cause": "client_close"},
                )
        return self._portal.call(self._portal._runtime.invoke, self._handle, name, args, kwargs)

    def __enter__(self) -> "DirectClient":
        with self._state_lock:
            if self._closed or self._closing:
                raise RuntimeError("direct client is closed")
        self._portal.call(self._portal._runtime.enter, self._handle)
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                error = self._close_error
                done = None
            elif self._closing:
                error = None
                done = self._close_done
            else:
                self._closing = True
                error = None
                done = None
        if done is not None:
            done.wait()
            if self._close_error is not None:
                raise self._close_error
            return
        if self._closed:
            if error is not None:
                raise error
            return
        try:
            final_trace, trace, initialization, transport_evidence = self._portal.call(
                self._portal._runtime.close_client, self._handle
            )
            self._snapshots.update(
                final_trace=final_trace,
                trace=trace,
                initialization=initialization,
                transport_evidence=transport_evidence,
            )
        except BaseException as exc:
            with self._state_lock:
                self._close_error = exc
            raise
        finally:
            with self._state_lock:
                self._closed = True
                self._closing = False
                self._close_done.set()

    @property
    def initialization(self) -> InitializationResult | None:
        return _cast(InitializationResult | None, self._invoke("initialization"))

    @property
    def timeout(self) -> float:
        return _cast(float, self._invoke("timeout"))

    @property
    def trace(self) -> _Any:
        return self._invoke("trace")

    @property
    def final_trace(self) -> _Any:
        return self._invoke("final_trace")

    @property
    def transport_evidence(self) -> _Any:
        return self._invoke("transport_evidence")

    def __getattr__(self, name: str) -> _Any:
        if name in _DIRECT_METHODS:
            return lambda *args: self._invoke(name, *args)
        raise AttributeError(name)

    def initialize(self) -> InitializationResult:
        return _cast(InitializationResult, self._invoke("initialize"))

    def list_tools(self, *, cursor: str | None = None) -> ListToolsResult:
        return _cast(ListToolsResult, self._invoke("list_tools", cursor=cursor))

    def list_all_tools(self) -> tuple[Tool, ...]:
        return _cast(tuple[Tool, ...], self._invoke("list_all_tools"))

    def list_resources(self, *, cursor: str | None = None) -> ListResourcesResult:
        return _cast(ListResourcesResult, self._invoke("list_resources", cursor=cursor))

    def list_all_resources(self) -> tuple[Resource, ...]:
        return _cast(tuple[Resource, ...], self._invoke("list_all_resources"))

    def list_resource_templates(self, *, cursor: str | None = None) -> ListResourceTemplatesResult:
        return _cast(ListResourceTemplatesResult, self._invoke("list_resource_templates", cursor=cursor))

    def list_all_resource_templates(self) -> tuple[ResourceTemplate, ...]:
        return _cast(tuple[ResourceTemplate, ...], self._invoke("list_all_resource_templates"))

    def list_prompts(self, *, cursor: str | None = None) -> ListPromptsResult:
        return _cast(ListPromptsResult, self._invoke("list_prompts", cursor=cursor))

    def list_all_prompts(self) -> tuple[DirectPrompt, ...]:
        return _cast(tuple[DirectPrompt, ...], self._invoke("list_all_prompts"))

    def read_resource(self, uri: str, **kwargs: _Any) -> ResourceReadResult | InputRequiredResult:
        return _cast(ResourceReadResult | InputRequiredResult, self._invoke("read_resource", uri, **kwargs))

    def get_prompt(self, name: str, arguments: _Mapping[str, str] | None = None, **kwargs: _Any) -> PromptResult | InputRequiredResult:
        return _cast(PromptResult | InputRequiredResult, self._invoke("get_prompt", name, arguments, **kwargs))

    def call_tool(self, name: str, arguments: _Mapping[str, _Any] | None = None, **kwargs: _Any) -> ToolCallResult | InputRequiredResult:
        return _cast(ToolCallResult | InputRequiredResult, self._invoke("call_tool", name, arguments, **kwargs))

    def complete(self, reference: _Any, argument: _Mapping[str, str], context_arguments: _Mapping[str, str] | None = None) -> CompletionResult:
        return _cast(CompletionResult, self._invoke("complete", reference, argument, context_arguments))

    def subscribe_resource(self, uri: str, *, meta: _Any = None) -> EmptyResult:
        return _cast(EmptyResult, self._invoke("subscribe_resource", uri, meta=meta))

    def unsubscribe_resource(self, uri: str, *, meta: _Any = None) -> EmptyResult:
        return _cast(EmptyResult, self._invoke("unsubscribe_resource", uri, meta=meta))

    def ping(self, *, meta: _Any = None) -> EmptyResult:
        return _cast(EmptyResult, self._invoke("ping", meta=meta))

    def set_logging_level(self, level: str, *, meta: _Any = None) -> EmptyResult:
        return _cast(EmptyResult, self._invoke("set_logging_level", level, meta=meta))

    def send_progress_notification(self, progress_token: str | int, progress: float, total: float | None = None, message: str | None = None, *, meta: _Any = None) -> None:
        self._invoke("send_progress_notification", progress_token, progress, total, message, meta=meta)

    def send_notification(self, notification: _Any) -> None:
        self._invoke("send_notification", notification)

    def send_roots_list_changed(self) -> None:
        self._invoke("send_roots_list_changed")

    def register_callbacks(self, **callbacks: _Any) -> _NoReturn:
        self._invoke("register_callbacks", **callbacks)
        raise AssertionError("callback registration unexpectedly returned")


class ExecutionHandle:
    """Blocking twin of :class:`AsyncExecutionHandle` with no async leakage."""

    def __init__(self, portal: _SyncPortal, identifier: int) -> None:
        self._portal = portal
        self._identifier = identifier
        self._closed = False

    @property
    def execution_id(self) -> _Any:
        return self._portal.call(self._portal._runtime.execution_info, self._identifier)[0]

    @property
    def spec(self) -> _ExecutionSpec:
        return _cast(_ExecutionSpec, self._portal.call(self._portal._runtime.execution_info, self._identifier)[1])

    @property
    def submitted_spec(self) -> _ExecutionSpec:
        return self.spec

    def snapshot(self) -> _ExecutionSnapshot:
        return _cast(_ExecutionSnapshot, self._portal.call(self._portal._runtime.execution_snapshot, self._identifier))

    def result(self, timeout: float | None = None) -> _ExecutionResult:
        try:
            return _cast(_ExecutionResult, self._portal.call(self._portal._runtime.execution_result, self._identifier, timeout))
        except RuntimeError:
            result = self._portal.cached_execution_result(self._identifier)
            if result is None:
                raise
            return result

    def cancel(self) -> None:
        self._portal.call(self._portal._runtime.execution_cancel, self._identifier)

    def events(self, *, after_sequence: int = -1) -> _Iterator[_CanonicalEvent]:
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        cursor = after_sequence
        try:
            while True:
                event = self._portal.call(
                    self._portal._runtime.next_execution_event,
                    self._identifier,
                    cursor,
                )
                if event is None:
                    return
                typed = _cast(_CanonicalEvent, event)
                if typed.sequence <= cursor:
                    continue
                cursor = typed.sequence
                yield typed
                if typed.kind.value == "execution.finished":
                    return
        finally:
            try:
                self._portal.call(self._portal._runtime.close_execution_events, self._identifier)
            except (RuntimeError, _KitClosed):
                pass

    def on_event(self, callback: _Callable[[_CanonicalEvent], _Any]) -> _Callable[[], None]:
        return _cast(
            _Callable[[], None],
            self._portal.call(self._portal._runtime.execution_callback, self._identifier, callback),
        )


class AgentSession:
    """Blocking proxy for the async session state machine."""

    def __init__(
        self,
        portal: _SyncPortal,
        spec: _AgentExecutionSpec,
        adapter: HarnessAdapter | None = None,
        runtime_servers: _Iterable[_Any] = (),
        interaction_handlers: InteractionHandlers | None = None,
        _handle: int | None = None,
    ) -> None:
        self._portal = portal
        self._handle = _handle if _handle is not None else _cast(
            int,
            portal.call(
                portal._runtime.create_session,
                spec,
                adapter,
                tuple(runtime_servers),
                interaction_handlers,
            ),
        )
        self._closed = False
        self._closing = False
        self._state_lock = _RLock()
        self._close_done = _ThreadEvent()
        self._close_error: BaseException | None = None
        self._result: _ExecutionResult | None = None

    def _invoke(self, name: str, *args: _Any, **kwargs: _Any) -> _Any:
        with self._state_lock:
            if self._closed:
                if name == "result" and self._result is not None:
                    return self._result
                raise _KitClosed("agent session is closed")
            if self._closing:
                raise _OperationCancelled("agent session is closing")
        return self._portal.call(self._portal._runtime.invoke_session, self._handle, name, args, kwargs)

    def __enter__(self) -> "AgentSession":
        self._invoke("__aenter__")
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def send(self, message: str | _UserMessage, *, timeout: float | None = None, metadata: dict[str, object] | None = None) -> _TurnResult:
        return _cast(_TurnResult, self._invoke("send", message, timeout=timeout, metadata=metadata))

    def enqueue_turn(self, message: str | _UserMessage, *, timeout: float | None = None, metadata: dict[str, object] | None = None) -> _Any:
        queued = self._invoke("enqueue_turn", message, timeout=timeout, metadata=metadata)
        return _SyncQueuedTurn(self._portal, queued)

    def snapshot(self) -> _ExecutionSnapshot:
        return _cast(_ExecutionSnapshot, self._invoke("snapshot"))

    @property
    def provenance(self) -> _SessionProvenance | None:
        with self._state_lock:
            if self._closed and self._result is not None:
                return self._result.provenance
        return _cast(_SessionProvenance | None, self._invoke("provenance"))

    @property
    def interactions(self) -> InteractionController:
        """Policy-gated handlers owned by this session's portal task."""

        return _cast(
            InteractionController,
            self._portal.call(self._portal._runtime.session_interactions, self._handle),
        )

    @property
    def result(self) -> _ExecutionResult:
        return _cast(_ExecutionResult, self._invoke("result"))

    def cancel(self) -> None:
        self._invoke("cancel")

    def fork(
        self,
        request: _SessionForkRequest,
        *,
        adapter_factory: _Callable[..., _Any],
    ) -> "AgentSession":
        handle, child_spec = _cast(
            tuple[int, _AgentExecutionSpec],
            self._portal.call(self._portal._runtime.fork_session, self._handle, request, adapter_factory),
        )
        return AgentSession(self._portal, child_spec, _handle=handle)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                if self._close_error is not None:
                    raise self._close_error
                return
            if self._closing:
                done = self._close_done
                owner = False
            else:
                self._closing = True
                self._close_error = None
                done = self._close_done
                owner = True
        if not owner:
            done.wait()
            if self._close_error is not None:
                raise self._close_error
            return
        try:
            self._result = _cast(_ExecutionResult, self._portal.call(self._portal._runtime.close_session, self._handle))
        except BaseException as exc:
            with self._state_lock:
                self._close_error = exc
            raise
        else:
            with self._state_lock:
                self._closed = True
        finally:
            with self._state_lock:
                self._closing = False
                self._close_done.set()


class _SyncQueuedTurn:
    """Blocking view of an async FIFO turn handle."""

    def __init__(self, portal: _SyncPortal, queued: _Any) -> None:
        self._portal = portal
        self._queued = queued
        self.turn_id = queued.turn_id

    def result(self) -> _TurnResult:
        return _cast(_TurnResult, self._portal.call(self._portal._runtime.queued_result, self._queued))

    def wait(self) -> _TurnResult:
        return self.result()


def _baseline_report(config: SDKConfig, probes: CapabilityProbeService) -> ProbeReport:
    configuration = ProbeResult(
        capability=_Capability(
            name="configuration",
            status=_CapabilityStatus.READY,
            protocol_version=None if config.protocol_revision == "auto" else config.protocol_revision,
        ),
        evidence=ProbeEvidence(
            kind=ProbeKind.CONFIGURATION,
            target="sdk-config",
            details=config.model_dump(mode="json"),
        ),
    )
    memory = probes.probe_storage("memory")
    results = (configuration, memory)
    capabilities = tuple(result.capability for result in results)
    return ProbeReport(readiness=_Readiness(ready=True, capabilities=capabilities), results=results)


class MCPTestKit:
    """Lifecycle-safe synchronous configuration and capability shell."""

    def __init__(
        self,
        config: SDKConfig | _Mapping[str, _Any] | None = None,
        *,
        env: _Mapping[str, str] | None = None,
        cwd: str | _Path | None = None,
        probe_timeout_seconds: float = 5.0,
        probe_output_limit: int = 64 * 1024,
        store: _ExecutionStore | None = None,
        embedded_worker: bool = True,
        adapter_registry: _HarnessAdapterRegistry | None = None,
        run_id: _RunId | str | None = None,
    ) -> None:
        self._state_lock = _RLock()
        self._closed = False
        scoped_run_id = run_id or _make_default_run_id()
        self._run_id = scoped_run_id if isinstance(scoped_run_id, _RunId) else _RunId(scoped_run_id or f"run-{_uuid4().hex}")
        self._context_depth = 0
        self._closing = False
        self._close_done = _ThreadEvent()
        self._close_error: BaseException | None = None
        self._portal: _SyncPortal | None = None
        self._active_direct: set[DirectClient] = set()
        self._active_sessions: set[AgentSession] = set()
        self._evaluations = _EvaluationRunner()
        self._probe_timeout_seconds = probe_timeout_seconds
        self._probe_output_limit = probe_output_limit
        self.config = config if isinstance(config, SDKConfig) else resolve_config(config, env=env, cwd=cwd)
        self._owns_store = False
        if store is None:
            scoped_store = _make_default_store()
            if scoped_store is not None:
                store = scoped_store
                self._owns_store = True
        self._store = store
        # Evaluation persistence follows the selected execution store.  Keep
        # the runner runtime-only registry, while detached evaluations remain
        # kit-local inside the runner.
        self._evaluations = _EvaluationRunner(durable_store=store)
        self._embedded_worker = embedded_worker
        self._adapter_registry = adapter_registry
        self._probes = CapabilityProbeService(
            timeout_seconds=probe_timeout_seconds,
            output_limit=probe_output_limit,
        )
        self._probes._set_lifecycle_guard(self._ensure_open)

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise _KitClosed("MCPTestKit is closed")

    @property
    def probes(self) -> CapabilityProbeService:
        """Synchronous capability namespace owned by this kit."""

        self._ensure_open()
        return self._probes

    @property
    def store(self) -> _ExecutionStore | None:
        """The optional execution store configured on this kit.

        Application composition layers may use identity matching when they
        inject a kit and store into separate typed services.  Returning the
        object without a wrapper keeps that check explicit and does not expose
        storage implementation details.
        """

        return self._store

    @property
    def run_id(self) -> _RunId:
        return self._run_id

    def _with_run_id(self, spec: _ExecutionSpec) -> _ExecutionSpec:
        return spec

    def get_trace(self, execution_id: _ExecutionId | str) -> _TraceResult:
        """Return the finalized canonical trace for an execution."""

        self._ensure_open()
        if self._store is None:
            raise _TraceUnavailable("MCPTestKit has no execution store")
        trace = self._store.get_trace(execution_id)
        if trace is None:
            raise _ExecutionNotFound(f"execution {execution_id!s} was not found")
        return trace

    def get_trace_view(self, execution_id: _ExecutionId | str) -> TraceView:
        """Return the finalized typed trace view for an execution."""

        self._ensure_open()
        if self._store is None:
            raise _TraceUnavailable("MCPTestKit has no execution store")
        view = self._store.get_trace_view(execution_id)
        if view is None:
            raise _ExecutionNotFound(f"execution {execution_id!s} was not found")
        return view

    def read_raw_evidence(
        self, reference: _RawEvidenceRef, *, max_bytes: int = 1_048_576
    ) -> RawEvidence:
        """Read bounded, redacted raw evidence by its durable reference."""

        self._ensure_open()
        if self._store is None:
            raise _TraceUnavailable("MCPTestKit has no execution store")
        return self._store.read_raw_evidence(reference, max_bytes=max_bytes)

    def __enter__(self) -> "MCPTestKit":
        with self._state_lock:
            if self._closed:
                raise _KitClosed("MCPTestKit is closed")
            self._context_depth += 1
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        with self._state_lock:
            self._context_depth = max(0, self._context_depth - 1)
            should_close = self._context_depth == 0
        if should_close:
            self.close()

    def close(self) -> None:
        """Close the shell; repeated calls are intentionally harmless."""

        with self._state_lock:
            if self._closed and not self._active_direct and not self._active_sessions and self._portal is None:
                return
            if self._closing:
                done = self._close_done
                owner = False
            else:
                self._closing = True
                self._close_error = None
                self._close_done.clear()
                done = self._close_done
                owner = True
            self._closed = True
            clients = tuple(self._active_direct)
            sessions = tuple(self._active_sessions)
            portal = self._portal
        if not owner:
            done.wait()
            if self._close_error is not None:
                raise self._close_error
            return
        failures: list[BaseException] = []
        for client in clients:
            try:
                client.close()
                with self._state_lock:
                    self._active_direct.discard(client)
            except BaseException as exc:
                failures.append(exc)
        for session in sessions:
            try:
                session.close()
                with self._state_lock:
                    self._active_sessions.discard(session)
            except BaseException as exc:
                failures.append(exc)
        if portal is not None and not failures:
            try:
                portal.close()
                with self._state_lock:
                    self._portal = None
            except BaseException as exc:
                failures.append(exc)
        owned_store = self._store if self._owns_store else None
        self._owns_store = False
        if owned_store is not None:
            try:
                close = getattr(owned_store, "close", None)
                if callable(close):
                    close()
            except BaseException as exc:
                failures.append(exc)
        if failures:
            with self._state_lock:
                self._close_error = failures[0]
                self._closing = False
                self._close_done.set()
            raise failures[0]
        with self._state_lock:
            self._closing = False
            self._close_done.set()

    def capabilities(self, requests: _Iterable[ProbeRequest] = ()) -> ProbeReport:
        """Return the baseline or exactly the explicitly requested probes."""

        self._ensure_open()
        requested = tuple(requests)
        if not requested:
            return _baseline_report(self.config, self.probes)
        return self.probes.probe_requested(requested)

    def register_evaluator(self, name: str, evaluator: _EvaluatorCallable) -> None:
        """Register an evaluator callback by its serializable name."""

        self._ensure_open()
        self._evaluations.register(name, evaluator)

    def evaluate(
        self,
        subject: _Any,
        evaluator: str | _EvaluatorCallable,
        *,
        required: bool = False,
        goal: str | None = None,
        trace: _Any = None,
        artifacts: _Any = (),
        metadata: _Mapping[str, str | int | float | bool | None] | None = None,
        execution_id: _Any = None,
        turn_id: _Any = None,
        case_id: str | None = None,
    ) -> _EvaluationResult:
        """Run and persist one evaluation without changing lifecycle."""

        self._ensure_open()
        return self._evaluations.evaluate(
            subject,
            evaluator,
            required=required,
            goal=goal,
            trace=trace,
            artifacts=artifacts,
            metadata=metadata,
            execution_id=execution_id,
            turn_id=turn_id,
            case_id=case_id,
        )

    def evaluation_results(self) -> tuple[_EvaluationResult, ...]:
        self._ensure_open()
        return self._evaluations.results()

    def _unsupported(self, operation: str) -> _NoReturn:
        self._ensure_open()
        raise _UnsupportedFeature(f"{operation} is not implemented in the configuration milestone")

    def run(self, spec: _DirectExecutionSpec | _AgentExecutionSpec) -> _ExecutionResult:
        if not isinstance(spec, (_DirectExecutionSpec, _AgentExecutionSpec)):
            self._unsupported("run")
        return self.submit(spec).result()

    def submit(self, spec: _ExecutionSpec) -> ExecutionHandle:
        spec = _cast(_AgentExecutionSpec, self._with_run_id(spec))
        if not isinstance(spec, (_DirectExecutionSpec, _AgentExecutionSpec)):
            self._unsupported("submit")
        self._ensure_open()
        with self._state_lock:
            if self._closed:
                raise _KitClosed("MCPTestKit is closed")
            portal = self._portal
            new_portal = portal is None
            if portal is None:
                portal = _SyncPortal(self.config, self._probe_timeout_seconds, self._probe_output_limit, self._store, self._embedded_worker, self._adapter_registry, self._run_id)
                self._portal = portal
            try:
                identifier = _cast(int, portal.call(portal._runtime.create_execution, spec))
            except BaseException:
                if new_portal and self._portal is portal:
                    self._portal = None
                    portal.close()
                raise
            return ExecutionHandle(portal, identifier)

    def direct(
        self,
        server: _ServerValue | _ServerBinding,
        *,
        protocol: object | None = None,
        timeout: float | None = None,
        validate_schemas: bool = False,
        secret_resolver: _Any = None,
        bearer_token: _Any = None,
        auth: _Any = None,
        for_agent: bool = False,
        resolve_host: _Any = None,
        raise_server_exceptions: bool = True,
        sampling_callback: _Any = None,
        elicitation_callback: _Any = None,
        list_roots_callback: _Any = None,
        logging_callback: _Any = None,
        message_handler: _Any = None,
        client_info: _Any = None,
        log_level: _Any = None,
        sampling_capabilities: _Any = None,
        result_claims: _Any = None,
        extensions: _Mapping[str, _Mapping[str, _Any]] | None = None,
        notification_bindings: _Iterable[_Any] | None = None,
        dispatcher: _Any = None,
        trace_bridge: _Any = None,
        trace_owner: bool = True,
        workspace_root: str | None = None,
    ) -> DirectClient:
        selected = server.server if hasattr(server, "server") else server
        self._validate_direct_preflight(selected, protocol, timeout)
        options = {
            key: value
            for key, value in {
                "protocol": protocol,
                "timeout": timeout,
                "validate_schemas": validate_schemas,
                "secret_resolver": secret_resolver,
                "bearer_token": bearer_token,
                "auth": auth,
                "for_agent": for_agent,
                "resolve_host": resolve_host,
                "raise_server_exceptions": raise_server_exceptions,
                "sampling_callback": _adapt_callback(sampling_callback),
                "elicitation_callback": _adapt_callback(elicitation_callback),
                "list_roots_callback": _adapt_callback(list_roots_callback),
                "logging_callback": _adapt_callback(logging_callback),
                "message_handler": _adapt_callback(message_handler),
                "client_info": client_info,
                "log_level": log_level,
                "sampling_capabilities": sampling_capabilities,
                "result_claims": result_claims,
                "extensions": extensions,
                "notification_bindings": notification_bindings,
                "dispatcher": dispatcher,
                "trace_bridge": trace_bridge,
                "trace_owner": trace_owner,
                "workspace_root": workspace_root,
            }.items()
            if value is not None
        }
        # ``protocol`` is validated by the authoritative async kit.  Keep the
        # selected server value and all options inside the portal runtime.
        with self._state_lock:
            if self._closed:
                raise _KitClosed("MCPTestKit is closed")
            portal = self._portal
            new_portal = portal is None
            if portal is None:
                portal = _SyncPortal(self.config, self._probe_timeout_seconds, self._probe_output_limit, self._store, self._embedded_worker, self._adapter_registry, self._run_id)
                self._portal = portal
            try:
                binding = server if isinstance(server, _ServerBinding) else _ServerBinding(server=selected)
                client = DirectClient(portal, binding, options)
            except BaseException:
                if new_portal:
                    self._portal = None
                    portal.close()
                raise
            self._active_direct.add(client)
            return client

    def _validate_direct_preflight(self, selected: _Any, protocol: object | None, timeout: float | None) -> None:
        if not isinstance(selected, _DIRECT_SERVER_TYPES):
            raise _UnsupportedFeature("direct server profiles require runtime resolution")
        if timeout is not None and (not _math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be positive and finite")
        requested_revision: str | None = self.config.protocol_revision
        requested_transport: _TransportKind | None = None
        if protocol is None:
            pass
        elif isinstance(protocol, _ProtocolConstraint):
            requested_revision = protocol.revision
            requested_transport = protocol.transport
        elif isinstance(protocol, str):
            requested_revision = protocol
        else:
            raise ValueError("protocol must be a revision string or ProtocolConstraint")
        if requested_revision not in {None, "", "auto", _CURRENT_MCP_PROTOCOL}:
            raise _UnsupportedFeature("explicit MCP protocol revision is not supported by the official client")
        actual_transport = {
            _InProcessServer: _TransportKind.IN_PROCESS,
            _StdioServer: _TransportKind.STDIO,
            _StreamableHTTPServer: _TransportKind.STREAMABLE_HTTP,
            _SSEServer: _TransportKind.SSE,
        }[type(selected)]
        if requested_transport is not None and requested_transport is not actual_transport:
            raise _UnsupportedFeature("requested transport is not supported by this direct binding")

    def agent_session(
        self,
        spec: _AgentExecutionSpec,
        *,
        adapter: HarnessAdapter | None = None,
        runtime_servers: _Iterable[_Any] = (),
        interaction_handlers: InteractionHandlers | None = None,
    ) -> AgentSession:
        self._ensure_open()
        if not isinstance(spec, _AgentExecutionSpec):
            self._unsupported("agent_session")
        spec = _cast(_AgentExecutionSpec, self._with_run_id(spec))
        with self._state_lock:
            if self._closed:
                raise _KitClosed("MCPTestKit is closed")
            portal = self._portal
            if portal is None:
                portal = _SyncPortal(self.config, self._probe_timeout_seconds, self._probe_output_limit, self._store, self._embedded_worker, self._adapter_registry, self._run_id)
                self._portal = portal
            try:
                session = AgentSession(portal, spec, adapter, runtime_servers, interaction_handlers)
            except BaseException:
                if self._portal is portal and not self._active_direct:
                    self._portal = None
                    portal.close()
                raise
            self._active_sessions.add(session)
            return session


__all__ = [
    "AgentSession",
    "HarnessAdapter",
    "CapabilityProbeService",
    "CallToolResult",
    "CompletionResult",
    "ConfigOrigin",
    "ConfigSource",
    "Configuration",
    "ConfigurationError",
    "DirectClient",
    "ExecutionHandle",
    "DirectPrompt",
    "DirectResource",
    "DirectResourceTemplate",
    "DirectTool",
    "EmptyResult",
    "GetPromptResult",
    "InitializationResult",
    "InputRequiredResult",
    "ListPromptsResult",
    "ListResourcesResult",
    "ListResourceTemplatesResult",
    "ListToolsResult",
    "MCPConfig",
    "MCPTestKit",
    "ProbeEvidence",
    "ProbeKind",
    "ProbeReport",
    "ProbeRequest",
    "ProbeResult",
    "ProbeService",
    "ReadinessProbeService",
    "SDKConfig",
    "PromptResult",
    "ResourceReadResult",
    "ToolCallResult",
    "Tool",
    "Resource",
    "ResourceTemplate",
    "load_config",
    "resolve_config",
    "AllowlistedTerminalHandler",
    "ElicitationRequest",
    "ElicitationResult",
    "ElicitationHandler",
    "FilesystemHandler",
    "FilesystemRequest",
    "FilesystemResult",
    "InteractionController",
    "InteractionHandlers",
    "InteractionReceipt",
    "PermissionRequest",
    "PermissionResult",
    "PermissionHandler",
    "SamplingRequest",
    "SamplingResult",
    "SamplingHandler",
    "TerminalHandler",
    "TerminalRequest",
    "TerminalResult",
    "WorkspaceFilesystemHandler",
]

__all__ = [*__all__, *_OBSERVABILITY_EXPORTS]
