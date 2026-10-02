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
                permission_policy="allow",
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
