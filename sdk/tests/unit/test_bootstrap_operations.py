from __future__ import annotations

import array
import errno
import json
import os
import pickle
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from m3 import _bootstrap_operations as operations
from m3._bootstrap_operations import (
    OwnedBootstrapOperations,
    PrivateBytes,
    PrivateOperationError,
)
from m3._literal_store import (
    LiteralBootstrapBroker,
    LiteralTransferError,
    RunLiteralStore,
    bootstrap_worker,
)
from m3._preparation_owner import PreparationWorker
from m3._preparation_worker import _group_alive

pytestmark = [
    pytest.mark.process_lifecycle,
    pytest.mark.skipif(os.name != "posix", reason="POSIX private operations"),
]
FIXTURES = Path(__file__).parents[1] / "fixtures"


def wait_for(predicate: Any, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_private_bytes_are_hidden_single_use_and_not_pickleable() -> None:
    value = PrivateBytes(b"private-capability")
    assert "private-capability" not in repr(value)
    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(value)
    assert value.consume() == b"private-capability"
    with pytest.raises(PrivateOperationError, match="consumed"):
        value.consume()


def test_native_sender_close_requires_receipt_on_macos(tmp_path: Path) -> None:
    # A short fixture root avoids the platform's Unix socket pathname limit.
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="m3-fd-test-", dir="/tmp"))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(root / "ipc"))
    listener.listen()
    observations: dict[str, bool] = {}
    try:
        for mode in ("early", "ack"):
            errors: list[int | None] = []

            def serve(failures: list[int | None]) -> None:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(5)
                    try:
                        assert connection.recv(16) == b"hello"
                        connection.sendall(b"reply")
                    except OSError as error:
                        failures.append(error.errno)

            server = threading.Thread(target=serve, args=(errors,))
            server.start()
            local, remote = socket.socketpair()
            process: subprocess.Popen[bytes] | None = None
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(FIXTURES / "private_descriptor_sender.py"),
                        str(remote.fileno()),
                        str(root / "ipc"),
                        mode,
                    ],
                    pass_fds=(remote.fileno(),),
                )
                remote.close()
                if mode == "early":
                    assert process.wait(timeout=5) == 0
                packet, rights, flags, _address = local.recvmsg(1, socket.CMSG_SPACE(4))
                assert packet == b"x" and not flags
                descriptors = array.array("i")
                descriptors.frombytes(rights[0][2])
                assert len(descriptors) == 1
                with socket.socket(fileno=descriptors[0]) as connected:
                    if mode == "ack":
                        local.sendall(b"a")
                        assert process.wait(timeout=5) == 0
                    connected.settimeout(5)
                    connected.sendall(b"hello")
                    observations[mode] = connected.recv(16) == b"reply"
            finally:
                local.close()
                remote.close()
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                server.join(timeout=5)
                assert not server.is_alive()
            if mode == "early" and sys.platform == "darwin":
                assert not observations[mode]
                assert errors == [errno.EPIPE]
        assert observations["ack"]
    finally:
        listener.close()
        (root / "ipc").unlink()
        root.rmdir()


