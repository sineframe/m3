"""Discover and exercise an MCP server's resources and prompts."""

from m3 import MCPTestKit, StdioServer
from m3.sync_api import PromptResult, ResourceReadResult


def test_read_a_resource(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        resources = client.list_all_resources()
        assert [resource.uri for resource in resources] == ["memory://testing-guide"]

        guide = client.read_resource(resources[0].uri)

    assert isinstance(guide, ResourceReadResult)
    assert "Discover capabilities" in guide.text


def test_render_a_prompt_with_arguments(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        prompts = client.list_all_prompts()
        assert [prompt.name for prompt in prompts] == ["review_order"]

        prompt = client.get_prompt("review_order", {"order_id": "order-042"})

    assert isinstance(prompt, PromptResult)
    assert prompt.description == "Review a stored order"
    assert (
        prompt.messages[0]["content"]["text"]
        == "Review order order-042 for correctness."
    )
