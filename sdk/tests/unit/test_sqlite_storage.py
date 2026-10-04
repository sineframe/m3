"""Focused contract checks for the fresh persistent storage boundary."""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pytest
from test_trace_dedup_equivalence import _corpus, _normalized

from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
)
from m3.events import EventFactory, EventSequence
from m3.storage import (
    ArtifactNotFound,
    BlobRecord,
    InMemoryArtifactStore,
    InMemoryExecutionStore,
    SequenceConflict,
    SQLiteArtifactStore,
    SQLiteExecutionStore,
    StorageConflict,
    StorageError,
    TerminalConflict,
)
from m3.storage import sqlite as sqlite_storage
from m3.trace.redaction import RedactionConfig
from m3.types import (
    ArtifactRef,
    ConnectionId,
    DirectSpec,
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventSource,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    ExecutionStatus,
    Ping,
    RequestLink,
    RevisionSelection,
    SecretReference,
    ServerBinding,
    SessionId,
    StdioServer,
    TurnId,
    TurnState,
)
from m3.types import (
    Event as StoredEvent,
)


def _store(tmp_path: Path) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(tmp_path / "m3.sqlite", blob_root=tmp_path / "blobs")


def _created(
    store: SQLiteExecutionStore, name: str = "execution-1"
) -> tuple[ExecutionId, EventFactory]:
    execution_id = ExecutionId(name)
    store.create(ExecutionState(execution_id=execution_id))
    factory = EventFactory(execution_id)
    store.append_events([factory.create(EventKind.EXECUTION_CREATED, payload={})])
    return execution_id, factory


def _run_execution(
    store: SQLiteExecutionStore,
    name: str,
    run_id: str,
    tool_results: list[bool],
    *,
    finished: bool = True,
) -> ExecutionId:
    """Persist an execution whose tool calls succeed (True) or fail (False)."""
    execution_id = ExecutionId(name)
    store.create(ExecutionState(execution_id=execution_id, run_id=run_id))
    factory = EventFactory(execution_id, allocator=EventSequence(start=0))
    events = [
        factory.create(
            EventKind.EXECUTION_CREATED, payload={"trace_id": f"trace-{name}"}
        )
    ]
    for index, succeeded in enumerate(tool_results, 1):

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
                        "isError": not succeeded,
                    },
                },
            )
        )
    if finished:
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
    return execution_id


def _stored_counts(tmp_path: Path, execution_id: ExecutionId) -> str | None:
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        row = connection.execute(
            "SELECT tool_call_counts_json FROM v2_executions WHERE id=?",
            (execution_id.root,),
        ).fetchone()
    return row[0]


def test_memory_and_sqlite_retain_defensive_typed_execution_specs(
    tmp_path: Path,
) -> None:
    spec = DirectSpec(
        servers=(ServerBinding(server=StdioServer(name="server", command="echo")),),
        operation=Ping(server="server"),
    )
    execution_id = ExecutionId("spec-parity")
    memory = InMemoryExecutionStore()
    memory.create(
        ExecutionState(execution_id=execution_id),
        specification=spec.model_dump(mode="json"),
    )
    memory_spec = memory.get_execution_spec(execution_id)
    assert memory_spec == spec
    assert memory_spec is not None
    memory_spec = memory_spec.model_copy(update={"metadata": {"mutated": True}})
    assert "mutated" not in memory.get_execution_spec(execution_id).metadata  # type: ignore[union-attr]

    sqlite = _store(tmp_path)
    sqlite.create(
        ExecutionState(execution_id=execution_id),
        specification=spec.model_dump(mode="json"),
    )
    assert sqlite.get_execution_spec(execution_id) == spec
    sqlite.close()


