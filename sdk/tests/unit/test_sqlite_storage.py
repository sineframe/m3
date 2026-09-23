"""Focused contract checks for the fresh persistent storage boundary."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pytest

from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
)
from m3.events import EventFactory
from m3.storage import (
    ArtifactNotFound,
    BlobRecord,
    InMemoryArtifactStore,
    InMemoryExecutionStore,
    SQLiteExecutionStore,
    StorageConflict,
    StorageError,
)
from m3.trace.redaction import RedactionConfig
from m3.types import (
    ArtifactRef,
    DirectSpec,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    ExecutionStatus,
    Ping,
    RevisionSelection,
    SecretReference,
    ServerBinding,
    SessionId,
    StdioServer,
    TurnId,
    TurnState,
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
    store = SQLiteExecutionStore(database, blob_root=tmp_path / "blobs")
    _created(store, "private")
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database}{suffix}")
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600

    target = tmp_path / "target.sqlite"
    target.write_bytes(b"keep")
    link = tmp_path / "link.sqlite"
    link.symlink_to(target)
    with pytest.raises(StorageError, match="symlink"):
        SQLiteExecutionStore(link)
    assert target.read_bytes() == b"keep"


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
