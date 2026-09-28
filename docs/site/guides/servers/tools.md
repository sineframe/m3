# Test MCP tools

Test a tool in two steps: discover its advertised schema, then call it with
known arguments and check the result that matters to your application.

## Requirements

Prepare a Python 3.10+ project with `m3 setup`. The
[stdio example project](https://github.com/sineframe/m3/tree/main/sdk/examples/docs/servers-stdio) supplies
the local shipping server used here. It starts without external services or
credentials.

## Discover and call the shipping tool

This complete test belongs in `tests/test_tools.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping-direct")


def test_discover_and_call_shipping_quote() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        tools = client.list_all_tools()
        shipping = next(tool for tool in tools if tool.name == "shipping_quote")
        assert list(shipping.input_schema["required"]) == ["weight_kg", "zone"]
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

Run it from the example project root:

```sh
m3 test -- tests/test_tools.py
```

`list_all_tools()` follows pagination until the server has no next page. The
test checks that the server advertises both required inputs and returns the
expected structured quote. A non-error result by itself would not verify the
quote contract.

For invalid inputs and expected tool errors, continue to [test errors and
schemas](/guides/servers/errors-schemas). For the server command setup, see
[stdio servers](/guides/servers/stdio).
