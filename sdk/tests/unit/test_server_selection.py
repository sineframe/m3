from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from m3._server_selection import normalize_servers
from m3.types import HTTPServer, StdioServer, TrustLevel


def test_normalize_server_alternatives_and_loopback_defaults() -> None:
    servers = normalize_servers(
        [
            {"type": "http", "url": "http://localhost:8765/mcp"},
            {"type": "stdio", "command": "python", "args": ["-m", "sample"]},
            {"type": "http", "url": "https://example.test/mcp"},
        ]
    )
    assert isinstance(servers[0], HTTPServer)
    assert servers[0].name == "server"
    assert servers[0].trust is TrustLevel.TRUSTED_PRIVATE
    assert servers[0].loopback_only is True
    assert isinstance(servers[1], StdioServer)
    assert servers[1].args == ("-m", "sample")
    assert servers[1].trust is TrustLevel.UNTRUSTED
    assert isinstance(servers[2], HTTPServer)
    assert servers[2].trust is TrustLevel.UNTRUSTED


def test_explicit_trust_disables_inferred_loopback_constraint() -> None:
    server = normalize_servers(
        [{"type": "http", "url": "http://127.0.0.1/mcp", "trust": "trusted_private"}]
    )[0]
    assert isinstance(server, HTTPServer)
    assert server.trust is TrustLevel.TRUSTED_PRIVATE
    assert server.loopback_only is False


@pytest.mark.parametrize(
    "selection",
    [
        [],
        [{"type": "http"}],
        [{"type": "http", "url": "ftp://example.test/mcp"}],
        [{"type": "http", "url": "http://example.test:0/mcp"}],
        [{"type": "http", "url": "http://example.test/mcp#fragment"}],
        [{"type": "http", "url": "http://example.test/mcp", "trust": None}],
        [{"type": "http", "url": "https://example.test/mcp", "command": "bad"}],
        [{"type": "stdio", "command": "python", "trust": "public"}],
        [{"type": "stdio", "command": "python", "args": [1]}],
        [{"type": "http", "url": "http://localhost", "trust": "sdk_loopback"}],
    ],
)
def test_invalid_server_selections_are_rejected(selection: object) -> None:
    with pytest.raises(ValueError):
        normalize_servers(selection)


def test_duplicate_server_selections_are_rejected() -> None:
    entry = {"type": "stdio", "command": "python"}
    with pytest.raises(ValueError, match="duplicate"):
        normalize_servers([entry, entry])


def _collect_matrix(
    tmp_path: Path, server_json: str | None = None
) -> subprocess.CompletedProcess[str]:
    return _collect(
        tmp_path,
        "import pytest\n"
        "@pytest.mark.m3(agents=[{'harness':'opencode','models':['opencode/a']}, "
        "{'harness':'codex','models':['gpt']}], "
        "servers=[{'type':'stdio','command':'marker-a'}, "
        "{'type':'stdio','command':'marker-b'}], trials=2)\n"
        "def test_matrix(agent, server): pass\n",
        server_json,
    )


def _collect(
    tmp_path: Path,
    source: str,
    server_json: str | None = None,
    *,
    collect_only: bool = True,
    suite: str | None = None,
) -> subprocess.CompletedProcess[str]:
    test_file = tmp_path / "test_matrix.py"
    test_file.write_text(source, encoding="utf-8")
    env = os.environ.copy()
    sdk_source = str(Path(__file__).parents[2] / "src")
    env["PYTHONPATH"] = sdk_source + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "m3.pytest_plugin",
    ]
    if collect_only:
        command.append("--collect-only")
    if suite is not None:
        command += ["--suite", suite]
    if server_json is not None:
        command += ["--m3-server-selections", server_json]
    command.append(str(test_file))
    return subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )


