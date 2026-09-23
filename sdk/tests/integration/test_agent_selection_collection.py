from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from m3 import MCPTestKit, ServerBinding, StdioServer, expect
from m3.agent_session import HarnessAdapter
from m3.async_api import AsyncMCPTestKit
from m3.harness import HarnessAdapterRegistry
from m3.storage import SQLiteExecutionStore
from m3.types import (
    RevisionSelection,
    ServerProfileRef,
    TextContent,
    TurnResponse,
    UserMessage,
)


def test_cli_agents_cross_product_with_tool_matrix_and_trials(tmp_path: Path) -> None:
    source = """
import pytest
from m3.matrix import ServerCase, ToolCase, ToolMatrix
from m3 import StdioServer
server = ServerCase(name="shipping", server=StdioServer(name="shipping", command="echo"), tools=(ToolCase(name="quote"),))
matrix = ToolMatrix(servers=(server,))
@pytest.mark.m3
@matrix.parametrize()
def test_one(case, agent):
    pass
"""
    test_file = tmp_path / "test_collection.py"
    test_file.write_text(source, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--harness",
            "opencode=provider/a,provider/b",
            "--trials",
            "2",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("test_one[") == 4
    assert "opencode-provider/a-opencode-trial-1" in result.stdout
    assert "opencode-provider/b-opencode-trial-2" in result.stdout


@pytest.mark.parametrize(
    "arguments",
    (
        ("--runtime=managed", "--harness=pi=openai/model"),
        ("--runtime=managed", "--harness=pi@1.2.3=openai/model"),
    ),
)
def test_cli_collects_managed_pi_selection(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    test_file = tmp_path / "test_managed_pi.py"
    test_file.write_text(
        "import pytest\n@pytest.mark.m3\ndef test_managed_pi(agent): pass\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            *arguments,
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("test_managed_pi[") == 1


def test_cli_executes_same_model_at_multiple_managed_versions(tmp_path: Path) -> None:
    test_file = tmp_path / "test_versions.py"
    test_file.write_text(
        "import pytest\n"
        "@pytest.mark.m3\n"
        "def test_versions(agent):\n"
        "    assert agent.entry['version'] in {'1.18.30', '1.18.31'}\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--runtime=managed",
            "--harness=opencode@1.18.30=model",
            "--harness=opencode@1.18.31=model",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_base_sdk_import_does_not_require_pytest(tmp_path: Path) -> None:
    script = """
import builtins
real = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'pytest' or name.startswith('pytest.'):
        raise ModuleNotFoundError('blocked pytest')
    return real(name, *args, **kwargs)
builtins.__import__ = blocked
import m3
from m3 import MCPTestKit, StdioServer
kit = MCPTestKit(env={})
assert kit.agents
assert StdioServer(name="s", command="echo").name == "s"
kit.close()
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_base_sdk_install_has_no_pytest_and_supports_notebook_selection(
    tmp_path: Path,
) -> None:
    venv = tmp_path / "venv"
    workspace = Path(__file__).parents[3]
    created = subprocess.run(
        ["uv", "venv", str(venv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    installed = subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(workspace / "sdk")],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    script = """
import importlib.util
assert importlib.util.find_spec("pytest") is None
from m3 import MCPTestKit, StdioServer
kit = MCPTestKit(env={})
agent = kit.agents([{"harness": "acp", "models": ["fixture"], "manifest": {"command": "fixture-agent", "protocol": "acp", "protocol_version": 1}}])[0]
assert agent.model == "fixture"
assert StdioServer(name="notebook", command="echo").name == "notebook"
kit.close()
"""
    checked = subprocess.run(
        [str(python), "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONNOUSERSITE": "1"},
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_collection_requires_marker_only_for_agent_and_runs_ordinary_once(
    tmp_path: Path,
) -> None:
    source = """
import pytest
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["m"]}])
def test_plain(): pass
def test_selected(agent): pass
"""
    test_file = tmp_path / "test_marker.py"
    test_file.write_text(source, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("test_plain") == 1
    assert result.stdout.count("test_selected[") == 1


def test_unmarked_agent_fixture_is_left_to_pytest_when_cli_selection_is_present(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_missing.py"
    test_file.write_text(
        """
import pytest

@pytest.fixture
def agent():
    return "project-agent"

def test_unmarked(agent):
    assert agent == "project-agent"

@pytest.mark.m3
def test_marked(agent):
    pass
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--harness",
            "opencode=provider/a",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("test_unmarked") == 1
    assert result.stdout.count("test_marked[") == 1


def test_marked_agent_without_selection_still_fails_at_collection(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_missing.py"
    test_file.write_text(
        "import pytest\n@pytest.mark.m3\ndef test_missing(agent): pass\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "agent test requires" in result.stdout + result.stderr


def test_function_marker_overrides_module_marker(tmp_path: Path) -> None:
    source = """
import pytest
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["module"]}])
@pytest.mark.m3(agents=[{"harness": "opencode", "models": ["function"]}])
def test_selected(agent): pass
"""
    test_file = tmp_path / "test_function_marker.py"
    test_file.write_text(source, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "opencode-function" in result.stdout
    assert "opencode-module" not in result.stdout


def test_cli_models_replace_marker_models_and_retain_acp_manifest(
    tmp_path: Path,
) -> None:
    source = """
import pytest, sys
pytestmark = pytest.mark.m3(agents=[
 {"harness": "acp", "models": ["marker"],
  "manifest": {"command": sys.executable, "args": ["fixture-agent"], "protocol": "acp", "protocol_version": 1}},
])
def test_selected(agent): pass
"""
    test_file = tmp_path / "test_cli_replace.py"
    test_file.write_text(source, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--harness",
            "acp=cli-model",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "acp-cli-model" in result.stdout
    assert "acp-marker" not in result.stdout


def test_collect_only_does_not_start_server(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    source = f"""\nimport pytest, sys\nfrom m3 import StdioServer\npytestmark = pytest.mark.m3(agents=[{{"harness": "opencode", "models": ["m"]}}])\nserver = StdioServer(name="side-effect", command=sys.executable, args=["-c", "open({str(marker)!r}, 'w').write('started')"])\ndef test_selected(agent): pass\n"""
    test_file = tmp_path / "test_collect_only.py"
    test_file.write_text(source, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


def test_xdist_collection_keeps_all_tool_matrix_ids(tmp_path: Path) -> None:
    source = """
import pytest
from m3.matrix import ServerCase, ToolCase, ToolMatrix
from m3 import StdioServer
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["a", "b"]}], trials=2)
matrix = ToolMatrix(servers=(
 ServerCase(name="one", server=StdioServer(name="one", command="echo"), tools=(ToolCase(name="quote"),)),
 ServerCase(name="two", server=StdioServer(name="two", command="echo"), tools=(ToolCase(name="quote"),)),
))
@matrix.parametrize()
def test_selected(case, agent): pass
"""
    test_file = tmp_path / "test_xdist_collect.py"
    test_file.write_text(source, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "2",
            "-p",
            "m3.pytest_plugin",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passed" in result.stdout, result.stdout + result.stderr


def test_cli_credentials_do_not_mutate_acp_manifest_entries(tmp_path: Path) -> None:
    test_file = tmp_path / "test_mixed.py"
    test_file.write_text(
        """
import pytest, sys
pytestmark = pytest.mark.m3(agents=[
 {"harness": "acp", "models": ["fixture"], "manifest": {"command": sys.executable, "args": ["agent"], "protocol": "acp", "protocol_version": 1}},
 {"harness": "opencode", "models": ["vendor/model"]},
])
def test_selected(agent):
    assert agent.harness in {"acp", "opencode"}
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--credential-env",
            "VENDOR_KEY=SOURCE",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tool_matrix_agent_trials_persist_twelve_distinct_executions(
    tmp_path: Path,
) -> None:
    examples = Path(__file__).parents[2] / "examples" / "servers"
    server = examples / "example_mcp_server.py"
    acp = examples / "deterministic_acp_agent.py"
    source = f'''
import pytest, sys
from m3 import EvaluationDecision, EvaluationStatus, StdioServer, expect
from m3.matrix import ServerCase, ToolCase, ToolMatrix

servers = tuple(
    ServerCase(
        name=f"shipping-{{index}}",
        server=StdioServer(name=f"shipping-{{index}}", command=sys.executable, args=[r"{server}"]),
        tools=(ToolCase(name="shipping_quote", prompt="Get a local shipping quote"),),
    )
    for index in range(3)
)
matrix = ToolMatrix(servers=servers)
pytestmark = pytest.mark.m3(
    agents=[{{"harness": "acp", "models": ["fixture-a", "fixture-b"],
             "manifest": {{"command": sys.executable, "args": [r"{acp}"],
                          "protocol": "acp", "protocol_version": 1}}}}],
    trials=2,
)

@matrix.parametrize()
def test_selected(case, agent, m3_kit):
    result = agent.run(case.tool.prompt, server=case.server)
    expect(result).to_have_tool_call("shipping_quote", server=case.server.name, status="success")
    m3_kit.register_evaluator(
        "fixture.v1", lambda _context: EvaluationDecision(status=EvaluationStatus.PASSED, score=1.0)
    )
    m3_kit.evaluate(result, "fixture.v1")
'''
    test_file = tmp_path / "test_selected.py"
    test_file.write_text(source, encoding="utf-8")
    database = tmp_path / "matrix.sqlite"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--results-db",
            str(database),
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    store = SQLiteExecutionStore(database)
    try:
        runs = store.list_test_runs()
        assert len(runs) == 1
        attempts = store.list_test_results(str(runs[0]["run_id"]))
        assert len(attempts) == 12
        execution_ids = [
            str(execution_id)
            for attempt in attempts
            for execution_id in attempt["execution_ids"]
        ]
        assert len(execution_ids) == len(set(execution_ids)) == 12
        specs = [
            store.get_execution_spec(execution_id) for execution_id in execution_ids
        ]
        assert all(spec is not None for spec in specs)
        assert len({spec.case_id for spec in specs}) == 3, [
            (
                spec.case_id,
                spec.metadata["m3.matrix.cell_id"],
                spec.metadata["harness_config"],
            )
            for spec in specs
        ]
        assert {spec.metadata["trial"] for spec in specs} == {1, 2}
        assert {spec.metadata["harness_config"] for spec in specs} == {
            "acp:fixture-a",
            "acp:fixture-b",
        }
        assert all(store.evaluations(execution_id) for execution_id in execution_ids)
    finally:
        store.close()


def test_selected_acp_submit_and_multiserver_session_are_public(tmp_path: Path) -> None:
    examples = Path(__file__).parents[2] / "examples" / "servers"
    entry = {
        "harness": "acp",
        "models": ["fixture"],
        "manifest": {
            "command": sys.executable,
            "args": [str(examples / "deterministic_acp_agent.py")],
            "protocol": "acp",
            "protocol_version": 1,
        },
    }
    first = StdioServer(
        name="catalog",
        command=sys.executable,
        args=(str(examples / "example_mcp_server.py"),),
    )
    second = first.model_copy(update={"name": "warehouse"})
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([entry])[0]
        handle = agent.submit("Get a local shipping quote.", server=first)
        assert handle.snapshot().lifecycle.value in {"starting", "running", "finished"}
        result = handle.result(timeout=30)
        expect(result).to_have_tool_call(
            "shipping_quote", server="catalog", status="success"
        )
        with agent.session(servers=[first, second]) as session:
            first_turn = session.send("Get a local shipping quote.", timeout=30)
            second_turn = session.send("Get a regional shipping quote.", timeout=30)
        assert first_turn.snapshot.outcome.value == "completed"
        assert second_turn.snapshot.outcome.value == "completed"
        assert len(session.result.trace_view.tool_calls) == 2


def test_selected_agent_runs_saved_harness_and_server_profiles(tmp_path: Path) -> None:
    examples = Path(__file__).parents[2] / "examples" / "servers"
    manifest = {
        "command": sys.executable,
        "args": [str(examples / "deterministic_acp_agent.py")],
        "protocol": "acp",
        "protocol_version": 1,
    }
    store = SQLiteExecutionStore(tmp_path / "profiles.sqlite")
    harness_profile = store.create_harness_profile(
        "fixture-agent",
        {"manifest": manifest, "trusted_unsandboxed": True},
        profile_id="harness-fixture",
        revision_id="harness-revision",
    )
    server_profile = store.create_server_profile(
        "fixture-servers",
        {
            "mcpServers": {
                "example-mcp": {
                    "command": sys.executable,
                    "args": [str(examples / "example_mcp_server.py")],
                }
            }
        },
        profile_id="servers-fixture",
        revision_id="servers-revision",
    )
    server = ServerBinding(
        profile=ServerProfileRef(
            profile_id=server_profile.id,
            server_name="example-mcp",
            revision=RevisionSelection(mode="latest"),
        )
    )
    entry = {
        "harness_profile": {
            "profile_id": harness_profile.id,
            "revision": {"mode": "latest"},
        }
    }
    try:
        with MCPTestKit(store=store, env={}) as kit:
            agent = kit.agents([entry])[0]
            result = agent.run("Get a local shipping quote.", server=server)
            expect(result).to_have_tool_call(
                "shipping_quote", server="example-mcp", status="success"
            )
    finally:
        store.close()


def test_selected_agent_public_tool_modes_are_forwarded() -> None:
    manifest = {"command": "fixture-agent", "protocol": "acp", "protocol_version": 1}
    recording = _RecordingKit()
    agent = recording.agents(
        [{"harness": "acp", "models": ["fixture"], "manifest": manifest}]
    )[0]
    server = StdioServer(name="example-mcp", command="echo")
    agent.run("probe", server=server, tools=[])
    assert recording.specs[-1].tool_policy.allowed_tools == ()
    agent.run("probe", server=server, tools=["example-mcp:shipping_quote"])
    assert recording.specs[-1].tool_policy.allowed_tools == (
        "example-mcp:shipping_quote",
    )


def test_selected_agent_submit_cancel_uses_real_execution_handle() -> None:
    class SlowAdapter(HarnessAdapter):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def start(self, _spec) -> None:
            return None

        async def send(
            self,
            message: UserMessage,
            *,
            timeout=None,
            metadata: Mapping[str, object] | None = None,
        ):
            del message, timeout, metadata
            self.started.set()
            await self.release.wait()
            return TurnResponse(content=(TextContent(text="released"),))

        async def close(self) -> None:
            return None

    async def exercise() -> None:
        adapter = SlowAdapter()
        registry = HarnessAdapterRegistry({"acp": lambda _harness: adapter})
        entry = {
            "harness": "acp",
            "models": ["fixture"],
            "manifest": {
                "command": "fixture-agent",
                "protocol": "acp",
                "protocol_version": 1,
            },
        }
        async with AsyncMCPTestKit(
            env={}, cwd="/tmp/m3-no-project", adapter_registry=registry
        ) as kit:
            agent = kit.agents([entry])[0]
            handle = agent.submit(
                "cancel-me", server=StdioServer(name="unused", command="echo"), tools=[]
            )
            await asyncio.wait_for(adapter.started.wait(), timeout=5)
            assert (await handle.snapshot()).lifecycle.value in {
                "starting",
                "running",
                "running_turn",
            }
            await handle.cancel()
            result = await handle.result(timeout=5)
            assert result.snapshot.outcome.value == "cancelled"
            assert (await handle.snapshot()).outcome.value == "cancelled"

    asyncio.run(exercise())


class _RecordingKit:
    def __init__(self) -> None:
        self.specs = []

    def agents(self, entries):
        from m3._agent_selection import expand

        return expand(self, entries)

    def run(self, spec):
        self.specs.append(spec)
        return spec


def test_async_selected_acp_run_and_multiturn_session_are_public() -> None:
    import asyncio

    async def exercise() -> None:
        examples = Path(__file__).parents[2] / "examples" / "servers"
        entry = {
            "harness": "acp",
            "models": ["fixture"],
            "manifest": {
                "command": sys.executable,
                "args": [str(examples / "deterministic_acp_agent.py")],
                "protocol": "acp",
                "protocol_version": 1,
            },
        }
        server = StdioServer(
            name="example-mcp",
            command=sys.executable,
            args=(str(examples / "example_mcp_server.py"),),
        )
        async with AsyncMCPTestKit(env={}) as kit:
            agent = kit.agents([entry])[0]
            result = await agent.run("Get a local shipping quote.", server=server)
            expect(result).to_have_tool_call(
                "shipping_quote", server="example-mcp", status="success"
            )
            async with agent.session(server=server) as session:
                first = await session.send("Get a local shipping quote.", timeout=30)
                second = await session.send(
                    "Get a regional shipping quote.", timeout=30
                )
            assert first.snapshot.outcome.value == "completed"
            assert second.snapshot.outcome.value == "completed"
            assert len(session.result.trace_view.tool_calls) == 2

    asyncio.run(exercise())
