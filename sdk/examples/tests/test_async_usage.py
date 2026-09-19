"""Use the async SDK without crossing sync/async boundaries in application tests."""

import pytest

from m3 import StdioServer
from m3.async_api import AsyncMCPTestKit
from m3.sync_api import ToolCallResult


@pytest.mark.asyncio
async def test_call_a_tool_asynchronously(example_server: StdioServer) -> None:
    async with AsyncMCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        result = await client.call_tool(
            "shipping_quote",
            {"weight_kg": 3, "zone": "regional"},
        )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"amount": 15.5, "currency": "USD"}
