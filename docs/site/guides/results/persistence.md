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
The CLI prints `M3 feedback: .m3/reports/RUN_ID/feedback.json`. The bundle's
top-level fields include `summary`, `limitations`, and `evaluation_stats`.
`summary.executions` counts executions, while `summary.failures` counts
failed or errored cases plus collection errors. `evaluation_stats` groups
explicit saved evaluations by name.

Each `tests[]` entry has `node_id`, `outcome`, `verdict`,
`effective_verdict`, `tool_result`, and `execution_ids`. `outcome` is the
pytest case outcome. `verdict` distinguishes assertion, protocol, setup,
teardown, and other results. `effective_verdict` applies the required-
evaluation policy without relabeling `outcome`. Each `failures[]` entry has
`kind`, `node_id`, `verdict`, `evaluator`, `status`, and `execution_id`.
An `executions[].outcome` value of `completed` describes lifecycle only.

File maps are relative to the report directory: `trace_files`,
`execution_files`, `spec_files`, `catalog_files`, `diagnostic_files`,
`test_run_files`, and `test_result_files`. `unavailable_references` lists
references that could not be included.

Trace `timeline[]` entries use these shapes:

- `tool_call`: `server_binding`, `status`, `tool`, `arguments`, `result`,
  and `provenance`
- `diagnostic`: `code`, `status`, `stage`, `operation`, `elapsed_seconds`,
  `timeout_seconds`, `message`, and `limitations`

With the plugin, pytest outcomes are saved as run records and M3 matcher
checks as execution evaluations. Other Python assertion results and aggregate
summary rows are not saved. Add `.m3/` to the project's Git ignore rules.

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
