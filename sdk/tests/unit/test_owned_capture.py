from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from typing import Any

import pytest

from m3._bootstrap_operations import OwnedBootstrapOperations
from m3._preparation_worker import _group_alive
from m3._startup_deadlines import StartupBudget
from m3.transport._owned_capture import OwnedCaptureSink
from m3.transport.capture_proxy import McpCaptureManager
from m3.types import HarnessTimeouts

pytestmark = [
    pytest.mark.process_lifecycle,
    pytest.mark.skipif(os.name != "posix", reason="owned capture requires POSIX"),
]


async def wait_for(predicate: Any, *, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_owned_capture_batches_redacted_records_and_removes_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = StartupBudget(HarnessTimeouts(total_seconds=10))
    clock.resume()
    capture = await McpCaptureManager.create_owned(clock)
    root = capture.root
    calls: list[int] = []
    append = OwnedBootstrapOperations.append_capture

    def record_append(
        self: OwnedBootstrapOperations, *args: Any, **kwargs: Any
    ) -> None:
        calls.append(1)
        append(self, *args, **kwargs)

    monkeypatch.setattr(OwnedBootstrapOperations, "append_capture", record_append)
    try:
        writer = capture.writer_for("connection")
        writer.add_secrets({"owned-capture-canary"})
        for number in range(64):
            writer.write(
                transport="http",
                direction="to_server",
                payload={
                    "jsonrpc": "2.0",
                    "id": number,
                    "method": "example",
                    "params": {"value": "owned-capture-canary"},
                },
            )
        await capture.flush(deadline=clock.deadline)
        records = capture.snapshots()[0].events
        assert [record.jsonrpc_id for record in records] == list(range(64))
        assert "owned-capture-canary" not in repr(records)
        assert len(calls) == 1
        assert stat.S_IMODE((root / "connection.jsonl").stat().st_mode) == 0o600
    finally:
        await capture.close()
    assert not root.exists()


@pytest.mark.asyncio
async def test_owned_capture_overflow_marks_capture_incomplete() -> None:
    clock = StartupBudget(HarnessTimeouts(total_seconds=10))
    clock.resume()
    capture = await McpCaptureManager.create_owned(clock)
    try:
        writer = capture.writer_for("connection")
        writer.write(
            transport="http", direction="to_server", payload="x" * (1024 * 1024 + 1)
        )
        await asyncio.sleep(0)
        assert "capture_queue_overflow" in capture.snapshot("connection").limitations
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_fenced_append_keeps_capture_root_until_writer_death(
    blocked_helper: Any,
) -> None:
    _worker, _arm, marker, fifo, _late = blocked_helper("capture")
    operations = OwnedBootstrapOperations()
    resource = operations.create_capture_root(deadline=time.monotonic() + 5)
    clock = StartupBudget(HarnessTimeouts())
    failures: list[str] = []
    sink = OwnedCaptureSink(operations, resource, clock, failures.append)
    sink.enqueue("connection.jsonl", '{"offset_ms":0}\n')
    await wait_for(marker.exists)
    state = json.loads(marker.read_text())
    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await sink.flush(deadline=start + 0.15)
    assert time.monotonic() - start < 0.6
    operations.close(deadline=time.monotonic() + 0.02)
    assert resource.root.exists()
    assert _group_alive(state["pid"])
    assert not operations.cleanup_complete
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        await wait_for(lambda: not _group_alive(state["pid"]))
    finally:
        os.close(reader)
    await sink.close()
    operations.close(deadline=time.monotonic() + 3)
    assert operations.cleanup_complete and not resource.root.exists()
    assert failures == ["capture_write_failed"]
