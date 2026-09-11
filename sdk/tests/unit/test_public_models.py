"""Phase 2 public-surface and value-model contract tests."""

from __future__ import annotations

import importlib
import inspect
import pickle
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

import mcp_pal
from mcp_pal._exports import _INTERNAL_MODULES, PUBLIC_EXPORTS
from mcp_pal.errors import InvalidTransitionError, ModelValidationError
from mcp_pal.types import (
    ACPAgent,
    AgentSpec,
    ArtifactId,
    ArtifactRef,
    AudioContent,
    Capability,
    CapabilityStatus,
    ClaudeCode,
    ConnectionId,
    ContentBlock,
    DirectSpec,
    ElicitationPolicy,
    ErrorCode,
    ErrorInfo,
    EvaluationContext,
    EvaluationId,
    EvaluationRegistration,
    EvaluationResult,
    EvaluationStatus,
    Event,
    EventId,
    ExecutionId,
    ExecutionOutcome,
    ExecutionResult,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
    FileContent,
    FilesystemPolicy,
    FullToolPolicy,
    HarnessId,
    HarnessProfileId,
    HarnessProfileRef,
    HarnessSpec,
    HTTPServer,
    ImageContent,
    InProcessServer,
    Metadata,
    NativeToolPolicy,
    OpaqueContent,
    OpenCode,
    PermissionPolicy,
    Ping,
    ProtocolConstraint,
    Readiness,
    ResourceLink,
    RestrictiveToolPolicy,
    RevisionId,
    RevisionSelection,
    SamplingPolicy,
    SecretReference,
    ServerBinding,
    ServerId,
    ServerProfileId,
    ServerProfileRef,
    ServerValue,
    SessionId,
    SSEServer,
    StdioServer,
    TerminalPolicy,
    TextContent,
    ToolPolicy,
    TraceId,
    TraceResult,
    TurnId,
    TurnOutcome,
    TurnResponse,
    TurnResult,
    TurnState,
    TurnStatus,
    UserMessage,
    WorkspaceKind,
    WorkspacePolicy,
)


class Mutable:
    pass


def test_public_modules_import_without_application_layers() -> None:
    for module_name in (
        "mcp_pal",
        "mcp_pal.sync_api",
        "mcp_pal.async_api",
        "mcp_pal.types",
        "mcp_pal.matchers",
        "mcp_pal.testing",
        "mcp_pal.pytest_plugin",
    ):
        module = importlib.import_module(module_name)
        assert module.__name__ == module_name


def test_phase4_implementation_modules_are_explicitly_internal() -> None:
    assert _INTERNAL_MODULES == (
        "mcp_pal.events",
        "mcp_pal.execution_trace",
        "mcp_pal.storage",
        "mcp_pal.trace.redaction",
    )
    assert not set(_INTERNAL_MODULES).intersection(PUBLIC_EXPORTS)


@pytest.mark.parametrize(
    "module_name",
    tuple(
        name
        for name in PUBLIC_EXPORTS
        if name not in {"mcp_pal", "mcp_pal.types", "mcp_pal.errors"}
    ),
)
def test_boundary_exports_match_manifest_without_accidental_names(
    module_name: str,
) -> None:
    module = importlib.import_module(module_name)
    assert tuple(module.__all__) == PUBLIC_EXPORTS[module_name]
    public_names = {
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and name != "annotations"
        and not inspect.ismodule(value)
    }
    assert public_names == set(module.__all__)


def test_root_exports_match_manifest_without_accidental_public_names() -> None:
    assert tuple(mcp_pal.__all__) == PUBLIC_EXPORTS["mcp_pal"]
    public_names = {
        name
        for name, value in vars(mcp_pal).items()
        if (not name.startswith("_") or name == "__version__")
        and not inspect.ismodule(value)
    }
    assert public_names == set(mcp_pal.__all__)


def test_types_manifest_contains_only_public_model_or_alias_names() -> None:
    types_module = importlib.import_module("mcp_pal.types")
    assert tuple(types_module.__all__) == PUBLIC_EXPORTS["mcp_pal.types"]
    for name in types_module.__all__:
        assert hasattr(types_module, name)


