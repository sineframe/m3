"""Asynchronous SDK configuration and capability boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable as _Awaitable, Callable as _Callable
import inspect as _inspect
from pathlib import Path as _Path
from uuid import uuid4 as _uuid4
from typing import Any as _Any, Iterable as _Iterable, Literal as _Literal, Mapping as _Mapping, NoReturn as _NoReturn, Protocol as _Protocol, cast as _cast

import httpx2
from mcp import ClientSession as _ClientSession

from .agent_session import AsyncAgentSession as _CoreAsyncAgentSession, HarnessAdapter
from .harness.contracts import (
    HarnessAdapterRegistry as _HarnessAdapterRegistry,
    default_harness_adapter_registry as _default_harness_adapter_registry,
)
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
from .server_group import ServerGroupManager as _ServerGroupManager
from .storage import ArtifactStore as _ArtifactStore
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
from .direct_client import (
    AsyncDirectClient as _CoreAsyncDirectClient,
    CallToolResult,
    CompletionResult,
    DirectPrompt,
    DirectResource,
    DirectResourceTemplate,
    DirectTool,
    EmptyResult,
    GetPromptResult,
    InitializeResult,
    InitializationResult,
    InputRequiredResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    Prompt,
    PromptPage,
    PromptResult,
    ReadResourceResult,
    Resource,
    ResourcePage,
    ResourceReadResult,
    ResourceTemplate,
    ResourceTemplatePage,
    ResourceTemplatesPage,
    ResourcesPage,
    Tool,
    ToolCallResult,
    ToolPage,
    ToolsPage,
)
from .evaluations import AsyncEvaluator as _AsyncEvaluator, EvaluationRunner as _EvaluationRunner, EvaluatorCallable as _EvaluatorCallable
from .errors import (
    KitClosed as _KitClosed,
    ModelValidationError as _ModelValidationError,
    OperationCancelled as _OperationCancelled,
    OperationTimeout as _OperationTimeout,
    ProtocolError as _ProtocolError,
    UnsupportedFeature as _UnsupportedFeature,
)
from .direct_trace import DirectTraceBridge as _DirectTraceBridge
from .execution_runtime import AsyncExecutionController as _AsyncExecutionController, AsyncExecutionHandle
from .execution_trace import ExecutionTraceRecorder as _ExecutionTraceRecorder
from .storage import ExecutionStore as _ExecutionStore
from .storage import InMemoryExecutionStore as _InMemoryExecutionStore
from .trace.redaction import RedactionConfig as _RedactionConfig
from .types import ExecutionOutcome as _ExecutionOutcome
from .services.probes import (
    AsyncCapabilityProbeService,
    ProbeEvidence,
    ProbeKind,
    ProbeReport,
    ProbeRequest,
    ProbeResult,
)
from .types import (
    AgentExecutionSpec as _AgentExecutionSpec,
    Capability as _Capability,
    CapabilityStatus as _CapabilityStatus,
    DirectExecutionSpec as _DirectExecutionSpec,
    ExecutionResult as _ExecutionResult,
    ExecutionSpec as _ExecutionSpec,
    ExecutionSnapshot as _ExecutionSnapshot,
    EvaluationResult as _EvaluationResult,
    Readiness as _Readiness,
    ServerBinding as _ServerBinding,
    ServerValue as _ServerValue,
    InProcessServer as _InProcessServer,
    ProtocolConstraint as _ProtocolConstraint,
    SecretReference as _SecretReference,
    SSEServer as _SSEServer,
    StdioServer as _StdioServer,
    StreamableHTTPServer as _StreamableHTTPServer,
    TransportKind as _TransportKind,
    TurnResult as _TurnResult,
    UserMessage as _UserMessage,
)
from .transport.direct import (
    HostResolver as _HostResolver,
    SecretResolver as _SecretResolver,
    TransportEvidence as _RemoteTransportEvidence,
    TransportName as _TransportName,
    remote_connection as _remote_connection,
)
from .transport.local import InProcessMCPTransport as _InProcessMCPTransport, StdioMCPTransport as _StdioMCPTransport


_CURRENT_MCP_PROTOCOL = "2025-11-25"
_SERVER_FAILURE_SETTLE_TIMEOUT = 0.05


def _adapt_callback(callback: _Any) -> _Any:
    """Keep callback failures value-free at the official client boundary.

    Notification failures are logged by the official client with their
    traceback.  Never let a user exception message (which may contain a
    credential) cross that boundary.  Catch only ordinary exceptions so
    cancellation and process-control exceptions keep their semantics.
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


