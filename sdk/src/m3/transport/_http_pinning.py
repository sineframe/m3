"""HTTP transports that validate DNS at the TCP connection boundary."""

from __future__ import annotations

import ipaddress
import threading
import time
from collections.abc import Callable
from typing import Any

import anyio
import httpcore
import httpcore2
import httpx
import httpx2
import idna

AddressResolver = Callable[[str, int], tuple[str, ...]]
MAX_ADDRESS_CANDIDATES = 8
MAX_PARALLEL_CONNECTS = 2


def canonical_hostname(hostname: str) -> str:
    """Return one comparable ASCII representation for Unicode and IDNA hosts."""

    try:
        return str(ipaddress.ip_address(hostname)).lower()
    except ValueError:
        pass
    try:
        return idna.encode(hostname.rstrip(".").lower()).decode("ascii")
    except idna.IDNAError as exc:
        raise RuntimeError("HTTP request hostname is invalid") from exc


def _default_port(scheme: str) -> int:
    return 443 if scheme.lower() == "https" else 80


class _ValidatingNetworkBackend:
    """Resolve, validate, and connect without a second hostname lookup."""

    def __init__(
        self,
        delegate: Any,
        *,
        resolve_addresses: AddressResolver,
        connect_error: type[Exception],
        connect_timeout: type[Exception],
        retryable_errors: tuple[type[Exception], ...],
        first_connect_deadline: float | None = None,
    ) -> None:
        self._delegate = delegate
        self._resolve_addresses = resolve_addresses
        self._connect_error = connect_error
        self._connect_timeout = connect_timeout
        self._retryable_errors = retryable_errors
        self._first_connect_deadline = first_connect_deadline
        self._deadline_lock = threading.Lock()

    def _effective_timeout(self, timeout: float | None) -> float | None:
        with self._deadline_lock:
            first_deadline = self._first_connect_deadline
            self._first_connect_deadline = None
        if first_deadline is None:
            return timeout
        remaining = max(0.0, first_deadline - time.monotonic())
        return remaining if timeout is None else min(timeout, remaining)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        timeout = self._effective_timeout(timeout)
        deadline = anyio.current_time() + timeout if timeout is not None else None
        try:
            with anyio.fail_after(timeout):
                try:
                    addresses = await anyio.to_thread.run_sync(
                        self._resolve_addresses,
                        host,
                        port,
                        abandon_on_cancel=True,
                    )
                except Exception as exc:
                    raise self._connect_error(
                        "destination rejected by endpoint policy"
                    ) from exc
                if not addresses:
                    raise self._connect_error("destination did not resolve")
                candidates = self._numeric_candidates(addresses)
                remaining = (
                    max(0.0, deadline - anyio.current_time())
                    if deadline is not None
                    else None
                )
                return await self._race_connections(
                    candidates,
                    port=port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
        except TimeoutError as exc:
            raise self._connect_timeout("connection timed out") from exc

    def _numeric_candidates(self, addresses: tuple[str, ...]) -> tuple[str, ...]:
        candidates: list[str] = []
        for address in addresses:
            try:
                numeric = str(ipaddress.ip_address(address))
            except ValueError as exc:
                raise self._connect_error(
                    "resolver returned a non-numeric destination"
                ) from exc
            if numeric not in candidates:
                candidates.append(numeric)
            if len(candidates) == MAX_ADDRESS_CANDIDATES:
                break
        return tuple(candidates)

    async def _race_connections(
        self,
        addresses: tuple[str, ...],
        *,
        port: int,
        timeout: float | None,
        local_address: str | None,
        socket_options: Any,
    ) -> Any:
        """Return the first successful validated address connection."""

        winner: list[Any] = []
        errors: list[Exception] = []
        completed = anyio.Event()
        next_index = 0
        finished_workers = 0
        worker_count = min(MAX_PARALLEL_CONNECTS, len(addresses))
        rounds = (len(addresses) + worker_count - 1) // worker_count
        attempt_timeout = timeout / rounds if timeout is not None else None

        async def worker() -> None:
            nonlocal finished_workers, next_index
            try:
                while not winner and next_index < len(addresses):
                    address = addresses[next_index]
                    next_index += 1
                    try:
                        # Numeric addresses make delegated connects deterministic;
                        # TLS still gets the logical hostname from the request.
                        with anyio.fail_after(attempt_timeout):
                            stream = await self._delegate.connect_tcp(
                                address,
                                port,
                                timeout=attempt_timeout,
                                local_address=local_address,
                                socket_options=socket_options,
                            )
                        if winner:
                            with anyio.CancelScope(shield=True):
                                await stream.aclose()
                        else:
                            winner.append(stream)
                            completed.set()
                    except TimeoutError as exc:
                        errors.append(self._connect_timeout("connection timed out"))
                        errors[-1].__cause__ = exc
                    except self._retryable_errors as exc:
                        errors.append(exc)
            finally:
                finished_workers += 1
                if finished_workers == worker_count:
                    completed.set()

        async with anyio.create_task_group() as task_group:
            for _ in range(worker_count):
                task_group.start_soon(worker)
            await completed.wait()
            if winner:
                task_group.cancel_scope.cancel()

        if winner:
            return winner[0]
        assert errors
        raise errors[-1]

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> Any:
        raise self._connect_error("Unix sockets are not valid remote HTTP destinations")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class _OriginPolicy:
    def __init__(
        self,
        *,
        scheme: str,
        hostname: str,
        port: int,
        allow_public_auth_origins: bool,
    ) -> None:
        self.scheme = scheme.lower()
        self.hostname = canonical_hostname(hostname)
        self.port = port
        self.allow_public_auth_origins = allow_public_auth_origins

    def validate(self, request: Any) -> None:
        scheme = request.url.scheme.lower()
        if scheme not in {"http", "https"}:
            raise RuntimeError("HTTP request escaped its validated upstream origin")
        hostname = canonical_hostname(request.url.host)
        port = request.url.port or _default_port(scheme)
        primary = hostname == self.hostname and port == self.port
        if primary and scheme != self.scheme:
            raise RuntimeError("HTTP request escaped its validated upstream origin")
        if not primary:
            if not self.allow_public_auth_origins or scheme != "https":
                raise RuntimeError("HTTP request escaped its validated upstream origin")


def _install_backend(
    transport: Any,
    *,
    resolve_addresses: AddressResolver,
    connect_error: type[Exception],
    connect_timeout: type[Exception],
    retryable_errors: tuple[type[Exception], ...],
    first_connect_deadline: float | None,
) -> None:
    pool = transport._pool
    pool._network_backend = _ValidatingNetworkBackend(
        pool._network_backend,
        resolve_addresses=resolve_addresses,
        connect_error=connect_error,
        connect_timeout=connect_timeout,
        retryable_errors=retryable_errors,
        first_connect_deadline=first_connect_deadline,
    )


class ValidatingHTTPXTransport(httpx.AsyncBaseTransport):
    """An httpx transport that retains logical URLs and validates every connect."""

    def __init__(
        self,
        *,
        scheme: str,
        hostname: str,
        port: int,
        resolve_addresses: AddressResolver,
        allow_public_auth_origins: bool = False,
        first_connect_deadline: float | None = None,
    ) -> None:
        self._origin = _OriginPolicy(
            scheme=scheme,
            hostname=hostname,
            port=port,
            allow_public_auth_origins=allow_public_auth_origins,
        )
        # Keep SSL_CERT_FILE/SSL_CERT_DIR support. The owning client disables
        # environment proxy mounts independently with trust_env=False.
        self._transport = httpx.AsyncHTTPTransport(trust_env=True)
        _install_backend(
            self._transport,
            resolve_addresses=resolve_addresses,
            connect_error=httpcore.ConnectError,
            connect_timeout=httpcore.ConnectTimeout,
            retryable_errors=(httpcore.ConnectError, httpcore.ConnectTimeout),
            first_connect_deadline=first_connect_deadline,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._origin.validate(request)
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


class ValidatingHTTPX2Transport(httpx2.AsyncBaseTransport):
    """An httpx2 transport that retains logical URLs and validates every connect."""

    def __init__(
        self,
        *,
        scheme: str,
        hostname: str,
        port: int,
        resolve_addresses: AddressResolver,
        allow_public_auth_origins: bool = False,
        first_connect_deadline: float | None = None,
    ) -> None:
        self._origin = _OriginPolicy(
            scheme=scheme,
            hostname=hostname,
            port=port,
            allow_public_auth_origins=allow_public_auth_origins,
        )
        self._transport = httpx2.AsyncHTTPTransport(trust_env=True)
        _install_backend(
            self._transport,
            resolve_addresses=resolve_addresses,
            connect_error=httpcore2.ConnectError,
            connect_timeout=httpcore2.ConnectTimeout,
            retryable_errors=(httpcore2.ConnectError, httpcore2.ConnectTimeout),
            first_connect_deadline=first_connect_deadline,
        )

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self._origin.validate(request)
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()
