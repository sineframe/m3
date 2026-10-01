"""Execute the public SecretReference value shown in its reference page."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from m3 import MCPTestKit
from m3.types import SecretReference, StdioServer, UserMessage

_ROOT = Path(__file__).parents[3]


def test_harness_guide_selection_resolves_documented_credential(monkeypatch):
    source = _ROOT / "sdk/examples/docs/agents-first-test/test_harness.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    selection = next(
        node.args[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "agents"
    )
    monkeypatch.setenv("M3_DOCS_CODEX_MODEL", "fixture-model")
    monkeypatch.setenv("MY_OPENAI_KEY", "dummy-provider-key")
    selections = eval(
        compile(ast.Expression(selection), str(source), "eval"), {"os": os}
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents(selections)[0]
        spec = agent._spec(
            UserMessage(content="test"),
            server=StdioServer(name="fixture", command=sys.executable),
        )
    assert spec.harness.credential_references["OPENAI_API_KEY"] == SecretReference(
        source="environment", name="MY_OPENAI_KEY"
    )


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
    assert "1 passed" in stdout


def test_github_actions_example_keeps_secrets_in_final_step() -> None:
    blocks = re.findall(
        r"```yaml\n(.*?)\n```",
        (_ROOT / "docs/site/guides/ci/github-actions.md").read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    baseline, upload = [yaml.load(block, Loader=yaml.BaseLoader) for block in blocks]
    assert baseline["on"] == {
        "pull_request": "",
        "push": {"branches": ["main"]},
    }
    assert baseline["permissions"] == {"contents": "read"}
    baseline_steps = baseline["jobs"]["m3-tests"]["steps"]
    assert baseline_steps[0]["uses"] == (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert baseline_steps[0]["with"] == {"persist-credentials": "false"}
    assert baseline_steps[1]["uses"] == (
        "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"
    )
    assert all("secrets." not in str(step) for step in baseline_steps)
    assert "--upload" not in baseline_steps[-1]["run"]

    workflow = upload
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["on"] == {
        "push": {"branches": ["main"]},
        "workflow_dispatch": "",
    }
    steps = workflow["jobs"]["m3-tests"]["steps"]
    assert steps[0]["uses"] == (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert steps[0]["with"] == {"persist-credentials": "false"}
    assert steps[1]["uses"] == (
        "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"
    )
    assert steps[2]["run"] == "uv sync --locked"
    assert steps[3]["run"].startswith(
        'uv tool install "sf-m3-cli==$(uv run --locked --no-sync'
    )
    assert 'version("sf-m3")' in steps[3]["run"]
    assert steps[4]["run"] == "m3 setup --python .venv/bin/python"
    assert not any("--all-packages" in str(step) for step in steps)
    assert all("secrets." not in str(step) for step in steps[:-1])
    assert set(steps[-1]["env"]) == {
        "M3_ACCESS_TOKEN",
        "MY_AGENT_KEY",
        "MY_JUDGE_KEY",
    }
    assert "M3_AGENT_MODEL" not in steps[-1]["env"]
    assert "m3 ci test --upload" in steps[-1]["run"]
    assert '--harness "codex=$M3_AGENT_MODEL"' in steps[-1]["run"]
    assert "--credential-env codex:OPENAI_API_KEY=MY_AGENT_KEY" in steps[-1]["run"]
    assert "--credential-env judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY" in steps[-1]["run"]
    assert "-- tests/ -q" in steps[-1]["run"]
    assert "uv sync" not in steps[-1]["run"]
    assert "--python .venv/bin/python" in steps[-1]["run"]
