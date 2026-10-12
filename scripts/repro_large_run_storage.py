"""Exercise six real CLI processes, four pytest workers each, without providers.

Run with a Python containing the SDK, CLI and pytest-xdist dependencies:
    python scripts/repro_large_run_storage.py --output /tmp/m3-storage-repro

The output directory must be new. Reports distinguish a deliberately terminated
invocation from completed runs. This is a contention repro, not a latency gate
or a native-harness compatibility test.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

TABLES = (
    "v2_test_runs",
    "v2_test_results",
    "v2_executions",
    "v2_sessions",
    "v2_turns",
    "v2_events",
)


def inspect_database(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        counts = {
            name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in TABLES
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        sequence_gaps = connection.execute(
            "SELECT execution_id FROM v2_events GROUP BY execution_id "
            "HAVING MIN(sequence) != 0 OR MAX(sequence) + 1 != COUNT(*)"
        ).fetchall()
        runs = [
            json.loads(row[0])
            for row in connection.execute("SELECT record_json FROM v2_test_runs")
        ]
    return {
        "counts": counts,
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "sequence_gaps": sequence_gaps,
        "runs": [
            {key: run.get(key) for key in ("run_id", "status", "exit_status")}
            for run in runs
        ],
    }


def refresh_feedback(path: Path) -> int:
    from m3.feedback import build_feedback, load_run_entries
    from m3.storage import SQLiteExecutionStore

    store = SQLiteExecutionStore(path)
    try:
        refreshed = 0
        for run in store.list_test_runs():
            run_id = str(run["run_id"])
            entries = load_run_entries(store, run_id)
            feedback = build_feedback(store, run_id, entries=entries)
            assert feedback.run_id == run_id
            refreshed += 1
        return refreshed
    finally:
        store.close()


def stop(processes: list[subprocess.Popen[bytes]]) -> None:
    # The CLI owns and stops its pytest process group on SIGTERM. Give that
    # existing cleanup contract time to finish before declaring driver failure.
    for process in processes:
        if process.poll() is None:
            process.terminate()
    end = time.monotonic() + 20
    survivors = []
    for process in processes:
        try:
            process.wait(timeout=max(0, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            survivors.append(process)
    for process in survivors:
        if process.poll() is None:
            process.kill()
    kill_end = time.monotonic() + 5
    for process in survivors:
        process.wait(timeout=max(0, kill_end - time.monotonic()))
    if survivors:
        raise RuntimeError("CLI did not complete termination cleanup")


def run_case(
    root: Path,
    output: Path,
    python: Path,
    name: str,
    *,
    databases: tuple[Path, ...],
    terminate_one: bool = False,
) -> dict[str, Any]:
    case = output / name
    case.mkdir()
    environment = dict(os.environ)
    source_paths = [str(root / package / "src") for package in ("sdk", "cli", "app")]
    if environment.get("PYTHONPATH"):
        source_paths.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(source_paths)
    fixture = root / "scripts" / "fixtures" / "large_run_storage"
    started = time.monotonic()
    processes: list[subprocess.Popen[bytes]] = []
    read_passes = 0
    refresh_passes = 0
    errors: list[str] = []
    terminated: int | None = None
    next_refresh = started
    before = {
        path.name: inspect_database(path)
        if path.exists()
        else {
            "counts": dict.fromkeys(TABLES, 0),
            "runs": [],
        }
        for path in set(databases)
    }
    with ExitStack() as stack:
        try:
            for index in range(6):
                log = stack.enter_context((case / f"process-{index}.log").open("wb"))
                processes.append(
                    subprocess.Popen(
                        [
                            str(python),
                            "-m",
                            "m3_cli",
                            "test",
                            "--python",
                            str(python),
                            "--project-root",
                            str(fixture),
                            "--results-db",
                            str(databases[index % len(databases)]),
                            "-n",
                            "4",
                            "--",
                            "test_writers.py",
                            "-q",
                        ],
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                )
            while any(process.poll() is None for process in processes):
                for database in set(databases):
                    if not database.exists():
                        continue
                    try:
                        with sqlite3.connect(
                            database.as_uri() + "?mode=ro", uri=True, timeout=0.2
                        ) as connection:
                            turns = connection.execute(
                                "SELECT COUNT(*) FROM v2_turns"
                            ).fetchone()[0]
                            connection.execute(
                                "SELECT COUNT(*) FROM v2_events"
                            ).fetchone()
                        read_passes += 1
                        if (
                            terminate_one
                            and terminated is None
                            and turns
                            >= before[database.name]["counts"]["v2_turns"] + 24
                        ):
                            victim = next(
                                (
                                    i
                                    for i, p in enumerate(processes)
                                    if p.poll() is None
                                ),
                                None,
                            )
                            if victim is not None:
                                processes[victim].terminate()
                                terminated = victim
                        if time.monotonic() >= next_refresh:
                            refresh_passes += refresh_feedback(database)
                            next_refresh = time.monotonic() + 1
                    except sqlite3.OperationalError as error:
                        # A cold database can exist before its schema transaction
                        # commits. Every other read/refresh failure is evidence.
                        if "no such table" not in str(error):
                            errors.append(str(error))
                if time.monotonic() - started > 180:
                    raise RuntimeError("storage workload exceeded 180-second deadline")
                time.sleep(0.1)
        finally:
            stop(processes)
    reports = {path.name: inspect_database(path) for path in set(databases)}
    logs = [(case / f"process-{i}.log").read_text(errors="replace") for i in range(6)]
    failures = [
        text
        for text in logs
        if "database is locked" in text or "cannot rollback" in text
    ]
    codes = [process.returncode for process in processes]
    assert all(code == 0 for i, code in enumerate(codes) if i != terminated), codes
    if terminate_one:
        assert terminated is not None, "termination did not overlap active writers"
        assert codes[terminated] != 0, "terminated invocation unexpectedly succeeded"
    assert not errors, errors
    assert not failures, failures
    for report in reports.values():
        assert report["integrity"] == [("ok",)]
        assert not report["foreign_key_errors"]
        assert not report["sequence_gaps"]
    changes = {
        name: {
            table: report["counts"][table] - before[name]["counts"][table]
            for table in TABLES
        }
        for name, report in reports.items()
    }
    if not terminate_one:
        expected = {
            "v2_test_runs": 6,
            "v2_test_results": 72,
            "v2_executions": 144,
            "v2_sessions": 72,
            "v2_turns": 576,
        }
        for table, count in expected.items():
            assert sum(change[table] for change in changes.values()) == count, changes
        for database_name, report in reports.items():
            old_runs = {run["run_id"] for run in before[database_name]["runs"]}
            assert all(
                run["status"] == "finished" and run["exit_status"] == 0
                for run in report["runs"]
                if run["run_id"] not in old_runs
            )
    result = {
        "case": name,
        "seconds": time.monotonic() - started,
        "exit_codes": codes,
        "terminated_process": terminated,
        "reader_passes": read_passes,
        "feedback_refreshes": refresh_passes,
        "reader_errors": errors,
        "new_records": changes,
        "databases": reports,
    }
    (case / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("this termination repro currently requires POSIX CLI cleanup")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    python = args.python.absolute()
    shared = (output / "shared.sqlite",)
    separate = tuple(output / f"separate-{index}.sqlite" for index in range(6))
    # Make source packages available to the driver feedback reader too.
    sys.path[:0] = [str(root / package / "src") for package in ("sdk", "cli", "app")]
    results = [
        run_case(root, output, python, "shared-cold", databases=shared),
        run_case(root, output, python, "shared-warm", databases=shared),
        run_case(root, output, python, "separate-cold", databases=separate),
        run_case(
            root,
            output,
            python,
            "shared-terminated",
            databases=shared,
            terminate_one=True,
        ),
        run_case(root, output, python, "shared-recovery", databases=shared),
    ]
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"output": str(output), "cases": len(results)}))


if __name__ == "__main__":
    main()
