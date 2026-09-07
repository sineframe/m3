"""Focused contract checks for the fresh persistent storage boundary."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import json
import stat
import os
import subprocess
import sys

import pytest

from mcp_pal.events import EventFactory
from mcp_pal.storage import ArtifactNotFound, InMemoryArtifactStore, InMemoryExecutionStore, SQLiteExecutionStore, StorageConflict
from mcp_pal.storage import StorageError
from mcp_pal.trace.redaction import RedactionConfig
from mcp_pal.types import (
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    ExecutionStatus,
    RevisionSelection,
    SecretReference,
    SessionId,
    TurnId,
    TurnState,
    DirectSpec,
    Ping,
    ServerBinding,
    StdioServer,
)


def _store(tmp_path: Path) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(tmp_path / "mcp-pal.sqlite", blob_root=tmp_path / "blobs")


def _created(store: SQLiteExecutionStore, name: str = "execution-1") -> tuple[ExecutionId, EventFactory]:
    execution_id = ExecutionId(name)
    store.create(ExecutionState(execution_id=execution_id))
    factory = EventFactory(execution_id)
    store.append_events([factory.create(EventKind.EXECUTION_CREATED, payload={})])
    return execution_id, factory


def test_memory_and_sqlite_retain_defensive_typed_execution_specs(tmp_path: Path) -> None:
    spec = DirectSpec(
        servers=(ServerBinding(server=StdioServer(name="server", command="echo")),),
        operation=Ping(server="server"),
    )
    execution_id = ExecutionId("spec-parity")
    memory = InMemoryExecutionStore()
    memory.create(ExecutionState(execution_id=execution_id), specification=spec.model_dump(mode="json"))
    memory_spec = memory.get_execution_spec(execution_id)
    assert memory_spec == spec
    assert memory_spec is not None
    memory_spec = memory_spec.model_copy(update={"metadata": {"mutated": True}})
    assert "mutated" not in memory.get_execution_spec(execution_id).metadata  # type: ignore[union-attr]

    sqlite = _store(tmp_path)
    sqlite.create(ExecutionState(execution_id=execution_id), specification=spec.model_dump(mode="json"))
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
import mcp_pal.storage
from mcp_pal.storage import SQLiteExecutionStore
try:
    SQLiteExecutionStore('optional-dependency-test.sqlite')
except ModuleNotFoundError as error:
    assert str(error) == 'SQLite storage requires the optional dependency; install mcp-pal[storage]'
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


def test_events_are_commit_gated_and_snapshots_reload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    event = factory.create(EventKind.DIAGNOSTIC, payload={"message": "safe"})
    with store.transaction(execution_id) as transaction:
        transaction.append([event])
        assert tuple(store.iter_events(execution_id)) == tuple(store.iter_events(execution_id, after_sequence=-1))
    assert tuple(store.iter_events(execution_id))[-1].payload["message"] == "safe"
    reloaded = SQLiteExecutionStore(tmp_path / "mcp-pal.sqlite", blob_root=tmp_path / "blobs")
    assert reloaded.get_snapshot(execution_id) == store.get_snapshot(execution_id)


def test_profile_revisions_archive_and_explicit_latest_clone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    profile = store.create_server_profile("server", {"url": "https://one.example"})
    first = store.resolve_revision(profile.id)
    second = store.add_revision(profile.id, {"url": "https://two.example"})
    assert store.resolve_revision(profile.id, RevisionSelection(mode="pinned", revision_id=first.id, revision_number=1)).id == first.id
    assert store.resolve_revision(profile.id).id == second.id
    store.archive_profile(profile.id)
    assert store.get_profile(profile.id).archived is True  # type: ignore[union-attr]
    execution_id = ExecutionId("clone-source")
    store.create(ExecutionState(execution_id=execution_id), server_bindings=[{"profile_id": profile.id, "revision_id": first.id.root}])
    clone = store.clone_execution(execution_id, use_latest=True)
    assert clone != execution_id
    assert store.resolved_bindings(clone)["servers"][0]["revision_id"] == second.id.root
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE v2_server_profiles SET current_revision_id=? WHERE id=?", ("missing-revision", profile.id))


def test_profile_listing_is_kind_scoped_archived_filtered_and_revision_ordered(tmp_path: Path) -> None:
    store = _store(tmp_path)
    server = store.create_profile("server", "zulu", {"mcpServers": {"z": {"command": "echo"}}})
    harness = store.create_profile("harness", "alpha", {"manifest": {"command": "agent"}, "trusted_unsandboxed": False})
    store.add_revision(server.id, {"mcpServers": {"z": {"command": "echo", "args": ["2"]}}}, revision_id="server-revision-2")
    store.archive_profile(server.id)

    assert [item.id for item in store.list_profiles("server")] == []
    assert [item.id for item in store.list_profiles("server", include_archived=True)] == [server.id]
    assert [item.id for item in store.list_profiles("harness")] == [harness.id]
    assert [item.revision_number for item in store.list_profile_revisions(server.id)] == [1, 2]
    with pytest.raises(ValueError, match="profile kind"):
        store.list_profiles("not-a-kind")
    with pytest.raises(StorageConflict, match="does not exist"):
        store.list_profile_revisions("missing-profile")


def test_profile_metadata_update_conflict_and_durable_redaction(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "mcp-pal.sqlite",
        blob_root=tmp_path / "blobs",
        config=RedactionConfig(secrets=frozenset({"super-secret"}), include_environment=False),
    )
    first = store.create_server_profile("first", {"token": SecretReference(source="environment", name="MCP_TOKEN")})
    second = store.create_server_profile("second", {"token": SecretReference(source="environment", name="MCP_TOKEN")})
    updated = store.update_profile(first.id, name="renamed", description="safe")
    assert updated.name == "renamed"
    assert store.resolve_revision(first.id).value["token"] == {"source": "environment", "name": "MCP_TOKEN"}
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
    store.append_events([factory.create(EventKind.EXECUTION_FINISHED, payload={"outcome": ExecutionOutcome.COMPLETED.value})])
    assert store.get_snapshot(execution_id).lifecycle is ExecutionStatus.FINISHED  # type: ignore[union-attr]
    store.delete_execution(execution_id)
    assert store.get_snapshot(execution_id) is None


def test_atomic_claim_and_durable_cancel(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, _ = _created(store)
    store.enqueue_command(execution_id)
    claim = store.claim_next("worker", lease_seconds=30)
    assert claim is not None
    assert store.heartbeat(claim[1]) is True
    assert store.request_cancel(execution_id)
    assert store.cancellation_requested(execution_id)


def test_database_files_are_private_and_symlink_paths_fail_closed(tmp_path: Path) -> None:
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
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        assert connection.execute("SELECT sequence FROM v2_sequence_reservations WHERE execution_id=?", (execution_id.root,)).fetchall() == [(reserved[1],)]
    store.release(execution_id, [reserved[1]])
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM v2_sequence_reservations").fetchone()[0] == 0


def test_session_events_materialize_turn_foreign_key_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    session_id = SessionId("session-1")
    store.append_events([factory.create(EventKind.SESSION_CREATED, session_id=session_id, payload={})])
    turn = TurnState(turn_id=TurnId("turn-1"), session_id=session_id, number=1)
    store.save_turn(turn)
    assert store.turns(execution_id)[0][0].turn_id == turn.turn_id


def test_turn_id_cannot_move_between_sessions_on_idempotent_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    first, second = SessionId("session-1"), SessionId("session-2")
    store.append_events([
        factory.create(EventKind.SESSION_CREATED, session_id=first, payload={}),
        factory.create(EventKind.SESSION_CREATED, session_id=second, payload={}),
    ])
    store.save_turn(TurnState(turn_id=TurnId("turn-1"), session_id=first, number=1))
    with pytest.raises(StorageConflict):
        store.save_turn(TurnState(turn_id=TurnId("turn-1"), session_id=second, number=1))


def test_terminal_execution_rejects_later_events_and_finished_state_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    execution_id, factory = _created(store)
    with pytest.raises(StorageConflict):
        store.append_events([factory.create(EventKind.EXECUTION_STATE_CHANGED, payload={"state": "finished"})])
    terminal = factory.create(EventKind.EXECUTION_FINISHED, payload={"outcome": ExecutionOutcome.COMPLETED.value}).model_copy(update={"sequence": 1})
    store.append_events([terminal])
    with pytest.raises(StorageConflict):
        store.append_events([factory.create(EventKind.DIAGNOSTIC, payload={"after": True}).model_copy(update={"sequence": 2})])


def test_clone_records_parent_and_keeps_original_revision_after_profile_edit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    profile = store.create_server_profile("server", {"version": 1})
    original = ExecutionId("clone-source")
    first = store.resolve_revision(profile.id)
    store.create(ExecutionState(execution_id=original), server_bindings=[{"profile_id": profile.id, "revision_id": first.id.root}])
    store.add_revision(profile.id, {"version": 2})
    clone = store.clone_execution(original)
    assert store.resolved_bindings(clone)["servers"][0]["revision_id"] == first.id.root
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        assert connection.execute("SELECT parent_execution_id FROM v2_executions WHERE id=?", (clone.root,)).fetchone()[0] == original.root


def test_large_event_payload_is_blob_backed_without_semantic_truncation(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "mcp-pal.sqlite", blob_root=tmp_path / "blobs", payload_blob_threshold=128)
    execution_id, factory = _created(store)
    payload = {"items": ["payload-value-" * 20] * 20, "metadata": {"nested": [1, 2, 3]}}
    event = factory.create(EventKind.DIAGNOSTIC, payload=payload)
    store.append_events([event])
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        stored = connection.execute("SELECT event_json FROM v2_events WHERE id=?", (event.event_id.root,)).fetchone()[0]
        assert "payload-value-" not in stored
        assert connection.execute("SELECT role FROM v2_event_blobs WHERE event_id=?", (event.event_id.root,)).fetchone()[0] == "payload"
    restored = store.events(execution_id)[-1]
    assert restored.model_dump(mode="json")["payload"] == event.model_dump(mode="json")["payload"]
    assert restored.payload_ref is not None
    store.append_events([factory.create(EventKind.EXECUTION_FINISHED, payload={"outcome": ExecutionOutcome.COMPLETED.value})])
    store.delete_execution(execution_id)
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM v2_event_blobs").fetchone()[0] == 0


def test_ephemeral_and_sqlite_artifact_bytes_redact_and_reopen(tmp_path: Path) -> None:
    literal = "classified-artifact-canary"
    reference = "resolved-artifact-canary"
    config = RedactionConfig(secrets=frozenset({literal, reference}), include_environment=False)
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
    raw_files = [database.read_bytes(), *(path.read_bytes() for path in blobs.rglob("*") if path.is_file())]
    assert all(canary.encode() not in raw for raw in raw_files for canary in (literal, reference))


def test_event_and_artifact_references_share_refcount_and_gc_after_terminal_delete(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "mcp-pal.sqlite", blob_root=tmp_path / "blobs", payload_blob_threshold=1)
    execution_id, factory = _created(store)
    payload = {"shared": "same bytes"}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    event = factory.create(EventKind.DIAGNOSTIC, payload=payload)
    store.append_events([event])
    artifact = store.artifacts.put(execution_id, "copy.json", encoded, media_type="application/json")
    stored_event = store.events(execution_id)[-1]
    assert stored_event.payload_ref is not None
    assert artifact.sha256 == stored_event.payload_ref.sha256
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        assert connection.execute("SELECT ref_count FROM v2_blobs WHERE sha256=?", (artifact.sha256,)).fetchone()[0] == 2
    store.append_events([factory.create(EventKind.EXECUTION_FINISHED, payload={"outcome": ExecutionOutcome.COMPLETED.value})])
    store.delete_execution(execution_id)
    with sqlite3.connect(tmp_path / "mcp-pal.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM v2_blobs").fetchone()[0] == 0


def test_failed_large_event_append_leaves_only_explicitly_collectable_orphan(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "mcp-pal.sqlite", blob_root=tmp_path / "blobs", payload_blob_threshold=128)
    execution_id, factory = _created(store)
    event = factory.create(EventKind.DIAGNOSTIC, payload={"large": "x" * 1024})
    # Force a sequence conflict after the blob has been atomically published.
    with pytest.raises(StorageConflict):
        store.append_events([event.model_copy(update={"sequence": 900})])
    assert len(store.events(execution_id)) == 1  # the creation event remains committed
    store.artifacts.cleanup()
    assert store.artifacts.blob_store.iter_records() == ()
