---
title: "Save and reopen executions"
description: "Direct SDK use keeps data in memory unless you choose a persistent store. The CLI chooses SQLite automatically."
---

# Save and reopen executions

Direct SDK use keeps data in memory unless you choose a persistent store. The
CLI chooses SQLite automatically.

## CLI-managed history

```sh
m3 test -- tests/test_m3_starter.py
```

This writes `.m3/executions.sqlite` and a feedback bundle under
`.m3/reports/<run-id>/feedback.json`. The CLI prints the run ID and report path.

## Direct SDK storage

Install storage support:

```sh
uv add "sf-m3[storage]"
```

Then pass an explicit store:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer
from m3.storage import SQLiteExecutionStore

pytestmark = pytest.mark.m3(suite_name="shipping")


def test_reopen_saved_execution(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    try:
        with MCPTestKit(store=store, env={}) as kit:
            with kit.direct(server) as client:
                client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
            execution_id = client.final_trace.execution_id

        saved = store.get_snapshot(execution_id)
        assert saved is not None
    finally:
        store.close()
```

Run this as `tests/test_persistence.py` in the first-test project. A temporary
database prevents tests from sharing history. Closing a kit does not remove
records from an explicitly selected SQLite store; close the store after its
last query.
