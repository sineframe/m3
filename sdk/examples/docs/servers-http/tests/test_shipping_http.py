import pytest

from m3 import MCPTestKit
from m3.types import HTTPServer, TrustLevel

pytestmark = pytest.mark.m3(suite_name="shipping-http")


def test_shipping_quote_over_http() -> None:
    server = HTTPServer(
        name="shipping",
        url="http://127.0.0.1:8765/mcp/",
        trust=TrustLevel.TRUSTED_PRIVATE,
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        assert "shipping_quote" in {tool.name for tool in client.list_all_tools()}
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
