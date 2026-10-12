"""Real Win32 pipe I/O: these tests never replace native calls with mocks."""

from __future__ import annotations

import ctypes
import os
import secrets
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from m3._literal_store import LiteralTransferError, _receive, _send
from m3._literal_windows import (
    WindowsChannel,
    WindowsListener,
    _SecurityAttributes,
    api,
    collect_quarantine,
    connect,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="requires real Win32 native I/O"
)


def _accepted(listener: WindowsListener) -> WindowsChannel:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            return listener.accept()[0]
        except TimeoutError:
            pass
    raise AssertionError("native pipe did not accept client")


@pytest.fixture
def listener() -> Iterator[WindowsListener]:
    value = WindowsListener()
    try:
        yield value
    finally:
        deadline = time.monotonic() + 2
        while True:
            try:
                value.cleanup()
                break
            except LiteralTransferError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        assert collect_quarantine() == 0


def test_real_subprocess_bounded_frames_and_current_user_acl(
    listener: WindowsListener,
) -> None:
    # Worker receives endpoint only; the test frame is deterministic public data.
    code = """
import sys,time
from m3._literal_windows import connect
from m3._literal_store import _send,_receive
with connect(sys.argv[1],time.monotonic()+5) as channel:
    _send(channel, {'version':1,'message':'x'*200000},time.monotonic()+5)
    assert _receive(channel,time.monotonic()+5) == {'version':1,'accepted':True}
print('ok')
"""
    worker = subprocess.Popen(
        [sys.executable, "-c", code, listener.endpoint],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with _accepted(listener) as channel:
            api().assert_private(channel.handle, pipe=True)
            request = _receive(channel, time.monotonic() + 5)
            assert request == {"version": 1, "message": "x" * 200000}
            _send(channel, {"version": 1, "accepted": True}, time.monotonic() + 5)
        output, error = worker.communicate(timeout=5)
        assert worker.returncode == 0, error
        assert output.strip() == "ok"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)


def test_poll_timeout_preserves_pending_native_connect(
    listener: WindowsListener,
) -> None:
    original = listener._pending
    for _ in range(3):
        with pytest.raises(TimeoutError):
            listener.accept()
        assert listener._pending is original
        assert original.handle
    with connect(listener.endpoint, time.monotonic() + 5) as client:
        with _accepted(listener) as server:
            assert server is original
            client.settimeout(1)
            client.sendall(b"ready")
            server.settimeout(1)
            assert server.recv(5) == b"ready"


def test_stalled_client_does_not_block_twelve_other_native_clients(
    listener: WindowsListener,
) -> None:
    stalled = connect(listener.endpoint, time.monotonic() + 5)
    stalled_server = _accepted(listener)

    def client(index: int) -> int:
        with connect(listener.endpoint, time.monotonic() + 5) as channel:
            _send(channel, {"index": index}, time.monotonic() + 5)
            return int(_receive(channel, time.monotonic() + 5)["index"])

    def transfer(channel: WindowsChannel) -> None:
        with channel:
            request = _receive(channel, time.monotonic() + 5)
            _send(channel, request, time.monotonic() + 5)

    try:
        with ThreadPoolExecutor(max_workers=24) as pool:
            clients = [pool.submit(client, index) for index in range(12)]
            transfers = [pool.submit(transfer, _accepted(listener)) for _ in range(12)]
            assert [future.result(timeout=6) for future in clients] == list(range(12))
            for future in transfers:
                future.result(timeout=6)
        assert stalled_server.handle
    finally:
        stalled.close()
        stalled_server.close()


def test_native_read_timeout_keeps_buffers_owned_until_cancel_completion(
    listener: WindowsListener,
) -> None:
    with connect(listener.endpoint, time.monotonic() + 5):
        server = _accepted(listener)
        server.settimeout(0.05)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            server.recv(256)
        assert time.monotonic() - started < 0.5
        server.close()
        deadline = time.monotonic() + 1
        while server.handle and time.monotonic() < deadline:
            collect_quarantine()
            time.sleep(0.01)
        assert not server.handle
        assert not server._operations


def test_close_cancels_active_native_read_without_releasing_live_event(
    listener: WindowsListener,
) -> None:
    with connect(listener.endpoint, time.monotonic() + 5):
        server = _accepted(listener)
        server.settimeout(30)
        settled = threading.Event()

        def read() -> None:
            try:
                server.recv(256)
            except OSError:
                pass
            finally:
                settled.set()

        thread = threading.Thread(target=read)
        thread.start()
        deadline = time.monotonic() + 2
        while not server._operations and time.monotonic() < deadline:
            time.sleep(0.001)
        assert server._operations
        started = time.monotonic()
        server.close()
        assert time.monotonic() - started < 0.5
        assert settled.wait(1)
        thread.join(timeout=1)
        collect_quarantine()
        assert not server.handle
        assert not server._operations


