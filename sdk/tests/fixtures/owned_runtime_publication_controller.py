"""Verify that a late complete acceptance marker remains excluded after loss."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from m3 import _bootstrap_operations as operations
from m3._bootstrap_operations import _receive_private, _send_private
from m3.runtime import (
    RuntimeManager,
    RuntimeValidationError,
    core,
    list_cache,
    opencode_models,
    prune_cache,
)


async def run(control: socket.socket) -> None:
    request, descriptor = _receive_private(control, time.monotonic() + 10)
    assert descriptor is None
    directory = Path(request["directory"])
    helper = Path(__file__).with_name("owned_runtime_publication_helper.py")
    original_popen = subprocess.Popen
    providers: list[operations.OwnedBootstrapOperations] = []
    original_provider = operations.OwnedBootstrapOperations

    class ObservedProvider(original_provider):
        def __init__(self) -> None:
            super().__init__()
            providers.append(self)

    class PublicationHelper(original_popen):
        def __init__(self, argv: list[str], *args: object, **kwargs: object) -> None:
            if len(argv) > 2 and argv[1:3] == ["-m", "m3._preparation_worker"]:
                argv = [argv[0], str(helper), *argv[3:]]
                kwargs["env"] = {
                    **kwargs.get("env", {}),
                    "M3_TEST_RUNTIME_PUBLICATION": str(directory),
                }
            super().__init__(argv, *args, **kwargs)

    subprocess.Popen = PublicationHelper
    operations.OwnedBootstrapOperations = ObservedProvider
    manager = RuntimeManager(directory / "cache", directory / "project", _owned=True)
    deadline = time.monotonic() + 2
    if request["kind"] == "runtime":
        task = asyncio.create_task(
            manager.acquire_owned("claude", request["selector"], deadline=deadline)
        )
    else:
        task = asyncio.create_task(
            manager.prepare_models_owned(
                directory / "catalog", request["source"], deadline=deadline
            )
        )
    try:
        observation_deadline = time.monotonic() + 8
        while not (directory / "entered").exists():
            assert not task.done(), "acquisition failed before final publication"
            assert time.monotonic() < observation_deadline
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("caller cancellation did not fence acquisition")
        assert time.monotonic() > deadline
        gate = os.open(directory / "write", os.O_WRONLY | os.O_NONBLOCK)
        os.write(gate, b"w")
        os.close(gate)
        while not (directory / "late").exists():
            assert time.monotonic() < observation_deadline
            await asyncio.sleep(0.01)
        observed = json.loads((directory / "entered").read_text())
        marker = Path(observed["marker"])
        assert json.loads(marker.read_text())["accepted"] is True
        os.kill(observed["pid"], signal.SIGKILL)
        provider = providers[0]
        await asyncio.to_thread(provider.close, deadline=time.monotonic() + 0.2)

        original_lock = core._entry_lock

        def bounded_reader_lock(path: Path, *, timeout: float = 300):
            return original_lock(path, timeout=0.1)

        core._entry_lock = opencode_models._entry_lock = bounded_reader_lock
        if request["kind"] == "runtime":
            assert list_cache(directory / "cache")[0]["status"] == "busy"
            try:
                prune_cache(directory / "cache")
            except RuntimeValidationError as error:
                assert str(error) == "runtime cache entry is busy"
            else:
                raise AssertionError("GC bypassed uncertain artifact custody")
            artifact = marker.parent
        else:
            try:
                opencode_models.prepare_models(directory / "catalog", request["source"])
            except RuntimeValidationError as error:
                assert str(error) == "runtime cache entry is busy"
            else:
                raise AssertionError(
                    "catalog reader bypassed uncertain artifact custody"
                )
            artifact = marker.with_name("opencode-models.json")
        assert artifact.exists()
        # The controlled marker owner was the sole remaining writer. Quarantine
        # its artifact before ending the helper so fixture teardown cannot make
        # the deliberately late generation eligible.
        os.replace(artifact, directory / "rejected")
        assert provider._worker is not None and provider._worker._process is not None
        provider._worker._process.kill()
        provider._worker._process.wait(timeout=3)
        _send_private(
            control, {"version": 1, "late_marker_fenced": True}, time.monotonic() + 5
        )
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    with socket.socket(fileno=int(sys.argv[1])) as channel:
        asyncio.run(run(channel))
