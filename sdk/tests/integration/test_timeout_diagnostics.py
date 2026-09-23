from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.process_lifecycle


def test_cli_execution_timeout_persists_partial_trace_and_feedback(
    tmp_path: Path,
) -> None:
    barrier = tmp_path / "prompt-entered"
    acp = tmp_path / "stalling_acp.py"
    acp.write_text(
        "\n".join(
            (
                "import json, pathlib, sys, time",
                f"barrier = pathlib.Path({str(barrier)!r})",
                "for line in sys.stdin:",
                "    request = json.loads(line)",
                "    method = request.get('method')",
                "    identifier = request.get('id')",
                "    if method == 'initialize':",
                "        print(json.dumps({'jsonrpc':'2.0','id':identifier,'result':{'protocolVersion':1}}), flush=True)",
                "    elif method == 'session/new':",
                "        print(json.dumps({'jsonrpc':'2.0','id':identifier,'result':{'sessionId':'stall'}}), flush=True)",
                "    elif method == 'session/prompt':",
                "        barrier.write_text('entered')",
                "        time.sleep(30)",
            )
        ),
        encoding="utf-8",
    )
    test_file = tmp_path / "test_timeout.py"
    test_file.write_text(
        f"""import sys\nimport pytest\nfrom m3 import StdioServer\n\npytestmark = pytest.mark.m3(agents=[{{"harness": "acp", "models": ["fixture"], "manifest": {{"command": sys.executable, "args": [{str(acp)!r}], "protocol": "acp", "protocol_version": 1}}}}])\n\ndef test_stalls(agent):\n    result = agent.run("stall", server=StdioServer(name="unused", command="echo"))\n    assert result.snapshot.outcome.value == "completed"\n""",
        encoding="utf-8",
    )
    database = tmp_path / "results.sqlite"
    execution_timeout = 3.0
    workspace = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(workspace.parent / "cli" / "src"), str(workspace / "src"))
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
        "--execution-timeout",
        str(execution_timeout),
        "--",
        str(test_file),
    ]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert barrier.exists(), f"CLI output:\n{result.stdout}\n{result.stderr}"
    assert barrier.read_text(encoding="utf-8") == "entered"
    assert result.returncode != 0
    assert "M3 execution timeout:" in result.stdout
    assert "stage=waiting_for_harness_response" in result.stdout
    assert "feedback=" in result.stdout
    reports = list((tmp_path / ".m3" / "reports").glob("*/feedback.json"))
    assert reports
    feedback = json.loads(reports[0].read_text(encoding="utf-8"))
    execution_files = list(reports[0].parent.glob("executions/*.json"))
    trace_files = list(reports[0].parent.glob("traces/*.json"))
    assert execution_files and trace_files
    execution = json.loads(execution_files[0].read_text(encoding="utf-8"))
    trace = json.loads(trace_files[0].read_text(encoding="utf-8"))
    assert feedback["executions"]
    assert execution["snapshot"]["outcome"] == "timed_out"
    diagnostics = [
        item for item in trace["timeline"] if item.get("kind") == "diagnostic"
    ]
    startup_started = next(
        index
        for index, item in enumerate(diagnostics)
        if item.get("code") == "stage_started"
        and item.get("stage") == "waiting_for_harness_response"
    )
    timeout_index = next(
        index
        for index, item in enumerate(diagnostics)
        if item.get("code") == "operation_timeout"
    )
    assert startup_started < timeout_index
    assert diagnostics[startup_started]["operation"] == "harness.response"
    assert any(
        item["code"] == "stage_started" and item["stage"] == "harness_startup"
        for item in diagnostics
    )
    assert any(
        item["code"] == "stage_completed" and item["stage"] == "harness_startup"
        for item in diagnostics
    )
    timeout = next(item for item in diagnostics if item["code"] == "operation_timeout")
    assert timeout["stage"] == "waiting_for_harness_response"
    assert timeout["operation"] == "harness.response"
    assert trace["completeness"] == "partial"
    spec_files = feedback["spec_files"]
    assert spec_files
    spec = json.loads(
        (reports[0].parent / next(iter(spec_files.values()))).read_text(
            encoding="utf-8"
        )
    )
    assert spec["timeout_seconds"] == execution_timeout
