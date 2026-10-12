"""Current-user-only Win32 named pipes, with owned overlapped I/O.

Uses documented CreateNamedPipeW/ReadFile/WriteFile/CancelIoEx APIs. Each
pending operation retains its buffer, event and OVERLAPPED until Windows
signals completion; timeout never frees storage still owned by the kernel.
No multiprocessing internals, pickle, TCP, or inherited/default ACLs.

This is a transport building block, not an enabled bootstrap backend. Native
pipe creation/open/security calls are synchronous: full bootstrap must run
those calls and capsule operations under the owned Windows helper. A caller
must not mistake overlapped transfer cancellation for ownership of setup.
"""

from __future__ import annotations

import ctypes
import math
import os
import secrets
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, cast

from ._literal_store import LiteralTransferError

_DWORD = ctypes.c_uint32
_WORD = ctypes.c_uint16
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p
_INVALID = ctypes.c_void_p(-1).value
_OVERLAPPED_FLAG = 0x40000000
_READ_CONTROL = 0x00020000
_READ = 0x80000000
_WRITE = 0x40000000
_PENDING = 997
_ABORTED = 995
_WAIT_TIMEOUT = 258
_BROKEN_PIPE = 109
_PIPE_BUSY = 231
_PIPE_CONNECTED = 535


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", _DWORD), ("descriptor", _HANDLE), ("inherit", _BOOL)]


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", _DWORD),
        ("offset_high", _DWORD),
        ("event", _HANDLE),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("sid", _HANDLE), ("attributes", _DWORD)]


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("size", _WORD),
        ("count", _WORD),
        ("reserved2", _WORD),
    ]


class _AllowedAce(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ubyte),
        ("flags", ctypes.c_ubyte),
        ("size", _WORD),
        ("mask", _DWORD),
        ("sid_start", _DWORD),
    ]


def _error() -> OSError:
    # Do not format native messages: paths and caller-controlled names can be
    # echoed by the OS. The public boundary remains value-free.
    return OSError("worker bootstrap native operation unavailable")


class _WindowsCtypes(Protocol):
    # The documented Windows-only ctypes symbols are absent from POSIX
    # typeshed. Cast the module surface after the explicit platform guard.
    def WinDLL(self, name: str, *, use_last_error: bool) -> Any: ...
    def get_last_error(self) -> int: ...


