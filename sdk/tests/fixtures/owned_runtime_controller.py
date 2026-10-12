"""Controller-loss fixture with an actual owned package and native writer."""

from __future__ import annotations

import asyncio
import socket
import sys
import time
from pathlib import Path

from m3._bootstrap_operations import _receive_private, _send_private
from m3.harness.native import ProcessOwner
from m3.runtime import RuntimeManager


async def run(channel: socket.socket) -> None:
    request, descriptor = _receive_private(channel, time.monotonic() + 10)
    assert descriptor is None
    manager = RuntimeManager(
        Path(request["cache"]), Path(request["project"]), _owned=True
    )
    lease = await manager.acquire_owned(
        "claude", request["selector"], deadline=time.monotonic() + 10
    )
    owner = ProcessOwner(Path(request["native_root"]))
    await owner.spawn(
        [str(lease.executable)], {}, runtime_descriptors=lease.descriptors
    )
    assert owner.process is not None and owner.process.stdout is not None
    assert await owner.process.stdout.readline() == b"ready\n"
    assert owner._guardian is not None
    await owner.startup_ready()
    _send_private(
        channel,
        {
            "version": 1,
            "guardian": owner._guardian.process.pid,
            "native": owner._guardian.native_pid,
        },
        time.monotonic() + 10,
    )
    # The test kills this controller. No graceful close path replaces EOF.
    await asyncio.Event().wait()


if __name__ == "__main__":
    with socket.socket(fileno=int(sys.argv[1])) as control:
        asyncio.run(run(control))
