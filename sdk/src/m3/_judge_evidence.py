"""Redaction and size bounds for judge evidence bundles."""

from __future__ import annotations

import hashlib

from ._types.evals import JudgeEvidence
from .trace.redaction import RedactionConfig, redact_for_persistence

# Match the per-field bound the built-in judge already applies to its subject.
MAX_FIELD_BYTES = 32 * 1024
MAX_CLAIMS = 64
MAX_CLAIM_BYTES = 2 * 1024
_TEXT_FIELDS = ("input", "reference", "candidate", "rubric")


def _clip(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def bound_judge_evidence(
    evidence: JudgeEvidence, *, config: RedactionConfig
) -> JudgeEvidence:
    """Return ``evidence`` redacted with ``config`` and clipped to size bounds.

    Redaction runs before clipping and before the rubric digest is taken, so
    the digest identifies the rubric exactly as it is stored. Clipped fields
    are named in ``truncated``; supplied fields are never silently dropped.
    """

    value = redact_for_persistence(
        evidence.model_dump(mode="json"),
        config=config,
        path="$.evaluation.judge_evidence",
    )
    truncated = set(value.get("truncated") or ())
    rubric = value.get("rubric")
    if value.get("rubric_digest") is None and isinstance(rubric, str):
        value["rubric_digest"] = hashlib.sha256(rubric.encode("utf-8")).hexdigest()
    for name in _TEXT_FIELDS:
        text = value.get(name)
        if isinstance(text, str):
            value[name], clipped = _clip(text, MAX_FIELD_BYTES)
            if clipped:
                truncated.add(name)
    claims = value.get("claims")
    if claims is not None:
        kept: list[str] = []
        for claim in claims[:MAX_CLAIMS]:
            text, clipped = _clip(claim, MAX_CLAIM_BYTES)
            kept.append(text)
            if clipped:
                truncated.add("claims")
        if len(claims) > MAX_CLAIMS:
            truncated.add("claims")
        value["claims"] = kept
    value["truncated"] = sorted(truncated)
    return JudgeEvidence.model_validate(value)


__all__ = ["MAX_CLAIMS", "MAX_CLAIM_BYTES", "MAX_FIELD_BYTES", "bound_judge_evidence"]
