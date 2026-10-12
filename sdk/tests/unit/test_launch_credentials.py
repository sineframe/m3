"""Named credentials rotate only when a new native launch is collected."""

import pickle
from pathlib import Path

import pytest

from m3._literal_store import RunLiteralStore
from m3.harness._launch_credentials import (
    CredentialResolutionError,
    LaunchCredentialBundle,
)
from m3.server_group import HarnessServerConfig
from m3.types import SecretReference, TransportKind


def server() -> HarnessServerConfig:
    return HarnessServerConfig(
        key="search",
        transport=TransportKind.STDIO,
        available=True,
        required=True,
        connection_id="search-connection",
        command="private-command",
        args=("${TOKEN:-literal}", "", "private-argument"),
        cwd="private-directory",
        environment={"SERVER_KEY": SecretReference(source="environment", name="TOKEN")},
        headers={"Authorization": "Bearer ${TOKEN}"},
        endpoint="https://example.test/${TOKEN}",
    )


def test_rotation_affects_future_launches_but_not_deferred_or_restarted_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKEN", "initial-credential")
    configuration = server()
    selected = {"API_KEY": SecretReference(source="environment", name="TOKEN")}
    first = LaunchCredentialBundle((configuration,), credential_references=selected)
    try:
        monkeypatch.setenv("TOKEN", "rotated-credential")
        for _ in range(2):
            assert first.credential_environment() == {"API_KEY": "initial-credential"}
            assert first.server_environment("search-connection") == {
                "SERVER_KEY": "initial-credential"
            }
            assert first.server_headers("search-connection") == {
                "Authorization": "Bearer initial-credential"
            }
            assert first.server_endpoint("search-connection") == (
                "https://example.test/initial-credential"
            )
        second = LaunchCredentialBundle(
            (configuration,), credential_references=selected
        )
        try:
            assert second.credential_environment() == {"API_KEY": "rotated-credential"}
        finally:
            second.close()
        assert configuration.environment["SERVER_KEY"] == selected["API_KEY"]
        assert configuration.headers["Authorization"] == "Bearer ${TOKEN}"
        assert first.server_command("search-connection") == (
            "private-command",
            "${TOKEN:-literal}",
            "",
            "private-argument",
        )
    finally:
        first.close()


def test_registry_contains_every_known_launch_input_without_ambient_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNSELECTED_API_KEY", "unselected-value")
    bundle = LaunchCredentialBundle(
        (server(),),
        credential_references={},
        resolver_environment={"TOKEN": "selected"},
        child_environment={"CUSTOM_INPUT": "private-child-input"},
    )
    try:
        assert bundle.redaction().secrets >= {
            "selected",
            "Bearer selected",
            "Bearer ${TOKEN}",
            "https://example.test/selected",
            "private-command",
            "private-argument",
            "private-directory",
            "${TOKEN:-literal}",
            "private-child-input",
        }
        assert "unselected-value" not in bundle.redaction().secrets
        assert "selected" not in repr(bundle)
        with pytest.raises(TypeError, match="cannot be serialized"):
            pickle.dumps(bundle)
    finally:
        bundle.close()


def test_frozen_memory_literal_is_used_without_rereading_a_source() -> None:
    registry = RunLiteralStore("run")
    frozen = registry.freeze("original-frozen-value")
    configuration = server()
    configuration.environment["SERVER_KEY"] = frozen
    bundle = LaunchCredentialBundle(
        (configuration,),
        credential_references={},
        resolver_environment={"TOKEN": "named-value"},
        literal_registry=registry,
    )
    registry.close()
    try:
        assert bundle.server_environment("search-connection") == {
            "SERVER_KEY": "original-frozen-value"
        }
    finally:
        bundle.close()


@pytest.mark.parametrize(
    "reference",
    [
        SecretReference(source="provider", name="explicit-file-provider"),
        SecretReference(source="environment", name="MISSING_NAMED_VALUE"),
    ],
)
def test_unavailable_sources_fail_with_value_free_errors(
    reference: SecretReference,
) -> None:
    with pytest.raises(CredentialResolutionError):
        LaunchCredentialBundle((), credential_references={"KEY": reference})


def test_pinned_source_and_effective_inputs_reject_mutation() -> None:
    from dataclasses import replace

    configuration = server()
    bundle = LaunchCredentialBundle(
        (configuration,),
        credential_references={},
        resolver_environment={"TOKEN": "selected"},
    )
    try:
        configuration.environment["SERVER_KEY"] = "changed-value"
        with pytest.raises(
            CredentialResolutionError, match="pinned launch inputs changed"
        ):
            bundle.validate_inputs((configuration,))
        effective = replace(configuration, command="capture-wrapper", environment={})
        bundle.bind_effective_inputs((effective,))
        with pytest.raises(
            CredentialResolutionError, match="pinned launch inputs changed"
        ):
            bundle.validate_inputs(
                (replace(effective, args=("changed-argument",)),), effective=True
            )
    finally:
        bundle.close()


