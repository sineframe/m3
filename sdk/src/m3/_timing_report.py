"""Build timing reports from the per-process JSONL files written by ``_timing``.

Streaming and bounded: files are read one line at a time, span durations go
into log-bucket histograms (about 4% percentile error) and the Chrome trace is
written as records are read. Memory grows with the number of distinct step
names, fixture keys and tests, never with the number of spans.

Privacy rule (same as ``_timing``): only names, ids, kinds and counts appear.
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from json.encoder import encode_basestring_ascii
from math import log as _log
from pathlib import Path
from typing import Any, TextIO

from ._timing import _LOG_BASE, bucket_of, bucket_value

MAX_KEYS_PER_NAME = 200
OTHER_KEY = "(other)"
TOP_TESTS = 5
TOP_CHILDREN = 3
_TEST_WRAPPERS = frozenset({"test", "test.setup", "test.call", "test.teardown"})
TEXT_ROWS = 40
_SKIP = {"trace.json", "summary.json"}


@dataclass
class StepStat:
    name: str
    key: str
    count: int
    total_ns: int
    p50_ns: int
    p95_ns: int
    max_ns: int


@dataclass
class SlowTest:
    test: str
    duration_ns: int
    children: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Summary:
    directory: str
    steps: list[StepStat] = field(default_factory=list)
    counters: list[StepStat] = field(default_factory=list)
    slowest_tests: list[SlowTest] = field(default_factory=list)
    spans: int = 0
    processes: int = 0
    dropped: int = 0
    incomplete: int = 0
    report_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Agg:
    __slots__ = ("buckets", "count", "max", "total")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0
        self.max = 0
        self.buckets: dict[int, int] = {}

    def add(self, ns: int) -> None:
        self.count += 1
        self.total += ns
        if ns > self.max:
            self.max = ns
        b = bucket_of(ns)
        self.buckets[b] = self.buckets.get(b, 0) + 1

    def merge(
        self, count: int, total: int, biggest: int, buckets: dict[str, int]
    ) -> None:
        self.count += count
        self.total += total
        if biggest > self.max:
            self.max = biggest
        for k, v in buckets.items():
            i = int(k)
            self.buckets[i] = self.buckets.get(i, 0) + v

    def percentile(self, q: float) -> int:
        if not self.count:
            return 0
        target = q * self.count
        seen = 0
        for index in sorted(self.buckets):
            seen += self.buckets[index]
            if seen >= target:
                return min(int(bucket_value(index)), self.max)
        return self.max

    def stat(self, name: str, key: str) -> StepStat:
        return StepStat(
            name,
            key,
            self.count,
            self.total,
            self.percentile(0.5),
            self.percentile(0.95),
            self.max,
        )


_q = encode_basestring_ascii  # C-accelerated JSON string quoting
# json.loads minus the wrapper overhead; scan_once is undocumented in typeshed.
_scan: Callable[[str, int], tuple[Any, int]] = json.JSONDecoder().scan_once  # type: ignore[attr-defined]
_dumps = json.JSONEncoder(separators=(",", ":")).encode


def _event(rec: dict[str, Any]) -> str:
    """One Chrome trace "X" event, formatted by hand (this is the hot loop)."""
    key = rec["key"]
    test = rec["test"]
    parent = rec["parent"]
    args = f'"id":{rec["id"]},"status":{_q(str(rec["status"]))}'
    if key is not None:
        args += f',"key":{_q(str(key))}'
    if test is not None:
        args += f',"test":{_q(str(test))}'
    if parent is not None:
        args += f',"parent":{parent}'
    if rec["notes"]:
        args += f',"notes":{_dumps(rec["notes"])}'
    return (
        f',\n{{"name":{_q(rec["name"])},"cat":{_q(str(key)) if key else _CAT},"ph":"X",'
        f'"ts":{rec["start_ns"] // 1000},"dur":{rec["dur_ns"] // 1000 or 1},'
        f'"pid":{rec["pid"]},"tid":{rec["tid"]},"args":{{{args}}}}}'
    )


_CAT = '"m3"'


def write_reports(directory: str | os.PathLike[str]) -> Summary:
    """Stream ``*.jsonl`` into ``trace.json`` and ``summary.json``; idempotent."""
    began = time.perf_counter_ns()
    root = Path(directory)
    summary = Summary(directory=str(root))
    steps: dict[tuple[str, str], _Agg] = {}
    keys_per_name: dict[str, set[str]] = {}
    counters: dict[str, _Agg] = {}
    test_totals: dict[str, dict[str, int]] = {}
    test_durations: dict[str, int] = {}
    named_pids: set[int] = set()
    pids: set[int] = set()

    trace_tmp = root / "trace.json.tmp"
    with trace_tmp.open("w", encoding="utf-8") as trace:
        trace.write('{"traceEvents":[')
        first = True
        write = trace.write

        def emit(text: str) -> None:
            nonlocal first
            trace.write(text if first else ",\n" + text)
            first = False

        for path in sorted(root.glob("*.jsonl")):
            if path.name in _SKIP:
                continue
            has_meta = False
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        rec = _scan(line, 0)[0]
                        kind = rec["type"]
                        if kind == "span":
                            _add_span(
                                rec,
                                steps,
                                keys_per_name,
                                test_totals,
                                test_durations,
                                named_pids,
                                write,
                                emit,
                            )
                            continue
                    except (ValueError, KeyError, TypeError, StopIteration):
                        continue  # truncated, foreign or malformed line
                    try:
                        if kind == "counter":
                            _add_counter(rec, counters, test_totals)
                        elif kind == "meta":
                            has_meta = True
                            summary.dropped += int(rec.get("dropped", 0))
                            pids.add(rec.get("pid", 0))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
                if not has_meta:
                    summary.incomplete += 1
        trace.write("]}\n")
    os.replace(trace_tmp, root / "trace.json")

    pids |= named_pids
    summary.spans = sum(a.count for a in steps.values())
    summary.processes = len(pids)
    summary.steps = sorted(
        (a.stat(n, k) for (n, k), a in steps.items()),
        key=lambda s: s.total_ns,
        reverse=True,
    )
    summary.counters = sorted(
        (a.stat(n, "") for n, a in counters.items()),
        key=lambda s: s.total_ns,
        reverse=True,
    )
    for test, duration in sorted(test_durations.items(), key=lambda i: -i[1])[
        :TOP_TESTS
    ]:
        children = sorted(
            # The test span and its pytest phases wrap everything else.
            (
                (n, t)
                for n, t in test_totals.get(test, {}).items()
                if n not in _TEST_WRAPPERS
            ),
            key=lambda i: -i[1],
        )[:TOP_CHILDREN]
        summary.slowest_tests.append(SlowTest(test, duration, children))
    summary.report_ns = time.perf_counter_ns() - began
    tmp = root / "summary.json.tmp"
    tmp.write_text(json.dumps(summary.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, root / "summary.json")
    return summary


def _add_counter(
    rec: dict[str, Any],
    counters: dict[str, _Agg],
    test_totals: dict[str, dict[str, int]],
) -> None:
    name = str(rec["name"])
    agg = counters.get(name)
    if agg is None:
        agg = counters[name] = _Agg()
    agg.merge(rec["count"], rec["total_ns"], rec["max_ns"], rec.get("buckets") or {})
    test = rec.get("test")
    if test is not None:
        per = test_totals.setdefault(test, {})
        per[name] = per.get(name, 0) + rec["total_ns"]


def _add_span(
    rec: dict[str, Any],
    steps: dict[tuple[str, str], _Agg],
    keys_per_name: dict[str, set[str]],
    test_totals: dict[str, dict[str, int]],
    test_durations: dict[str, int],
    named_pids: set[int],
    write: Any,
    emit: Any,
) -> None:
    name = rec["name"]
    dur = rec["dur_ns"]
    key = rec["key"]
    pid = rec["pid"]
    event = _event(rec)  # validates the record before any state changes
    if pid not in named_pids:
        named_pids.add(pid)
        emit(
            _dumps(
                {
                    "name": "process_name",
                    "ph": "M",
                    "pid": pid,
                    "args": {"name": f"{rec['process']} ({pid})"},
                }
            )
        )
    write(event)
    is_test = name == "test"
    if is_test:
        if key is not None:
            test_durations[key] = test_durations.get(key, 0) + dur
        group = ""  # the key is a node id; keep it out of the step table
    else:
        group = key or ""
    agg = steps.get((name, group))
    if agg is None:  # first sight of this (name, key): enforce the key cap
        seen = keys_per_name.setdefault(name, set())
        if len(seen) >= MAX_KEYS_PER_NAME:
            group = OTHER_KEY
        else:
            seen.add(group)
        agg = steps.get((name, group))
        if agg is None:
            agg = steps[(name, group)] = _Agg()
    agg.count += 1
    agg.total += dur
    if dur > agg.max:
        agg.max = dur
    bucket = int(_log(dur) / _LOG_BASE) if dur > 1 else 0
    buckets = agg.buckets
    buckets[bucket] = buckets.get(bucket, 0) + 1
    if not is_test:
        test = rec["test"]
        if test is not None:
            per = test_totals.get(test)
            if per is None:
                per = test_totals[test] = {}
            per[name] = per.get(name, 0) + dur


# Rendering -----------------------------------------------------------------


def _fmt(ns: int) -> str:
    if ns < 1_000_000:
        return f"{ns / 1e6:.2f}ms"
    if ns < 1_000_000_000:
        return f"{ns / 1e6:.1f}ms"
    return f"{ns / 1e9:.2f}s"


def _label(stat: StepStat) -> str:
    return f"{stat.name}[{stat.key}]" if stat.key else stat.name


def _rows(stats: list[StepStat]) -> list[list[str]]:
    return [
        [
            _label(s),
            str(s.count),
            _fmt(s.total_ns),
            _fmt(s.p50_ns),
            _fmt(s.p95_ns),
            _fmt(s.max_ns),
        ]
        for s in stats
    ]


_HEAD = ["step", "count", "total", "p50", "p95", "max"]


def _table(rows: list[list[str]], head: list[str] = _HEAD) -> list[str]:
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    out = []
    for r in [head, *rows]:
        cells = [r[0].ljust(widths[0])] + [
            c.rjust(w) for c, w in zip(r[1:], widths[1:], strict=True)
        ]
        out.append("  ".join(cells).rstrip())
    return out


def _footer(summary: Summary) -> str:
    text = (
        f"report built in {_fmt(summary.report_ns)}; {summary.dropped} records dropped"
    )
    if summary.incomplete:
        text += f"; {summary.incomplete} process file incomplete"
    return text


def render_text(summary: Summary) -> str:
    lines = [f"M3 timings: {summary.directory}", ""]
    if summary.steps:
        lines += _table(_rows(summary.steps[:TEXT_ROWS]))
        hidden = len(summary.steps) - TEXT_ROWS
        if hidden > 0:
            lines.append(f"... {hidden} more steps in summary.json")
    else:
        lines.append("no spans recorded")
    if summary.counters:
        lines += ["", "counters"]
        lines += _table(_rows(summary.counters[:TEXT_ROWS]), ["counter", *_HEAD[1:]])
    if summary.slowest_tests:
        lines += ["", "slowest tests"]
        for t in summary.slowest_tests:
            lines.append(f"  {_fmt(t.duration_ns):>9}  {t.test}")
            for name, total in t.children:
                lines.append(f"  {'':>9}    {name} {_fmt(total)}")
    lines += ["", _footer(summary)]
    return "\n".join(lines)


def _md_table(rows: list[list[str]], head: list[str]) -> list[str]:
    out = ["| " + " | ".join(head) + " |"]
    out.append("|" + "|".join([" --- "] + [" ---: "] * (len(head) - 1)) + "|")
    for r in rows:
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
    return out


def render_markdown(summary: Summary) -> str:
    lines = [f"### M3 timings: `{summary.directory}`", ""]
    if summary.steps:
        lines += _md_table(_rows(summary.steps[:TEXT_ROWS]), _HEAD)
    else:
        lines.append("No spans recorded.")
    if summary.counters:
        lines += ["", "#### Counters", ""]
        lines += _md_table(_rows(summary.counters[:TEXT_ROWS]), ["counter", *_HEAD[1:]])
    if summary.slowest_tests:
        lines += ["", "#### Slowest tests", ""]
        rows = [
            [
                f"`{t.test}`",
                _fmt(t.duration_ns),
                ", ".join(f"{n} {_fmt(v)}" for n, v in t.children),
            ]
            for t in summary.slowest_tests
        ]
        lines += _md_table(rows, ["test", "duration", "top steps"])
    lines += ["", f"_{_footer(summary)}_"]
    return "\n".join(lines) + "\n"


def finish(
    directory: str | os.PathLike[str], *, print_to: TextIO | None = None
) -> Summary | None:
    """Write reports, print the text summary, mirror it to the job summary."""
    root = Path(directory)
    if not root.is_dir():
        return None
    out = print_to if print_to is not None else sys.stdout
    try:
        summary = write_reports(root)
        print(render_text(summary), file=out)
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a", encoding="utf-8") as handle:
                handle.write(render_markdown(summary) + "\n")
        return summary
    except Exception as exc:
        warnings.warn(f"m3 timings report failed: {exc}", RuntimeWarning, stacklevel=2)
        return None
