"""Native harness regressions for bounded diagnostics and pipe draining."""

import asyncio
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from mcp_pal.harness.base import RunSpec
from mcp_pal_app.harness_claude_cli import ClaudeCodeRunner
from mcp_pal_app.harness_opencode_cli import OpenCodeRunner


def executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _spec(*, timeout_seconds: float = 2) -> RunSpec:
    return RunSpec(
        "native prompt",
        "native model",
        {"mcpServers": {"draw": {"command": "echo"}}},
        "draw",
        timeout_seconds=timeout_seconds,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("kind", ["claude", "opencode"])
def test_native_stderr_above_limits_does_not_deadlock(tmp_path: Path, kind: str) -> None:
    if kind == "claude":
        body = """
import json,sys
sys.stderr.write("diagnostic-start-" + ("X" * 300000)); sys.stderr.flush()
assert sys.stdin.read() == "native prompt"
print(json.dumps({"type":"result","result":"ready","session_id":"s","num_turns":1}), flush=True)
"""
        runner: Any = ClaudeCodeRunner(executable(tmp_path / "claude.py", body))
    else:
        body = """
import json,sys
sys.stderr.write("diagnostic-start-" + ("X" * 300000)); sys.stderr.flush()
assert sys.stdin.read() == "native prompt"
print(json.dumps({"type":"text","sessionID":"s","part":{"type":"text","text":"ready"}}), flush=True)
print(json.dumps({"type":"step_finish","sessionID":"s","part":{"type":"step-finish"}}), flush=True)
"""
        runner = OpenCodeRunner(executable(tmp_path / "opencode.py", body))

    result = asyncio.run(runner.run(_spec()))

    assert result.status == "completed"
    assert result.stderr.startswith("diagnostic-start-")
    assert len(result.stderr.encode("utf-8")) <= 64 * 1024


@pytest.mark.parametrize("kind", ["claude", "opencode"])
@pytest.mark.parametrize("outcome", ["cancelled", "timed_out"])
def test_native_repeated_cleanup_reaps_owned_children(
    tmp_path: Path, kind: str, outcome: str
) -> None:
    pid_file = tmp_path / f"{kind}-{outcome}.pid"
    body = f"""
import os,time
open({str(pid_file)!r}, "w").write(str(os.getpid()))
time.sleep(30)
"""
    executable_path = executable(tmp_path / f"{kind}-{outcome}.py", body)

    async def run_repetitions() -> list[str]:
        statuses: list[str] = []
        for _ in range(50):
            runner: Any = ClaudeCodeRunner(executable_path) if kind == "claude" else OpenCodeRunner(executable_path)
            cancel_event: asyncio.Event | None = asyncio.Event() if outcome == "cancelled" else None
            timeout_seconds = 0.75 if outcome == "timed_out" else 2
            task = asyncio.create_task(
                runner.run(_spec(timeout_seconds=timeout_seconds), cancel_event=cancel_event)
            )
            for _ in range(2000):
                if pid_file.exists():
                    break
                await asyncio.sleep(0.001)
            assert pid_file.exists(), "native fixture did not start"
            if outcome == "cancelled":
                assert cancel_event is not None
                cancel_event.set()
            result = await task
            statuses.append(result.status)
            pid = int(pid_file.read_text(encoding="utf-8"))
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError(f"native child {pid} survived {outcome} cleanup")
            pid_file.unlink(missing_ok=True)
        return statuses

    statuses = asyncio.run(run_repetitions())
    expected = "cancelled" if outcome == "cancelled" else "timed_out"
    assert statuses == [expected] * 50