@pytest.mark.asyncio
async def test_command_literal_collision_redacts_public_model_without_breaking_native_session() -> (
    None
):
    from pathlib import Path

    from m3._types.specs import AgentSpec
    from m3.async_api import AsyncMCPTestKit
    from m3.types import OpenCode, ServerBinding, StdioServer

    executable = str(Path(__file__).parents[1] / "fixtures/opencode_serve_fixture.py")
    spec = AgentSpec(
        harness=OpenCode(model="fixture", executable=executable),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(spec)
        async with session:
            assert (await session.send("one")).error is None
            assert (await session.send("two")).error is None
        assert session.result.trace is not None
        runtime = session.result.trace.view().runtime
        assert runtime.model_id.state.value == "observed"
        assert runtime.model_id.value == "[REDACTED]"


@pytest.mark.asyncio
async def test_deferred_stdio_launch_and_actual_restart_reuse_pinned_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json
    import os
    import sys

    from mcp.client.stdio import get_default_environment

    from m3.transport.capture_proxy import McpCaptureManager

    child = tmp_path / "child.py"
    child.write_text(
        "import json,os,sys\n"
        "request=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc':'2.0','id':request['id'],"
        "'result':{'value':os.environ['SERVER_KEY']}}),flush=True)\n"
    )
    configuration = HarnessServerConfig(
        key="source",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="source-connection",
        command=sys.executable,
        args=(str(child),),
        environment={"SERVER_KEY": "Bearer ${TOKEN}"},
    )
    manager = McpCaptureManager(tmp_path / "capture")
    monkeypatch.delenv("TOKEN", raising=False)
    prepared = manager.prepare((configuration,))
    assert prepared[0].environment == configuration.environment
    assert not list(manager.root.glob("*.env.json"))
    assert not manager.enforces_portable_policy((configuration.connection_id,))
    monkeypatch.setenv("TOKEN", "first-${INNER}-credential")
    credentials = LaunchCredentialBundle(
        manager.credential_configurations(),
        credential_references={},
        stdio_baseline=get_default_environment(),
    )
    try:
        monkeypatch.setenv("TOKEN", "rotated-credential")
        monkeypatch.delenv("INNER", raising=False)
        for _ in range(2):
            instrumented = (await manager.activate(credentials))[0]
            handoff = manager.root / "source-connection.env.json"
            assert handoff.stat().st_mode & 0o777 == 0o600
            process = await asyncio.create_subprocess_exec(
                instrumented.command,
                *instrumented.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
                },
            )
            assert process.stdin is not None and process.stdout is not None
            try:
                process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"lookup"}\n')
                await process.stdin.drain()
                reply = await asyncio.wait_for(process.stdout.readline(), timeout=5)
                process.stdin.close()
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
                assert process.returncode == 0, stderr.decode(errors="replace")
                assert (
                    json.loads(reply)["result"]["value"]
                    == "Bearer first-${INNER}-credential"
                )
                assert not handoff.exists()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        capture_file = Path(instrumented.args[instrumented.args.index("--capture") + 1])
        assert "first-${INNER}-credential" not in capture_file.read_text()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_rejected_native_preflight_never_resolves_stdio_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from m3._types.specs import AgentSpec
    from m3.async_api import AsyncMCPTestKit
    from m3.errors import UnsupportedFeature
    from m3.harness.claude import ClaudeCodeHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.transport.capture_proxy import McpCaptureManager
    from m3.types import ClaudeCode, Readiness, ServerBinding, StdioServer

    class UnsupportedNativeAdapter(ClaudeCodeHarnessAdapter):
        async def preflight(self, launch: HarnessLaunch) -> Readiness:
            assert isinstance(launch.capture, McpCaptureManager)
            assert not list(launch.capture.root.glob("*.env.json"))
            assert launch._credentials is None
            return Readiness(ready=False, reason="unsupported_fixture_capability")

    executable = str(Path(__file__).parents[1] / "fixtures/claude_stream_fixture.py")
    monkeypatch.delenv("UNAVAILABLE_SELECTED_CREDENTIAL", raising=False)
    spec = AgentSpec(
        harness=ClaudeCode(model="fixture", executable=executable),
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="source",
                    command=sys.executable,
                    environment={
                        "SERVER_KEY": SecretReference(
                            source="environment", name="UNAVAILABLE_SELECTED_CREDENTIAL"
                        )
                    },
                )
            ),
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(
            spec, adapter=UnsupportedNativeAdapter(executable=executable)
        )
        with pytest.raises(UnsupportedFeature, match="unsupported_fixture_capability"):
            async with session:
                pass


