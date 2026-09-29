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
