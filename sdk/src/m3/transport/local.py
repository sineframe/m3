"""Official-MCP local transport adapters.

These adapters own only connection lifecycle.  MCP framing, initialization,
JSON-RPC, and capability behavior remain in the official ``mcp`` package.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeAlias

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_client_server_memory_streams

from ..trace.redaction import is_sensitive_key
from ..types import InProcessServer, SecretReference, StdioServer


class LocalTransportError(Exception):
    """Base class for sanitized local transport failures."""

    def __init__(
        self, message: str, *, evidence: TransportEvidence | None = None
    ) -> None:
        super().__init__(message)
        self.evidence = evidence


class TransportStartupError(LocalTransportError):
    """A local server or process could not be started."""


class TransportProcessError(LocalTransportError):
    """An owned server process/task failed while serving."""


class TransportClosed(LocalTransportError):
    """An operation was attempted after transport shutdown."""


@dataclass(frozen=True, slots=True)
class TransportEvidence:
    """Safe lifecycle evidence; no command, environment, or raw stderr."""

    transport: str
    started: bool = False
    closed: bool = False
    partial: bool = False
    error_kind: str | None = None
    limitations: tuple[str, ...] = ()


class TransportConnection(Protocol):
    read_stream: Any
    write_stream: Any
    evidence: TransportEvidence

    async def close(self) -> None: ...

    async def wait_for_failure_publication(self, timeout: float) -> bool: ...

    def raise_if_failed(self, *, expose_original: bool = False) -> None: ...


Factory: TypeAlias = Callable[[], Any | Awaitable[Any]]
SecretResolver: TypeAlias = Callable[[SecretReference], str]
SecretObserver: TypeAlias = Callable[[str], None]


_IN_PROCESS_SERVER_ACTIVE = 0
_CURRENT_WORKSPACE_ROOT: ContextVar[str | None] = ContextVar(
    "m3_workspace_root", default=None
)


def current_workspace_root() -> str | None:
    """Return the workspace scoped to the current in-process MCP server task."""

    return _CURRENT_WORKSPACE_ROOT.get()


class _InProcessExceptionLogFilter(logging.Filter):
    """Keep official MCP logs useful without copying exception secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        if _IN_PROCESS_SERVER_ACTIVE > 0:
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_IN_PROCESS_LOG_FILTER = _InProcessExceptionLogFilter()
for _logger_name in ("mcp.shared.jsonrpc_dispatcher", "mcp.server.runner"):
    logging.getLogger(_logger_name).addFilter(_IN_PROCESS_LOG_FILTER)


def _leaf_server_failure(error: BaseException) -> BaseException:
    """Unwrap official AnyIO task-group wrappers for opted-in callers."""

    children = getattr(error, "exceptions", None)
    if isinstance(children, tuple) and children:
        return _leaf_server_failure(children[0])
    return error


class _Connection:
    def __init__(self, *, read_stream: Any, write_stream: Any, transport: str) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.evidence = TransportEvidence(transport=transport, started=True)
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise TransportClosed("local transport is closed")

    def raise_if_failed(self, *, expose_original: bool = False) -> None:
        del expose_original
        return None

    async def wait_for_failure_publication(self, timeout: float) -> bool:
        del timeout
        return False


