# Submit input to a paused execution

Managed input lets an agent execution pause while it waits for a person’s response. The caller reads the persisted request, submits a response keyed by the request name, then waits for the worker to finish.

## Requirements

This example uses Codex CLI `0.156.1` and a model available to your Codex login. That adapter supports action-bound elicitation in the tested version. Set `M3_DOCS_CODEX_MODEL`, install M3 with pytest, and review Codex’s tool approval prompt. The run uses a SQLite execution store because managed input needs a persistent store that implements M3’s managed-input API. Provider access and managed runtime acquisition require network access.

The companion `shipping_server.py` in the [runnable project](https://github.com/sineframe/m3/tree/main/sdk/examples/docs/elicitation-managed-input) implements `book_shipment`: its first call requests the `shipping_address` form, and its next call returns a booked result only when that keyed response matches. The project includes both this server and the test below.

## Pause, read, submit, and wait

Save the following complete test as `test_managed_input.py` beside `shipping_server.py`:

```python
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from m3 import ElicitationResponse, ExecutionOutcome, MCPTestKit, expect
from m3.storage import SQLiteExecutionStore
from m3.types import ExecutionStatus, StdioServer

HERE = Path(__file__).resolve().parent


def test_managed_form_input_resumes_codex_execution(tmp_path: Path) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    try:
        with MCPTestKit(store=store, env={}) as kit:
            agent = kit.agents(
                [
                    {
                        "harness": "codex",
                        "models": [os.environ["M3_DOCS_CODEX_MODEL"]],
                        "runtime": "managed",
                        "version": "0.156.1",
                    }
                ]
            )[0]
            handle = agent.submit(
                "Use shipping:book_shipment once for a 2 kg parcel. Ask me for the "
                "address if needed, then report whether the shipment was booked.",
                server=server,
                tools=["shipping:book_shipment"],
                timeout=120,
                human_input="managed",
            )

            deadline = time.monotonic() + 120
            pending = handle.pending_elicitation()
            while pending is None and time.monotonic() < deadline:
                snapshot = handle.snapshot()
                if snapshot.lifecycle is ExecutionStatus.FINISHED:
                    raise AssertionError(
                        f"execution finished before input was pending: {snapshot.outcome}"
                    )
                time.sleep(0.2)
                pending = handle.pending_elicitation()

            assert pending is not None, "execution did not request managed input"
            assert set(pending.requests) == {"shipping_address"}
            handle.respond_elicitation(
                pending.round_id,
                {
                    "shipping_address": ElicitationResponse(
                        action="accept",
                        content={"street": "1 Main Street", "city": "Pune"},
                    )
                },
                idempotency_key=f"docs-response-{pending.round_id}",
            )
            result = handle.result(timeout=120)

        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
        expect(result).to_have_tool_call(
            "book_shipment",
            server="shipping",
            status="success",
            count=1,
        )
    finally:
        store.close()
```

Run it from the project directory with `python -m pytest -q test_managed_input.py`. The test waits up to two minutes for the form request. It then submits the response using the request key and round ID returned by the execution, and checks that the worker completes the tool call. It never relies on an author’s execution ID.

The submission idempotency key is derived from the reader’s round ID. In an application, persist the key with the submitted response so a retried submission reuses both. This test handles form requests only. A URL request needs an explicit consent flow and a response without form content.

Managed input cannot be combined with a predefined elicitation plan on the same execution. Direct SDK elicitation does not need an agent harness; agent-driven elicitation uses the harness capability described in [the elicitation guide](/guides/elicitation/plans). Next: read the [elicitation API reference](/reference/python/m3/elicitation) and [execution lifecycle](/concepts/lifecycle).
