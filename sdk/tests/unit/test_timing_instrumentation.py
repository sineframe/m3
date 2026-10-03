"""SDK timing instrumentation, exercised in a subprocess with M3_TIMINGS=1.

``timed``/``counted`` decorators are applied at import time, so the modules
must be imported with the variable already set.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

PRELUDE = """
import asyncio, json, sys
from pathlib import Path
from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from m3 import _timing
from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.harness import DeterministicHarnessAdapter, HarnessAdapterRegistry
from m3.storage import SQLiteExecutionStore
from m3.sync_api import MCPTestKit
from m3.types import ACPAgent, InProcessServer, ServerBinding, StdioServer

out = Path(sys.argv[1])
_timing.start(out, "unit")


def _server():
    async def list_tools(_c, _p):
        return mcp_types.ListToolsResult(tools=[])

    return Server("timing-fixture", on_list_tools=list_tools)

"""


def _run(tmp_path: Path, body: str) -> list[dict[str, object]]:
    script = PRELUDE + textwrap.dedent(body) + "\n_timing.stop()\n"
    env = {**os.environ, "M3_TIMINGS": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    records: list[dict[str, object]] = []
    for path in tmp_path.glob("*.jsonl"):
        records.extend(json.loads(line) for line in path.read_text().splitlines())
    return records


def _spans(records: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for record in records:
        if record["type"] == "span":
            grouped.setdefault(str(record["name"]), []).append(record)
    return grouped


def _ancestors(span: dict[str, object], by_id: dict[object, dict[str, object]]):
    parent = by_id.get(span["parent"])
    while parent is not None:
        yield parent["name"]
        parent = by_id.get(parent["parent"])


def test_agent_session_spans_nest_under_start_and_close(tmp_path: Path) -> None:
    records = _run(
        tmp_path,
        """
        async def main():
            registry = HarnessAdapterRegistry(
                {"acp": lambda _h: DeterministicHarnessAdapter(name="test-acp")}
            )
            spec = AgentSpec(
                harness=ACPAgent(model="m"),
                servers=(
                    ServerBinding(
                        server=StdioServer(name="first", command="mcp-first"),
                        alias="first",
                    ),
                ),
            )
            async with AsyncMCPTestKit(
                env={}, cwd="/tmp/m3-no-project", adapter_registry=registry
            ) as kit:
                async with kit.agent_session(spec) as session:
                    await session.send("hello")

        asyncio.run(main())
        """,
    )
    spans = _spans(records)
    by_id = {r["id"]: r for r in records if r["type"] == "span"}
    start = spans["session.start"][0]
    assert start["key"] == "test-acp"
    assert "session.start" in set(_ancestors(spans["harness.open"][0], by_id))
    assert spans["session.turn"][0]["key"] == "test-acp"
    assert any(
        "session.close" in set(_ancestors(s, by_id)) for s in spans["trace.finalize"]
    )
    assert "kit.open" in spans and "kit.close" in spans
    assert "workspace.create" in spans


def test_sync_direct_client_keeps_test_scope_through_portal(tmp_path: Path) -> None:
    records = _run(
        tmp_path,
        """
        with _timing.test_scope("t1"):
            with MCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
                with kit.direct(InProcessServer(name="fixture", factory=_server)) as c:
                    c.list_tools()
        """,
    )
    spans = _spans(records)
    requests = spans["mcp.request"]
    assert {"initialize", "tools/list"} <= {r["key"] for r in requests}
    assert all(r["test"] == "t1" for r in requests)
    assert "portal.start" in spans and "portal.close" in spans
    assert all(r["test"] == "t1" for r in spans["server.launch"])


def test_sqlite_store_counters_are_aggregated(tmp_path: Path) -> None:
    records = _run(
        tmp_path,
        """
        store = SQLiteExecutionStore(Path(sys.argv[1]) / "m3.sqlite")
        with _timing.test_scope("t2"):
            with MCPTestKit(env={}, cwd="/tmp/m3-no-project", store=store) as kit:
                with kit.direct(InProcessServer(name="fixture", factory=_server)) as c:
                    c.list_tools()
        """,
    )
    counters = {r["name"] for r in records if r["type"] == "counter"}
    assert {"store.connect", "trace.emit"} <= counters
    assert "store.open" in _spans(records)