def test_pytest_server_and_agent_selections_form_matrix(tmp_path: Path) -> None:
    result = _collect_matrix(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    ids = [
        line
        for line in result.stdout.splitlines()
        if "test_matrix.py::test_matrix[" in line
    ]
    assert len(ids) == 8, result.stdout
    assert sum("server-stdio-1" in line for line in ids) == 4
    assert sum("server-stdio-2" in line for line in ids) == 4
    assert any("opencode" in line and "trial-1" in line for line in ids)
    assert any("codex" in line and "trial-2" in line for line in ids)


def test_cli_server_array_replaces_marker_entries(tmp_path: Path) -> None:
    replacement = json.dumps([{"type": "stdio", "command": "cli-only"}])
    result = _collect_matrix(tmp_path, replacement)
    assert result.returncode == 0, result.stdout + result.stderr
    ids = [
        line
        for line in result.stdout.splitlines()
        if "test_matrix.py::test_matrix[" in line
    ]
    assert len(ids) == 4, result.stdout
    assert all("server-stdio-1" in line for line in ids)


def test_cli_http_selection_does_not_inherit_marker_trust(tmp_path: Path) -> None:
    source = (
        "import pytest\n"
        "@pytest.mark.m3(agents=[{'harness':'opencode','models':['opencode/a']}], "
        "servers=[{'type':'http','url':'https://example.test/mcp',"
        "'name':'same','trust':'public'}])\n"
        "def test_agent_http(agent, server): pass\n"
    )
    replacement = json.dumps(
        [{"type": "http", "url": "https://example.test/mcp", "name": "same"}]
    )
    result = _collect(tmp_path, source, replacement)
    assert result.returncode != 0
    assert "agent HTTP server has trust=untrusted" in result.stdout
    assert "set trust='public' (or --trust public)" in result.stdout


@pytest.mark.parametrize("fixture_location", ["module", "conftest"])
def test_marked_agent_keeps_project_server_fixture_without_selection(
    tmp_path: Path, fixture_location: str
) -> None:
    fixture_source = (
        "import pytest\n@pytest.fixture\ndef server(): return 'project-server'\n"
    )
    if fixture_location == "conftest":
        (tmp_path / "conftest.py").write_text(fixture_source, encoding="utf-8")
    source = (
        (fixture_source if fixture_location == "module" else "import pytest\n")
        + "@pytest.mark.m3(agents=[{'harness':'opencode','models':['opencode/a']}])\n"
        + "def test_existing_fixture(agent, server): assert server == 'project-server'\n"
    )
    result = _collect(tmp_path, source, collect_only=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("indirect", [False, True])
@pytest.mark.parametrize("keyword", [False, True])
def test_marked_agent_keeps_pytest_server_parameter(
    tmp_path: Path, indirect: bool, keyword: bool
) -> None:
    parameter = (
        f"argnames='server', argvalues=['alpha', 'beta'], indirect={indirect}"
        if keyword
        else f"'server', ['alpha', 'beta'], indirect={indirect}"
    )
    source = (
        "import pytest\n"
        "@pytest.mark.m3(agents=[{'harness':'opencode','models':['opencode/a']}])\n"
        f"@pytest.mark.parametrize({parameter})\n"
        "def test_existing_parameter(agent, server): assert server in {'alpha', 'beta'}\n"
    )
    result = _collect(tmp_path, source, collect_only=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_m3_server_selection_conflicts_with_pytest_server_parameter(
    tmp_path: Path,
) -> None:
    source = (
        "import pytest\n"
        "@pytest.mark.m3(servers=[{'type':'stdio','command':'selected'}])\n"
        "@pytest.mark.parametrize('server', ['alpha'])\n"
        "def test_conflict(server): pass\n"
    )
    result = _collect(tmp_path, source)
    assert result.returncode != 0
    assert "M3 server selections conflict" in result.stdout


def test_suite_skips_server_validation_for_other_suites(tmp_path: Path) -> None:
    source = (
        "import pytest\n"
        "@pytest.mark.m3(suite_name='A')\n"
        "def test_other_suite(agent, server): pass\n"
        "@pytest.mark.m3(suite_name='B', "
        "servers=[{'type':'stdio','command':'selected'}])\n"
        "def test_selected_suite(server): assert server.command == 'selected'\n"
    )
    result = _collect(tmp_path, source, collect_only=False, suite="B")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 deselected" in result.stdout
