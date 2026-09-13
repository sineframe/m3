"""Bounded, redacted raw-evidence storage contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mcp_pal.errors import RawEvidenceIntegrityError, RawEvidenceUnavailable
from mcp_pal.events import EventFactory
from mcp_pal.observability import (
    CaptureOptions,
    EvidenceCapture,
    Observation,
    ObservationState,
    RawEvidence,
)
from mcp_pal.storage import (
    InMemoryExecutionStore,
    SQLiteExecutionStore,
    StorageConflict,
)
from mcp_pal.storage.evidence import (
    evidence_id_for,
    prepare_evidence,
)
from mcp_pal.trace.redaction import RedactionConfig
from mcp_pal.types import (
    EventId,
    EventKind,
    EvidenceRef,
    ExecutionId,
    ExecutionState,
)


def _execution(
    store: InMemoryExecutionStore | SQLiteExecutionStore,
    name: str = "raw-evidence-execution",
) -> tuple[ExecutionId, EventId, EventId]:
    execution_id = ExecutionId(name)
    store.create(ExecutionState(execution_id=execution_id))
    factory = EventFactory(execution_id)
    created = factory.create(EventKind.EXECUTION_CREATED, payload={})
    evidence_event = factory.create(EventKind.DIAGNOSTIC, payload={"message": "safe"})
    store.append_events((created, evidence_event))
    return execution_id, created.event_id, evidence_event.event_id


def _store(
    kind: str,
    tmp_path: Path,
    *,
    config: CaptureOptions | None = None,
    redaction: RedactionConfig | None = None,
) -> InMemoryExecutionStore | SQLiteExecutionStore:
    if kind == "memory":
        return InMemoryExecutionStore(config=redaction, capture_config=config)
    return SQLiteExecutionStore(
        tmp_path / "raw.sqlite",
        blob_root=tmp_path / "raw-blobs",
        config=redaction,
        capture_config=config,
    )


def test_capture_config_defaults_and_positive_caps() -> None:
    assert CaptureOptions().model_dump() == {
        "capture_raw_evidence": True,
        "capture_provider_messages": True,
        "capture_stderr": True,
        "raw_preview_bytes": 65_536,
        "raw_frame_bytes": 1_048_576,
        "raw_execution_bytes": 67_108_864,
    }
    with pytest.raises(ValueError):
        CaptureOptions(raw_frame_bytes=0)
    with pytest.raises(ValueError):
        CaptureOptions(raw_preview_bytes=0)
    with pytest.raises(ValueError):
        CaptureOptions(raw_execution_bytes=0)


def test_public_evidence_models_reject_inconsistent_bounds_and_capture_metadata() -> (
    None
):
    reference = EvidenceRef(evidence_id=evidence_id_for("evidence-model"), size_bytes=3)
    with pytest.raises(ValueError):
        RawEvidence(
            reference=reference,
            media_type="text/plain",
            content="abcd",
            size_bytes=3,
            returned_size_bytes=4,
        )
    with pytest.raises(ValueError):
        RawEvidence(
            reference=reference,
            media_type="text/plain",
            content="a",
            size_bytes=3,
            returned_size_bytes=1,
            truncated=False,
        )
    preview = Observation[str](
        state=ObservationState.OBSERVED, value="abc", evidence_ref=reference
    )
    EvidenceCapture(
        reference=reference,
        preview=preview,
        original_size_bytes=3,
        stored_size_bytes=3,
        redacted=False,
        truncated=False,
    )
    with pytest.raises(ValueError):
        EvidenceCapture(
            reference=reference,
            preview=preview,
            original_size_bytes=3,
            stored_size_bytes=2,
            redacted=False,
            truncated=False,
        )
    with pytest.raises(ValueError):
        EvidenceCapture(
            reference=reference,
            preview=preview.model_copy(
                update={
                    "evidence_ref": EvidenceRef(evidence_id=evidence_id_for("other"))
                }
            ),
            original_size_bytes=3,
            stored_size_bytes=3,
            redacted=False,
            truncated=False,
        )
    with pytest.raises(ValueError):
        EvidenceCapture(
            reference=reference,
            preview=preview,
            original_size_bytes=3,
            stored_size_bytes=3,
            redacted=True,
            truncated=False,
        )


def test_evidence_id_is_opaque_and_round_trips() -> None:
    event_id = "e" * 256
    evidence_id = evidence_id_for(event_id)
    assert evidence_id.startswith("re:")
    assert len(evidence_id) == 67
    assert evidence_id == evidence_id_for(event_id)
    assert evidence_id != evidence_id_for("f" * 256)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_long_event_id_round_trips_through_concrete_stores(
    kind: str, tmp_path: Path
) -> None:
    store = _store(kind, tmp_path)
    execution_id = ExecutionId(f"long-{kind}")
    store.create(ExecutionState(execution_id=execution_id))
    factory = EventFactory(execution_id)
    created = factory.create(EventKind.EXECUTION_CREATED, payload={})
    long_event_id = EventId("e" * 256)
    event = factory.create(
        EventKind.DIAGNOSTIC, event_id=long_event_id, payload={"message": "safe"}
    )
    store.append_events((created, event))
    capture = store.put_raw_evidence(long_event_id, b"long-id", media_type="text/plain")
    assert len(capture.reference.evidence_id) == 67
    assert store.read_raw_evidence(capture.reference).content == "long-id"


def test_preview_is_utf8_safe_and_json_serializable() -> None:
    prepared = prepare_evidence(
        "snowman ✓ and secret".encode(),
        config=CaptureOptions(
            raw_preview_bytes=8, raw_frame_bytes=128, raw_execution_bytes=128
        ),
        redaction_config=RedactionConfig(
            secrets=frozenset({"secret"}), include_environment=False
        ),
        remaining_bytes=128,
    )
    assert "secret" not in prepared.preview
    json.dumps(prepared.preview, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_structural_json_redaction_happens_before_blob_publication(
    kind: str, tmp_path: Path
) -> None:
    config = RedactionConfig(
        secrets=frozenset({"configured-secret"}), include_environment=False
    )
    store = _store(kind, tmp_path, redaction=config)
    _, _, event_id = _execution(store)
    raw = json.dumps(
        {
            "nested": {
                "api_key": "unknown-value",
                "url": "https://user:password@example.test/path?token=unknown-token",
            },
            "configured": "configured-secret",
        }
    ).encode()
    capture = store.put_raw_evidence(event_id, raw, media_type="application/json")
    visible = str(store.read_raw_evidence(capture.reference).content)
    for secret in (
        "unknown-value",
        "password",
        "unknown-token",
        "configured-secret",
    ):
        assert secret not in visible
    assert "[REDACTED]" in visible
    if kind == "sqlite":
        blob_files = (tmp_path / "raw-blobs").rglob("*.gz")
        stored_bytes = b"".join(path.read_bytes() for path in blob_files)
        for secret in (
            "unknown-value",
            "password",
            "unknown-token",
            "configured-secret",
        ):
            assert secret.encode() not in stored_bytes


def test_malformed_json_and_opaque_binary_use_byte_redaction_only() -> None:
    config = RedactionConfig(
        secrets=frozenset({"configured-secret"}), include_environment=False
    )
    malformed = prepare_evidence(
        b'{"api_key":"unknown-value", "value":"configured-secret"',
        config=CaptureOptions(),
        redaction_config=config,
        remaining_bytes=1024,
    )
    assert b"configured-secret" not in malformed.content
    assert b"unknown-value" in malformed.content
    binary = prepare_evidence(
        b"\x00configured-secret\xff",
        config=CaptureOptions(),
        redaction_config=config,
        remaining_bytes=1024,
    )
    assert b"configured-secret" not in binary.content
    assert binary.content.startswith(b"\x00")


def test_disabled_capture_does_not_publish_evidence() -> None:
    store = InMemoryExecutionStore(
        capture_config=CaptureOptions(capture_raw_evidence=False)
    )
    _, _, event_id = _execution(store)
    with pytest.raises(RawEvidenceUnavailable):
        store.put_raw_evidence(event_id, b"secret", media_type="text/plain")


def test_capture_metadata_reports_redaction_without_truncation() -> None:
    store = InMemoryExecutionStore(
        config=RedactionConfig(
            secrets=frozenset({"secret"}), include_environment=False
        ),
        capture_config=CaptureOptions(raw_frame_bytes=64, raw_execution_bytes=64),
    )
    _, _, event_id = _execution(store)
    capture = store.put_raw_evidence(event_id, b"secret", media_type="text/plain")
    assert capture.redacted is True
    assert capture.truncated is False
    assert capture.preview.state.value == "redacted"
    assert capture.preview.reason.value == "redacted_by_policy"
    assert capture.preview.evidence_ref == capture.reference


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_redaction_frame_and_execution_caps_are_applied_before_storage(
    kind: str, tmp_path: Path
) -> None:
    config = CaptureOptions(
        raw_preview_bytes=4, raw_frame_bytes=8, raw_execution_bytes=10
    )
    store = _store(
        kind,
        tmp_path,
        config=config,
        redaction=RedactionConfig(
            secrets=frozenset({"secret"}), include_environment=False
        ),
    )
    execution_id, _, first_event = _execution(store)
    second_event = EventFactory(execution_id).create(
        EventKind.DIAGNOSTIC, payload={"second": True}
    )
    # The first event is already sequence 1; construct the next event with the
    # store's expected sequence through the original factory in real callers.
    second_event = second_event.model_copy(update={"sequence": 2})
    store.append_events((second_event,))
    first_capture = store.put_raw_evidence(
        first_event, b"secret-abcdefgh", media_type="text/plain"
    )
    second_capture = store.put_raw_evidence(
        second_event.event_id, b"0123456789", media_type="text/plain"
    )
    first = first_capture.reference
    second = second_capture.reference
    assert first_capture.original_size_bytes == len(b"secret-abcdefgh")
    assert first_capture.stored_size_bytes == 8
    assert first_capture.redacted is True
    assert first_capture.truncated is True
    assert first_capture.preview.state.value == "truncated"
    assert first_capture.preview.reason.value == "evidence_truncated"
    assert first_capture.preview.evidence_ref == first
    assert second_capture.stored_size_bytes == 2
    assert second_capture.truncated is True
    assert "secret" not in str(store.read_raw_evidence(first).content)
    bounded = store.read_raw_evidence(first, max_bytes=3)
    assert bounded.returned_size_bytes == 3
    assert bounded.truncated is True
    assert len(str(bounded.content)) <= 3
    assert store.read_raw_evidence(second).returned_size_bytes == 2


@pytest.mark.parametrize("size,stored", [(7, 7), (8, 8), (9, 8)])
def test_frame_cap_boundary(size: int, stored: int, tmp_path: Path) -> None:
    store = SQLiteExecutionStore(
        tmp_path / f"frame-{size}.sqlite",
        blob_root=tmp_path / f"frame-{size}-blobs",
        capture_config=CaptureOptions(raw_frame_bytes=8, raw_execution_bytes=64),
    )
    _, _, event_id = _execution(store)
    capture = store.put_raw_evidence(
        event_id, b"x" * size, media_type="application/octet-stream"
    )
    assert capture.reference.size_bytes == stored


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_missing_malformed_tampered_and_wrong_role_references_are_typed(
    kind: str, tmp_path: Path
) -> None:
    if kind == "sqlite":
        store: InMemoryExecutionStore | SQLiteExecutionStore = SQLiteExecutionStore(
            tmp_path / "raw.sqlite",
            blob_root=tmp_path / "raw-blobs",
            payload_blob_threshold=1,
        )
    else:
        store = _store(kind, tmp_path)
    _, _, event_id = _execution(store)
    if kind == "sqlite":
        with pytest.raises(RawEvidenceUnavailable):
            store.read_raw_evidence(EvidenceRef(evidence_id=evidence_id_for(event_id)))
    capture = store.put_raw_evidence(event_id, b"safe", media_type="text/plain")
    ref = capture.reference
    assert (
        store.read_raw_evidence(EvidenceRef(evidence_id=ref.evidence_id)).content
        == "safe"
    )
    with pytest.raises(RawEvidenceUnavailable):
        store.read_raw_evidence(EvidenceRef(evidence_id="bad"))
    with pytest.raises(RawEvidenceIntegrityError):
        store.read_raw_evidence(ref.model_copy(update={"sha256": "0" * 64}))
    with pytest.raises(RawEvidenceIntegrityError):
        store.read_raw_evidence(ref.model_copy(update={"size_bytes": 99}))
    with pytest.raises(RawEvidenceIntegrityError):
        store.read_raw_evidence(
            ref.model_copy(update={"media_type": "application/json"})
        )
    with pytest.raises(RawEvidenceIntegrityError):
        store.read_raw_evidence(ref.model_copy(update={"storage_key": "wrong-key"}))
    with pytest.raises(ValueError):
        store.read_raw_evidence(ref, max_bytes=-1)
    if isinstance(store, SQLiteExecutionStore):
        (tmp_path / "raw-blobs").mkdir(exist_ok=True)
        before = tuple((tmp_path / "raw-blobs").rglob("*.gz"))
        with pytest.raises(ValueError):
            store.put_raw_evidence(event_id, b"would-not-publish", media_type="")
        with pytest.raises(ValueError):
            store.put_raw_evidence(event_id, b"would-not-publish", media_type="x" * 257)
        assert tuple((tmp_path / "raw-blobs").rglob("*.gz")) == before
        assert ref.storage_key is not None
        (tmp_path / "raw-blobs" / ref.storage_key).write_bytes(b"tampered")
        with pytest.raises(RawEvidenceIntegrityError):
            store.read_raw_evidence(ref)


def test_sqlite_fresh_schema_allows_payload_and_raw_roles_and_reopen(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "raw.sqlite", blob_root=tmp_path / "blobs", payload_blob_threshold=1
    )
    execution_id, created_id, event_id = _execution(store)
    with pytest.raises(RawEvidenceUnavailable):
        store.read_raw_evidence(EvidenceRef(evidence_id=evidence_id_for(event_id)))
    capture = store.put_raw_evidence(event_id, b"durable", media_type="text/plain")
    ref = capture.reference
    with sqlite3.connect(tmp_path / "raw.sqlite") as connection:
        roles = connection.execute(
            "SELECT role,evidence_id FROM v2_event_blobs "
            "WHERE event_id=? ORDER BY role",
            (event_id.root,),
        ).fetchall()
        assert roles == [("payload", None), ("raw_evidence", ref.evidence_id)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_event_blobs(event_id,sha256,role) VALUES(?,?,?)",
                (event_id.root, ref.sha256, "invalid"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_event_blobs(event_id,sha256,role,media_type) "
                "VALUES(?,?,?,?)",
                (created_id.root, ref.sha256, "raw_evidence", "text/plain"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE v2_event_blobs SET media_type=? "
                "WHERE event_id=? AND role='payload'",
                ("application/json", created_id.root),
            )
        connection.execute(
            "UPDATE v2_event_blobs SET evidence_id=? "
            "WHERE event_id=? AND role='raw_evidence'",
            (evidence_id_for("another-event"), event_id.root),
        )
    tampered_store = SQLiteExecutionStore(
        tmp_path / "raw.sqlite", blob_root=tmp_path / "blobs"
    )
    with pytest.raises(RawEvidenceIntegrityError):
        tampered_store.read_raw_evidence(
            ref.model_copy(update={"evidence_id": evidence_id_for("another-event")})
        )
    tampered_store.close()
    with sqlite3.connect(tmp_path / "raw.sqlite") as connection:
        connection.execute(
            "UPDATE v2_event_blobs SET evidence_id=? "
            "WHERE event_id=? AND role='raw_evidence'",
            (ref.evidence_id, event_id.root),
        )
    reopened = SQLiteExecutionStore(
        tmp_path / "raw.sqlite", blob_root=tmp_path / "blobs"
    )
    reopened_value = reopened.read_raw_evidence(ref)
    assert reopened_value.content == "durable"
    assert reopened_value.returned_size_bytes == reopened_value.size_bytes == 7
    assert reopened_value.truncated is False
    assert reopened.read_raw_evidence(ref, max_bytes=0).returned_size_bytes == 0
    assert reopened.read_raw_evidence(ref, max_bytes=0).truncated is True
    with pytest.raises(StorageConflict):
        reopened.put_raw_evidence(event_id, b"again", media_type="text/plain")
    with sqlite3.connect(tmp_path / "raw.sqlite") as connection:
        assert connection.execute(
            "SELECT ref_count FROM v2_blobs WHERE sha256=?", (ref.sha256,)
        ).fetchone() == (1,)
    assert execution_id == ExecutionId("raw-evidence-execution")


def test_binary_evidence_is_json_safe_and_reports_read_bounds(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "binary.sqlite", blob_root=tmp_path / "binary-blobs"
    )
    _, _, event_id = _execution(store)
    capture = store.put_raw_evidence(
        event_id, b"\xff\x00binary", media_type="application/octet-stream"
    )
    value = store.read_raw_evidence(capture.reference, max_bytes=4)
    assert value.content == "base64:/wBiaQ=="
    assert value.size_bytes == 8
    assert value.returned_size_bytes == 4
    assert value.truncated is True


def test_sqlite_cleanup_does_not_touch_unrelated_files(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    store = SQLiteExecutionStore(tmp_path / "raw.sqlite", blob_root=tmp_path / "blobs")
    execution_id, _, event_id = _execution(store)
    store.put_raw_evidence(event_id, b"safe", media_type="text/plain")
    finished = (
        EventFactory(execution_id)
        .create(EventKind.EXECUTION_FINISHED, payload={"outcome": "completed"})
        .model_copy(update={"sequence": 2})
    )
    store.append_events((finished,))
    store.delete_execution(execution_id)
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_shared_blobs_are_refcounted_and_deleted_only_after_execution_cleanup(
    kind: str, tmp_path: Path
) -> None:
    store = _store(kind, tmp_path)
    first_execution, _, first_event = _execution(store, "raw-first")
    second_execution, _, second_execution_event = _execution(store, "raw-second")
    second_event = (
        EventFactory(first_execution)
        .create(EventKind.DIAGNOSTIC, payload={"second": True})
        .model_copy(update={"sequence": 2})
    )
    store.append_events((second_event,))
    first_capture = store.put_raw_evidence(
        first_event, b"same", media_type="text/plain"
    )
    second_capture = store.put_raw_evidence(
        second_execution_event, b"same", media_type="application/json"
    )
    first = first_capture.reference
    second = second_capture.reference
    assert first.sha256 == second.sha256
    assert second_capture.reference.media_type == "application/json"
    if isinstance(store, SQLiteExecutionStore):
        with sqlite3.connect(tmp_path / "raw.sqlite") as connection:
            assert connection.execute(
                "SELECT ref_count FROM v2_blobs WHERE sha256=?", (first.sha256,)
            ).fetchone() == (2,)
    finished = (
        EventFactory(first_execution)
        .create(EventKind.EXECUTION_FINISHED, payload={"outcome": "completed"})
        .model_copy(update={"sequence": 3})
    )
    store.append_events((finished,))
    store.delete_execution(first_execution)
    with pytest.raises(RawEvidenceUnavailable):
        store.read_raw_evidence(first)
    second_value = store.read_raw_evidence(second)
    assert second_value.content == "same"
    assert second_value.media_type == "application/json"
    if isinstance(store, SQLiteExecutionStore):
        with sqlite3.connect(tmp_path / "raw.sqlite") as connection:
            assert connection.execute(
                "SELECT ref_count FROM v2_blobs WHERE sha256=?", (first.sha256,)
            ).fetchone() == (1,)
        reopened = SQLiteExecutionStore(
            tmp_path / "raw.sqlite", blob_root=tmp_path / "raw-blobs"
        )
        assert reopened.read_raw_evidence(second).media_type == "application/json"
        reopened.close()
    second_finished = (
        EventFactory(second_execution)
        .create(EventKind.EXECUTION_FINISHED, payload={"outcome": "completed"})
        .model_copy(update={"sequence": 2})
    )
    store.append_events((second_finished,))
    store.delete_execution(second_execution)
    if isinstance(store, SQLiteExecutionStore):
        assert second.storage_key is not None
        assert not (tmp_path / "raw-blobs" / second.storage_key).exists()
    with pytest.raises(RawEvidenceUnavailable):
        store.read_raw_evidence(first)
    with pytest.raises(RawEvidenceUnavailable):
        store.read_raw_evidence(second)
