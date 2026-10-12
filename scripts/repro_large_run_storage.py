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
import selectors
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
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


class CheckpointOwner:
    """Fixture-only live socket leases; no descendant discovery or stale kills."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="m3-db-"))
        self.endpoint = self.root / "checkpoint"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.endpoint))
        self.endpoint.chmod(0o600)
        self.listener.listen()
        self.listener.setblocking(False)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.listener, selectors.EVENT_READ)
        self.peers: dict[socket.socket, dict[str, Any]] = {}
        self.invocations: dict[int, dict[str, Any]] = {}

    def register(self, index: int, directory: Path, *, victim: bool = False) -> Path:
        directory.mkdir(mode=0o700)
        (directory / "checkpoint.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "endpoint": str(self.endpoint),
                    "invocation": index,
                    "hold_victim": victim,
                }
            )
        )
        self.invocations[index] = {
            "process": None,
            "group": None,
            "checkpoint": None,
            "lifecycle": None,
        }
        return directory / "pytest-temp"

    def bind(self, index: int, process: subprocess.Popen[bytes]) -> None:
        self.invocations[index]["process"] = process

    def _message(self, channel: socket.socket, message: dict[str, Any]) -> None:
        peer = self.peers[channel]
        if peer.get("hello") is None:
            assert set(message) == {
                "version",
                "kind",
                "invocation",
                "role",
                "worker",
                "pid",
                "group",
            }, message
            assert message["version"] == 1 and message["kind"] == "hello"
            index, pid, group = message["invocation"], message["pid"], message["group"]
            assert type(index) is int and index in self.invocations
            assert type(pid) is int and type(group) is int and pid > 1 and group > 1
            assert group != os.getpgrp() and os.getpgid(pid) == group
            role = message["role"]
            assert role in ("controller", "worker")
            invocation = self.invocations[index]
            if role == "controller":
                assert pid == group and os.getsid(pid) == group
                assert invocation["group"] is None
                invocation["group"] = group
            else:
                assert isinstance(message["worker"], str)
                assert invocation["group"] == group
                assert not any(
                    other.get("hello", {}).get("invocation") == index
                    and other.get("hello", {}).get("worker") == message["worker"]
                    and other.get("hello", {}).get("role") == "worker"
                    for other in self.peers.values()
                )
            peer["hello"] = message
            if role == "controller":
                channel.sendall(b'{"action":"begin"}\n')
            workers = self.live(index, role="worker")
            if len(workers) == 4:
                for worker in workers:
                    worker.sendall(b'{"action":"begin"}\n')
            return
        hello = peer["hello"]
        assert message["kind"] == "unfinished" and hello["role"] == "worker"
        assert hello["invocation"] == 0 and message["trial"] == 0
        assert len(message["turn_ids"]) == 2
        assert self.invocations[0]["checkpoint"] is None
        self.invocations[0]["checkpoint"] = message

    def live(self, index: int, *, role: str | None = None) -> list[socket.socket]:
        return [
            channel
            for channel, peer in self.peers.items()
            if not peer["eof"]
            and peer.get("hello", {}).get("invocation") == index
            and (role is None or peer["hello"]["role"] == role)
        ]

    def pump(self, timeout: float) -> None:
        for key, _ in self.selector.select(max(0, timeout)):
            channel = key.fileobj
            if channel is self.listener:
                connection, _ = self.listener.accept()
                connection.setblocking(False)
                self.peers[connection] = {"buffer": b"", "eof": False}
                self.selector.register(connection, selectors.EVENT_READ)
                continue
            assert isinstance(channel, socket.socket)
            peer = self.peers[channel]
            try:
                data = channel.recv(4096)
            except ConnectionResetError:
                data = b""
            if not data:
                peer["eof"] = True
                self.selector.unregister(channel)
                channel.close()
                continue
            peer["buffer"] += data
            assert len(peer["buffer"]) <= 4096, "fixture checkpoint exceeds bound"
            while b"\n" in peer["buffer"]:
                frame, peer["buffer"] = peer["buffer"].split(b"\n", 1)
                self._message(channel, json.loads(frame))

    def signal_groups(self, indices: list[int], sig: signal.Signals) -> None:
        for index in indices:
            # Every kill target is anchored by a retained live lease and a
            # fresh OS identity check; EOF removes that lease's authority.
            for channel in self.live(index):
                hello = self.peers[channel]["hello"]
                try:
                    if os.getpgid(hello["pid"]) != hello["group"]:
                        raise RuntimeError("fixture worker changed owned process group")
                    os.killpg(hello["group"], sig)
                    break
                except ProcessLookupError:
                    continue

    def groups_dead(self, indices: list[int]) -> bool:
        for index in indices:
            group = self.invocations[index]["group"]
            if group is None:
                continue
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                return False
            return False
        return True

    def close(self) -> None:
        for channel in self.peers:
            channel.close()
        self.selector.close()
        self.listener.close()
        self.endpoint.unlink()
        self.root.rmdir()


def stop(
    processes: list[subprocess.Popen[bytes]],
    owner: CheckpointOwner | None = None,
    *,
    grace_seconds: float = 20,
) -> None:
    indices = (
        []
        if owner is None
        else [
            index
            for index, invocation in owner.invocations.items()
            if invocation["process"] in processes
        ]
    )
    for process in processes:
        if process.poll() is None:
            process.terminate()
    end = time.monotonic() + grace_seconds
    survivors = []
    for process in processes:
        try:
            process.wait(timeout=max(0, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            survivors.append(process)
    if owner is not None and survivors:
        owner.pump(0)
        owner.signal_groups(indices, signal.SIGTERM)
        # The CLI must run its pending handler to reap its own pytest child.
        # We never claim to reap grandchildren from this driver process.
        for process in survivors:
            if process.poll() is None:
                process.send_signal(signal.SIGCONT)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            owner.pump(min(0.1, end - time.monotonic()))
            if not any(owner.live(index) for index in indices) and owner.groups_dead(
                indices
            ):
                break
        owner.signal_groups(indices, signal.SIGKILL)
    for process in survivors:
        if process.poll() is None:
            process.kill()
    kill_end = time.monotonic() + 5
    for process in survivors:
        process.wait(timeout=max(0, kill_end - time.monotonic()))
    if owner is not None:
        end = time.monotonic() + 5
        while time.monotonic() < end:
            owner.pump(min(0.1, end - time.monotonic()))
            if not any(owner.live(index) for index in indices) and owner.groups_dead(
                indices
            ):
                break
        for index in indices:
            peers = [
                peer
                for peer in owner.peers.values()
                if peer.get("hello", {}).get("invocation") == index
            ]
            owner.invocations[index]["lifecycle"] = {
                "registered_workers": sum(
                    peer["hello"]["role"] == "worker" for peer in peers
                ),
                "peer_eof": all(peer["eof"] for peer in peers),
                "group_dead": owner.groups_dead([index]),
                "cli_reaped": owner.invocations[index]["process"].poll() is not None,
            }
        if any(owner.live(index) for index in indices) or not owner.groups_dead(
            indices
        ):
            raise RuntimeError("owned fixture worker cleanup incomplete")
    if survivors:
        raise RuntimeError("CLI did not complete termination cleanup")


def unfinished_session(database: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT s.state,s.closed_at,e.snapshot_json,e.run_id FROM v2_sessions s "
            "JOIN v2_executions e ON s.execution_id=e.id WHERE s.id=? AND e.id=?",
            (checkpoint["session_id"], checkpoint["execution_id"]),
        ).fetchone()
        assert row is not None
        state, closed, snapshot_json, run_id = row
        snapshot = json.loads(snapshot_json)
        assert closed is None and state not in ("closed", "finished"), row
        assert snapshot["finished_at"] is None and snapshot["run_id"] == run_id
        turns = connection.execute(
            "SELECT id FROM v2_turns WHERE session_id=? ORDER BY number",
            (checkpoint["session_id"],),
        ).fetchall()
        assert [turn[0] for turn in turns] == checkpoint["turn_ids"]
        assert len(turns) == 2
        results = connection.execute(
            "SELECT COUNT(*) FROM v2_test_results WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        assert results == 0, "victim completed a trial before unfinished checkpoint"
        record = json.loads(
            connection.execute(
                "SELECT record_json FROM v2_test_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        )
        assert record["status"] == "running" and record.get("exit_status") is None
    return {
        **checkpoint,
        "run_id": run_id,
        "session_state": state,
        "completed_test_results": results,
    }


def counts_for_run(database: Path, run_id: str) -> dict[str, int]:
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        return {
            "results": connection.execute(
                "SELECT COUNT(*) FROM v2_test_results WHERE run_id=?", (run_id,)
            ).fetchone()[0],
            "executions": connection.execute(
                "SELECT COUNT(*) FROM v2_executions WHERE run_id=?", (run_id,)
            ).fetchone()[0],
            "sessions": connection.execute(
                "SELECT COUNT(*) FROM v2_sessions s JOIN v2_executions e "
                "ON s.execution_id=e.id WHERE e.run_id=?",
                (run_id,),
            ).fetchone()[0],
            "turns": connection.execute(
                "SELECT COUNT(*) FROM v2_turns t JOIN v2_sessions s ON t.session_id=s.id "
                "JOIN v2_executions e ON s.execution_id=e.id WHERE e.run_id=?",
                (run_id,),
            ).fetchone()[0],
        }


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
    interrupted: dict[str, Any] | None = None
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
        owner = CheckpointOwner()
        stack.callback(owner.close)
        try:
            for index in range(6):
                log = stack.enter_context((case / f"process-{index}.log").open("wb"))
                base = owner.register(
                    index,
                    case / f"bootstrap-{index}",
                    victim=terminate_one and index == 0,
                )
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
                            "--basetemp",
                            str(base),
                        ],
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                )
                owner.bind(index, processes[-1])
            while any(process.poll() is None for process in processes):
                owner.pump(0.1)
                checkpoint = owner.invocations[0]["checkpoint"]
                if terminate_one and terminated is None and checkpoint is not None:
                    assert len(owner.live(0, role="worker")) == 4
                    interrupted = unfinished_session(databases[0], checkpoint)
                    processes[0].terminate()
                    terminated = 0
                for database in set(databases):
                    if not database.exists():
                        continue
                    try:
                        with sqlite3.connect(
                            database.as_uri() + "?mode=ro", uri=True, timeout=0.2
                        ) as connection:
                            connection.execute(
                                "SELECT COUNT(*) FROM v2_turns"
                            ).fetchone()[0]
                            connection.execute(
                                "SELECT COUNT(*) FROM v2_events"
                            ).fetchone()
                        read_passes += 1
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
        finally:
            stop(processes, owner)
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
        assert terminated == 0 and interrupted is not None, (
            "victim did not checkpoint an unfinished session"
        )
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
    survivor_counts = {}
    for database_name, report in reports.items():
        database = next(path for path in set(databases) if path.name == database_name)
        old_runs = {run["run_id"] for run in before[database_name]["runs"]}
        for run in report["runs"]:
            if run["run_id"] in old_runs:
                continue
            if interrupted is not None and run["run_id"] == interrupted["run_id"]:
                victim_counts = counts_for_run(database, run["run_id"])
                assert victim_counts["turns"] == 2 and victim_counts["results"] < 12
                interrupted["final_records"] = victim_counts
                continue
            assert run["status"] == "finished" and run["exit_status"] == 0
            survivor_counts[run["run_id"]] = counts_for_run(database, run["run_id"])
            assert survivor_counts[run["run_id"]] == {
                "results": 12,
                "executions": 24,
                "sessions": 12,
                "turns": 96,
            }
    assert len(survivor_counts) == (5 if terminate_one else 6)
    lifecycle = {index: item["lifecycle"] for index, item in owner.invocations.items()}
    assert all(item["registered_workers"] == 4 for item in lifecycle.values())
    result = {
        "case": name,
        "seconds": time.monotonic() - started,
        "exit_codes": codes,
        "terminated_process": terminated,
        "interrupted_session": interrupted,
        "completed_survivors": survivor_counts,
        "lifecycle": lifecycle,
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
