"""Controlled publication boundary in the real preparation helper.

The tiny artifact replaces native eligibility for unit tests only. The
guardian, process groups, lease, private channel and filesystem IO are real.
"""

from __future__ import annotations

import array
import hashlib
import os
import runpy
import signal
import socket
import sys
from dataclasses import replace
from pathlib import Path

from m3 import _cache_guard as guard
from m3 import _immutable_cache as cache

mode, arm_name, ready_name, gate_name, late_name = sys.argv[1:6]
payload = b"controlled-ripgrep-artifact"
artifact = replace(
    cache.RIPGREP,
    binary_size=len(payload),
    binary_sha256=hashlib.sha256(payload).hexdigest(),
)
cache.RIPGREP = guard.RIPGREP = artifact
guard.validate_native = lambda request: None
guard.download_binary = lambda *, timeout: payload
original_replace = os.replace
original_open = os.open
original_fdopen = os.fdopen
accepted_descriptors: set[int] = set()


def controlled_replace(source: Path, target: Path) -> None:
    if (
        mode != "accepted_loss"
        and Path(arm_name).exists()
        and source.name.startswith(artifact.key + ".pending-")
        and target.name == artifact.key
    ):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        Path(ready_name).write_text(str(os.getpid()))
        descriptor = os.open(gate_name, os.O_RDONLY)
        try:
            os.read(descriptor, 1)
        finally:
            os.close(descriptor)
        original_replace(source, target)
        Path(late_name).touch()
    else:
        original_replace(source, target)


def controlled_open(path, flags, mode=0o777, **kwargs):
    descriptor = original_open(path, flags, mode, **kwargs)
    if isinstance(path, Path) and path.name == "accepted":
        accepted_descriptors.add(descriptor)
    return descriptor


class HeldAcceptedWrite:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.descriptor)

    def write(self, value: bytes) -> int:
        Path(ready_name).write_text(str(os.getpid()))
        gate = original_open(gate_name, os.O_RDONLY)
        try:
            os.read(gate, 1)
        finally:
            os.close(gate)
        written = os.write(self.descriptor, value)
        Path(late_name).touch()
        gate = original_open(gate_name + "-hold", os.O_RDONLY)
        try:
            os.read(gate, 1)
        finally:
            os.close(gate)
        return written


def controlled_fdopen(descriptor, *args, **kwargs):
    if mode == "accepted_loss" and descriptor in accepted_descriptors:
        return HeldAcceptedWrite(descriptor)
    return original_fdopen(descriptor, *args, **kwargs)


guard.os.open = controlled_open
guard.os.fdopen = controlled_fdopen
if mode == "guard_loss":
    original_writer = guard._ForkJob

    class ObservedWriter(original_writer):
        def __init__(self, function, *, output, gated=False, keep_fds=()):
            reader, writer = os.pipe()
            try:
                super().__init__(
                    function, output=output, gated=gated, keep_fds=(*keep_fds, writer)
                )
                os.close(writer)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as proof:
                    proof.connect(os.environ["M3_TEST_WRITER_ENDPOINT"])
                    proof.sendmsg(
                        [b"w"],
                        [
                            (
                                socket.SOL_SOCKET,
                                socket.SCM_RIGHTS,
                                array.array("i", [reader]),
                            )
                        ],
                    )
            finally:
                os.close(reader)

    guard._ForkJob = ObservedWriter


guard.os.replace = controlled_replace
sys.argv = [sys.argv[0], *sys.argv[6:]]
runpy.run_module("m3._preparation_worker", run_name="__main__")
