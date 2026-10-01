"""Verify the documented external-agent variation with a local ACP process."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[3]
_PROJECT = _ROOT / "sdk" / "examples" / "docs" / "acp-connect"


def test_external_manifest_variation_runs_matching_local_agent(tmp_path: Path) -> None:
    manifest = json.loads((_PROJECT / "example.json").read_text(encoding="utf-8"))
    project = tmp_path / "acp-connect"
    project.mkdir()
    for relative in manifest["files"]:
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_PROJECT / relative, destination)

    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    wrapper = executable_dir / "external-acp-agent"
    wrapper.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    environment = {
        "PATH": os.pathsep.join((str(executable_dir), os.defpath)),
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PYTHONPATH": str(_ROOT / "sdk" / "src"),
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ACP_AGENT_COMMAND": str(wrapper),
        "ACP_AGENT_ARGS": json.dumps([str(project / "deterministic_acp_agent.py")]),
        "ACP_AGENT_CREDENTIAL_ENV": "M3_LOCAL_PROVIDER_KEY",
        "M3_DOCS_AGENT_MODEL": "local-acp-fixture",
        "M3_DOCS_PROVIDER_API_KEY": "local-fixture-placeholder",
    }
    local = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_connect.py"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert local.returncode == 0, local.stdout + local.stderr
    assert "1 passed" in local.stdout
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_connect_external.py"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed" in completed.stdout