@pytest.mark.asyncio
async def test_direct_codex_preparation_pins_refs_and_rotation_waits_for_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from m3._types.specs import AgentSpec
    from m3.harness.codex import CodexHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, ServerBinding, StdioServer

    monkeypatch.setenv("SELECTED_CODEX_CREDENTIAL", " first-key ")
    definition = StdioServer(name="search", command="private-command")
    spec = AgentSpec(
        harness=Codex(
            model="fixture",
            credential_references={
                "OPENAI_API_KEY": SecretReference(
                    source="environment", name="SELECTED_CODEX_CREDENTIAL"
                )
            },
        ),
        servers=(ServerBinding(server=definition),),
    )
    configuration = server()
    monkeypatch.setenv("TOKEN", "server-key")
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (configuration,), spec.tool_policy
    )
    adapter = CodexHarnessAdapter()
    for name in ("first", "repeat", "changed", "next"):
        (tmp_path / name).mkdir()
    first = adapter.environment_for_launch(launch, tmp_path / "first")
    monkeypatch.setenv("SELECTED_CODEX_CREDENTIAL", "second-key")
    repeated = adapter.environment_for_launch(launch, tmp_path / "repeat")
    assert first["OPENAI_API_KEY"] == repeated["OPENAI_API_KEY"] == " first-key "
    assert adapter._prepared_credentials is not None
    assert {
        " first-key ",
        "first-key",
    } <= adapter._prepared_credentials.redaction().secrets
    configuration.environment["SERVER_KEY"] = "changed-after-collection"
    with pytest.raises(CredentialResolutionError, match="pinned launch inputs changed"):
        adapter.environment_for_launch(launch, tmp_path / "changed")
    await adapter.close()
    assert adapter._prepared_credentials is None
    next_launch = replace(launch, configurations=(server(),))
    next_environment = adapter.environment_for_launch(next_launch, tmp_path / "next")
    assert next_environment["OPENAI_API_KEY"] == "second-key"
    await adapter.close()


def test_emitted_native_literals_are_pinned_without_expanding_them() -> None:
    from dataclasses import replace

    from m3._types.specs import AgentSpec
    from m3.harness._launch_credentials import collect_native_credentials
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, ServerBinding, StdioServer

    registry = RunLiteralStore("invocation")
    selected = registry.freeze("private-profile-${UNAVAILABLE}")
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(
            ServerBinding(server=StdioServer(name="declared", command="mcp-child")),
        ),
    )
    configuration = HarnessServerConfig(
        "declared",
        TransportKind.STDIO,
        True,
        True,
        "declared-connection",
        command="mcp-child",
    )
    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot(),
        (configuration,),
        spec.tool_policy,
        _literal_registry=registry,
        _native_secret_inputs=(selected, "private-domain.example"),
    )
    bundle = collect_native_credentials(
        launch, resolver_environment={}, child_environment={}
    )
    try:
        registry.close()
        assert {
            "private-profile-${UNAVAILABLE}",
            "private-domain.example",
        } <= bundle.redaction().secrets
        bundle.validate_selection(launch)
        with pytest.raises(CredentialResolutionError, match="native settings changed"):
            bundle.validate_selection(replace(launch, _native_secret_inputs=()))
        with pytest.raises(CredentialResolutionError, match="harness inputs changed"):
            bundle.validate_selection(
                replace(
                    launch,
                    spec=spec.model_copy(update={"harness": Codex(model="changed")}),
                )
            )
    finally:
        bundle.close()


