"""Server definitions and per-harness run options shared by every eval."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from m3.types import NativeToolPolicy, StdioServer

ROOT = Path(__file__).resolve().parents[1]
CONTROL_SCRIPT = ROOT / "_control" / "server.py"

CONTROL = StdioServer(
    name="control",
    command=sys.executable,
    args=(str(CONTROL_SCRIPT),),
    cwd=str(CONTROL_SCRIPT.parent),
)

CLAUDE_KINDS = frozenset({"claude_code", "claude-code", "claude"})


def load_quirk(quirk_dir: Path) -> dict[str, Any]:
    with (quirk_dir / "quirk.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def expand_nonce(value: Any, nonce: str | None) -> Any:
    """Resolve synthetic server-only expectations, including nested arguments."""
    if nonce is None:
        return value
    if isinstance(value, dict):
        return {key: expand_nonce(item, nonce) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_nonce(item, nonce) for item in value]
    if isinstance(value, str):
        return value.replace("{nonce}", nonce)
    return value


def quirk_server(
    quirk_dir: Path, script: str, *, args: tuple[str, ...] = ()
) -> StdioServer:
    path = quirk_dir / script
    return StdioServer(
        name="quirk",
        command=sys.executable,
        args=(str(path), *args),
        cwd=str(quirk_dir),
    )


def run_kwargs(agent: Any) -> dict[str, Any]:
    """Extra agent.run options for a two-server execution.

    Under its default policy M3 binds only one MCP server per Claude Code
    execution, and every eval needs two (control and quirk). The `full` native
    policy accepts several servers; it also enables Claude's built-in tools
    and skips permission prompts.
    """
    if agent.harness not in CLAUDE_KINDS:
        return {}
    return {
        "tool_policy": NativeToolPolicy(
            harness="claude-code",
            policy={"mode": "full", "server": "control"},
            nonportable_reason=(
                "quirks matrix binds control and quirk servers in one Claude Code "
                "execution; M3's default Claude Code policy binds one server"
            ),
        )
    }
