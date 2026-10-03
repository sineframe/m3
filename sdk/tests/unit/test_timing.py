from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import threading
import time
import tracemalloc
import warnings
from pathlib import Path
from typing import Any

import pytest

from m3 import _timing, _timing_report


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(_timing.ENV_VAR, raising=False)
    _timing._reset()
    yield
    _timing._reset()


def _enable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str = "unit"
) -> Path:
    monkeypatch.setenv(_timing.ENV_VAR, "1")
    _timing._reset()
    _timing.start(tmp_path, label)
    assert _timing.active()
    return tmp_path


def _records(directory: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        out += [json.loads(line) for line in path.read_text().splitlines()]
    return out


def _spans(directory: Path) -> list[dict[str, Any]]:
    return [r for r in _records(directory) if r["type"] == "span"]


def test_disabled_returns_noop_singleton_without_allocating(tmp_path: Path) -> None:
    assert not _timing.ENABLED
    assert _timing.span("x", key="k") is _timing.NOOP
    assert _timing.count("x") is _timing.NOOP
    key = lambda: "k"
    for _ in range(100):  # warm up
        with _timing.span("x", key=key) as s:
            s.note("a", 1)
        with _timing.count("x"):
            pass
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(10_000):
            with _timing.span("x", key="k"):
                pass
            with _timing.count("x"):
                pass
        after = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert after - before < 1024
    assert list(tmp_path.iterdir()) == []


def test_decorators_are_identity_when_disabled() -> None:
    def fn() -> int:
        return 1

    assert _timing.timed("x")(fn) is fn
    assert _timing.counted("x")(fn) is fn


def test_decorators_wrap_when_enabled_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_timing.ENV_VAR, "yes")
    module = importlib.reload(_timing)
    try:

        def fn() -> int:
            return 1

        assert module.ENABLED
        assert module.timed("x")(fn) is not fn
        assert module.counted("x")(fn) is not fn
    finally:
        monkeypatch.delenv(_timing.ENV_VAR, raising=False)
        importlib.reload(_timing)


def test_enabled_without_start_collects_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(_timing.ENV_VAR, "1")
    _timing._reset()
    assert _timing.ENABLED
    assert _timing.span("x") is _timing.NOOP
    assert _timing.current() is None
    with _timing.span("x"):
        pass
    _timing.stop()
    assert list(tmp_path.iterdir()) == []


def test_start_ignored_when_disabled(tmp_path: Path) -> None:
    _timing.start(tmp_path / "t", "unit")
    assert not _timing.active()
    assert not (tmp_path / "t").exists()


def test_nesting_key_notes_and_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = _enable(monkeypatch, tmp_path)
    with _timing.span("outer", key=lambda: "k") as outer:
        outer.note("n", 3)
        outer.note("long", "x" * 500)
        outer.note("bad", object())  # type: ignore[arg-type]
        with _timing.span("inner"):
            pass
    _timing.stop()
    assert not _timing.active()
    outer_rec, inner_rec = sorted(_spans(d), key=lambda r: r["name"] != "outer")
    assert inner_rec["parent"] == outer_rec["id"]
    assert outer_rec["parent"] is None
    assert outer_rec["key"] == "k"
    assert outer_rec["status"] == "ok"
    assert outer_rec["notes"]["n"] == 3
    assert len(outer_rec["notes"]["long"]) == 200
    assert "bad" not in outer_rec["notes"]
    assert outer_rec["process"] == "unit"
    assert outer_rec["pid"] == os.getpid()
    assert outer_rec["dur_ns"] >= inner_rec["dur_ns"] >= 0
    assert outer_rec["start_ns"] > 1_000_000_000_000_000_000
    meta = [r for r in _records(d) if r["type"] == "meta"]
    assert len(meta) == 1 and meta[0]["dropped"] == 0
    _timing.stop()  # safe to repeat


