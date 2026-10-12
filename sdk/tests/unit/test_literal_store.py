from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from m3._literal_store import (
    LiteralBootstrapBroker,
    LiteralTransferError,
    RunLiteralStore,
    bootstrap_worker,
)


def test_registry_preserves_literals_and_rejects_foreign_handles() -> None:
    store = RunLiteralStore("run-a")
    handles = [store.freeze(value) for value in ("", 'a$"\\\nλ', "private-value")]
    assert [store.resolve(handle) for handle in handles] == [
        "",
        'a$"\\\nλ',
        "private-value",
    ]
    assert "private-value" not in repr(store)
    assert "private-value" not in json.dumps(handles[-1].metadata())
    with pytest.raises(LiteralTransferError, match="identity mismatch"):
        store.resolve(replace(handles[-1], run_id="other-run"))
    with pytest.raises(LiteralTransferError, match="manifest"):
        store.export([handles[0], handles[0]])
    store.close()
    with pytest.raises(LiteralTransferError, match="closed"):
        store.resolve(handles[0])


@pytest.fixture
def private_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "bootstrap"
    directory.mkdir(mode=0o700)
    return directory


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
def test_bootstrap_freezes_values_and_retains_worker_local_copy(
    private_directory: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source.toml"
    source.write_text("original-literal", encoding="utf-8")
    store = RunLiteralStore("run")
    handle = store.freeze(source.read_text(encoding="utf-8"))
    broker = LiteralBootstrapBroker(store, private_directory)
    try:
        descriptor = broker.issue([handle])
        source.unlink()
        capsule = Path(descriptor.capsule)
        assert "original-literal" not in capsule.read_text(encoding="utf-8")
        assert "capability" not in descriptor.metadata()
        worker = bootstrap_worker(descriptor)
        assert not capsule.exists()
        replacement = bootstrap_worker(broker.issue([handle]))
        assert replacement.resolve(handle) == "original-literal"
    finally:
        broker.close()
        store.close()
    assert worker.resolve(handle) == "original-literal"
    worker.close()
    replacement.close()
    assert list(private_directory.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
@pytest.mark.process_lifecycle
def test_real_worker_bootstrap_without_literals_in_process_arguments(
    private_directory: Path,
) -> None:
    store = RunLiteralStore("run")
    values = ["", 'quoted "\\$λ\n', "frozen-value"]
    handles = [store.freeze(value) for value in values]
    broker = LiteralBootstrapBroker(store, private_directory)
    try:
        descriptor = broker.issue(handles)
        script = (
            "import json,sys; from m3._literal_store import "
            "WorkerBootstrap,LiteralHandle,bootstrap_worker; "
            "d=json.loads(sys.argv[1]); d['slots']=tuple(d['slots']); "
            "d=WorkerBootstrap(**d); s=bootstrap_worker(d); "
            "print(json.dumps([s.resolve(LiteralHandle(d.run_id,d.generation,k)) "
            "for k in d.slots]))"
        )
        argv = [sys.executable, "-c", script, json.dumps(descriptor.metadata())]
        assert all(value not in argv[-1] for value in values if value)
        result = subprocess.run(
            argv, check=True, capture_output=True, text=True, timeout=5
        )
        assert json.loads(result.stdout) == values
    finally:
        broker.close()
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
def test_wrong_manifest_and_replay_fail_without_values(private_directory: Path) -> None:
    store = RunLiteralStore("run")
    handle = store.freeze("must-not-leak")
    broker = LiteralBootstrapBroker(store, private_directory, timeout_seconds=0.2)
    try:
        descriptor = broker.issue([handle])
        with pytest.raises(LiteralTransferError) as error:
            bootstrap_worker(replace(descriptor, generation="wrong"))
        assert "must-not-leak" not in str(error.value)
        descriptor = broker.issue([handle])
        capsule = Path(descriptor.capsule)
        original_capsule = capsule.read_bytes()
        worker = bootstrap_worker(descriptor)
        worker.close()
        capsule.write_bytes(original_capsule)
        capsule.chmod(0o600)
        with pytest.raises(LiteralTransferError):
            bootstrap_worker(descriptor, timeout_seconds=0.3)
    finally:
        broker.close()
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
def test_capsule_symlink_and_permissions_rejected(private_directory: Path) -> None:
    store = RunLiteralStore("run")
    broker = LiteralBootstrapBroker(store, private_directory)
    try:
        descriptor = broker.issue([store.freeze("value")])
        capsule = Path(descriptor.capsule)
        capsule.chmod(0o644)
        with pytest.raises(LiteralTransferError, match="private"):
            bootstrap_worker(descriptor)
        capsule.chmod(0o600)
        target = capsule.with_suffix(".target")
        capsule.rename(target)
        capsule.symlink_to(target)
        with pytest.raises(LiteralTransferError):
            bootstrap_worker(descriptor)
        assert target.exists()
        capsule.unlink()
        target.unlink()
    finally:
        broker.close()
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
def test_cleanup_interrupts_stalled_authenticated_transfer(
    private_directory: Path,
) -> None:
    store = RunLiteralStore("run")
    broker = LiteralBootstrapBroker(store, private_directory, timeout_seconds=30)
    descriptor = broker.issue([])
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(descriptor.endpoint)
    try:
        # A connection sending no frame must not hold teardown for 30 seconds.
        start = time.monotonic()
        broker.close()
        assert time.monotonic() - start < 1.5
    finally:
        client.close()
        store.close()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_deadlines_are_rejected(
    private_directory: Path, timeout: float
) -> None:
    store = RunLiteralStore("run")
    with pytest.raises(LiteralTransferError, match="finite and positive"):
        LiteralBootstrapBroker(store, private_directory, timeout_seconds=timeout)
    store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
def test_late_bundle_is_never_published_after_parse_deadline(
    private_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import m3._literal_store as literals

    store = RunLiteralStore("run")
    handle = store.freeze("frozen")
    broker = LiteralBootstrapBroker(store, private_directory)
    original_receive = literals._receive

    def delayed_receive(channel: socket.socket, deadline: float) -> dict[str, object]:
        result = original_receive(channel, deadline)
        if "values" in result:
            time.sleep(0.1)
        return result

    monkeypatch.setattr(literals, "_receive", delayed_receive)
    try:
        with pytest.raises(LiteralTransferError, match="deadline"):
            bootstrap_worker(broker.issue([handle]), timeout_seconds=0.05)
    finally:
        broker.close()
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
def test_pending_capsule_writer_cannot_hold_teardown_or_publish_after_close(
    private_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import m3._literal_store as literals

    store = RunLiteralStore("run")
    handle = store.freeze("private-literal")
    broker = LiteralBootstrapBroker(store, private_directory)
    writing = threading.Event()
    release = threading.Event()
    original_dump = literals.json.dump
    failures: list[BaseException] = []

    def stalled_dump(*args: object, **kwargs: object) -> None:
        writing.set()
        assert release.wait(5)
        original_dump(*args, **kwargs)

    def issue() -> None:
        try:
            broker.issue([handle])
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(literals.json, "dump", stalled_dump)
    writer = threading.Thread(target=issue)
    writer.start()
    try:
        assert writing.wait(5)
        start = time.monotonic()
        broker.close()
        assert time.monotonic() - start < 1.5
        assert not broker.cleanup_complete
        assert not broker._capabilities
    finally:
        release.set()
        writer.join(timeout=5)
        broker.close()
        store.close()
    assert not writer.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], LiteralTransferError)
    assert "private-literal" not in str(failures[0])
    assert broker.cleanup_complete
    assert list(private_directory.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
def test_stalled_client_does_not_block_concurrent_valid_workers(
    private_directory: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = RunLiteralStore("run")
    handle = store.freeze("frozen-value")
    broker = LiteralBootstrapBroker(store, private_directory, timeout_seconds=2)
    descriptors = [broker.issue([handle]) for _ in range(12)]
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    silent.connect(descriptors[0].endpoint)
    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [
                executor.submit(bootstrap_worker, descriptor, timeout_seconds=1)
                for descriptor in descriptors
            ]
            workers = [future.result(timeout=3) for future in futures]
        assert all(worker.resolve(handle) == "frozen-value" for worker in workers)
        for worker in workers:
            worker.close()
    finally:
        silent.close()
        broker.close()
        store.close()
    assert broker.cleanup_complete


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
@pytest.mark.parametrize("allocation", ["thread", "socket"])
def test_constructor_rolls_back_all_owned_resources(
    private_directory: Path, monkeypatch: pytest.MonkeyPatch, allocation: str
) -> None:
    import m3._literal_store as literals

    store = RunLiteralStore("run")
    owned_roots: list[Path] = []
    original_mkdtemp = literals.tempfile.mkdtemp

    def record_root(*args: object, **kwargs: object) -> str:
        root = original_mkdtemp(*args, **kwargs)
        owned_roots.append(Path(root))
        return root

    def fail_start(*args: object, **kwargs: object) -> None:
        raise RuntimeError("resource failure")

    def fail_socket(*args: object, **kwargs: object) -> None:
        raise OSError("resource failure")

    monkeypatch.setattr(literals.tempfile, "mkdtemp", record_root)
    if allocation == "thread":
        monkeypatch.setattr(literals.threading.Thread, "start", fail_start)
    else:
        monkeypatch.setattr(literals.socket, "socket", fail_socket)
    try:
        with pytest.raises(LiteralTransferError, match="listener unavailable"):
            LiteralBootstrapBroker(store, private_directory)
        assert all(not root.exists() for root in owned_roots)
        assert not list(private_directory.iterdir())
    finally:
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
@pytest.mark.parametrize("fault", ["version", "duplicate", "oversized", "partial"])
def test_malformed_requests_do_not_break_other_worker_bootstraps(
    private_directory: Path, fault: str
) -> None:
    store = RunLiteralStore("run")
    handle = store.freeze("private-literal")
    broker = LiteralBootstrapBroker(store, private_directory, timeout_seconds=0.2)
    descriptor = broker.issue([handle])
    request = json.loads(Path(descriptor.capsule).read_text(encoding="utf-8"))
    if fault == "version":
        request["version"] = True
        raw = json.dumps(request).encode()
    elif fault == "duplicate":
        raw = b'{"version":1,"version":1}'
    else:
        raw = b"{"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(descriptor.endpoint)
            size = 9 * 1024 * 1024 if fault == "oversized" else len(raw)
            if fault == "partial":
                size += 100
            client.sendall(struct.pack("!I", size) + raw)
            client.shutdown(socket.SHUT_WR)
            try:
                received = client.recv(4096)
            except ConnectionResetError:
                # Linux resets a socket closed with an unread oversized body;
                # macOS reports EOF. Both must reject without returning data.
                received = b""
            assert received == b""
        worker = bootstrap_worker(descriptor)
        assert worker.resolve(handle) == "private-literal"
        worker.close()
    finally:
        broker.close()
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX IPC backend")
@pytest.mark.parametrize("fault", ["partial", "wrong_run", "wrong_manifest", "stall"])
def test_worker_rejects_partial_or_foreign_bundles_atomically(
    private_directory: Path, fault: str
) -> None:
    import m3._literal_store as literals

    store = RunLiteralStore("run")
    handle = store.freeze("private-literal")
    broker = LiteralBootstrapBroker(store, private_directory)
    descriptor = broker.issue([handle])
    fake_endpoint = Path(descriptor.endpoint).parent / "fake"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(fake_endpoint))
    listener.listen()
    listener.settimeout(2)

    def peer() -> None:
        try:
            channel, _ = listener.accept()
            with channel:
                literals._receive(channel, time.monotonic() + 2)
                response = {
                    "version": 1,
                    "run_id": "other" if fault == "wrong_run" else descriptor.run_id,
                    "generation": descriptor.generation,
                    "values": {handle.slot: "private-literal"},
                }
                if fault == "wrong_manifest":
                    response["values"] = {"foreign": "private-literal"}
                encoded = json.dumps(response).encode()
                channel.sendall(struct.pack("!I", len(encoded)))
                if fault == "partial":
                    channel.sendall(encoded[: len(encoded) // 2])
                elif fault == "stall":
                    # A stalled body cannot reset the original deadline.
                    time.sleep(0.25)
                else:
                    channel.sendall(encoded)
        finally:
            listener.close()

    server = threading.Thread(target=peer)
    server.start()
    try:
        with pytest.raises(LiteralTransferError) as caught:
            bootstrap_worker(
                replace(descriptor, endpoint=str(fake_endpoint)), timeout_seconds=0.15
            )
        assert "private-literal" not in str(caught.value)
    finally:
        server.join(timeout=3)
        fake_endpoint.unlink(missing_ok=True)
        broker.close()
        store.close()
    assert not server.is_alive()
