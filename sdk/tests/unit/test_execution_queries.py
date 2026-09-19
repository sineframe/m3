from datetime import datetime, timezone

import pytest

from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore
from m3.types import (
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionState,
    ExecutionStatus,
)


def _snapshot(name: str, seconds: int) -> ExecutionState:
    return ExecutionState(
        execution_id=ExecutionId(name),
        created_at=datetime(2025, 1, 1, 0, 0, seconds, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_execution_listing_is_newest_first_with_stable_tie_break(tmp_path, store_kind):
    store = (
        InMemoryExecutionStore()
        if store_kind == "memory"
        else SQLiteExecutionStore(tmp_path.resolve() / "queries.sqlite")
    )
    try:
        for snapshot in (
            _snapshot("same-b", 2),
            _snapshot("same-a", 2),
            _snapshot("old", 1),
        ):
            store.create(snapshot)
        page = store.list_executions(limit=2, offset=0)
        assert isinstance(page, ExecutionPage)
        assert [item.execution_id.root for item in page.items] == ["same-b", "same-a"]
        assert page.total == 3
        assert [
            item.execution_id.root
            for item in store.list_executions(limit=2, offset=2).items
        ] == ["old"]
    finally:
        store.close()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_execution_listing_filters_lifecycle_and_outcome(tmp_path, store_kind):
    store = (
        InMemoryExecutionStore()
        if store_kind == "memory"
        else SQLiteExecutionStore(tmp_path.resolve() / "filters.sqlite")
    )
    try:
        store.create(_snapshot("created", 1))
        finished = _snapshot("finished", 2).model_copy(
            update={
                "lifecycle": ExecutionStatus.FINISHED,
                "outcome": ExecutionOutcome.CANCELLED,
                "finished_at": datetime(2025, 1, 1, 0, 0, 3, tzinfo=timezone.utc),
            }
        )
        store.create(finished)
        page = store.list_executions(
            lifecycle=ExecutionStatus.FINISHED, outcome=ExecutionOutcome.CANCELLED
        )
        assert [item.execution_id.root for item in page.items] == ["finished"]
    finally:
        store.close()


def test_sqlite_report_reopens_from_snapshot_and_events(tmp_path):
    database = tmp_path.resolve() / "reopen.sqlite"
    store = SQLiteExecutionStore(database)
    store.create(_snapshot("reopen", 1))
    store.close()
    reopened = SQLiteExecutionStore(database)
    try:
        report = reopened.get_report("reopen")
        assert report is not None
        assert report.snapshot.execution_id == ExecutionId("reopen")
        assert report.events == ()
        assert report.direct_result is None
        assert report.error is None
        assert report.evidence is None
    finally:
        reopened.close()


def test_report_projection_rejects_inconsistent_counts():
    snapshot = _snapshot("report", 1)
    with pytest.raises(ValueError, match="event count"):
        ExecutionReport(snapshot=snapshot, event_count=1)
