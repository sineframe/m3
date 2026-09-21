from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _local_client import TestClient

from m3.storage import SQLiteExecutionStore
from m3_app.api.app import create_app
from m3_app.settings import Settings


def _run_agent_test(
    tmp_path: Path, database: Path, *, baseline: str | None = None
) -> subprocess.CompletedProcess[str]:
    server = (
        Path(__file__).parents[3]
        / "sdk"
        / "examples"
        / "servers"
        / "example_mcp_server.py"
    )
    agent = (
        Path(__file__).parents[3]
        / "sdk"
        / "examples"
        / "servers"
        / "deterministic_acp_agent.py"
    )
    source = f'''\
import pytest, sys
from m3 import EvaluationDecision, EvaluationStatus, StdioServer, expect
pytestmark = pytest.mark.m3(agents=[{{"harness": "acp", "models": ["fixture-a", "fixture-b"], "manifest": {{"command": sys.executable, "args": [r"{agent}"], "protocol": "acp", "protocol_version": 1}}}}], trials=2)
def test_selected(agent):
    """Checks that the agent selects the shipping quote tool."""
    result = agent.run("Get a local shipping quote.", server=StdioServer(name="example-mcp", command=sys.executable, args=[r"{server}"]))
    expect(result).to_have_tool_call("shipping_quote", server="example-mcp", status="success")
    agent.kit.register_evaluator("fixture.v1", lambda _context: EvaluationDecision(status=EvaluationStatus.PASSED, score=1.0))
    agent.kit.evaluate(result, "fixture.v1")
'''
    test_file = tmp_path / "test_selected.py"
    test_file.write_text(source, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(Path(__file__).parents[3] / "sdk" / "src"),
            str(Path(__file__).parents[3] / "cli" / "src"),
        )
    )
    command = [
        sys.executable,
        "-m",
        "m3_cli",
        "test",
        "--python",
        sys.executable,
        "--results-db",
        str(database),
        "--trials",
        "2",
        "--",
        "-q",
    ]
    if baseline:
        command.extend(("--baseline", baseline))
    command.append(str(test_file))
    return subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def test_cli_agent_run_is_readable_through_every_v2_envelope(tmp_path: Path) -> None:
    database = tmp_path / "agent-v2.sqlite"
    first = _run_agent_test(tmp_path, database)
    assert first.returncode == 0, first.stdout + first.stderr
    store = SQLiteExecutionStore(database)
    baseline = str(store.list_test_runs()[0]["run_id"])
    store.close()
    second = _run_agent_test(tmp_path, database, baseline=baseline)
    assert second.returncode == 0, second.stdout + second.stderr
    store = SQLiteExecutionStore(database)
    runs = [str(item["run_id"]) for item in store.list_test_runs()]
    current = next(item for item in runs if item != baseline)
    execution_ids = [
        str(value)
        for result in store.list_test_results(current)
        for value in result["execution_ids"]
    ]
    store.close()
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        assert len(execution_ids) == 4
        for execution_id in execution_ids:
            report = client.get(f"/api/v2/executions/{execution_id}/report")
            assert report.status_code == 200
            assert len(report.json()["test_results"]) == 1
            test_result = report.json()["test_results"][0]
            assert "test_selected.py::test_selected[" in test_result["node_id"]
            assert test_result["description"] == (
                "Checks that the agent selects the shipping quote tool."
            )
            assert test_result["outcome"] == "passed"
            assert {
                "version",
                "execution_id",
                "spec",
                "report",
                "trace",
            } <= report.json().keys()
        aggregate = client.post(
            "/api/v2/evaluations/aggregate",
            json={
                "group_by": ["metadata.harness_config"],
                "filters": {"evaluator": "fixture.v1"},
            },
        )
        assert aggregate.status_code == 200
        aggregate_body = aggregate.json()
        assert aggregate_body["version"] == "v2"
        assert aggregate_body["aggregate"]["groups"]
        feedback = client.get(
            f"/api/v2/feedback/{current}", params={"baseline_run_id": baseline}
        )
        assert feedback.status_code == 200
        body = feedback.json()
        assert body["version"] == "v2"
        assert body["feedback"]["comparison"]["baseline_run_id"] == baseline