@pytest.mark.parametrize("module_name", ("mcp_pal.types", "mcp_pal.errors"))
def test_every_manifest_model_has_json_schema(module_name: str) -> None:
    module = importlib.import_module(module_name)
    for name in PUBLIC_EXPORTS[module_name]:
        value = getattr(module, name)
        if inspect.isclass(value) and issubclass(value, BaseModel):
            assert value.model_json_schema()


@pytest.mark.parametrize(
    "alias_name",
    ("ContentBlock", "ExecutionSpec", "HarnessSpec", "ServerValue", "ToolPolicy"),
)
def test_every_public_serializable_alias_has_json_schema(alias_name: str) -> None:
    alias = getattr(importlib.import_module("mcp_pal.types"), alias_name)
    assert TypeAdapter(alias).json_schema()


def test_spec_and_content_json_round_trip() -> None:
    spec = AgentSpec(
        harness=ClaudeCode(name="claude", model="claude-test"),
        servers=(ServerBinding(server=StdioServer(name="echo", command="echo")),),
        message=UserMessage(content=(TextContent(text="hello"),)),
    )
    restored = AgentSpec.model_validate(spec.model_dump(mode="json"))
    assert restored == spec

    event = Event(
        event_id="event-1",
        execution_id="execution-1",
        sequence=0,
        kind="execution.created",
        monotonic_offset_ms=0,
        timestamp=datetime.now(timezone.utc),
    )
    restored_event = Event.model_validate(event.model_dump(mode="json", by_alias=True))
    assert restored_event == event


