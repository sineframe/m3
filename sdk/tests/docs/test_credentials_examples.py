"""Run the complete credential example outside the source checkout."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[3]


@pytest.mark.process_lifecycle
def test_complete_credentials_project_runs_from_clean_copy(tmp_path: Path) -> None:
    source = _ROOT / "sdk/examples/docs/credentials"
    manifest = json.loads((source / "example.json").read_text(encoding="utf-8"))
    project = tmp_path / "credentials"
    project.mkdir()
    for relative in manifest["files"]:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    process = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q", "test_credentials.py"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(f"credential example timed out\n{stdout}{stderr}")
    assert process.returncode == 0, stdout + stderr
