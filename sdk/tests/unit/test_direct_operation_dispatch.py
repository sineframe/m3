"""Focused execution dispatch contracts for serializable direct operations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from m3.execution_runtime import AsyncExecutionController
from m3.types import (
    CallTool,
    CallToolResult,
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
    ReadResource,
    ReadResourceResult,
    ServerBinding,
    StdioServer,
)


class _DispatchClient:
    def __init__(self) -> None:
        self.tool_cursors: list[str | None] = []

    async def __aenter__(self) -> _DispatchClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def list_tools(self, *, cursor: str | None = None) -> object:
        self.tool_cursors.append(cursor)
        if cursor is None:
            return SimpleNamespace(
                raw=SimpleNamespace(page="one"),
                tools=(),
                next_cursor="page-2",
            )
        return SimpleNamespace(
            raw=SimpleNamespace(page="two"),
            tools=(),
            next_cursor=None,
        )

    async def list_resources(self, *, cursor: str | None = None) -> object:
        return SimpleNamespace(
            raw=SimpleNamespace(page="resources"),
            resources=(),
            next_cursor=None,
        )

    async def list_resource_templates(self, *, cursor: str | None = None) -> object:
        return SimpleNamespace(
            raw=SimpleNamespace(page="templates"),
            resource_templates=(),
            next_cursor=None,
        )

    async def list_prompts(self, *, cursor: str | None = None) -> object:
        return SimpleNamespace(
            raw=SimpleNamespace(page="prompts"),
            prompts=(),
            next_cursor=None,
        )

    async def call_tool(self, name: str, arguments: Any) -> object:
        assert name == "echo"
        assert dict(arguments) == {"text": "dispatch"}
        return SimpleNamespace(
            raw=SimpleNamespace(method="tools/call"),
            content=({"type": "text", "text": "dispatch"},),
            structured_content={"value": 1},
            is_error=True,
        )

    async def read_resource(self, uri: str) -> object:
        assert uri == "memory://value"
        return SimpleNamespace(
            raw=SimpleNamespace(method="resources/read"),
            contents=({"uri": uri, "text": "value"},),
        )

    async def get_prompt(self, name: str, arguments: Any) -> object:
        assert name == "greeting"
        assert dict(arguments) == {"name": "Ada"}
        return SimpleNamespace(
            raw=SimpleNamespace(method="prompts/get"),
            description="greeting",
            messages=({"role": "user"},),
        )

    async def ping(self) -> object:
        return SimpleNamespace(raw=SimpleNamespace(method="ping"), result_type="pong")


class _DispatchKit:
    def __init__(self, client: _DispatchClient) -> None:
        self.client = client
        self.selected_servers: list[object] = []

    def direct(self, _server: object, **_options: object) -> _DispatchClient:
        self.selected_servers.append(_server)
        return self.client


def _spec(operation: object) -> DirectSpec:
    return DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="dispatch", command="unused"),
                alias="dispatch",
            ),
        ),
        operation=operation,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_all_direct_operation_variants_dispatch_to_typed_results() -> None:
    client = _DispatchClient()
    controller = AsyncExecutionController(_DispatchKit(client))
    operations = (
        (ListTools(server="dispatch"), ListToolsResult),
        (ListResources(server="dispatch"), ListResourcesResult),
        (ListTemplates(server="dispatch"), ListTemplatesResult),
        (ListPrompts(server="dispatch"), ListPromptsResult),
        (
            CallTool(
                server="dispatch",
                name="echo",
                arguments={"text": "dispatch"},
            ),
            CallToolResult,
        ),
        (ReadResource(server="dispatch", uri="memory://value"), ReadResourceResult),
        (
            GetPrompt(
                server="dispatch",
                name="greeting",
                arguments={"name": "Ada"},
            ),
            GetPromptResult,
        ),
        (Ping(server="dispatch"), PingResult),
    )

    for operation, result_type in operations:
        result = await controller.run(_spec(operation))
        assert result.snapshot.outcome.value == "completed"
        assert isinstance(result.direct_result, result_type)
        assert result.direct_result.raw is not None
        if isinstance(result.direct_result, CallToolResult):
            assert result.direct_result.is_error is True

    await controller.close()


@pytest.mark.asyncio
async def test_list_operation_honors_start_cursor_and_all_pages() -> None:
    client = _DispatchClient()
    controller = AsyncExecutionController(_DispatchKit(client))

    all_pages = await controller.run(_spec(ListTools(server="dispatch")))
    assert isinstance(all_pages.direct_result, ListToolsResult)
    assert client.tool_cursors == [None, "page-2"]

    client.tool_cursors.clear()
    one_page = await controller.run(
        _spec(ListTools(server="dispatch", cursor="page-2", all_pages=False))
    )
    assert isinstance(one_page.direct_result, ListToolsResult)
    assert client.tool_cursors == ["page-2"]
    await controller.close()


@pytest.mark.asyncio
async def test_explicit_selector_dispatches_to_the_requested_second_binding() -> None:
    client = _DispatchClient()
    kit = _DispatchKit(client)
    controller = AsyncExecutionController(kit)
    first = StdioServer(name="first", command="unused")
    second = StdioServer(name="second", command="unused")
    spec = DirectSpec(
        servers=(
            ServerBinding(server=first, alias="first"),
            ServerBinding(server=second, alias="second"),
        ),
        operation=Ping(server="second"),
    )

    result = await controller.run(spec)

    assert result.snapshot.outcome.value == "completed"
    assert len(kit.selected_servers) == 1
    assert kit.selected_servers[0] == second
    await controller.close()
