---
title: "Test tool errors and input schemas"
description: "An MCP tool can report an expected application error as a normal call result. When schema validation is enabled, M3 can also reject test arguments that do not match the tool's advertised input schema before sending the call."
---

# Test tool errors and input schemas

An MCP tool can report an expected application error as a normal call result.
When schema validation is enabled, M3 can also reject test arguments that do
not match the tool's advertised input schema before sending the call.

## Requirements

Use a Python 3.10+ project prepared with `m3 setup`. Download the example
[`shipping_server.py`](../../../../sdk/examples/docs/servers-stdio/shipping_server.py)
and save it as `shipping_server.py` in the project root. It includes the
shipping schema and a tool that returns an MCP error result.

## Check a tool result and reject invalid input

Save this complete test file as `tests/test_errors_schemas.py`:

```python
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
```

Run from the project root:

```sh
m3 test -- tests/test_errors_schemas.py
```

The first test asserts an MCP result with `is_error=True`; the call itself
returns normally. Schema mismatch raises `ModelValidationError` because
`validate_schemas=True`. Without that option, the client does not perform
this local input-schema check. A server can still reject input for its own
reasons. Continue to [test a tool contract](tools.md).
