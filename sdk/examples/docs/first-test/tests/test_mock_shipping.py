from m3 import MCPTestKit
from m3.testing import MockMCPServer


def test_mock_shipping() -> None:
    server = MockMCPServer(name="shipping-contract")

    @server.tool(
        input_schema={
            "type": "object",
            "properties": {"weight_kg": {"type": "number"}},
            "required": ["weight_kg"],
        }
    )
    def shipping_quote(arguments: dict[str, float]) -> dict[str, object]:
        return {"amount": 5 + arguments["weight_kg"] * 2, "currency": "USD"}

    server.expect_tool_call("shipping_quote", {"weight_kg": 2})
    with MCPTestKit(env={}) as kit, kit.direct(server.in_process()) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2})

    server.verify()
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
