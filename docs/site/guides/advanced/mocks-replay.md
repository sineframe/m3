# Use mocks and replay

Use a mock to test client behavior against a controlled MCP exchange.

Save as `tests/test_mock_shipping.py`:

```python
import pytest

from m3 import MCPTestKit
from m3.testing import MockMCPServer

pytestmark = pytest.mark.m3(suite_name="shipping")


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
```

Run:

```sh
uv run pytest tests/test_mock_shipping.py
```

Unexpected requests raise `MockExpectationError`.

Use `Recording` and `ReplayServer` when the exact recorded interaction is part
of the test contract. Replay divergence raises `ReplayMismatch`.

Mocks prove behavior against the declared fixture. They do not prove that a
deployed server or agent provider currently behaves the same way. Keep one
direct integration test for the boundary the mock replaces.

See the [testing utility reference](/reference/python/m3/testing) for those
object contracts.
