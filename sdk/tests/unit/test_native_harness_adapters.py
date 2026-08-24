from __future__ import annotations

from pathlib import Path

import pytest

from mcp_pal.agent_session import AsyncAgentSession
from mcp_pal.harness import default_harness_adapter_registry
from mcp_pal.harness.claude import ClaudeCodeHarnessAdapter
from mcp_pal.harness.contracts import HarnessLaunch, HarnessStartupError, HarnessTurnRequest, HarnessTurnResult
from mcp_pal.harness.opencode import OpenCodeHarnessAdapter
from mcp_pal.harness.native import _server_configuration, write_config
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.server_group import HarnessServerConfiguration, ServerGroupSnapshot
from mcp_pal.types import ACPAgent, AgentExecutionSpec, ClaudeCode, OpenCode, RestrictiveToolPolicy, SecretReference, ServerBinding, StdioServer, TextContent, TransportKind
from mcp_pal.errors import UnsupportedFeature


def _spec() -> AgentExecutionSpec:
    return AgentExecutionSpec(
        harness=ACPAgent(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )


def _launch() -> HarnessLaunch:
    spec = _spec()
    return HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)


def _response_text(result: HarnessTurnResult) -> str:
    response = result.response
    assert response is not None
    block = response.content[0]
    assert isinstance(block, TextContent)
    return block.text


@pytest.mark.asyncio
async def test_claude_uses_one_stream_process_across_turns() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    first = await session.send(HarnessTurnRequest.from_message("one"))
    second = await session.send(HarnessTurnRequest.from_message("two"))
    assert first.response is not None and _response_text(first) == "one"
    assert second.response is not None and _response_text(second) == "two"
    assert session.snapshot().turns == 2
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_cleanup_is_idempotent() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    await adapter.open(_launch())
    await adapter.close()
    await adapter.close()


@pytest.mark.asyncio
async def test_claude_keeps_config_until_owned_process_cleanup() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    await adapter.open(_launch())
    config = adapter._config
    assert config is not None and config.exists()
    assert config.stat().st_mode & 0o777 == 0o600
    await adapter.close()
    assert not config.exists()


@pytest.mark.asyncio
async def test_opencode_uses_one_serve_and_session_across_turns() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py")
    adapter = OpenCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    first = await session.send(HarnessTurnRequest.from_message("one"))
    second = await session.send(HarnessTurnRequest.from_message("two"))
    assert first.response is not None and _response_text(first) == "one"
    assert second.response is not None and _response_text(second) == "two"
    assert session.snapshot().turns == 2
    await adapter.close()


@pytest.mark.asyncio
async def test_missing_native_executable_is_not_ready() -> None:
    adapter = ClaudeCodeHarnessAdapter(executable="mcp-pal-no-such-claude")
    readiness = await adapter.preflight(_launch())
    assert readiness.ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "executable"),
    (
        (ClaudeCodeHarnessAdapter, "claude_stream_fixture.py"),
        (OpenCodeHarnessAdapter, "opencode_serve_fixture.py"),
    ),
)
async def test_native_adapters_fail_closed_for_explicit_tool_policy(
    adapter_type: type[ClaudeCodeHarnessAdapter] | type[OpenCodeHarnessAdapter],
    executable: str,
) -> None:
    path = str(Path(__file__).parents[1] / "fixtures" / executable)
    adapter = adapter_type(executable=path)
    spec = _spec().model_copy(
        update={"tool_policy": RestrictiveToolPolicy(allowed_tools=("fixture:read",))}
    )
    session = AsyncAgentSession(spec, adapter)
    with pytest.raises(UnsupportedFeature):
        await session.__aenter__()
    assert session._closed is True


def test_default_registry_selects_real_native_adapters_without_fake_fallback() -> None:
    registry = default_harness_adapter_registry()
    claude = registry.resolve(_spec().model_copy(update={"harness": ClaudeCode(model="fixture", executable="missing-claude")}))
    opencode = registry.resolve(_spec().model_copy(update={"harness": OpenCode(model="fixture", executable="missing-opencode")}))
    assert isinstance(claude, ClaudeCodeHarnessAdapter)
    assert isinstance(opencode, OpenCodeHarnessAdapter)


