"""Typed, harness-neutral observation contracts.

This module is intentionally small and provider agnostic.  Concrete harnesses
translate their native messages into these values; they do not extend the
discriminator with provider-specific fields.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from math import isfinite
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    Field,
    JsonValue,
    StrictBool,
    TypeAdapter,
    field_validator,
    model_validator,
)

from ..types import FrozenModel


class RawEvidenceInput(FrozenModel):
    """Bounded input supplied by an adapter before store redaction.

    The explicit encoding keeps binary evidence JSON serializable without
    asking adapters to hide a conversion in an untyped evidence map.
    """

    content: str
    media_type: str = Field(min_length=1, max_length=256)
    encoding: Literal["utf8", "base64"] = "utf8"

    def as_bytes(self) -> bytes:
        if self.encoding == "utf8":
            return self.content.encode("utf-8")
        try:
            return base64.b64decode(self.content.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError):
            raise ValueError("base64 raw evidence is malformed") from None

    def __repr__(self) -> str:
        # Media types may originate from untrusted adapters; never echo them
        # (or raw content) in diagnostics and reprs.
        return f"RawEvidenceInput(encoding={self.encoding!r}, size={len(self.content)})"


class HarnessObservationBase(FrozenModel):
    """Fields common to every observation emitted by a harness adapter."""

    observation_id: str = Field(min_length=1, max_length=256)
    harness_kind: str = Field(min_length=1, max_length=128)
    turn_sequence: int = Field(ge=0)
    wall_time: datetime
    monotonic_offset_ms: float = Field(ge=0)
    provider_id: str | None = Field(default=None, min_length=1, max_length=256)
    block_id: str | None = Field(default=None, min_length=1, max_length=256)
    call_id: str | None = Field(default=None, min_length=1, max_length=256)
    raw_evidence: RawEvidenceInput | None = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(observation_id={self.observation_id!r}, "
            f"kind={getattr(self, 'kind', 'unknown')!r}, "
            f"harness_kind={self.harness_kind!r})"
        )

    @field_validator("wall_time")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("wall_time must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("turn_sequence", mode="before")
    @classmethod
    def _strict_turn_sequence(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("turn_sequence must be an integer")
        return value

    @field_validator("monotonic_offset_ms", mode="before")
    @classmethod
    def _finite_offset(cls, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("monotonic_offset_ms must be numeric")
        if not isfinite(value):
            raise ValueError("monotonic_offset_ms must be finite")
        return value


class RawFrameObservation(HarnessObservationBase):
    kind: Literal["raw_frame"] = "raw_frame"
    direction: Literal["inbound", "outbound"] = "inbound"
    media_type: str = Field(
        default="application/octet-stream", min_length=1, max_length=256
    )
    payload: JsonValue | None = None
    text: str | None = None


class MessageChunkObservation(HarnessObservationBase):
    kind: Literal["message_chunk"] = "message_chunk"
    role: Literal["user", "assistant", "system", "tool"] = "assistant"
    text: str | None = None
    content: tuple[JsonValue, ...] = ()
    message_id: str | None = Field(default=None, min_length=1, max_length=256)
    complete: StrictBool = False


class ReasoningChunkObservation(HarnessObservationBase):
    kind: Literal["reasoning_chunk"] = "reasoning_chunk"
    text: str | None = Field(default=None, max_length=8_388_608)
    visibility: Literal["visible", "encrypted", "provider_hidden", "unavailable"] = (
        "visible"
    )
    complete: StrictBool = False


class ToolCallObservedObservation(HarnessObservationBase):
    kind: Literal["tool_call_observed"] = "tool_call_observed"
    server: str | None = Field(default=None, min_length=1, max_length=256)
    tool: str = Field(min_length=1, max_length=256)
    arguments: JsonValue | None = None
    status: (
        Literal[
            "success",
            "tool_error",
            "protocol_error",
            "transport_error",
            "cancelled",
            "timed_out",
            "incomplete",
        ]
        | None
    ) = None


class ToolResultObservedObservation(HarnessObservationBase):
    kind: Literal["tool_result_observed"] = "tool_result_observed"
    result: JsonValue | None = None
    is_error: StrictBool | None = None
    status: (
        Literal[
            "success",
            "tool_error",
            "protocol_error",
            "transport_error",
            "cancelled",
            "timed_out",
            "incomplete",
        ]
        | None
    ) = None
    error_message: str | None = None


class UsageObservedObservation(HarnessObservationBase):
    kind: Literal["usage_observed"] = "usage_observed"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cache_creation_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=1, max_length=16)

    @model_validator(mode="after")
    def _finite_cost(self) -> UsageObservedObservation:
        if self.cost is not None and not isfinite(self.cost):
            raise ValueError("cost must be finite")
        return self

    @field_validator(
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        mode="before",
    )
    @classmethod
    def _strict_token_count(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise TypeError("token counts must be integers")
        return value

    @field_validator("cost", mode="before")
    @classmethod
    def _strict_cost(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise TypeError("cost must be numeric")
        return value


class PlanObservedObservation(HarnessObservationBase):
    kind: Literal["plan_observed"] = "plan_observed"
    plan: JsonValue | None = None
    status: str | None = Field(default=None, min_length=1, max_length=128)


class StateObservedObservation(HarnessObservationBase):
    kind: Literal["state_observed"] = "state_observed"
    state: str = Field(min_length=1, max_length=128)
    detail: JsonValue | None = None


class InteractionObservedObservation(HarnessObservationBase):
    kind: Literal["interaction_observed"] = "interaction_observed"
    interaction_kind: Literal[
        "permission.request",
        "permission.response",
        "sampling.request",
        "sampling.response",
        "elicitation.request",
        "elicitation.response",
        "filesystem.read.request",
        "filesystem.read.response",
        "filesystem.write.request",
        "filesystem.write.response",
        "terminal.create.request",
        "terminal.create.response",
        "terminal.output.request",
        "terminal.output.response",
        "terminal.wait.request",
        "terminal.wait.response",
        "terminal.release.request",
        "terminal.release.response",
        "terminal.kill.request",
        "terminal.kill.response",
    ]
    request: JsonValue | None = None
    response: JsonValue | None = None


class ProcessObservedObservation(HarnessObservationBase):
    kind: Literal["process_observed"] = "process_observed"
    phase: Literal["started", "exited", "failed"]
    executable: str | None = Field(default=None, min_length=1, max_length=4096)
    pid: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    signal: int | None = Field(default=None, ge=0)
    stderr: str | None = None
    stderr_state: (
        Literal["observed", "disabled", "unavailable", "truncated", "redacted"] | None
    ) = None

    @field_validator("pid", "exit_code", "signal", mode="before")
    @classmethod
    def _strict_int(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("process numeric fields must be integers")
        return value


class MetadataObservedObservation(HarnessObservationBase):
    kind: Literal["metadata_observed"] = "metadata_observed"
    name: str = Field(min_length=1, max_length=256)
    value: JsonValue | None = None


HarnessObservation: TypeAlias = Annotated[
    RawFrameObservation
    | MessageChunkObservation
    | ReasoningChunkObservation
    | ToolCallObservedObservation
    | ToolResultObservedObservation
    | UsageObservedObservation
    | PlanObservedObservation
    | StateObservedObservation
    | InteractionObservedObservation
    | ProcessObservedObservation
    | MetadataObservedObservation,
    Field(discriminator="kind"),
]

HARNESS_OBSERVATION_ADAPTER: TypeAdapter[HarnessObservation] = TypeAdapter(
    HarnessObservation
)


class TurnEvidence(FrozenModel):
    """Immutable, typed evidence produced by one harness turn."""

    sequence: int = Field(ge=0)
    status: Literal["completed", "failed", "timed_out", "cancelled", "interrupted"]
    observations: tuple[HarnessObservation, ...] = ()
    limitations: tuple[str, ...] = ()

    @field_validator("sequence", mode="before")
    @classmethod
    def _strict_sequence(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("sequence must be an integer")
        return value

    @model_validator(mode="after")
    def _consistent_observations(self) -> TurnEvidence:
        ids = tuple(item.observation_id for item in self.observations)
        if len(ids) != len(set(ids)):
            raise ValueError("turn observation IDs must be unique")
        if any(item.turn_sequence != self.sequence for item in self.observations):
            raise ValueError("turn observations must have the turn sequence")
        _validate_limitations(self.limitations)
        return self


class HarnessSessionEvidence(FrozenModel):
    """Immutable session-level evidence, ordered by turn sequence."""

    session_id: str = Field(min_length=1, max_length=256)
    turns: tuple[TurnEvidence, ...] = ()
    limitations: tuple[str, ...] = ()
    closed: StrictBool = False

    @model_validator(mode="after")
    def _ordered_turns(self) -> HarnessSessionEvidence:
        sequences = tuple(turn.sequence for turn in self.turns)
        if sequences != tuple(sorted(set(sequences))):
            raise ValueError("session turns must have unique ascending sequences")
        _validate_limitations(self.limitations)
        return self


_ALLOWED_LIMITATIONS = frozenset(
    {
        "cleanup_failed",
        "persistence_failed",
        "capture_incomplete",
        "capture_disabled",
        "partial_trace",
    }
)


def _validate_limitations(values: tuple[str, ...]) -> None:
    if any(
        not isinstance(item, str)
        or not item.strip()
        or item not in _ALLOWED_LIMITATIONS
        for item in values
    ):
        raise ValueError("limitations contain an unknown or empty value")
    if len(values) != len(set(values)):
        raise ValueError("limitations must be unique")


__all__ = [
    "HARNESS_OBSERVATION_ADAPTER",
    "HarnessObservation",
    "HarnessObservationBase",
    "HarnessSessionEvidence",
    "InteractionObservedObservation",
    "MessageChunkObservation",
    "MetadataObservedObservation",
    "PlanObservedObservation",
    "ProcessObservedObservation",
    "RawEvidenceInput",
    "RawFrameObservation",
    "ReasoningChunkObservation",
    "StateObservedObservation",
    "ToolCallObservedObservation",
    "ToolResultObservedObservation",
    "TurnEvidence",
    "UsageObservedObservation",
]