def test_all_serializable_model_representatives_round_trip() -> None:
    execution = ExecutionId("execution-1")
    session = SessionId("session-1")
    turn_id = TurnId("turn-1")
    server = StdioServer(name="echo", command="echo")
    trace = TraceResult(trace_id=TraceId("trace-1"), execution_id=execution)
    artifact = ArtifactRef(
        artifact_id=ArtifactId("artifact-1"),
        execution_id=execution,
        name="trace.json",
        size_bytes=1,
        sha256="0" * 64,
    )
    values: tuple[BaseModel, ...] = (
        ExecutionId("execution-2"),
        SessionId("session-2"),
        TurnId("turn-2"),
        ServerId("server-1"),
        ServerProfileId("server-profile-1"),
        HarnessId("harness-1"),
        HarnessProfileId("harness-profile-1"),
        RevisionId("revision-1"),
        ConnectionId("connection-1"),
        EventId("event-1"),
        ArtifactId("artifact-2"),
        TraceId("trace-2"),
        EvaluationId("evaluation-1"),
        Metadata(name="example", labels={"team": "sdk"}),
        SecretReference(source="environment", name="TOKEN"),
        ErrorInfo(code=ErrorCode.INVALID_ARGUMENT, message="bad input"),
        ProtocolConstraint(revision="2025-06-18"),
        TextContent(text="hello"),
        FileContent(path="input.txt"),
        ImageContent(media_type="image/png", data="aGVsbG8="),
        AudioContent(media_type="audio/wav", uri="https://example.test/audio"),
        ResourceLink(uri="https://example.test/resource"),
        OpaqueContent(provider="example", payload={"value": 1}),
        UserMessage(content=(TextContent(text="hello"),)),
        server,
        HTTPServer(name="http", url="https://example.test/mcp"),
        SSEServer(name="sse", url="https://example.test/sse"),
        ServerBinding(server=server),
        ServerProfileRef(
            profile_id=ServerProfileId("server-profile-2"),
            revision=RevisionSelection(
                mode="pinned", revision_id=RevisionId("revision-2"), revision_number=1
            ),
        ),
        ClaudeCode(model="claude-test"),
        OpenCode(model="opencode-test"),
        ACPAgent(model="acp-test"),
        HarnessProfileRef(
            profile_id=HarnessProfileId("harness-profile-2"),
            revision=RevisionSelection(mode="latest"),
        ),
        WorkspacePolicy(),
        RestrictiveToolPolicy(),
        FullToolPolicy(acknowledge_risk=True),
        NativeToolPolicy(
            harness="example",
            policy={"mode": "safe"},
            nonportable_reason="provider-specific",
        ),
        PermissionPolicy(),
        ElicitationPolicy(),
        SamplingPolicy(),
        FilesystemPolicy(),
        TerminalPolicy(),
        EvaluationRegistration(name="quality"),
        DirectSpec(servers=(ServerBinding(server=server),), operation=Ping()),
        AgentSpec(
            servers=(
                ServerBinding(
                    profile=ServerProfileRef(
                        profile_id=ServerProfileId("server-profile-3"),
                        revision=RevisionSelection(mode="latest"),
                    )
                ),
            ),
            harness_profile=HarnessProfileRef(
                profile_id=HarnessProfileId("harness-profile-3"),
                revision=RevisionSelection(
                    mode="pinned",
                    revision_id=RevisionId("revision-4"),
                    revision_number=2,
                ),
            ),
            message=UserMessage(content="hello"),
        ),
        ExecutionState(execution_id=execution),
        TurnState(turn_id=turn_id, session_id=session, number=1),
        Event(
            event_id=EventId("event-2"),
            execution_id=execution,
            sequence=0,
            kind="execution.created",
            monotonic_offset_ms=0,
        ),
        trace,
        artifact,
        EvaluationContext(execution_id=execution, trace=trace, artifacts=(artifact,)),
        EvaluationResult(
            evaluation_id=EvaluationId("evaluation-2"),
            name="quality",
            status=EvaluationStatus.NOT_RUN,
        ),
        Capability(name="stdio", status=CapabilityStatus.READY),
        Readiness(ready=True),
        TurnResponse(content=(TextContent(text="done"),)),
        TurnResult(
            snapshot=TurnState(
                turn_id=turn_id, session_id=session, number=1
            ).transition(TurnStatus.FINISHED, TurnOutcome.COMPLETED)
        ),
        ExecutionResult(
            snapshot=ExecutionState(execution_id=execution).transition(
                ExecutionStatus.FINISHED, ExecutionOutcome.COMPLETED
            )
        ),
    )
    for value in values:
        restored = type(value).model_validate(
            value.model_dump(mode="json", by_alias=True)
        )
        assert restored == value, type(value).__name__


def test_turn_result_exposes_its_selector_id_without_a_trace_view() -> None:
    turn = TurnState(turn_id="turn-selector", session_id="session-1", number=1)
    result = TurnResult(
        snapshot=turn.transition(TurnStatus.FINISHED, TurnOutcome.COMPLETED)
    )
    assert result.turn_id == TurnId("turn-selector")
    assert not hasattr(result, "trace_view")


def test_artifact_refs_cannot_claim_unredacted_state() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="artifact-unsafe",
            execution_id="execution-1",
            name="trace.bin",
            size_bytes=0,
            sha256="0" * 64,
            redacted=False,
        )

    artifact = ArtifactRef(
        artifact_id="artifact-safe",
        execution_id="execution-1",
        name="trace.bin",
        size_bytes=0,
        sha256="0" * 64,
    )
    assert artifact.redacted is True


def test_discriminated_public_aliases_round_trip() -> None:
    server = StdioServer(name="echo", command="echo")
    cases = (
        (ContentBlock, TextContent(text="hello")),
        (ServerValue, server),
        (HarnessSpec, ClaudeCode(model="claude-test")),
        (ToolPolicy, FullToolPolicy(acknowledge_risk=True)),
        (
            ExecutionSpec,
            DirectSpec(servers=(ServerBinding(server=server),), operation=Ping()),
        ),
    )
    for annotation, value in cases:
        adapter = TypeAdapter(annotation)
        restored = adapter.validate_python(adapter.dump_python(value, mode="json"))
        assert restored == value