def test_native_mcp_config_redacts_credential_keys() -> None:
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfiguration(
                key="fixture",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="connection-1",
                command="fixture",
                environment={"API_KEY": "secret-value", "SAFE": "safe-value"},
                headers={"Authorization": "Bearer secret-value"},
            ),
        ),
        base.tool_policy,
    )
    config = _server_configuration(launch)
    server = config["mcpServers"]["fixture"]
    assert server["env"]["API_KEY"] == "[REDACTED]"
    assert server["env"]["SAFE"] == "safe-value"
    assert "secret-value" not in repr(config)


def test_native_mcp_config_resolves_environment_reference_only_in_0600_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PAL_NATIVE_SECRET", "native-secret-value")
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfiguration(
                key="fixture",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="connection-1",
                command="fixture",
                environment={"TOKEN": SecretReference(source="environment", name="MCP_PAL_NATIVE_SECRET")},
            ),
        ),
        base.tool_policy,
    )
    config = write_config(tmp_path, launch)
    assert config.stat().st_mode & 0o777 == 0o600
    assert 'native-secret-value' in config.read_text(encoding="utf-8")
    assert 'native-secret-value' not in repr(launch)
    config.unlink()


def test_native_mcp_config_rejects_provider_reference_without_resolver(tmp_path: Path) -> None:
    base = _launch()
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        (
            HarnessServerConfiguration(
                key="fixture",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="connection-1",
                command="fixture",
                environment={"TOKEN": SecretReference(source="provider", name="provider-key")},
            ),
        ),
        base.tool_policy,
    )
    with pytest.raises(HarnessStartupError) as caught:
        write_config(tmp_path, launch)
    assert "provider-key" not in str(caught.value)
    assert not (tmp_path / "mcp-config.json").exists()


@pytest.mark.asyncio
async def test_public_kit_preserves_native_claude_conversation_for_three_turns() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=adapter)
        async with session:
            for text in ("one", "two", "three"):
                result = await session.send(text)
                assert result.error is None
                assert result.evidence["usage_requested"] is True
                assert result.evidence["usage_enforced"] is False
                assert result.evidence["usage_observed"] is True
                assert result.evidence["usage_unavailable"] is False
        assert len(session.result.turns) == 3
        assert session.result.snapshot.outcome is not None
        assert session.result.snapshot.outcome.value == "completed"


@pytest.mark.asyncio
async def test_public_kit_default_registry_uses_real_claude_adapter() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py")
    spec = _spec().model_copy(update={"harness": ClaudeCode(model="fixture", executable=executable)})
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(spec)
        async with session:
            for text in ("one", "two", "three"):
                result = await session.send(text)
                assert result.error is None
        assert type(session.adapter).__name__ == "ClaudeCodeHarnessAdapter"
        assert len(session.result.turns) == 3


@pytest.mark.asyncio
async def test_public_kit_exposes_opencode_usage_evidence() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py")
    spec = _spec().model_copy(update={"harness": OpenCode(model="fixture", executable=executable)})
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(spec)
        async with session:
            result = await session.send("one")
            assert result.error is None
            assert result.evidence["usage_requested"] is True
            assert result.evidence["usage_enforced"] is False
            assert result.evidence["usage_observed"] is True
            assert result.evidence["usage_unavailable"] is False


@pytest.mark.asyncio
async def test_native_claude_timeout_reaps_process_and_close_is_safe() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(HarnessTurnRequest.from_message("sleep", timeout_seconds=0.05))
    assert result.status == "timed_out"
    await adapter.close()
    assert adapter._owner is None


@pytest.mark.asyncio
async def test_native_claude_cancel_reaps_process() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_stream_fixture.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    await session.cancel()
    await adapter.close()
    assert adapter._owner is None


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ("huge", "binary"))
async def test_native_claude_hostile_output_is_bounded_and_typed(message: str) -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "claude_hostile_output_fixture.py")
    adapter = ClaudeCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(HarnessTurnRequest.from_message(message, timeout_seconds=2))
    assert result.error is not None
    assert result.error.code.value == "protocol_error"
    await adapter.close()
    assert adapter._owner is None


@pytest.mark.asyncio
async def test_native_opencode_oversized_http_response_is_bounded_and_typed() -> None:
    executable = str(Path(__file__).parents[1] / "fixtures" / "opencode_serve_fixture.py")
    adapter = OpenCodeHarnessAdapter(executable=executable)
    session = await adapter.open(_launch())
    result = await session.send(HarnessTurnRequest.from_message("huge", timeout_seconds=2))
    assert result.error is not None
    assert result.error.code.value == "protocol_error"
    await adapter.close()
    assert adapter._owner is None