_DIRECT_OPERATION_NAMES = frozenset(
    {
        "initialize",
        "list_tools",
        "list_all_tools",
        "list_resources",
        "list_all_resources",
        "list_resource_templates",
        "list_all_resource_templates",
        "list_prompts",
        "list_all_prompts",
        "read_resource",
        "get_prompt",
        "call_tool",
        "complete",
        "subscribe_resource",
        "unsubscribe_resource",
        "ping",
        "set_logging_level",
        "send_progress_notification",
        "send_notification",
        "send_roots_list_changed",
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


class _OwnedLifecycle:
    """Run transport enter/exit in one persistent owner task.

    Official MCP stdio and HTTP adapters contain AnyIO cancel scopes whose
    exit must happen in the task that entered them.  The public kit may be
    closed by a different task, so cleanup is submitted to this owner task
    rather than attempting to exit the scopes from the caller.
    """

    def __init__(
        self,
        open_resources: _Callable[[], _Awaitable[tuple[_Any, _Any, _Any, _Callable[[], _Awaitable[None]]]]],
    ) -> None:
        self._open_resources = open_resources
        self._commands: asyncio.Queue[tuple[str, asyncio.Future[_Any]]] = asyncio.Queue()
        self._start_future: asyncio.Future[tuple[_Any, _Any, _Any, _Callable[[], _Awaitable[None]]]] | None = None
        self._close_future: asyncio.Future[None] | None = None
        self._task = asyncio.create_task(self._run())

    async def start(self) -> tuple[_Any, _Any, _Any, _Callable[[], _Awaitable[None]]]:
        if self._start_future is None:
            self._start_future = asyncio.get_running_loop().create_future()
            await self._commands.put(("start", self._start_future))
        return await asyncio.shield(self._start_future)

    async def close(self) -> None:
        if self._task.done():
            return
        # A caller may close the kit while another task is still waiting for
        # transport/session startup.  The owner task is the only safe place
        # to unwind official AnyIO scopes, so cancel that task rather than
        # queueing a close command behind an open that may never finish.
        if self._start_future is not None and not self._start_future.done():
            self._task.cancel()
            try:
                await asyncio.shield(self._task)
            except asyncio.CancelledError:
                pass
            return
        if self._close_future is None:
            self._close_future = asyncio.get_running_loop().create_future()
            if self._task.done():
                self._close_future.set_result(None)
            else:
                await self._commands.put(("close", self._close_future))
        await asyncio.shield(self._close_future)

    async def _run(self) -> None:
        resources: tuple[_Any, _Any, _Any, _Callable[[], _Awaitable[None]]] | None = None
        try:
            command, future = await self._commands.get()
            if command != "start":
                future.set_result(None)
                return
            try:
                resources = await self._open_resources()
            except BaseException as error:
                if not future.done():
                    self._set_future_exception(future, error)
                if self._close_future is not None and not self._close_future.done():
                    self._close_future.set_result(None)
                return
            if not future.done():
                future.set_result(resources)
            command, future = await self._commands.get()
            if command == "close":
                try:
                    await resources[3]()
                except BaseException as error:
                    if not future.done():
                        self._set_future_exception(future, error)
                else:
                    if not future.done():
                        future.set_result(None)
        except asyncio.CancelledError:
            if self._start_future is not None and not self._start_future.done():
                # A cancelled start waiter is shielded from cancelling this
                # future. Cancel the owner-owned future explicitly instead of
                # publishing an unobserved CancelledError.
                self._start_future.cancel()
            raise
        finally:
            if self._close_future is not None and not self._close_future.done():
                self._close_future.set_result(None)

    @staticmethod
    def _set_future_exception(future: asyncio.Future[_Any], error: BaseException) -> None:
        """Publish an error while marking abandoned futures as retrieved."""

        future.set_exception(error)

        def consume_exception(done: asyncio.Future[_Any]) -> None:
            if not done.cancelled():
                done.exception()

        future.add_done_callback(consume_exception)


class _EnteredSessionProxy:
    """Expose an already-entered official session to the core facade."""

    def __init__(self, session: _Any) -> None:
        self._session = session

    async def __aenter__(self) -> _Any:
        return self._session

    async def __aexit__(self, exc_type: _Any, exc_value: _Any, traceback: _Any) -> None:
        return None

    def __getattr__(self, name: str) -> _Any:
        return getattr(self._session, name)


class AsyncDirectClient(_CoreAsyncDirectClient):
    """Lifecycle-owned async direct client returned by ``AsyncMCPTestKit``."""

    def __init__(
        self,
        kit: "AsyncMCPTestKit",
        server: _ServerValue,
        *,
        timeout: float,
        validate_schemas: bool,
        secret_resolver: _SecretResolver | None,
        bearer_token: _SecretReference | None,
        auth: httpx2.Auth | None,
        for_agent: bool,
        resolve_host: _HostResolver | None,
        raise_server_exceptions: bool,
        session_options: _Mapping[str, _Any],
        trace_bridge: _DirectTraceBridge | None = None,
        trace_owner: bool = True,
        workspace_root: str | None = None,
    ) -> None:
        self._kit = kit
        self._server: _ServerValue | None = server
        self._timeout = timeout
        self._validate_schemas_option = validate_schemas
        self._secret_resolver = secret_resolver
        self._bearer_token = bearer_token
        self._auth = auth
        self._for_agent = for_agent
        self._resolve_host = resolve_host
        self._raise_server_exceptions = raise_server_exceptions
        self._session_options = dict(session_options)
        self._redaction_config = _RedactionConfig.from_environment()
        # Keep the bridge private: it observes the official decoded session
        # streams, while the public client exposes only immutable snapshots.
        self._trace_observer = trace_bridge or _DirectTraceBridge(
            server_binding=type(server).__name__,
            redaction_config=self._redaction_config,
        )
        self._trace_owner = trace_owner
        self._workspace_root = workspace_root
        # The core facade owns this optional slot; initialize it before
        # startup so failed transport setup still exposes final evidence.
        self._trace_bridge = self._trace_observer
        self._trace_finalized = False
        self._trace_outcome: _ExecutionOutcome | None = None
        self._lifecycle: _OwnedLifecycle | None = None
        self._owner: _Any = None
        self._connection: _Any = None
        self._core_started = False
        self._entered = False
        self._closed = False
        self._initialized = None
        self._closed_transport_evidence: _Any = None

    def __getattribute__(self, name: str) -> _Any:
        value = super().__getattribute__(name)
        if name not in _DIRECT_OPERATION_NAMES or not callable(value):
            return value

        async def guarded(*args: _Any, **kwargs: _Any) -> _Any:
            try:
                result = await value(*args, **kwargs)
            except BaseException as exc:
                self._mark_trace_failure(exc)
                if isinstance(exc, _ProtocolError):
                    # An in-process server can publish its original handler
                    # failure immediately after the official JSON-RPC error.
                    # Wait for the transport's explicit settlement signal;
                    # otherwise retain the typed protocol error.
                    await self._raise_connection_failure(wait_for_failure=True)
                    raise
                if isinstance(exc, _ModelValidationError):
                    raise
                await self._raise_connection_failure()
                raise
            await self._raise_connection_failure()
            return result

        return guarded

    async def _raise_connection_failure(self, *, wait_for_failure: bool = False) -> None:
        connection = self._connection
        if connection is None:
            return
        if wait_for_failure:
            wait_for_publication = getattr(connection, "wait_for_failure_publication", None)
            if callable(wait_for_publication):
                await wait_for_publication(_SERVER_FAILURE_SETTLE_TIMEOUT)
        raise_if_failed = getattr(connection, "raise_if_failed", None)
        if not callable(raise_if_failed):
            return
        raise_if_failed(expose_original=True)

    @property
    def transport_evidence(self) -> _Any:
        """Return sanitized partial/terminal transport evidence when available."""

        if self._connection is None:
            return self._closed_transport_evidence
        evidence = getattr(self._connection, "evidence", None)
        if evidence is None or hasattr(evidence, "protocol_version"):
            return evidence
        # Local adapters intentionally expose only lifecycle evidence. Project
        # their safe fields into the same evidence shape as remote adapters so
        # callers can inspect negotiated metadata consistently across transports.
        initialization = self._initialized
        server_info = initialization.server_info if initialization is not None else {}
        capabilities = initialization.capabilities if initialization is not None else {}
        extension_value = capabilities.get("extensions", {})
        extensions = (
            tuple(sorted(str(key) for key in extension_value))
            if isinstance(extension_value, _Mapping)
            else ()
        )
        capability_names = tuple(
            sorted(str(key) for key, value in capabilities.items() if value is not None)
        )
        transport = str(getattr(evidence, "transport", "unknown"))
        state: _Literal["created", "connecting", "initialized", "closed", "failed"] = (
            "failed"
            if bool(getattr(evidence, "partial", False))
            else "closed"
            if bool(getattr(evidence, "closed", False))
            else "initialized"
            if initialization is not None
            else "connecting"
        )
        return _RemoteTransportEvidence(
            transport=_cast(_TransportName, transport),
            endpoint=f"<{transport}>",
            state=state,
            protocol_version=initialization.protocol_version if initialization is not None else None,
            server_name=str(server_info["name"]) if "name" in server_info else None,
            server_version=str(server_info["version"]) if "version" in server_info else None,
            instructions=initialization.instructions is not None if initialization is not None else False,
            capabilities=capability_names,
            extensions=extensions,
            error_type=getattr(evidence, "error_kind", None),
        )

    async def _open_resources(self) -> tuple[_Any, _Any, _Any, _Callable[[], _Awaitable[None]]]:
        owner: _Any
        connection: _Any
        session: _Any
        server = self._server
        if isinstance(server, _InProcessServer):
            owner = _InProcessMCPTransport(
                server,
                raise_server_exceptions=self._raise_server_exceptions,
                workspace_root=self._workspace_root,
            )
            self._owner = owner
            connection = await owner.open()
            self._connection = connection
            read_stream, write_stream = self._trace_observer.wrap_streams(
                connection.read_stream, connection.write_stream
            )
            session = _ClientSession(
                _cast(_Any, read_stream),
                _cast(_Any, write_stream),
                read_timeout_seconds=self._timeout,
                **self._session_options,
            )
            try:
                await session.__aenter__()
            except BaseException:
                await connection.close()
                raise

            async def cleanup() -> None:
                await session.__aexit__(None, None, None)
                await connection.close()

            return owner, connection, session, cleanup
        elif isinstance(server, _StdioServer):
            resolver = self._secret_resolver.resolve if self._secret_resolver is not None else None
            owner = _StdioMCPTransport(
                server,
                secret_resolver=resolver,
                secret_observer=self._trace_observer.bind_secret_values,
                workspace_root=self._workspace_root,
            )
            self._owner = owner
            connection = await owner.open()
            self._connection = connection
            read_stream, write_stream = self._trace_observer.wrap_streams(
                connection.read_stream, connection.write_stream
            )
            session = _ClientSession(
                _cast(_Any, read_stream),
                _cast(_Any, write_stream),
                read_timeout_seconds=self._timeout,
                **self._session_options,
            )
            try:
                await session.__aenter__()
            except BaseException:
                await connection.close()
                raise

            async def cleanup() -> None:
                await session.__aexit__(None, None, None)
                await connection.close()

            return owner, connection, session, cleanup
        elif isinstance(server, (_StreamableHTTPServer, _SSEServer)):
            kwargs: dict[str, _Any] = {
                "resolver": self._secret_resolver,
                "bearer_token": self._bearer_token,
                "auth": self._auth,
                "timeout": self._timeout,
                "for_agent": self._for_agent,
                "initialize": False,
                "session_options": self._session_options,
                "trace_bridge": self._trace_observer,
                "secret_observer": self._trace_observer.bind_secret_values,
            }
            if self._resolve_host is not None:
                kwargs["resolve_host"] = self._resolve_host
            owner = _remote_connection(server, **kwargs)
            # Publish the owner before entering so a failed remote handshake
            # leaves sanitized transport evidence reachable from the typed
            # client and its exception.
            self._owner = owner
            self._connection = owner
            session = await owner.__aenter__()

            async def cleanup() -> None:
                await owner.aclose()

            return owner, owner, session, cleanup
        else:
            raise _UnsupportedFeature("unsupported direct server transport")

    async def _open_and_initialize(self) -> None:
        self._lifecycle = _OwnedLifecycle(self._open_resources)
        owner, connection, session, _cleanup = await self._lifecycle.start()
        self._owner = owner
        self._connection = connection
        session = _EnteredSessionProxy(session)
        _CoreAsyncDirectClient.__init__(
            self,
            session,
            timeout=self._timeout,
            validate_schemas=self._validate_schemas_option,
            trace_bridge=self._trace_observer,
            redaction_config=self._redaction_config,
            workspace_root=self._workspace_root,
        )
        self._core_started = True
        await _CoreAsyncDirectClient.__aenter__(self)
        if hasattr(self._owner, "capture_session_metadata"):
            self._owner.capture_session_metadata()

    async def __aenter__(self) -> "AsyncDirectClient":
        if self._closed:
            raise RuntimeError("direct client is closed")
        if self._entered:
            return self
        try:
            await self._open_and_initialize()
            await self._raise_connection_failure()
            self._entered = True
            return self
        except BaseException as exc:
            self._mark_trace_failure(exc)
            failure = exc
            try:
                await self._raise_connection_failure(wait_for_failure=True)
            except BaseException as server_failure:
                failure = server_failure
            try:
                await self.aclose()
            except BaseException:
                pass
            raise failure

    def _mark_trace_failure(self, error: BaseException) -> None:
        if isinstance(error, (asyncio.CancelledError, _OperationCancelled)):
            self._trace_outcome = _ExecutionOutcome.CANCELLED
        elif isinstance(error, (TimeoutError, asyncio.TimeoutError, _OperationTimeout)):
            self._trace_outcome = _ExecutionOutcome.TIMED_OUT
        else:
            self._trace_outcome = _ExecutionOutcome.FAILED

    async def aclose(self) -> None:
        if self._closed:
            return
        failure: BaseException | None = None
        if self._core_started:
            try:
                await _CoreAsyncDirectClient.aclose(self)
            except BaseException as exc:
                failure = exc
        else:
            self._closed = True
        if self._lifecycle is not None:
            try:
                await self._lifecycle.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        self._closed_transport_evidence = self.transport_evidence
        if failure is not None:
            self._mark_trace_failure(failure)
        if self._trace_owner and not self._trace_finalized:
            outcome = self._trace_outcome or _ExecutionOutcome.COMPLETED
            try:
                self._trace_observer.finalize(
                    outcome,
                    cleanup_succeeded=failure is None,
                )
                self._trace_finalized = True
            except BaseException as exc:
                if failure is None:
                    failure = exc
        try:
            self._trace_observer.clear_secret_values()
        except BaseException:
            if failure is None:
                failure = RuntimeError("trace redaction cleanup failed")
        self._redaction_config = _RedactionConfig(include_environment=False)
        self._secret_resolver = None
        self._bearer_token = None
        self._auth = None
        self._session_options.clear()
        self._server = None
        self._owner = None
        self._connection = None
        self._session = _cast(_Any, None)
        self._kit._direct_closed(self)
        if failure is not None:
            raise failure

    async def __aexit__(self, exc_type: _Any, exc_value: _Any, traceback: _Any) -> None:
        if exc_value is not None:
            self._mark_trace_failure(exc_value)
        try:
            await self.aclose()
        except BaseException:
            if exc_value is None:
                raise


AsyncAgentSession = _CoreAsyncAgentSession


class AsyncMCPTestKit:
    """Async twin of :class:`mcp_pal.sync_api.MCPTestKit`.

    Capability probes run in a worker thread so subprocess and filesystem
    operations never block the event loop.
    """

    def __init__(
        self,
        config: SDKConfig | _Mapping[str, _Any] | None = None,
        *,
        env: _Mapping[str, str] | None = None,
        cwd: str | _Path | None = None,
        probe_timeout_seconds: float = 5.0,
        probe_output_limit: int = 64 * 1024,
        adapter_registry: _HarnessAdapterRegistry | None = None,
        store: _ExecutionStore | None = None,
        embedded_worker: bool = True,
    ) -> None:
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._active_direct: set[AsyncDirectClient] = set()
        self._active_sessions: set[AsyncAgentSession] = set()
        self._adapter_registry = (
            adapter_registry
            if adapter_registry is not None
            else _default_harness_adapter_registry()
        )
        self._execution_controller = _AsyncExecutionController(
            self,
            store=store,
            worker=embedded_worker,
        )
        if store is not None and embedded_worker:
            # Async kits are often constructed inside an already-running
            # event loop (including the sync portal). Start ownership here so
            # a second toolkit can claim durable work submitted by another
            # process/toolkit before its first local submit.
            try:
                self._execution_controller.start_embedded_worker()
            except RuntimeError:
                # Construction outside an event loop is valid; submit() will
                # bind the worker to the loop that executes the work.
                pass
        self._evaluations = _EvaluationRunner()
        self.config = config if isinstance(config, SDKConfig) else resolve_config(config, env=env, cwd=cwd)
        self._probes = AsyncCapabilityProbeService(
            timeout_seconds=probe_timeout_seconds,
            output_limit=probe_output_limit,
        )
        self._probes._set_lifecycle_guard(self._ensure_open)

    def _ensure_open(self) -> None:
        if self._closed:
            raise _KitClosed("AsyncMCPTestKit is closed")

    @property
    def probes(self) -> AsyncCapabilityProbeService:
        """Asynchronous capability namespace owned by this kit."""

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

        return self._execution_controller._persistent_store

    async def __aenter__(self) -> "AsyncMCPTestKit":
        self._ensure_open()
        # Construction is allowed outside an event loop. Bind a persistent
        # worker when the kit is actually entered so it can claim durable work
        # submitted by another process before this kit submits locally.
        if self._execution_controller._persistent_store is not None:
            self._execution_controller.start_embedded_worker()
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the shell; repeated calls are intentionally harmless."""

        async with self._close_lock:
            self._closed = True
            failures: list[BaseException] = []
            try:
                await self._execution_controller.close()
            except BaseException as exc:
                failures.append(exc)
            for client in tuple(self._active_direct):
                try:
                    await client.aclose()
                except BaseException as exc:
                    failures.append(exc)
            for session in tuple(self._active_sessions):
                try:
                    await session.aclose()
                except BaseException as exc:
                    failures.append(exc)
            if failures:
                raise failures[0]

    async def capabilities(self, requests: _Iterable[ProbeRequest] = ()) -> ProbeReport:
        self._ensure_open()
        requested = tuple(requests)
        if requested:
            return await self.probes.probe_requested(requested)
        configuration = ProbeResult(
            capability=_Capability(
                name="configuration",
                status=_CapabilityStatus.READY,
                protocol_version=None if self.config.protocol_revision == "auto" else self.config.protocol_revision,
            ),
            evidence=ProbeEvidence(
                kind=ProbeKind.CONFIGURATION,
                target="sdk-config",
                details=self.config.model_dump(mode="json"),
            ),
        )
        memory = await self.probes.probe_storage("memory")
        results = (configuration, memory)
        capabilities = tuple(result.capability for result in results)
        return ProbeReport(readiness=_Readiness(ready=True, capabilities=capabilities), results=results)

    def register_evaluator(self, name: str, evaluator: _EvaluatorCallable | _AsyncEvaluator) -> None:
        """Register a deterministic evaluator by its serializable name."""

        self._ensure_open()
        self._evaluations.register(name, evaluator)

    async def evaluate(
        self,
        subject: _Any,
        evaluator: str | _EvaluatorCallable | _AsyncEvaluator,
        *,
        required: bool = False,
        goal: str | None = None,
        trace: _Any = None,
        artifacts: _Any = (),
        metadata: _Mapping[str, str | int | float | bool | None] | None = None,
        execution_id: _Any = None,
    ) -> _EvaluationResult:
        """Run and persist one deterministic evaluation without changing lifecycle."""

        self._ensure_open()
        return await self._evaluations.evaluate_async(
            subject,
            evaluator,
            required=required,
            goal=goal,
            trace=trace,
            artifacts=artifacts,
            metadata=metadata,
            execution_id=execution_id,
        )

    def evaluation_results(self) -> tuple[_EvaluationResult, ...]:
        self._ensure_open()
        return self._evaluations.results()

    def _unsupported(self, operation: str) -> _NoReturn:
        self._ensure_open()
        raise _UnsupportedFeature(f"{operation} is not implemented in the configuration milestone")

    async def run(self, spec: _DirectExecutionSpec | _AgentExecutionSpec) -> _ExecutionResult:
        self._ensure_open()
        return await self._execution_controller.run(spec)

    def submit(self, spec: _ExecutionSpec) -> AsyncExecutionHandle:
        self._ensure_open()
        return self._execution_controller.submit(spec)

    def _direct_closed(self, client: AsyncDirectClient) -> None:
        self._active_direct.discard(client)

    def _session_closed(self, session: AsyncAgentSession) -> None:
        self._active_sessions.discard(session)

    def direct(
        self,
        server: _ServerValue | _ServerBinding,
        *,
        protocol: object | None = None,
        timeout: float | None = None,
        validate_schemas: bool = False,
        secret_resolver: _SecretResolver | None = None,
        bearer_token: _SecretReference | None = None,
        auth: httpx2.Auth | None = None,
        for_agent: bool = False,
        resolve_host: _HostResolver | None = None,
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
        trace_bridge: _DirectTraceBridge | None = None,
        trace_owner: bool = True,
        workspace_root: str | None = None,
    ) -> AsyncDirectClient:
        self._ensure_open()
        selected = server.server if hasattr(server, "server") else server
        if selected is None or not isinstance(selected, (_InProcessServer, _StdioServer, _StreamableHTTPServer, _SSEServer)):
            raise _UnsupportedFeature("direct server profiles require runtime resolution")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        requested_revision: str | None
        requested_transport: _TransportKind | None = None
        if protocol is None:
            requested_revision = self.config.protocol_revision
        elif isinstance(protocol, _ProtocolConstraint):
            requested_revision = protocol.revision
            requested_transport = protocol.transport
        elif isinstance(protocol, str):
            requested_revision = protocol
        else:
            raise ValueError("protocol must be a revision string or ProtocolConstraint")
        actual_transport = {
            _InProcessServer: _TransportKind.IN_PROCESS,
            _StdioServer: _TransportKind.STDIO,
            _StreamableHTTPServer: _TransportKind.STREAMABLE_HTTP,
            _SSEServer: _TransportKind.SSE,
        }[type(selected)]
        if requested_transport is not None and requested_transport is not actual_transport:
            raise _UnsupportedFeature("requested transport is not supported by this direct binding")
        if requested_revision not in {None, "", "auto", _CURRENT_MCP_PROTOCOL}:
            raise _UnsupportedFeature("explicit MCP protocol revision is not supported by the official client")
        client = AsyncDirectClient(
            self,
            selected,
            timeout=timeout or 30.0,
            validate_schemas=validate_schemas,
            secret_resolver=secret_resolver,
            bearer_token=bearer_token,
            auth=auth,
            for_agent=for_agent,
            resolve_host=resolve_host,
            raise_server_exceptions=raise_server_exceptions,
            trace_bridge=trace_bridge,
            trace_owner=trace_owner,
            workspace_root=workspace_root,
            session_options={
                key: value
                for key, value in {
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
                    "notification_bindings": tuple(notification_bindings)
                    if notification_bindings is not None
                    else None,
                    "dispatcher": dispatcher,
                }.items()
                if value is not None
            },
        )
        self._active_direct.add(client)
        return client

    def agent_session(
        self,
        spec: _AgentExecutionSpec,
        *,
        adapter: HarnessAdapter | None = None,
        runtime_servers: _Iterable[_Any] = (),
        _event_sink: _Any = None,
        interaction_handlers: InteractionHandlers | None = None,
        _trace_recorder: _ExecutionTraceRecorder | None = None,
        _trace_owner: bool = True,
        _execution_id: _Any = None,
        _artifact_store: _ArtifactStore | None = None,
    ) -> AsyncAgentSession:
        self._ensure_open()
        if not isinstance(spec, _AgentExecutionSpec):
            self._unsupported("agent_session")
        resolved = adapter or self._adapter_registry.resolve(spec)
        bindings = spec.servers + _runtime_server_bindings(runtime_servers)
        manager = _ServerGroupManager(bindings, tool_policy=spec.tool_policy)
        interactions = InteractionController(
            permission_policy=spec.permission_policy,
            elicitation_policy=spec.elicitation_policy,
            sampling_policy=spec.sampling_policy,
            filesystem_policy=spec.filesystem_policy,
            terminal_policy=spec.terminal_policy,
            handlers=interaction_handlers,
        )
        recorder = _trace_recorder
        if recorder is None:
            from .types import ExecutionId as _ExecutionId

            recorder = _ExecutionTraceRecorder(
                _InMemoryExecutionStore(),
                _execution_id if _execution_id is not None else _ExecutionId(str(_uuid4())),
            )
        session = AsyncAgentSession(
            spec,
            resolved,
            server_manager=manager,
            server_manager_factory=lambda: _ServerGroupManager(bindings, tool_policy=spec.tool_policy),
            on_close=self._session_closed,
            event_sink=_event_sink,
            interaction_controller=interactions,
            trace_recorder=recorder,
            trace_owner=_trace_owner,
            artifact_store=_artifact_store,
        )
        self._active_sessions.add(session)
        return session


__all__ = [
    "AsyncAgentSession",
    "HarnessAdapter",
    "AsyncCapabilityProbeService",
    "AsyncDirectClient",
    "AsyncExecutionHandle",
    "AsyncMCPTestKit",
    "CallToolResult",
    "CompletionResult",
    "ConfigOrigin",
    "ConfigSource",
    "Configuration",
    "ConfigurationError",
    "DirectPrompt",
    "DirectResource",
    "DirectResourceTemplate",
    "DirectTool",
    "EmptyResult",
    "GetPromptResult",
    "InitializeResult",
    "InitializationResult",
    "InputRequiredResult",
    "ListPromptsResult",
    "ListResourcesResult",
    "ListResourceTemplatesResult",
    "ListToolsResult",
    "MCPConfig",
    "ProbeEvidence",
    "ProbeKind",
    "ProbeReport",
    "ProbeRequest",
    "ProbeResult",
    "Prompt",
    "PromptPage",
    "PromptResult",
    "ReadResourceResult",
    "Resource",
    "ResourcePage",
    "ResourceReadResult",
    "ResourceTemplate",
    "ResourceTemplatePage",
    "ResourceTemplatesPage",
    "ResourcesPage",
    "SDKConfig",
    "Tool",
    "ToolCallResult",
    "ToolPage",
    "ToolsPage",
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
