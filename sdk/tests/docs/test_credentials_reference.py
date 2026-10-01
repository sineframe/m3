"""Execute the public SecretReference value shown in its reference page."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from m3.types import SecretReference

_ROOT = Path(__file__).parents[3]


def test_credentials_reference_constructs_the_public_value() -> None:
    page = (_ROOT / "docs/site/reference/credentials.md").read_text(encoding="utf-8")
    match = re.search(r"```python\n(.*?)\n```", page, flags=re.DOTALL)
    assert match is not None
    namespace: dict[str, object] = {}
    exec(compile(match.group(1), "reference/credentials.md", "exec"), namespace)
    value = namespace["endpoint_key"]
    assert isinstance(value, SecretReference)
    assert value.source == "environment"
    assert value.name == "MCP_ENDPOINT_KEY"


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
    assert "3 passed" in stdout