def test_storage_import_is_lazy_without_sqlalchemy(tmp_path: Path) -> None:
    """The base package remains importable; selecting SQLite fails clearly."""
    script = """
import builtins
real_import = builtins.__import__
def deny_sqlalchemy(name, *args, **kwargs):
    if name == 'sqlalchemy' or name.startswith('sqlalchemy.'):
        raise ModuleNotFoundError('blocked for optional-dependency test')
    return real_import(name, *args, **kwargs)
builtins.__import__ = deny_sqlalchemy
import m3.storage
from m3.storage import SQLiteExecutionStore
try:
    SQLiteExecutionStore('optional-dependency-test.sqlite')
except ModuleNotFoundError as error:
    assert str(error) == 'SQLite storage requires the optional dependency; install sf-m3[storage]'
    import os
    assert not os.path.exists('optional-dependency-test.sqlite')
else:
    raise AssertionError('SQLite storage unexpectedly initialized without SQLAlchemy')
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_sqlite_managed_input_uses_expanded_database_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)

    store = SQLiteExecutionStore("~/m3.sqlite")
    expected = home / "m3.sqlite"
    assert store.database == expected
    assert store.managed_input_store.database == expected
    assert store.artifacts.database == expected

    execution_id = ExecutionId("managed-expanded-path")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(
        execution_id,
        payload={"human_input": "managed"},
    )
    claimed = store.claim_next("worker-a", lease_seconds=0.01)
    assert claimed is not None and claimed[0].id == command.id
    _, lease = claimed
    time.sleep(0.03)
    assert store.mark_managed_recovery_unavailable_if_lease_lost(lease)
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED


def test_events_are_commit_gated_and_snapshots_reload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    event = factory.create(EventKind.DIAGNOSTIC, payload={"message": "safe"})
    with store.transaction(execution_id) as transaction:
        transaction.append([event])
        assert tuple(store.iter_events(execution_id)) == tuple(
            store.iter_events(execution_id, after_sequence=-1)
        )
    assert tuple(store.iter_events(execution_id))[-1].payload["message"] == "safe"
    reloaded = SQLiteExecutionStore(
        tmp_path / "m3.sqlite", blob_root=tmp_path / "blobs"
    )
    assert reloaded.get_snapshot(execution_id) == store.get_snapshot(execution_id)


def test_profile_revisions_archive_and_explicit_latest_clone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    profile = store.create_server_profile("server", {"url": "https://one.example"})
    first = store.resolve_revision(profile.id)
    second = store.add_revision(profile.id, {"url": "https://two.example"})
    assert (
        store.resolve_revision(
            profile.id,
            RevisionSelection(mode="pinned", revision_id=first.id, revision_number=1),
        ).id
        == first.id
    )
    assert store.resolve_revision(profile.id).id == second.id
    store.archive_profile(profile.id)
    assert store.get_profile(profile.id).archived is True  # type: ignore[union-attr]
    execution_id = ExecutionId("clone-source")
    store.create(
        ExecutionState(execution_id=execution_id),
        server_bindings=[{"profile_id": profile.id, "revision_id": first.id.root}],
    )
    clone = store.clone_execution(execution_id, use_latest=True)
    assert clone != execution_id
    assert store.resolved_bindings(clone)["servers"][0]["revision_id"] == second.id.root
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE v2_server_profiles SET current_revision_id=? WHERE id=?",
                ("missing-revision", profile.id),
            )


def test_profile_listing_is_kind_scoped_archived_filtered_and_revision_ordered(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    server = store.create_profile(
        "server", "zulu", {"mcpServers": {"z": {"command": "echo"}}}
    )
    harness = store.create_profile(
        "harness",
        "alpha",
        {"manifest": {"command": "agent"}, "trusted_unsandboxed": False},
    )
    store.add_revision(
        server.id,
        {"mcpServers": {"z": {"command": "echo", "args": ["2"]}}},
        revision_id="server-revision-2",
    )
    store.archive_profile(server.id)

    assert [item.id for item in store.list_profiles("server")] == []
    assert [
        item.id for item in store.list_profiles("server", include_archived=True)
    ] == [server.id]
    assert [item.id for item in store.list_profiles("harness")] == [harness.id]
    assert [
        item.revision_number for item in store.list_profile_revisions(server.id)
    ] == [1, 2]
    with pytest.raises(ValueError, match="profile kind"):
        store.list_profiles("not-a-kind")
    with pytest.raises(StorageConflict, match="does not exist"):
        store.list_profile_revisions("missing-profile")


def test_profile_metadata_update_conflict_and_durable_redaction(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite",
        blob_root=tmp_path / "blobs",
        config=RedactionConfig(
            secrets=frozenset({"super-secret"}), include_environment=False
        ),
    )
    first = store.create_server_profile(
        "first", {"token": SecretReference(source="environment", name="MCP_TOKEN")}
    )
    second = store.create_server_profile(
        "second", {"token": SecretReference(source="environment", name="MCP_TOKEN")}
    )
    updated = store.update_profile(first.id, name="renamed", description="safe")
    assert updated.name == "renamed"
    assert store.resolve_revision(first.id).value["token"] == {
        "source": "environment",
        "name": "MCP_TOKEN",
    }
    with pytest.raises(StorageConflict, match="name"):
        store.update_profile(second.id, name="renamed")


def test_content_addressed_blobs_are_shared_and_collected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first, _ = _created(store, "one")
    second, _ = _created(store, "two")
    a = store.artifacts.put(first, "a.txt", b"same")
    b = store.artifacts.put(second, "b.txt", b"same")
    assert a.sha256 == b.sha256
    store.artifacts.delete(a)
    assert store.artifacts.get(b) == b"same"
    store.artifacts.delete(b)
    store.artifacts.cleanup()
    with pytest.raises(ArtifactNotFound):
        store.artifacts.get(b)


def test_active_delete_rejected_and_terminal_delete_cascades(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    with pytest.raises(StorageConflict):
        store.delete_execution(execution_id)
    store.append_events(
        [
            factory.create(
                EventKind.EXECUTION_FINISHED,
                payload={"outcome": ExecutionOutcome.COMPLETED.value},
            )
        ]
    )
    assert store.get_snapshot(execution_id).lifecycle is ExecutionStatus.FINISHED  # type: ignore[union-attr]
    store.delete_execution(execution_id)
    assert store.get_snapshot(execution_id) is None


def test_terminal_delete_removes_managed_input_rows_atomically(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store, "managed-delete")
    other_id, other_factory = _created(store, "managed-retained")
    managed = store.managed_input_store

    def create_round(execution: ExecutionId, round_id: str) -> None:
        managed.create_round(
            PendingElicitationRound(
                round_id=round_id,
                execution_id=execution.root,
                logical_operation_id=f"operation-{round_id}",
                server="shipping",
                operation_kind="tool",
                operation_name="book",
                requests={
                    "address": FormElicitationRequest(
                        request_key="address",
                        message="Address",
                        requested_schema={"type": "object"},
                    )
                },
                created_at=datetime.now(timezone.utc),
            ),
            round_index=0,
            round_limit=10,
            owner_id=f"worker-{round_id}",
            lease_seconds=30,
        )

    create_round(execution_id, "round-managed-delete")
    create_round(other_id, "round-managed-retained")
    record = managed.get_round(execution_id.root, "round-managed-delete")
    assert record is not None
    managed.submit_responses(
        execution_id.root,
        record.round_id,
        {
            "address": ElicitationResponse(
                action="accept", content={"value": "exact-value"}
            )
        },
        owner_id=record.owner_id,
        lease_token=record.lease_token,
        response_idempotency_key="managed-delete-response",
    )
    other_record = managed.get_round(other_id.root, "round-managed-retained")
    assert other_record is not None

    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM m3_managed_input_diagnostics WHERE execution_id=?",
                (execution_id.root,),
            ).fetchone()[0]
            == 1
        )
        assert (
            "exact-value"
            in connection.execute(
                "SELECT responses_json FROM m3_managed_input_rounds WHERE execution_id=?",
                (execution_id.root,),
            ).fetchone()[0]
        )

    store.append_events(
        [
            factory.create(
                EventKind.EXECUTION_FINISHED,
                payload={"outcome": ExecutionOutcome.COMPLETED.value},
            )
        ]
    )
    store.append_events(
        [
            other_factory.create(
                EventKind.EXECUTION_FINISHED,
                payload={"outcome": ExecutionOutcome.COMPLETED.value},
            ),
        ]
    )

    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute(
            """
            CREATE TRIGGER abort_managed_delete
            BEFORE DELETE ON v2_executions
            WHEN OLD.id = 'managed-delete'
            BEGIN
                SELECT RAISE(ABORT, 'rollback delete');
            END
            """
        )
    with pytest.raises(Exception, match="rollback delete"):
        store.delete_execution(execution_id)

    assert store.get_snapshot(execution_id) is not None
    assert managed.get_round(execution_id.root, record.round_id) is not None
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM m3_managed_input_diagnostics WHERE execution_id=?",
                (execution_id.root,),
            ).fetchone()[0]
            == 1
        )
        assert (
            "exact-value"
            in connection.execute(
                "SELECT responses_json FROM m3_managed_input_rounds WHERE execution_id=?",
                (execution_id.root,),
            ).fetchone()[0]
        )
        connection.execute("DROP TRIGGER abort_managed_delete")

    store.delete_execution(execution_id)

    assert store.get_snapshot(execution_id) is None
    assert managed.get_round(execution_id.root, record.round_id) is None
    assert managed.get_round(other_id.root, other_record.round_id) is not None
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM m3_managed_input_diagnostics WHERE execution_id=?",
                (execution_id.root,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM m3_managed_input_rounds WHERE execution_id=?",
                (execution_id.root,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM m3_managed_input_rounds WHERE execution_id=?",
                (other_id.root,),
            ).fetchone()[0]
            == 1
        )
        persisted = " ".join(
            repr(tuple(row))
            for table in ("m3_managed_input_diagnostics", "m3_managed_input_rounds")
            for row in connection.execute(f"SELECT * FROM {table}")
        )
        assert "exact-value" not in persisted


def test_atomic_claim_and_durable_cancel(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    store.enqueue_command(execution_id)
    claim = store.claim_next("worker", lease_seconds=30)
    assert claim is not None
    assert store.heartbeat(claim[1]) is True
    assert store.request_cancel(execution_id)
    assert store.cancellation_requested(execution_id)


def test_database_files_are_private_and_symlink_paths_fail_closed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private.sqlite"
    previous = os.umask(0o022)
    try:
        store = SQLiteExecutionStore(database, blob_root=tmp_path / "blobs")
        _created(store, "private")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database}{suffix}")
        assert sidecar.exists()
        assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600

    target = tmp_path / "target.sqlite"
    target.write_bytes(b"keep")
    link = tmp_path / "link.sqlite"
    link.symlink_to(target)
    with pytest.raises(StorageError, match="symlink"):
        SQLiteExecutionStore(link)
    assert target.read_bytes() == b"keep"


def _driver_connection_opens(store: SQLiteExecutionStore) -> list[int]:
    """Record driver connections opened from now on, after pooling two."""
    from sqlalchemy import event

    with store._connect(), store._connect():
        pass

    opened: list[int] = []
    event.listen(
        store._engine,
        "connect",
        lambda dbapi_connection, _record: opened.append(id(dbapi_connection)),
    )
    return opened


def _pragma(store: SQLiteExecutionStore, name: str) -> int:
    with store._connect() as connection:
        row = connection.execute(f"PRAGMA {name}").fetchone()
    assert row is not None
    return int(row[0])


def test_pooled_driver_connection_is_reused_across_operations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    opened = _driver_connection_opens(store)
    execution_id, factory = _created(store, "pooled")
    for index in range(20):
        store.append_events(
            [factory.create(EventKind.DIAGNOSTIC, payload={"n": index})]
        )
        list(store.iter_events(execution_id))
    # Nested checkouts get distinct connections and return to the pool.
    with store._connect() as outer, store._connect() as inner:
        assert outer._connection.connection.dbapi_connection is not (
            inner._connection.connection.dbapi_connection
        )
    assert opened == []
    store.close()


def test_leaked_transaction_is_rolled_back_when_connection_returns(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    other = _store(tmp_path)
    connection = store._connect()
    connection.execute("BEGIN IMMEDIATE")
    assert connection.in_transaction
    connection.close()
    _created(other, "after-leak")
    with store._connect() as reused:
        assert not reused.in_transaction
    _created(store, "after-reuse")


def test_changed_connection_settings_are_restored_on_reuse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    opened = _driver_connection_opens(store)
    assert _pragma(store, "busy_timeout") == store.busy_timeout_ms
    with store._connect() as connection:
        connection.mark_settings_dirty()
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
    assert _pragma(store, "busy_timeout") == store.busy_timeout_ms
    assert _pragma(store, "foreign_keys") == 1
    trace = _normalized(_corpus()[sorted(_corpus())[0]]())
    execution_id = ExecutionId("corpus")
    store.create(ExecutionState(execution_id=execution_id))
    store.append_events(
        [
            event.model_copy(
                update={
                    "execution_id": execution_id,
                    "event_id": EventId(f"corpus-{event.sequence}"),
                }
            )
            for event in trace.events
        ]
    )
    with sqlite3.connect(tmp_path / "m3.sqlite") as raw:
        raw.execute("UPDATE v2_executions SET tool_call_counts_json=NULL")
    assert store.tool_call_counts([execution_id])
    assert _pragma(store, "busy_timeout") == store.busy_timeout_ms
    assert opened == []


def test_wal_connections_use_normal_synchronous(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.journal_mode == "wal"
    assert _pragma(store, "synchronous") == 1


def _user_version(database: Path) -> int:
    with sqlite3.connect(database) as raw:
        return int(raw.execute("PRAGMA user_version").fetchone()[0])


def _index_names(database: Path) -> set[str]:
    with sqlite3.connect(database) as raw:
        return {
            str(row[0])
            for row in raw.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }


def test_open_of_current_database_skips_schema_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _store(tmp_path)
    execution_id, factory = _created(first)
    assert _user_version(tmp_path / "m3.sqlite") == sqlite_storage._SCHEMA_GENERATION

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("schema setup must be skipped")

    monkeypatch.setattr(sqlite_storage._CompatConnection, "executescript", refuse)
    monkeypatch.setattr(SQLiteExecutionStore, "_migrate_legacy_profiles", refuse)
    second = _store(tmp_path)
    assert second.get_snapshot(execution_id) == first.get_snapshot(execution_id)
    second.append_events([factory.create(EventKind.DIAGNOSTIC, payload={})])
    assert tuple(second.iter_events(execution_id))[-1].kind == EventKind.DIAGNOSTIC
    other = ExecutionId("execution-2")
    second.create(ExecutionState(execution_id=other))
    assert second.get_snapshot(other).execution_id == other


def test_database_without_schema_generation_is_fully_migrated_then_marked(
    tmp_path: Path,
) -> None:
    database = tmp_path / "m3.sqlite"
    _store(tmp_path).close()
    with sqlite3.connect(database) as raw:
        raw.execute("DROP INDEX v2_executions_created_at")
        raw.execute("PRAGMA user_version=0")
    assert "v2_executions_created_at" not in _index_names(database)
    store = _store(tmp_path)
    assert "v2_executions_created_at" in _index_names(database)
    assert _user_version(database) == sqlite_storage._SCHEMA_GENERATION
    _created(store)


def test_rows_from_an_older_writer_are_migrated_on_open(tmp_path: Path) -> None:
    database = tmp_path / "m3.sqlite"
    _store(tmp_path).close()
    assert _user_version(database) == sqlite_storage._SCHEMA_GENERATION
    # An SDK that predates run labels never updates user_version.
    with sqlite3.connect(database) as raw:
        raw.execute(
            "INSERT INTO v2_test_runs(run_id,record_json,created_at,updated_at) "
            "VALUES('run-old','{}','2026-01-01T00:00:00+00:00',"
            "'2026-01-01T00:00:00+00:00')"
        )
    _store(tmp_path).close()
    with sqlite3.connect(database) as raw:
        label = raw.execute(
            "SELECT run_label FROM v2_test_runs WHERE run_id='run-old'"
        ).fetchone()[0]
    assert label


def test_open_of_current_database_without_pending_work_takes_no_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(tmp_path).close()
    blocker = sqlite3.connect(tmp_path / "m3.sqlite", isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("schema setup must be skipped")

    monkeypatch.setattr(sqlite_storage._CompatConnection, "executescript", refuse)
    try:
        store = SQLiteExecutionStore(
            tmp_path / "m3.sqlite", blob_root=tmp_path / "blobs", busy_timeout_ms=50
        )
        store.close()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_newer_schema_generation_is_left_alone(tmp_path: Path) -> None:
    database = tmp_path / "m3.sqlite"
    _store(tmp_path).close()
    newer = sqlite_storage._SCHEMA_GENERATION + 5
    with sqlite3.connect(database) as raw:
        raw.execute(f"PRAGMA user_version={newer}")
    _created(_store(tmp_path))
    assert _user_version(database) == newer


def test_standalone_artifact_store_does_not_mark_database_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "m3.sqlite"
    SQLiteArtifactStore(database, tmp_path / "blobs").close()
    assert _user_version(database) == 0
    assert "v2_executions_created_at" in _index_names(database)
    with sqlite3.connect(database) as raw:
        raw.execute("DROP INDEX v2_executions_created_at")
    ran: list[str] = []
    original = sqlite_storage._SqliteBase._migrate_legacy_evaluations

    def record(self: object, connection: object) -> None:
        ran.append("evaluations")
        original(self, connection)  # type: ignore[arg-type]

    monkeypatch.setattr(
        sqlite_storage._SqliteBase, "_migrate_legacy_evaluations", record
    )
    store = _store(tmp_path)
    assert ran
    assert "v2_executions_created_at" in _index_names(database)
    assert _user_version(database) == sqlite_storage._SCHEMA_GENERATION
    store.close()


def test_internal_artifact_store_shares_engine_without_initializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    initialized: list[object] = []
    monkeypatch.setattr(
        sqlite_storage._SqliteBase, "_initialize", lambda self: initialized.append(self)
    )
    shared = SQLiteArtifactStore(
        store.database, tmp_path / "other", config=None, _shared_from=store
    )
    assert initialized == []
    assert shared._engine is store._engine
    assert shared.database == store.database
    assert shared.busy_timeout_ms == store.busy_timeout_ms
    assert shared.journal_mode == store.journal_mode == "wal"
    assert store.artifacts._engine is store._engine
    assert store.artifacts.blob_root == (tmp_path / "blobs").resolve()
    execution_id, _ = _created(store)
    ref = store.artifacts.put(execution_id, "note.txt", b"hello")
    assert store.artifacts.get(ref) == b"hello"


def test_close_is_idempotent_and_shared_artifact_store_keeps_engine(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    ref = store.artifacts.put(execution_id, "note.txt", b"hello")
    store.artifacts.close()
    assert store.artifacts.get(ref) == b"hello"
    store.close()
    store.close()
    reopened = _store(tmp_path)
    assert reopened.artifacts.get(ref) == b"hello"


def test_database_path_replaced_by_symlink_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "m3.sqlite"
    store = _store(tmp_path)
    _created(store)
    target = tmp_path / "target.sqlite"
    target.write_bytes(b"keep")
    database.unlink()
    database.symlink_to(target)
    with pytest.raises(StorageError, match="symlink"):
        list(store.iter_events(ExecutionId("execution-1")))
    assert target.read_bytes() == b"keep"


def test_replaced_database_file_is_reopened_not_written_unlinked(
    tmp_path: Path,
) -> None:
    database = tmp_path / "m3.sqlite"
    store = _store(tmp_path)
    opened = _driver_connection_opens(store)
    _created(store, "before")
    old_inode = database.stat().st_ino
    database.rename(tmp_path / "moved.sqlite")
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    replacement = tmp_path / "replacement.sqlite"
    SQLiteExecutionStore(replacement, blob_root=tmp_path / "blobs").close()
    os.replace(replacement, database)
    assert database.stat().st_ino != old_inode
    _created(store, "after")
    assert opened
    with sqlite3.connect(database) as raw:
        ids = {row[0] for row in raw.execute("SELECT id FROM v2_executions")}
    assert ids == {"after"}


def test_corrupt_database_has_value_free_storage_error(tmp_path: Path) -> None:
    database = tmp_path / "corrupt.sqlite"
    database.write_bytes(b"not a sqlite database")
    with pytest.raises(StorageError) as caught:
        SQLiteExecutionStore(database)
    assert str(caught.value) == "database is unavailable"
    assert str(database) not in str(caught.value)


def test_sqlite_sequence_reservations_are_consumed_and_released(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    reserved = store.allocate(execution_id, count=2)
    event = factory.create(EventKind.DIAGNOSTIC, payload={"n": 1})
    assert event.sequence == reserved[0]
    store.append_events([event])
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert connection.execute(
            "SELECT sequence FROM v2_sequence_reservations WHERE execution_id=?",
            (execution_id.root,),
        ).fetchall() == [(reserved[1],)]
    store.release(execution_id, [reserved[1]])
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM v2_sequence_reservations"
            ).fetchone()[0]
            == 0
        )


def test_session_events_materialize_turn_foreign_key_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    session_id = SessionId("session-1")
    store.append_events(
        [factory.create(EventKind.SESSION_CREATED, session_id=session_id, payload={})]
    )
    turn = TurnState(turn_id=TurnId("turn-1"), session_id=session_id, number=1)
    store.save_turn(turn)
    assert store.turns(execution_id)[0][0].turn_id == turn.turn_id


def test_turn_id_cannot_move_between_sessions_on_idempotent_update(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _execution_id, factory = _created(store)
    first, second = SessionId("session-1"), SessionId("session-2")
    store.append_events(
        [
            factory.create(EventKind.SESSION_CREATED, session_id=first, payload={}),
            factory.create(EventKind.SESSION_CREATED, session_id=second, payload={}),
        ]
    )
    store.save_turn(TurnState(turn_id=TurnId("turn-1"), session_id=first, number=1))
    with pytest.raises(StorageConflict):
        store.save_turn(
            TurnState(turn_id=TurnId("turn-1"), session_id=second, number=1)
        )


def test_terminal_execution_rejects_later_events_and_finished_state_only(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _execution_id, factory = _created(store)
    with pytest.raises(StorageConflict):
        store.append_events(
            [
                factory.create(
                    EventKind.EXECUTION_STATE_CHANGED, payload={"state": "finished"}
                )
            ]
        )
    terminal = factory.create(
        EventKind.EXECUTION_FINISHED,
        payload={"outcome": ExecutionOutcome.COMPLETED.value},
    ).model_copy(update={"sequence": 1})
    store.append_events([terminal])
    with pytest.raises(StorageConflict):
        store.append_events(
            [
                factory.create(
                    EventKind.DIAGNOSTIC, payload={"after": True}
                ).model_copy(update={"sequence": 2})
            ]
        )


def test_clone_records_parent_and_keeps_original_revision_after_profile_edit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    profile = store.create_server_profile("server", {"version": 1})
    original = ExecutionId("clone-source")
    first = store.resolve_revision(profile.id)
    store.create(
        ExecutionState(execution_id=original),
        server_bindings=[{"profile_id": profile.id, "revision_id": first.id.root}],
    )
    store.add_revision(profile.id, {"version": 2})
    clone = store.clone_execution(original)
    assert store.resolved_bindings(clone)["servers"][0]["revision_id"] == first.id.root
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT parent_execution_id FROM v2_executions WHERE id=?",
                (clone.root,),
            ).fetchone()[0]
            == original.root
        )


def test_large_event_payload_is_blob_backed_without_semantic_truncation(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite",
        blob_root=tmp_path / "blobs",
        payload_blob_threshold=128,
    )
    execution_id, factory = _created(store)
    payload = {"items": ["payload-value-" * 20] * 20, "metadata": {"nested": [1, 2, 3]}}
    event = factory.create(EventKind.DIAGNOSTIC, payload=payload)
    store.append_events([event])
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        stored = connection.execute(
            "SELECT event_json FROM v2_events WHERE id=?", (event.event_id.root,)
        ).fetchone()[0]
        assert "payload-value-" not in stored
        assert (
            connection.execute(
                "SELECT role FROM v2_event_blobs WHERE event_id=?",
                (event.event_id.root,),
            ).fetchone()[0]
            == "payload"
        )
    restored = store.events(execution_id)[-1]
    assert (
        restored.model_dump(mode="json")["payload"]
        == event.model_dump(mode="json")["payload"]
    )
    assert restored.payload_ref is not None
    store.append_events(
        [
            factory.create(
                EventKind.EXECUTION_FINISHED,
                payload={"outcome": ExecutionOutcome.COMPLETED.value},
            )
        ]
    )
    store.delete_execution(execution_id)
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM v2_event_blobs").fetchone()[0] == 0
        )


def test_ephemeral_and_sqlite_artifact_bytes_redact_and_reopen(tmp_path: Path) -> None:
    literal = "classified-artifact-canary"
    reference = "resolved-artifact-canary"
    config = RedactionConfig(
        secrets=frozenset({literal, reference}), include_environment=False
    )
    content = b"prefix:" + literal.encode() + b":" + reference.encode() + b":suffix"

    ephemeral = InMemoryArtifactStore(config=config)
    ephemeral_ref = ephemeral.put("execution-artifacts", "output.txt", content)
    assert ephemeral.get(ephemeral_ref) == b"prefix:[REDACTED]:[REDACTED]:suffix"

    database = tmp_path / "artifact.sqlite"
    blobs = tmp_path / "artifact-blobs"
    store = SQLiteExecutionStore(database, blob_root=blobs, config=config)
    execution_id, _ = _created(store, "execution-artifacts")
    artifact = store.artifacts.put(execution_id, "output.txt", content)
    assert store.artifacts.get(artifact) == b"prefix:[REDACTED]:[REDACTED]:suffix"
    store.close()
    reopened = SQLiteExecutionStore(database, blob_root=blobs, config=config)
    assert reopened.artifacts.get(artifact) == b"prefix:[REDACTED]:[REDACTED]:suffix"
    reopened.close()
    raw_files = [
        database.read_bytes(),
        *(path.read_bytes() for path in blobs.rglob("*") if path.is_file()),
    ]
    assert all(
        canary.encode() not in raw
        for raw in raw_files
        for canary in (literal, reference)
    )


def test_persisted_execution_spec_upgrades_legacy_profile_selector(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    profile = store.create_server_profile(
        "legacy-selector",
        {"mcpServers": {"echo": {"command": "echo"}}},
        profile_id="legacy-profile",
    )
    from m3.types import ServerProfileRef

    spec = DirectSpec(
        servers=(
            ServerBinding(
                profile=ServerProfileRef(
                    profile_id=profile.id,
                    server_name="echo",
                    revision=RevisionSelection(mode="latest"),
                )
            ),
        ),
        operation=Ping(server="echo"),
    )
    persisted = spec.model_dump(mode="json")
    del persisted["servers"][0]["profile"]["server_name"]
    persisted["operation"]["server"] = "legacy-profile"
    execution_id = ExecutionId("legacy-spec-execution")
    store.create(ExecutionState(execution_id=execution_id), specification=persisted)
    try:
        loaded = store.get_execution_spec(execution_id)
        assert loaded is not None
        assert loaded.servers[0].profile is not None
        assert loaded.servers[0].profile.server_name == "legacy-profile"
    finally:
        store.close()


def test_event_and_artifact_references_share_refcount_and_gc_after_terminal_delete(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite",
        blob_root=tmp_path / "blobs",
        payload_blob_threshold=1,
    )
    execution_id, factory = _created(store)
    payload = {"shared": "same bytes"}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    event = factory.create(EventKind.DIAGNOSTIC, payload=payload)
    store.append_events([event])
    artifact = store.artifacts.put(
        execution_id, "copy.json", encoded, media_type="application/json"
    )
    stored_event = store.events(execution_id)[-1]
    assert stored_event.payload_ref is not None
    assert artifact.sha256 == stored_event.payload_ref.sha256
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT ref_count FROM v2_blobs WHERE sha256=?", (artifact.sha256,)
            ).fetchone()[0]
            == 2
        )
    store.append_events(
        [
            factory.create(
                EventKind.EXECUTION_FINISHED,
                payload={"outcome": ExecutionOutcome.COMPLETED.value},
            )
        ]
    )
    store.delete_execution(execution_id)
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM v2_blobs").fetchone()[0] == 0


def test_failed_large_event_append_leaves_only_explicitly_collectable_orphan(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite",
        blob_root=tmp_path / "blobs",
        payload_blob_threshold=128,
    )
    execution_id, factory = _created(store)
    event = factory.create(EventKind.DIAGNOSTIC, payload={"large": "x" * 1024})
    # Force a sequence conflict after the blob has been atomically published.
    with pytest.raises(StorageConflict):
        store.append_events([event.model_copy(update={"sequence": 900})])
    assert len(store.events(execution_id)) == 1  # the creation event remains committed
    store.artifacts.cleanup()
    assert store.artifacts.blob_store.iter_records() == ()


@pytest.mark.parametrize("kind", ["artifact", "payload", "raw_event"])
def test_cleanup_waits_for_blob_reference_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite",
        blob_root=tmp_path / "blobs",
        payload_blob_threshold=128,
    )
    execution_id, factory = _created(store)
    other = SQLiteExecutionStore(
        store.database, blob_root=store.artifacts.blob_root, payload_blob_threshold=128
    )
    published, cleanup_started, release = Event(), Event(), Event()
    original_put = store.artifacts.blob_store.put

    def paused_put(
        content: bytes, *, sha256: str | None = None, size_bytes: int | None = None
    ) -> BlobRecord:
        blob = original_put(content, sha256=sha256, size_bytes=size_bytes)
        published.set()
        if not release.wait(5):
            raise TimeoutError("writer was not released")
        return blob

    monkeypatch.setattr(store.artifacts.blob_store, "put", paused_put)
    payload = {"large": "x" * 1024}

    def write() -> None:
        if kind == "artifact":
            store.artifacts.put(execution_id, "race.bin", b"race artifact")
        elif kind == "payload":
            store.append_events([factory.create(EventKind.DIAGNOSTIC, payload=payload)])
        else:
            store.append_event(
                factory.create(EventKind.DIAGNOSTIC, payload={}),
                b"race raw evidence",
                media_type="text/plain",
            )

    def clean() -> None:
        cleanup_started.set()
        other.artifacts.cleanup()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        try:
            assert published.wait(5)
            cleanup = pool.submit(clean)
            assert cleanup_started.wait(5)
            with pytest.raises(FutureTimeoutError):
                cleanup.result(timeout=0.2)
        finally:
            release.set()
        writer.result(timeout=10)
        cleanup.result(timeout=10)

    if kind == "artifact":
        (reference,) = tuple(store.artifacts.iter_refs(execution_id))
        assert store.artifacts.get(reference) == b"race artifact"
    elif kind == "payload":
        assert store.events(execution_id)[-1].payload == payload
    else:
        reference = store.events(execution_id)[-1].raw_evidence_ref
        assert reference is not None
        assert store.read_raw_evidence(reference).content == "race raw evidence"


def test_writer_waits_for_cleanup_before_reusing_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    other = _store(tmp_path)
    content = b"previously unreferenced"
    store.artifacts.blob_store.put(content)
    sweeping, writer_started, release = Event(), Event(), Event()
    original_collect = store.artifacts.blob_store.garbage_collect

    def paused_collect(references: dict[str, int]) -> tuple[str, ...]:
        sweeping.set()
        if not release.wait(5):
            raise TimeoutError("collector was not released")
        return original_collect(references)

    def write_copy() -> ArtifactRef:
        writer_started.set()
        return other.artifacts.put(execution_id, "copy", content)

    monkeypatch.setattr(store.artifacts.blob_store, "garbage_collect", paused_collect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanup = pool.submit(store.artifacts.cleanup)
        try:
            assert sweeping.wait(5)
            writer = pool.submit(write_copy)
            assert writer_started.wait(5)
            with pytest.raises(FutureTimeoutError):
                writer.result(timeout=0.2)
        finally:
            release.set()
        cleanup.result(timeout=10)
        reference = writer.result(timeout=10)

    assert other.artifacts.get(reference) == content


@pytest.mark.parametrize("name", sorted(_corpus()))
def test_tool_call_counts_match_trace_view_before_and_after_persisting(
    tmp_path: Path, name: str
) -> None:
    store = _store(tmp_path)
    trace = _normalized(_corpus()[name]())
    execution_id = ExecutionId("corpus")
    store.create(ExecutionState(execution_id=execution_id))
    store.append_events(
        [
            event.model_copy(
                update={
                    "execution_id": execution_id,
                    "event_id": EventId(f"corpus-{event.sequence}"),
                }
            )
            for event in trace.events
        ]
    )
    summary = store.get_trace_view(execution_id).summary
    expected = (summary.tool_call_count, summary.successful_tool_call_count)
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute("UPDATE v2_executions SET tool_call_counts_json=NULL")

    assert store.tool_call_counts([execution_id]) == {"corpus": expected}
    assert json.loads(_stored_counts(tmp_path, execution_id)) == {
        "schema_version": "2.0",
        "total": expected[0],
        "successful": expected[1],
    }
    assert store.tool_call_counts([execution_id]) == {"corpus": expected}


def test_tool_call_counts_omit_unfinished_and_missing_and_fill_null_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    finished = _run_execution(store, "finished", "run", [True, False, True])
    empty = _run_execution(store, "empty", "run", [])
    unfinished = _run_execution(store, "unfinished", "run", [True], finished=False)
    ids = [finished, empty, unfinished, "missing"]

    assert store.tool_call_counts(ids) == {"finished": (3, 2), "empty": (0, 0)}
    assert _stored_counts(tmp_path, unfinished) is None

    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute("UPDATE v2_executions SET tool_call_counts_json=NULL")

    assert store.tool_call_counts(ids) == {"finished": (3, 2), "empty": (0, 0)}
    assert json.loads(_stored_counts(tmp_path, finished)) == {
        "schema_version": "2.0",
        "total": 3,
        "successful": 2,
    }

    stale = json.dumps({"schema_version": "1.0", "total": 9, "successful": 9})
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute(
            "UPDATE v2_executions SET tool_call_counts_json=? WHERE id=?",
            (stale, finished.root),
        )
        connection.execute(
            "UPDATE v2_executions SET tool_call_counts_json=? WHERE id=?",
            ("not json", empty.root),
        )

    assert store.tool_call_counts(ids) == {"finished": (3, 2), "empty": (0, 0)}
    assert json.loads(_stored_counts(tmp_path, finished)) == {
        "schema_version": "2.0",
        "total": 3,
        "successful": 2,
    }


def _event(
    execution_id: str,
    sequence: int,
    kind: EventKind = EventKind.DIAGNOSTIC,
    *,
    payload: dict[str, object] | None = None,
    origin: EventOrigin | None = None,
    request_sequence: int | None = None,
    server: str | None = None,
) -> StoredEvent:
    return StoredEvent(
        event_id=EventId(f"{execution_id}-event-{sequence}"),
        execution_id=ExecutionId(execution_id),
        sequence=sequence,
        kind=kind,
        timestamp=datetime(2026, 1, 1, 0, 0, sequence % 60, tzinfo=timezone.utc),
        monotonic_offset_ms=float(sequence),
        server_binding=server,
        connection_id=ConnectionId("connection-1") if server else None,
        correlation=(
            RequestLink(
                jsonrpc_id=request_sequence,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=request_sequence,
            )
            if request_sequence is not None
            else None
        ),
        payload=payload if payload is not None else {"sequence": sequence},
        **(
            {"provenance": EventSource(origin=origin, source="fixture")}
            if origin is not None
            else {}
        ),
    )


def _tool_request(
    execution_id: str,
    sequence: int,
    *,
    origin: EventOrigin = EventOrigin.HARNESS_REPORTED,
    params: dict[str, object] | None = None,
) -> StoredEvent:
    return _event(
        execution_id,
        sequence,
        EventKind.TOOL_CALL_REQUESTED,
        payload={"params": params or {"name": "lookup", "arguments": {}}},
        origin=origin,
        request_sequence=sequence,
        server="server-1",
    )


@pytest.mark.parametrize("threshold", [0, None])
def test_append_event_returns_the_event_a_reload_produces(
    tmp_path: Path, threshold: int | None
) -> None:
    options = {} if threshold is None else {"payload_blob_threshold": threshold}
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite", blob_root=tmp_path / "blobs", **options
    )
    execution_id, factory = _created(store)
    event = factory.create(EventKind.DIAGNOSTIC, payload={"note": "x" * 64})
    returned = store.append_event(event, b"raw bytes", media_type="text/plain")
    assert returned == store.events(execution_id)[-1]
    assert returned.raw_evidence_ref is not None
    if threshold == 0:
        assert returned.payload_ref is not None
        assert returned.payload["note"] == "x" * 64


def test_append_events_return_committed_events_in_stored_form(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite", blob_root=tmp_path / "blobs", payload_blob_threshold=0
    )
    _created(store, "stored-form")
    batch = (
        _event("stored-form", 1, payload={"a": [1, 2, 3]}),
        _event("stored-form", 2, payload={}),
    )
    committed = store._append("stored-form", batch)
    assert committed == store.events("stored-form")[1:]


def test_append_after_store_written_terminal_event_is_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("cancelled-execution")
    store.create(ExecutionState(execution_id=execution_id))
    store.append_events(
        [
            _event(
                execution_id.root,
                0,
                EventKind.EXECUTION_CREATED,
                payload={"trace_id": "trace-1"},
            )
        ]
    )
    store.enqueue_command(execution_id)
    assert store.claim_next("worker", lease_seconds=30) is not None
    assert store.request_cancel(execution_id)
    assert store.finalize_cancelled(execution_id)
    snapshot = store.get_snapshot(execution_id)
    assert snapshot is not None and snapshot.lifecycle is ExecutionStatus.FINISHED
    next_sequence = store.events(execution_id)[-1].sequence + 1
    with pytest.raises(TerminalConflict):
        store.append_events([_event(execution_id.root, next_sequence)])
    assert store.get_snapshot(execution_id) == snapshot


def test_append_after_a_store_written_terminal_raises_terminal_conflict(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-terminal-append")
    store.create(ExecutionState(execution_id=execution_id))
    store.append_events(
        [
            _event(
                execution_id.root,
                0,
                EventKind.EXECUTION_CREATED,
                payload={"trace_id": "trace-1"},
            )
        ]
    )
    assert store.request_cancel(execution_id)
    committed = store.events(execution_id)
    used = committed[-1].sequence
    for sequence in (used, used + 3):
        with pytest.raises(TerminalConflict):
            store.append_events([_event(execution_id.root, sequence)])
    assert store.events(execution_id) == committed


def test_non_contiguous_append_raises_sequence_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    with pytest.raises(SequenceConflict):
        store.append_events([_event(execution_id.root, 5)])
    with pytest.raises(SequenceConflict):
        store.append_events([_event(execution_id.root, 0)])
    duplicate = _event(execution_id.root, 1)
    with pytest.raises(StorageConflict) as raised:
        store.append_events([duplicate, duplicate.model_copy(update={"sequence": 2})])
    assert not isinstance(raised.value, SequenceConflict)


def test_append_reads_no_history_unless_it_adds_a_tool_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    key = execution_id.root
    store.append_events([_tool_request(key, 1)])
    loads = 0
    original = store._events

    def counting(*args: object, **kwargs: object) -> tuple[StoredEvent, ...]:
        nonlocal loads
        loads += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "_events", counting)
    store.append_events(
        [
            _event(
                key, 2, EventKind.EXECUTION_STATE_CHANGED, payload={"state": "idle"}
            ),
            _event(key, 3),
        ]
    )
    assert loads == 0
    store.append_events(
        [
            _tool_request(
                key,
                4,
                origin=EventOrigin.WIRE_OBSERVED,
                params={"name": "other", "arguments": {}},
            )
        ]
    )
    assert loads == 1
    snapshot = store.get_snapshot(key)
    assert snapshot is not None and snapshot.tool_call_count == 2
    assert snapshot == store._derive_snapshot(key)


def test_snapshot_after_mixed_appends_matches_full_replay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    key = execution_id.root
    store.append_events(
        [
            _event(
                key,
                1,
                EventKind.HARNESS_SELECTION,
                payload={
                    "harness": {"kind": "opencode", "runtime": "managed"},
                    "model": {"requested_id": "openai/gpt-5"},
                },
            ),
            _tool_request(key, 2),
        ]
    )
    store.append_events(
        [
            _event(
                key,
                3,
                EventKind.HARNESS_RUNTIME_RESOLVED,
                payload={"resolved_version": "1.0.0"},
            ),
            _event(
                key, 4, EventKind.EXECUTION_STATE_CHANGED, payload={"state": "idle"}
            ),
            _tool_request(key, 5, origin=EventOrigin.WIRE_OBSERVED),
        ]
    )
    store.append_events(
        [
            _event(
                key,
                6,
                EventKind.EXECUTION_FINISHED,
                payload={"outcome": ExecutionOutcome.COMPLETED.value},
            )
        ]
    )
    snapshot = store.get_snapshot(key)
    assert snapshot is not None
    assert snapshot.sequence == 6
    assert snapshot.lifecycle is ExecutionStatus.FINISHED
    assert snapshot.agent is not None
    assert snapshot.agent.harness.resolved_version == "1.0.0"
    assert snapshot.tool_call_count is not None
    full = sqlite_storage._fold_snapshot(
        ExecutionState(execution_id=execution_id),
        store.events(key),
        sequence=6,
        tool_calls=snapshot.tool_call_count,
    )
    assert full.model_dump() == snapshot.model_dump()


def test_incremental_snapshot_matches_full_replay_for_random_histories(
    tmp_path: Path,
) -> None:
    hypothesis = pytest.importorskip("hypothesis")
    st = hypothesis.strategies
    from m3.trace.counts import tool_call_count

    kinds = st.sampled_from(
        (
            "state",
            "selection",
            "resolved",
            "provider",
            "reported_request",
            "wire_request",
            "retry_request",
            "response",
            "diagnostic",
        )
    )

    def build(key: str, sequence: int, choice: str, number: int) -> StoredEvent:
        if choice == "state":
            state = ("running_turn", "idle", "waiting_for_input", "closing")[number % 4]
            return _event(
                key,
                sequence,
                EventKind.EXECUTION_STATE_CHANGED,
                payload={"state": state},
            )
        if choice == "selection":
            return _event(
                key,
                sequence,
                EventKind.HARNESS_SELECTION,
                payload={
                    "harness": {"kind": f"harness-{number % 3}", "runtime": "managed"},
                    "model": {"requested_id": f"model-{number % 3}"},
                },
            )
        if choice == "resolved":
            return _event(
                key,
                sequence,
                EventKind.HARNESS_RUNTIME_RESOLVED,
                payload={"resolved_version": f"1.{number % 4}.0"},
            )
        if choice == "provider":
            return _event(
                key,
                sequence,
                EventKind.PROVIDER_EVENT,
                payload={"category": "model", "data": f"observed-{number % 3}"},
            )
        if choice in {"reported_request", "wire_request"}:
            return _tool_request(
                key,
                sequence,
                origin=(
                    EventOrigin.HARNESS_REPORTED
                    if choice == "reported_request"
                    else EventOrigin.WIRE_OBSERVED
                ),
                params={"name": f"tool-{number % 2}", "arguments": {}},
            )
        if choice == "retry_request":
            return _tool_request(
                key,
                sequence,
                params={
                    "name": "lookup",
                    "arguments": {},
                    "requestState": f"state-{number}",
                },
            )
        if choice == "response":
            return _event(
                key,
                sequence,
                EventKind.MCP_RESPONSE,
                payload={"result": {"content": []}},
                request_sequence=max(sequence - 1, 1),
                server="server-1",
            )
        return _event(key, sequence)

    @hypothesis.settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=[hypothesis.HealthCheck.function_scoped_fixture],
    )
    @hypothesis.given(
        steps=st.lists(st.tuples(kinds, st.integers(0, 9)), min_size=1, max_size=14),
        batches=st.lists(st.integers(1, 4), min_size=1, max_size=14),
        finish=st.booleans(),
    )
    def check(steps: list[tuple[str, int]], batches: list[int], finish: bool) -> None:
        key = f"random-{len(list(tmp_path.iterdir()))}"
        store = SQLiteExecutionStore(
            tmp_path / f"{key}.sqlite", blob_root=tmp_path / f"{key}-blobs"
        )
        try:
            store.create(ExecutionState(execution_id=ExecutionId(key)))
            events = [
                _event(key, 0, EventKind.EXECUTION_CREATED, payload={}),
                *(
                    build(key, index + 1, choice, number)
                    for index, (choice, number) in enumerate(steps)
                ),
            ]
            if finish:
                events.append(
                    _event(
                        key,
                        len(events),
                        EventKind.EXECUTION_FINISHED,
                        payload={"outcome": "completed"},
                    )
                )
            position = 0
            for size in itertools.cycle(batches):
                if position >= len(events):
                    break
                batch = events[position : position + size]
                position += len(batch)
                store.append_events(batch)
                snapshot = store.get_snapshot(key)
                assert snapshot is not None
                if snapshot.lifecycle is not ExecutionStatus.FINISHED:
                    assert snapshot == store._derive_snapshot(key)
                prefix = events[:position]
                expected = sqlite_storage._fold_snapshot(
                    ExecutionState(execution_id=ExecutionId(key)),
                    prefix,
                    sequence=prefix[-1].sequence,
                    tool_calls=tool_call_count(prefix),
                )
                assert snapshot.model_dump() == expected.model_dump()
        finally:
            store.close()

    check()


def test_close_leaves_a_self_contained_database_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store, "checkpointed")
    store.close()
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes((tmp_path / "m3.sqlite").read_bytes())
    with sqlite3.connect(copy) as connection:
        rows = connection.execute(
            "SELECT id FROM v2_executions WHERE id=?", (execution_id.root,)
        ).fetchall()
    assert rows == [(execution_id.root,)]
    assert (
        not (tmp_path / "m3.sqlite-wal").exists()
        or not (tmp_path / "m3.sqlite-wal").stat().st_size
    )


def test_close_tolerates_a_reader_blocking_the_checkpoint(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "m3.sqlite", blob_root=tmp_path / "blobs", busy_timeout_ms=5000
    )
    _created(store, "blocked")
    reader = sqlite3.connect(tmp_path / "m3.sqlite")
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM v2_executions").fetchone()
        started = time.monotonic()
        store.close()
        # Closing must not wait out the busy timeout for another reader.
        assert time.monotonic() - started < 1.0
    finally:
        reader.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_forked_child_opens_its_own_connection_and_leaves_the_parents(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store, "forked")
    opened = _driver_connection_opens(store)
    pid = os.fork()
    if pid == 0:
        status = 1
        try:
            store.append_events(
                [_event(execution_id.root, 1, payload={"who": "child"})]
            )
            status = 0 if len(opened) == 1 else 2
        finally:
            os._exit(status)
    _, wait_status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(wait_status) == 0
    # The child never opened a connection in this process's counter.
    assert opened == []
    store.append_events([_event(execution_id.root, 2)])
    assert len(store.events(execution_id)) == 3
    assert opened == []
    store.close()


def test_schema_fingerprint_matches_the_recorded_generation() -> None:
    """Fail when DDL changes without a new schema generation."""
    import hashlib
    import inspect

    source = inspect.getsource(sqlite_storage._SqliteBase._initialize)
    normalized = "".join((sqlite_storage.SCHEMA + source).split())
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    assert fingerprint == sqlite_storage._SCHEMA_FINGERPRINT, (
        "The SQLite schema or its migrations changed. Bump _SCHEMA_GENERATION "
        "in m3/storage/sqlite.py and set _SCHEMA_FINGERPRINT to "
        f"{fingerprint!r}."
    )


def _tamper_snapshot(
    store: SQLiteExecutionStore, key: str, edit: Callable[[dict[str, object]], None]
) -> None:
    with store._connect() as connection:
        row = connection.execute(
            "SELECT snapshot_json FROM v2_executions WHERE id=?", (key,)
        ).fetchone()
        snapshot = json.loads(row[0])
        edit(snapshot)
        connection.execute(
            "UPDATE v2_executions SET snapshot_json=? WHERE id=?",
            (json.dumps(snapshot), key),
        )


@pytest.mark.parametrize("damage", ["missing-agent", "stale-sequence"])
def test_append_repairs_a_stale_stored_snapshot_from_full_history(
    tmp_path: Path, damage: str
) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    key = execution_id.root
    store.append_events(
        [
            _event(
                key,
                1,
                EventKind.HARNESS_SELECTION,
                payload={
                    "harness": {"kind": "opencode", "runtime": "managed"},
                    "model": {"requested_id": "openai/gpt-5"},
                },
            )
        ]
    )
    if damage == "missing-agent":
        _tamper_snapshot(store, key, lambda snapshot: snapshot.pop("agent"))
    else:
        _tamper_snapshot(store, key, lambda snapshot: snapshot.update(sequence=0))
        _tamper_snapshot(store, key, lambda snapshot: snapshot.update(agent=None))
    store.append_events([_event(key, 2)])
    snapshot = store.get_snapshot(key)
    assert snapshot is not None
    assert snapshot.agent is not None
    assert snapshot.sequence == 2
    assert snapshot == store._derive_snapshot(key)
    store.close()
