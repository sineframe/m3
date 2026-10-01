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
from runpy import run_path

import pytest
from acp.schema import InitializeResponse, McpServerStdio, NewSessionResponse

pytestmark = pytest.mark.process_lifecycle

_ROOT = Path(__file__).parents[3]
_PROJECT = _ROOT / "sdk" / "examples" / "docs" / "acp-connect"


@pytest.mark.asyncio
async def test_wrapper_returns_typed_initialization_and_session_responses(tmp_path):
    source = _ROOT / "sdk/examples/docs/acp-wrapper/wrapped_agent.py"
    agent = run_path(str(source))["WrappedAgent"]()
    initialized = await agent.initialize(1)
    assert isinstance(initialized, InitializeResponse)
    wire = initialized.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert wire["agentInfo"]["name"] == "wrapped-agent"
    assert "stdio" not in wire["agentCapabilities"]["mcpCapabilities"]
    session = await agent.new_session(
        str(tmp_path),
        [McpServerStdio(name="echo", command=sys.executable, args=[], env=[])],
    )
    assert isinstance(session, NewSessionResponse)
    assert session.session_id
    assert "agentInfo" not in session.model_dump(by_alias=True)


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


@pytest.mark.parametrize(
    "source_value,expected_success",
    [("local-fixture-placeholder", True), ("wrong-credential", False)],
)
def test_external_manifest_forwards_credential_to_agent(
    tmp_path: Path, source_value: str, expected_success: bool
) -> None:
    project = _copy_project("acp-connect", tmp_path / "acp-connect")

    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    wrapper = executable_dir / "external-acp-agent"
    wrapper.write_text(
        "#!/bin/sh\n"
        '[ "${M3_LOCAL_PROVIDER_KEY:-}" = "local-fixture-placeholder" ] || exit 23\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n',
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
        "M3_DOCS_PROVIDER_API_KEY": source_value,
    }
    local = _run_project_test(project, "test_connect.py", environment)
    assert local.returncode == 0, local.stdout + local.stderr
    completed = _run_project_test(project, "test_connect_external.py", environment)
    if expected_success:
        assert completed.returncode == 0, completed.stdout + completed.stderr
    else:
        assert completed.returncode != 0, (
            "Agent accepted an incorrect mapped credential"
        )


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