def test_capsule_bytes_never_enter_generic_preparation_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OwnedBootstrapOperations()
    controls: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    original_send, original_deliver = PreparationWorker.send, PreparationWorker._deliver

    def send(worker: PreparationWorker, action: str, ident: str, **values: Any) -> None:
        controls.append({"action": action, "id": ident, **values})
        original_send(worker, action, ident, **values)

    def deliver(worker: PreparationWorker, frame: dict[str, Any]) -> None:
        events.append(frame)
        original_deliver(worker, frame)

    monkeypatch.setattr(PreparationWorker, "send", send)
    monkeypatch.setattr(PreparationWorker, "_deliver", deliver)
    try:
        capsule = provider.create_capsule(
            PrivateBytes(b"private-capability"), deadline=time.monotonic() + 5
        )
        assert stat.S_IMODE(capsule.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(capsule.path.stat().st_mode) == 0o600
        assert (
            provider.read_and_unlink(
                capsule.path, deadline=time.monotonic() + 2
            ).consume()
            == b"private-capability"
        )
        assert not capsule.path.exists()
    finally:
        provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete
    assert "private-capability" not in json.dumps(controls + events)
    assert all("bytes" not in frame.get("payload", {}) for frame in controls)
    assert all(
        frame.get("data", {}) == {} for frame in events if frame["event"] == "ready"
    )


def test_native_auth_absent_read_and_owned_home_cleanup(tmp_path: Path) -> None:
    provider = OwnedBootstrapOperations()
    source = tmp_path / "auth.json"
    try:
        assert provider.read_native_auth(source, deadline=time.monotonic() + 5) is None
        source.write_bytes(b'{"token":"frozen-token"}')
        source.chmod(0o600)
        value = provider.read_native_auth(source, deadline=time.monotonic() + 2)
        assert value is not None and source.exists()
        resource = provider.stage_native_auth(value, deadline=time.monotonic() + 2)
        assert resource.path.name == "auth.json"
        assert resource.path.read_bytes() == source.read_bytes()
        (resource.root / "config.toml").write_text("native fixture output")
        source.chmod(0o644)
        with pytest.raises(PrivateOperationError):
            provider.read_native_auth(source, deadline=time.monotonic() + 2)
    finally:
        provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete and not resource.root.exists()


def test_facade_close_preserves_other_owner_material() -> None:
    first, second = OwnedBootstrapOperations(), OwnedBootstrapOperations()
    try:
        left = first.create_capsule(
            PrivateBytes(b"left"), deadline=time.monotonic() + 5
        )
        right = second.create_capsule(
            PrivateBytes(b"right"), deadline=time.monotonic() + 2
        )
        assert first._worker is second._worker
        first.close(deadline=time.monotonic() + 2)
        assert first.cleanup_complete and not left.root.exists()
        assert right.path.read_bytes() == b"right"
    finally:
        first.close(deadline=time.monotonic() + 2)
        second.close(deadline=time.monotonic() + 2)
    assert second.cleanup_complete


def test_remove_checks_exact_capsule_inode_before_deleting() -> None:
    provider = OwnedBootstrapOperations()
    resource = provider.create_capsule(
        PrivateBytes(b"original"), deadline=time.monotonic() + 5
    )
    saved = resource.path.with_name("original")
    resource.path.rename(saved)
    resource.path.write_bytes(b"replacement")
    resource.path.chmod(0o600)
    try:
        with pytest.raises(PrivateOperationError):
            provider.remove(resource, deadline=time.monotonic() + 2)
        assert resource.path.read_bytes() == b"replacement"
        assert saved.read_bytes() == b"original"
    finally:
        resource.path.unlink()
        saved.rename(resource.path)
        provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete


def test_remove_accepts_already_removed_owned_root() -> None:
    provider = OwnedBootstrapOperations()
    resource = provider.create_capsule(
        PrivateBytes(b"original"), deadline=time.monotonic() + 5
    )
    resource.path.unlink()
    resource.root.rmdir()
    provider.remove(resource, deadline=time.monotonic() + 2)
    provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete


def test_close_does_not_start_second_removal_before_writer_death(
    blocked_helper: Any,
) -> None:
    worker, arm, marker, _fifo, _late = blocked_helper("remove")
    provider = OwnedBootstrapOperations()
    resource = provider.create_capsule(
        PrivateBytes(b"original"), deadline=time.monotonic() + 2
    )
    with pytest.raises(PrivateOperationError, match="deadline"):
        provider.remove(resource, deadline=time.monotonic() + 0.2)
    state = json.loads(marker.read_text())
    assert _group_alive(state["pid"])
    removal_jobs = tuple(provider._jobs)
    provider.close(deadline=time.monotonic() + 0.02)
    assert tuple(provider._jobs) == removal_jobs
    assert resource.path.exists() and not provider.cleanup_complete
    wait_for(lambda: not _group_alive(state["pid"]))
    wait_for(lambda: not provider._jobs)
    assert resource.path.exists()
    arm.unlink()
    provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete and not resource.root.exists()
    assert not worker.failed


def test_missing_native_handle_receipt_fences_and_closes_received_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OwnedBootstrapOperations()
    provider.create_capsule(PrivateBytes(b"warm"), deadline=time.monotonic() + 5)
    observed: list[int] = []

    def decline(channel: socket.socket, result: Any, deadline: float) -> None:
        if result.channel is None:
            return
        observed.append(result.channel.fileno())
        # The real child remains in recv(1) awaiting its same-deadline ACK.

    monkeypatch.setattr(operations, "_acknowledge_handle", decline)
    try:
        start = time.monotonic()
        with pytest.raises(PrivateOperationError, match="deadline"):
            provider.create_endpoint(deadline=start + 0.2)
        assert time.monotonic() - start < 0.6
        assert len(observed) == 1
        with pytest.raises(OSError) as error:
            os.fstat(observed[0])
        assert error.value.errno == errno.EBADF
    finally:
        provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete


def test_stalled_write_deadline_retains_ownership_until_writer_death(
    blocked_helper: Any,
) -> None:
    worker, _arm, marker, _fifo, _late = blocked_helper()
    provider = OwnedBootstrapOperations()
    start = time.monotonic()
    with pytest.raises(PrivateOperationError, match="deadline"):
        provider.create_capsule(PrivateBytes(b"never-published"), deadline=start + 0.2)
    assert time.monotonic() - start < 0.6
    state = json.loads(marker.read_text())
    root = Path(state["root"])
    assert _group_alive(state["pid"])
    assert root.exists()
    assert provider._resources == []
    provider.close(deadline=time.monotonic() + 0.02)
    wait_for(lambda: not _group_alive(state["pid"]))
    wait_for(lambda: not root.exists())
    wait_for(lambda: provider.cleanup_complete)
    assert not worker.failed


def test_close_fences_real_stalled_write_before_late_completion(
    blocked_helper: Any,
) -> None:
    _worker, _arm, marker, fifo, late = blocked_helper()
    provider = OwnedBootstrapOperations()
    failures: list[BaseException] = []

    def write() -> None:
        try:
            provider.create_capsule(
                PrivateBytes(b"late-auth"), deadline=time.monotonic() + 5
            )
        except BaseException as error:
            failures.append(error)

    writer = threading.Thread(target=write)
    writer.start()
    wait_for(marker.exists)
    state = json.loads(marker.read_text())
    root = Path(state["root"])
    start = time.monotonic()
    provider.close(deadline=start + 0.02)
    assert time.monotonic() - start < 0.3
    assert _group_alive(state["pid"])
    assert root.exists() and not provider.cleanup_complete
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        wait_for(late.exists)
    finally:
        os.close(reader)
    writer.join(timeout=2)
    assert not writer.is_alive() and len(failures) == 1
    assert isinstance(failures[0], PrivateOperationError)
    assert "late-auth" not in str(failures[0])
    assert provider._resources == []
    wait_for(lambda: not _group_alive(state["pid"]))
    wait_for(lambda: not root.exists())
    wait_for(lambda: provider.cleanup_complete)


def test_stalled_read_uses_same_bootstrap_deadline(blocked_helper: Any) -> None:
    _worker, arm, marker, _fifo, _late = blocked_helper("read")
    arm.unlink()
    store = RunLiteralStore("run")
    broker = LiteralBootstrapBroker(store)
    descriptor = broker.issue([store.freeze("frozen")])
    arm.touch()
    start = time.monotonic()
    try:
        with pytest.raises(LiteralTransferError, match="deadline"):
            bootstrap_worker(descriptor, deadline=start + 0.2)
        assert time.monotonic() - start < 0.6
        state = json.loads(marker.read_text())
        assert _group_alive(state["pid"])
        wait_for(lambda: not _group_alive(state["pid"]))
    finally:
        arm.unlink(missing_ok=True)
        broker.close()
        store.close()
    assert broker.cleanup_complete


def test_supplied_absolute_deadline_is_not_reset_by_issue_or_bootstrap() -> None:
    store = RunLiteralStore("run")
    broker = LiteralBootstrapBroker(store)
    handle = store.freeze("frozen")
    try:
        expired = time.monotonic() - 1
        with pytest.raises(LiteralTransferError, match="deadline"):
            broker.issue([handle], deadline=expired)
        descriptor = broker.issue([handle], deadline=time.monotonic() + 2)
        with pytest.raises(LiteralTransferError, match="deadline"):
            bootstrap_worker(descriptor, deadline=expired)
        assert Path(descriptor.capsule).exists()
        worker = bootstrap_worker(descriptor, deadline=time.monotonic() + 2)
        assert worker.resolve(handle) == "frozen"
        worker.close()
    finally:
        broker.close()
        store.close()
    assert broker.cleanup_complete


def test_rejected_native_result_closes_received_fd_and_owned_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warm = OwnedBootstrapOperations()
    warm.create_capsule(PrivateBytes(b"warm"), deadline=time.monotonic() + 5)
    warm.close(deadline=time.monotonic() + 2)
    provider = OwnedBootstrapOperations()
    received: list[int] = []
    roots: list[Path] = []
    original_decode = operations._decode_result

    def reject(
        operation: str, root: Path, frame: dict[str, Any], descriptor: int | None
    ) -> Any:
        if operation == "private_endpoint":
            assert descriptor is not None
            received.append(descriptor)
            roots.append(root)
            raise PrivateOperationError("invalid private bootstrap result")
        return original_decode(operation, root, frame, descriptor)

    monkeypatch.setattr(operations, "_decode_result", reject)
    try:
        with pytest.raises(PrivateOperationError, match="invalid"):
            provider.create_endpoint(deadline=time.monotonic() + 2)
        assert len(received) == 1
        with pytest.raises(OSError) as error:
            os.fstat(received[0])
        assert error.value.errno == errno.EBADF
    finally:
        provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete and not roots[0].exists()


def test_close_after_fd_receipt_prevents_native_handle_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warm = OwnedBootstrapOperations()
    warm.create_capsule(PrivateBytes(b"warm"), deadline=time.monotonic() + 5)
    warm.close(deadline=time.monotonic() + 2)
    provider = OwnedBootstrapOperations()
    received: list[int] = []
    original_acknowledge = operations._acknowledge_handle

    def close_before_ack(channel: socket.socket, result: Any, deadline: float) -> None:
        assert result.channel is not None
        received.append(result.channel.fileno())
        provider.close(deadline=time.monotonic() + 0.02)
        original_acknowledge(channel, result, deadline)

    monkeypatch.setattr(operations, "_acknowledge_handle", close_before_ack)
    try:
        with pytest.raises(PrivateOperationError):
            provider.create_endpoint(deadline=time.monotonic() + 2)
        assert provider._resources == []
        assert len(received) == 1
        with pytest.raises(OSError) as error:
            os.fstat(received[0])
        assert error.value.errno == errno.EBADF
    finally:
        provider.close(deadline=time.monotonic() + 2)
    assert provider.cleanup_complete
