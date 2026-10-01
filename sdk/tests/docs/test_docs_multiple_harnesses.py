"""Run copied native-runtime documentation source with a local MCP fixture.

The Codex child speaks the real app-server protocol used by the default M3
adapter. It receives the native config M3 generated, launches its instrumented
shipping server, performs MCP initialize/list/call RPC, and reports those actual
frames to the adapter. There is no provider credential or external provider
request in this verification path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from m3 import (
    ClaudeCode,
    Codex,
    ExecutionOutcome,
    HarnessCase,
    HarnessMatrix,
    MCPTestKit,
    OpenCode,
    Pi,
    ServerCase,
    ToolCase,
    expect,
)
from m3.types import SecretReference, StdioServer

_REPOSITORY_ROOT = Path(__file__).parents[3]
_SDK_ROOT = _REPOSITORY_ROOT / "sdk"
_FIXTURES = _SDK_ROOT / "tests" / "fixtures"
_RUNTIME_FEED_SPEC = importlib.util.spec_from_file_location(
    "docs_runtime_feed", _FIXTURES / "docs_runtime_feed.py"
)
if _RUNTIME_FEED_SPEC is None or _RUNTIME_FEED_SPEC.loader is None:
    raise RuntimeError("could not load the local runtime feed fixture")
_RUNTIME_FEED_MODULE = importlib.util.module_from_spec(_RUNTIME_FEED_SPEC)
_RUNTIME_FEED_SPEC.loader.exec_module(_RUNTIME_FEED_MODULE)
LocalRuntimeFeed = _RUNTIME_FEED_MODULE.LocalRuntimeFeed

pytestmark = pytest.mark.process_lifecycle


def _copy_project(project_id: str, destination: Path) -> Path:
    source = _SDK_ROOT / "examples" / "docs" / project_id
    manifest = json.loads((source / "example.json").read_text(encoding="utf-8"))
    destination.mkdir()
    for relative in manifest["files"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    shutil.copy2(source / "example.json", destination / "example.json")
    return destination


def _install_protocol_fixtures(
    tmp_path: Path, fixture_names: dict[str, str]
) -> tuple[Path, Path]:
    fixture_dir = _SDK_ROOT / "tests" / "fixtures"
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    marker = tmp_path / "mcp-wire.jsonl"
    for executable_name, fixture_name in fixture_names.items():
        wrapper = executable_dir / executable_name
        wrapper.write_text(
            "#!/bin/sh\n"
            "export M3_DOCS_LOCAL_PROVIDER=1\n"
            f"export M3_DOCS_MCP_WIRE_MARKER={shlex.quote(str(marker))}\n"
            f"exec {shlex.quote(sys.executable)} "
            f'{shlex.quote(str(fixture_dir / fixture_name))} "$@" '
            f"2>>{shlex.quote(str(marker) + '.errors')}\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return executable_dir, marker


def _run_project_pytest(
    project: Path,
    tmp_path: Path,
    environment: dict[str, str],
    test_file: str,
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        test_file,
        f"--basetemp={tmp_path / 'pytest-basetemp'}",
    ]
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
        stdout, stderr = process.communicate(timeout=timeout)
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
            f"{test_file} exceeded {timeout}s; process group was terminated\n"
            f"{stdout}{stderr}"
        ) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _run_project_command(
    command: list[str], project: Path, environment: dict[str, str], *, timeout: int
) -> subprocess.CompletedProcess[str]:
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
        stdout, stderr = process.communicate(timeout=timeout)
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
        raise TimeoutError(f"command exceeded {timeout}s\n{stdout}{stderr}") from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _isolated_environment(
    executable_dir: Path, tmp_path: Path, values: dict[str, str]
) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(exist_ok=True)
    return {
        "PATH": os.pathsep.join((str(executable_dir), os.defpath)),
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PYTHONPATH": str(_SDK_ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        **values,
    }


@pytest.mark.parametrize("harness_name", ("codex", "pi", "claude_code", "opencode"))
def test_native_adapter_smoke_uses_configured_mcp_server(
    harness_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_project("agents-matrices", tmp_path / "agents-matrices")
    executable_dir, marker = _install_protocol_fixtures(
        tmp_path,
        {
            "codex": "codex_app_server_fixture.py",
            "pi": "pi_rpc_fixture.py",
            "claude": "claude_stream_fixture.py",
            "opencode": "opencode_serve_fixture.py",
        },
    )
    values = {
        "M3_DOCS_CODEX_MODEL": "local-protocol-fixture",
        "M3_DOCS_PI_MODEL": "m3-fixture/local-protocol-fixture",
        "M3_DOCS_CLAUDE_MODEL": "local-protocol-fixture",
        "M3_DOCS_OPENCODE_MODEL": "m3-fixture/local-protocol-fixture",
        "M3_DOCS_PI_PROVIDER": "m3-fixture",
        "M3_DOCS_OPENCODE_PROVIDER": "m3-fixture",
        "M3_DOCS_PI_KEY_NAME": "M3_PI_FIXTURE_KEY",
        "M3_DOCS_CLAUDE_KEY_NAME": "M3_CLAUDE_FIXTURE_KEY",
        "M3_DOCS_OPENCODE_KEY_NAME": "M3_OPENCODE_FIXTURE_KEY",
        "M3_DOCS_PI_API_KEY": "local-fixture-placeholder",
        "M3_DOCS_CLAUDE_API_KEY": "local-fixture-placeholder",
        "M3_DOCS_OPENCODE_API_KEY": "local-fixture-placeholder",
    }
    environment = _isolated_environment(executable_dir, tmp_path, values)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    fixture_dir = _SDK_ROOT / "tests" / "fixtures"
    monkeypatch.setenv("M3_DOCS_LOCAL_PROVIDER", "1")
    monkeypatch.setenv("M3_DOCS_MCP_WIRE_MARKER", str(marker))
    harnesses = {
        "codex": Codex(model=values["M3_DOCS_CODEX_MODEL"]),
        "pi": Pi(
            provider=values["M3_DOCS_PI_PROVIDER"],
            model=values["M3_DOCS_PI_MODEL"],
            credential_references={
                values["M3_DOCS_PI_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_PI_API_KEY"
                )
            },
        ),
        "claude_code": ClaudeCode(
            model=values["M3_DOCS_CLAUDE_MODEL"],
            credential_references={
                values["M3_DOCS_CLAUDE_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_CLAUDE_API_KEY"
                )
            },
        ),
        "opencode": OpenCode(
            provider=values["M3_DOCS_OPENCODE_PROVIDER"],
            model=values["M3_DOCS_OPENCODE_MODEL"],
            credential_references={
                values["M3_DOCS_OPENCODE_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_OPENCODE_API_KEY"
                )
            },
        ),
    }
    fixture_names = {
        "codex": "codex_app_server_fixture.py",
        "pi": "pi_rpc_fixture.py",
        "claude_code": "claude_stream_fixture.py",
        "opencode": "opencode_serve_fixture.py",
    }
    executable = executable_dir / harness_name
    executable.write_text(
        "#!/bin/sh\n"
        f"export M3_DOCS_LOCAL_PROVIDER=1\n"
        f"export M3_DOCS_MCP_WIRE_MARKER={shlex.quote(str(marker))}\n"
        f"exec {shlex.quote(sys.executable)} "
        f'{shlex.quote(str(fixture_dir / fixture_names[harness_name]))} "$@" '
        f"2>>{shlex.quote(str(marker) + '.errors')}\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    server = ServerCase(
        name="shipping",
        server=StdioServer(
            name="shipping",
            command=sys.executable,
            args=(str(project / "shipping_server.py"),),
            cwd=str(project),
        ),
        tools=(
            ToolCase(
                name="shipping_quote",
                id="local",
                arguments={"weight_kg": 2, "zone": "local"},
                prompt="Call shipping_quote with weight_kg 2 and zone local.",
            ),
        ),
    )
    case = HarnessMatrix.each_tool(
        servers=(server,),
        harnesses=(HarnessCase(name=harness_name, harness=harnesses[harness_name]),),
        id=f"native-smoke-{harness_name}",
    ).cases()[0]
    with MCPTestKit(env={}) as kit:
        result = case.run(kit=kit, timeout=30)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": {"amount": 9.0, "currency": "USD"}},
        result_partial=True,
    )
    wire = [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len(wire) == 1
    assert wire[0]["name"] == "shipping_quote"


@pytest.mark.timeout(300)
def test_four_native_matrix_project_uses_each_real_adapter_and_mcp_wire(
    tmp_path: Path,
) -> None:
    project = _copy_project("agents-matrices", tmp_path / "agents-matrices")
    executable_dir, marker = _install_protocol_fixtures(
        tmp_path,
        {
            "codex": "codex_app_server_fixture.py",
            "pi": "pi_rpc_fixture.py",
            "claude": "claude_stream_fixture.py",
            "opencode": "opencode_serve_fixture.py",
        },
    )
    environment = _isolated_environment(
        executable_dir,
        tmp_path,
        {
            "M3_DOCS_CODEX_MODEL": "local-protocol-fixture",
            "M3_DOCS_PI_MODEL": "m3-fixture/local-protocol-fixture",
            "M3_DOCS_CLAUDE_MODEL": "local-protocol-fixture",
            "M3_DOCS_OPENCODE_MODEL": "m3-fixture/local-protocol-fixture",
            "M3_DOCS_PI_PROVIDER": "m3-fixture",
            "M3_DOCS_OPENCODE_PROVIDER": "m3-fixture",
            "M3_DOCS_PI_KEY_NAME": "M3_PI_FIXTURE_KEY",
            "M3_DOCS_CLAUDE_KEY_NAME": "M3_CLAUDE_FIXTURE_KEY",
            "M3_DOCS_OPENCODE_KEY_NAME": "M3_OPENCODE_FIXTURE_KEY",
            "M3_DOCS_PI_API_KEY": "local-fixture-placeholder",
            "M3_DOCS_CLAUDE_API_KEY": "local-fixture-placeholder",
            "M3_DOCS_OPENCODE_API_KEY": "local-fixture-placeholder",
        },
    )
    completed = _run_project_pytest(
        project, tmp_path, environment, "test_agent_matrix.py", timeout=240
    )
    fixture_errors = Path(str(marker) + ".errors")
    error_log = (
        fixture_errors.read_text(encoding="utf-8") if fixture_errors.exists() else ""
    )
    wire_log = marker.read_text(encoding="utf-8") if marker.exists() else ""
    assert completed.returncode == 0, (
        completed.stdout
        + completed.stderr
        + "\nMCP wire calls:\n"
        + wire_log
        + "\nNative fixture stderr:\n"
        + error_log
    )
    wire = [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len(wire) == 32
    assert {item["name"] for item in wire} == {
        "shipping_quote",
        "shipping_window",
        "stock_count",
        "reorder_status",
    }
    assert all(item["method"] == "tools/call" for item in wire)


@pytest.mark.timeout(300)
@pytest.mark.parametrize("include_acp,expected_count", ((False, 8), (True, 10)))
def test_multi_harness_cli_project_expands_expected_native_and_acp_selections(
    include_acp: bool,
    expected_count: int,
    tmp_path: Path,
) -> None:
    project = _copy_project(
        "agents-multiple-harnesses", tmp_path / "agents-multiple-harnesses"
    )
    executable_dir, marker = _install_protocol_fixtures(
        tmp_path,
        {
            "codex": "codex_app_server_fixture.py",
            "pi": "pi_rpc_fixture.py",
            "claude": "claude_stream_fixture.py",
            "opencode": "opencode_serve_fixture.py",
        },
    )
    candidate_cli = Path(sys.executable).absolute().parent / "m3"
    cli_value = os.environ.get("M3_DOCS_CLI") or str(candidate_cli)
    assert Path(cli_value).is_file(), "set M3_DOCS_CLI to the candidate m3 executable"
    project_python = os.environ.get("M3_DOCS_PROJECT_PYTHON", sys.executable)
    values = {
        "M3_DOCS_PYTHON": project_python,
        "M3_DOCS_CODEX_MODEL": "local-protocol-fixture",
        "M3_DOCS_PI_MODEL": "m3-fixture/local-protocol-fixture",
        "M3_DOCS_CLAUDE_MODEL": "local-protocol-fixture",
        "M3_DOCS_OPENCODE_MODEL": "m3-fixture/local-protocol-fixture",
        "M3_DOCS_PI_KEY_NAME": "M3_PI_FIXTURE_KEY",
        "M3_DOCS_CLAUDE_KEY_NAME": "M3_CLAUDE_FIXTURE_KEY",
        "M3_DOCS_OPENCODE_KEY_NAME": "M3_OPENCODE_FIXTURE_KEY",
        "M3_DOCS_PI_API_KEY": "local-fixture-placeholder",
        "M3_DOCS_CLAUDE_API_KEY": "local-fixture-placeholder",
        "M3_DOCS_OPENCODE_API_KEY": "local-fixture-placeholder",
    }
    environment = _isolated_environment(executable_dir, tmp_path, values)
    environment["PATH"] = os.pathsep.join(
        (
            str(Path(sys.executable).parent),
            str(Path(cli_value).parent),
            environment["PATH"],
        )
    )
    environment["M3_DOCS_LOCAL_PROVIDER"] = "1"
    environment["M3_DOCS_MCP_WIRE_MARKER"] = str(marker)
    command = [
        str(Path(cli_value).resolve()),
        "test",
        "--python",
        project_python,
        "--harness",
        "codex=local-protocol-fixture",
        "--harness",
        "pi=m3-fixture/local-protocol-fixture",
        "--harness",
        "claude_code=local-protocol-fixture",
        "--harness",
        "opencode=m3-fixture/local-protocol-fixture",
        "--credential-env",
        "pi:M3_PI_FIXTURE_KEY=M3_DOCS_PI_API_KEY",
        "--credential-env",
        "claude_code:M3_CLAUDE_FIXTURE_KEY=M3_DOCS_CLAUDE_API_KEY",
        "--credential-env",
        "opencode:M3_OPENCODE_FIXTURE_KEY=M3_DOCS_OPENCODE_API_KEY",
    ]
    if include_acp:
        command.extend(("--harness", "acp=fixture"))
    command.extend(("--trials", "2", "--", "tests/test_agents.py"))
    completed = _run_project_command(command, project, environment, timeout=240)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"{expected_count} passed" in completed.stdout
    wire = [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len(wire) == 8
    assert all(item["name"] == "shipping_quote" for item in wire)
    assert all(
        item["arguments"] == {"weight_kg": 2, "zone": "local"}
        and item["result"].get("structuredContent")
        == {"amount": 9.0, "currency": "USD"}
        for item in wire
    )


def test_multi_harness_sdk_pins_use_local_runtime_assets_and_real_mcp(
    tmp_path: Path,
) -> None:
    versions = {
        "codex": "0.156.1",
        "pi": "0.85.1",
        "claude": "2.1.50",
        "opencode": "1.2.0",
    }
    marker = tmp_path / "multi-harness-mcp-wire.jsonl"
    with LocalRuntimeFeed(
        marker_path=marker,
        kind_versions={kind: (version,) for kind, version in versions.items()},
    ) as feed:
        project = _copy_project(
            "agents-multiple-harnesses", tmp_path / "multi-harness-pins"
        )
        fixture_dir = _FIXTURES
        environment = _isolated_environment(
            Path(sys.executable).parent,
            tmp_path,
            {
                "PYTHONPATH": os.pathsep.join(
                    (
                        str(_SDK_ROOT / "src"),
                        str(fixture_dir),
                        os.environ.get("PYTHONPATH", ""),
                    )
                ),
                "PYTEST_PLUGINS": "docs_runtime_feed_plugin",
                "M3_DOCS_RUNTIME_FEED_URL": feed.manifest_feed_url,
                "M3_DOCS_MCP_WIRE_MARKER": str(marker),
                "M3_HARNESS_CACHE_DIR": str(tmp_path / "runtime-cache"),
                "M3_DOCS_CODEX_MODEL": "fixture-codex",
                "M3_DOCS_CODEX_VERSION": versions["codex"],
                "M3_DOCS_PI_MODEL": "m3-fixture/local-protocol-fixture",
                "M3_DOCS_PI_PROVIDER": "m3-fixture",
                "M3_DOCS_PI_KEY_NAME": "M3_PI_FIXTURE_KEY",
                "M3_DOCS_PI_API_KEY": "local-fixture-placeholder",
                "M3_DOCS_PI_VERSION": versions["pi"],
                "M3_DOCS_CLAUDE_MODEL": "local-protocol-fixture",
                "M3_DOCS_CLAUDE_KEY_NAME": "M3_CLAUDE_FIXTURE_KEY",
                "M3_DOCS_CLAUDE_API_KEY": "local-fixture-placeholder",
                "M3_DOCS_CLAUDE_VERSION": versions["claude"],
                "M3_DOCS_OPENCODE_MODEL": "m3-fixture/local-protocol-fixture",
                "M3_DOCS_OPENCODE_PROVIDER": "m3-fixture",
                "M3_DOCS_OPENCODE_KEY_NAME": "M3_OPENCODE_FIXTURE_KEY",
                "M3_DOCS_OPENCODE_API_KEY": "local-fixture-placeholder",
                "M3_DOCS_OPENCODE_VERSION": versions["opencode"],
                "M3_DOCS_PYTHON": sys.executable,
            },
        )
        output = _run_project_pytest(
            project,
            tmp_path,
            environment,
            "test_sdk_pins.py",
            timeout=240,
        )

    assert "1 passed" in output.stdout
    calls = [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len(calls) == 8
    assert {call["method"] for call in calls} == {"tools/call"}
    assert {call["name"] for call in calls} == {"shipping_quote"}
    assert {
        kind: sum(call.get("runtime_kind") == kind for call in calls)
        for kind in versions
    } == {kind: 2 for kind in versions}
    assert all(
        call.get("runtime_version") == versions[call["runtime_kind"]] for call in calls
    )
    assert all(
        call["arguments"] == {"weight_kg": 2, "zone": "local"}
        and call["result"].get("structuredContent")
        == {"amount": 9.0, "currency": "USD"}
        for call in calls
    )
    assert set(feed.requests) == {
        f"/manifest/{kind}/{version}" for kind, version in versions.items()
    } | {f"/asset/{kind}/{version}.zip" for kind, version in versions.items()}
