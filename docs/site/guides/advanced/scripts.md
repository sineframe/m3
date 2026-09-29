---
title: "Run M3 without pytest"
description: "The SDK can run in a normal Python program. Persistence is in memory unless you select a store."
---

# Run M3 without pytest

The SDK can run in a normal Python program. Persistence is in memory unless you
select a store.

```python
import sys
from pathlib import Path

from m3 import MCPTestKit, StdioServer

HERE = Path(__file__).resolve().parent

server = StdioServer(
    name="shipping",
    command=sys.executable,
    args=(str(HERE / "shipping_server.py"),),
    cwd=str(HERE),
)


def main():
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}


if __name__ == "__main__":
    main()
```

Save it beside the first tutorial's `shipping_server.py`, then run:

```sh
python check_shipping.py
```

Both context managers are required so the subprocess and trace finalize.
