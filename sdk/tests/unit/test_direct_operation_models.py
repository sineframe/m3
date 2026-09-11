"""Serializable direct-operation specifications and result contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

import mcp_pal.async_api as async_api
import mcp_pal.sync_api as sync_api
from mcp_pal.direct_client import (
    AsyncDirectClient,
    Prompt,
    Resource,
    ResourceTemplate,
    Tool,
)
from mcp_pal.types import (
    CallTool,
    CallToolResult,
    DirectOperation,
    DirectResult,
    DirectSpec,
    GetPrompt,
    GetPromptResult,
    ListPrompts,
    ListPromptsResult,
    ListResources,
    ListResourcesResult,
    ListTemplates,
    ListTemplatesResult,
    ListTools,
    ListToolsResult,
    Ping,
    PingResult,
    PromptInfo,
    ReadResource,
    ReadResourceResult,
    ResourceInfo,
    ServerBinding,
    StdioServer,
    ToolInfo,
)


def test_direct_value_exports_use_the_stable_types_module_identities() -> None:
    from mcp_pal import types

    assert types.ToolInfo is async_api.ToolInfo
    assert types.ResourceInfo is async_api.ResourceInfo
    assert types.TemplateInfo is async_api.TemplateInfo
    assert types.PromptInfo is async_api.PromptInfo
    assert types.PromptInfo is sync_api.PromptInfo
    assert types.ResourceInfo is sync_api.ResourceInfo
    assert types.TemplateInfo is sync_api.TemplateInfo
    assert Tool is types.ToolInfo
    assert Resource is types.ResourceInfo
    assert ResourceTemplate is types.TemplateInfo
    assert Prompt is types.PromptInfo


@pytest.mark.asyncio
async def test_direct_client_conversions_return_stable_values_with_raw_evidence() -> (
    None
):
    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> object:
            return SimpleNamespace(
                protocol_version="2025-06-18", server_info={}, capabilities={}
            )

        async def list_tools(self, *, params: object = None) -> object:
            del params
            return SimpleNamespace(tools=(raw_tool,), next_cursor=None)

        async def list_resources(self, *, params: object = None) -> object:
            del params
            return SimpleNamespace(resources=(raw_resource,), next_cursor=None)

        async def list_resource_templates(self, *, params: object = None) -> object:
            del params
            return SimpleNamespace(resource_templates=(raw_template,), next_cursor=None)

        async def list_prompts(self, *, params: object = None) -> object:
            del params
            return SimpleNamespace(prompts=(raw_prompt,), next_cursor=None)

    raw_tool = SimpleNamespace(name="tool", inputSchema={"type": "object"})
    raw_resource = SimpleNamespace(name="resource", uri="memory://resource")
    raw_template = SimpleNamespace(name="template", uri_template="memory://{id}")
    raw_prompt = SimpleNamespace(name="prompt", arguments=())

    async with AsyncDirectClient(Session()) as client:
        tool = (await client.list_tools()).tools[0]
        resource = (await client.list_resources()).resources[0]
        template = (await client.list_resource_templates()).resource_templates[0]
        prompt = (await client.list_prompts()).prompts[0]

    from mcp_pal import types

    assert type(tool) is types.ToolInfo
    assert type(resource) is types.ResourceInfo
    assert type(template) is types.TemplateInfo
    assert type(prompt) is types.PromptInfo
    assert tool.raw is raw_tool
    assert resource.raw is raw_resource
    assert template.raw is raw_template
    assert prompt.raw is raw_prompt
    assert "raw" not in tool.model_dump(mode="json")
    assert "raw" not in resource.model_dump(mode="json")
    assert "raw" not in template.model_dump(mode="json")
    assert "raw" not in prompt.model_dump(mode="json")


def test_direct_operations_are_discriminated_and_round_trip_as_json() -> None:
    operations = (
        ListTools(server="primary", cursor="next"),
        ListResources(),
        ListTemplates(all_pages=False),
        ListPrompts(cursor="cursor"),
        CallTool(name="add", arguments={"left": 2}),
        ReadResource(uri="memory://value"),
        GetPrompt(name="greeting", arguments={"name": "Ada"}),
        Ping(),
    )
    adapter = TypeAdapter(DirectOperation)
    for operation in operations:
        restored = adapter.validate_python(adapter.dump_python(operation, mode="json"))
        assert restored == operation
        assert adapter.validate_json(operation.model_dump_json()) == operation

    schema = adapter.json_schema()
    assert schema["discriminator"]["propertyName"] == "kind"
    assert set(schema["discriminator"]["mapping"]) == {
        "list_tools",
        "list_resources",
        "list_resource_templates",
        "list_prompts",
        "call_tool",
        "read_resource",
        "get_prompt",
        "ping",
    }


def test_direct_operation_inputs_are_frozen_and_validate_selectors() -> None:
    operation = CallTool(name="echo", arguments={"nested": {"value": 1}})
    with pytest.raises((TypeError, ValidationError)):
        operation.name = "changed"  # type: ignore[misc]
    with pytest.raises((TypeError, AttributeError)):
        operation.arguments["nested"]["value"] = 2  # type: ignore[index]

    server = StdioServer(name="echo", command="echo")
    spec = DirectSpec(
        servers=(ServerBinding(server=server, alias="primary"),),
        operation=Ping(server="primary"),
        validate_schemas=True,
    )
    assert spec.operation.server == "primary"
    assert (
        DirectSpec.model_validate(spec.model_dump(mode="json")).validate_schemas is True
    )
    with pytest.raises(ValidationError, match="unique aliases"):
        DirectSpec(
            servers=(
                ServerBinding(server=server, alias="same"),
                ServerBinding(
                    server=StdioServer(name="other", command="echo"), alias="same"
                ),
            ),
            operation=Ping(),
        )
    with pytest.raises(ValidationError, match="does not match"):
        DirectSpec(
            servers=(ServerBinding(server=server, alias="primary"),),
            operation=Ping(server="missing"),
        )
    with pytest.raises(ValidationError, match="required when multiple"):
        DirectSpec(
            servers=(
                ServerBinding(server=server, alias="one"),
                ServerBinding(
                    server=StdioServer(name="other", command="echo"), alias="two"
                ),
            ),
            operation=Ping(),
        )
    with pytest.raises(ValidationError, match="unique aliases"):
        DirectSpec(
            servers=(
                ServerBinding(server=server),
                ServerBinding(
                    server=StdioServer(name="other", command="echo"), alias="echo"
                ),
            ),
            operation=Ping(server="echo"),
        )
    with pytest.raises(ValidationError):
        ServerBinding(server=server, alias="")


def test_direct_operation_results_are_discriminated_and_serializable() -> None:
    adapter = TypeAdapter(DirectResult)
    results = (
        ListToolsResult(
            tools=(ToolInfo(name="echo"),), raw=SimpleNamespace(kind="tools")
        ),
        ListResourcesResult(
            resources=(ResourceInfo(name="doc", uri="memory://doc"),),
            raw=SimpleNamespace(kind="resources"),
        ),
        ListTemplatesResult(
            resource_templates=(), raw=SimpleNamespace(kind="templates")
        ),
        ListPromptsResult(
            prompts=(PromptInfo(name="greeting"),), raw=SimpleNamespace(kind="prompts")
        ),
        CallToolResult(
            content=({"type": "text", "text": "ok"},),
            structured_content={"value": 3},
            raw=SimpleNamespace(kind="call_tool"),
        ),
        ReadResourceResult(
            contents=({"type": "text", "text": "ok"},),
            raw=SimpleNamespace(kind="read_resource"),
        ),
        GetPromptResult(
            description="hello",
            messages=({"role": "user"},),
            raw=SimpleNamespace(kind="get_prompt"),
        ),
        PingResult(result_type="pong", raw=SimpleNamespace(kind="ping")),
    )
    for result in results:
        dumped = result.model_dump(mode="json")
        assert "raw" not in dumped
        assert "raw" not in result.model_dump_json()
        assert result.raw is not None
        restored = adapter.validate_json(result.model_dump_json())
        assert restored.raw is None
        assert restored == type(result).model_validate(dumped)
    assert adapter.json_schema()["discriminator"]["propertyName"] == "kind"


def test_direct_execution_requires_an_explicit_operation() -> None:
    server = StdioServer(name="echo", command="echo")
    with pytest.raises(ValidationError):
        DirectSpec(servers=(ServerBinding(server=server),))
