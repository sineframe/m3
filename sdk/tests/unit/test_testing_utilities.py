"""Deterministic mock, fault, and replay contracts."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp import types as mcp_types

from m3 import testing as testing_module
from m3.async_api import (
    AsyncMCPTestKit,
    CallToolResult,
    PromptResult,
    ResourceReadResult,
)
from m3.errors import (
    ModelValidationError,
    OperationCancelled,
    OperationTimeout,
    ProtocolError,
    TransportError,
)
from m3.sync_api import MCPTestKit
from m3.testing import (
    ArtifactIntegrityError,
    FaultInjector,
    Gate,
    MockExpectationError,
    MockMCPServer,
    RecordedArtifact,
    RecordedInteraction,
    Recording,
    RedactionBinding,
    ReplayMismatch,
    ReplayServer,
    VirtualClock,
)
from m3.trace.redaction import RedactionConfig


def _server() -> MockMCPServer:
    server = MockMCPServer(
        "mock-fixture",
        redaction_config=RedactionConfig(
            secrets=frozenset({"mock-secret"}), include_environment=False
        ),
    )

    @server.tool("echo")
    def echo(arguments: dict[str, object]) -> str:
        server.state["calls"] = int(server.state.get("calls", 0)) + 1
        return str(arguments.get("text", ""))

    @server.resource("memory://doc", name="doc")
    def resource(_uri: str) -> str:
        return "resource value"

    @server.prompt("greet")
    def prompt(arguments: dict[str, str]) -> str:
        return f"hello {arguments.get('name', 'world')}"

    return server


@pytest.mark.asyncio
async def test_decorated_stateful_server_works_through_official_client() -> None:
    mock = _server()
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            result = await client.call_tool("echo", {"text": "hello"})
            assert isinstance(result, CallToolResult)
            assert result.content[0]["text"] == "hello"
            resource = await client.read_resource("memory://doc")
            assert isinstance(resource, ResourceReadResult)
            assert resource.text == "resource value"
            prompt = await client.get_prompt("greet", {"name": "Ada"})
            assert isinstance(prompt, PromptResult)
            assert prompt.messages[0]["content"]["text"] == "hello Ada"
    assert mock.state["calls"] == 1
    mock.close()


@pytest.mark.asyncio
async def test_expectations_cover_official_tool_resource_and_prompt_calls() -> None:
    mock = _server()
    mock.expect("tools/list")
    mock.expect_tool_call("echo", {"text": "expected"}).returns("matched")
    mock.expect("resources/list")
    mock.expect("resources/read", uri="memory://doc")
    mock.expect("prompts/list")
    mock.expect("prompts/get", name="greet", arguments={"name": "Ada"})

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            await client.list_tools()
            result = await client.call_tool("echo", {"text": "expected"})
            assert isinstance(result, CallToolResult)
            assert result.content[0]["text"] == "matched"
            await client.list_resources()
            await client.read_resource("memory://doc")
            await client.list_prompts()
            await client.get_prompt("greet", {"name": "Ada"})
    mock.verify()
    mock.close()


@pytest.mark.asyncio
async def test_repeated_optional_unordered_and_fallback_expectations_are_real() -> None:
    mock = MockMCPServer()

    @mock.tool("first")
    def first(_arguments: dict[str, object]) -> str:
        return "first-handler"

    @mock.tool("second")
    def second(_arguments: dict[str, object]) -> str:
        return "second-handler"

    @mock.tool("repeat")
    def repeat(_arguments: dict[str, object]) -> str:
        return "repeat-handler"

    @mock.tool("fallback")
    def fallback(_arguments: dict[str, object]) -> str:
        return "fallback-handler"

    mock.expect("tools/call", tool="unused", optional=True)
    mock.expect("tools/call", tool="first", unordered="pair")
    mock.expect("tools/call", tool="second", unordered="pair")
    mock.expect("tools/call", tool="repeat", repeat=(2, 2))
    mock.expect("tools/call", tool="fallback", fallback=True).returns("fallback-value")

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            await client.call_tool("second", {})
            await client.call_tool("first", {})
            await client.call_tool("repeat", {})
            await client.call_tool("repeat", {})
            result = await client.call_tool("fallback", {})
            assert isinstance(result, CallToolResult)
            assert result.content[0]["text"] == "fallback-value"
    mock.verify()
    mock.close()


def test_expectation_modes_and_strict_unexpected_calls() -> None:
    mock = MockMCPServer()
    mock.expect_tool_call("echo", {"text": "hello"}).returns("ok")
    mock.expect("tools/call", tool="optional", optional=True)
    mock.expect("tools/call", tool="repeat", repeat=(1, 2))
    mock.expect("tools/call", arguments={"nested": {"x": 1}}, subset=True)
    mock.expect("tools/call", tool="fallback", fallback=True).returns("fallback")
    assert (
        mock._expect_call(
            "tools/call", {"tool": "echo", "arguments": {"text": "hello"}}
        )
        is not None
    )
    assert (
        mock._expect_call("tools/call", {"tool": "repeat", "arguments": {}}) is not None
    )
    with pytest.raises(MockExpectationError):
        mock._expect_call("unknown/method", {})


@pytest.mark.asyncio
async def test_gate_fault_and_virtual_clock_are_deterministic() -> None:
    gate = Gate()
    faults = FaultInjector()
    faults.hang("tools/call", gate)
    completed = False

    async def operation() -> None:
        nonlocal completed
        await faults.before("tools/call")
        completed = True

    task = asyncio.create_task(operation())
    await asyncio.sleep(0)
    assert completed is False
    gate.open()
    await task
    assert completed is True

    clock = VirtualClock()
    sleeper = asyncio.create_task(clock.sleep(5))
    await asyncio.sleep(0)
    assert sleeper.done() is False
    await clock.advance(5)
    await sleeper
    assert clock.now == 5


@pytest.mark.asyncio
async def test_recording_is_redacted_json_and_replay_is_strict() -> None:
    mock = _server()
    mock.expect_tool_call("echo", {"text": "mock-secret"})
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            result = await client.call_tool("echo", {"text": "mock-secret"})
            assert isinstance(result, CallToolResult)
    recording = mock.recording()
    encoded = recording.to_json()
    assert "mock-secret" not in encoded
    restored = Recording.from_json(encoded)
    replay = ReplayServer(
        restored,
        redaction_config=RedactionConfig(
            secrets=frozenset({"mock-secret"}), include_environment=False
        ),
    )
    match = replay.match(
        "tools/call", {"tool": "echo", "arguments": {"text": "mock-secret"}}
    )
    assert match.method == "tools/call"
    with pytest.raises(ReplayMismatch):
        replay.match("tools/call", {"tool": "echo", "arguments": {"text": "different"}})


def test_recorded_artifacts_are_content_addressed_and_tamper_evident() -> None:
    artifact = RecordedArtifact.from_bytes("blob-1", b"opaque binary\x00\xff")
    recording = Recording((), redaction_bound=True, artifacts=(artifact,))
    recording.validate_artifacts({"blob-1": b"opaque binary\x00\xff"})
    with pytest.raises(ArtifactIntegrityError):
        recording.validate_artifacts({"blob-1": b"tampered"})
    with pytest.raises(ValueError, match="explicit redaction config"):
        recording.to_json()
    restored = Recording.from_json(
        recording.with_redaction_config(
            RedactionConfig(include_environment=False)
        ).to_json()
    )
    assert restored.artifacts == (artifact,)
    with pytest.raises(ArtifactIntegrityError):
        ReplayServer(restored, artifacts={"blob-1": b"tampered"})


def test_manual_recording_serialization_applies_explicit_redaction_and_safe_provenance() -> (
    None
):
    canary = "MANUAL-CANARY-SECRET"
    config = RedactionConfig(
        secrets=frozenset({canary, "TOKEN", "TOKEN-LONG"}),
        include_environment=False,
    )
    recording = Recording(
        (
            RecordedInteraction(
                "tools/call", {"arguments": {"value": canary}}, {"echo": canary}
            ),
        ),
        redaction_bound=True,
        redaction_bindings=(
            RedactionBinding(
                source="runtime-secret-resolver",
                version="v2",
                wildcard_paths=("$.interactions[].params.arguments.value",),
                secret_references=("provider/test-secret",),
            ),
        ),
    ).with_redaction_config(config)
    encoded = recording.to_json()
    assert canary not in encoded
    assert "provider/test-secret" in encoded
    assert "runtime-secret-resolver" in encoded
    assert "MANUAL-CANARY" not in repr(recording)
    restored = Recording.from_json(encoded)
    assert restored.redaction_bindings[0].secret_references == ("provider/test-secret",)
    assert "MANUAL-CANARY" not in repr(restored)
    replay = ReplayServer(restored, redaction_config=config)
    matched = replay.match("tools/call", {"arguments": {"value": canary}})
    assert matched.params["arguments"] == {"value": "[REDACTED]"}


def test_recording_rejects_bound_json_without_explicit_binding_metadata() -> None:
    document = (
        '{"schema":"m3.mock_recording.v1","redaction_bound":true,"interactions":[]}'
    )
    with pytest.raises(ValueError, match="redaction bindings"):
        Recording.from_json(document)


@pytest.mark.asyncio
async def test_recording_captures_ordered_non_tool_operations_and_replays_them() -> (
    None
):
    mock = _server()
    mock.resource_template("memory://{name}", name="memory")
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            await client.list_all_tools()
            await client.list_all_resources()
            await client.list_all_resource_templates()
            await client.list_all_prompts()
            await client.read_resource("memory://doc")
            await client.get_prompt("greet", {"name": "Ada"})
            await client.subscribe_resource("memory://doc")
            await client.unsubscribe_resource("memory://doc")
            await client.complete(
                mcp_types.PromptReference(name="greet"), {"name": "name", "value": "A"}
            )
    recording = mock.recording()
    methods = [item.method for item in recording.interactions]
    assert methods == [
        "tools/list",
        "resources/list",
        "resources/templates/list",
        "prompts/list",
        "resources/read",
        "prompts/get",
        "resources/subscribe",
        "resources/unsubscribe",
        "completion/complete",
    ]
    assert recording.server_name == "mock-fixture"
    assert recording.initialization["serverInfo"] == {
        "name": "mock-fixture",
        "version": "1",
    }
    replay = ReplayServer(Recording.from_json(recording.to_json()))
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(replay.in_process()) as client:
            await client.list_all_tools()
            await client.list_all_resources()
            await client.list_all_resource_templates()
            await client.list_all_prompts()
            await client.read_resource("memory://doc")
            await client.get_prompt("greet", {"name": "Ada"})
            await client.subscribe_resource("memory://doc")
            await client.unsubscribe_resource("memory://doc")
            await client.complete(
                mcp_types.PromptReference(name="greet"), {"name": "name", "value": "A"}
            )
    replay.verify_replay()


@pytest.mark.asyncio
async def test_response_shape_faults_are_observable_through_official_client() -> None:
    faults = FaultInjector().oversized("tools/call", 32)
    mock = MockMCPServer(faults=faults)

    @mock.tool("first")
    def first(_arguments: dict[str, object]) -> str:
        return "first"

    @mock.tool("second")
    def second(_arguments: dict[str, object]) -> str:
        return "second"

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            tools = await client.list_all_tools()
            result = await client.call_tool("first", {})

    assert [tool.name for tool in tools] == ["first", "second"]
    assert isinstance(result, CallToolResult)
    assert len(result.content[-1]["text"]) == 32


@pytest.mark.asyncio
async def test_invalid_advertised_schema_is_rejected_by_official_server() -> None:
    mock = MockMCPServer(faults=FaultInjector().invalid_schema("tools/list"))

    @mock.tool("echo")
    def echo(_arguments: dict[str, object]) -> str:
        return "ok"

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            with pytest.raises(ProtocolError):
                await client.list_tools()


@pytest.mark.asyncio
async def test_wire_faults_are_typed_and_concurrent_responses_reorder() -> None:
    faults = FaultInjector().reorder("tools/call")
    mock = MockMCPServer(faults=faults)

    @mock.tool("echo")
    async def echo(arguments: dict[str, object]) -> str:
        if arguments.get("value") == 1:
            await asyncio.sleep(0.02)
        return str(arguments.get("value"))

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process()) as client:
            first, second = await asyncio.gather(
                client.call_tool("echo", {"value": 1}),
                client.call_tool("echo", {"value": 2}),
            )

    assert isinstance(first, CallToolResult)
    assert isinstance(second, CallToolResult)
    assert first.content[0]["text"] == "1"
    assert second.content[0]["text"] == "2"


@pytest.mark.asyncio
async def test_disconnect_malformed_and_partial_faults_close_as_transport_errors() -> (
    None
):
    for configure in ("disconnect", "malformed", "partial_frame"):
        faults = FaultInjector()
        getattr(faults, configure)("tools/call")
        mock = MockMCPServer(faults=faults)

        @mock.tool("echo")
        def echo(_arguments: dict[str, object]) -> str:
            return "ok"

        async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
            async with kit.direct(mock.in_process(), timeout=0.2) as client:
                with pytest.raises(TransportError):
                    await client.call_tool("echo", {})


@pytest.mark.asyncio
async def test_protocol_and_cancellation_race_faults_are_typed() -> None:
    protocol_fault = FaultInjector().protocol_error(
        "tools/call", code=-32042, message="injected"
    )
    protocol_mock = MockMCPServer(faults=protocol_fault)

    @protocol_mock.tool("echo")
    def protocol_echo(_arguments: dict[str, object]) -> str:
        return "ok"

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(protocol_mock.in_process()) as client:
            with pytest.raises(ProtocolError):
                await client.call_tool("echo", {})

    race_fault = FaultInjector().cancellation_race("tools/call")
    race_mock = MockMCPServer(faults=race_fault)

    @race_mock.tool("echo")
    def race_echo(_arguments: dict[str, object]) -> str:
        return "ok"

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(race_mock.in_process()) as client:
            operation = asyncio.create_task(client.call_tool("echo", {}))
            await asyncio.sleep(0.02)
            operation.cancel()
            with pytest.raises(OperationCancelled):
                await operation


@pytest.mark.asyncio
async def test_invalid_structured_result_is_model_validation_with_trace_evidence() -> (
    None
):
    faults = FaultInjector().invalid_result("tools/call")
    mock = MockMCPServer(faults=faults)

    @mock.tool("echo")
    def echo(_arguments: dict[str, object]) -> dict[str, object]:
        return {"value": 1}

    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(mock.in_process(), validate_schemas=True) as client:
            with pytest.raises(ModelValidationError) as failure:
                await client.call_tool("echo", {})
            assert failure.value.details["kind"] == "invalid_result"
            assert "invalid structured result" not in str(failure.value)
            evidence = failure.value.details["trace_evidence"]
            assert isinstance(evidence, Mapping)
            assert evidence["highest_sequence"] > 0
        assert client.final_trace is not None
        assert client.final_trace.highest_sequence >= 0
        assert len(client.final_trace.events) > 0


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_literal_wire_invalid_structured_result_is_sanitized_and_finalized() -> (
    None
):
    faults = FaultInjector().invalid_result("tools/call")
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(faults.stdio_server(), validate_schemas=True) as client:
            with pytest.raises(ModelValidationError) as failure:
                await client.call_tool("echo", {"text": "wire-result-secret"})
            assert failure.value.details["kind"] == "invalid_result"
            assert "wire-result-secret" not in repr(failure.value.details)
        assert client.final_trace is not None
        assert client.final_trace.highest_sequence >= 0


@pytest.mark.process_lifecycle
def test_sync_literal_wire_invalid_structured_result_has_same_contract() -> None:
    faults = FaultInjector().invalid_result("tools/call")
    kit = MCPTestKit(env={}, cwd="/tmp/m3-no-project")
    client = kit.direct(faults.stdio_server(), validate_schemas=True)
    try:
        with client:
            with pytest.raises(ModelValidationError) as failure:
                client.call_tool("echo", {})
            assert failure.value.details["kind"] == "invalid_result"
            assert client.trace is not None
        assert client.final_trace is not None
    finally:
        kit.close()


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_fixture_emits_literal_faults_and_bounds_raw_payloads() -> None:
    for configure in ("partial_frame", "malformed", "process_crash"):
        faults = FaultInjector()
        getattr(faults, configure)("tools/call")
        async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
            async with kit.direct(faults.stdio_server()) as client:
                with pytest.raises((OperationTimeout, TransportError)) as failure:
                    await client.call_tool("echo", {"text": "wire-secret"}, timeout=2)
                assert "wire-secret" not in str(failure.value)

    faults = FaultInjector().oversized("tools/call", 256)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.direct(faults.stdio_server()) as client:
            result = await client.call_tool("echo", {"text": "ok"})
            assert isinstance(result, CallToolResult)
            assert len(result.content[-1]["text"]) == 256


@pytest.mark.process_lifecycle
def test_stdio_fixture_is_usable_through_sync_client() -> None:
    faults = FaultInjector().partial_frame("tools/call")
    kit = MCPTestKit(env={}, cwd="/tmp/m3-no-project")
    client = kit.direct(faults.stdio_server())
    try:
        with client:
            with pytest.raises((OperationTimeout, TransportError)):
                client.call_tool("echo", {"text": "sync-wire-secret"}, timeout=2)
    finally:
        kit.close()


@pytest.mark.asyncio
async def test_sse_fixture_emits_literal_event_faults() -> None:
    faults = FaultInjector().partial_frame("tools/call")
    try:
        async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
            async with kit.direct(faults.sse_server(), timeout=2) as client:
                with pytest.raises((OperationTimeout, TransportError)) as failure:
                    await client.call_tool("echo", {"text": "sse-wire-secret"})
                assert "sse-wire-secret" not in str(failure.value)
    finally:
        faults.close_fixture()

    faults = FaultInjector().oversized("tools/call", 128)
    try:
        async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
            async with kit.direct(faults.sse_server(), timeout=2) as client:
                result = await client.call_tool("echo", {})
                assert isinstance(result, CallToolResult)
                assert len(result.content[-1]["text"]) == 128
    finally:
        faults.close_fixture()


def test_recording_rejects_unsafe_deserialization() -> None:
    with pytest.raises(ValueError):
        Recording.from_json("__import__('os').system('false')")


def test_json_projection_fails_closed_for_hostile_and_cyclic_values() -> None:
    class Hostile(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("secret-value")

        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("secret-value")

        def __len__(self) -> int:
            raise RuntimeError("secret-value")

    with pytest.raises(TypeError) as caught:
        testing_module._json_safe(Hostile())
    assert "secret-value" not in str(caught.value)
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(TypeError):
        testing_module._json_safe(cycle)


def test_recording_rejects_hostile_json_shapes_and_unbound_replay() -> None:
    valid_prefix = (
        '{"schema":"m3.mock_recording.v1","redaction_bound":true,"interactions":['
    )
    with pytest.raises(ValueError):
        Recording.from_json(
            valid_prefix + '{"method":"tools/call","params":{},"sequence":true}]}'
        )
    with pytest.raises(ValueError):
        Recording.from_json(
            valid_prefix + '{"method":"tools/call","params":{},"unknown":1}]}'
        )
    with pytest.raises(ValueError):
        Recording.from_json(
            '{"schema":"m3.mock_recording.v1","redaction_bound":true,"interactions":[],"value":NaN}'
        )
    with pytest.raises(ValueError):
        ReplayServer(Recording((), redaction_bound=False))
    deep = "{" + '"x":{' * 70 + "0" + "}}" * 70
    with pytest.raises(ValueError):
        Recording.from_json(
            '{"schema":"m3.mock_recording.v1","redaction_bound":true,"initialization":'
            + deep
            + ',"interactions":[]}'
        )


def test_relaxed_replay_preserves_recording_and_relaxation_provenance() -> None:
    recording = Recording(
        (RecordedInteraction("tools/call", {"tool": "echo", "arguments": {}}),),
        redaction_bound=True,
        provenance=("source-recording",),
    )
    replay = ReplayServer(recording, strict=False)
    replay.match("tools/call", {"tool": "different", "arguments": {}})
    assert replay.provenance == ("source-recording", "relaxed_replay")


def test_property_strategy_accepts_json_schema_required_lists() -> None:
    pytest.importorskip("hypothesis")
    from hypothesis import find

    from m3.testing_property import schema_strategy

    strategy = schema_strategy(
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
    )
    value = find(
        strategy, lambda candidate: isinstance(candidate, dict) and "name" in candidate
    )
    assert isinstance(value, dict)
    assert isinstance(value["name"], str)


def test_property_strategies_generate_valid_and_invalid_values() -> None:
    pytest.importorskip("hypothesis")
    from hypothesis import find
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

    from m3.testing_property import invalid_schema_strategy, schema_strategy

    schema = {
        "$defs": {"tag": {"type": "string", "pattern": "^[A-Z]{2}$"}},
        "type": "object",
        "properties": {
            "tag": {"$ref": "#/$defs/tag"},
            "values": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["tag", "values"],
        "additionalProperties": False,
    }
    validator = Draft202012Validator(schema)
    valid = cast(Any, find(schema_strategy(schema), lambda value: True))
    invalid = cast(Any, find(invalid_schema_strategy(schema), lambda value: True))
    assert validator.is_valid(valid)
    assert not validator.is_valid(invalid)


def test_property_strategies_cover_composition_nullable_and_bounds() -> None:
    pytest.importorskip("hypothesis")
    from hypothesis import find
    from jsonschema import Draft202012Validator

    from m3.testing_property import invalid_schema_strategy, schema_strategy

    schemas: list[dict[str, Any]] = [
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        {"allOf": [{"type": "integer"}, {"minimum": 2}]},
        {"not": {"type": "null"}},
        {"type": ["string", "null"], "nullable": True},
        {"type": "integer", "minimum": 2, "maximum": 10, "multipleOf": 2},
        {"type": "string", "format": "email"},
    ]
    for schema in schemas:
        validator = Draft202012Validator(schema)
        assert validator.is_valid(
            cast(Any, find(schema_strategy(schema), lambda value: True))
        )
        assert not validator.is_valid(
            cast(Any, find(invalid_schema_strategy(schema), lambda value: True))
        )


def test_bare_tool_and_prompt_decorators_register_function_names() -> None:
    mock = MockMCPServer()

    @mock.tool
    def bare(arguments: dict[str, object]) -> str:
        return str(arguments.get("value", ""))

    @mock.prompt
    def welcome(_arguments: dict[str, str]) -> str:
        return "welcome"

    assert bare.__name__ == "bare"
    assert welcome.__name__ == "welcome"
    assert "bare" in mock._tools
    assert "welcome" in mock._prompts


def test_expectations_are_ordered_unless_explicitly_unordered() -> None:
    mock = MockMCPServer()
    mock.expect_tool_call("first", {})
    mock.expect_tool_call("second", {})
    with pytest.raises(MockExpectationError):
        mock._expect_call("tools/call", {"tool": "second", "arguments": {}})

    unordered = MockMCPServer()
    unordered.expect("tools/call", tool="first", unordered="g")
    unordered.expect("tools/call", tool="second", unordered="g")
    assert (
        unordered._expect_call("tools/call", {"tool": "second", "arguments": {}})
        is not None
    )


def test_native_pytest_setup_and_teardown_errors_remain_in_summary(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_failures.py"
    test_file.write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def setup_failure():\n    raise RuntimeError('setup')\n"
        "@pytest.fixture\n"
        "def teardown_failure():\n    yield\n    raise RuntimeError('teardown')\n"
        "def test_setup(setup_failure): pass\n"
        "def test_teardown(teardown_failure): pass\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--results-db",
            str(tmp_path / "results.sqlite"),
            str(test_file),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "errors" in result.stdout


def test_progress_counts_setup_and_teardown_without_double_completion() -> None:
    from m3.pytest_plugin import _Progress

    reporter = SimpleNamespace(
        isatty=True,
        rewrite=lambda *_args, **_kwargs: None,
        write_line=lambda *_args: None,
    )
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=0),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
    )
    progress = _Progress(config)
    progress.reporter = reporter
    progress.enabled = True
    progress.pytest_collection_finish(SimpleNamespace(items=[1, 2, 3]))
    progress.pytest_runtest_logreport(
        SimpleNamespace(nodeid="skip", when="setup", outcome="skipped")
    )
    progress.pytest_runtest_logreport(
        SimpleNamespace(nodeid="fail", when="setup", outcome="failed")
    )
    progress.pytest_runtest_logreport(
        SimpleNamespace(nodeid="ok", when="setup", outcome="passed")
    )
    progress.pytest_runtest_logreport(
        SimpleNamespace(nodeid="ok", when="call", outcome="passed")
    )
    progress.pytest_runtest_logreport(
        SimpleNamespace(nodeid="ok", when="teardown", outcome="failed")
    )
    assert (progress.completed, progress.passed, progress.failed, progress.skipped) == (
        3,
        0,
        2,
        1,
    )


def test_progress_is_disabled_for_non_tty() -> None:
    from m3.pytest_plugin import _Progress

    reporter = SimpleNamespace(isatty=False)
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=0),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
    )
    progress = _Progress(config)
    progress.reporter = reporter
    progress.enabled = progress.enabled and progress._is_tty()
    assert progress.enabled is False


def test_progress_restores_exact_native_reporter_mode() -> None:
    from m3.pytest_plugin import _Progress

    reporter = SimpleNamespace(isatty=True, _show_progress_info="count")
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=0),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
    )
    progress = _Progress(config)
    progress.reporter = reporter
    progress.disable_native_progress()
    assert reporter._show_progress_info is False
    progress.restore_native_progress()
    assert reporter._show_progress_info == "count"