class _Api:
    def __init__(self) -> None:
        if os.name != "nt":
            raise LiteralTransferError("Windows literal backend unavailable")
        windows_ctypes = cast(_WindowsCtypes, ctypes)
        loader = windows_ctypes.WinDLL
        self.kernel = loader("kernel32", use_last_error=True)
        self.security = loader("advapi32", use_last_error=True)
        self.last_error: Callable[[], int] = windows_ctypes.get_last_error
        h, d, p, w, b = _HANDLE, _DWORD, ctypes.c_void_p, ctypes.c_wchar_p, _BOOL
        signatures: dict[str, tuple[Any, list[Any]]] = {
            "GetCurrentProcess": (h, []),
            "CloseHandle": (b, [h]),
            "LocalFree": (p, [p]),
            "CreateEventW": (h, [p, b, b, w]),
            "WaitForSingleObject": (d, [h, d]),
            "CreateNamedPipeW": (h, [w, d, d, d, d, d, d, p]),
            "ConnectNamedPipe": (b, [h, p]),
            "DisconnectNamedPipe": (b, [h]),
            "CreateFileW": (h, [w, d, d, p, d, d, h]),
            "ReadFile": (b, [h, p, d, p, p]),
            "WriteFile": (b, [h, p, d, p, p]),
            "CancelIoEx": (b, [h, p]),
            "GetOverlappedResult": (b, [h, p, p, b]),
            "WaitNamedPipeW": (b, [w, d]),
            "GetHandleInformation": (b, [h, p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = result, arguments
        security_signatures: dict[str, tuple[Any, list[Any]]] = {
            "OpenProcessToken": (b, [h, d, p]),
            "GetTokenInformation": (b, [h, ctypes.c_int, p, d, p]),
            "GetLengthSid": (d, [p]),
            "EqualSid": (b, [p, p]),
            "ConvertSidToStringSidW": (b, [p, p]),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (b, [w, d, p, p]),
            "GetSecurityInfo": (d, [h, ctypes.c_int, d, p, p, p, p, p]),
            "GetSecurityDescriptorControl": (b, [p, p, p]),
            "GetAce": (b, [p, d, p]),
        }
        for name, (result, arguments) in security_signatures.items():
            function = getattr(self.security, name)
            function.restype, function.argtypes = result, arguments
        token = _HANDLE()
        if not self.security.OpenProcessToken(
            self.kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)
        ):
            raise _error()
        try:
            length = _DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            if not 0 < length.value < 4096:
                raise _error()
            data = ctypes.create_string_buffer(length.value)
            if not self.security.GetTokenInformation(
                token, 1, data, length, ctypes.byref(length)
            ):
                raise _error()
            user = _TokenUser.from_buffer(data)
            sid_size = self.security.GetLengthSid(user.sid)
            if not 0 < sid_size <= 68:
                raise _error()
            self.sid = ctypes.create_string_buffer(ctypes.string_at(user.sid, sid_size))
            text = ctypes.c_wchar_p()
            if not self.security.ConvertSidToStringSidW(self.sid, ctypes.byref(text)):
                raise _error()
            try:
                if text.value is None:
                    raise _error()
                self.sid_text = text.value
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.kernel.CloseHandle(token)

    def attributes(self) -> tuple[_SecurityAttributes, _HANDLE]:
        descriptor = _HANDLE()
        # Protected DACL with exactly the token user, and an explicit owner.
        # Supplying NULL here would grant Everyone/Anonymous pipe read access.
        sddl = f"O:{self.sid_text}D:P(A;;GA;;;{self.sid_text})"
        if not self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise _error()
        return _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, 0
        ), descriptor

    def assert_private(self, handle: int, *, pipe: bool = False) -> None:
        owner, acl, descriptor = _HANDLE(), _HANDLE(), _HANDLE()
        code = self.security.GetSecurityInfo(
            handle,
            6 if pipe else 1,
            0x5,
            ctypes.byref(owner),
            None,
            ctypes.byref(acl),
            None,
            ctypes.byref(descriptor),
        )
        if code:
            raise _error()
        try:
            control, revision = _WORD(), _DWORD()
            if not self.security.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ):
                raise _error()
            if (
                not owner
                or not acl
                or not self.security.EqualSid(owner, self.sid)
                or not control.value & 0x1000
            ):
                raise LiteralTransferError("worker bootstrap path is not private")
            header = _Acl.from_address(acl.value or 0)
            if header.count != 1:
                raise LiteralTransferError("worker bootstrap path is not private")
            ace_pointer = _HANDLE()
            if not self.security.GetAce(acl, 0, ctypes.byref(ace_pointer)):
                raise _error()
            ace = _AllowedAce.from_address(ace_pointer.value or 0)
            if (
                ace.type != 0
                or ace.flags
                or ace.size < 16
                or not self.security.EqualSid((ace_pointer.value or 0) + 8, self.sid)
            ):
                raise LiteralTransferError("worker bootstrap path is not private")
            flags = _DWORD()
            if (
                not self.kernel.GetHandleInformation(handle, ctypes.byref(flags))
                or flags.value & 1
            ):
                raise LiteralTransferError("worker bootstrap handle is inheritable")
        finally:
            self.kernel.LocalFree(descriptor)


_api_lock = threading.Lock()
_api_instance: _Api | None = None


def api() -> _Api:
    global _api_instance
    with _api_lock:
        if _api_instance is None:
            _api_instance = _Api()
        return _api_instance


class _Operation:
    def __init__(self, buffer: Any) -> None:
        self.buffer = buffer
        self.active = True
        self.overlapped = _Overlapped()
        self.overlapped.event = api().kernel.CreateEventW(None, 1, 0, None)
        if not self.overlapped.event:
            raise _error()

    def close(self) -> None:
        api().kernel.CloseHandle(self.overlapped.event)


_quarantine_lock = threading.Lock()
_quarantine: set[WindowsChannel] = set()


def collect_quarantine() -> int:
    """Nonblocking collection of acknowledged native completions only."""
    with _quarantine_lock:
        channels = tuple(_quarantine)
    for channel in channels:
        channel._finish_close()
    with _quarantine_lock:
        return len(_quarantine)


