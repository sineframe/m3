"""Writers wait for SQLite's write lock in short steps, never longer or
differently than SQLite's own busy timeout would make them wait."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from m3.storage import SQLiteExecutionStore
from m3.storage import sqlite as sqlite_storage

# Tests that start real lock-holding processes and assert timing are marked
# process_lifecycle so CI runs them serially; the rest run everywhere.
needs_fast_wait = pytest.mark.skipif(
    not sqlite_storage._FAST_WRITE_LOCK_WAIT_SUPPORTED,
    reason="the fast write-lock wait needs Python 3.11+",
)

_SRC = str(Path(__file__).parents[2].resolve() / "src")


def _database(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "results.sqlite"
    SQLiteExecutionStore(path).close()
    return path


def _write_suite(store: SQLiteExecutionStore) -> None:
    store.ensure_suite(f"suite-{uuid.uuid4().hex[:8]}")


def _write_project(store: SQLiteExecutionStore) -> None:
    store.ensure_project(str(uuid.uuid4()), "project")


def _elapsed(call: Callable[[], object]) -> tuple[BaseException | None, float]:
    started = time.monotonic()
    try:
        call()
    except Exception as exc:
        return exc, time.monotonic() - started
    return None, time.monotonic() - started


@pytest.fixture
def holder() -> Iterator[Callable[[Path, float], subprocess.Popen[str]]]:
    """Start another process that holds SQLite's write lock for ``seconds``."""
    processes: list[subprocess.Popen[str]] = []

    def start(database: Path, seconds: float) -> subprocess.Popen[str]:
        script = textwrap.dedent(
            f"""
            import sqlite3, time
            connection = sqlite3.connect({str(database)!r}, isolation_level=None)
            connection.execute("BEGIN IMMEDIATE")
            print("ready", flush=True)
            time.sleep({seconds})
            connection.execute("COMMIT")
            """
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONPATH": _SRC},
            stdout=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        return process

    yield start
    for process in processes:
        process.kill()
        process.wait(timeout=10)


@pytest.mark.process_lifecycle
@pytest.mark.parametrize("write", [_write_suite, _write_project])
def test_writer_proceeds_as_soon_as_another_process_commits(
    tmp_path: Path,
    holder: Callable[[Path, float], subprocess.Popen[str]],
    write: Callable[[SQLiteExecutionStore], None],
) -> None:
    database = _database(tmp_path)
    holder(database, 0.5)
    store = SQLiteExecutionStore(database, busy_timeout_ms=5000)

    error, elapsed = _elapsed(lambda: write(store))

    assert error is None
    assert elapsed < 2.0


@pytest.mark.process_lifecycle
@pytest.mark.parametrize("write", [_write_suite, _write_project])
def test_writer_gives_up_at_its_busy_timeout(
    tmp_path: Path,
    holder: Callable[[Path, float], subprocess.Popen[str]],
    write: Callable[[SQLiteExecutionStore], None],
) -> None:
    database = _database(tmp_path)
    holder(database, 30)
    store = SQLiteExecutionStore(database, busy_timeout_ms=500)

    error, elapsed = _elapsed(lambda: write(store))

    assert error is not None
    # Never sooner than the busy timeout, and at most the short polling
    # window later than SQLite's own handler alone.
    assert 0.5 <= elapsed < 2.5


@pytest.mark.process_lifecycle
def test_write_with_zero_busy_timeout_does_not_wait(
    tmp_path: Path, holder: Callable[[Path, float], subprocess.Popen[str]]
) -> None:
    database = _database(tmp_path)
    holder(database, 30)
    connection = SQLiteExecutionStore(database)._connect()
    try:
        connection.mark_settings_dirty()
        connection.execute("PRAGMA busy_timeout=0")
        error, elapsed = _elapsed(
            lambda: connection.execute(
                "UPDATE v2_projects SET project_name=project_name WHERE id='none'"
            )
        )
    finally:
        connection.close()

    assert error is not None
    assert elapsed < 0.5


@needs_fast_wait
def test_statements_inside_a_transaction_keep_sqlites_own_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteExecutionStore(_database(tmp_path))
    waited: list[str] = []
    original = sqlite_storage._CompatConnection._execute_waiting_for_write_lock

    def spy(self: object, statement: str, parameters: object) -> object:
        waited.append(statement.split()[0])
        return original(self, statement, parameters)  # type: ignore[arg-type]

    monkeypatch.setattr(
        sqlite_storage._CompatConnection, "_execute_waiting_for_write_lock", spy
    )
    _write_suite(store)  # BEGIN IMMEDIATE, then writes inside the transaction
    _write_project(store)  # one autocommit write

    assert waited == ["BEGIN", "INSERT"]


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="needs setitimer")
@pytest.mark.process_lifecycle
def test_interrupted_wait_leaves_nothing_behind(
    tmp_path: Path, holder: Callable[[Path, float], subprocess.Popen[str]]
) -> None:
    database = _database(tmp_path)
    process = holder(database, 30)
    store = SQLiteExecutionStore(database, busy_timeout_ms=5000)

    class Interrupted(Exception):
        pass

    def interrupt(*_: object) -> None:
        raise Interrupted

    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.2)
        with pytest.raises(Interrupted):
            _write_project(store)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    process.kill()
    process.wait(timeout=10)

    # The lock is free again: every later write, from any thread, is prompt
    # and every pooled connection is back at the store's timeout.
    errors: list[BaseException] = []

    def write() -> None:
        try:
            _write_project(store)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=write)
    error, elapsed = _elapsed(lambda: (_write_suite(store), thread.start()))
    thread.join(timeout=10)
    assert (error, errors) == (None, [])
    assert elapsed < 1.0
    with store._connect() as connection:
        timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 5000


