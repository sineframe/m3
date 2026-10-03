from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from m3 import _timing, _timing_report


def _span(
    name: str,
    dur_ns: int,
    *,
    key: str | None = None,
    test: str | None = None,
    pid: int = 1,
    process: str = "worker",
    start_ns: int = 1_700_000_000_000_000_000,
    sid: int = 1,
) -> dict[str, Any]:
    return {
        "type": "span",
        "name": name,
        "key": key,
        "id": sid,
        "parent": None,
        "test": test,
        "start_ns": start_ns,
        "dur_ns": dur_ns,
        "pid": pid,
        "tid": 7,
        "process": process,
        "status": "ok",
        "notes": None,
    }


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _step(summary: _timing_report.Summary, name: str, key: str = "") -> Any:
    return next(s for s in summary.steps if s.name == name and s.key == key)


def test_histogram_percentiles_within_error_bound(tmp_path: Path) -> None:
    rng = random.Random(3)
    durations = [int(rng.lognormvariate(15, 1.5)) + 1 for _ in range(20_000)]
    _write(
        tmp_path / "a-1.jsonl",
        [_span("step", d, sid=i) for i, d in enumerate(durations)],
    )
    summary = _timing_report.write_reports(tmp_path)
    stat = _step(summary, "step")
    ordered = sorted(durations)
    for got, q in ((stat.p50_ns, 0.5), (stat.p95_ns, 0.95)):
        exact = ordered[int(q * len(ordered)) - 1]
        assert abs(got - exact) / exact < 0.05
    assert stat.count == 20_000
    assert stat.total_ns == sum(durations)
    assert stat.max_ns == max(durations)


def test_trace_is_valid_chrome_json_with_process_names(tmp_path: Path) -> None:
    _write(
        tmp_path / "cli-1.jsonl",
        [_span("cli.run", 5_000_000, pid=1, process="cli", start_ns=2_000_000_000)],
    )
    _write(
        tmp_path / "worker-2.jsonl",
        [
            _span(
                "test",
                3_000_000,
                key="t::a",
                test="t::a",
                pid=2,
                start_ns=3_000_000_000,
            ),
            _span("fixture.setup", 1_000, key="f", test="t::a", pid=2, sid=2),
        ],
    )
    _timing_report.write_reports(tmp_path)
    trace = json.loads((tmp_path / "trace.json").read_text())
    events = trace["traceEvents"]
    meta = [e for e in events if e["ph"] == "M"]
    assert {e["pid"]: e["args"]["name"] for e in meta} == {
        1: "cli (1)",
        2: "worker (2)",
    }
    complete = [e for e in events if e["ph"] == "X"]
    assert len(complete) == 3
    run = next(e for e in complete if e["name"] == "cli.run")
    assert run["ts"] == 2_000_000 and run["dur"] == 5_000  # microseconds
    assert {"pid", "tid"} <= run.keys()


def test_per_test_totals_and_slowest_tests(tmp_path: Path) -> None:
    records = []
    for i in range(8):
        node = f"t::case{i}"
        records.append(_span("test", (i + 1) * 1_000_000, key=node, test=node))
        records.append(_span("session.turn", (i + 1) * 600_000, test=node, key="h"))
        records.append(_span("fixture.setup", 100_000, test=node))
        records.append(_span("fixture.setup", 100_000, test=node))
    _write(tmp_path / "w-1.jsonl", records)
    summary = _timing_report.write_reports(tmp_path)
    assert [t.test for t in summary.slowest_tests] == [
        "t::case7",
        "t::case6",
        "t::case5",
        "t::case4",
        "t::case3",
    ]
    top = summary.slowest_tests[0]
    assert top.duration_ns == 8_000_000
    assert top.children[0] == ("session.turn", 4_800_000)
    assert top.children[1] == ("fixture.setup", 200_000)
    # node ids never become step-table keys
    assert _step(summary, "test").count == 8


