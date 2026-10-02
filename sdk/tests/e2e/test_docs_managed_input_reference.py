"""Execute the managed-input reference with native Codex and local fixtures."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from test_real_codex_managed_mrtr import (
    FixtureCodexHarnessAdapter,
    ModelOutput,
    ResponsesRun,
    function_tools,
    open_codex_responses_provider,
)

import m3
from m3 import ElicitationResponse, MCPTestKit, StdioServer
from m3.harness.contracts import HarnessAdapterRegistry
from m3.storage import SQLiteExecutionStore
from m3.types import Codex, HarnessSpec

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "docs" / "site" / "reference" / "python" / "m3" / "managed-input.md"


@pytest.mark.e2e
@pytest.mark.process_lifecycle
def test_managed_input_reference_fence_against_native_codex_and_local_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = os.environ.get("M3_DOCS_CODEX_EXECUTABLE") or shutil.which("codex")
    cache_root = os.environ.get("M3_HARNESS_CACHE_DIR") or str(
        tmp_path.parent / f"{tmp_path.name}-managed-runtime-cache"
    )
    if executable is None:
        pytest.fail("install Codex CLI 0.156.1 or set M3_DOCS_CODEX_EXECUTABLE")
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    assert version == "codex-cli 0.156.1"

    monkeypatch.setenv("M3_DOCS_CODEX_MODEL", "m3-fixture-model")
    monkeypatch.chdir(tmp_path)
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    isolated_home = tmp_path / "empty-home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(
            function_name="mcp__shipping::book_shipment",
            arguments={"weight_kg": 2},
        )
    )
    provider.enqueue(ModelOutput(text="Shipment booked by the local fixture."))
    server_source = (
        ROOT
        / "sdk"
        / "examples"
        / "docs"
        / "elicitation-managed-input"
        / "shipping_server.py"
    )
    server_copy = tmp_path / "shipping_server.py"
    shutil.copy2(server_source, server_copy)
    shipping_server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(server_copy),),
        cwd=str(tmp_path),
    )
    with open_codex_responses_provider(provider) as provider_url:
        assert provider_url.startswith("http://127.0.0.1:")

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            return FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )

        registry = HarnessAdapterRegistry({"codex": make_adapter})

        def configured_kit(*, store: SQLiteExecutionStore, env: dict[str, str]):
            return MCPTestKit(
                store=store,
                env=env,
                cwd=str(tmp_path),
                adapter_registry=registry,
                harness_cache_dir=cache_root,
            )

        source = re.findall(
            r"```(?:python|py)\s*\n(.*?)\n```",
            PAGE.read_text(encoding="utf-8"),
            re.DOTALL,
        )[0]
        namespace: dict[str, object] = {
            "__name__": "__doc_example__",
            "MCPTestKit": configured_kit,
            "SQLiteExecutionStore": SQLiteExecutionStore,
            "ElicitationResponse": ElicitationResponse,
            "os": os,
            "time": time,
            "shipping_server": shipping_server,
        }
        with monkeypatch.context() as isolated_modules:
            isolated_modules.setattr(m3, "MCPTestKit", configured_kit)
            exec(compile(source, "managed-input.md::1", "exec"), namespace)

    assert len(provider.requests) == 2
    assert "mcp__shipping::book_shipment" in function_tools(provider.requests[0])
    assert len(provider.responses) == 2
    assert all(response.status == 200 for response in provider.responses)
    assert '"type":"function_call"' in provider.responses[0].body
    assert '"type":"message"' in provider.responses[1].body
    second_input = provider.requests[1].body.get("input")
    assert isinstance(second_input, list)
    assert any(
        isinstance(item, dict) and item.get("type") == "function_call_output"
        for item in second_input
    )
    assert all(request.path == "/v1/responses" for request in provider.requests)
    assert all("authorization" not in request.headers for request in provider.requests)
    result = namespace["result"]
    assert result.snapshot.outcome.value == "completed"
    assert result.trace_view is not None
    assert len(result.trace_view.tool_calls) == 1
    assert result.trace_view.tool_calls[0].tool.value == "book_shipment"
    assert result.trace_view.tool_calls[0].tool_status.value == "success"
    assert result.trace_view.elicitations
    assert result.trace_view.elicitations[0].request_key == "shipping_address"

    reopened_store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    try:
        records = reopened_store.managed_input_store.list_rounds(
            result.snapshot.execution_id.root
        )
    finally:
        reopened_store.close()
    assert len(records) == 1
    assert records[0].status == "resolved"
    assert records[0].responses == {
        "shipping_address": ElicitationResponse(
            action="accept",
            content={"street": "1 Main Street", "city": "Pune"},
        )
    }


@pytest.mark.e2e
@pytest.mark.process_lifecycle
def test_managed_input_project_test_against_native_codex_and_local_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = os.environ.get("M3_DOCS_CODEX_EXECUTABLE") or shutil.which("codex")
    cache_root = os.environ.get("M3_HARNESS_CACHE_DIR") or str(
        tmp_path / "managed-runtime-cache"
    )
    if executable is None:
        pytest.fail("install Codex CLI 0.156.1 or set M3_DOCS_CODEX_EXECUTABLE")
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    assert version == "codex-cli 0.156.1"

    project = tmp_path / "elicitation-managed-input"
    project.mkdir()
    source_project = ROOT / "sdk" / "examples" / "docs" / "elicitation-managed-input"
    manifest = json.loads((source_project / "example.json").read_text(encoding="utf-8"))
    for filename in manifest["files"]:
        shutil.copy2(source_project / filename, project / filename)

    monkeypatch.chdir(project)
    monkeypatch.setenv("M3_DOCS_CODEX_MODEL", "m3-fixture-model")
    isolated_source_home = tmp_path / "empty-codex-source-home-project"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))
    isolated_home = tmp_path / "empty-home-project"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))

    provider = ResponsesRun()
    provider.enqueue(
        ModelOutput(
            function_name="mcp__shipping::book_shipment",
            arguments={"weight_kg": 2},
        )
    )
    provider.enqueue(ModelOutput(text="Shipment booked by the local fixture."))
    with open_codex_responses_provider(provider) as provider_url:
        assert provider_url.startswith("http://127.0.0.1:")

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            if not isinstance(harness, Codex):
                raise TypeError("Codex fixture received a non-Codex harness")
            return FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )

        registry = HarnessAdapterRegistry({"codex": make_adapter})

        def configured_kit(*, store: SQLiteExecutionStore, env: dict[str, str]):
            return MCPTestKit(
                store=store,
                env=env,
                cwd=str(project),
                adapter_registry=registry,
                harness_cache_dir=cache_root,
            )

        with monkeypatch.context() as isolated_modules:
            isolated_modules.setattr(m3, "MCPTestKit", configured_kit)
            test_namespace = runpy.run_path(
                str(project / "test_managed_input.py"),
                run_name="m3_docs_managed_input_project",
            )
        test_namespace["test_managed_form_input_resumes_codex_execution"](tmp_path)

    assert len(provider.requests) == 2
    assert "mcp__shipping::book_shipment" in function_tools(provider.requests[0])
    assert len(provider.responses) == 2
    assert all(response.status == 200 for response in provider.responses)
    assert '"type":"function_call"' in provider.responses[0].body
    assert '"type":"message"' in provider.responses[1].body
    second_input = provider.requests[1].body.get("input")
    assert isinstance(second_input, list)
    assert any(
        isinstance(item, dict) and item.get("type") == "function_call_output"
        for item in second_input
    )
    assert all(request.path == "/v1/responses" for request in provider.requests)
    assert all("authorization" not in request.headers for request in provider.requests)


@pytest.mark.e2e
@pytest.mark.process_lifecycle
@pytest.mark.timeout(300)
def test_elicitation_agent_sessions_use_local_providers_and_real_managed_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the complete two-turn Codex and Pi guide tests with local providers."""

    tests_root = ROOT / "sdk" / "tests"
    monkeypatch.syspath_prepend(str(tests_root))
    project = tmp_path / "elicitation-agents"
    project.mkdir()
    source_project = ROOT / "sdk" / "examples" / "docs" / "elicitation-agents"
    manifest = json.loads((source_project / "example.json").read_text(encoding="utf-8"))
    for filename in manifest["files"]:
        shutil.copy2(source_project / filename, project / filename)

    codex_fixture = _load_test_module(
        "m3_docs_codex_mrtr_fixture",
        tests_root / "e2e" / "test_real_codex_managed_mrtr.py",
    )
    pi_fixture = _load_test_module(
        "m3_docs_pi_mrtr_fixture",
        tests_root / "e2e" / "test_real_pi_mrtr_gate.py",
    )
    source = _load_test_module(
        "docs_elicitation_agent_sessions", project / "test_agent_sessions.py"
    )

    configured_cache = os.environ.get("M3_DOCS_MANAGED_NATIVE_CACHE")
    cache_root = Path(configured_cache or tmp_path / "runtime-cache")
    runtime_manager = None
    if configured_cache:
        expected_receipts = (
            cache_root
            / "codex/0.156.1/darwin-arm64-64/sha256-2bd64af14dedd47795f2f6bfd5d125cf79199acc2c7ba222144e08127111a5ca/receipt.json",
            cache_root
            / "pi/0.85.1/darwin-arm64-64/sha256-d5f70e3c0cf7398eac239fd0261ee074d98b7ba7f6b43fe3617f052ed5b79d06/receipt.json",
        )
        assert all(receipt.is_file() for receipt in expected_receipts), (
            "the configured cache must already contain the two pinned runtimes"
        )
    runtime_manager, codex_lease, pi_lease = asyncio.run(
        _acquire_elicitation_runtime_pair(project, cache_root)
    )
    codex_executable = str(codex_lease.executable)
    pi_executable = str(pi_lease.executable)

    assert _binary_version(codex_executable) == "codex-cli 0.156.1"
    assert _binary_version(pi_executable) == "0.85.1"

    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("M3_HARNESS_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("M3_DOCS_CODEX_MODEL", "m3-fixture-model")
    monkeypatch.setenv("M3_DOCS_PI_MODEL", "m3-fixture/fixture-model")
    monkeypatch.setenv("M3_DOCS_PI_PROVIDER", "m3-fixture")
    monkeypatch.setenv("M3_DOCS_PI_KEY_NAME", "M3_PI_FIXTURE_KEY")
    monkeypatch.setenv("M3_DOCS_PI_API_KEY", "local-fixture-placeholder")

    responses = codex_fixture.ResponsesRun()
    for tool, arguments in (
        ("book_shipment", {"weight_kg": 2}),
        ("book_verified_shipment", {"address_kind": "business"}),
    ):
        responses.enqueue(
            codex_fixture.ModelOutput(
                function_name=f"mcp__shipping::{tool}", arguments=arguments
            )
        )
        responses.enqueue(codex_fixture.ModelOutput(text="completed locally"))
    pi_run = pi_fixture.ProviderRun()
    pi_frames: list[dict[str, Any] | None] = []

    class RecordingPiAdapter(pi_fixture.FixturePiHarnessAdapter):
        async def next_frame(self, process: Any, timeout: float | None) -> Any:
            frame = await super().next_frame(process, timeout)
            pi_frames.append(frame)
            return frame

    import m3.async_api as async_api
    from m3 import Codex, Pi
    from m3.harness.contracts import default_adapters

    codex_adapters: list[Any] = []
    failures: list[str] = []
    try:
        with codex_fixture.open_codex_responses_provider(responses) as codex_url:
            with pi_fixture.open_provider(pi_run) as pi_url:
                assert codex_url.startswith("http://127.0.0.1:")
                assert pi_url.startswith("http://127.0.0.1:")
                registry = default_adapters()

                def make_codex_adapter(harness: Any) -> Any:
                    if not isinstance(harness, Codex):
                        raise TypeError("expected Codex harness")
                    adapter = codex_fixture.FixtureCodexHarnessAdapter(
                        executable=harness.executable or codex_executable,
                        provider_url=codex_url,
                    )
                    codex_adapters.append(adapter)
                    return adapter

                def make_pi_adapter(harness: Any) -> Any:
                    if not isinstance(harness, Pi):
                        raise TypeError("expected Pi harness")
                    return RecordingPiAdapter(
                        executable=harness.executable or pi_executable,
                        environment={
                            "M3_PI_FIXTURE_PROVIDER_URL": pi_url,
                            "M3_DOCS_MCP_WIRE_MARKER": str(
                                tmp_path / "pi-mcp-wire.jsonl"
                            ),
                        },
                    )

                registry.register("codex", make_codex_adapter)
                registry.register("pi", make_pi_adapter)
                monkeypatch.setattr(async_api, "_default_adapters", lambda: registry)
                try:
                    source.test_codex_uses_fresh_plan_for_each_session_turn()
                except AssertionError as error:
                    frames = codex_adapters[0].native_frames if codex_adapters else []
                    failures.append(
                        f"Codex guide test failed: {error}; frames={frames!r}"
                    )
                try:
                    source.test_pi_uses_fresh_plan_for_each_session_turn()
                except AssertionError as error:
                    failures.append(
                        f"Pi guide test failed: {error}; frames={pi_frames!r}"
                    )
    finally:
        if runtime_manager is not None:
            asyncio.run(runtime_manager.close())

    assert not failures, "\n".join(failures)
    assert len(responses.requests) == 4
    assert len(responses.responses) == 4
    assert all(response.status == 200 for response in responses.responses)
    first_turn_tools = codex_fixture.function_tools(responses.requests[0])
    second_turn_tools = codex_fixture.function_tools(responses.requests[2])
    assert "mcp__shipping::book_shipment" in first_turn_tools
    assert "mcp__shipping::book_verified_shipment" in first_turn_tools
    assert "mcp__shipping::book_shipment" in second_turn_tools
    assert "mcp__shipping::book_verified_shipment" in second_turn_tools
    assert '"type":"function_call"' in responses.responses[0].body
    assert '"type":"message"' in responses.responses[1].body
    assert '"type":"function_call"' in responses.responses[2].body
    assert '"type":"message"' in responses.responses[3].body
    for request in (responses.requests[1], responses.requests[3]):
        request_input = request.body.get("input")
        assert isinstance(request_input, list)
        assert any(
            isinstance(item, dict) and item.get("type") == "function_call_output"
            for item in request_input
        )
    assert all("authorization" not in item.headers for item in responses.requests)
    assert len(pi_run.requests) == 4
    pi_tool_results = [
        frame
        for frame in pi_frames
        if frame and frame.get("type") == "tool_execution_end"
    ]
    assert len(pi_tool_results) == 2
    assert all(frame.get("isError") is False for frame in pi_tool_results)


def _load_test_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _binary_version(executable: str) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return completed.stdout.strip()


async def _acquire_elicitation_runtime_pair(
    project: Path, cache: Path
) -> tuple[Any, Any, Any]:
    from m3.runtime import RuntimeManager

    manager = RuntimeManager(cache_root=cache, project_root=project)
    codex = await manager.acquire("codex", "0.156.1")
    pi = await manager.acquire("pi", "0.85.1")
    return manager, codex, pi
