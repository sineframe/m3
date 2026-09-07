"""Mechanical sync/async public-surface parity contracts."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[2]
CHECKER = ROOT / "scripts" / "check_sync_async_parity.py"
GENERATOR = ROOT / "scripts" / "generate_sync_async_parity.py"
MANIFEST = ROOT / "parity" / "sync_async_parity.json"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_sync_async_parity_checker_passes() -> None:
    result = _run(CHECKER)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "parity check passed" in result.stdout


def test_sync_async_parity_manifest_is_generator_normalize() -> None:
    result = _run(GENERATOR, "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_one_sided_export_requires_an_explicit_manifest_decision(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["module_exports"]["async_pending"].append("NewAsyncResult")
    altered = tmp_path / "parity.json"
    altered.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run(CHECKER, "--manifest", str(altered))

    assert result.returncode != 0
    assert "missing pending async export" in result.stderr


def test_manifest_records_lifecycle_and_result_alias_mappings() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    direct_key = "mcp_pal.sync_api.DirectClient:mcp_pal.async_api.AsyncDirectClient"
    mappings = {tuple(pair) for pair in manifest["owner_members"][direct_key]["paired"]}
    assert ("aclose", "close") in mappings
    assert ("call_tool", "call_tool") in mappings
    assert [
        "mcp_pal.sync_api",
        "InitializationResult",
        "mcp_pal.async_api",
        "InitializeResult",
    ] in manifest["module_exports"]["paired"]
