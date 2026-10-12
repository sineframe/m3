"""Launch handoffs reject malformed inputs before a child can be spawned."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from m3.transport._stdio_handoff import StdioHandoff


@pytest.mark.parametrize(
    "update",
    [
        {"command": []},
        {"command": [""]},
        {"command": ["python", "argument\x00suffix"]},
        {"command": ["python", 1]},
        {"environment": {"TOKEN": 1}},
        {"environment": {"BAD=NAME": "value"}},
        {"environment": {"TOKEN": "value\x00suffix"}},
        {"canaries": [False]},
        {"cwd": ""},
        {"cwd": "directory\x00suffix"},
        {"unknown": "value"},
    ],
)
def test_malformed_launch_data_is_rejected(update: dict[str, object]) -> None:
    payload = {"environment": {}, "canaries": [], "command": ["python"], **update}
    with pytest.raises(ValidationError):
        StdioHandoff.model_validate(payload)


def test_private_values_are_absent_from_handoff_repr() -> None:
    handoff = StdioHandoff(
        environment={"TOKEN": "private-value"},
        canaries=("private-value",),
        command=("python", "private-value"),
        cwd="private-value",
    )
    assert "private-value" not in repr(handoff)


def test_environment_only_handoff_preserves_legacy_shape() -> None:
    handoff = StdioHandoff.model_validate(
        {"environment": {"TOKEN": "value"}, "canaries": ["value"]}
    )
    assert handoff.command is None
    assert handoff.model_dump(mode="json", exclude_none=True) == {
        "environment": {"TOKEN": "value"},
        "canaries": ["value"],
    }


def test_invalid_passive_observer_does_not_invalidate_launch() -> None:
    handoff = StdioHandoff.model_validate(
        {"environment": {}, "canaries": [], "command": ["python"], "observer": {}}
    )
    assert handoff.observer is None
    assert handoff.command == ("python",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["legacy", "duplicate_command", "duplicate_cwd", "cwd_only"]
)
async def test_proxy_launch_sources_are_unambiguous_and_one_shot(
    tmp_path: Path, scenario: str
) -> None:
    marker = tmp_path / "child-started"
    program = """import json
import os
import pathlib
import sys

request = json.loads(sys.stdin.readline())
pathlib.Path(sys.argv[1]).touch()
print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": os.environ["TOKEN"]}), flush=True)
"""
    command = [sys.executable, "-c", program, str(marker)]
    payload: dict[str, object] = {
        "environment": {"TOKEN": "handoff-value"},
        "canaries": [],
    }
    handoff = tmp_path / "launch.json"
    arguments = [
        "-m",
        "m3.transport.stdio_proxy",
        "--capture",
        str(tmp_path / "capture.jsonl"),
        "--baseline",
        "0",
        "--env-file",
        str(handoff),
    ]
    if scenario == "legacy":
        arguments.extend(["--", *command])
    elif scenario == "duplicate_command":
        payload["command"] = command
        arguments.extend(["--", *command])
    elif scenario == "duplicate_cwd":
        payload["command"] = command
        arguments.extend(["--cwd", str(tmp_path)])
    else:
        payload["cwd"] = str(tmp_path)
        arguments.extend(["--", *command])
    handoff.write_text(json.dumps(payload))
    handoff.chmod(0o600)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
    )
    try:
        if scenario == "legacy":
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
            await process.stdin.drain()
            response = await asyncio.wait_for(process.stdout.readline(), timeout=5)
            assert json.loads(response)["result"] == "handoff-value"
            process.stdin.close()
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        assert process.returncode == (0 if scenario == "legacy" else 78), (
            stderr.decode()
        )
        assert marker.exists() is (scenario == "legacy")
        assert not handoff.exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
