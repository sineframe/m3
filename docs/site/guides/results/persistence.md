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
failed or errored cases plus collection errors. `summary.test_outcome_counts`
and `summary.effective_verdict_counts` include `xfailed` beside `skipped`, and
`summary.xfailed_tests` counts expected failures. `summary.skipped_tests`
counts only genuine skips. `evaluation_stats` groups
explicit saved evaluations by name.

Each `tests[]` entry has `node_id`, `outcome`, `verdict`,
`effective_verdict`, `tool_result`, and `execution_ids`. `outcome` is the
pytest case outcome. `verdict` distinguishes assertion, protocol, setup,
teardown, and other results. `effective_verdict` applies the required-
evaluation policy without relabeling `outcome`. All three are `xfailed` for a
pytest expected failure: the test ran and failed as expected. This differs
from `skipped`, where the test did not run. An xfailed test is not a failure
and does not need attention. A test with an xfail marker also has a top-level
`xfail_reason`, which is `""` for a bare `@pytest.mark.xfail`. Runs saved
before this field existed report skipped xfail tests as `xfailed`.

The `failures[]` array includes failed pytest cases copied from `tests[]`.
Those entries include `node_id`, `outcome`, `verdict`, `effective_verdict`,
and `execution_ids`; they do not have `kind` or `execution_id`. Required
evaluation failures may be attached to a failed case under `evaluations[]`.
When they appear as separate entries, they have `kind: "evaluation"`,
`evaluation_id`, `execution_id`, `evaluator`, `case_id`, and `status`, plus
evaluation details such as `required`, `score`, `rationale`, and `message`.
Detached evaluation entries have `execution_id: null`. Collection and worker
failures have `kind: "collection"` or `kind: "worker"` plus fields from their
manifest reports; persistence failures have `kind: "persistence"` and
`message`. These sources do not necessarily have `node_id`, `verdict`, or
`execution_ids`, so inspect each entry's source-specific fields rather than
filtering on one set of fields. An `executions[].outcome` value of `completed`
describes lifecycle only.

File maps are relative to the report directory. Each maps an ID to a file
path; a map is `{}` when the run has nothing of that kind:

| Map | Key | File holds |
| --- | --- | --- |
| `execution_files` | execution ID | The execution report (`executions/`). |
| `spec_files` | execution ID | The execution spec, when one was recorded (`specs/`). |
| `catalog_files` | execution ID | Tool-catalog versions observed through `tools/list` (`catalogs/`). |
| `trace_files` | execution ID | The trace view (`traces/`). |
| `evidence_files` | evidence ID | Raw captured evidence referenced by trace events; truncated captures are listed in `unavailable_references` instead (`evidence/`). |
| `artifact_files` | artifact ID | The bytes of each artifact recorded on the execution (`artifacts/`). |
| `diagnostic_files` | attempt ID | Diagnostics attached to a test result (`diagnostics/`). |
| `test_run_files` | run ID | The saved test-run manifest for this run and its baseline run, if any (`diagnostics/`). |
| `test_result_files` | attempt ID | One saved test result per attempt (`diagnostics/`). |

`unavailable_references` lists references that could not be included.

When a run sets `M3_TIMINGS=1`, `.m3/reports/<run-id>/` also has a `timings/`
folder with `summary.json`, `trace.json`, and the raw per-process records. The
bundle does not reference it. See [Find slow steps in a test run](timings.md).

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