def test_models_are_frozen_and_secret_references_have_no_value_field() -> None:
    reference = SecretReference(source="environment", name="TEAM_TOKEN")
    assert "value" not in reference.model_dump()
    with pytest.raises(ValidationError):
        reference.name = "OTHER"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        FullToolPolicy()
    assert (
        FullToolPolicy(acknowledge_risk=True).model_dump(mode="json")[
            "acknowledge_risk"
        ]
        is True
    )
    updated = ClaudeCode(model="before").with_model("after")
    assert isinstance(updated, ClaudeCode)
    assert updated.model == "after"


def test_nested_payloads_are_copied_and_deeply_immutable() -> None:
    source = {"nested": {"items": [{"value": 1}], "flags": {3, 1}}}
    payload = OpaqueContent(provider="fixture", payload=source)
    source["nested"]["items"][0]["value"] = 99
    source["nested"]["flags"].add(5)
    assert payload.payload["nested"]["items"][0]["value"] == 1
    assert 5 not in payload.payload["nested"]["flags"]
    with pytest.raises(TypeError):
        payload.payload["nested"]["items"][0]["value"] = 4
    with pytest.raises(TypeError):
        dict.__setitem__(payload.payload, "bypass", "blocked")
    with pytest.raises(AttributeError):
        payload.payload["nested"]["flags"].add(5)
    restored = OpaqueContent.model_validate(payload.model_dump(mode="json"))
    assert restored == payload


def test_secret_references_remain_references_inside_arbitrary_payloads() -> None:
    source = {"credential": SecretReference(source="environment", name="TOKEN")}
    payload = OpaqueContent(provider="fixture", payload=source)
    assert "value" not in payload.model_dump(mode="json")
    assert payload.payload["credential"].name == "TOKEN"
    with pytest.raises(ValidationError):
        SecretReference(source="environment", name="TOKEN", value="resolved")
    with pytest.raises(TypeError):
        payload.payload["credential"]["resolved"] = "do-not-store"


def test_non_json_arbitrary_values_are_rejected_at_construction() -> None:
    builders = (
        lambda: OpaqueContent(provider="fixture", payload={"bad": Mutable()}),
        lambda: ErrorInfo(
            code=ErrorCode.INVALID_ARGUMENT, message="bad", details={"bad": Mutable()}
        ),
        lambda: UserMessage(content="hello", metadata={"bad": Mutable()}),
        lambda: TurnResponse(metadata={"bad": Mutable()}),
        lambda: Event(
            event_id="event-1",
            execution_id="execution-1",
            sequence=0,
            kind="test",
            monotonic_offset_ms=0,
            payload={"bad": Mutable()},
        ),
        lambda: ACPAgent(model="acp", manifest={"bad": Mutable()}),
        lambda: NativeToolPolicy(
            harness="example", policy={"bad": Mutable()}, nonportable_reason="test"
        ),
        lambda: InProcessServer(
            name="local", factory=lambda: object(), descriptor={"bad": Mutable()}
        ),
    )
    for build in builders:
        with pytest.raises(ValidationError):
            build()


def test_revision_selection_and_profile_bindings_are_explicit_and_serializable() -> (
    None
):
    latest = RevisionSelection(mode="latest")
    pinned = RevisionSelection(
        mode="pinned", revision_id="revision-1", revision_number=1
    )
    assert latest.model_validate(latest.model_dump(mode="json")) == latest
    assert pinned.model_validate(pinned.model_dump(mode="json")) == pinned
    with pytest.raises(ValidationError):
        RevisionSelection(mode="pinned", revision_id="revision-1")
    with pytest.raises(ValidationError):
        RevisionSelection(mode="latest", revision_id="revision-1", revision_number=1)

    spec = DirectSpec(
        servers=(
            ServerBinding(
                profile=ServerProfileRef(profile_id="server-profile", revision=pinned)
            ),
        ),
        operation=Ping(),
    )
    assert DirectSpec.model_validate(spec.model_dump(mode="json")) == spec
    effective = DirectSpec(
        servers=(
            ServerBinding(
                profile=ServerProfileRef(profile_id="server-profile", revision=pinned)
            ),
        ),
        operation=Ping(),
    )
    assert effective.servers[0].profile is not None
    assert effective.servers[0].profile.revision.mode == "pinned"


