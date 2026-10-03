"""Micro-benchmarks for the opt-in timing feature (``M3_TIMINGS``).

Run with ``uv run --project sdk python scripts/bench_timings.py``. Each
benchmark that depends on the import-time switch runs in a child process.

1. Disabled path: zero allocations, identity decorators.
2. Enabled span cost (target: median under 3 us per span close).
3. Event-loop pauses while 200k spans are emitted, timing on versus off.
4. Writer burst beyond the queue size: dropped count is reported.
5. Report cost on a synthetic run (2M spans, 10k tests, 200 fixtures, 4 files;
   targets: under 15 s and under 250 MB peak RSS).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path


def _child(mode: str, *args: str, enabled: bool) -> dict[str, float]:
    env = dict(os.environ)
    env.pop("M3_TIMINGS", None)
    if enabled:
        env["M3_TIMINGS"] = "1"
    out = subprocess.run(
        [sys.executable, __file__, "--child", mode, *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])  # type: ignore[no-any-return]


# Child modes ---------------------------------------------------------------


def _disabled_alloc() -> dict[str, float]:
    from m3 import _timing

    assert not _timing.ENABLED

    def fn() -> None:
        return None

    identity = _timing.timed("x")(fn) is fn and _timing.counted("x")(fn) is fn
    key = "k"
    for _ in range(1000):
        with _timing.span("x", key=key):
            pass
        with _timing.count("x"):
            pass
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(100_000):
        with _timing.span("x", key=key):
            pass
        with _timing.count("x"):
            pass
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    return {"alloc_bytes": after - before, "identity": float(identity)}


def _span_cost(directory: str) -> dict[str, float]:
    from m3 import _timing

    _timing.start(directory, "bench")
    per_span = []
    for _ in range(100):
        began = time.perf_counter_ns()
        for _ in range(1000):
            with _timing.span("bench.step", key="k"):
                pass
        per_span.append((time.perf_counter_ns() - began) / 1000)
    _timing.stop()
    return {
        "median_us": statistics.median(per_span) / 1000,
        "p95_us": sorted(per_span)[94] / 1000,
    }


def _loop_lag(directory: str) -> dict[str, float]:
    from m3 import _timing

    _timing.start(directory, "bench")  # no-op when M3_TIMINGS is unset

    async def main() -> float:
        worst = 0.0
        stop = False

        async def heartbeat() -> None:
            nonlocal worst
            while not stop:
                began = time.perf_counter()
                await asyncio.sleep(0.001)
                worst = max(worst, time.perf_counter() - began - 0.001)

        beat = asyncio.create_task(heartbeat())
        for _ in range(200):
            for _ in range(1000):
                with _timing.span("bench.step", key="k"):
                    pass
            await asyncio.sleep(0)
        stop = True
        await beat
        return worst * 1000

    lag = asyncio.run(main())
    _timing.stop()
    return {"max_lag_ms": lag}


def _burst(directory: str, total: int) -> dict[str, float]:
    from m3 import _timing

    _timing.start(directory, "bench")
    began = time.perf_counter()
    for _ in range(total):
        with _timing.span("bench.burst"):
            pass
    emit_s = time.perf_counter() - began
    sink = _timing._sink
    assert sink is not None
    _timing.stop()
    written = sum(
        1
        for path in Path(directory).glob("*.jsonl")
        for line in path.open()
        if '"type":"span"' in line
    )
    return {
        "emitted": total,
        "written": written,
        "dropped": sink.dropped,
        "emit_s": emit_s,
        "max_queue": sink.max_queue,
        "writer_busy_s": sink.busy_ns / 1e9,
    }


def _report(directory: str) -> dict[str, float]:
    from m3 import _timing_report

    unit = 1 if sys.platform == "darwin" else 1024
    base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
    began = time.perf_counter()
    cpu = time.process_time()
    summary = _timing_report.write_reports(directory)
    wall = time.perf_counter() - began
    cpu = time.process_time() - cpu
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
    return {
        "spans": summary.spans,
        "wall_s": wall,
        "cpu_s": cpu,
        "rss_growth_mb": (peak - base) / 1e6,
        "peak_rss_mb": peak / 1e6,
    }


def _generate(directory: Path, spans: int, tests: int, fixtures: int) -> None:
    rng = random.Random(7)
    files = [
        (directory / f"{label}-{pid}.jsonl").open("w")
        for label, pid in (("cli", 1), ("controller", 2), ("worker", 3), ("worker", 4))
    ]
    dumps = json.dumps
    for i in range(spans):
        pid = 1 + i % 4
        test = f"tests/test_x.py::test_{i % tests}"
        name = "fixture.setup" if i % 2 else "session.turn"
        key = f"fixture_{i % fixtures}" if i % 2 else "claude"
        record = {
            "type": "span",
            "name": name,
            "key": key,
            "id": i,
            "parent": None,
            "test": test,
            "start_ns": 1_700_000_000_000_000_000 + i * 1000,
            "dur_ns": int(rng.lognormvariate(14, 1.5)),
            "pid": pid,
            "tid": 1,
            "process": "worker",
            "status": "ok",
            "notes": None,
        }
        files[i % 4].write(dumps(record, separators=(",", ":")) + "\n")
    for handle in files:
        handle.close()


# Parent -------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    parser.add_argument("args", nargs="*")
    parser.add_argument("--report-spans", type=int, default=2_000_000)
    ns = parser.parse_args()
    if ns.child:
        modes = {
            "disabled": lambda a: _disabled_alloc(),
            "span": lambda a: _span_cost(a[0]),
            "lag": lambda a: _loop_lag(a[0]),
            "burst": lambda a: _burst(a[0], int(a[1])),
            "report": lambda a: _report(a[0]),
        }
        print(json.dumps(modes[ns.child](ns.args)))
        return

    with tempfile.TemporaryDirectory(prefix="m3-bench-") as tmp:
        root = Path(tmp)

        def sub(name: str) -> str:
            path = root / name
            path.mkdir()
            return str(path)

        r1 = _child("disabled", enabled=False)
        ok = r1["alloc_bytes"] < 1024 and r1["identity"] == 1.0
        print(
            f"1. disabled path: {r1['alloc_bytes']:.0f} bytes allocated over 200k calls, "
            f"identity decorators={bool(r1['identity'])} -> {'PASS' if ok else 'FAIL'}"
        )

        r2 = _child("span", sub("span"), enabled=True)
        print(
            f"2. enabled span close: median {r2['median_us']:.2f} us, "
            f"p95 {r2['p95_us']:.2f} us (target < 3 us) -> "
            f"{'PASS' if r2['median_us'] < 3 else 'FAIL'}"
        )

        off = _child("lag", sub("lag-off"), enabled=False)
        on = _child("lag", sub("lag-on"), enabled=True)
        print(
            f"3. event-loop max lag over 200k spans: off {off['max_lag_ms']:.2f} ms, "
            f"on {on['max_lag_ms']:.2f} ms"
        )

        r4 = _child("burst", sub("burst"), "300000", enabled=True)
        print(
            f"4. burst of {r4['emitted']:.0f} spans in {r4['emit_s']:.2f} s: "
            f"written {r4['written']:.0f}, dropped {r4['dropped']:.0f}, "
            f"max queue {r4['max_queue']:.0f}, writer busy {r4['writer_busy_s']:.2f} s "
            f"(written + dropped == emitted: "
            f"{r4['written'] + r4['dropped'] == r4['emitted']})"
        )

        data = Path(sub("report"))
        spans = ns.report_spans
        began = time.perf_counter()
        _generate(data, spans, tests=10_000, fixtures=200)
        print(
            f"   (generated {spans} synthetic spans in {time.perf_counter() - began:.1f} s)"
        )
        r5 = _child("report", str(data), enabled=False)
        ok5 = r5["wall_s"] < 15 and r5["peak_rss_mb"] < 250
        print(
            f"5. report over {r5['spans']:.0f} spans: {r5['wall_s']:.1f} s wall / {r5['cpu_s']:.1f} s cpu, "
            f"peak RSS {r5['peak_rss_mb']:.0f} MB "
            f"(growth {r5['rss_growth_mb']:.0f} MB; targets < 15 s, < 250 MB) -> "
            f"{'PASS' if ok5 else 'FAIL'}"
        )


if __name__ == "__main__":
    main()
