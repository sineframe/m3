"""Managed runtime identity survives storage and drives matrix grouping."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import pytest

from m3._types.agent_identity import (
    AgentIdentity,
    HarnessIdentity,
    ModelIdentity,
    project_agent_identity,
)
from m3.events import EventFactory, EventSequence
from m3.feedback import _config, _Entry
from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore
from m3.types import (
    EventKind,
    ExecutionReport,
    ExecutionState,
)


class _Spec:
    metadata: ClassVar[dict[str, str]] = {"harness_config": "openai/gpt-5"}

    def model_dump(self, *, mode: str = "json") -> dict[str, object]:
        return {
            "kind": "agent",
            "harness": {"kind": "opencode", "model": "openai/gpt-5"},
            "metadata": {"harness_config": "openai/gpt-5"},
        }


@pytest.mark.parametrize("storage_kind", ["memory", "sqlite"])
def test_runtime_identity_survives_snapshot_report_and_reopen(
    storage_kind: str, tmp_path: Path
) -> None:
    db = tmp_path / "identity.sqlite"
    store = (
        InMemoryExecutionStore()
        if storage_kind == "memory"
        else SQLiteExecutionStore(db)
    )
    try:
        store.create(ExecutionState(execution_id="runtime-identity"))
        factory = EventFactory(
            "runtime-identity",
            allocator=EventSequence(start=0),
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        events = (
            factory.create(
                EventKind.EXECUTION_CREATED,
                payload={"trace_id": "runtime-trace"},
            ),
            factory.create(
                EventKind.HARNESS_SELECTION,
                payload={
                    "harness": {
                        "kind": "opencode",
                        "runtime": "managed",
                        "requested_selector": "latest",
                    },
                    "model": {"requested_id": "openai/gpt-5", "provider": "openai"},
                },
            ),
            factory.create(
                EventKind.HARNESS_RUNTIME_RESOLVED,
                payload={
                    "resolved_version": "1.18.30",
                    "target": "darwin-arm64",
                    "digest": "a" * 64,
                    "verification_method": "github_asset_sha256",
                    "immutable_release": False,
                },
            ),
            factory.create(
                EventKind.EXECUTION_FINISHED,
                payload={
                    "outcome": "completed",
                    "completeness": "complete",
                    "limitations": [],
                },
            ),
        )
        store.append_events(events)
        snapshot = store.get_snapshot("runtime-identity")
        assert snapshot is not None and snapshot.agent is not None
        assert snapshot.agent.harness.resolved_version == "1.18.30"
        report = store.get_report("runtime-identity")
        assert report is not None and report.agent == snapshot.agent
        trace = store.get_trace_view("runtime-identity")
        assert trace.agent == snapshot.agent
    finally:
        if hasattr(store, "close"):
            store.close()
    if storage_kind == "sqlite":
        reopened = SQLiteExecutionStore(db)
        try:
            assert (
                reopened.get_report("runtime-identity").agent.harness.digest == "a" * 64
            )
        finally:
            reopened.close()


def _report(version: str | None) -> ExecutionReport:
    identity = AgentIdentity(
        harness=HarnessIdentity(
            kind="opencode",
            runtime="managed",
            requested_selector="latest",
            resolved_version=version,
            target="darwin-arm64" if version else None,
            digest=("a" if version == "1.18.30" else "b") * 64 if version else None,
        ),
        model=ModelIdentity(requested_id="openai/gpt-5", provider="openai"),
    )
    return ExecutionReport(
        snapshot=ExecutionState(
            execution_id=f"runtime-{version or 'unresolved'}", agent=identity
        ),
        agent=identity,
    )


def test_resolved_version_changes_matrix_label_and_digest() -> None:
    first = _config(_Entry(_report("1.18.30"), _Spec()))
    second = _config(_Entry(_report("1.18.31"), _Spec()))
    assert first[0] != second[0]
    assert first[1] != second[1]
    assert "1.18.30" in first[0]
    assert "1.18.31" in second[0]


def test_unresolved_setup_keeps_requested_selector_identity() -> None:
    label, digest = _config(_Entry(_report(None), _Spec()))
    assert "opencode@latest" in label
    assert digest is not None


def test_observed_model_is_distinct_from_requested_model() -> None:
    factory = EventFactory(
        "runtime-observed-model",
        allocator=EventSequence(start=0),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    events = (
        factory.create(
            EventKind.HARNESS_SELECTION,
            payload={
                "harness": {"kind": "codex", "runtime": "managed"},
                "model": {"requested_id": "gpt-requested"},
            },
        ),
        factory.create(
            EventKind.PROVIDER_EVENT,
            payload={"category": "model", "data": "gpt-observed"},
        ),
    )
    identity = project_agent_identity(events)
    assert identity is not None
    assert identity.model.requested_id == "gpt-requested"
    assert identity.model.observed_id == "gpt-observed"


def test_old_records_without_runtime_events_have_no_agent_identity(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "old.sqlite")
    try:
        store.create(ExecutionState(execution_id="old-record"))
        factory = EventFactory(
            "old-record",
            allocator=EventSequence(start=0),
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        store.append_events(
            (
                factory.create(
                    EventKind.EXECUTION_CREATED, payload={"trace_id": "old-trace"}
                ),
                factory.create(
                    EventKind.EXECUTION_FINISHED,
                    payload={
                        "outcome": "completed",
                        "completeness": "complete",
                        "limitations": [],
                    },
                ),
            )
        )
        report = store.get_report("old-record")
        assert report is not None
        assert report.snapshot.agent is None
        assert report.agent is None
        assert store.get_trace_view("old-record").agent is None
    finally:
        store.close()
