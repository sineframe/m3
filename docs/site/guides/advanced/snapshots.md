---
title: "Snapshot stable values"
description: "Use snapshot for a public value whose full stable projection is meaningful to review. Prefer field assertions when only a few behaviors matter."
---

# Snapshot stable values

Use `snapshot` for a public value whose full stable projection is meaningful to
review. Prefer field assertions when only a few behaviors matter.

Start from the first-test project and save this as
`tests/test_shipping_snapshot.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer
from m3.snapshots import snapshot

pytestmark = pytest.mark.m3(suite_name="shipping")


def test_shipping_snapshot() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    captured = snapshot(result)
    assert captured["is_error"] is False
    assert captured["structured_content"] == {
        "amount": 9.0,
        "currency": "USD",
    }
```

Run from the first-test project:

```sh
m3 test -- tests/test_shipping_snapshot.py
```

The expected snapshot must come from a reviewed test result, not hand-written
guesses. Keep volatile identity, path, timing, and secret-bearing data out of
the comparison through supported snapshot options or narrower assertions.