async def test_test_id_and_parent_across_gather(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = _enable(monkeypatch, tmp_path)

    async def child(i: int) -> None:
        await asyncio.sleep(0.001)
        with _timing.span("child", key=str(i)):
            await asyncio.sleep(0.001)

    with _timing.test_scope("t::a"):
        with _timing.span("root"):
            await asyncio.gather(*(child(i) for i in range(3)))
    with _timing.span("outside"):
        pass
    _timing.stop()
    spans = _spans(d)
    root = next(s for s in spans if s["name"] == "root")
    kids = [s for s in spans if s["name"] == "child"]
    assert len(kids) == 3
    assert all(k["parent"] == root["id"] and k["test"] == "t::a" for k in kids)
    assert next(s for s in spans if s["name"] == "outside")["test"] is None


async def test_async_decorator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_timing.ENV_VAR, "1")
    module = importlib.reload(_timing)
    try:

        @module.timed("slow", key="k")
        async def slow(x: int) -> int:
            await asyncio.sleep(0)
            return x + 1

        @module.timed("fast")
        def fast(x: int) -> int:
            return x * 2

        module.start(tmp_path, "unit")
        assert await slow(1) == 2
        assert fast(4) == 8
        module.stop()
        names = {r["name"]: r for r in _spans(tmp_path)}
        assert names["slow"]["key"] == "k"
        assert "fast" in names
        assert slow.__name__ == "slow"
    finally:
        monkeypatch.delenv(_timing.ENV_VAR, raising=False)
        importlib.reload(_timing)


def test_error_status_and_reraise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = _enable(monkeypatch, tmp_path)
    with pytest.raises(KeyError):
        with _timing.span("boom"):
            raise KeyError("x")
    _timing.stop()
    (rec,) = _spans(d)
    assert rec["status"] == "error"
    assert rec["notes"]["error"] == "KeyError"
    assert "x" not in json.dumps(rec["notes"])


def test_threads_and_adopt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    d = _enable(monkeypatch, tmp_path)
    token_holder: list[Any] = []

    def work(i: int, token: Any) -> None:
        with _timing.adopt(token):
            for _ in range(50):
                with _timing.span("w", key=str(i)):
                    pass

    with _timing.test_scope("t::x"), _timing.span("parent"):
        token = _timing.current()
        token_holder.append(token)
        threads = [threading.Thread(target=work, args=(i, token)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    _timing.stop()
    spans = _spans(d)
    parent = next(s for s in spans if s["name"] == "parent")
    workers = [s for s in spans if s["name"] == "w"]
    assert len(workers) == 100
    assert {w["tid"] for w in workers} != {parent["tid"]}
    assert all(w["parent"] == parent["id"] and w["test"] == "t::x" for w in workers)
    assert len({s["id"] for s in spans}) == len(spans)


def test_adopt_none_is_noop() -> None:
    assert _timing.adopt(None) is _timing.NOOP


def test_full_queue_counts_drops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_timing, "QUEUE_MAX", 5)
    d = _enable(monkeypatch, tmp_path)
    sink = _timing._sink
    assert sink is not None
    gate = threading.Event()
    real_write = sink._write

    def slow_write(text: str) -> None:
        gate.wait(5)
        real_write(text)

    sink._write = slow_write  # type: ignore[method-assign]
    for _ in range(200):
        with _timing.span("burst"):
            pass
    assert sink.dropped > 0
    gate.set()
    _timing.stop()
    meta = next(r for r in _records(d) if r["type"] == "meta")
    assert meta["dropped"] == sink.dropped
    written = len(_spans(d))
    assert written + meta["dropped"] == 200


def test_unwritable_directory_warns_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(_timing.ENV_VAR, "1")
    _timing._reset()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _timing.start(blocker / "sub", "unit")
        with _timing.span("x"):
            pass
        _timing.stop()
    assert len([w for w in caught if "m3 timings" in str(w.message)]) == 1
    assert not _timing.active()


def test_write_error_warns_once_and_deactivates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, tmp_path)
    sink = _timing._sink
    assert sink is not None and sink.file is not None

    class Broken:
        def write(self, _: str) -> None:
            raise OSError("disk full")

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    sink.file = Broken()  # type: ignore[assignment]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with _timing.span("x"):
            pass
        sink.thread.join(timeout=5)  # type: ignore[union-attr]
        with _timing.span("y"):  # inactive now, must not raise
            pass
        _timing.stop()
    assert len([w for w in caught if "m3 timings" in str(w.message)]) == 1
    assert not _timing.active()


