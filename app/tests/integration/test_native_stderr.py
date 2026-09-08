"""Native harness regressions for bounded diagnostics and pipe draining."""

import asyncio
import os
import stat
import sys
import time
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
@pytest.mark.parametrize("iteration", range(50))
def test_native_repeated_cleanup_reaps_owned_children(
    tmp_path: Path, kind: str, outcome: str, iteration: int
) -> None:
    pid_file = tmp_path / f"{kind}-{outcome}-{iteration}.pid"
    body = f"""
import os,time
open({str(pid_file)!r}, "w").write(str(os.getpid()))
time.sleep(30)
"""
    executable_path = executable(tmp_path / f"{kind}-{outcome}.py", body)

    async def run_once(timeout_seconds: float) -> str | None:
        runner: Any = ClaudeCodeRunner(executable_path) if kind == "claude" else OpenCodeRunner(executable_path)
        cancel_event: asyncio.Event | None = asyncio.Event() if outcome == "cancelled" else None
        task = asyncio.create_task(
            runner.run(_spec(timeout_seconds=timeout_seconds), cancel_event=cancel_event)
        )
        # Process scheduling can be delayed while the complete cleanup matrix
        # is distributed across two workers; bound startup independently from
        # the runner's 0.75s/2s operation timeout.
        deadline = time.monotonic() + 15
        while not pid_file.exists() and not task.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.001)
        if not pid_file.exists():
            # Under xdist load a short operation timeout can elapse before the
            # subprocess scheduler runs.  Retry this iteration once with the
            # normal two-second budget so each parametrized case still checks
            # a real child cleanup rather than accepting a scheduler miss.
            await asyncio.wait_for(task, timeout=10)
            return None
        if outcome == "cancelled":
            assert cancel_event is not None
            cancel_event.set()
        result = await asyncio.wait_for(task, timeout=10)
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError(f"native child {pid} survived {outcome} cleanup")
        pid_file.unlink(missing_ok=True)
        return result.status

    initial_timeout = 0.75 if outcome == "timed_out" else 2
    status = asyncio.run(run_once(initial_timeout))
    if status is None:
        pid_file.unlink(missing_ok=True)
        status = asyncio.run(run_once(2))
    expected = "cancelled" if outcome == "cancelled" else "timed_out"
    assert status == expected
