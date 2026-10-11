from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
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