def test_counters_aggregate_per_test_and_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = _enable(monkeypatch, tmp_path)
    for _ in range(10):
        with _timing.count("hot"):
            pass
    with _timing.test_scope("t::a"):
        for _ in range(5):
            with _timing.count("hot"):
                pass
    _timing.stop()
    records = _records(d)
    assert not [r for r in records if r["type"] == "span"]
    counters = {r["test"]: r for r in records if r["type"] == "counter"}
    assert counters[None]["count"] == 10
    assert counters["t::a"]["count"] == 5
    assert sum(counters["t::a"]["buckets"].values()) == 5
    assert counters["t::a"]["total_ns"] >= counters["t::a"]["max_ns"] >= 0


def test_counted_decorator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_timing.ENV_VAR, "1")
    module = importlib.reload(_timing)
    try:

        @module.counted("c")
        def fn() -> int:
            return 7

        module.start(tmp_path, "unit")
        assert fn() == 7 and fn() == 7
        module.stop()
        (rec,) = [r for r in _records(tmp_path) if r["type"] == "counter"]
        assert rec["count"] == 2
    finally:
        monkeypatch.delenv(_timing.ENV_VAR, raising=False)
        importlib.reload(_timing)


def test_second_start_ignored_while_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, tmp_path / "a")
    _timing.start(tmp_path / "b", "other")
    _timing.stop()
    assert not (tmp_path / "b").exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_fork_child_resets_inactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, tmp_path)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        status = b"1" if _timing.active() else b"0"
        os.write(write_fd, status)
        os._exit(0)
    os.waitpid(pid, 0)
    assert os.read(read_fd, 1) == b"0"
    os.close(read_fd)
    os.close(write_fd)
    assert _timing.active()


def test_module_documents_privacy_rule() -> None:
    assert "Privacy rule" in (_timing.__doc__ or "")
    assert sys.modules["m3._timing"] is _timing


def _slow_sink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, delay: float
) -> tuple[Path, Any]:
    d = _enable(monkeypatch, tmp_path)
    sink = _timing._sink
    assert sink is not None
    real_write = sink._write

    def slow_write(text: str) -> None:
        time.sleep(delay)
        real_write(text)

    sink._write = slow_write  # type: ignore[method-assign]
    with _timing.span("slow"):
        pass
    return d, sink


def test_stop_waits_for_slow_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d, _ = _slow_sink(monkeypatch, tmp_path, 0.5)
    assert _timing.stop() is True
    summary = _timing_report.write_reports(d)
    assert summary.spans == 1 and summary.incomplete == 0


def test_stop_reports_unfinished_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_timing, "STOP_TIMEOUT_S", 0.2)
    d, sink = _slow_sink(monkeypatch, tmp_path, 1.0)
    with pytest.warns(RuntimeWarning, match="writer did not finish"):
        assert _timing.stop() is False
    summary = _timing_report.write_reports(d)
    assert summary.incomplete == 1
    sink.thread.join(10)


def test_fail_marks_span_error() -> None:
    _timing.NOOP.fail("nope")  # inactive: no-op, no raise


def test_fail_sets_status_and_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = _enable(monkeypatch, tmp_path)
    with _timing.span("t") as s:
        s.fail("assertion")
    _timing.stop()
    (rec,) = _spans(d)
    assert rec["status"] == "error"
    assert rec["notes"]["error"] == "assertion"