@pytest.mark.asyncio
async def test_default_login_bytes_and_literals_are_reused_until_session_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from m3._types.specs import AgentSpec
    from m3.harness.codex import CodexHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, ServerBinding, StdioServer

    host = tmp_path / "host"
    host.mkdir()
    source = host / "auth.json"
    initial = b'{"tokens": {"access_token": "first-login"}}\n'
    rotated = b'{"tokens": {"access_token": "future-login"}}\n'
    source.write_bytes(initial)
    source.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(host))
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(
            ServerBinding(server=StdioServer(name="declared", command="mcp-child")),
        ),
    )
    configuration = HarnessServerConfig(
        "declared",
        TransportKind.STDIO,
        True,
        True,
        "declared-connection",
        command="mcp-child",
    )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (configuration,), spec.tool_policy
    )
    adapter = CodexHarnessAdapter()
    for name in ("first", "restart", "future"):
        (tmp_path / name).mkdir()
    first = adapter.environment_for_launch(launch, tmp_path / "first")
    source.write_bytes(rotated)
    assert adapter.environment_for_launch(launch, tmp_path / "first") == first
    restart = adapter.environment_for_launch(launch, tmp_path / "restart")
    assert Path(first["CODEX_HOME"], "auth.json").read_bytes() == initial
    assert Path(restart["CODEX_HOME"], "auth.json").read_bytes() == initial
    assert adapter._prepared_credentials is not None
    assert "first-login" in adapter._prepared_credentials.redaction().secrets
    assert "future-login" not in adapter._prepared_credentials.redaction().secrets
    await adapter.close()
    future = adapter.environment_for_launch(replace(launch), tmp_path / "future")
    assert Path(future["CODEX_HOME"], "auth.json").read_bytes() == rotated
    await adapter.close()


@pytest.mark.asyncio
async def test_bounded_default_login_rejects_missing_owner_before_file_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from m3._types.specs import AgentSpec
    from m3.errors import UnsupportedFeature
    from m3.harness.codex import CodexHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, ServerBinding, StdioServer

    def unexpected_access(path: Path) -> bool:
        pytest.fail("bounded credentials must not use direct native auth IO")

    monkeypatch.setattr(Path, "is_file", unexpected_access)
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(
            ServerBinding(server=StdioServer(name="declared", command="mcp-child")),
        ),
    )
    configuration = HarnessServerConfig(
        "declared",
        TransportKind.STDIO,
        True,
        True,
        "declared-connection",
        command="mcp-child",
    )
    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot(),
        (configuration,),
        spec.tool_policy,
        _credential_deadline=time.monotonic() + 5,
    )
    adapter = CodexHarnessAdapter()
    with pytest.raises(UnsupportedFeature, match="owned Codex login"):
        await adapter.prepare_launch_credentials(launch)
    assert adapter._prepared_credentials is None
    assert tuple(tmp_path.iterdir()) == ()
    await adapter.close()


def test_original_deadline_is_checked_after_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3._types.specs import AgentSpec
    from m3.harness import _launch_credentials
    from m3.harness.contracts import HarnessLaunch, HarnessStartupError
    from m3.server_group import ServerGroupSnapshot
    from m3.types import Codex, ServerBinding, StdioServer

    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(
            ServerBinding(server=StdioServer(name="declared", command="mcp-child")),
        ),
    )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), (), spec.tool_policy, _credential_deadline=10
    )
    clock = iter((9.0, 10.0))
    monkeypatch.setattr(_launch_credentials.time, "monotonic", lambda: next(clock))
    with pytest.raises(HarnessStartupError, match="deadline exceeded"):
        _launch_credentials.collect_native_credentials(
            launch, resolver_environment={}, child_environment={}
        )


@pytest.mark.asyncio
async def test_eager_capture_is_adopted_without_resolving_its_refs_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3._types.specs import AgentSpec
    from m3.harness.codex import CodexHarnessAdapter
    from m3.harness.contracts import HarnessLaunch
    from m3.server_group import ServerGroupSnapshot
    from m3.transport.capture_proxy import McpCaptureManager
    from m3.types import Codex, ServerBinding, StdioServer

    monkeypatch.setenv("TOKEN", "captured-once")
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(
            ServerBinding(server=StdioServer(name="source", command="ordinary-child")),
        ),
    )
    source = HarnessServerConfig(
        "source",
        TransportKind.STDIO,
        True,
        True,
        "source-connection",
        command="ordinary-child",
        environment={"TOKEN": SecretReference(source="environment", name="TOKEN")},
    )
    capture = McpCaptureManager(tmp_path / "capture")
    adapter = CodexHarnessAdapter(
        executable=str(
            Path(__file__).parents[1] / "fixtures" / "codex_app_server_fixture.py"
        )
    )
    try:
        instrumented = await capture.instrument((source,))
        monkeypatch.delenv("TOKEN")
        launch = HarnessLaunch(
            spec, ServerGroupSnapshot(), instrumented, spec.tool_policy, capture=capture
        )
        session = await adapter.open(launch)
        assert session.session_id == "fixture-thread"
        bundle = adapter._prepared_credentials
        assert bundle is not None
        assert bundle.server_environment("source-connection") == {
            "TOKEN": "captured-once"
        }
        assert bundle.reference_environment() == {"TOKEN": "captured-once"}
        assert "captured-once" in bundle.redaction().secrets
    finally:
        await adapter.close()
        await capture.close()
