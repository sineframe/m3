from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from m3 import ElicitationResponse, FormElicitationRequest, PendingElicitationRound
from m3.errors import ManagedInputConflict, ManagedInputValidationError
from m3.storage import SQLiteManagedInputStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def pending_round() -> PendingElicitationRound:
    execution_id = f"docs-execution-{uuid4().hex}"
    round_id = f"docs-round-{uuid4().hex}"
    request = FormElicitationRequest(
        request_key="shipping_address",
        message="Enter the delivery address.",
        requested_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    return PendingElicitationRound(
        round_id=round_id,
        execution_id=execution_id,
        logical_operation_id=f"docs-operation-{uuid4().hex}",
        server="shipping",
        operation_kind="tool",
        operation_name="book_shipment",
        request_state="shipping-address:server-state",
        requests={"shipping_address": request},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deadline=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )


def test_response_retry_survives_store_reopen_and_stale_lease_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "managed-input.sqlite"
    clock = Clock()
    pending = pending_round()
    store = SQLiteManagedInputStore(database, clock=clock)
    record = store.create_round(
        pending,
        round_index=0,
        round_limit=10,
        owner_id="docs-worker",
        lease_seconds=30,
    )
    response = {
        "shipping_address": ElicitationResponse(
            action="accept", content={"city": "Pune"}
        )
    }

    with pytest.raises(ManagedInputValidationError, match="exactly"):
        store.submit_responses(
            pending.execution_id,
            pending.round_id,
            {},
            owner_id=record.owner_id,
            lease_token=record.lease_token,
            response_idempotency_key="docs-response-1",
        )
    assert store.get_round(pending.execution_id, pending.round_id).status == "pending"

    accepted = store.submit_responses(
        pending.execution_id,
        pending.round_id,
        response,
        owner_id=record.owner_id,
        lease_token=record.lease_token,
        response_idempotency_key="docs-response-1",
    )
    assert accepted.status == "response_validated"
    del store
    reopened = SQLiteManagedInputStore(database, clock=clock)
    assert reopened.get_round(pending.execution_id, pending.round_id) == accepted
    retried = reopened.submit_responses(
        pending.execution_id,
        pending.round_id,
        response,
        owner_id=record.owner_id,
        lease_token=record.lease_token,
        response_idempotency_key="docs-response-1",
    )
    assert retried == accepted

    pending_stale = pending_round()
    stale = reopened.create_round(
        pending_stale,
        round_index=0,
        round_limit=10,
        owner_id="docs-worker",
        lease_seconds=30,
    )
    clock.value += timedelta(seconds=31)
    with pytest.raises(ManagedInputConflict, match="expired"):
        reopened.submit_responses(
            pending_stale.execution_id,
            pending_stale.round_id,
            response,
            owner_id=stale.owner_id,
            lease_token=stale.lease_token,
            response_idempotency_key="docs-response-stale",
        )
    assert (
        reopened.get_round(pending_stale.execution_id, pending_stale.round_id).status
        == "pending"
    )
