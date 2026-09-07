"""Shared bounded, redacted raw-evidence helpers for execution stores."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

from ..errors import RawEvidenceIntegrityError, RawEvidenceUnavailable
from ..observability import (
    Observation,
    ObservationReason,
    ObservationState,
    RawEvidence,
    EvidenceCapture,
    CaptureOptions,
)
from ..trace.redaction import RedactionConfig, redact_artifact_bytes, redact_result
from ..types import EventId, EvidenceRef


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    """Redacted and bounded bytes plus the metadata needed by a store."""

    content: bytes
    preview: str
    original_size_bytes: int
    redaction_count: int
    truncated: bool


def evidence_id_for(event_id: EventId | str) -> str:
    """Return the stable opaque raw-evidence identifier for an event."""

    value = str(
        event_id.root if isinstance(event_id, EventId) else EventId(event_id).root
    )
    if not value:
        raise RawEvidenceUnavailable("raw evidence reference is invalid")
    return f"re:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def validate_evidence_id(evidence_id: str) -> str:
    """Validate the fixed-length opaque identifier without decoding ownership."""

    if not isinstance(evidence_id, str) or not re.fullmatch(
        r"re:[0-9a-f]{64}", evidence_id
    ):
        raise RawEvidenceUnavailable("raw evidence reference is invalid")
    return evidence_id


def prepare_evidence(
    content: bytes,
    *,
    config: CaptureOptions,
    redaction_config: RedactionConfig,
    remaining_bytes: int,
) -> PreparedEvidence:
    """Redact first, then apply frame and execution bounds."""

    if not isinstance(content, bytes):
        raise TypeError("raw evidence content must be bytes")
    if remaining_bytes < 0:
        raise ValueError("remaining evidence budget must be non-negative")
    if not config.capture_raw_evidence:
        raise RawEvidenceUnavailable("raw evidence capture is disabled")

    # Redaction is deliberately performed before either limit is applied. Run
    # the structural projection for JSON so sensitive keys and URL credentials
    # are removed even when their values were not configured as canaries. The
    # byte pass still handles configured secrets in all formats.
    redaction = redact_artifact_bytes(content, config=redaction_config)
    redacted = redaction.data
    try:
        parsed = json.loads(redacted.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    if parsed is not None:
        projected = redact_result(
            parsed, config=redaction_config, path="$.raw_evidence"
        ).value
        if projected != parsed:
            redacted = json.dumps(
                projected,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            structural_count = 1
        else:
            structural_count = 0
    else:
        structural_count = 0
    limit = min(config.raw_frame_bytes, remaining_bytes)
    bounded = redacted[:limit]
    truncated = len(bounded) < len(redacted)
    preview = _preview(bounded, config.raw_preview_bytes)
    return PreparedEvidence(
        bounded,
        preview,
        len(content),
        redaction.redacted_count + structural_count,
        truncated,
    )


def make_ref(
    event_id: EventId | str, content: bytes, *, media_type: str
) -> EvidenceRef:
    """Build metadata for already-redacted, already-bounded bytes."""

    if not media_type or len(media_type) > 256:
        raise ValueError(
            "raw evidence media_type must be non-empty and at most 256 characters"
        )
    return EvidenceRef(
        evidence_id=evidence_id_for(event_id),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        media_type=media_type,
    )


def verify_reference(reference: EvidenceRef, expected: EvidenceRef) -> None:
    """Reject forged, stale, or non-raw references without exposing metadata."""

    validate_evidence_id(reference.evidence_id)
    if reference.evidence_id != expected.evidence_id:
        raise RawEvidenceUnavailable("raw evidence reference is invalid")
    for field in ("sha256", "size_bytes", "media_type", "storage_key"):
        supplied = getattr(reference, field)
        if supplied is not None and supplied != getattr(expected, field):
            raise RawEvidenceIntegrityError(
                "raw evidence metadata does not match stored content"
            )


def make_capture(
    reference: EvidenceRef, prepared: PreparedEvidence
) -> EvidenceCapture:
    """Build explicit preview state while retaining both capture booleans."""

    if prepared.truncated:
        state = ObservationState.TRUNCATED
        reason = ObservationReason.EVIDENCE_TRUNCATED
    elif prepared.redaction_count:
        state = ObservationState.REDACTED
        reason = ObservationReason.REDACTED_BY_POLICY
    else:
        state = ObservationState.OBSERVED
        reason = None
    preview = Observation[str](
        state=state,
        value=prepared.preview,
        reason=reason,
        evidence_ref=reference,
    )
    return EvidenceCapture(
        reference=reference,
        preview=preview,
        original_size_bytes=prepared.original_size_bytes,
        stored_size_bytes=len(prepared.content),
        redacted=prepared.redaction_count > 0,
        truncated=prepared.truncated,
    )


def make_result(
    reference: EvidenceRef, content: bytes, *, max_bytes: int
) -> RawEvidence:
    """Verify and expose bounded content through the typed public model."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    if (
        reference.sha256 is None
        or reference.size_bytes is None
        or reference.media_type is None
    ):
        raise RawEvidenceIntegrityError("raw evidence metadata is incomplete")
    if (
        len(content) != reference.size_bytes
        or hashlib.sha256(content).hexdigest() != reference.sha256
    ):
        raise RawEvidenceIntegrityError("raw evidence integrity verification failed")
    visible = content[:max_bytes]
    return RawEvidence(
        reference=reference,
        media_type=reference.media_type,
        content=_text_or_base64(visible),
        size_bytes=reference.size_bytes,
        returned_size_bytes=len(visible),
        truncated=len(visible) < len(content),
    )


def _preview(content: bytes, limit: int) -> str:
    value = content[:limit]
    return _text_or_base64(value)


def _text_or_base64(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        encoded = base64.b64encode(content).decode("ascii")
        return f"base64:{encoded}"


__all__ = [
    "PreparedEvidence",
    "evidence_id_for",
    "make_capture",
    "make_ref",
    "make_result",
    "prepare_evidence",
    "validate_evidence_id",
    "verify_reference",
]
