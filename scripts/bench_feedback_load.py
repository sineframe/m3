"""Benchmark the pytest finish path: ``load_run_entries`` and ``export_feedback``.

Run against any checkout by pointing uv at its ``sdk`` project (the script
prints ``m3.__file__`` so you can confirm which checkout was imported)::

    uv run --project <checkout>/sdk --extra storage python scripts/bench_feedback_load.py
    uv run --project <checkout>/sdk --extra storage python scripts/bench_feedback_load.py \
        --repeats 2 --sizes 100x5 --no-real

Scenarios (median and p95 over ``--repeats`` after one warm-up):

1. Real data (``--real``/``--no-real``, on when the source database exists):
   a copy of ``--source`` (default ``.m3/executions.sqlite`` in the working
   directory, plus -wal/-shm and the ``.blobs`` sibling) is made once into
   ``--workdir``.
   ``load_cold`` NULLs ``tool_call_counts_json`` before each repeat,
   ``load_warm`` runs with the cache populated, ``export`` times
   ``export_feedback`` only.
2. Synthetic (``--synthetic``/``--no-synthetic``): a fresh store per
   ``NxT`` in ``--sizes`` (default ``100x5,100x50,500x5,500x50``: N finished
   executions in one run, T tool calls each), same three measurements.

Counters come from one extra instrumented run (not the timed ones):
``load_events_calls`` is the number of ``SQLiteExecutionStore._load_events``
calls; ``cache_update_statements`` is the number of cursor executions of
``UPDATE v2_executions SET tool_call_counts_json`` (an ``executemany`` counts
as one statement; ``cache_update_rows`` counts the parameter sets).

Targets: one ``_load_events`` call per execution instead of two, and the
cache UPDATEs for a page of executions written in one transaction.
Prints one JSON line per result.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import m3
from m3.events import EventFactory, EventSequence
from m3.feedback import build_feedback, export_feedback, load_run_entries
from m3.storage import SQLiteExecutionStore
from m3.types import (
    EventDirection,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    RequestLink,
)

DEFAULT_SOURCE = ".m3/executions.sqlite"
CACHE_UPDATE = "UPDATE v2_executions SET tool_call_counts_json"


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _counters(fn: Callable[[], Any], store: SQLiteExecutionStore) -> dict[str, int]:
    from sqlalchemy import event

    counts = {
        "load_events_calls": 0,
        "cache_update_statements": 0,
        "cache_update_rows": 0,
    }
    original = SQLiteExecutionStore._load_events

    def counting(self: Any, *args: Any, **kwargs: Any) -> Any:
        counts["load_events_calls"] += 1
        return original(self, *args, **kwargs)

    def before(
        conn: Any, cursor: Any, statement: str, params: Any, ctx: Any, many: bool
    ) -> None:
        if statement.lstrip().startswith(CACHE_UPDATE):
            counts["cache_update_statements"] += 1
            counts["cache_update_rows"] += len(params) if many else 1

    SQLiteExecutionStore._load_events = counting  # type: ignore[method-assign]
    event.listen(store._engine, "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(store._engine, "before_cursor_execute", before)
        SQLiteExecutionStore._load_events = original  # type: ignore[method-assign]
    return counts


def _measure(
    scenario: str,
    measurement: str,
    store: SQLiteExecutionStore,
    run: Callable[[], Any],
    repeats: int,
    *,
    prepare: Callable[[], None] = lambda: None,
    extra: dict[str, Any],
) -> None:
    prepare()
    run()  # warm-up
    times = []
    for _ in range(repeats):
        prepare()
        start = time.perf_counter()
        run()
        times.append(time.perf_counter() - start)
    prepare()
    counters = _counters(run, store)
    print(
        json.dumps(
            {
                "scenario": scenario,
                "measurement": measurement,
                **extra,
                "median_s": round(statistics.median(times), 5),
                "p95_s": round(_pct(times, 0.95), 5),
                **counters,
                "m3_file": m3.__file__,
            }
        ),
        flush=True,
    )


def _run_all(
    scenario: str,
    store: SQLiteExecutionStore,
    db: Path,
    run_id: str,
    repeats: int,
    work: Path,
    extra: dict[str, Any],
) -> None:
    def clear_cache() -> None:
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE v2_executions SET tool_call_counts_json=NULL")

    entries = load_run_entries(store, run_id)
    extra = {"n_executions": len(entries), **extra}
    _measure(
        scenario,
        "load_cold",
        store,
        lambda: load_run_entries(store, run_id),
        repeats,
        prepare=clear_cache,
        extra=extra,
    )
    load_run_entries(store, run_id)  # repopulate cache
    _measure(
        scenario,
        "load_warm",
        store,
        lambda: load_run_entries(store, run_id),
        repeats,
        extra=extra,
    )
    entries = load_run_entries(store, run_id)
    feedback = build_feedback(store, run_id, entries=entries)

    def export() -> None:
        export_feedback(feedback, store, tempfile.mkdtemp(dir=work), entries=entries)

    _measure(scenario, "export", store, export, repeats, extra=extra)


def _seed(store: SQLiteExecutionStore, run_id: str, n: int, calls: int) -> None:
    """Persist ``n`` finished executions shaped like ``_run_execution`` in tests."""
    for i in range(n):
        execution_id = ExecutionId(f"exec-{i}")
        store.create(ExecutionState(execution_id=execution_id, run_id=run_id))
        factory = EventFactory(execution_id, allocator=EventSequence(start=0))
        events = [
            factory.create(
                EventKind.EXECUTION_CREATED, payload={"trace_id": f"trace-{i}"}
            )
        ]
        for index in range(1, calls + 1):

            def link(direction: EventDirection, index: int = index) -> RequestLink:
                return RequestLink(
                    jsonrpc_id=index, direction=direction, request_sequence=index
                )

            events.append(
                factory.create(
                    EventKind.TOOL_CALL_REQUESTED,
                    connection_id="connection",
                    correlation=link(EventDirection.CLIENT_TO_SERVER),
                    payload={
                        "method": "tools/call",
                        "call_id": f"call-{index}",
                        "params": {"name": "echo", "arguments": {}},
                    },
                )
            )
            events.append(
                factory.create(
                    EventKind.TOOL_RESULT_RECEIVED,
                    connection_id="connection",
                    correlation=link(EventDirection.SERVER_TO_CLIENT),
                    payload={
                        "method": "tools/call",
                        "result": {
                            "content": [{"type": "text", "text": "x"}],
                            "isError": index % 7 == 0,
                        },
                    },
                )
            )
        events.append(
            factory.create(
                EventKind.EXECUTION_FINISHED,
                payload={
                    "outcome": ExecutionOutcome.COMPLETED.value,
                    "completeness": "complete",
                    "limitations": [],
                },
            )
        )
        store.append_events(events)


def _real(args: argparse.Namespace, work: Path) -> None:
    source = Path(args.source)
    target = work / "real"
    target.mkdir(parents=True, exist_ok=True)
    db = target / source.name
    for suffix in ("", "-wal", "-shm"):
        if Path(f"{source}{suffix}").exists():
            shutil.copy2(f"{source}{suffix}", f"{db}{suffix}")
    blobs = Path(f"{source}.blobs")
    if blobs.exists():
        shutil.copytree(blobs, Path(f"{db}.blobs"), dirs_exist_ok=True)
    # The store derives its blob root from the database path (<db>.blobs).
    store = SQLiteExecutionStore(db)
    run_id = args.run
    if run_id is None:
        with sqlite3.connect(db) as connection:
            row = connection.execute(
                "SELECT run_id FROM v2_executions WHERE run_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            print(json.dumps({"scenario": "real", "skipped": "no run ids in source"}))
            return
        run_id = row[0]
    _run_all(
        "real",
        store,
        db,
        run_id,
        args.repeats,
        work,
        {"tool_calls": None, "run_id": run_id},
    )


def _synthetic(args: argparse.Namespace, work: Path) -> None:
    for size in args.sizes.split(","):
        n, calls = (int(part) for part in size.lower().split("x"))
        db = work / f"synthetic-{n}x{calls}.sqlite"
        store = SQLiteExecutionStore(db)
        _seed(store, "run-bench", n, calls)
        assert store.get_trace_view("exec-0") is not None
        _run_all(
            "synthetic",
            store,
            db,
            "run-bench",
            args.repeats,
            work,
            {"tool_calls": calls},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument(
        "--sizes",
        default="100x5,100x50,500x5,500x50",
        help="NxT list, e.g. 100x5,500x50",
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument(
        "--run", default=None, help="run id for the real scenario (default: latest)"
    )
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--real", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--synthetic", action=argparse.BooleanOptionalAction, default=True
    )
    args = parser.parse_args()
    real = Path(args.source).exists() if args.real is None else args.real
    # Resolve: the store rejects symlinked paths (macOS $TMPDIR is under /var).
    work = Path(args.workdir or tempfile.mkdtemp(prefix="bench_feedback_")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"m3_file": m3.__file__, "workdir": str(work)}), flush=True)
    if real:
        _real(args, work)
    if args.synthetic:
        _synthetic(args, work)


if __name__ == "__main__":
    main()