class _InProcessConnection(_Connection):
    _FAILURE_SETTLE_TIMEOUT = 0.05

    def __init__(
        self,
        *,
        read_stream: Any,
        write_stream: Any,
        memory_context: Any,
        server_task: asyncio.Task[Any],
        raise_server_exceptions: bool,
    ) -> None:
        super().__init__(
            read_stream=read_stream, write_stream=write_stream, transport="in_process"
        )
        self._memory_context = memory_context
        self._server_task = server_task
        self._raise_server_exceptions = raise_server_exceptions
        self._server_failure: BaseException | None = None
        self._failure_observed = False
        self._server_settled = asyncio.Event()
        self._close_task: asyncio.Task[None] | None = None
        server_task.add_done_callback(self._capture_server_failure)

    def _capture_server_failure(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            self._server_failure = asyncio.CancelledError()
        else:
            try:
                failure = task.exception()
                self._server_failure = (
                    _leaf_server_failure(failure) if failure is not None else None
                )
            except BaseException:
                self._server_failure = RuntimeError("server task failed")
        if self._server_failure is not None:
            self.evidence = TransportEvidence(
                transport="in_process",
                started=True,
                closed=self._closed,
                partial=True,
                error_kind="server_failure",
            )
        self._server_settled.set()

    async def wait_for_failure_publication(self, timeout: float) -> bool:
        """Wait briefly for the owned server task to publish its outcome."""

        if not self._server_settled.is_set():
            try:
                await asyncio.wait_for(self._server_settled.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return False
        return self._server_failure is not None

    def raise_if_failed(self, *, expose_original: bool = False) -> None:
        if self._server_failure is not None:
            self._failure_observed = True
        if self._server_failure is not None and self._raise_server_exceptions:
            if expose_original:
                raise self._server_failure
            raise TransportProcessError(
                "in-process MCP server failed",
                evidence=self.evidence,
            )
        if self._server_failure is not None and expose_original:
            raise TransportProcessError(
                "in-process MCP server failed",
                evidence=self.evidence,
            )

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._close_task = asyncio.create_task(
                asyncio.wait_for(self._close_impl(), timeout=3.0)
            )
        close_task = self._close_task
        if close_task is None:
            return
        try:
            # Shield the owned cleanup task so cancellation of a caller does
            # not abandon the server task or memory streams.  The cancellation
            # still propagates to the caller; a later close() can await the
            # same task and observe its final state.
            await asyncio.shield(close_task)
        except asyncio.TimeoutError:
            self.evidence = TransportEvidence(
                transport="in_process",
                started=True,
                closed=False,
                partial=True,
                error_kind="cleanup_timeout",
                limitations=("server_task_not_reaped",),
            )
            raise TransportProcessError(
                "in-process MCP cleanup timed out", evidence=self.evidence
            ) from None
        await self.wait_for_failure_publication(self._FAILURE_SETTLE_TIMEOUT)
        if self._server_failure is not None and not self._failure_observed:
            if self._raise_server_exceptions:
                raise self._server_failure
            raise TransportProcessError(
                "in-process MCP server failed during cleanup",
                evidence=self.evidence,
            )

    async def _close_impl(self) -> None:
        for stream in (self.read_stream, self.write_stream):
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        if not self._server_task.done():
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await self._memory_context.__aexit__(None, None, None)
        self.evidence = TransportEvidence(
            transport="in_process",
            started=True,
            closed=True,
            partial=self._server_failure is not None,
            error_kind="server_failure" if self._server_failure is not None else None,
        )


class _StdioConnection(_Connection):
    def __init__(
        self, *, stack: AsyncExitStack, owner_task: asyncio.Task[Any] | None
    ) -> None:
        self._stack = stack
        self._owner_task = owner_task
        # stdio_client owns its AnyIO streams and process.  The context manager
        # yields the same read/write pair accepted by ClientSession.
        super().__init__(read_stream=None, write_stream=None, transport="stdio")
        self._streams: tuple[Any, Any] | None = None

    def set_streams(self, streams: tuple[Any, Any]) -> None:
        self.read_stream, self.write_stream = streams
        self._streams = streams

    async def close(self) -> None:
        if self._closed:
            return
        owner = self._owner_task
        if owner is not None and owner is not asyncio.current_task():
            self.evidence = TransportEvidence(
                transport="stdio",
                started=True,
                closed=False,
                partial=True,
                error_kind="cleanup_owner",
                limitations=("cleanup_must_run_in_owner_task",),
            )
            raise TransportProcessError(
                "stdio MCP cleanup must run in the owner task",
                evidence=self.evidence,
            )
        try:
            # stdio_client owns an AnyIO task group/cancel scope and must be
            # exited in this task. Its official shutdown path already applies
            # bounded flush/process cleanup; wrapping it in wait_for would move
            # __aexit__ to another task and violate AnyIO's scope ownership.
            await self._stack.aclose()
        except asyncio.CancelledError:
            # The official context must be exited in this task.  Permit a
            # caller that was cancelled during cleanup to retry close() in a
            # finally block rather than permanently marking it closed.
            raise
        except Exception:
            self.evidence = TransportEvidence(
                transport="stdio",
                started=True,
                closed=False,
                partial=True,
                error_kind="cleanup_failed",
                limitations=("owned_process_not_reaped",),
            )
            raise TransportProcessError(
                "stdio MCP cleanup failed", evidence=self.evidence
            ) from None
        self._closed = True
        self.evidence = TransportEvidence(transport="stdio", started=True, closed=True)


class InProcessMCPTransport:
    """Connect an official low-level MCP ``Server`` over memory streams."""

    def __init__(
        self,
        server: InProcessServer | Factory,
        *,
        raise_server_exceptions: bool = True,
        workspace_root: str | None = None,
    ) -> None:
        self._factory: Factory = (
            server.factory if isinstance(server, InProcessServer) else server
        )
        self._raise_server_exceptions = raise_server_exceptions
        self._workspace_root = workspace_root
        self._connection: _InProcessConnection | None = None

    async def open(self) -> _InProcessConnection:
        memory_context = create_client_server_memory_streams()
        preserve_original_startup = False
        try:
            client_streams, server_streams = await memory_context.__aenter__()
            token = _CURRENT_WORKSPACE_ROOT.set(self._workspace_root)
            try:
                server = self._factory()
                if inspect.isawaitable(server):
                    server = await server
            finally:
                _CURRENT_WORKSPACE_ROOT.reset(token)
            run = getattr(server, "run", None)
            options_factory = getattr(server, "create_initialization_options", None)
            if not callable(run) or not callable(options_factory):
                raise TransportStartupError(
                    "in-process MCP server is not an official server",
                    evidence=TransportEvidence(
                        transport="in_process",
                        partial=True,
                        error_kind="startup_failure",
                    ),
                )
            token = _CURRENT_WORKSPACE_ROOT.set(self._workspace_root)
            try:
                initialization_options = options_factory()
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                preserve_original_startup = True
                if self._raise_server_exceptions:
                    raise error
                raise TransportStartupError(
                    "in-process MCP server startup failed",
                    evidence=TransportEvidence(
                        transport="in_process",
                        partial=True,
                        error_kind="startup_failure",
                    ),
                ) from None
            finally:
                _CURRENT_WORKSPACE_ROOT.reset(token)

            async def serve() -> Any:
                global _IN_PROCESS_SERVER_ACTIVE
                _IN_PROCESS_SERVER_ACTIVE += 1
                try:
                    return await run(
                        server_streams[0],
                        server_streams[1],
                        initialization_options,
                        raise_exceptions=self._raise_server_exceptions,
                    )
                finally:
                    _IN_PROCESS_SERVER_ACTIVE -= 1

            token = _CURRENT_WORKSPACE_ROOT.set(self._workspace_root)
            try:
                task = asyncio.create_task(serve())
            finally:
                _CURRENT_WORKSPACE_ROOT.reset(token)
            return _InProcessConnection(
                read_stream=client_streams[0],
                write_stream=client_streams[1],
                memory_context=memory_context,
                server_task=task,
                raise_server_exceptions=self._raise_server_exceptions,
            )
        except asyncio.CancelledError:
            await memory_context.__aexit__(None, None, None)
            raise
        except LocalTransportError:
            await memory_context.__aexit__(None, None, None)
            raise
        except Exception as error:
            await memory_context.__aexit__(None, None, None)
            if self._raise_server_exceptions and preserve_original_startup:
                raise error
            raise TransportStartupError(
                "in-process MCP server startup failed",
                evidence=TransportEvidence(
                    transport="in_process", partial=True, error_kind="startup_failure"
                ),
            ) from None

    async def __aenter__(self) -> _InProcessConnection:
        self._connection = await self.open()
        return self._connection

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        connection = self._connection
        if connection is not None:
            await connection.close()


class StdioMCPTransport:
    """Connect to an owned stdio server through official ``stdio_client``."""

    def __init__(
        self,
        server: StdioServer,
        *,
        secret_resolver: SecretResolver | None = None,
        secret_observer: SecretObserver | None = None,
        workspace_root: str | None = None,
    ) -> None:
        self._server = server
        self._secret_resolver = secret_resolver
        self._secret_observer = secret_observer
        self._workspace_root = workspace_root
        self._connection: _StdioConnection | None = None

    def _environment(self) -> dict[str, str] | None:
        if not self._server.environment:
            return None
        values: dict[str, str] = {}
        for key, value in self._server.environment.items():
            if not key or "\x00" in key:
                raise TransportStartupError(
                    "stdio environment contains an invalid name",
                    evidence=TransportEvidence(
                        transport="stdio", partial=True, error_kind="startup_failure"
                    ),
                )
            if isinstance(value, SecretReference):
                if self._secret_resolver is not None:
                    values[key] = self._secret_resolver(value)
                elif value.source == "environment" and value.name in os.environ:
                    values[key] = os.environ[value.name]
                else:
                    raise TransportStartupError(
                        "stdio secret resolution is unavailable",
                        evidence=TransportEvidence(
                            transport="stdio",
                            partial=True,
                            error_kind="startup_failure",
                        ),
                    )
            else:
                values[key] = value
            if "\x00" in values[key]:
                raise TransportStartupError(
                    "stdio environment contains an invalid value",
                    evidence=TransportEvidence(
                        transport="stdio", partial=True, error_kind="startup_failure"
                    ),
                )
            if self._secret_observer is not None and (
                isinstance(value, SecretReference) or is_sensitive_key(key)
            ):
                try:
                    self._secret_observer(values[key])
                except BaseException:
                    raise TransportStartupError(
                        "stdio secret redaction setup failed",
                        evidence=TransportEvidence(
                            transport="stdio",
                            partial=True,
                            error_kind="startup_failure",
                        ),
                    ) from None
        return values

    def _cwd(self) -> str | None:
        configured = self._server.cwd
        if configured is None:
            return self._workspace_root
        try:
            path = Path(configured)
            if not path.is_absolute():
                raise ValueError
            resolved = path.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError
        except (OSError, RuntimeError, ValueError):
            raise TransportStartupError(
                "stdio cwd is not an existing directory",
                evidence=TransportEvidence(
                    transport="stdio", partial=True, error_kind="startup_failure"
                ),
            ) from None
        return str(resolved)

    async def open(self) -> _StdioConnection:
        if "\x00" in self._server.command or any(
            "\x00" in arg for arg in self._server.args
        ):
            raise TransportStartupError(
                "stdio command contains an invalid argument",
                evidence=TransportEvidence(
                    transport="stdio", partial=True, error_kind="startup_failure"
                ),
            )
        try:
            environment = self._environment()
            parameters = StdioServerParameters(
                command=self._server.command,
                args=list(self._server.args),
                env=environment,
                cwd=self._cwd(),
            )
            self._secret_resolver = None
            self._secret_observer = None
            try:
                self._server = self._server.model_copy(update={"environment": {}})
            except BaseException:
                raise TransportStartupError(
                    "stdio secret redaction setup failed",
                    evidence=TransportEvidence(
                        transport="stdio", partial=True, error_kind="startup_failure"
                    ),
                ) from None
            stack = AsyncExitStack()
            errlog = open(os.devnull, "w", encoding="utf-8")
            stack.callback(errlog.close)
            try:
                streams = await stack.enter_async_context(
                    stdio_client(parameters, errlog=errlog)
                )
            except asyncio.CancelledError:
                await stack.aclose()
                raise
            except Exception:
                await stack.aclose()
                raise
            connection = _StdioConnection(
                stack=stack, owner_task=asyncio.current_task()
            )
            connection.set_streams(streams)
            return connection
        except LocalTransportError:
            raise
        except Exception:
            raise TransportStartupError(
                "stdio MCP server startup failed",
                evidence=TransportEvidence(
                    transport="stdio", partial=True, error_kind="startup_failure"
                ),
            ) from None

    async def __aenter__(self) -> _StdioConnection:
        self._connection = await self.open()
        return self._connection

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        connection = self._connection
        if connection is not None:
            await connection.close()


__all__ = [
    "InProcessMCPTransport",
    "LocalTransportError",
    "StdioMCPTransport",
    "TransportClosed",
    "TransportConnection",
    "TransportEvidence",
    "TransportProcessError",
    "TransportStartupError",
    "current_workspace_root",
]