def test_oversized_native_frame_rejected_and_next_connection_works(
    listener: WindowsListener,
) -> None:
    with connect(listener.endpoint, time.monotonic() + 5) as client:
        with _accepted(listener) as server:
            client.settimeout(1)
            client.sendall(struct.pack("!I", 8 * 1024 * 1024 + 1))
            with pytest.raises(LiteralTransferError):
                _receive(server, time.monotonic() + 1)
    with connect(listener.endpoint, time.monotonic() + 5) as client:
        with _accepted(listener) as server:
            _send(client, {"ok": True}, time.monotonic() + 1)
            assert _receive(server, time.monotonic() + 1) == {"ok": True}


def test_native_client_rejects_explicit_everyone_acl() -> None:
    native = api()
    descriptor = ctypes.c_void_p()
    sddl = f"O:{native.sid_text}D:P(A;;GA;;;{native.sid_text})(A;;GR;;;WD)"
    assert native.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    )
    attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, 0)
    endpoint = "\\\\.\\pipe\\m3-lit-" + secrets.token_hex(32)
    try:
        handle = native.kernel.CreateNamedPipeW(
            endpoint,
            3 | 0x40000000 | 0x00080000,
            8,
            1,
            65536,
            65536,
            50,
            ctypes.byref(attributes),
        )
    finally:
        native.kernel.LocalFree(descriptor)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(LiteralTransferError, match="not private"):
            connect(endpoint, time.monotonic() + 1)
    finally:
        native.kernel.CloseHandle(handle)


@pytest.mark.parametrize(
    "endpoint", [r"\\remote\pipe\m3-lit-test", "localhost:1234", r"\\.\pipe\other"]
)
def test_remote_or_unrecognized_endpoint_rejected_before_native_open(
    endpoint: str,
) -> None:
    with pytest.raises(LiteralTransferError, match="must be local"):
        connect(endpoint, time.monotonic() + 1)


def test_real_pending_connect_io_incomplete_keeps_kernel_storage(
    listener: WindowsListener,
) -> None:
    channel = listener._pending
    operation = channel._accept_operation
    assert operation is not None
    # This is an actual pending ConnectNamedPipe, without a peer. The documented
    # non-waiting query returns ERROR_IO_INCOMPLETE; no API result is mocked.
    with pytest.raises(OSError):
        channel._complete(operation)
    assert operation in channel._operations
    assert api().kernel.WaitForSingleObject(operation.overlapped.event, 0) == 258
    with connect(listener.endpoint, time.monotonic() + 5):
        with _accepted(listener) as accepted:
            assert accepted is channel
            assert operation not in channel._operations


def test_late_native_security_validation_cannot_accept_expired_connection(
    listener: WindowsListener, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = api()
    original = native.assert_private

    def validate(handle: int, *, pipe: bool = False) -> None:
        original(handle, pipe=pipe)
        time.sleep(0.06)

    # Control only the delay after real successful native validation. This is
    # failure-path deadline evidence, not substituted native I/O success.
    monkeypatch.setattr(native, "assert_private", validate)
    with pytest.raises(TimeoutError):
        connect(listener.endpoint, time.monotonic() + 0.03)
    # The rejected client handle was closed, so the server sees EOF.
    monkeypatch.setattr(native, "assert_private", original)
    with _accepted(listener) as server:
        server.settimeout(1)
        assert server.recv(1) == b""


def test_close_during_native_instance_creation_is_bounded_and_fences_late_channel(
    listener: WindowsListener, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()
    original = listener._instance
    late: list[WindowsChannel] = []
    errors: list[BaseException] = []

    def create(*, first: bool = False) -> WindowsChannel:
        started.set()
        assert release.wait(3)
        channel = original(first=first)
        late.append(channel)
        return channel

    def accept() -> None:
        try:
            listener.accept()
        except BaseException as exc:
            errors.append(exc)

    # The reservation is delayed, then it creates a real native instance after
    # close. The production close path must not wait on this coordination lock.
    monkeypatch.setattr(listener, "_instance", create)
    with connect(listener.endpoint, time.monotonic() + 5):
        thread = threading.Thread(target=accept)
        thread.start()
        try:
            assert started.wait(1)
            before = time.monotonic()
            listener.close()
            assert time.monotonic() - before < 0.5
            assert not listener.cleanup_complete
            with pytest.raises(LiteralTransferError, match="incomplete"):
                listener.cleanup()
        finally:
            release.set()
            thread.join(timeout=3)
        assert not thread.is_alive()
        assert errors and isinstance(errors[0], OSError)
        assert len(late) == 1
        deadline = time.monotonic() + 1
        while late[0].handle and time.monotonic() < deadline:
            collect_quarantine()
            time.sleep(0.01)
        assert not late[0].handle
        assert listener._pending is not late[0]