def test_counters_merge_across_processes(tmp_path: Path) -> None:
    def counter(pid: int, test: str | None, n: int, total: int) -> dict[str, Any]:
        return {
            "type": "counter",
            "name": "store.append",
            "test": test,
            "count": n,
            "total_ns": total,
            "max_ns": total // n,
            "buckets": {str(_timing.bucket_of(total // n)): n},
            "pid": pid,
            "process": "w",
        }

    _write(tmp_path / "w-1.jsonl", [counter(1, "t::a", 10, 10_000)])
    _write(
        tmp_path / "w-2.jsonl",
        [
            counter(2, None, 30, 90_000),
            {
                "type": "meta",
                "pid": 2,
                "process": "w",
                "dropped": 4,
                "max_queue": 1,
                "writer_busy_ns": 1,
                "max_lag_ns": 1,
            },
            {
                "type": "meta",
                "pid": 1,
                "process": "w",
                "dropped": 3,
                "max_queue": 1,
                "writer_busy_ns": 1,
                "max_lag_ns": 1,
            },
        ],
    )
    summary = _timing_report.write_reports(tmp_path)
    (stat,) = summary.counters
    assert (stat.name, stat.count, stat.total_ns) == ("store.append", 40, 100_000)
    assert stat.max_ns == 3_000
    assert summary.dropped == 7
    assert summary.processes == 2


def test_idempotent_and_ignores_output_files(tmp_path: Path) -> None:
    _write(tmp_path / "w-1.jsonl", [_span("a", 1_000_000, test="t", key="k")])
    first = _timing_report.write_reports(tmp_path)
    trace_first = (tmp_path / "trace.json").read_text()
    second = _timing_report.write_reports(tmp_path)
    assert (tmp_path / "trace.json").read_text() == trace_first
    assert first.steps == second.steps and second.spans == 1
    on_disk = json.loads((tmp_path / "summary.json").read_text())
    assert on_disk["spans"] == 1 and on_disk["report_ns"] > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "summary.json",
        "trace.json",
        "w-1.jsonl",
    ]


def test_truncated_lines_are_skipped(tmp_path: Path) -> None:
    good = json.dumps(_span("a", 10))
    (tmp_path / "w-1.jsonl").write_text(good + "\n" + good[:20] + "\n[1]\n")
    assert _timing_report.write_reports(tmp_path).spans == 1


def test_key_cardinality_is_capped(tmp_path: Path) -> None:
    n = _timing_report.MAX_KEYS_PER_NAME + 50
    _write(
        tmp_path / "w-1.jsonl",
        [_span("fixture.setup", 10, key=f"f{i}") for i in range(n)],
    )
    summary = _timing_report.write_reports(tmp_path)
    assert len(summary.steps) == _timing_report.MAX_KEYS_PER_NAME + 1
    assert _step(summary, "fixture.setup", _timing_report.OTHER_KEY).count == 50


def _summary(tmp_path: Path) -> _timing_report.Summary:
    records = [
        _span("test", 2_500_000_000, key="t::slow", test="t::slow"),
        _span("session.turn", 2_000_000_000, key="claude", test="t::slow"),
        _span("fixture.setup", 1_500_000, key="m3_kit", test="t::slow"),
    ]
    records.append(
        {
            "type": "counter",
            "name": "store.append",
            "test": None,
            "count": 3,
            "total_ns": 300,
            "max_ns": 150,
            "buckets": {"5": 3},
            "pid": 1,
            "process": "w",
        }
    )
    records.append(
        {
            "type": "meta",
            "pid": 1,
            "process": "w",
            "dropped": 2,
            "max_queue": 1,
            "writer_busy_ns": 1,
            "max_lag_ns": 1,
        }
    )
    _write(tmp_path / "w-1.jsonl", records)
    return _timing_report.write_reports(tmp_path)


def test_render_text(tmp_path: Path) -> None:
    text = _timing_report.render_text(_summary(tmp_path))
    lines = text.splitlines()
    assert lines[0] == f"M3 timings: {tmp_path}"
    header = next(line for line in lines if line.startswith("step"))
    assert header.split() == ["step", "count", "total", "p50", "p95", "max"]
    assert "session.turn[claude]" in text
    assert "2.00s" in text and "1.5ms" in text
    assert "counters" in text and "store.append" in text
    assert "slowest tests" in text and "t::slow" in text
    assert lines[-1].startswith("report built in ")
    assert lines[-1].endswith("; 2 records dropped")


def test_render_markdown(tmp_path: Path) -> None:
    md = _timing_report.render_markdown(_summary(tmp_path))
    assert md.startswith("### M3 timings: ")
    assert "| step | count | total | p50 | p95 | max |" in md
    assert "| session.turn[claude] | 1 | 2.00s |" in md
    assert "#### Counters" in md and "#### Slowest tests" in md
    assert "2 records dropped" in md


def test_finish_prints_and_writes_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d = tmp_path / "timings"
    d.mkdir()
    _write(d / "w-1.jsonl", [_span("a", 1_000_000)])
    step = tmp_path / "step.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
    summary = _timing_report.finish(d)
    assert summary is not None
    assert capsys.readouterr().out.startswith(f"M3 timings: {d}")
    assert step.read_text().startswith("### M3 timings")
    assert (d / "trace.json").exists()


def test_finish_missing_directory_returns_none(tmp_path: Path) -> None:
    assert _timing_report.finish(tmp_path / "missing") is None


def test_finish_swallows_errors_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: Any) -> Any:
        raise RuntimeError("nope")

    monkeypatch.setattr(_timing_report, "write_reports", boom)
    with pytest.warns(RuntimeWarning, match="report failed"):
        assert _timing_report.finish(tmp_path) is None


_RSS_PROBE = """
import resource, sys
from m3 import _timing_report
unit = 1 if sys.platform == "darwin" else 1024
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
summary = _timing_report.write_reports(sys.argv[1])
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
print(summary.spans, len(summary.steps), after - before)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="needs resource module")
def test_memory_stays_bounded_for_200k_spans(tmp_path: Path) -> None:
    rng = random.Random(1)
    with (tmp_path / "w-1.jsonl").open("w") as handle:
        for i in range(200_000):
            rec = _span(
                "fixture.setup",
                rng.randint(1_000, 5_000_000),
                key=f"f{i % 50}",
                test=f"t::{i % 500}",
                sid=i,
            )
            handle.write(json.dumps(rec) + "\n")
    size = (tmp_path / "w-1.jsonl").stat().st_size
    out = subprocess.run(
        [sys.executable, "-c", _RSS_PROBE, str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    spans, steps, growth = (int(v) for v in out)
    assert (spans, steps) == (200_000, 50)
    # Streaming: peak growth is a fraction of the input size, not proportional to it.
    assert growth < size / 2
    assert growth < 40 * 1024 * 1024


def test_file_without_meta_is_incomplete(tmp_path: Path) -> None:
    _write(tmp_path / "w-1.jsonl", [_span("a", 10)])
    summary = _timing_report.write_reports(tmp_path)
    assert summary.incomplete == 1
    assert json.loads((tmp_path / "summary.json").read_text())["incomplete"] == 1
    assert "1 process file incomplete" in _timing_report.render_text(summary)
    assert "1 process file incomplete" in _timing_report.render_markdown(summary)
