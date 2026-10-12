"""Private benchmark checkpoints, outside all production m3 configuration."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest


class Lease:
    def __init__(self, descriptor: dict[str, Any], role: str, worker: str) -> None:
        self.victim = descriptor["hold_victim"]
        self.role = role
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(60)
        self.socket.connect(descriptor["endpoint"])
        self.reader = self.socket.makefile("rb")
        self.send(
            {
                "version": 1,
                "kind": "hello",
                "invocation": descriptor["invocation"],
                "role": role,
                "worker": worker,
                "pid": os.getpid(),
                "group": os.getpgid(0),
            }
        )
        assert self.receive() == {"action": "begin"}

    def send(self, message: dict[str, Any]) -> None:
        frame = json.dumps(message).encode() + b"\n"
        assert len(frame) <= 4096
        self.socket.sendall(frame)

    def receive(self) -> dict[str, Any]:
        frame = self.reader.readline(4097)
        if not frame:
            raise RuntimeError("benchmark coordinator lost")
        assert len(frame) <= 4096 and frame.endswith(b"\n")
        return json.loads(frame)

    def hold(self) -> None:
        # A bounded live socket wait, not a sleep. EOF fails immediately if the
        # driver disappears; no unfinished session can silently be released.
        self.socket.settimeout(180)
        assert self.receive() == {"action": "release"}

    def close(self) -> None:
        self.reader.close()
        self.socket.close()


def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    workerinput = getattr(config, "workerinput", None)
    config._storage_checkpoint = None
    configured_base = config.getoption("basetemp")
    if configured_base is None:
        return
    base = Path(configured_base)
    if workerinput is not None:
        base = base.parent
    descriptor = base.parent / "checkpoint.json"
    if descriptor.exists():
        config._storage_checkpoint = Lease(
            json.loads(descriptor.read_text()),
            "controller" if workerinput is None else "worker",
            "controller" if workerinput is None else workerinput["workerid"],
        )


def pytest_unconfigure(config: pytest.Config) -> None:
    lease = getattr(config, "_storage_checkpoint", None)
    if lease is not None:
        lease.close()
        config._storage_checkpoint = None


@pytest.fixture
def checkpoint(request: pytest.FixtureRequest) -> Lease | None:
    return request.config._storage_checkpoint