class WindowsChannel:
    def __init__(self, handle: int) -> None:
        self.handle = handle
        self._timeout = 30.0
        self._lock = threading.Lock()
        self._operations: list[_Operation] = []
        self._closing = False
        self._connected = False
        self._accept_operation: _Operation | None = None

    def __repr__(self) -> str:
        return "WindowsChannel(<private native ownership>)"

    def settimeout(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("worker bootstrap timeout must be finite and nonnegative")
        self._timeout = seconds

    def _io(self, kind: str, buffer: Any, size: int) -> int:
        native = api()
        operation = _Operation(buffer)
        deadline = time.monotonic() + self._timeout
        with self._lock:
            if self._closing:
                operation.close()
                raise _error()
            self._operations.append(operation)
            if kind == "connect":
                immediate = native.kernel.ConnectNamedPipe(
                    self.handle, ctypes.byref(operation.overlapped)
                )
            else:
                function = (
                    native.kernel.ReadFile
                    if kind == "read"
                    else native.kernel.WriteFile
                )
                immediate = function(
                    self.handle, buffer, size, None, ctypes.byref(operation.overlapped)
                )
            error = native.last_error() if not immediate else 0
            if not immediate and error != _PENDING:
                self._operations.remove(operation)
                operation.close()
                if kind == "connect" and error == _PIPE_CONNECTED:
                    return 0
                if kind == "read" and error in (_BROKEN_PIPE, 38):
                    return 0
                raise _error()
        try:
            expired = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    native.kernel.CancelIoEx(
                        self.handle, ctypes.byref(operation.overlapped)
                    )
                    expired = True
                    break
                wait = native.kernel.WaitForSingleObject(
                    operation.overlapped.event,
                    min(50, max(1, math.ceil(remaining * 1000))),
                )
                if wait == 0:
                    break
                if wait != _WAIT_TIMEOUT:
                    native.kernel.CancelIoEx(
                        self.handle, ctypes.byref(operation.overlapped)
                    )
                    expired = True
                    break
            if expired:
                # Retain all kernel storage until cancellation is acknowledged.
                if (
                    native.kernel.WaitForSingleObject(operation.overlapped.event, 0)
                    == 0
                ):
                    self._complete(operation)
                raise TimeoutError("worker bootstrap native deadline exceeded")
            transferred = self._complete(operation)
            if time.monotonic() >= deadline:
                raise TimeoutError("worker bootstrap native deadline exceeded")
            return transferred
        finally:
            # Only the active waiter may consume its event/completion. Close
            # cancels immediately but never races that waiter to free storage.
            with self._lock:
                operation.active = False
                closing = self._closing
            if closing:
                self.close()

    def _complete(self, operation: _Operation) -> int:
        count = _DWORD()
        native = api()
        okay = native.kernel.GetOverlappedResult(
            self.handle, ctypes.byref(operation.overlapped), ctypes.byref(count), 0
        )
        error = native.last_error() if not okay else 0
        if not okay and error not in (_ABORTED, _BROKEN_PIPE, 38):
            # In particular ERROR_IO_INCOMPLETE is not acknowledgement. Keep
            # the buffer/event/OVERLAPPED until completion is observed.
            raise _error()
        with self._lock:
            if operation in self._operations:
                self._operations.remove(operation)
                operation.close()
        return int(count.value) if okay else 0

    def start_accept(self) -> None:
        """Start one persistent connect; polling never drops a arriving peer."""
        operation = _Operation(None)
        operation.active = False
        with self._lock:
            immediate = api().kernel.ConnectNamedPipe(
                self.handle, ctypes.byref(operation.overlapped)
            )
            error = api().last_error() if not immediate else 0
            if error == _PIPE_CONNECTED:
                operation.close()
                self._connected = True
                self._accept_operation = None
                return
            if error not in (0, _PENDING):
                operation.close()
                raise _error()
            self._connected = False
            self._operations.append(operation)
            self._accept_operation = operation

    def await_accept(self) -> None:
        with self._lock:
            if self._closing:
                raise _error()
            if self._connected:
                return
            operation = self._accept_operation
            if operation is None:
                raise _error()
            operation.active = True
        try:
            wait = api().kernel.WaitForSingleObject(operation.overlapped.event, 100)
            if wait == _WAIT_TIMEOUT:
                raise TimeoutError("worker bootstrap accept poll expired")
            if wait != 0:
                raise _error()
            self._complete(operation)
            with self._lock:
                if self._closing:
                    raise _error()
                self._connected = True
                self._accept_operation = None
        finally:
            with self._lock:
                operation.active = False
                closing = self._closing
            if closing:
                self.close()

    def recv(self, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        count = self._io("read", buffer, size)
        return bytes(buffer.raw[:count])

    def sendall(self, data: bytes) -> None:
        deadline = time.monotonic() + self._timeout
        offset = 0
        while offset < len(data):
            self._timeout = max(0, deadline - time.monotonic())
            chunk = data[offset : offset + 65536]
            buffer = ctypes.create_string_buffer(chunk)
            count = self._io("write", buffer, len(chunk))
            if not count:
                raise _error()
            offset += count

    def shutdown(self, how: int) -> None:
        del how
        with self._lock:
            self._closing = True
            if self.handle:
                api().kernel.CancelIoEx(self.handle, None)

    def _finish_close(self) -> bool:
        native = api()
        with self._lock:
            for operation in tuple(self._operations):
                if operation.active:
                    continue
                if (
                    native.kernel.WaitForSingleObject(operation.overlapped.event, 0)
                    == 0
                ):
                    # Acknowledged cancellation; no active waiter can touch it.
                    self._operations.remove(operation)
                    operation.close()
            if self._operations:
                return False
            if self.handle:
                native.kernel.CloseHandle(self.handle)
                self.handle = 0
        with _quarantine_lock:
            _quarantine.discard(self)
        return True

    def close(self) -> None:
        if self.handle:
            self.shutdown(0)
            if not self._finish_close():
                with _quarantine_lock:
                    _quarantine.add(self)

    def __enter__(self) -> WindowsChannel:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def connect(endpoint: str, deadline: float) -> WindowsChannel:
    prefix = "\\\\.\\pipe\\m3-lit-"
    suffix = endpoint[len(prefix) :]
    if (
        not endpoint.startswith(prefix)
        or len(suffix) != 64
        or any(c not in "0123456789abcdef" for c in suffix)
    ):
        raise LiteralTransferError("worker bootstrap endpoint must be local")
    native = api()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("worker bootstrap native deadline exceeded")
        # Identification SQOS prohibits a peer from impersonating the client.
        handle = native.kernel.CreateFileW(
            endpoint,
            _READ | _WRITE | _READ_CONTROL,
            0,
            None,
            3,
            _OVERLAPPED_FLAG | 0x00100000 | 0x00010000,
            None,
        )
        if handle not in (None, _INVALID):
            try:
                native.assert_private(handle, pipe=True)
                if time.monotonic() >= deadline:
                    raise TimeoutError("worker bootstrap native deadline exceeded")
                return WindowsChannel(int(handle))
            except BaseException:
                native.kernel.CloseHandle(handle)
                raise
        if native.last_error() != _PIPE_BUSY:
            raise _error()
        native.kernel.WaitNamedPipeW(
            endpoint, min(50, max(1, math.ceil(remaining * 1000)))
        )


class WindowsListener:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._channels: set[WindowsChannel] = set()
        self.endpoint = "\\\\.\\pipe\\m3-lit-" + secrets.token_hex(32)
        self._pending_creations = 0
        self._pending = self._instance(first=True)
        self._channels.add(self._pending)

    def _instance(self, *, first: bool = False) -> WindowsChannel:
        native = api()
        attributes, descriptor = native.attributes()
        try:
            handle = native.kernel.CreateNamedPipeW(
                self.endpoint,
                3 | _OVERLAPPED_FLAG | (0x00080000 if first else 0),
                8,
                66,
                65536,
                65536,
                50,
                ctypes.byref(attributes),
            )
        finally:
            native.kernel.LocalFree(descriptor)
        if handle in (None, _INVALID):
            raise _error()
        try:
            native.assert_private(handle, pipe=True)
            channel = WindowsChannel(int(handle))
        except BaseException:
            native.kernel.CloseHandle(handle)
            raise
        try:
            channel.start_accept()
        except BaseException:
            channel.close()
            raise
        return channel

    def accept(self) -> tuple[WindowsChannel, object]:
        with self._lock:
            if self._closed:
                raise _error()
            channel = self._pending
        channel.await_accept()
        with self._lock:
            closed = self._closed
            if not closed:
                self._pending_creations += 1
        if closed:
            channel.close()
            raise _error()
        next_channel: WindowsChannel | None = None
        try:
            # Native creation/security queries must never hold the broker's
            # coordination lock. Ownership is reserved before leaving it.
            next_channel = self._instance()
        except BaseException:
            channel.close()
            raise
        finally:
            with self._lock:
                self._pending_creations -= 1
                self._channels = {item for item in self._channels if item.handle}
                if next_channel is not None:
                    self._channels.add(next_channel)
                closed = self._closed
                if next_channel is not None and not closed:
                    self._pending = next_channel
            if closed:
                channel.close()
                if next_channel is not None:
                    next_channel.close()
        if closed:
            raise _error()
        return channel, None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            channels = tuple(self._channels)
        for channel in channels:
            channel.close()

    def cleanup(self) -> None:
        self.close()
        collect_quarantine()
        with self._lock:
            pending = self._pending_creations
            channels = tuple(self._channels)
        settled = all(channel._finish_close() for channel in channels)
        if pending or not settled:
            raise LiteralTransferError("worker bootstrap native cleanup incomplete")
        with self._lock:
            self._channels.clear()

    @property
    def cleanup_complete(self) -> bool:
        with self._lock:
            return (
                self._closed
                and not self._pending_creations
                and not any(channel.handle for channel in self._channels)
            )
