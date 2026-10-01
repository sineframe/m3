"""Verify the documented external-agent variation with a local ACP process."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[3]
_PROJECT = _ROOT / "sdk" / "examples" / "docs" / "acp-connect"


def _copy_project(project_id: str, destination: Path) -> Path:
    source = _ROOT / "sdk" / "examples" / "docs" / project_id
    manifest = json.loads((source / "example.json").read_text(encoding="utf-8"))
    destination.mkdir()
    for relative in manifest["files"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    return destination


def _run_project_test(
    project: Path, test_file: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "pytest", "-q", test_file]
    process = subprocess.Popen(
        command,
        cwd=project,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
        raise TimeoutError(
            f"{test_file} exceeded 90 seconds\n{stdout}{stderr}"
        ) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def test_external_manifest_variation_runs_matching_local_agent(tmp_path: Path) -> None:
    project = _copy_project("acp-connect", tmp_path / "acp-connect")

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
    local = _run_project_test(project, "test_connect.py", environment)
    assert local.returncode == 0, local.stdout + local.stderr
    assert "1 passed" in local.stdout
    completed = _run_project_test(project, "test_connect_external.py", environment)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed" in completed.stdout


def test_wrapper_project_runs_from_clean_copy(tmp_path: Path) -> None:
    project = _copy_project("acp-wrapper", tmp_path / "acp-wrapper")
    home = tmp_path / "wrapper-home"
    home.mkdir()
    environment = {
        "PATH": os.defpath,
        "HOME": str(home),
        "PYTHONPATH": str(_ROOT / "sdk" / "src"),
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    completed = _run_project_test(project, "test_wrapper.py", environment)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed" in completed.stdout
