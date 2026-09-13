"""Failure-safe bridge from harness observations to stable trace events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..execution_trace import ExecutionTraceRecorder
from ..observability import CaptureOptions
from ..trace.redaction import redact_for_persistence
from ..types import EventKind, EventOrigin, EventSource, EvidenceRef, TurnId
from .observations import (
    HARNESS_OBSERVATION_ADAPTER,
    HarnessObservation,
    InteractionObservedObservation,
    MessageChunkObservation,
    MetadataObservedObservation,
    PlanObservedObservation,
    ProcessObservedObservation,
    RawFrameObservation,
    ReasoningChunkObservation,
    StateObservedObservation,
    ToolCallObservedObservation,
    ToolResultObservedObservation,
    UsageObservedObservation,
)

_PROVIDER_OBSERVATIONS = (
    MessageChunkObservation,
    ReasoningChunkObservation,
)


class HarnessObservationSink:
    """Convert typed adapter observations into safe stable evidence.

    The sink is deliberately a one-way, best-effort boundary.  ``emit`` never
    raises, including when an adapter hands it malformed data or persistence
    has failed.  The recorder remains the sole authority for event validation,
    redaction, ordering, and terminal-state protection.
    """

    def __init__(
        self,
        recorder: ExecutionTraceRecorder,
        *,
        capture_config: CaptureOptions | None = None,
        turn_id: TurnId | str | None = None,
    ) -> None:
        self._recorder = recorder
        store_config = getattr(
            getattr(recorder, "_store", None), "_capture_config", None
        )
        self._capture_config = capture_config or store_config or CaptureOptions()
        self._limitations: list[str] = []
        self._raw_references: dict[str, EvidenceRef] = {}
        self._observation_ids: set[str] = set()
        self._pending_limitations: set[str] = set()
        # Session callers provide the real TurnId.  Standalone adapters retain
        # the deterministic fallback keyed by observation sequence.
        # Keep Identifier objects intact: ``str(TurnId(...))`` is the
        # Pydantic ``root='...'`` representation, not the identifier value.
        self._turn_id = turn_id

    @property
    def limitations(self) -> tuple[str, ...]:
        return tuple(self._limitations)

    @property
    def raw_references(self) -> Mapping[str, EvidenceRef]:
        return dict(self._raw_references)

    def emit(self, observation: HarnessObservation) -> None:
        """Persist one observation without allowing adapter-facing failures."""

        try:
            value = HARNESS_OBSERVATION_ADAPTER.validate_python(observation)
            self._pending_limitations.clear()
            if value.observation_id in self._observation_ids:
                self._limit("capture_incomplete")
                self._diagnostic("duplicate_harness_observation")
                return
            self._observation_ids.add(value.observation_id)
            if not self._capture_config.capture_provider_messages and isinstance(
                value, _PROVIDER_OBSERVATIONS
            ):
                self._limit("capture_disabled")
                return
            kind, payload, kwargs = self._normalize(value)
            raw = value.raw_evidence
            raw_content: bytes | None = None
            raw_media_type: str | None = None
            if raw is not None and self._capture_config.capture_raw_evidence:
                raw_content = raw.as_bytes()
                raw_media_type = raw.media_type
        except Exception:
            self._limit("capture_incomplete")
            self._diagnostic("harness_observation_failed")
            return
        try:
            event = self._recorder.emit(
                kind,
                payload=payload,
                provenance=EventSource(
                    origin=EventOrigin.HARNESS_REPORTED,
                    source=value.harness_kind,
                    provider_kind=value.harness_kind,
                ),
                turn_id=(
                    self._turn_id
                    if self._turn_id is not None
                    else f"turn-{value.turn_sequence}"
                ),
                **kwargs,
                raw_evidence_content=raw_content,
                raw_evidence_media_type=raw_media_type,
            )
        except Exception:
            self._limit("persistence_failed")
            self._diagnostic("harness_observation_persistence_failed")
            return
        if raw_content is not None and event.raw_evidence_ref is not None:
            self._raw_references[value.observation_id] = event.raw_evidence_ref
        elif raw is not None:
            self._limit("capture_disabled")
        raw_capture = event.payload.get("raw_capture")
        if isinstance(raw_capture, Mapping) and raw_capture.get("truncated") is True:
            self._pending_limitations.add("capture_incomplete")
        for limitation in tuple(self._pending_limitations):
            self._limit(limitation)
        self._pending_limitations.clear()

    def _normalize(
        self, observation: HarnessObservation
    ) -> tuple[EventKind, dict[str, Any], dict[str, Any]]:
        payload = self._common_payload(observation)
        if isinstance(observation, RawFrameObservation):
            payload.update(
                {"category": "raw_frame", "direction": observation.direction}
            )
            if "payload" in observation.model_fields_set:
                payload["data"] = self._safe_json(observation.payload)
            if "text" in observation.model_fields_set:
                payload["text"] = self._safe_text(observation.text, truncate=True)
            payload["media_type"] = observation.media_type
            return EventKind.PROVIDER_EVENT, payload, {}
        if isinstance(observation, MessageChunkObservation):
            content: list[Any] = list(observation.content)
            if observation.text is not None:
                content = [
                    {
                        "kind": "text",
                        "text": self._safe_text(observation.text, truncate=True),
                    }
                ]
            else:
                bounded: list[Any] = []
                used = 0
                for item in content:
                    projected = self._safe_json(item)
                    size = len(
                        json.dumps(
                            projected,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
                    if used + size > self._capture_config.raw_preview_bytes:
                        bounded.append({"capture": "truncated"})
                        break
                    bounded.append(projected)
                    used += size
                content = bounded
            payload.update(
                {
                    "role": observation.role,
                    "content": content,
                    "complete": observation.complete,
                }
            )
            if "message_id" in observation.model_fields_set:
                payload["message_id"] = observation.message_id
            return (
                EventKind.ASSISTANT_CONTENT
                if observation.role == "assistant"
                else EventKind.AGENT_MESSAGE,
                payload,
                {},
            )
        if isinstance(observation, ReasoningChunkObservation):
            if observation.visibility == "visible":
                payload["content"] = (
                    [
                        {
                            "kind": "text",
                            "text": self._safe_text(observation.text, truncate=True),
                        }
                    ]
                    if observation.text is not None
                    else []
                )
            return (
                EventKind.REASONING,
                payload,
                {
                    "reasoning": {
                        "visibility": observation.visibility,
                        "explicit": observation.visibility == "visible",
                    }
                },
            )
        if isinstance(observation, ToolCallObservedObservation):
            payload.update({"tool": observation.tool})
            if observation.server is not None:
                payload["server"] = observation.server
            if "arguments" in observation.model_fields_set:
                payload["arguments"] = self._safe_json(observation.arguments)
            if observation.status is not None:
                payload["tool_status"] = observation.status
            return (
                EventKind.TOOL_CALL_REQUESTED,
                payload,
                {"server_binding": observation.server},
            )
        if isinstance(observation, ToolResultObservedObservation):
            if "result" in observation.model_fields_set:
                payload["result"] = self._safe_json(observation.result)
            if "is_error" in observation.model_fields_set:
                if isinstance(payload.get("result"), dict):
                    payload["result"] = {
                        **payload["result"],
                        "isError": observation.is_error,
                    }
                else:
                    payload["isError"] = observation.is_error
            if observation.status is not None:
                payload["tool_status"] = observation.status
            if observation.error_message is not None:
                payload["error"] = {
                    "message": self._safe_text(observation.error_message, truncate=True)
                }
            return EventKind.TOOL_RESULT_RECEIVED, payload, {}
        if isinstance(observation, UsageObservedObservation):
            payload.update({"category": "usage"})
            for name in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cache_creation_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "total_tokens",
                "cost",
                "currency",
            ):
                if name in observation.model_fields_set:
                    payload[name] = getattr(observation, name)
            return EventKind.PROVIDER_EVENT, payload, {}
        if isinstance(observation, PlanObservedObservation):
            payload.update({"category": "plan"})
            if "plan" in observation.model_fields_set:
                payload["plan"] = self._safe_json(observation.plan)
            if "status" in observation.model_fields_set:
                payload["status"] = observation.status
            return EventKind.PROVIDER_EVENT, payload, {}
        if isinstance(observation, StateObservedObservation):
            payload.update({"category": "state", "state": observation.state})
            if "detail" in observation.model_fields_set:
                payload["detail"] = self._safe_json(observation.detail)
            return EventKind.PROVIDER_EVENT, payload, {}
        if isinstance(observation, InteractionObservedObservation):
            payload.update({"interaction_kind": observation.interaction_kind})
            if "request" in observation.model_fields_set:
                payload["request"] = self._safe_json(observation.request)
            if "response" in observation.model_fields_set:
                payload["response"] = self._safe_json(observation.response)
            try:
                return EventKind(observation.interaction_kind), payload, {}
            except ValueError:
                return (
                    EventKind.PROVIDER_EVENT,
                    {**payload, "category": "interaction"},
                    {},
                )
        if isinstance(observation, ProcessObservedObservation):
            if self._capture_config.capture_stderr:
                if "stderr" in observation.model_fields_set:
                    stderr = observation.stderr
                    if stderr is None:
                        payload["stderr_state"] = "unavailable"
                    else:
                        safe_stderr = self._safe_text(stderr, truncate=True)
                        state = "observed"
                        redacted = safe_stderr != stderr
                        if (
                            len(stderr.encode("utf-8"))
                            > self._capture_config.raw_preview_bytes
                        ):
                            state = "truncated"
                        elif redacted:
                            state = "redacted"
                        payload["stderr"] = safe_stderr
                        payload["stderr_state"] = state
                elif observation.stderr_state == "unavailable":
                    # With no content there is no contradictory value to
                    # distrust; preserve the adapter's genuine unavailable
                    # observation. Claims of redacted/truncated content are
                    # not accepted without captured bytes.
                    payload["stderr_state"] = "unavailable"
            else:
                payload["stderr_state"] = "disabled"
            for name in ("executable", "pid", "exit_code", "signal"):
                if name in observation.model_fields_set:
                    payload[name] = getattr(observation, name)
            payload["phase"] = observation.phase
            return (
                EventKind.PROCESS_STARTED
                if observation.phase == "started"
                else EventKind.PROCESS_EXITED,
                payload,
                {},
            )
        if isinstance(observation, MetadataObservedObservation):
            payload.update({"category": observation.name})
            if "value" in observation.model_fields_set:
                payload["data"] = self._safe_json(observation.value)
            return EventKind.PROVIDER_EVENT, payload, {}
        raise TypeError("unknown harness observation")

    @staticmethod
    def _common_payload(observation: HarnessObservation) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "observation_id": observation.observation_id,
            "harness_kind": observation.harness_kind,
            # ProviderEntry/UsageEntry require an explicit source identity;
            # harness_kind is the provider-neutral identity at this boundary.
            "provider": observation.harness_kind,
            "turn_sequence": observation.turn_sequence,
            "wall_time": observation.wall_time.isoformat(),
            "monotonic_offset_ms": observation.monotonic_offset_ms,
        }
        for name in ("provider_id", "block_id", "call_id"):
            value = getattr(observation, name)
            if value is not None:
                payload[name] = value
        return payload

    def _safe_text(self, value: str | None, *, truncate: bool = False) -> str | None:
        if value is None:
            return None
        safe = redact_for_persistence(
            value,
            config=getattr(self._recorder, "_redaction_config", None),
            path="$.harness_observation.text",
        )
        if not isinstance(safe, str):
            return None
        if truncate:
            bounded = safe.encode("utf-8")[: self._capture_config.raw_preview_bytes]
            if len(safe.encode("utf-8")) > self._capture_config.raw_preview_bytes:
                self._pending_limitations.add("capture_incomplete")
            safe = bounded.decode("utf-8", errors="ignore")
        return safe

    def _safe_json(self, value: Any) -> Any:
        safe = redact_for_persistence(
            value,
            config=getattr(self._recorder, "_redaction_config", None),
            path="$.harness_observation.value",
        )
        try:
            encoded = json.dumps(
                safe, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
        except (TypeError, ValueError):
            return {"capture": "unavailable"}
        if len(encoded.encode("utf-8")) > self._capture_config.raw_preview_bytes:
            self._pending_limitations.add("capture_incomplete")
            return {"capture": "truncated"}
        return safe

    def _limit(self, limitation: str) -> None:
        if limitation not in self._limitations:
            self._limitations.append(limitation)
        try:
            self._recorder.add_limitation(limitation)
        except Exception:
            pass

    def _diagnostic(self, code: str) -> None:
        try:
            self._recorder.emit(
                EventKind.DIAGNOSTIC,
                payload={
                    "code": code,
                    "message": "harness evidence capture was unavailable",
                },
                provenance=EventSource(
                    origin=EventOrigin.DERIVED, source="mcp_pal.harness"
                ),
            )
        except Exception:
            self._limit("persistence_failed")


__all__ = ["HarnessObservationSink"]
