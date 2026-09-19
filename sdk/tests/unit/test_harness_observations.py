"""Harness observation contracts and failure-safe sink coverage."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from m3 import expect
from m3.agent_session import HarnessAdapter as AgentHarnessAdapter
from m3.errors import RawEvidenceIntegrityError
from m3.execution_trace import ExecutionTraceRecorder
from m3.harness import (
    HARNESS_OBSERVATION_ADAPTER,
    HarnessObservationSink,
    HarnessSessionEvidence,
    HarnessSessionSnapshot,
    HarnessTurnResult,
    InteractionObservedObservation,
    MessageChunkObservation,
    MetadataObservedObservation,
    PlanObservedObservation,
    ProcessObservedObservation,
    RawEvidenceInput,
    RawFrameObservation,
    ReasoningChunkObservation,
    StateObservedObservation,
    ToolCallObservedObservation,
    ToolResultObservedObservation,
    TurnEvidence,
    UsageObservedObservation,
)
from m3.harness.contracts import HarnessAdapter as ContractHarnessAdapter
from m3.observability import CaptureOptions
from m3.storage import (
    InMemoryExecutionStore,
    SQLiteExecutionStore,
    StorageConflict,
)
from m3.trace.redaction import RedactionConfig
from m3.types import (
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventSource,
    ExecutionOutcome,
    RequestLink,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_harness_adapter_identity_remains_compatible() -> None:
    from m3.harness import HarnessAdapter

    assert HarnessAdapter is AgentHarnessAdapter
    assert ContractHarnessAdapter is AgentHarnessAdapter


def _common(**kwargs: object) -> dict[str, object]:
    return {
        "observation_id": "obs-1",
        "harness_kind": "fixture",
        "turn_sequence": 1,
        "wall_time": NOW,
        "monotonic_offset_ms": 2.5,
        **kwargs,
    }


def _event_for_observation(recorder: ExecutionTraceRecorder, observation_id: str):
    return next(
        event
        for event in recorder.events()
        if event.payload.get("observation_id") == observation_id
    )


def test_closed_discriminator_accepts_exact_observation_variants() -> None:
    values = (
        RawFrameObservation(**_common()),
        MessageChunkObservation(**_common()),
        ReasoningChunkObservation(**_common()),
        ToolCallObservedObservation(**_common(tool="echo")),
        ToolResultObservedObservation(**_common()),
        UsageObservedObservation(**_common()),
        PlanObservedObservation(**_common()),
        StateObservedObservation(**_common(state="running")),
        InteractionObservedObservation(
            **_common(interaction_kind="permission.request")
        ),
        ProcessObservedObservation(**_common(phase="started")),
        MetadataObservedObservation(**_common(name="model")),
    )
    assert {item.kind for item in values} == {
        "raw_frame",
        "message_chunk",
        "reasoning_chunk",
        "tool_call_observed",
        "tool_result_observed",
        "usage_observed",
        "plan_observed",
        "state_observed",
        "interaction_observed",
        "process_observed",
        "metadata_observed",
    }


def test_observation_union_schema_and_json_round_trip() -> None:
    value = UsageObservedObservation(**_common(input_tokens=3))
    restored = HARNESS_OBSERVATION_ADAPTER.validate_python(
        value.model_dump(mode="json")
    )
    assert restored == value
    schema = HARNESS_OBSERVATION_ADAPTER.json_schema()
    assert schema.get("discriminator", {}).get("propertyName") == "kind"
    assert set(schema["$defs"]) >= {
        "RawFrameObservation",
        "UsageObservedObservation",
        "MetadataObservedObservation",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wall_time": datetime(2026, 1, 1, 12, 0)},
        {"turn_sequence": True},
        {"monotonic_offset_ms": -1},
        {"monotonic_offset_ms": float("inf")},
        {"kind": "unknown"},
        {"tool": "echo", "extra": "rejected"},
    ],
)
def test_observations_reject_malformed_or_open_values(
    kwargs: dict[str, object],
) -> None:
    values = _common(tool="echo")
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        ToolCallObservedObservation(**values)


def test_observation_boolean_fields_are_strict() -> None:
    with pytest.raises((TypeError, ValueError)):
        ToolResultObservedObservation(**_common(is_error="false"))  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        MessageChunkObservation(**_common(complete="false"))  # type: ignore[arg-type]


def test_sink_explicit_empty_turn_id_is_not_replaced_with_fallback() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "turn-id-edge")
    sink = HarnessObservationSink(recorder, turn_id="")
    sink.emit(MessageChunkObservation(**_common(observation_id="empty-turn", text="x")))
    assert "persistence_failed" in sink.limitations
    assert not any(
        event.payload.get("observation_id") == "empty-turn"
        and event.turn_id == "turn-1"
        for event in recorder.events()
    )


@pytest.mark.parametrize("field", ["pid", "exit_code", "signal"])
def test_process_numeric_fields_are_strict(field: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        ProcessObservedObservation(
            **_common(phase="exited", **{field: 1.5})  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("field", ["pid", "exit_code", "signal"])
def test_process_numeric_fields_allow_explicit_none(field: str) -> None:
    observation = ProcessObservedObservation(**_common(phase="exited", **{field: None}))
    assert getattr(observation, field) is None


def test_raw_input_supports_binary_explicitly_and_rejects_bad_base64() -> None:
    raw = RawEvidenceInput(
        content="AP+A", media_type="application/octet-stream", encoding="base64"
    )
    assert raw.as_bytes() == b"\x00\xff\x80"
    with pytest.raises(ValueError):
        RawEvidenceInput(
            content="not-base64!",
            media_type="application/octet-stream",
            encoding="base64",
        ).as_bytes()
    assert "not-base64" not in repr(raw)
    assert "application/octet-stream" not in repr(raw)


def test_usage_observation_contains_all_cache_dimensions() -> None:
    usage = UsageObservedObservation(
        **_common(
            input_tokens=1,
            output_tokens=2,
            reasoning_tokens=3,
            cache_creation_tokens=4,
            cache_read_tokens=5,
            cache_write_tokens=6,
            total_tokens=21,
        )
    )
    assert usage.cache_creation_tokens == 4
    assert usage.cache_read_tokens == 5
    assert usage.cache_write_tokens == 6


def test_evidence_is_immutable_and_round_trips() -> None:
    observation = ToolCallObservedObservation(
        **_common(tool="echo", arguments={"x": 1})
    )
    turn = TurnEvidence(sequence=1, status="completed", observations=(observation,))
    session = HarnessSessionEvidence(session_id="session", turns=(turn,))
    with pytest.raises((TypeError, AttributeError)):
        observation.arguments["x"] = 2  # type: ignore[index]
    restored = HarnessSessionEvidence.model_validate(session.model_dump(mode="json"))
    assert restored == session


def test_harness_result_and_snapshot_validate_typed_evidence_identity() -> None:
    observation = ToolCallObservedObservation(**_common(tool="echo"))
    turn = TurnEvidence(sequence=1, status="failed", observations=(observation,))
    with pytest.raises(ValueError):
        HarnessTurnResult(sequence=2, status="failed", turn_evidence=turn)
    result = HarnessTurnResult(sequence=1, status="failed", turn_evidence=turn)
    assert result.turn_evidence == turn
    session = HarnessSessionEvidence(session_id="session", turns=(turn,), closed=True)
    with pytest.raises(ValueError):
        HarnessSessionSnapshot(
            session_id="other",
            turns=1,
            server_configuration_count=0,
            closed=True,
            session_evidence=session,
        )
    snapshot = HarnessSessionSnapshot(
        session_id="session",
        turns=1,
        server_configuration_count=0,
        closed=True,
        session_evidence=session,
    )
    assert snapshot.session_evidence == session


def test_turn_and_session_evidence_reject_identity_order_and_limitations() -> None:
    first = ToolCallObservedObservation(**_common(observation_id="first", tool="echo"))
    duplicate = first.model_copy(update={"observation_id": "first"})
    with pytest.raises(ValueError, match="IDs"):
        TurnEvidence(
            sequence=1,
            status="completed",
            observations=(first, duplicate),
        )
    with pytest.raises(ValueError, match="turn sequence"):
        TurnEvidence(sequence=2, status="completed", observations=(first,))
    with pytest.raises(ValueError, match="unknown"):
        TurnEvidence(sequence=1, status="completed", limitations=("secret",))
    second = TurnEvidence(sequence=2, status="completed")
    with pytest.raises(ValueError, match="ascending"):
        HarnessSessionEvidence(
            session_id="session",
            turns=(second, TurnEvidence(sequence=1, status="completed")),
        )
    with pytest.raises(ValueError, match="unique"):
        HarnessSessionEvidence(session_id="session", turns=(second, second))
    with pytest.raises(ValueError, match="unknown"):
        HarnessSessionEvidence(session_id="session", limitations=("secret",))


def _sink(
    *, config: CaptureOptions | None = None
) -> tuple[InMemoryExecutionStore, ExecutionTraceRecorder, HarnessObservationSink]:
    store = InMemoryExecutionStore(capture_config=config)
    recorder = ExecutionTraceRecorder(store, "observation-test")
    return store, recorder, HarnessObservationSink(recorder, capture_config=config)


def test_sink_maps_variants_and_preserves_order_and_identity() -> None:
    store, recorder, sink = _sink()
    values = (
        MessageChunkObservation(**_common(observation_id="message", text="hello")),
        ReasoningChunkObservation(**_common(observation_id="reasoning", text="why")),
        ToolCallObservedObservation(
            **_common(
                observation_id="call", tool="echo", call_id="call-1", arguments={"x": 1}
            )
        ),
        ToolResultObservedObservation(
            **_common(observation_id="result", call_id="call-1", result={"content": []})
        ),
        ProcessObservedObservation(
            **_common(observation_id="process", phase="exited", exit_code=0)
        ),
        MetadataObservedObservation(
            **_common(observation_id="meta", name="model", value="fixture")
        ),
    )
    for value in values:
        sink.emit(value)
    events = recorder.events()
    assert [event.sequence for event in events] == list(range(len(events)))
    assert [event.payload["observation_id"] for event in events[1:]] == [
        value.observation_id for value in values
    ]
    assert all(event.provenance.source == "fixture" for event in events[1:])
    assert store.get_snapshot("observation-test") is not None


def test_sink_maps_all_remaining_variants_to_stable_kinds() -> None:
    _, recorder, sink = _sink()
    values = (
        RawFrameObservation(**_common(observation_id="raw", text="frame")),
        UsageObservedObservation(**_common(observation_id="usage", input_tokens=1)),
        PlanObservedObservation(**_common(observation_id="plan", plan={"step": 1})),
        StateObservedObservation(**_common(observation_id="state", state="running")),
        InteractionObservedObservation(
            **_common(
                observation_id="interaction", interaction_kind="permission.request"
            )
        ),
        MetadataObservedObservation(
            **_common(observation_id="metadata", name="model", value="fixture")
        ),
    )
    for value in values:
        sink.emit(value)
    assert [event.kind for event in recorder.events()[1:]] == [
        EventKind.PROVIDER_EVENT,
        EventKind.PROVIDER_EVENT,
        EventKind.PROVIDER_EVENT,
        EventKind.PROVIDER_EVENT,
        EventKind.PERMISSION_REQUEST,
        EventKind.PROVIDER_EVENT,
    ]


def test_sink_raw_evidence_is_bounded_redacted_and_readable() -> None:
    store, recorder, sink = _sink(
        config=CaptureOptions(raw_frame_bytes=8, raw_preview_bytes=8)
    )
    value = RawFrameObservation(
        **_common(
            observation_id="bounded-raw",
            raw_evidence=RawEvidenceInput(content="123456789", media_type="text/plain"),
        ),
        text="safe",
    )
    sink.emit(value)
    event = _event_for_observation(recorder, value.observation_id)
    assert event.raw_evidence_ref is not None
    assert event.raw_evidence_ref.sha256 is not None
    assert event.raw_evidence_ref.size_bytes == 8
    assert event.raw_evidence_ref.storage_key is not None
    raw = store.read_raw_evidence(event.raw_evidence_ref)
    assert raw.returned_size_bytes == 8
    assert raw.content == "12345678"
    assert sink.raw_references[value.observation_id] == event.raw_evidence_ref


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_raw_capture_metadata_and_source_timing_survive_projection_and_reopen(
    backend: str, tmp_path: Path
) -> None:
    config = CaptureOptions(raw_frame_bytes=4, raw_preview_bytes=3)
    if backend == "memory":
        store = InMemoryExecutionStore(capture_config=config)
    else:
        store = SQLiteExecutionStore(
            tmp_path / "capture-metadata.sqlite",
            blob_root=tmp_path / "capture-metadata-blobs",
            capture_config=config,
        )
    recorder = ExecutionTraceRecorder(store, f"capture-metadata-{backend}")
    sink = HarnessObservationSink(recorder, capture_config=config)
    sink.emit(
        RawFrameObservation(
            **_common(
                observation_id="raw-capture",
                monotonic_offset_ms=12.5,
                raw_evidence=RawEvidenceInput(
                    content="abcdef", media_type="text/plain"
                ),
                text="abcdef",
            )
        )
    )
    sink.emit(
        MessageChunkObservation(
            **_common(observation_id="timed-message", monotonic_offset_ms=12.5),
            text="hello",
        )
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    view = trace.view()
    raw = view.raw_messages[0]
    assert trace.completeness == "partial"
    assert "capture_incomplete" in trace.limitations
    assert raw.preview.state.value == "truncated"
    assert raw.preview.value == "abc"
    assert raw.evidence_ref is not None
    assert raw.size_bytes == 4
    message = next(entry for entry in view.timeline if entry.kind == "message")
    assert message.timing.started_at == NOW
    assert message.timing.start_offset_ms == 12.5
    if backend == "sqlite":
        reopened = SQLiteExecutionStore(
            tmp_path / "capture-metadata.sqlite",
            blob_root=tmp_path / "capture-metadata-blobs",
            capture_config=config,
        )
        reopened_trace = ExecutionTraceRecorder(
            reopened, recorder.execution_id
        ).finalize(ExecutionOutcome.COMPLETED)
        assert reopened_trace.view().raw_messages[0].preview.state.value == "truncated"
        reopened_message = next(
            entry for entry in reopened_trace.view().timeline if entry.kind == "message"
        )
        assert reopened_message.timing.started_at == NOW


def test_raw_evidence_uses_utf8_byte_cap() -> None:
    store, recorder, sink = _sink(
        config=CaptureOptions(raw_frame_bytes=4, raw_preview_bytes=4)
    )
    sink.emit(
        RawFrameObservation(
            **_common(
                raw_evidence=RawEvidenceInput(content="ééé", media_type="text/plain")
            )
        )
    )
    reference = _event_for_observation(recorder, "obs-1").raw_evidence_ref
    assert reference is not None and reference.size_bytes == 4
    assert store.read_raw_evidence(reference).content == "éé"


def test_raw_capture_disabled_is_policy_limitation_not_capture_failure() -> None:
    config = CaptureOptions(capture_raw_evidence=False)
    _, recorder, sink = _sink(config=config)
    sink.emit(
        RawFrameObservation(
            **_common(
                raw_evidence=RawEvidenceInput(content="frame", media_type="text/plain")
            )
        )
    )
    event = recorder.events()[-1]
    assert event.raw_evidence_ref is None
    assert sink.limitations == ("capture_disabled",)


@pytest.mark.parametrize("second_has_raw", [False, True])
def test_duplicate_observation_id_preserves_first_capture(
    second_has_raw: bool,
) -> None:
    store, recorder, sink = _sink()
    first = RawFrameObservation(
        **_common(
            observation_id="duplicate",
            raw_evidence=RawEvidenceInput(content="first", media_type="text/plain"),
        )
    )
    sink.emit(first)
    first_reference = recorder.events()[-1].raw_evidence_ref
    second_kwargs = _common(observation_id="duplicate", text="second")
    if second_has_raw:
        second_kwargs["raw_evidence"] = RawEvidenceInput(
            content="second", media_type="text/plain"
        )
    sink.emit(RawFrameObservation(**second_kwargs))
    assert "capture_incomplete" in sink.limitations
    assert sink.raw_references.get("duplicate") == first_reference
    assert first_reference is not None
    assert store.read_raw_evidence(first_reference).content == "first"


def test_sink_respects_provider_and_stderr_switches() -> None:
    config = CaptureOptions(capture_provider_messages=False, capture_stderr=False)
    _, recorder, sink = _sink(config=config)
    sink.emit(
        MessageChunkObservation(
            **_common(observation_id="hidden-message", text="hidden")
        )
    )
    sink.emit(
        ProcessObservedObservation(
            **_common(observation_id="stderr-disabled", phase="exited", stderr="secret")
        )
    )
    events = recorder.events()
    process_event = next(
        event for event in events if event.kind is EventKind.PROCESS_EXITED
    )
    assert "stderr" not in process_event.payload
    assert process_event.payload["stderr_state"] == "disabled"
    assert "capture_disabled" in sink.limitations
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    process = next(entry for entry in trace.view().timeline if entry.kind == "process")
    assert process.stderr.state.value == "unavailable"  # type: ignore[attr-defined]


def test_provider_filter_does_not_drop_raw_frames_or_structured_observations() -> None:
    config = CaptureOptions(capture_provider_messages=False, capture_raw_evidence=False)
    _, recorder, sink = _sink(config=config)
    sink.emit(RawFrameObservation(**_common(observation_id="raw-wire", text="wire")))
    sink.emit(
        UsageObservedObservation(**_common(observation_id="usage", input_tokens=1))
    )
    sink.emit(
        PlanObservedObservation(**_common(observation_id="plan", plan={"step": "one"}))
    )
    sink.emit(
        StateObservedObservation(
            **_common(observation_id="state", state="running", detail=None)
        )
    )
    sink.emit(
        MetadataObservedObservation(
            **_common(observation_id="metadata", name="model", value=None)
        )
    )
    events = recorder.events()
    provider_events = [
        event for event in events if event.kind is EventKind.PROVIDER_EVENT
    ]
    assert [event.kind for event in provider_events] == [
        EventKind.PROVIDER_EVENT,
        EventKind.PROVIDER_EVENT,
        EventKind.PROVIDER_EVENT,
        EventKind.PROVIDER_EVENT,
        EventKind.PROVIDER_EVENT,
    ]
    assert _event_for_observation(recorder, "metadata").payload["data"] is None
    assert "capture_disabled" not in sink.limitations


def test_structured_provider_observations_project_to_typed_entries() -> None:
    _, recorder, sink = _sink()
    sink.emit(
        UsageObservedObservation(
            **_common(observation_id="usage", input_tokens=1, cache_read_tokens=2)
        )
    )
    sink.emit(
        MetadataObservedObservation(
            **_common(observation_id="metadata", name="model", value=None)
        )
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    timeline = trace.view().timeline
    usage = next(entry for entry in timeline if entry.kind == "usage")
    assert usage.input_tokens.state.value == "observed"  # type: ignore[attr-defined]
    assert usage.cache_read_tokens.value == 2  # type: ignore[attr-defined]
    provider = next(entry for entry in timeline if entry.kind == "provider")
    assert provider.data.state.value == "observed"  # type: ignore[attr-defined]
    assert provider.data.value is None  # type: ignore[attr-defined]
    assert trace.view().summary.usage.value.cache_read_tokens.value == 2  # type: ignore[union-attr]


def test_reasoning_chunks_coalesce_only_with_same_block_or_adjacent_unidentified() -> (
    None
):
    _, recorder, sink = _sink()
    sink.emit(
        ReasoningChunkObservation(
            **_common(observation_id="observation-1", block_id="block-a", text="one")
        )
    )
    sink.emit(
        ReasoningChunkObservation(
            **_common(observation_id="observation-2", block_id="block-a", text="two")
        )
    )
    sink.emit(
        ReasoningChunkObservation(
            **_common(observation_id="observation-3", block_id="block-b", text="three")
        )
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    reasoning = [entry for entry in trace.view().timeline if entry.kind == "reasoning"]
    assert len(reasoning) == 2
    assert reasoning[0].block_id.value == "block-a"  # type: ignore[attr-defined]


def test_sink_bounds_redacts_text_and_derives_stderr_state() -> None:
    store = InMemoryExecutionStore(
        config=RedactionConfig(
            secrets=frozenset({"CANARY"}), include_environment=False
        ),
        capture_config=CaptureOptions(raw_preview_bytes=5),
    )
    recorder = ExecutionTraceRecorder(
        store,
        "bounded",
        redaction_config=RedactionConfig(
            secrets=frozenset({"CANARY"}), include_environment=False
        ),
    )
    sink = HarnessObservationSink(recorder)
    sink.emit(
        MessageChunkObservation(
            **_common(observation_id="bounded-message", text="CANARY-abcdef")
        )
    )
    sink.emit(
        ProcessObservedObservation(
            **_common(
                observation_id="bounded-process", phase="exited", stderr="CANARY-abcdef"
            )
        )
    )
    events = recorder.events()
    assert (
        _event_for_observation(recorder, "bounded-message").payload["content"][0][
            "text"
        ]
        == "[REDA"
    )
    assert _event_for_observation(recorder, "bounded-process").payload[
        "stderr_state"
    ] == ("truncated")
    assert "CANARY" not in repr(events)
    process = next(
        entry
        for entry in recorder.finalize(ExecutionOutcome.COMPLETED).view().timeline
        if entry.kind == "process"
    )
    assert process.stderr.state.value == "truncated"  # type: ignore[attr-defined]


def test_stderr_redaction_state_is_typed() -> None:
    config = RedactionConfig(
        secrets=frozenset({"stderr-secret"}), include_environment=False
    )
    store = InMemoryExecutionStore(config=config)
    recorder = ExecutionTraceRecorder(store, "stderr-redacted", redaction_config=config)
    HarnessObservationSink(recorder).emit(
        ProcessObservedObservation(**_common(phase="failed", stderr="stderr-secret"))
    )
    process = next(
        entry
        for entry in recorder.finalize(ExecutionOutcome.FAILED).view().timeline
        if entry.kind == "process"
    )
    assert process.stderr.state.value == "redacted"  # type: ignore[attr-defined]


def test_sink_redacts_json_urls_errors_stderr_and_raw_bytes() -> None:
    secret = "super-secret-token"
    config = RedactionConfig(
        secrets=frozenset({secret}),
        sensitive_keys=frozenset({"api_key", "authorization"}),
        include_environment=False,
    )
    store = InMemoryExecutionStore(config=config)
    recorder = ExecutionTraceRecorder(
        store, "redaction-observation", redaction_config=config
    )
    sink = HarnessObservationSink(recorder)
    sink.emit(
        MetadataObservedObservation(
            **_common(
                observation_id="json-secret",
                name="request",
                value={
                    "api_key": secret,
                    "url": f"https://example.test/?token={secret}",
                    "safe": "value",
                },
            )
        )
    )
    sink.emit(
        ProcessObservedObservation(
            **_common(
                observation_id="stderr-secret",
                phase="failed",
                stderr=f"Authorization: Bearer {secret}",
            )
        )
    )
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="error-secret",
                call_id="call-secret",
                error_message=f"failed with {secret}",
            )
        )
    )
    sink.emit(
        RawFrameObservation(
            **_common(
                observation_id="raw-secret",
                raw_evidence=RawEvidenceInput(
                    content=f"raw={secret}", media_type="text/plain"
                ),
            )
        )
    )
    trace_text = repr(recorder.events())
    assert secret not in trace_text
    assert secret not in repr(
        store.read_raw_evidence(recorder.events()[-1].raw_evidence_ref)
    )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "state"),
    [
        ({"stderr": ""}, "observed"),
        ({"stderr": None}, "unavailable"),
        ({"stderr_state": "unavailable"}, "unavailable"),
        ({"stderr_state": "redacted"}, "not_emitted"),
        ({"stderr_state": "truncated"}, "not_emitted"),
    ],
)
def test_stderr_states_are_lossless_without_trusting_claims(
    kwargs: dict[str, object], state: str
) -> None:
    _, recorder, sink = _sink()
    sink.emit(ProcessObservedObservation(**_common(phase="exited", **kwargs)))
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    process = next(entry for entry in trace.view().timeline if entry.kind == "process")
    assert process.stderr.state.value == state  # type: ignore[attr-defined]


def test_sink_limitations_are_merged_into_terminal_trace() -> None:
    _, recorder, sink = _sink(config=CaptureOptions(capture_provider_messages=False))
    sink.emit(MessageChunkObservation(**_common(text="not captured")))
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    assert trace.completeness == "partial"
    assert "capture_disabled" in trace.limitations


@pytest.mark.parametrize("outcome", list(ExecutionOutcome))
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_sink_limitations_and_observations_survive_reopen(
    tmp_path: Path, outcome: ExecutionOutcome, backend: str
) -> None:
    config = CaptureOptions(capture_provider_messages=False)
    if backend == "memory":
        store = InMemoryExecutionStore(capture_config=config)
        reopened = store
    else:
        database = tmp_path / f"reopen-{outcome.value}.sqlite"
        blob_root = tmp_path / f"reopen-{outcome.value}-blobs"
        store = SQLiteExecutionStore(
            database, blob_root=blob_root, capture_config=config
        )
        reopened = SQLiteExecutionStore(
            database, blob_root=blob_root, capture_config=config
        )
    first = ExecutionTraceRecorder(store, f"reopen-{backend}-{outcome.value}")
    HarnessObservationSink(first, capture_config=config).emit(
        MessageChunkObservation(**_common(text="not captured"))
    )
    second = ExecutionTraceRecorder(reopened, first.execution_id)
    trace = second.finalize(outcome)
    assert trace.completeness == "partial"
    assert trace.limitations == ("capture_disabled",)
    assert any(event.kind is EventKind.DIAGNOSTIC for event in trace.events)


def test_sink_reported_calls_are_typed_and_matchable() -> None:
    _, recorder, sink = _sink()
    sink.emit(
        ToolCallObservedObservation(
            **_common(
                observation_id="reported-call",
                tool="echo",
                server="fixture",
                call_id="call-1",
                arguments={"x": 1},
            )
        )
    )
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="reported-result",
                call_id="call-1",
                result={"content": []},
                status="success",
            )
        )
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    view = trace.view()
    expect(view).to_have_tool_call(
        name="echo", server="fixture", arguments={"x": 1}, evidence="reported"
    )
    assert view.timeline[1].correlation.value == "reported_only"  # type: ignore[attr-defined]


def test_sink_never_raises_for_malformed_input_or_terminal_recorder() -> None:
    _, recorder, sink = _sink()
    sink.emit({"kind": "not-an-observation"})  # type: ignore[arg-type]
    recorder.finalize(ExecutionOutcome.FAILED)
    sink.emit(ToolCallObservedObservation(**_common(tool="echo")))
    assert "persistence_failed" in sink.limitations


def test_sink_sqlite_raw_evidence_reopens(tmp_path: Path) -> None:
    database = tmp_path / "observation.sqlite"
    blob_root = tmp_path / "observation-blobs"
    store = SQLiteExecutionStore(database, blob_root=blob_root)
    recorder = ExecutionTraceRecorder(store, "observation-sqlite")
    sink = HarnessObservationSink(recorder)
    value = RawFrameObservation(
        **_common(
            raw_evidence=RawEvidenceInput(content="frame", media_type="text/plain")
        ),
    )
    sink.emit(value)
    event = _event_for_observation(recorder, value.observation_id)
    assert event.raw_evidence_ref is not None
    reopened = SQLiteExecutionStore(database, blob_root=blob_root)
    assert event.raw_evidence_ref.sha256 is not None
    assert event.raw_evidence_ref.size_bytes == 5
    assert event.raw_evidence_ref.storage_key is not None
    assert reopened.read_raw_evidence(event.raw_evidence_ref).content == "frame"


def test_atomic_raw_failure_keeps_contiguous_events_and_no_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, recorder, sink = _sink()

    original_commit = store._commit_checked
    failed = False

    def fail_once(*args: object, **kwargs: object) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected failure")
        original_commit(*args, **kwargs)

    monkeypatch.setattr(store, "_commit_checked", fail_once)
    sink.emit(
        RawFrameObservation(
            **_common(
                observation_id="failed-raw",
                raw_evidence=RawEvidenceInput(
                    content="failed", media_type="text/plain"
                ),
            )
        )
    )
    assert not sink.raw_references
    events = recorder.events()
    assert [event.sequence for event in events] == list(range(len(events)))
    assert any(event.kind is EventKind.DIAGNOSTIC for event in events)
    monkeypatch.undo()
    sink.emit(
        MetadataObservedObservation(
            **_common(observation_id="next", name="next", value="ok")
        )
    )
    events = recorder.events()
    assert [event.sequence for event in events] == list(range(len(events)))
    assert _event_for_observation(recorder, "next").raw_evidence_ref is None


def test_memory_duplicate_raw_event_preserves_existing_reference() -> None:
    store, recorder, sink = _sink()
    sink.emit(
        RawFrameObservation(
            **_common(
                raw_evidence=RawEvidenceInput(
                    content="original", media_type="text/plain"
                )
            )
        )
    )
    committed = recorder.events()[-1]
    reference = committed.raw_evidence_ref
    assert reference is not None
    duplicate = committed.model_copy(
        update={"sequence": committed.sequence + 1, "raw_evidence_ref": None}
    )
    with pytest.raises(StorageConflict):
        store.append_event(duplicate, b"replacement", media_type="text/plain")
    assert store.read_raw_evidence(reference).content == "original"
    sink.emit(
        MetadataObservedObservation(
            **_common(observation_id="next", name="ok", value=True)
        )
    )
    assert recorder.events()[-1].sequence == committed.sequence + 1


def test_sqlite_atomic_raw_metadata_failure_is_transactional(tmp_path: Path) -> None:
    database = tmp_path / "metadata-failure.sqlite"
    blob_root = tmp_path / "metadata-blobs"
    store = SQLiteExecutionStore(database, blob_root=blob_root)
    recorder = ExecutionTraceRecorder(store, "metadata-failure")
    sink = HarnessObservationSink(recorder)
    sink.emit(
        RawFrameObservation(
            **_common(
                raw_evidence=RawEvidenceInput(
                    content="original", media_type="text/plain"
                )
            )
        )
    )
    first = recorder.events()[-1]
    reference = first.raw_evidence_ref
    assert reference is not None and reference.sha256 is not None
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE v2_blobs SET size_bytes=? WHERE sha256=?",
            (999, reference.sha256),
        )
    replacement = first.model_copy(
        update={
            "event_id": EventId("replacement-event"),
            "sequence": first.sequence + 1,
            "raw_evidence_ref": None,
        }
    )
    with pytest.raises(RawEvidenceIntegrityError):
        store.append_event(replacement, b"original", media_type="text/plain")
    assert len(recorder.events()) == first.sequence + 1
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE v2_blobs SET size_bytes=? WHERE sha256=?",
            (reference.size_bytes, reference.sha256),
        )
    store.append_event(replacement, b"original", media_type="text/plain")
    assert recorder.events()[-1].sequence == replacement.sequence


def test_atomic_raw_callbacks_observe_complete_committed_reference() -> None:
    store, _recorder, sink = _sink()
    observed: list[object] = []
    store.subscribe("observation-test", observed.append)
    sink.emit(
        RawFrameObservation(
            **_common(
                raw_evidence=RawEvidenceInput(content="frame", media_type="text/plain")
            )
        )
    )
    event = observed[-1]
    assert event.raw_evidence_ref is not None
    reference = event.raw_evidence_ref
    assert reference.sha256 and reference.size_bytes == 5 and reference.storage_key
    assert store.read_raw_evidence(reference).content == "frame"


def test_sqlite_atomic_raw_failure_does_not_leave_blob_or_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "failed.sqlite"
    blob_root = tmp_path / "failed-blobs"
    store = SQLiteExecutionStore(database, blob_root=blob_root)
    recorder = ExecutionTraceRecorder(store, "failed-observation")
    sink = HarnessObservationSink(recorder)

    original_append = store._append
    failed = False

    def fail_once(*args: object, **kwargs: object) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected append failure")
        original_append(*args, **kwargs)

    monkeypatch.setattr(store, "_append", fail_once)
    sink.emit(
        RawFrameObservation(
            **_common(
                observation_id="failed-raw",
                raw_evidence=RawEvidenceInput(
                    content="failed", media_type="text/plain"
                ),
            )
        )
    )
    sink.emit(
        MetadataObservedObservation(
            **_common(observation_id="next", name="next", value="ok")
        )
    )
    events = recorder.events()
    assert [event.sequence for event in events] == list(range(len(events)))
    assert any(event.kind is EventKind.DIAGNOSTIC for event in events)
    assert not tuple(blob_root.glob("**/*.gz"))


def test_explicit_chunk_identity_is_turn_and_role_scoped_across_interleaving() -> None:
    _, recorder, sink = _sink()
    sink.emit(
        MessageChunkObservation(
            **_common(observation_id="m1", message_id="message-a", text="a1")
        )
    )
    sink.emit(
        MessageChunkObservation(
            **_common(observation_id="m2", message_id="message-b", text="b")
        )
    )
    sink.emit(
        MessageChunkObservation(
            **_common(observation_id="m3", message_id="message-a", text="a2")
        )
    )
    sink.emit(
        MessageChunkObservation(
            **_common(
                observation_id="m4",
                turn_sequence=2,
                message_id="message-a",
                text="a3",
            )
        )
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    messages = [entry for entry in trace.view().timeline if entry.kind == "message"]
    assert len(messages) == 3
    assert [block.text for block in messages[0].content] == ["a1", "a2"]  # type: ignore[attr-defined]
    assert messages[0].turn_id != messages[-1].turn_id


def test_reported_and_wire_calls_correlate_once_with_wire_authority() -> None:
    _, recorder, sink = _sink()
    wire_provenance = EventSource(origin=EventOrigin.WIRE_OBSERVED, source="direct")
    recorder.emit(
        EventKind.MCP_REQUEST,
        turn_id="turn-2",
        server_binding="fixture",
        connection_id="connection",
        correlation=RequestLink(
            jsonrpc_id=7,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=3,
        ),
        payload={
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"x": 1}},
            "call_id": "shared",
        },
        provenance=wire_provenance,
    )
    recorder.emit(
        EventKind.MCP_RESPONSE,
        turn_id="turn-2",
        server_binding="fixture",
        connection_id="connection",
        correlation=RequestLink(
            jsonrpc_id=7,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=3,
        ),
        payload={
            "method": "tools/call",
            "result": {"content": [{"type": "text", "text": "wire"}]},
        },
        provenance=wire_provenance,
    )
    sink.emit(
        ToolCallObservedObservation(
            **_common(
                observation_id="correlated-call",
                tool="echo",
                server="fixture",
                call_id="shared",
                arguments={"x": 1},
            )
        )
    )
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="correlated-result",
                call_id="shared",
                result={"content": [{"type": "text", "text": "wire"}]},
                is_error=False,
            )
        )
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    calls = trace.view().tool_calls
    assert len(calls) == 1
    assert calls[0].correlation.value == "correlated"
    assert calls[0].wire.state.value == "observed"
    assert calls[0].reported.state.value == "observed"
    assert calls[0].conflicts == ()
    assert calls[0].result.value.content[0].text == "wire"  # type: ignore[union-attr]


def test_reported_error_without_result_preserves_explicit_tool_error() -> None:
    _, recorder, sink = _sink()
    sink.emit(
        ToolCallObservedObservation(
            **_common(observation_id="error-call", tool="echo", call_id="error-1")
        )
    )
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="error-result",
                call_id="error-1",
                status="tool_error",
                error_message="tool failed",
            )
        )
    )
    call = recorder.finalize(ExecutionOutcome.FAILED).view().tool_calls[0]
    assert call.tool_status.value == "tool_error"
    assert call.correlation.value == "reported_only"


@pytest.mark.parametrize("key", ["isError", "is_error"])
def test_reported_malformed_error_flag_is_incomplete_not_success(key: str) -> None:
    _, recorder, sink = _sink()
    sink.emit(
        ToolCallObservedObservation(
            **_common(observation_id="bad-flag-call", tool="echo", call_id="bad-flag")
        )
    )
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="bad-flag-result",
                call_id="bad-flag",
                result={"content": [], key: "false"},
            )
        )
    )
    call = recorder.finalize(ExecutionOutcome.COMPLETED).view().tool_calls[0]
    assert call.tool_status.value == "incomplete"


def test_correlated_sources_retain_field_conflicts_with_wire_authority() -> None:
    _, recorder, sink = _sink()
    provenance = EventSource(origin=EventOrigin.WIRE_OBSERVED, source="direct")
    recorder.emit(
        EventKind.MCP_REQUEST,
        turn_id="turn-1",
        server_binding="wire-server",
        connection_id="connection",
        correlation=RequestLink(
            jsonrpc_id=8,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=1,
        ),
        payload={
            "method": "tools/call",
            "params": {"name": "wire-tool", "arguments": {"wire": True}},
            "call_id": "shared-conflict",
        },
        provenance=provenance,
    )
    recorder.emit(
        EventKind.MCP_RESPONSE,
        turn_id="turn-1",
        server_binding="wire-server",
        connection_id="connection",
        correlation=RequestLink(
            jsonrpc_id=8,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=1,
        ),
        payload={
            "method": "tools/call",
            "result": {"type": "text", "content": [{"type": "text", "text": "wire"}]},
        },
        provenance=provenance,
    )
    sink.emit(
        ToolCallObservedObservation(
            **_common(
                observation_id="conflict-call",
                tool="reported-tool",
                server="reported-server",
                call_id="shared-conflict",
                arguments={"reported": True},
                status="tool_error",
            )
        )
    )
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="conflict-result",
                call_id="shared-conflict",
                result={"content": [{"type": "text", "text": "reported"}]},
                status="tool_error",
            )
        )
    )
    call = recorder.finalize(ExecutionOutcome.FAILED).view().tool_calls[0]
    assert {conflict.field for conflict in call.conflicts} == {
        "server",
        "tool",
        "arguments",
        "result",
        "status",
    }
    assert call.tool.value == "wire-tool"
    assert call.tool_status.value == "success"


def test_reported_missing_duplicate_and_out_of_order_ids_stay_separate() -> None:
    _, recorder, sink = _sink()
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="late-result", call_id="late", result={"content": []}
            )
        )
    )
    sink.emit(
        ToolCallObservedObservation(
            **_common(observation_id="dup-call-1", tool="echo", call_id="dup")
        )
    )
    sink.emit(
        ToolCallObservedObservation(
            **_common(observation_id="dup-call-2", tool="echo", call_id="dup")
        )
    )
    sink.emit(
        ToolResultObservedObservation(
            **_common(
                observation_id="dup-result", call_id="dup", result={"content": []}
            )
        )
    )
    trace = recorder.finalize(ExecutionOutcome.FAILED)
    assert len(trace.view().tool_calls) == 2
    assert all(
        call.correlation.value == "reported_only" for call in trace.view().tool_calls
    )
    assert any(entry.kind == "diagnostic" for entry in trace.view().timeline)