def test_specs_reject_runtime_factories_and_missing_default_bindings() -> None:
    with pytest.raises(ValidationError):
        DirectSpec()
    with pytest.raises(ValidationError):
        DirectSpec(
            servers=(
                ServerBinding(
                    server=InProcessServer(name="local", factory=lambda: object())
                ),
            ),
            operation=Ping(),
        )
    optional = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="unused", command="echo"), required=False
            ),
        ),
        operation=Ping(),
    )
    assert optional.servers[0].required is False


def test_transitions_reject_nonterminal_outcomes_and_revalidate() -> None:
    execution = ExecutionState(execution_id="execution-1")
    with pytest.raises(ModelValidationError):
        execution.transition(ExecutionStatus.QUEUED, ExecutionOutcome.FAILED)
    queued = execution.transition(ExecutionStatus.QUEUED)
    with pytest.raises(ModelValidationError):
        queued.transition(ExecutionStatus.STARTING, ExecutionOutcome.FAILED)

    turn = TurnState(turn_id="turn-1", session_id="session-1", number=1)
    with pytest.raises(ModelValidationError):
        turn.transition(TurnStatus.RUNNING, TurnOutcome.FAILED)
    running = turn.transition(TurnStatus.RUNNING)
    with pytest.raises(ModelValidationError):
        running.transition(TurnStatus.FINISHED)


def test_results_require_terminal_snapshots() -> None:
    with pytest.raises(ValidationError):
        TurnResult(
            snapshot=TurnState(turn_id="turn-1", session_id="session-1", number=1)
        )
    with pytest.raises(ValidationError):
        ExecutionResult(snapshot=ExecutionState(execution_id="execution-1"))


def test_snapshot_finished_at_matches_lifecycle() -> None:
    with pytest.raises(ValidationError):
        ExecutionState(
            lifecycle=ExecutionStatus.FINISHED,
            outcome=ExecutionOutcome.COMPLETED,
            execution_id="execution-1",
        )
    with pytest.raises(ValidationError):
        ExecutionState(
            execution_id="execution-1", finished_at=datetime.now(timezone.utc)
        )
    with pytest.raises(ValidationError):
        TurnState(
            lifecycle=TurnStatus.FINISHED,
            outcome=TurnOutcome.COMPLETED,
            turn_id="turn-1",
            session_id="session-1",
            number=1,
        )
    with pytest.raises(ValidationError):
        TurnState(
            turn_id="turn-1",
            session_id="session-1",
            number=1,
            finished_at=datetime.now(timezone.utc),
        )


def test_workspace_risk_and_execution_transitions_are_validated() -> None:
    with pytest.raises(ValidationError):
        WorkspacePolicy(kind=WorkspaceKind.IN_PLACE)
    assert WorkspacePolicy(kind=WorkspaceKind.IN_PLACE, acknowledge_risk=True)

    snapshot = ExecutionState(execution_id="execution-1")
    queued = snapshot.transition("queued")
    finished = queued.transition("finished", ExecutionOutcome.CANCELLED)
    assert finished.outcome is ExecutionOutcome.CANCELLED
    with pytest.raises(InvalidTransitionError):
        finished.transition("queued")

    turn = TurnState(turn_id="turn-1", session_id="session-1", number=1)
    finished_turn = turn.transition(TurnStatus.RUNNING).transition(
        TurnStatus.FINISHED, TurnOutcome.COMPLETED
    )
    assert finished_turn.outcome is TurnOutcome.COMPLETED


def test_runtime_only_in_process_factory_is_excluded_from_serialization() -> None:
    from mcp_pal.types import InProcessServer

    server = InProcessServer(name="local", factory=lambda: object())
    assert "factory" not in server.model_dump()
    assert server.model_dump()["origin"] == "python_registration"
    with pytest.raises(TypeError):
        pickle.dumps(server)