def test_threads_writing_together_all_land(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(_database(tmp_path))
    errors: list[BaseException] = []

    def write() -> None:
        try:
            for _ in range(20):
                _write_project(store)
                _write_suite(store)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    with store._connect() as connection:
        projects = connection.execute("SELECT COUNT(*) FROM v2_projects").fetchone()[0]
    assert projects == 8 * 20


@pytest.mark.process_lifecycle
def test_waiting_leaves_no_files_beside_the_database(
    tmp_path: Path, holder: Callable[[Path, float], subprocess.Popen[str]]
) -> None:
    database = _database(tmp_path)
    holder(database, 0.3)
    _write_project(SQLiteExecutionStore(database))

    names = {path.name for path in tmp_path.iterdir()}
    assert names <= {
        "results.sqlite",
        "results.sqlite-wal",
        "results.sqlite-shm",
        "results.sqlite.blobs",
    }


def test_rollback_journal_databases_keep_sqlites_own_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Retrying there would drop the pending lock that keeps new readers out.
    store = SQLiteExecutionStore(tmp_path.resolve() / "journal.sqlite", wal=False)
    assert store.journal_mode != "wal"
    calls: list[str] = []
    monkeypatch.setattr(
        sqlite_storage._CompatConnection,
        "_execute_waiting_for_write_lock",
        lambda self, statement, parameters: calls.append(statement),
    )
    _write_project(store)

    assert calls == []


def test_stale_snapshot_is_not_retried(tmp_path: Path) -> None:
    # SQLITE_BUSY_SNAPSHOT: only a new read snapshot helps, so fail at once.
    database = _database(tmp_path)
    store = SQLiteExecutionStore(database, busy_timeout_ms=2000)
    _write_project(store)
    _write_project(store)
    connection = store._connect()
    try:
        rows = connection._connection.connection.dbapi_connection.execute(
            "SELECT id FROM v2_projects"
        )
        rows.fetchone()  # keep a read snapshot open on this connection
        _write_project(SQLiteExecutionStore(database))
        error, elapsed = _elapsed(
            lambda: connection.execute(
                "INSERT INTO v2_projects(id,project_name,created_at,updated_at) "
                "VALUES('x','x','t','t')"
            )
        )
        rows.close()
    finally:
        connection.close()

    assert error is not None
    # Retrying would poll for the whole fast-wait window first.
    assert elapsed < sqlite_storage._FAST_WRITE_LOCK_WAIT / 2


@needs_fast_wait
@pytest.mark.process_lifecycle
def test_interrupt_that_closes_the_connection_is_not_replaced(
    tmp_path: Path,
    holder: Callable[[Path, float], subprocess.Popen[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SQLAlchemy closes the DBAPI connection when an interrupt lands inside
    # it; restoring the timeout then fails, but the interrupt must surface.
    database = _database(tmp_path)
    holder(database, 30)
    store = SQLiteExecutionStore(database)
    original = sqlite_storage._CompatConnection._run
    attempts = 0

    def interrupted_run(
        self: sqlite_storage._CompatConnection, statement: str, parameters: object = ()
    ) -> object:
        nonlocal attempts
        if statement.startswith("INSERT"):
            attempts += 1
            if attempts == 2:
                # What SQLAlchemy does with an interrupt inside execution.
                self._connection.invalidate()
                raise KeyboardInterrupt
        return original(self, statement, parameters)  # type: ignore[arg-type]

    monkeypatch.setattr(sqlite_storage._CompatConnection, "_run", interrupted_run)
    with pytest.raises(KeyboardInterrupt):
        _write_project(store)


def test_without_busy_result_codes_writes_keep_sqlites_own_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before Python 3.11 sqlite3 cannot tell a busy lock from other errors.
    monkeypatch.setattr(sqlite_storage, "_FAST_WRITE_LOCK_WAIT_SUPPORTED", False)
    calls: list[str] = []
    monkeypatch.setattr(
        sqlite_storage._CompatConnection,
        "_execute_waiting_for_write_lock",
        lambda self, statement, parameters: calls.append(statement),
    )
    store = SQLiteExecutionStore(_database(tmp_path))
    _write_project(store)
    _write_suite(store)

    assert calls == []
