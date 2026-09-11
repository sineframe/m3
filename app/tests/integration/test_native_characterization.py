"""Gate 0 contract snapshots for native runners.

These deliberately exercise the public worker/API boundary so future ACP work
cannot accidentally change native command lines, prompt handling, or reports.
"""

import asyncio
import json
import os
import stat
import time
from pathlib import Path

from fastapi.testclient import TestClient

from mcp_pal.harness.base import RunSpec
from mcp_pal_app.api import create_app
from mcp_pal_app.harness_claude_cli import ClaudeCodeRunner
from mcp_pal_app.harness_opencode_cli import OpenCodeRunner, opencode_config
from mcp_pal_app.settings import Settings


def executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def wait_terminal(client, run_id):
    for _ in range(200):
        value = client.get(f"/api/v1/runs/{run_id}").json()
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(0.01)
    raise AssertionError("native worker did not reach a terminal state")


def profile(client):
    return client.post(
        "/api/v1/profiles",
        json={
            "name": "gate0",
            "mcp_json": {"mcpServers": {"draw": {"command": "echo"}}},
        },
    ).json()


def test_claude_worker_contract_snapshot(tmp_path):
    marker = tmp_path / "claude.json"
    exe = executable(
        tmp_path / "claude.py",
        f"""
import json,sys
assert sys.stdin.read() == 'native prompt'
json.dump(sys.argv[1:], open({str(marker)!r}, 'w'))
print(json.dumps({{'type':'system','subtype':'init','mcp_servers':{{'draw':'connected'}}}}))
print(json.dumps({{'type':'result','result':'native result','session_id':'native-session','num_turns':1}}))
""",
    )
    settings = Settings(
        database_path=str(tmp_path / "db.sqlite"),
        anthropic_api_key="present",
        claude_executable=exe,
        claude_model_ids=["native-model"],
    )
    with TestClient(create_app(settings)) as client:
        p = profile(client)
        created = client.post(
            "/api/v1/runs",
            json={
                "model": "native-model",
                "prompt": "native prompt",
                "expected_output": "native result",
                "profile_revision_id": p["current_revision_id"],
                "enabled_server": "draw",
            },
        ).json()
        run = wait_terminal(client, created["id"])
        report = client.get(f"/api/v1/runs/{created['id']}/report").json()
    assert run["status"] == "completed" and run["claude_result"] == "native result"
    assert report["trace"]["schema"] == "claude.v2"
    args = json.loads(marker.read_text())
    assert args[0] == "--print" and "--mcp-config" in args and "--model" in args
    assert "native result" in json.dumps(report)


def test_opencode_worker_contract_snapshot(tmp_path):
    marker = tmp_path / "opencode.json"
    exe = executable(
        tmp_path / "opencode.py",
        f"""
import json,os,sys
assert sys.stdin.read() == 'native prompt'
json.dump(sys.argv[1:], open({str(marker)!r}, 'w'))
print(json.dumps({{'type':'text','sessionID':'native-session','part':{{'type':'text','text':'native result'}}}}))
print(json.dumps({{'type':'step_finish','sessionID':'native-session','part':{{'type':'step-finish'}}}}))
""",
    )
    settings = Settings(
        database_path=str(tmp_path / "db.sqlite"),
        opencode_api_key="present",
        opencode_executable=exe,
        opencode_model_ids=["native-model"],
    )
    with TestClient(create_app(settings)) as client:
        p = profile(client)
        created = client.post(
            "/api/v1/runs",
            json={
                "harness": "opencode",
                "model": "native-model",
                "prompt": "native prompt",
                "expected_output": "native result",
                "profile_revision_id": p["current_revision_id"],
                "enabled_server": "draw",
            },
        ).json()
        run = wait_terminal(client, created["id"])
        report = client.get(f"/api/v1/runs/{created['id']}/report").json()
    assert run["status"] == "completed" and run["claude_result"] == "native result"
    assert report["trace"]["schema"] == "opencode.v2"
    args = json.loads(marker.read_text())
    assert args[:3] == ["--pure", "run", "--format"] and "--model" in args


def test_native_command_and_config_contracts_are_exact(tmp_path):
    spec = RunSpec(
        "prompt",
        "model",
        {
            "mcpServers": {
                "draw": {
                    "command": "echo",
                    "args": ["server.py"],
                    "env": {"TOKEN": "${DRAW_TOKEN}"},
                },
                "other": {"command": "ignored"},
            }
        },
        "draw",
        "mcp_only",
        7,
        4,
        0.25,
    )
    claude = ClaudeCodeRunner("claude").build_command(spec, "/tmp/selected.json")
    assert claude == [
        "claude",
        "--print",
        "--input-format",
        "text",
        "--bare",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--strict-mcp-config",
        "--mcp-config",
        "/tmp/selected.json",
        "--no-session-persistence",
        "--model",
        "model",
        "--max-turns",
        "4",
        "--max-budget-usd",
        "0.25",
        "--tools",
        "",
        "--allowedTools",
        "mcp__draw__*",
    ]
    config = opencode_config(spec.mcp_config, "draw", "mcp_only")
    assert config["mcp"]["draw"] == {
        "type": "local",
        "command": ["echo", "server.py"],
        "enabled": True,
        "environment": {"TOKEN": "{env:DRAW_TOKEN}"},
    }
    assert config["tools"] == {"*": False, "draw_*": True} and config["permission"] == {
        "*": "deny",
        "draw_*": "allow",
    }


def test_native_cancellation_reaps_process_groups(tmp_path):
    pid_files = [tmp_path / "claude.pid", tmp_path / "open.pid"]
    scripts = []
    for index, name in enumerate(("claude", "open")):
        scripts.append(
            executable(
                tmp_path / f"{name}.py",
                f"""
import os,time
open({str(pid_files[index])!r},'w').write(str(os.getpid()))
time.sleep(30)
""",
            )
        )

    async def cancel(runner, path):
        task = asyncio.create_task(
            runner.run(
                RunSpec(
                    "p",
                    "m",
                    {"mcpServers": {"draw": {"command": "echo"}}},
                    "draw",
                    timeout_seconds=30,
                ),
                cancel_event=asyncio.Event(),
            )
        )
        for _ in range(100):
            if path.exists():
                break
            await asyncio.sleep(0.01)
        runner.request_cancel()
        await asyncio.sleep(0.05)
        return await task

    results = [
        asyncio.run(cancel(ClaudeCodeRunner(scripts[0]), pid_files[0])),
        asyncio.run(cancel(OpenCodeRunner(scripts[1]), pid_files[1])),
    ]
    assert all(r.status in {"cancelled", "failed"} for r in results)
    for path in pid_files:
        pid = int(path.read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise AssertionError(f"native subprocess {pid} was not reaped")
