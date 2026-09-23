"""Contract tests for durable managed elicitation input."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
    UrlElicitationRequest,
)
from m3.errors import (
    ManagedInputConflict,
    ManagedInputRecoveryError,
    ManagedInputStateError,
    ManagedInputValidationError,
)
from m3.storage import SQLiteManagedInputStore


def _pending(
    *,
    execution_id: str = "execution-1",
    round_id: str = "round-1",
    request_state: str | None = "opaque-state",
) -> PendingElicitationRound:
    return PendingElicitationRound(
        round_id=round_id,
        execution_id=execution_id,
        logical_operation_id="operation-1",
        server="example-mcp",
        operation_kind="tool",
        operation_name="book_shipment",
        request_state=request_state,
        requests={
            "address": FormElicitationRequest(
                request_key="address",
                message="Address",
                requested_schema={
                    "type": "object",
                    "required": ["city"],
                    "properties": {"city": {"type": "string"}},
                },
            ),
            "payment": UrlElicitationRequest(
                request_key="payment",
                message="Payment",
                url="https://example.test/pay",
                elicitation_id="pay-1",
            ),
        },
        created_at=datetime.now(timezone.utc),
    )


def _responses(city: str = "Pune") -> dict[str, ElicitationResponse]:
    return {
        "address": ElicitationResponse(action="accept", content={"city": city}),
        "payment": ElicitationResponse(action="accept"),
    }


def _store(tmp_path: Path) -> SQLiteManagedInputStore:
    return SQLiteManagedInputStore(tmp_path / "m3.sqlite")


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def test_create_and_reload_preserves_round_state_and_opaque_request_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    created = store.create_round(
        _pending(request_state="unfamiliar-state"),
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
        harness_session_id="session-1",
        native_resume_token="resume-1",
        delivery_idempotency_key="delivery-1",
        session_id="session-record-1",
        turn_id="turn-record-1",
        operation_parameters={"weight_kg": 2, "zone": "local"},
    )
    assert created.status == "pending"
    assert created.pending.request_state == "unfamiliar-state"
    assert created.round_limit == 10
    assert created.harness_session_id == "session-1"
    assert created.native_resume_token == "resume-1"
    assert created.delivery_idempotency_key == "delivery-1"
    assert created.session_id == "session-record-1"
    assert created.turn_id == "turn-record-1"
    assert created.operation_parameters == {"weight_kg": 2, "zone": "local"}

    reloaded = SQLiteManagedInputStore(tmp_path / "m3.sqlite")
    assert reloaded.get_round("execution-1", "round-1") == created


def test_empty_or_out_of_limit_round_is_rejected_without_persisting(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(ManagedInputValidationError):
        store.create_round(
            _pending(),
            round_index=10,
            round_limit=10,
            owner_id="worker-a",
            lease_seconds=30,
        )
    assert store.get_round("execution-1", "round-1") is None

    empty = _pending()
    empty = empty.model_copy(update={"requests": {}})
    with pytest.raises(ManagedInputValidationError):
        store.create_round(
            empty,
            round_index=0,
            round_limit=10,
            owner_id="worker-a",
            lease_seconds=30,
        )


def test_response_submission_requires_exact_keys_and_schema_without_consuming_round(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create_round(
        _pending(),
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
    )

    with pytest.raises(ManagedInputValidationError, match="exactly"):
        store.submit_responses(
            "execution-1",
            "round-1",
            {"address": _responses()["address"]},
            owner_id="worker-a",
            lease_token=store.get_round("execution-1", "round-1").lease_token,
            response_idempotency_key="response-1",
        )
    assert store.get_round("execution-1", "round-1").status == "pending"

    record = store.get_round("execution-1", "round-1")
    assert record is not None
    with pytest.raises(ManagedInputValidationError, match="unexpected"):
        store.submit_responses(
            "execution-1",
            "round-1",
            {**_responses(), "unexpected": ElicitationResponse(action="decline")},
            owner_id="worker-a",
            lease_token=record.lease_token,
            response_idempotency_key="response-2",
        )
    assert store.get_round("execution-1", "round-1").status == "pending"

    with pytest.raises(ManagedInputValidationError, match="schema"):
        store.submit_responses(
            "execution-1",
            "round-1",
            _responses(city=123),  # type: ignore[arg-type]
            owner_id="worker-a",
            lease_token=record.lease_token,
            response_idempotency_key="response-3",
        )
    assert store.get_round("execution-1", "round-1").status == "pending"


def test_lifecycle_is_atomic_and_old_lease_cannot_mutate_after_takeover(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    store = SQLiteManagedInputStore(tmp_path / "m3.sqlite", clock=clock)
    first = store.create_round(
        _pending(),
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=0.01,
    )
    clock.value += timedelta(seconds=1)
    second = store.claim_round(
        "execution-1",
        "round-1",
        owner_id="worker-b",
        lease_seconds=30,
        expected_lease_token=first.lease_token,
        delivery_state="not_started",
    )
    with pytest.raises(ManagedInputConflict):
        store.submit_responses(
            "execution-1",
            "round-1",
            _responses(),
            owner_id="worker-a",
            lease_token=first.lease_token,
            response_idempotency_key="old-response",
        )

    validated = store.submit_responses(
        "execution-1",
        "round-1",
        _responses(),
        owner_id="worker-b",
        lease_token=second.lease_token,
        response_idempotency_key="response-1",
    )
    assert validated.status == "response_validated"
    started = store.start_delivery(
        "execution-1", "round-1", owner_id="worker-b", lease_token=second.lease_token
    )
    assert started.status == "delivery_started"
    assert started.delivery_started_at is not None
    assert started.delivery_attempts == 1
    assert started.request_state == "opaque-state"
    delivered = store.mark_delivered(
        "execution-1", "round-1", owner_id="worker-b", lease_token=second.lease_token
    )
    assert delivered.status == "delivered"
    assert delivered.delivered_at is not None
    resolved = store.resolve(
        "execution-1", "round-1", owner_id="worker-b", lease_token=second.lease_token
    )
    assert resolved.status == "resolved"

    with pytest.raises(ManagedInputStateError):
        store.start_delivery(
            "execution-1",
            "round-1",
            owner_id="worker-b",
            lease_token=second.lease_token,
        )


def test_ambiguous_delivery_never_redelivers_a_response_without_proof(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    store = SQLiteManagedInputStore(tmp_path / "ambiguous-delivery.sqlite", clock=clock)
    created = store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    validated = store.submit_responses(
        "execution-1",
        "round-1",
        _responses(),
        owner_id="worker-a",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    started = store.start_delivery(
        "execution-1", "round-1", owner_id="worker-a", lease_token=validated.lease_token
    )
    assert started.status == "delivery_started"
    assert started.delivery_attempts == 1
    assert started.responses == _responses()

    clock.value += timedelta(seconds=31)
    with pytest.raises(ManagedInputRecoveryError, match="idempotent delivery proof"):
        store.claim_round(
            "execution-1",
            "round-1",
            owner_id="worker-b",
            lease_seconds=30,
            expected_lease_token=started.lease_token,
            delivery_state="not_delivered",
        )
    unchanged = store.get_round("execution-1", "round-1")
    assert unchanged is not None
    assert unchanged.status == "delivery_started"
    assert unchanged.delivery_attempts == 1
    assert unchanged.responses == _responses()

    delivered = store.claim_round(
        "execution-1",
        "round-1",
        owner_id="worker-b",
        lease_seconds=30,
        expected_lease_token=started.lease_token,
        delivery_state="delivered",
    )
    assert delivered.status == "delivered"
    assert delivered.responses == _responses()


def test_managed_record_model_serialization_round_trip_preserves_lifecycle_metadata(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    created = store.create_round(
        _pending(request_state="opaque"),
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
        harness_session_id="harness-session",
        native_resume_token="opaque-resume-token",
        delivery_idempotency_key="delivery-key",
        session_id="m3-session",
        turn_id="turn-1",
        operation_parameters={"arguments": {"city": "Pune"}},
    )
    validated = store.submit_responses(
        "execution-1",
        "round-1",
        _responses(),
        owner_id="worker-a",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    started = store.start_delivery(
        "execution-1", "round-1", owner_id="worker-a", lease_token=validated.lease_token
    )
    delivered = store.mark_delivered(
        "execution-1", "round-1", owner_id="worker-a", lease_token=started.lease_token
    )
    resolved = store.resolve(
        "execution-1", "round-1", owner_id="worker-a", lease_token=delivered.lease_token
    )

    restored = type(resolved).model_validate(resolved.model_dump(mode="json"))
    assert restored == resolved
    reopened = SQLiteManagedInputStore(tmp_path / "m3.sqlite")
    assert reopened.get_round("execution-1", "round-1") == restored


def test_claim_before_expiry_and_invalid_owner_or_idempotency_are_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(ManagedInputValidationError):
        store.create_round(
            _pending(),
            round_index=0,
            round_limit=10,
            owner_id="",
            lease_seconds=30,
        )
    created = store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    with pytest.raises(ManagedInputConflict, match="lease"):
        store.claim_round(
            "execution-1",
            "round-1",
            owner_id="worker-b",
            lease_seconds=30,
            expected_lease_token=created.lease_token,
            delivery_state="not_started",
        )
    with pytest.raises(ManagedInputValidationError):
        store.submit_responses(
            "execution-1",
            "round-1",
            _responses(),
            owner_id="worker-a",
            lease_token=created.lease_token,
            response_idempotency_key="",
        )


def test_duplicate_round_id_is_idempotent_only_for_identical_data(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pending = _pending()
    first = store.create_round(
        pending, round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    assert (
        store.create_round(
            pending,
            round_index=0,
            round_limit=10,
            owner_id="worker-a",
            lease_seconds=30,
            lease_token=first.lease_token,
        )
        == first
    )
    with pytest.raises(ManagedInputConflict):
        store.create_round(
            _pending(request_state="different"),
            round_index=0,
            round_limit=10,
            owner_id="worker-a",
            lease_seconds=30,
            lease_token=first.lease_token,
        )


def test_duplicate_round_identity_includes_live_interaction_metadata(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pending = _pending()
    kwargs = dict(
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
        lease_token="stable-lease",
        harness_session_id="harness-session",
        native_resume_token="native-resume",
        delivery_idempotency_key="delivery-key",
    )
    first = store.create_round(pending, **kwargs)
    assert store.create_round(pending, **kwargs) == first
    for field in (
        "harness_session_id",
        "native_resume_token",
        "delivery_idempotency_key",
    ):
        conflicting = dict(kwargs)
        conflicting[field] = f"different-{field}"
        with pytest.raises(ManagedInputConflict):
            store.create_round(pending, **conflicting)


def test_response_shape_rules_are_stricter_than_json_schema_acceptance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pending = _pending()
    address = pending.requests["address"].model_copy(
        update={"requested_schema": {"type": "object"}}
    )
    pending = pending.model_copy(
        update={
            "requests": {"address": address, "payment": pending.requests["payment"]}
        }
    )
    created = store.create_round(
        pending, round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    with pytest.raises(ManagedInputValidationError, match="content"):
        store.submit_responses(
            "execution-1",
            "round-1",
            {
                "address": ElicitationResponse(action="accept"),
                "payment": ElicitationResponse(action="accept"),
            },
            owner_id="worker-a",
            lease_token=created.lease_token,
            response_idempotency_key="missing-content",
        )
    with pytest.raises(ManagedInputValidationError, match="URL"):
        store.submit_responses(
            "execution-1",
            "round-1",
            {
                "address": ElicitationResponse(
                    action="accept", content={"city": "Pune"}
                ),
                "payment": ElicitationResponse(
                    action="accept", content={"payment": "secret"}
                ),
            },
            owner_id="worker-a",
            lease_token=created.lease_token,
            response_idempotency_key="url-content",
        )


@pytest.mark.parametrize(
    "column,value", [("pending_json", "[]"), ("operation_parameters_json", "[]")]
)
def test_invalid_persisted_json_shapes_are_typed_failures(
    tmp_path: Path, column: str, value: str
) -> None:
    store = _store(tmp_path)
    store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute(
            f"UPDATE m3_managed_input_rounds SET {column}=? WHERE execution_id=?",
            (value, "execution-1"),
        )
        connection.commit()
    with pytest.raises(ManagedInputValidationError, match="stored"):
        store.get_round("execution-1", "round-1")


def test_invalid_persisted_response_map_shape_is_typed_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    store.submit_responses(
        "execution-1",
        "round-1",
        _responses(),
        owner_id="worker-a",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute(
            "UPDATE m3_managed_input_rounds SET responses_json=? WHERE execution_id=?",
            ("[]", "execution-1"),
        )
        connection.commit()
    with pytest.raises(ManagedInputValidationError, match="stored"):
        store.get_round("execution-1", "round-1")


def test_migrations_are_safe_for_partial_schema_and_concurrent_initializers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "m3.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE m3_managed_input_rounds (execution_id TEXT, round_id TEXT, pending_json TEXT, status TEXT)"
        )
        connection.commit()
    with ThreadPoolExecutor(max_workers=4) as executor:
        tuple(executor.map(lambda _: SQLiteManagedInputStore(database), range(4)))
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(m3_managed_input_rounds)")
        }
    assert {
        "execution_id",
        "round_id",
        "round_index",
        "round_limit",
        "pending_json",
        "status",
        "operation_parameters_json",
        "delivery_attempts",
        "failure_code",
    } <= columns
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version FROM m3_managed_input_schema"
        ).fetchone() == (1,)


def test_same_idempotent_submission_replays_and_conflicting_one_fails(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    created = store.create_round(
        _pending(),
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
    )
    first = store.submit_responses(
        "execution-1",
        "round-1",
        _responses(),
        owner_id="worker-a",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    assert (
        store.submit_responses(
            "execution-1",
            "round-1",
            _responses(),
            owner_id="worker-a",
            lease_token=created.lease_token,
            response_idempotency_key="response-1",
        )
        == first
    )
    with pytest.raises(ManagedInputConflict):
        store.submit_responses(
            "execution-1",
            "round-1",
            _responses(city="Mumbai"),
            owner_id="worker-a",
            lease_token=created.lease_token,
            response_idempotency_key="response-1",
        )


def test_concurrent_identical_submissions_are_one_atomic_commit(tmp_path: Path) -> None:
    database = tmp_path / "m3.sqlite"
    store = SQLiteManagedInputStore(database)
    created = store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    first = SQLiteManagedInputStore(database)
    second = SQLiteManagedInputStore(database)
    barrier = threading.Barrier(2)

    def submit(candidate: SQLiteManagedInputStore):
        barrier.wait(timeout=5)
        return candidate.submit_responses(
            "execution-1",
            "round-1",
            _responses(),
            owner_id="worker-a",
            lease_token=created.lease_token,
            response_idempotency_key="response-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, (first, second)))
    assert {result.status for result in results} == {"response_validated"}
    assert first.get_round("execution-1", "round-1") == second.get_round(
        "execution-1", "round-1"
    )


def test_managed_values_are_defensive_and_keep_redacted_diagnostic_projection(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pending = _pending()
    created = store.create_round(
        pending,
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
        operation_parameters={"token": "operation-secret"},
    )
    responses = _responses()
    responses["address"] = ElicitationResponse(
        action="accept", content={"city": "Pune", "token": "super-secret"}
    )
    record = store.submit_responses(
        "execution-1",
        "round-1",
        responses,
        owner_id="worker-a",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    responses = record.responses
    assert responses is not None
    with pytest.raises(TypeError):
        responses["address"] = ElicitationResponse(action="decline")  # type: ignore[index]
    assert store.get_round("execution-1", "round-1").responses == responses
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        diagnostic = connection.execute(
            "SELECT responses_redacted_json FROM m3_managed_input_diagnostics"
        ).fetchone()[0]
    assert "super-secret" not in diagnostic
    assert "operation-secret" not in diagnostic
    assert "[REDACTED]" in diagnostic
    assert store.redacted_responses("execution-1", "round-1") == {
        "address": {
            "action": "accept",
            "content": {"city": "Pune", "token": "[REDACTED]"},
            "meta": None,
        },
        "payment": {"action": "accept", "content": None, "meta": None},
    }


def test_recovery_unavailable_is_terminal_and_old_owner_cannot_clear_it(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    store = SQLiteManagedInputStore(tmp_path / "m3.sqlite", clock=clock)
    created = store.create_round(
        _pending(),
        round_index=0,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
    )
    validated = store.submit_responses(
        "execution-1",
        "round-1",
        _responses(),
        owner_id="worker-a",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    started = store.start_delivery(
        "execution-1", "round-1", owner_id="worker-a", lease_token=validated.lease_token
    )
    clock.value += timedelta(seconds=31)
    with pytest.raises(ManagedInputRecoveryError):
        store.claim_round(
            "execution-1",
            "round-1",
            owner_id="worker-b",
            lease_seconds=30,
            expected_lease_token=started.lease_token,
        )
    failed = store.fail_recovery(
        "execution-1",
        "round-1",
        expected_lease_token=started.lease_token,
        message="native interaction cannot be resumed safely",
    )
    assert failed.status == "failed"
    with pytest.raises(ManagedInputRecoveryError):
        store.claim_round(
            "execution-1",
            "round-1",
            owner_id="worker-b",
            lease_seconds=30,
            expected_lease_token=created.lease_token,
        )


@pytest.mark.parametrize(
    "state", ["pending", "response_validated", "delivery_started", "delivered"]
)
def test_recovery_failure_can_terminalize_each_unresolved_state(
    tmp_path: Path, state: str
) -> None:
    clock = _Clock()
    store = SQLiteManagedInputStore(tmp_path / "m3.sqlite", clock=clock)
    record = store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    if state in {"response_validated", "delivery_started", "delivered"}:
        record = store.submit_responses(
            "execution-1",
            "round-1",
            _responses(),
            owner_id="worker-a",
            lease_token=record.lease_token,
            response_idempotency_key="response-1",
        )
    if state in {"delivery_started", "delivered"}:
        record = store.start_delivery(
            "execution-1",
            "round-1",
            owner_id="worker-a",
            lease_token=record.lease_token,
        )
    if state == "delivered":
        record = store.mark_delivered(
            "execution-1",
            "round-1",
            owner_id="worker-a",
            lease_token=record.lease_token,
        )
    clock.value += timedelta(seconds=31)
    failed = store.fail_recovery(
        "execution-1",
        "round-1",
        expected_lease_token=record.lease_token,
        message="replacement worker cannot prove safe recovery",
    )
    assert failed.status == "failed"
    assert failed.failure_code == "recovery_unavailable"


def test_delivery_started_claim_requires_evidence_and_updates_state(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    store = SQLiteManagedInputStore(tmp_path / "m3.sqlite", clock=clock)
    created = store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    validated = store.submit_responses(
        "execution-1",
        "round-1",
        _responses(),
        owner_id="worker-a",
        lease_token=created.lease_token,
        response_idempotency_key="response-1",
    )
    started = store.start_delivery(
        "execution-1", "round-1", owner_id="worker-a", lease_token=validated.lease_token
    )
    clock.value += timedelta(seconds=31)
    with pytest.raises(ManagedInputRecoveryError):
        store.claim_round(
            "execution-1",
            "round-1",
            owner_id="worker-b",
            lease_seconds=30,
            expected_lease_token=started.lease_token,
            delivery_state="not_delivered",
        )
    reclaimed = store.claim_round(
        "execution-1",
        "round-1",
        owner_id="worker-b",
        lease_seconds=30,
        expected_lease_token=started.lease_token,
        delivery_state="not_delivered",
        idempotent_delivery=True,
    )
    assert reclaimed.status == "response_validated"

    second = store.create_round(
        _pending(round_id="round-2"),
        round_index=1,
        round_limit=10,
        owner_id="worker-a",
        lease_seconds=30,
    )
    second = store.submit_responses(
        "execution-1",
        "round-2",
        _responses(),
        owner_id="worker-a",
        lease_token=second.lease_token,
        response_idempotency_key="response-2",
    )
    second = store.start_delivery(
        "execution-1", "round-2", owner_id="worker-a", lease_token=second.lease_token
    )
    clock.value += timedelta(seconds=31)
    delivered = store.claim_round(
        "execution-1",
        "round-2",
        owner_id="worker-b",
        lease_seconds=30,
        expected_lease_token=second.lease_token,
        delivery_state="delivered",
    )
    assert delivered.status == "delivered"
    assert delivered.delivered_at is not None


def test_pre_feature_database_is_migrated_idempotently(tmp_path: Path) -> None:
    database = tmp_path / "m3.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('kept')")
        connection.commit()
    store = SQLiteManagedInputStore(database)
    assert store.get_round("missing", "missing") is None
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "m3_managed_input_rounds" in tables
        assert connection.execute("SELECT value FROM legacy_marker").fetchone() == (
            "kept",
        )
    SQLiteManagedInputStore(database)


def test_invalid_request_state_protocol_types_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PendingElicitationRound.model_validate(
            {
                **_pending().model_dump(mode="json"),
                "request_state": {"not": "a string"},
            }
        )
    assert _pending(request_state="").request_state == ""


def test_clock_must_be_timezone_aware(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SQLiteManagedInputStore(
            tmp_path / "m3.sqlite", clock=lambda: datetime(2026, 1, 1)
        )


def test_corrupt_persisted_status_is_reported_as_typed_storage_failure(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create_round(
        _pending(), round_index=0, round_limit=10, owner_id="worker-a", lease_seconds=30
    )
    with sqlite3.connect(tmp_path / "m3.sqlite") as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE m3_managed_input_rounds SET status='corrupt' WHERE execution_id=?",
            ("execution-1",),
        )
        connection.commit()
    with pytest.raises(ManagedInputValidationError, match="stored"):
        store.get_round("execution-1", "round-1")
