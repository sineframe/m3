"""Complete sync and async examples for the public direct MCP client surface."""

from __future__ import annotations

import pytest
from mcp import types
from mcp.shared.exceptions import MCPDeprecationWarning
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.sync_api import PromptResult, ResourceReadResult, ToolCallResult

from mcp_pal import MCPTestKit, StdioServer, UnsupportedFeature


def _progress_notification() -> types.ProgressNotification:
    return types.ProgressNotification(
        params=types.ProgressNotificationParams(
            progress_token="catalog",
            progress=2,
            total=2,
            message="generic notification",
        )
    )


def test_every_sync_direct_client_operation(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        initialization = client.initialize()
        assert initialization.server_info["name"] == "mcp-pal-example-server"

        first_tools = client.list_tools()
        assert first_tools.next_cursor == "page-2"
        assert len(first_tools.tools) >= 7
        assert [
            tool.name
            for tool in client.list_tools(cursor=first_tools.next_cursor).tools
        ] == ["catalog_marker"]
        assert len(client.list_all_tools()) >= 8

        resources_page = client.list_resources()
        assert resources_page.resources[0].uri == "memory://testing-guide"
        assert client.list_all_resources() == resources_page.resources

        templates_page = client.list_resource_templates()
        assert (
            templates_page.resource_templates[0].uri_template
            == "memory://orders/{order_id}"
        )
        assert client.list_all_resource_templates() == templates_page.resource_templates

        prompts_page = client.list_prompts()
        assert prompts_page.prompts[0].name == "review_order"
        assert client.list_all_prompts() == prompts_page.prompts

        guide = client.read_resource("memory://testing-guide")
        assert isinstance(guide, ResourceReadResult)
        assert "Discover capabilities" in guide.text
        prompt = client.get_prompt("review_order", {"order_id": "order-007"})
        assert isinstance(prompt, PromptResult)
        assert (
            prompt.messages[0]["content"]["text"]
            == "Review order order-007 for correctness."
        )
        quote = client.call_tool("shipping_quote", {"weight_kg": 1, "zone": "local"})
        assert isinstance(quote, ToolCallResult)
        assert quote.is_error is False

        completion = client.complete(
            types.PromptReference(name="review_order"),
            {"name": "order_id", "value": "order-"},
        )
        assert completion.values == ("order-001", "order-002")
        assert completion.total == 2
        assert completion.has_more is False

        with pytest.warns(MCPDeprecationWarning):
            client.subscribe_resource("memory://testing-guide")
        observations = client.call_tool("session_observations")
        assert isinstance(observations, ToolCallResult)
        assert (
            "memory://testing-guide" in observations.structured_content["subscriptions"]
        )
        with pytest.warns(MCPDeprecationWarning):
            client.unsubscribe_resource("memory://testing-guide")
        client.ping()
        with pytest.warns(MCPDeprecationWarning):
            client.set_logging_level("info")
        with pytest.warns(MCPDeprecationWarning):
            client.send_progress_notification("direct", 1, 1, "complete")
        client.send_notification(_progress_notification())
        with pytest.warns(MCPDeprecationWarning):
            client.send_roots_list_changed()

        observed_result = client.call_tool("session_observations")
        assert isinstance(observed_result, ToolCallResult)
        observed = observed_result.structured_content
        assert observed["logging_level"] == "info"
        assert observed["progress_notifications"] == 2
        assert observed["roots_changed"] == 1
        assert tuple(observed["subscriptions"]) == ()


@pytest.mark.asyncio
async def test_every_async_direct_client_operation(example_server: StdioServer) -> None:
    async with AsyncMCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        initialization = await client.initialize()
        assert initialization.server_info["name"] == "mcp-pal-example-server"

        first_tools = await client.list_tools()
        assert first_tools.next_cursor == "page-2"
        assert [
            tool.name
            for tool in (await client.list_tools(cursor=first_tools.next_cursor)).tools
        ] == ["catalog_marker"]
        assert len(await client.list_all_tools()) >= 8

        resources_page = await client.list_resources()
        assert (await client.list_all_resources()) == resources_page.resources
        templates_page = await client.list_resource_templates()
        assert (
            await client.list_all_resource_templates()
        ) == templates_page.resource_templates
        prompts_page = await client.list_prompts()
        assert (await client.list_all_prompts()) == prompts_page.prompts

        guide = await client.read_resource("memory://testing-guide")
        assert isinstance(guide, ResourceReadResult)
        assert "Discover capabilities" in guide.text
        prompt = await client.get_prompt("review_order", {"order_id": "order-008"})
        assert isinstance(prompt, PromptResult)
        assert prompt.messages
        quote = await client.call_tool(
            "shipping_quote", {"weight_kg": 1, "zone": "local"}
        )
        assert isinstance(quote, ToolCallResult)
        assert quote.is_error is False

        completion = await client.complete(
            types.PromptReference(name="review_order"),
            {"name": "order_id", "value": "order-"},
        )
        assert completion.values == ("order-001", "order-002")

        with pytest.warns(MCPDeprecationWarning):
            await client.subscribe_resource("memory://testing-guide")
        with pytest.warns(MCPDeprecationWarning):
            await client.unsubscribe_resource("memory://testing-guide")
        await client.ping()
        with pytest.warns(MCPDeprecationWarning):
            await client.set_logging_level("warning")
        with pytest.warns(MCPDeprecationWarning):
            await client.send_progress_notification("direct", 1, 1, "complete")
        await client.send_notification(_progress_notification())
        with pytest.warns(MCPDeprecationWarning):
            await client.send_roots_list_changed()

        observed_result = await client.call_tool("session_observations")
        assert isinstance(observed_result, ToolCallResult)
        observed = observed_result.structured_content
        assert observed["logging_level"] == "warning"
        assert observed["progress_notifications"] == 2
        assert observed["roots_changed"] == 1
        assert tuple(observed["subscriptions"]) == ()


def test_post_construction_callback_registration_is_explicitly_unsupported(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:  # noqa: SIM117
        with pytest.raises(UnsupportedFeature, match="callbacks must be supplied"):
            client.register_callbacks(sampling_callback=lambda *_args: None)


@pytest.mark.asyncio
async def test_async_post_construction_callback_registration_is_explicitly_unsupported(
    example_server: StdioServer,
) -> None:
    async with AsyncMCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        with pytest.raises(UnsupportedFeature, match="callbacks must be supplied"):
            client.register_callbacks(sampling_callback=lambda *_args: None)
