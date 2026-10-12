"""Private benchmark checkpoints, outside all production m3 configuration."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Literal, cast

import pytest


class Lease:
    def __init__(
        self,
        endpoint: str,
        invocation: int,
        victim: bool,
        role: Literal["controller", "worker"],
        worker: str,
    ) -> None:
        self.victim = victim
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(60)
        self.socket.connect(endpoint)
        self.reader = self.socket.makefile("rb")
        self.send(
            {
                "version": 1,
                "kind": "hello",
                "invocation": invocation,
                "role": role,
                "worker": worker,
                "pid": os.getpid(),
                "group": os.getpgid(0),
            }
        )
        assert self.receive() == {"action": "begin"}

    def send(self, message: dict[str, object]) -> None:
        frame = json.dumps(message).encode() + b"\n"
        assert len(frame) <= 4096
        self.socket.sendall(frame)

    def receive(self) -> dict[str, object]:
        frame = self.reader.readline(4097)
        if not frame:
            raise RuntimeError("checkpoint server connection lost")
        assert len(frame) <= 4096 and frame.endswith(b"\n")
        decoded = json.loads(frame)
        assert isinstance(decoded, dict) and all(
            isinstance(key, str) for key in decoded
        )
        return cast(dict[str, object], decoded)

    def hold(self) -> None:
        # EOF fails immediately if the driver disappears. This is a bounded
        # socket wait; no unfinished session can silently be released.
        self.socket.settimeout(180)
        assert self.receive() == {"action": "release"}

    def close(self) -> None:
        self.reader.close()
        self.socket.close()


_CHECKPOINT = pytest.StashKey[Lease | None]()


def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    workerinput: dict[str, object] | None = getattr(config, "workerinput", None)
    config.stash[_CHECKPOINT] = None
    configured_base = config.getoption("basetemp")
    if configured_base is None:
        return
    assert isinstance(configured_base, (str, Path))
    base = Path(configured_base)
    if workerinput is not None:
        base = base.parent
    descriptor = base.parent / "checkpoint.json"
    if not descriptor.exists():
        return
    metadata = json.loads(descriptor.read_text())
    assert set(metadata) == {"version", "endpoint", "invocation", "hold_victim"}
    assert type(metadata["version"]) is int and metadata["version"] == 1
    endpoint, index, victim = (
        metadata["endpoint"],
        metadata["invocation"],
        metadata["hold_victim"],
    )
    assert isinstance(endpoint, str) and type(index) is int and type(victim) is bool
    worker = "controller" if workerinput is None else workerinput["workerid"]
    assert isinstance(worker, str)
    config.stash[_CHECKPOINT] = Lease(
        endpoint,
        index,
        victim,
        "controller" if workerinput is None else "worker",
        worker,
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    lease = config.stash.get(_CHECKPOINT, None)
    if lease is not None:
        lease.close()
        config.stash[_CHECKPOINT] = None


@pytest.fixture
def checkpoint(request: pytest.FixtureRequest) -> Lease | None:
    return request.config.stash[_CHECKPOINT]
