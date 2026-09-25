from __future__ import annotations

from pathlib import Path

from m3.events import EventFactory
from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore
from m3.types import (
    DirectSpec,
    EventKind,
    ExecutionId,
    ExecutionState,
    Ping,
    ServerBinding,
    StdioServer,
)


def test_suite_registry_normalizes_names_and_reuses_identity(tmp_path: Path) -> None:
    for store in (
        InMemoryExecutionStore(),
        SQLiteExecutionStore(tmp_path / "suites.sqlite"),
    ):
        first = store.ensure_suite("  Payments API ")
        second = store.ensure_suite("Payments API")
        assert first.id == second.id
        assert first.normalized_name == "Payments API"
        assert len(store.list_suites()) == 1
        if hasattr(store, "close"):
            store.close()


def test_sqlite_migration_adds_suite_columns_to_existing_database(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "migration.sqlite")
    with store._connect() as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(v2_executions)").fetchall()
        }
        result_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(v2_test_results)"
            ).fetchall()
        }
    assert "suite_id" in columns
    assert "suite_id" in result_columns
    store.close()


def test_direct_sqlite_execution_does_not_require_a_suite(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "direct.sqlite")
    execution_id = ExecutionId("unsuited-direct")
    store.create(ExecutionState(execution_id=execution_id))
    snapshot = store.get_snapshot(execution_id)
    assert snapshot is not None
    assert snapshot.suite_id is None
    assert snapshot.suite_name is None
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT suite_id FROM v2_executions WHERE id=?", (execution_id.root,)
            ).fetchone()[0]
            is None
        )
    store.close()


def test_in_memory_suite_survives_events_and_suite_filter() -> None:
    store = InMemoryExecutionStore()
    execution_id = ExecutionId("suite-event")
    store.create(ExecutionState(execution_id=execution_id, suite_name="catalog"))
    initial = store.get_snapshot(execution_id)
    assert initial is not None and initial.suite_id is not None
    store.append_events(
        (EventFactory(execution_id).create(EventKind.EXECUTION_CREATED),)
    )
    persisted = store.get_snapshot(execution_id)
    assert persisted is not None
    assert persisted.suite_id == initial.suite_id
    assert persisted.suite_name == "catalog"
    assert store.list_executions(suite_id=initial.suite_id.root).total == 1


def test_in_memory_unsuited_spec_preserves_snapshot_suite() -> None:
    store = InMemoryExecutionStore()
    execution_id = ExecutionId("suite-spec")
    spec = DirectSpec(
        servers=(ServerBinding(server=StdioServer(name="s", command="true")),),
        operation=Ping(server="s"),
    )
    store.create(
        ExecutionState(execution_id=execution_id, suite_name="catalog"),
        specification=spec.model_dump(mode="json"),
    )
    persisted = store.get_snapshot(execution_id)
    assert persisted is not None
    assert persisted.suite_name == "catalog"
    assert persisted.suite_id == store.get_suite_by_name("catalog").id


def test_in_memory_snapshot_uses_normalized_suite_name() -> None:
    store = InMemoryExecutionStore()
    execution_id = ExecutionId("spaced-suite")
    store.create(ExecutionState(execution_id=execution_id, suite_name="  catalog  "))
    persisted = store.get_snapshot(execution_id)
    assert persisted is not None
    assert persisted.suite_name == "catalog"
    assert persisted.suite_id == store.get_suite_by_name("catalog").id
    assert store.list_executions(suite_id=persisted.suite_id.root).total == 1
