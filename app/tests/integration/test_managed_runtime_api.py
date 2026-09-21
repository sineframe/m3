"""Saved managed runtime provenance survives the public API boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from _local_client import TestClient

from m3.events import EventFactory, EventSequence
from m3.storage import SQLiteExecutionStore
from m3.types import EventKind, ExecutionState
from m3_app.api.app import create_app
from m3_app.settings import Settings


def test_saved_managed_identity_in_detail_report_and_trace(tmp_path: Path) -> None:
    database = tmp_path / "managed-runtime-api.sqlite"
    store = SQLiteExecutionStore(database)
    execution_id = "managed-runtime-api"
    store.create(ExecutionState(execution_id=execution_id))
    factory = EventFactory(
        execution_id,
        allocator=EventSequence(start=0),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    store.append_events(
        (
            factory.create(
                EventKind.EXECUTION_CREATED, payload={"trace_id": "managed-api-trace"}
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
    )
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    try:
        with TestClient(application) as client:
            detail = client.get(f"/api/v2/executions/{execution_id}")
            listing = client.get("/api/v2/executions")
            report = client.get(f"/api/v2/executions/{execution_id}/report")
            assert (
                detail.status_code == listing.status_code == report.status_code == 200
            )
            for identity in (
                detail.json()["snapshot"]["agent"],
                listing.json()["page"]["items"][0]["agent"],
                report.json()["report"]["agent"],
                report.json()["trace"]["agent"],
            ):
                assert identity["harness"]["requested_selector"] == "latest"
                assert identity["harness"]["resolved_version"] == "1.18.30"
                assert identity["harness"]["digest"] == "a" * 64
                assert identity["model"]["requested_id"] == "openai/gpt-5"
    finally:
        store.close()
