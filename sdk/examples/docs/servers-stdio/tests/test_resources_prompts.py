import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping-direct")


def test_read_server_resource() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        resources = client.list_all_resources()
        assert [resource.uri for resource in resources] == ["memory://testing-guide"]
        guide = client.read_resource(resources[0].uri)

    assert "Discover capabilities" in guide.text


def test_get_prompt_with_required_argument() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        prompts = client.list_all_prompts()
        assert [prompt.name for prompt in prompts] == ["review_order"]
        prompt = client.get_prompt(
            "review_order", {"order_id": "order-created-by-reader"}
        )

    assert prompt.description == "Review a stored order"
    assert prompt.messages[0]["content"]["text"] == (
        "Review order order-created-by-reader for correctness."
    )
