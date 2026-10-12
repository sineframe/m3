"""Server definitions shared by every task and the baseline."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

from m3.types import StdioServer

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "_server"
CONTROL_SCRIPT = ROOT / "_control" / "server.py"

CONTROL = StdioServer(
    name="control",
    command=sys.executable,
    args=(str(CONTROL_SCRIPT),),
    cwd=str(CONTROL_SCRIPT.parent),
)


def tracker_server(variant: str, nonce: str) -> StdioServer:
    """The single MCP server every task binds: the tracker, in one surface variant."""
    environment = {}
    if os.environ.get("MT_CALL_LOG"):
        environment["MT_CALL_LOG"] = os.environ["MT_CALL_LOG"]
    return StdioServer(
        name="tracker",
        command=sys.executable,
        args=(str(SERVER_DIR / "server.py"), "--variant", variant, "--nonce", nonce),
        cwd=str(SERVER_DIR),
        environment=environment,
    )


def load_task(task_dir: Path) -> dict[str, Any]:
    with (Path(task_dir) / "task.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)
