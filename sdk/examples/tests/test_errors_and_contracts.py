"""Examples for MCP tool errors, input schemas, and data-driven contracts."""

import pytest
from mcp_pal.sync_api import ToolCallResult

from mcp_pal import MCPTestKit, ModelValidationError, StdioServer


def test_assert_an_expected_tool_error(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        result = client.call_tool("always_fails")

    # MCP tool errors are results, not transport exceptions.
    assert isinstance(result, ToolCallResult)
    assert result.is_error is True
    assert result.content[0]["text"] == "expected example failure"


@pytest.mark.parametrize(
    ("weight_kg", "zone", "expected_amount"),
    [
        pytest.param(1, "local", 7.0, id="local"),
        pytest.param(2, "regional", 12.0, id="regional"),
        pytest.param(0.5, "international", 8.5, id="international"),
    ],
)
def test_shipping_contract(
    example_server: StdioServer,
    weight_kg: float,
    zone: str,
    expected_amount: float,
) -> None:
    with (
        MCPTestKit(env={}) as kit,
        kit.direct(example_server, validate_schemas=True) as client,
    ):
        result = client.call_tool(
            "shipping_quote", {"weight_kg": weight_kg, "zone": zone}
        )

    assert isinstance(result, ToolCallResult)
    assert result.structured_content == {"amount": expected_amount, "currency": "USD"}


def test_reject_arguments_that_do_not_match_the_tool_schema(
    example_server: StdioServer,
) -> None:
    with (  # noqa: SIM117
        MCPTestKit(env={}) as kit,
        kit.direct(example_server, validate_schemas=True) as client,
    ):
        with pytest.raises(
            ModelValidationError, match="tool JSON Schema validation failed"
        ):
            client.call_tool("shipping_quote", {"weight_kg": -1, "zone": "local"})
