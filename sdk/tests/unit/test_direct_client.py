"""Focused contracts for the official MCP ClientSession adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from collections.abc import Mapping as ABCMapping
from typing import Any, cast

import pytest
from mcp import types

from mcp_pal.direct_client import (
    AsyncDirectClient,
    ClientSessionOptions,
    DirectOperationEvent,
    InputRequiredResult,
    PromptResult,
    ResourceReadResult,
    ToolCallResult,
    create_client_session,
)
from mcp_pal.errors import (
    ModelValidationError,
    OperationCancelled,
    OperationTimeout,
    ProtocolError,
    TransportError,
    UnsupportedFeature,
)
from mcp_pal.trace.redaction import RedactionConfig


def _client(
    session: object,
    *,
    validate_schemas: bool = False,
    timeout: float = 30.0,
    evidence_provider: Any = None,
    event_hook: Any = None,
    redaction_config: RedactionConfig | None = None,
) -> AsyncDirectClient:
    return AsyncDirectClient(
        cast(Any, session),
        validate_schemas=validate_schemas,
        timeout=timeout,
        evidence_provider=evidence_provider,
        event_hook=event_hook,
        redaction_config=redaction_config,
    )


def test_official_session_factory_preserves_constructor_callback_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp

    sampling = object()
    elicitation = object()
    options = ClientSessionOptions(
        sampling_callback=sampling, elicitation_callback=elicitation
    )
    captured: dict[str, Any] = {}

    def factory(*args: Any, **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(mcp, "ClientSession", factory)
    create_client_session(None, None, options=options)
    assert captured["sampling_callback"] is sampling
    assert captured["elicitation_callback"] is elicitation


class FakeSession:
    def __init__(self) -> None:
        self.entered = False
        self.closed = False
        self.tools_calls: list[str | None] = []
        self.fail_tools = False
        self.notifications: list[object] = []
        self.completion_calls: list[
            tuple[object, dict[str, str], dict[str, str] | None]
        ] = []
        self.subscribe_calls: list[tuple[str, dict[str, object]]] = []
        self.unsubscribe_calls: list[tuple[str, dict[str, object]]] = []
        self.ping_calls: list[dict[str, object]] = []
        self.logging_calls: list[tuple[object, dict[str, object]]] = []
        self.progress_calls: list[
            tuple[str | int, float, float | None, str | None, dict[str, object]]
        ] = []
        self.roots_list_changed_count = 0

    async def __aenter__(self) -> FakeSession:
        self.entered = True
        return self

    async def __aexit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> None:
        self.closed = True

    async def initialize(self) -> types.InitializeResult:
        return types.InitializeResult(
            protocol_version="2025-06-18",
            capabilities=types.ServerCapabilities(),
            server_info=types.Implementation(name="fixture", version="1.0"),
            instructions="fixture instructions",
        )

    async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
        cursor = getattr(params, "cursor", None) if params is not None else None
        self.tools_calls.append(cursor)
        if self.fail_tools:
            raise RuntimeError("secret=do-not-leak")
        if cursor is None:
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="first",
                        input_schema={"type": "object", "additionalProperties": False},
                    )
                ],
                next_cursor="page-2",
            )
        return types.ListToolsResult(
            tools=[types.Tool(name="second", input_schema={"type": "object"})]
        )

    async def list_resources(
        self, *, params: object = None
    ) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[types.Resource(name="doc", uri="memory://doc")]
        )

    async def list_resource_templates(
        self, *, params: object = None
    ) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(
            resource_templates=[
                types.ResourceTemplate(name="doc", uri_template="memory://{id}")
            ]
        )

    async def read_resource(
        self, uri: str, **kwargs: object
    ) -> types.ReadResourceResult:
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, text="hello")]
        )

    async def list_prompts(self, *, params: object = None) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[types.Prompt(name="greet")])

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None, **kwargs: object
    ) -> types.GetPromptResult:
        return types.GetPromptResult(
            messages=[
                types.PromptMessage(role="user", content=types.TextContent(text=name))
            ]
        )

    async def call_tool(
        self, name: str, arguments: dict[str, object] | None = None, **kwargs: object
    ) -> types.CallToolResult:
        if name == "slow":
            await asyncio.sleep(1)
        if name == "bad":
            return types.CallToolResult(
                content=[types.TextContent(text="failed")], is_error=True
            )
        return types.CallToolResult(
            content=[types.TextContent(text="ok")], structured_content={"value": 3}
        )

    async def complete(
        self,
        reference: object,
        argument: dict[str, str],
        context_arguments: dict[str, str] | None = None,
    ) -> types.CompleteResult:
        self.completion_calls.append((reference, argument, context_arguments))
        return types.CompleteResult(
            completion=types.Completion(values=["one", "two"], total=2, has_more=False)
        )

    async def subscribe_resource(self, uri: str, **kwargs: object) -> types.EmptyResult:
        self.subscribe_calls.append((uri, kwargs))
        return types.EmptyResult()

    async def unsubscribe_resource(
        self, uri: str, **kwargs: object
    ) -> types.EmptyResult:
        self.unsubscribe_calls.append((uri, kwargs))
        return types.EmptyResult()

    async def send_ping(self, **kwargs: object) -> types.EmptyResult:
        self.ping_calls.append(kwargs)
        return types.EmptyResult()

    async def set_logging_level(
        self, level: object, **kwargs: object
    ) -> types.EmptyResult:
        self.logging_calls.append((level, kwargs))
        return types.EmptyResult()

    async def send_progress_notification(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        **kwargs: object,
    ) -> None:
        self.progress_calls.append((progress_token, progress, total, message, kwargs))
        return None

    async def send_notification(self, notification: object) -> None:
        self.notifications.append(notification)

    async def send_roots_list_changed(self) -> None:
        self.roots_list_changed_count += 1


@pytest.mark.asyncio
async def test_initialize_and_context_lifecycle_preserve_official_raw() -> None:
    session = FakeSession()
    async with _client(session) as client:
        assert client.initialization is not None
        assert client.initialization.protocol_version == "2025-06-18"
        assert client.initialization.server_info["name"] == "fixture"
        assert client.initialization.raw is not None
    assert session.entered and session.closed


@pytest.mark.asyncio
async def test_pagination_and_typed_resource_prompt_wrappers() -> None:
    async with _client(FakeSession()) as client:
        tools = await client.list_all_tools()
        resources = await client.list_all_resources()
        templates = await client.list_all_resource_templates()
        prompts = await client.list_all_prompts()
        read = await client.read_resource("memory://doc")
        prompt = await client.get_prompt("greet", {"name": "Ada"})
        assert isinstance(read, ResourceReadResult)
        assert isinstance(prompt, PromptResult)
    assert [tool.name for tool in tools] == ["first", "second"]
    assert resources[0].uri == "memory://doc"
    assert templates[0].uri_template == "memory://{id}"
    assert prompts[0].name == "greet"
    assert read.text == "hello"
    assert prompt.messages[0]["content"]["text"] == "greet"


@pytest.mark.asyncio
async def test_tool_error_is_a_result_and_raw_response_is_preserved() -> None:
    async with _client(FakeSession()) as client:
        result = await client.call_tool("bad", {})
    assert isinstance(result, ToolCallResult)
    assert result.is_error is True
    assert result.content[0]["text"] == "failed"
    assert result.raw is not None


@pytest.mark.asyncio
async def test_structural_input_and_output_validation() -> None:
    session = FakeSession()
    async with _client(session, validate_schemas=True) as client:
        with pytest.raises(ModelValidationError):
            await client.call_tool("first", {"unexpected": object()})

    class OutputSession(FakeSession):
        async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="first",
                        input_schema={"type": "object"},
                        output_schema={"type": "object", "required": ["value"]},
                    )
                ]
            )

    async with _client(OutputSession(), validate_schemas=True) as client:
        result = await client.call_tool("first", {})
        assert isinstance(result, ToolCallResult)
        assert result.structured_content == {"value": 3}

    class InvalidOutputSession(FakeSession):
        async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="first",
                        input_schema={"type": "object"},
                        output_schema={"type": "string"},
                    )
                ]
            )

    async with _client(InvalidOutputSession(), validate_schemas=True) as client:
        with pytest.raises(ModelValidationError) as error:
            await client.call_tool("first", {})
    assert error.value.details["kind"] == "invalid_result"

    class MissingStructuredOutput(FakeSession):
        async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="first",
                        input_schema={"type": "object"},
                        output_schema={"type": "object"},
                    )
                ]
            )

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object] | None = None,
            **kwargs: object,
        ) -> types.CallToolResult:
            return types.CallToolResult(
                content=[types.TextContent(text="only unstructured")]
            )

    async with _client(MissingStructuredOutput(), validate_schemas=True) as client:
        with pytest.raises(ModelValidationError) as error:
            await client.call_tool("first", {})
    assert error.value.details["kind"] == "invalid_result"


@pytest.mark.asyncio
async def test_protocol_failures_are_typed_and_redacted() -> None:
    session = FakeSession()
    session.fail_tools = True
    async with _client(session) as client:
        with pytest.raises(ProtocolError) as error:
            await client.list_tools()
    assert "do-not-leak" not in str(error.value)
    assert error.value.details["operation"] == "tools/list"
    assert error.value.details["partial_evidence"]["captured"] is False


@pytest.mark.asyncio
async def test_repeated_pagination_cursor_is_protocol_error() -> None:
    class LoopSession(FakeSession):
        async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
            return types.ListToolsResult(tools=[], next_cursor="same")

    async with _client(LoopSession()) as client:
        with pytest.raises(ProtocolError):
            await client.list_all_tools()


@pytest.mark.asyncio
async def test_completion_subscriptions_ping_logging_and_notifications_use_official_methods() -> (
    None
):
    session = FakeSession()
    reference = types.PromptReference(name="greet")
    context_arguments = {"language": "en"}
    notification = types.InitializedNotification()
    async with _client(session) as client:
        completion = await client.complete(
            reference, {"argument": "a"}, context_arguments
        )
        await client.subscribe_resource("memory://doc", meta={"trace": "subscribe"})
        await client.unsubscribe_resource("memory://doc", meta={"trace": "unsubscribe"})
        await client.ping()
        await client.set_logging_level("info")
        await client.send_progress_notification("token", 0.5, 1.0, "half")
        await client.send_notification(notification)
        await client.send_roots_list_changed()
    assert completion.values == ("one", "two")
    assert session.completion_calls == [
        (reference, {"argument": "a"}, context_arguments)
    ]
    assert session.subscribe_calls == [
        ("memory://doc", {"meta": {"trace": "subscribe"}})
    ]
    assert session.unsubscribe_calls == [
        ("memory://doc", {"meta": {"trace": "unsubscribe"}})
    ]
    assert len(session.ping_calls) == 1
    assert session.logging_calls == [("info", {"meta": None})]
    assert session.progress_calls == [("token", 0.5, 1.0, "half", {"meta": None})]
    assert session.notifications == [notification]
    assert session.roots_list_changed_count == 1


@pytest.mark.asyncio
async def test_callback_registration_is_explicitly_unsupported_after_session_creation() -> (
    None
):
    async with _client(FakeSession()) as client:
        with pytest.raises(UnsupportedFeature):
            client.register_callbacks(sampling=lambda: None)


@pytest.mark.asyncio
async def test_timeout_and_task_cancellation_map_to_safe_partial_evidence() -> None:
    async with _client(FakeSession()) as client:
        with pytest.raises(OperationTimeout) as timeout:
            await client.call_tool("slow", {}, timeout=0.001)
        assert timeout.value.details["partial_evidence"]["captured"] is False
        task = asyncio.create_task(client.call_tool("slow", {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(OperationCancelled) as cancelled:
            await task
        assert cancelled.value.details["partial_evidence"]["captured"] is False


@pytest.mark.asyncio
async def test_default_timeout_applies_to_initialization_and_each_request() -> None:
    class SlowInitialize(FakeSession):
        async def initialize(self) -> types.InitializeResult:
            await asyncio.sleep(1)
            return await super().initialize()

    with pytest.raises(OperationTimeout):
        async with _client(SlowInitialize(), timeout=0.001):
            pass

    async with _client(FakeSession(), timeout=0.001) as client:
        with pytest.raises(OperationTimeout):
            await client.call_tool("slow", {})


@pytest.mark.asyncio
async def test_transport_errors_are_typed_and_provider_evidence_is_hooked() -> None:
    class BrokenSession(FakeSession):
        async def send_ping(self, **kwargs: object) -> types.EmptyResult:
            raise OSError("socket-secret")

    class Evidence:
        def capture(self, operation: str, phase: str) -> dict[str, object]:
            return {"raw_evidence_ref": {"evidence_id": f"{operation}-{phase}"}}

    events: list[DirectOperationEvent] = []
    async with _client(
        BrokenSession(), evidence_provider=Evidence(), event_hook=events.append
    ) as client:
        with pytest.raises(TransportError) as error:
            await client.ping()
    assert "socket-secret" not in str(error.value)
    assert error.value.details["partial_evidence"]["captured"] is True
    assert [event.phase for event in events if event.operation == "ping"] == [
        "started",
        "failed",
    ]


@pytest.mark.asyncio
async def test_provider_evidence_cannot_override_operation_metadata() -> None:
    class Evidence:
        def capture(self, operation: str, phase: str) -> dict[str, object]:
            return {
                "operation": "spoofed",
                "phase": "spoofed",
                "captured": False,
                "ref": "safe",
            }

    events: list[DirectOperationEvent] = []
    async with _client(
        FakeSession(), evidence_provider=Evidence(), event_hook=events.append
    ) as client:
        await client.ping()

    ping_started = next(event for event in events if event.operation == "ping")
    assert ping_started.operation == "ping"
    assert ping_started.phase == "started"
    assert ping_started.evidence["captured"] is True
    assert ping_started.evidence["ref"] == "safe"


@pytest.mark.asyncio
async def test_provider_evidence_is_redacted_before_protocol_errors_and_hooks() -> None:
    class Evidence:
        def capture(self, operation: str, phase: str) -> dict[str, object]:
            return {
                "raw_secret": "PROVIDER-EVIDENCE-SECRET",
                "nested": {"echo": "PROVIDER-EVIDENCE-SECRET"},
            }

    events: list[DirectOperationEvent] = []
    session = FakeSession()
    session.fail_tools = True
    async with _client(
        session,
        evidence_provider=Evidence(),
        event_hook=events.append,
        redaction_config=RedactionConfig(
            secrets=frozenset({"PROVIDER-EVIDENCE-SECRET"}),
            include_environment=False,
        ),
    ) as client:
        with pytest.raises(ProtocolError) as caught:
            await client.list_tools()

    assert "PROVIDER-EVIDENCE-SECRET" not in repr(caught.value.details)
    assert caught.value.details["partial_evidence"]["raw_secret"] == "[REDACTED]"
    assert all("PROVIDER-EVIDENCE-SECRET" not in repr(event) for event in events)


@pytest.mark.asyncio
async def test_provider_evidence_is_redacted_before_transport_errors_and_hooks() -> (
    None
):
    class BrokenSession(FakeSession):
        async def send_ping(self, **kwargs: object) -> types.EmptyResult:
            raise OSError("socket-secret")

    class Evidence:
        def capture(self, operation: str, phase: str) -> dict[str, object]:
            return {"credential_echo": "PROVIDER-TRANSPORT-SECRET"}

    events: list[DirectOperationEvent] = []
    async with _client(
        BrokenSession(),
        evidence_provider=Evidence(),
        event_hook=events.append,
        redaction_config=RedactionConfig(
            secrets=frozenset({"PROVIDER-TRANSPORT-SECRET"}),
            include_environment=False,
        ),
    ) as client:
        with pytest.raises(TransportError) as caught:
            await client.ping()

    assert "PROVIDER-TRANSPORT-SECRET" not in repr(caught.value.details)
    assert caught.value.details["partial_evidence"]["credential_echo"] == "[REDACTED]"
    assert all("PROVIDER-TRANSPORT-SECRET" not in repr(event) for event in events)


@pytest.mark.asyncio
async def test_hostile_provider_failure_is_value_free_and_not_exposed() -> None:
    class HostileEvidence:
        def capture(self, operation: str, phase: str) -> Mapping[str, Any]:
            raise RuntimeError("HOSTILE-PROVIDER-SECRET")

    events: list[DirectOperationEvent] = []
    session = FakeSession()
    session.fail_tools = True
    async with _client(
        session,
        evidence_provider=HostileEvidence(),
        event_hook=events.append,
    ) as client:
        with pytest.raises(ProtocolError) as caught:
            await client.list_tools()

    assert caught.value.details["partial_evidence"] == {
        "operation": "tools/list",
        "phase": "failed",
        "captured": False,
    }
    assert "HOSTILE-PROVIDER-SECRET" not in repr(caught.value.details)
    assert all("HOSTILE-PROVIDER-SECRET" not in repr(event) for event in events)


@pytest.mark.asyncio
async def test_hostile_provider_mapping_serialization_fails_closed() -> None:
    class HostileMapping(ABCMapping[str, Any]):
        def __getitem__(self, key: str) -> Any:
            raise RuntimeError("HOSTILE-MAPPING-SECRET")

        def __iter__(self) -> Any:
            raise RuntimeError("HOSTILE-MAPPING-SECRET")

        def __len__(self) -> int:
            return 1

    class Evidence:
        def capture(self, operation: str, phase: str) -> Mapping[str, Any]:
            return HostileMapping()

    session = FakeSession()
    session.fail_tools = True
    async with _client(session, evidence_provider=Evidence()) as client:
        with pytest.raises(ProtocolError) as caught:
            await client.list_tools()

    assert caught.value.details["partial_evidence"] == {
        "operation": "tools/list",
        "phase": "failed",
        "captured": False,
    }
    assert "HOSTILE-MAPPING-SECRET" not in repr(caught.value.details)


@pytest.mark.asyncio
async def test_official_mcp_errors_are_protocol_errors_without_provider_text() -> None:
    from mcp.shared.exceptions import MCPError

    class BrokenSession(FakeSession):
        async def send_ping(self, **kwargs: object) -> types.EmptyResult:
            raise MCPError(code=-32602, message="provider-secret")

    async with _client(BrokenSession()) as client:
        with pytest.raises(ProtocolError) as error:
            await client.ping()
    assert "provider-secret" not in str(error.value)
    assert error.value.details["protocol_code"] == -32602


@pytest.mark.asyncio
async def test_official_input_required_results_are_not_coerced_to_empty_wrappers() -> (
    None
):
    class InteractiveSession(FakeSession):
        async def read_resource(self, uri: str, **kwargs: object) -> Any:
            return types.InputRequiredResult.model_construct(
                input_requests={"request-1": {"method": "elicitation/create"}},
                request_state="state-1",
            )

        async def get_prompt(
            self, name: str, arguments: dict[str, str] | None = None, **kwargs: object
        ) -> Any:
            return types.InputRequiredResult.model_construct(
                input_requests={"request-2": {"method": "sampling/createMessage"}},
                request_state="state-2",
            )

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object] | None = None,
            **kwargs: object,
        ) -> Any:
            return types.InputRequiredResult.model_construct(
                input_requests={"request-3": {"method": "roots/list"}},
                request_state="state-3",
            )

    async with _client(InteractiveSession()) as client:
        read = await client.read_resource(
            "memory://interactive", allow_input_required=True
        )
        prompt = await client.get_prompt("interactive", allow_input_required=True)
        call = await client.call_tool("interactive", allow_input_required=True)
    assert isinstance(read, InputRequiredResult) and read.request_state == "state-1"
    assert isinstance(prompt, InputRequiredResult) and prompt.request_state == "state-2"
    assert isinstance(call, InputRequiredResult) and call.request_state == "state-3"


@pytest.mark.asyncio
async def test_draft202012_nested_refs_and_boolean_schemas_are_supported() -> None:
    class SchemaSession(FakeSession):
        mode: str = "ref"

        async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
            # A model-constructed official result lets this fixture exercise
            # the JSON Schema boolean form, which MCP's Pydantic model rejects
            # at normal construction despite JSON Schema allowing it.
            if self.mode == "boolean":
                raw_tool = types.Tool.model_construct(
                    name="first", input_schema=True, output_schema=True
                )
            else:
                raw_tool = types.Tool(
                    name="first",
                    input_schema={
                        "$defs": {"positive": {"type": "integer", "minimum": 1}},
                        "type": "object",
                        "properties": {
                            "values": {
                                "type": "array",
                                "items": {"$ref": "#/$defs/positive"},
                                "minItems": 1,
                            }
                        },
                        "required": ["values"],
                        "additionalProperties": False,
                    },
                    output_schema={
                        "type": "object",
                        "properties": {"value": {"type": "integer", "minimum": 1}},
                        "required": ["value"],
                    },
                )
            return types.ListToolsResult.model_construct(tools=[raw_tool])

    async with _client(SchemaSession(), validate_schemas=True) as client:
        result = await client.call_tool("first", {"values": [2, 3]})
        assert isinstance(result, ToolCallResult)
        assert result.structured_content == {"value": 3}

    boolean_session = SchemaSession()
    boolean_session.mode = "boolean"
    async with _client(boolean_session, validate_schemas=True) as client:
        await client.call_tool("first", {"anything": "accepted"})

    false_session = SchemaSession()
    false_session.mode = "boolean"

    async def false_tools(*, params: object = None) -> types.ListToolsResult:
        raw_tool = types.Tool.model_construct(
            name="first", input_schema=False, output_schema=True
        )
        return types.ListToolsResult.model_construct(tools=[raw_tool])

    false_session.list_tools = false_tools  # type: ignore[method-assign]
    async with _client(false_session, validate_schemas=True) as client:
        with pytest.raises(ModelValidationError) as error:
            await client.call_tool("first", {})
    assert error.value.details["kind"] == "invalid_arguments"


@pytest.mark.asyncio
async def test_malformed_and_remote_schemas_are_typed_without_echoing_untrusted_data() -> (
    None
):
    class BadSchemaSession(FakeSession):
        async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
            raw_tool = types.Tool.model_construct(
                name="first",
                input_schema={"type": "not-a-schema", "secret": "schema-secret"},
            )
            return types.ListToolsResult.model_construct(tools=[raw_tool])

    async with _client(BadSchemaSession(), validate_schemas=True) as client:
        with pytest.raises(ModelValidationError) as error:
            await client.call_tool("first", {})
    assert error.value.details["kind"] == "invalid_schema"
    assert "schema-secret" not in repr(error.value.details)
    assert "not-a-schema" not in str(error.value)

    class RemoteSchemaSession(BadSchemaSession):
        async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
            raw_tool = types.Tool.model_construct(
                name="first",
                input_schema={"$ref": "https://example.invalid/secret-schema.json"},
            )
            return types.ListToolsResult.model_construct(tools=[raw_tool])

    async with _client(RemoteSchemaSession(), validate_schemas=True) as client:
        with pytest.raises(ModelValidationError) as error:
            await client.call_tool("first", {})
    assert error.value.details["kind"] == "invalid_schema"


def test_wrappers_round_trip_without_serializing_raw_evidence() -> None:
    from mcp_pal.direct_client import Tool, ToolCallResult

    tool = Tool(raw={"secret": "not-public"}, name="fixture", input_schema=True)
    dumped = tool.model_dump(mode="json")
    assert "raw" not in dumped
    assert Tool.model_validate(dumped).raw is None

    result = ToolCallResult(
        raw={"secret": "not-public"}, structured_content={"ok": True}
    )
    result_dump = result.model_dump(mode="json")
    assert "raw" not in result_dump
    assert ToolCallResult.model_validate(result_dump).raw is None


@pytest.mark.asyncio
async def test_lifecycle_failure_cleanup_and_use_state_are_explicit() -> None:
    class FailingInitialize(FakeSession):
        async def initialize(self) -> types.InitializeResult:
            raise RuntimeError("credential-secret")

    failed = FailingInitialize()
    with pytest.raises(ProtocolError) as error:
        async with _client(failed):
            pass
    assert failed.closed is True
    assert "credential-secret" not in str(error.value)

    class FailingEnter(FakeSession):
        async def __aenter__(self) -> FailingEnter:
            raise RuntimeError("enter-secret")

    not_entered = FailingEnter()
    with pytest.raises(ProtocolError):
        async with _client(not_entered):
            pass
    assert not_entered.closed is False

    client = _client(FakeSession())
    with pytest.raises(RuntimeError):
        await client.list_tools()
    await client.__aenter__()
    await client.aclose()
    await client.aclose()
    with pytest.raises(RuntimeError):
        await client.ping()
