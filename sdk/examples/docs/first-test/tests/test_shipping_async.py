import sys
from pathlib import Path

import pytest

from m3.async_api import AsyncMCPTestKit
from m3.types import StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping")


@pytest.mark.asyncio
async def test_shipping_async() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    async with AsyncMCPTestKit(env={}) as kit:
        async with kit.direct(server) as client:
            result = await client.call_tool(
                "shipping_quote", {"weight_kg": 2, "zone": "local"}
            )
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
