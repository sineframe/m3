import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, ModelValidationError, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping-direct")


def test_assert_a_tool_error_result() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("always_fails")

    assert result.is_error is True
    assert result.content[0]["text"] == "expected example failure"


def test_validate_schema_and_reject_invalid_arguments() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server, validate_schemas=True) as client:
        valid = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
        assert valid.structured_content == {"amount": 9.0, "currency": "USD"}
        with pytest.raises(
            ModelValidationError, match="tool JSON Schema validation failed"
        ):
            client.call_tool("shipping_quote", {"weight_kg": -1, "zone": "local"})
