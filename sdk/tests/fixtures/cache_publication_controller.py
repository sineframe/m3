"""Real controller faults at an acknowledged cache publication boundary."""

from __future__ import annotations

import array
import fcntl
import hashlib
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from m3._bootstrap_operations import OwnedBootstrapOperations, PrivateBytes
from m3._immutable_cache import RIPGREP, CacheRequest


def release_writer(directory: Path) -> None:
    descriptor = os.open(directory / "gate", os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(descriptor, b"g")
    finally:
        os.close(descriptor)


def acquire_lease(descriptor: int, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            assert time.monotonic() < deadline
            select.select([], [], [], 0.01)


def artifact_key() -> str:
    raw = b"controlled-ripgrep-artifact"
    return replace(
        RIPGREP,
        binary_size=len(raw),
        binary_sha256=hashlib.sha256(raw).hexdigest(),
    ).key


def check_guard_loss(
    owner: OwnedBootstrapOperations,
    directory: Path,
    done: threading.Event,
    listener: socket.socket,
) -> None:
    connection, _ = listener.accept()
    with connection:
        raw, ancillary, _flags, _address = connection.recvmsg(
            1, socket.CMSG_SPACE(array.array("i").itemsize)
        )
    assert raw == b"w"
    descriptors = array.array("i")
    descriptors.frombytes(ancillary[0][2])
    assert len(descriptors) == 1
    writer_eof = descriptors[0]
    ident, job = next(iter(owner._jobs.items()))
    stage = job.root
    assert owner._worker is not None
    guard = owner._worker._groups[ident]
    os.kill(guard, signal.SIGKILL)
    assert done.wait(3)
    owner.close(deadline=time.monotonic() + 0.2)
    assert not owner.cleanup_complete and stage.exists()
    key = artifact_key()
    lock = os.open(directory / "shared" / (key + ".lock"), os.O_RDWR)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise AssertionError("writer did not retain exclusion after guard death")
        release_writer(directory)
        readable, _, _ = select.select([writer_eof], [], [], 5)
        assert readable and os.read(writer_eof, 1) == b""
        os.close(writer_eof)
        # Missing guardian death evidence keeps the helper lease quarantined,
        # even after the fixture independently proves writer EOF.
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise AssertionError("helper released uncertain publication custody")
        assert owner._worker._process is not None
        owner._worker._process.kill()
        owner._worker._process.wait(timeout=3)
        acquire_lease(lock, time.monotonic() + 5)
        assert stage.exists()
        assert not (directory / "shared" / key / "accepted").exists()
        # Fixture teardown only after the actual writer lease has ended.
        shutil.rmtree(stage)
    finally:
        os.close(lock)
    print(json.dumps({"quarantined": True, "writer_lease_retained": True}), flush=True)


def install_helper(directory: Path, mode: str, helper: str) -> None:
    original = subprocess.Popen

    class FixturePopen(original):
        def __init__(self, argv, *args, **kwargs):
            if isinstance(argv, list) and "m3._preparation_worker" in argv:
                argv = [
                    argv[0],
                    helper,
                    mode,
                    str(directory / "armed"),
                    str(directory / "ready"),
                    str(directory / "gate"),
                    str(directory / "late"),
                    *argv[3:],
                ]
            super().__init__(argv, *args, **kwargs)

    subprocess.Popen = FixturePopen


def main(directory: Path, mode: str, helper: str) -> None:
    proof_root = None
    listener = None
    if mode == "guard_loss":
        proof_root = tempfile.TemporaryDirectory(prefix="m3-cache-proof-", dir="/tmp")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        endpoint = str(Path(proof_root.name) / "writer")
        listener.bind(endpoint)
        listener.listen(1)
        os.environ["M3_TEST_WRITER_ENDPOINT"] = endpoint
    install_helper(directory, mode, helper)
    warm = OwnedBootstrapOperations()
    warm.create_capsule(PrivateBytes(b"warm-helper"), deadline=time.monotonic() + 10)
    warm.close(deadline=time.monotonic() + 5)
    (directory / "armed").touch()
    owner = OwnedBootstrapOperations()
    done = threading.Event()
    errors: list[str] = []
    deadline = time.monotonic() + (1 if mode in {"late", "accepted_loss"} else 20)

    def acquire() -> None:
        try:
            owner.materialize_immutable_cache(
                CacheRequest(directory / "shared", Path("/fixture/native"), "1.18.35"),
                deadline=deadline,
            )
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            done.set()

    thread = threading.Thread(target=acquire, daemon=True)
    thread.start()
    end = time.monotonic() + 5
    while not (directory / "ready").exists():
        assert time.monotonic() < end
        done.wait(0.005)
    if mode == "eof":
        print("held-publication", flush=True)
        os._exit(0)
    if mode == "accepted_loss":
        ident = next(iter(owner._jobs))
        assert owner._worker is not None
        guard = owner._worker._groups[ident]
        assert done.wait(3)
        assert time.monotonic() >= deadline
        release_writer(directory)
        end = time.monotonic() + 3
        while not (directory / "late").exists():
            assert time.monotonic() < end
            done.wait(0.005)
        # The complete late manifest exists while custody still excludes readers.
        assert list((directory / "shared").glob("*/accepted"))
        os.kill(guard, signal.SIGKILL)
        owner.close(deadline=time.monotonic() + 5)
        assert owner.cleanup_complete
        assert not (directory / "shared" / artifact_key() / "accepted").exists()
        assert list((directory / "shared").glob("*.rejected-*/accepted"))
        print(
            json.dumps({"cleanup_complete": True, "late_manifest_fenced": True}),
            flush=True,
        )
        return
    if mode == "guard_loss":
        assert listener is not None and proof_root is not None
        check_guard_loss(owner, directory, done, listener)
        listener.close()
        proof_root.cleanup()
        return
    if mode == "late":
        assert done.wait(3)
        assert errors
        assert time.monotonic() >= deadline
        release_writer(directory)
    else:
        owner.close(deadline=time.monotonic() + 3)
    assert done.wait(3)
    owner.close(deadline=time.monotonic() + 5)
    assert owner.cleanup_complete
    assert errors
    assert mode != "late" or (directory / "late").exists()
    assert not list((directory / "shared").glob("*/accepted"))
    print(
        json.dumps(
            {
                "fenced": True,
                "cleanup_complete": True,
                "late_rename": (directory / "late").exists(),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
