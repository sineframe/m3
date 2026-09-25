from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]
from pathlib import Path
from types import SimpleNamespace

import pytest

import m3.harness.codex as codex_module
from m3._types.specs import AgentSpec
from m3.agent_session import (
    AdapterTurn,
    AsyncAgentSession,
    _require_elicitation_capability,
)
from m3.elicitation import expect_form
from m3.errors import UnsupportedFeature
from m3.harness._rpc_native import NativeRPCAdapter
from m3.harness.codex import (
    CodexHarnessAdapter,
    codex_configuration,
    render_codex_config,
)
from m3.harness.contracts import (
    HarnessLaunch,
    HarnessStartupError,
    HarnessTurnRequest,
    Readiness,
)
from m3.harness.pi import PiHarnessAdapter
from m3.harness.pi_extension.bridge import (
    ActionContextChannel,
    BridgeActionStatus,
    MCPBridge,
    qualified_tool_name,
)
from m3.interaction_handlers import Interactions
from m3.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase
from m3.server_group import HarnessServerConfig, ServerGroupSnapshot, ServerRecord
from m3.transport.capture_proxy import McpCaptureManager
from m3.types import (
    Codex,
    PermissionPolicy,
    Pi,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    TurnResponse,
    UserMessage,
)

ROOT = Path(__file__).parents[1]
CODEX_FIXTURE = ROOT / "fixtures" / "codex_app_server_fixture.py"
PI_FIXTURE = ROOT / "fixtures" / "pi_rpc_fixture.py"


@pytest.mark.asyncio
async def test_pi_managed_round_limit_reaches_action_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PiHarnessAdapter()
    adapter._session = SimpleNamespace(turn_count=0)
    adapter._action_channel = ActionContextChannel(
        tmp_path / "pi-action-context.json", tmp_path / "pi-action-status.json"
    )
    adapter._managed_input_runtime = SimpleNamespace(
        execution_id="execution-1",
        bound_identity=("pi-session", "m3-session", "turn-1"),
    )
    seen_limits: list[int] = []

    async def fake_native_send(
        self: NativeRPCAdapter,
        _message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: object = None,
    ) -> AdapterTurn:
        del timeout, metadata
        channel = adapter._action_channel
        assert channel is not None
        context = channel.read_context()
        assert context is not None
        seen_limits.append(context.round_limit)
        channel.write_status(
            BridgeActionStatus(context.generation, context.turn_sequence, "completed")
        )
        return AdapterTurn(
            response=TurnResponse(content=(TextContent(text="continued"),))
        )

    monkeypatch.setattr(NativeRPCAdapter, "send", fake_native_send)

    result = await adapter.send(
        UserMessage(content="continue"), elicitation_round_limit=11
    )

    assert result.response is not None
    assert result.response.content == (TextContent(text="continued"),)
    assert seen_limits == [11]


def _launch(
    harness: object, *, configurations: tuple[HarnessServerConfig, ...] = ()
) -> HarnessLaunch:
    spec = AgentSpec(
        harness=harness,
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    return HarnessLaunch(
        spec, ServerGroupSnapshot(), configurations, RestrictiveToolPolicy()
    )


@pytest.mark.parametrize(
    "server,tool",
    [("orders", "shipping_quote"), ("服务/🚚", "报价 tool"), ("x" * 200, "y" * 200)],
)
def test_pi_qualified_names_are_readable_safe_and_bounded(
    server: str, tool: str
) -> None:
    value = qualified_tool_name(server, tool)
    assert len(value) <= 64
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value)
    assert "shipping_quote" in qualified_tool_name("orders", "shipping_quote")
    assert qualified_tool_name("a-b", "c") != qualified_tool_name("a_b", "c")


def test_pi_rejects_tampered_dynamic_tool_map(tmp_path: Path) -> None:
    adapter = PiHarnessAdapter()
    adapter._tool_map_path = str(tmp_path / "mapping.json")
    adapter._tool_servers = {"orders"}
    (tmp_path / "mapping.json").write_text(
        json.dumps({"forged": ["orders", "shipping_quote"]})
    )
    assert adapter._tool_identity("forged") == (None, "forged")
    (tmp_path / "mapping.json").write_bytes(b"\xff")
    assert adapter._tool_identity("forged") == (None, "forged")


def test_pi_accepts_bridge_only_dynamic_tool_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, tool = "remote", "shipping_quote"
    path = tmp_path / "mapping.json"
    monkeypatch.setenv("M3_PI_TOOL_MAP", str(path))
    MCPBridge({server: {tool: {"description": "quotes"}}}).list_tools()
    adapter = PiHarnessAdapter()
    adapter._tool_map_path = str(path)
    adapter._tool_servers = {server}
    assert adapter._tool_identity(qualified_tool_name(server, tool)) == (server, tool)


def test_pi_declares_verified_agent_mrtr_capabilities() -> None:
    interaction = PiHarnessAdapter().capabilities.interaction
    assert interaction.supports_elicitation
    assert interaction.preserves_request_keys
    assert interaction.preserves_multi_request_rounds
    assert interaction.supports_interaction_cancellation
    assert interaction.retry_owner == "m3"
    assert not interaction.supports_interaction_resume
    assert not interaction.supports_idempotent_response_delivery


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", ("managed_input", "spec_plan"))
@pytest.mark.parametrize("system_binary", ("absent", "unsupported"))
async def test_codex_managed_mrtr_selects_runtime_before_capability_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feature: str,
    system_binary: str,
) -> None:
    order: list[str] = []
    system_executable = tmp_path / "system-codex"
    if system_binary == "unsupported":
        system_executable.write_text("not executed by this test\n")

    managed_executable = str(CODEX_FIXTURE.resolve())

    class Lease:
        executable = managed_executable

        def __init__(self) -> None:
            self.environment: dict[str, str] = {}
            self.provenance = {"version": "0.156.1"}

        async def release(self) -> None:
            return None

    class RuntimeManager:
        async def acquire(self, kind: str, selector: str) -> Lease:
            order.append(f"acquire:{kind}:{selector}")
            return Lease()

    class ServerManager:
        capture = None

        def __init__(self) -> None:
            self.defaults: object = None

        async def start(
            self, *, stdio_environment_defaults: object = None
        ) -> ServerGroupSnapshot:
            order.append("server-start")
            self.defaults = stdio_environment_defaults
            return ServerGroupSnapshot()

        def snapshot(self) -> ServerGroupSnapshot:
            return ServerGroupSnapshot()

        def configurations(self) -> tuple[object, ...]:
            return ()

        async def close(self) -> None:
            return None

    probes: list[str] = []

    def fake_probe(executable: str, args: tuple[str, ...]) -> str | None:
        order.append(f"probe:{executable}")
        probes.append(executable)
        if args == ("--version",) and executable == managed_executable:
            return "codex-cli 0.156.1"
        if args == ("--version",):
            return "codex-cli 0.1.0" if system_binary == "unsupported" else None
        return None

    monkeypatch.setattr(codex_module, "probe_help", fake_probe)
    adapter = CodexHarnessAdapter(executable=str(system_executable))

    async def fake_preflight(_launch: HarnessLaunch) -> Readiness:
        order.append("preflight")
        return Readiness(ready=True)

    async def fake_open(_launch: HarnessLaunch) -> None:
        order.append("open")
        return None

    monkeypatch.setattr(adapter, "preflight", fake_preflight)
    monkeypatch.setattr(adapter, "open", fake_open)
    server_manager = ServerManager()
    spec = AgentSpec(
        harness=Codex(
            model="fixture",
            executable=str(system_executable),
            runtime="managed",
            version="0.156.1",
        ),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        elicitation=(
            expect_form("address").accept({"city": "Pune"})
            if feature == "spec_plan"
            else None
        ),
    )
    session = AsyncAgentSession(
        spec,
        adapter,
        server_manager=server_manager,
        runtime_manager=RuntimeManager(),
        managed_input_runtime=object() if feature == "managed_input" else None,
    )

    await session.__aenter__()
    try:
        assert order[0] == "acquire:codex:0.156.1"
        assert probes == [managed_executable]
        assert order.index(f"probe:{managed_executable}") < order.index("server-start")
        assert (
            order.index("server-start") < order.index("preflight") < order.index("open")
        )
        assert server_manager.defaults == {
            "fixture": {"CODEX_MCP_PROTOCOL_VERSION": "2026-07-28"}
        }
        assert adapter.capabilities.interaction.supports_elicitation
    finally:
        await session.aclose()


def test_codex_capability_cache_tracks_executable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_executable = tmp_path / "system-codex"
    system_executable.write_text("system codex placeholder\n")
    managed_executable = tmp_path / "managed-codex"
    managed_executable.write_text("managed codex placeholder\n")
    probes: list[str] = []

    def fake_probe(executable: str, _args: tuple[str, ...]) -> str:
        probes.append(executable)
        return (
            "codex-cli 0.156.1"
            if executable == str(managed_executable)
            else "codex-cli 0.1.0"
        )

    monkeypatch.setattr(codex_module, "probe_help", fake_probe)
    adapter = CodexHarnessAdapter(executable=str(system_executable))
    assert not adapter.capabilities.interaction.supports_elicitation

    adapter.executable = str(managed_executable)
    assert adapter.capabilities.interaction.supports_elicitation
    assert probes == [str(system_executable), str(managed_executable)]


def test_codex_capability_cache_rechecks_binary_created_at_same_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "managed-codex"
    probes: list[str] = []

    def fake_probe(path: str, _args: tuple[str, ...]) -> str | None:
        probes.append(path)
        return "codex-cli 0.156.1" if executable.exists() else None

    monkeypatch.setattr(codex_module, "probe_help", fake_probe)
    adapter = CodexHarnessAdapter(executable=str(executable))
    assert not adapter.capabilities.interaction.supports_elicitation

    executable.write_text("managed codex placeholder\n")
    assert adapter.capabilities.interaction.supports_elicitation
    assert probes == [str(executable), str(executable)]


@pytest.mark.asyncio
async def test_pi_preflight_downgrades_mrtr_for_unverified_version(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "pi-old.py"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('pi --mode rpc --help')\n"
        "elif '--version' in sys.argv:\n"
        "    print('0.85.0')\n"
    )
    executable.chmod(0o755)
    adapter = PiHarnessAdapter(executable=str(executable))
    readiness = await adapter.preflight(
        _launch(Pi(model="fixture", executable=str(executable)))
    )
    assert readiness.ready
    assert not adapter.capabilities.interaction.supports_elicitation
    with pytest.raises(UnsupportedFeature):
        _require_elicitation_capability(adapter)


@pytest.mark.asyncio
async def test_pi_action_channel_is_turn_scoped_and_cleaned() -> None:
    launch = _launch(Pi(model="fixture", executable=str(PI_FIXTURE)))
    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    session = await adapter.open(launch)
    result = await adapter.send("ordinary turn")
    assert result.outcome.value == "completed"
    assert adapter._action_channel is not None
    assert not adapter._action_channel.context_path.exists()
    assert not adapter._action_channel.status_path.exists()
    await session.close()


@pytest.mark.asyncio
async def test_pi_open_closes_native_session_when_control_extension_does_not_connect() -> (
    None
):
    launch = _launch(Pi(model="fixture", executable=str(PI_FIXTURE)))
    adapter = PiHarnessAdapter(
        executable=str(PI_FIXTURE), environment={"M3_PI_FIXTURE_NO_CONTROL": "1"}
    )
    with pytest.raises(HarnessStartupError, match="managed-control extension"):
        await adapter.open(launch)
    assert adapter.control_channel is None
    assert adapter._session is None
    assert adapter._process is None


@pytest.mark.asyncio
async def test_pi_completed_provider_turn_fails_when_required_plan_is_unused() -> None:
    launch = _launch(Pi(model="fixture", executable=str(PI_FIXTURE)))
    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    session = await adapter.open(launch)
    result = await adapter.send(
        "ordinary provider response",
        elicitation=expect_form("address").accept({"city": "Pune"}),
    )
    assert result.outcome.value == "failed"
    assert result.error is not None
    await session.close()


@pytest.mark.asyncio
async def test_instrumented_http_credentials_do_not_reach_harness_configs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "Bearer harness-secret"
    monkeypatch.setenv("M3_HTTP_SECRET", canary)
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"remote"})
    config = HarnessServerConfig(
        key="remote",
        transport=TransportKind.STREAMABLE_HTTP,
        required=True,
        available=True,
        connection_id="remote",
        endpoint="http://127.0.0.1:1/mcp",
        headers={
            "Authorization": SecretReference(
                source="environment", name="M3_HTTP_SECRET"
            )
        },
    )
    try:
        instrumented = (await manager.instrument((config,)))[0]
        codex_launch = _launch(
            Codex(model="fixture", executable=str(CODEX_FIXTURE)),
            configurations=(instrumented,),
        )
        codex = CodexHarnessAdapter(executable=str(CODEX_FIXTURE), environment={})
        assert canary not in render_codex_config(codex_launch)
        assert canary not in "\n".join(
            codex.environment_for_launch(codex_launch, tmp_path).values()
        )
        pi_launch = _launch(
            Pi(model="fixture", executable=str(PI_FIXTURE)),
            configurations=(instrumented,),
        )
        pi = PiHarnessAdapter(executable=str(PI_FIXTURE), environment={})
        await pi.open(pi_launch)
        assert canary not in json.dumps(pi._launch_environment or {})
        await pi.close()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_codex_native_app_server_handshake_multiturn_and_usage() -> None:
    launch = _launch(Codex(model="fixture", executable=str(CODEX_FIXTURE)))
    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    session = await adapter.open(launch)
    first = await session.send(HarnessTurnRequest.from_message("one"))
    second = await session.send(HarnessTurnRequest.from_message("two"))
    assert first.status == second.status == "completed"
    assert first.response is not None and first.response.text == "fixture response"
    assert second.response is not None and second.response.text == "fixture response"
    assert first.turn_evidence is not None
    assert any(
        getattr(item, "name", "") == "turn_id"
        for item in first.turn_evidence.observations
    )
    assert any(
        getattr(item, "kind", "") == "usage_observed"
        for item in first.turn_evidence.observations
    )
    assert session.snapshot().turns == 2
    await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_mode", ("allow", "deny"))
async def test_codex_mcp_tool_approval_uses_session_permission_policy(
    permission_mode: str,
) -> None:
    configuration = HarnessServerConfig(
        key="fixture",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="fixture-connection",
        command="fixture",
    )
    base = _launch(
        Codex(model="fixture", executable=str(CODEX_FIXTURE)),
        configurations=(configuration,),
    )
    launch = HarnessLaunch(
        base.spec,
        base.servers,
        base.configurations,
        base.tool_policy,
        Interactions(permission_policy=PermissionPolicy(mode=permission_mode)),
    )
    adapter = CodexHarnessAdapter(
        executable=str(CODEX_FIXTURE),
        environment={"M3_CODEX_FIXTURE_APPROVAL": "1"},
    )
    session = await adapter.open(launch)
    try:
        result = await session.send(HarnessTurnRequest.from_message("quote"))
        assert result.status == "completed"
        # Codex reports the requested native MCP item even when the permission
        # callback denies execution; the receipt records the actual decision.
        assert len(result.tool_calls) == 1
        assert launch.interactions is not None
        receipts = launch.interactions.receipts()
        assert len(receipts) == 1
        assert receipts[0].decision == permission_mode
    finally:
        await session.close()


def test_codex_ordinary_session_rejects_explicit_legacy_mcp_version() -> None:
    configuration = HarnessServerConfig(
        key="fixture",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="fixture-connection",
        command="fixture",
        environment={"CODEX_MCP_PROTOCOL_VERSION": "2025-06-18"},
    )
    launch = _launch(
        Codex(model="fixture", executable=str(CODEX_FIXTURE)),
        configurations=(configuration,),
    )
    with pytest.raises(HarnessStartupError, match="protocol version is unsupported"):
        render_codex_config(launch)


def test_codex_rejects_secret_backed_legacy_mcp_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M3_TEST_MCP_VERSION", "2025-06-18")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-source-home"))
    configuration = HarnessServerConfig(
        key="fixture",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="fixture-connection",
        command="fixture",
        environment={
            "CODEX_MCP_PROTOCOL_VERSION": SecretReference(
                source="environment", name="M3_TEST_MCP_VERSION"
            )
        },
    )
    launch = _launch(
        Codex(model="fixture", executable=str(CODEX_FIXTURE)),
        configurations=(configuration,),
    )
    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    with pytest.raises(HarnessStartupError, match="protocol version is unsupported"):
        adapter.environment_for_launch(launch, tmp_path)


@pytest.mark.asyncio
async def test_pi_native_rpc_handshake_multiturn_and_usage() -> None:
    launch = _launch(Pi(model="fixture", executable=str(PI_FIXTURE)))
    adapter = PiHarnessAdapter(
        executable=str(PI_FIXTURE),
        environment={"M3_PI_FIXTURE_STARTUP_EVENT": "1"},
    )
    session = await adapter.open(launch)
    result = await session.send(HarnessTurnRequest.from_message("hello"))
    assert result.status == "completed"
    assert result.response is not None and result.response.text == "fixture response"
    assert result.turn_evidence is not None
    assert any(
        getattr(item, "kind", "") == "usage_observed"
        for item in result.turn_evidence.observations
    )
    await session.close()


@pytest.mark.asyncio
async def test_pi_error_stop_reason_is_a_failed_turn() -> None:
    launch = _launch(Pi(model="fixture", executable=str(PI_FIXTURE)))
    adapter = PiHarnessAdapter(
        executable=str(PI_FIXTURE),
        environment={"M3_PI_FIXTURE_ERROR": "1"},
    )
    session = await adapter.open(launch)
    result = await session.send(HarnessTurnRequest.from_message("hello"))
    assert result.status == "failed"
    assert result.response is None
    assert result.error is not None
    assert "fixture provider secret" not in result.error.message
    await session.close()


def test_codex_config_is_bounded() -> None:
    launch = _launch(Codex(model="fixture"))
    assert "[mcp_servers]" in render_codex_config(launch)
    assert "mcp_servers" in codex_configuration(launch)


@pytest.mark.parametrize("required", [True, False])
def test_codex_config_preserves_server_requirement(required: bool) -> None:
    config = HarnessServerConfig(
        "fixture",
        TransportKind.STDIO,
        required,
        True,
        "fixture-1",
        command="fixture",
    )
    launch = _launch(Codex(model="fixture"), configurations=(config,))

    assert codex_configuration(launch)["mcp_servers"]["fixture"]["required"] is required
    assert (
        tomllib.loads(render_codex_config(launch))["mcp_servers"]["fixture"]["required"]
        is required
    )


def test_codex_update_setting_is_written_at_toml_root(tmp_path: Path) -> None:
    server = HarnessServerConfig(
        "server",
        TransportKind.STDIO,
        True,
        True,
        "server-1",
        command="echo",
    )
    for name, configurations in (("empty", ()), ("server", (server,))):
        root = tmp_path / name
        root.mkdir()
        adapter = CodexHarnessAdapter()
        adapter.environment_for_launch(
            _launch(Codex(model="fixture"), configurations=configurations), root
        )
        parsed = tomllib.loads((root / "codex-home" / "config.toml").read_text())
        assert parsed["check_for_update_on_startup"] is False
        assert "check_for_update_on_startup" not in parsed["mcp_servers"]
        assert all(
            "check_for_update_on_startup" not in value
            for value in parsed["mcp_servers"].values()
        )


def test_codex_secret_references_are_not_rendered_as_secret_values() -> None:
    config = HarnessServerConfig(
        "secret",
        TransportKind.STDIO,
        True,
        True,
        "secret-1",
        command="server",
        environment={"TOKEN": SecretReference(source="environment", name="TOKEN")},
    )
    rendered = render_codex_config(
        _launch(Codex(model="fixture"), configurations=(config,))
    )
    assert "TOKEN" in rendered
    assert "secret-value" not in rendered


def test_codex_config_preserves_quoted_server_alias() -> None:
    config = HarnessServerConfig(
        'a"b\\c\n', TransportKind.STDIO, True, True, "alias-1", command="server"
    )
    rendered = render_codex_config(
        _launch(Codex(model="fixture"), configurations=(config,))
    )
    parsed = tomllib.loads(rendered)
    assert parsed["mcp_servers"]['a"b\\c\n']["command"] == "server"


@pytest.mark.asyncio
async def test_native_cancel_sends_pi_abort_before_cleanup() -> None:
    class FakeProcess:
        owner = None

        def __init__(self) -> None:
            self.frames: list[dict[str, object]] = []
            self.closed = False

        async def write(self, frame: dict[str, object]) -> None:
            self.frames.append(frame)

        async def close(self) -> None:
            self.closed = True

    process = FakeProcess()
    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    await adapter._cancel(process)  # type: ignore[arg-type]
    assert process.frames == [{"type": "abort"}]
    assert process.closed is False


@pytest.mark.asyncio
async def test_pi_cancel_marks_inflight_turn_cancelled_and_keeps_process() -> None:
    launch = _launch(Pi(model="fixture", executable=str(PI_FIXTURE)))
    adapter = PiHarnessAdapter(
        executable=str(PI_FIXTURE), environment={"M3_PI_FIXTURE_BLOCK": "1"}
    )
    session = await adapter.open(launch)
    sending = asyncio.create_task(
        session.send(HarnessTurnRequest.from_message("blocked"))
    )
    await asyncio.sleep(0.1)
    await session.cancel()
    result = await asyncio.wait_for(sending, timeout=3)
    assert result.status == "cancelled"
    assert adapter._process is not None
    await session.close()


@pytest.mark.asyncio
async def test_native_derived_observations_redact_runtime_secrets() -> None:
    class FakeProcess:
        owner = None

        def __init__(self) -> None:
            self.frames = [
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "secret-canary",
                    },
                    "usage": {},
                },
                {"type": "agent_settled"},
            ]

        async def write(self, frame: object) -> None:
            del frame

        async def next(self, timeout: float | None = None) -> object:
            del timeout
            return self.frames.pop(0)

        async def close(self) -> None:
            return None

    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    adapter._runtime_secrets = {"secret-canary"}
    result = await adapter._send(HarnessTurnRequest.from_message("x"), 1, FakeProcess())  # type: ignore[arg-type]
    assert result.response is not None
    assert "secret-canary" not in result.response.text
    assert all(
        "secret-canary" not in repr(item) for item in result.turn_evidence.observations
    )  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_pi_streaming_tool_events_do_not_duplicate_execution_observations() -> (
    None
):
    server, tool = "server / one", "tool.name/with spaces"
    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    launch = _launch(
        Pi(model="fixture", executable=str(PI_FIXTURE)),
        configurations=(
            HarnessServerConfig(
                server,
                TransportKind.STDIO,
                True,
                True,
                "c",
                command="fixture",
                tools=(tool,),
            ),
        ),
    )
    await adapter.preflight(launch)
    qualified = qualified_tool_name(server, tool)
    from datetime import datetime, timezone

    observations: list[object] = []
    reported_calls: list[dict[str, object]] = []
    for frame in (
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "toolcall_start", "name": qualified},
        },
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "toolcall_end", "name": qualified},
        },
        {
            "type": "tool_execution_start",
            "toolCallId": "call-1",
            "toolName": qualified,
            "args": {},
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "call-1",
            "toolName": qualified,
            "result": {"ok": True},
            "isError": False,
        },
    ):
        _, _, calls = adapter.consume_frame(  # type: ignore[arg-type]
            frame, 1, datetime.now(timezone.utc), 0.0, observations
        )
        reported_calls.extend(calls)
    assert reported_calls == [
        {
            "call_id": "call-1",
            "server": server,
            "tool": tool,
            "qualified_name": qualified,
            "arguments": {},
        }
    ]
    assert (
        sum(getattr(item, "kind", "") == "tool_call_observed" for item in observations)
        == 1
    )
    assert (
        sum(
            getattr(item, "kind", "") == "tool_result_observed" for item in observations
        )
        == 1
    )


def test_provider_protocol_errors_are_not_successes() -> None:
    from datetime import datetime, timezone

    from m3.harness._rpc_native import JsonRpcProcess

    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    with pytest.raises(HarnessStartupError):
        adapter.consume_frame(
            {"error": {"code": -1}}, 1, datetime.now(timezone.utc), 0.0, []
        )
    assert JsonRpcProcess is not None


@pytest.mark.asyncio
async def test_codex_terminal_failure_is_not_projected_as_completed() -> None:
    from datetime import datetime, timezone

    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    observations: list[object] = []
    terminal, _, _ = adapter.consume_frame(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "t", "status": "failed"}},
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,
    )  # type: ignore[arg-type]
    assert terminal is True
    assert adapter._terminal_status == "failed"


@pytest.mark.asyncio
async def test_codex_completed_mcp_item_without_started_event_reports_a_call() -> None:
    from datetime import datetime, timezone

    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    observations: list[object] = []
    terminal, _, calls = adapter.consume_frame(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {
                    "type": "mcpToolCall",
                    "id": "call-1",
                    "server": "deepwiki",
                    "tool": "read_wiki_structure",
                    "arguments": {"repoName": "modelcontextprotocol/python-sdk"},
                    "status": "completed",
                    "result": {"content": [{"type": "text", "text": "ok"}]},
                },
            },
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,
    )  # type: ignore[arg-type]
    assert terminal is False
    assert calls == [
        {
            "call_id": "call-1",
            "server": "deepwiki",
            "tool": "read_wiki_structure",
            "arguments": {"repoName": "modelcontextprotocol/python-sdk"},
        }
    ]
    assert (
        sum(getattr(item, "kind", "") == "tool_call_observed" for item in observations)
        == 1
    )
    assert (
        sum(
            getattr(item, "kind", "") == "tool_result_observed" for item in observations
        )
        == 1
    )


def test_codex_secret_source_is_mapped_to_target_environment_name(
    tmp_path: Path,
) -> None:
    config = HarnessServerConfig(
        "secret",
        TransportKind.STDIO,
        True,
        True,
        "secret-1",
        command="server",
        environment={
            "TARGET_TOKEN": SecretReference(source="environment", name="SOURCE_TOKEN")
        },
    )
    launch = _launch(Codex(model="fixture"), configurations=(config,))
    adapter = CodexHarnessAdapter(
        executable=str(CODEX_FIXTURE), environment={"SOURCE_TOKEN": "secret-value"}
    )
    environment = adapter.environment_for_launch(launch, tmp_path)
    assert environment["TARGET_TOKEN"] == "secret-value"
    assert (
        "SOURCE_TOKEN" not in environment
        or environment["SOURCE_TOKEN"] == "secret-value"
    )


def test_pi_bridge_catalog_names_are_stable_and_calls_are_routed() -> None:
    async def call(arguments: object) -> object:
        return {"echo": arguments}

    bridge = MCPBridge(
        {"server one": {"tool/name": {"description": "demo", "call": call}}}
    )
    descriptor = bridge.list_tools()[0]
    assert descriptor["name"] == qualified_tool_name("server one", "tool/name")
    assert asyncio.run(bridge.call_tool("server one", "tool/name", {"x": 1})) == {
        "echo": {"x": 1}
    }


def test_pi_bridge_qualified_names_are_safe_bounded_and_metadata_cannot_override() -> (
    None
):
    server = "服务/" + "s" * 300
    tool = "工具." + "t" * 300
    bridge = MCPBridge(
        {
            server: {
                tool: {
                    "name": "original",
                    "label": "bad",
                    "server": "wrong",
                    "tool": "wrong",
                    "description": "kept",
                    "inputSchema": {"type": "object"},
                }
            },
            "other": {tool: {}},
        }
    )
    catalog = bridge.list_tools()
    assert len({item["name"] for item in catalog}) == 2
    for item in catalog:
        assert len(item["name"]) <= 64
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", item["name"])
        assert item["name"] == qualified_tool_name(item["server"], item["tool"])
        assert item["tool"] in item["label"]
        assert item["server"] in {server, "other"}
        assert item["tool"] == tool
    first = next(item for item in catalog if item["server"] == server)
    assert first["description"] == "kept"
    assert first["inputSchema"] == {"type": "object"}


def test_pi_extension_has_no_request_local_timeout() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "m3"
        / "harness"
        / "pi_extension"
        / "extension.ts"
    ).read_text()
    assert "30000" not in source
    assert "bridge timeout" not in source
    assert "setTimeout" not in source


def test_pi_extension_finalizes_only_at_settled_action_boundary() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "m3"
        / "harness"
        / "pi_extension"
        / "extension.ts"
    ).read_text()
    assert 'pi.on?.("agent_settled", finalizeAction)' in source
    assert 'pi.on?.("agent_end", finalizeAction)' not in source


@pytest.mark.skipif(shutil.which("pi") is None, reason="Pi is not installed")
def test_bundled_pi_extension_loads_without_starting_a_model_turn() -> None:
    bridge = (
        Path(__file__).parents[2]
        / "src"
        / "m3"
        / "harness"
        / "pi_extension"
        / "bridge.py"
    )
    extension = bridge.with_name("extension.ts")
    environment = dict(os.environ)
    sdk_root = ROOT.parent
    server = sdk_root / "examples" / "servers" / "example_mcp_server.py"
    environment["M3_PI_BRIDGE_COMMAND"] = "uv"
    environment["M3_PI_BRIDGE_ARGV"] = json.dumps(
        ["run", "--project", str(sdk_root), "python", str(bridge)]
    )
    environment["M3_MCP_CONFIG"] = json.dumps(
        {
            "example": {
                "transport": "stdio",
                "command": "uv",
                "args": ["run", "--project", str(sdk_root), "python", str(server)],
                "cwd": str(sdk_root.parent),
                "env": {},
            }
        }
    )
    result = subprocess.run(
        [
            "pi",
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(extension),
        ],
        input='{"type":"get_state"}\n',
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    messages = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    assert any(
        message.get("command") == "get_state" and message.get("success") is True
        for message in messages
    )


def test_public_types_and_registry_are_native_peers() -> None:
    from m3.harness import default_adapters
    from m3.types import HarnessSpec

    assert HarnessSpec.__metadata__
    registry = default_adapters()
    assert set(("codex", "pi")).issubset(registry._factories)
    assert Codex(model="x").kind == "codex"
    assert Pi(model="x").kind == "pi"


def test_harness_matrix_accepts_codex_and_pi_cases() -> None:
    server = ServerCase(
        name="fixture",
        server=StdioServer(name="fixture", command="fixture"),
        tools=(ToolCase(name="echo"),),
    )
    matrix = HarnessMatrix(
        mode="each_server",
        servers=(server,),
        harnesses=(
            HarnessCase(name="codex", harness=Codex(model="fixture")),
            HarnessCase(name="pi", harness=Pi(model="fixture")),
        ),
        trials=1,
    )
    assert [case.harness.name for case in matrix.cases()] == ["codex", "pi"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness", "adapter"),
    [
        (
            Codex(model="fixture", executable=str(CODEX_FIXTURE)),
            CodexHarnessAdapter(executable=str(CODEX_FIXTURE)),
        ),
        (
            Pi(model="fixture", executable=str(PI_FIXTURE)),
            PiHarnessAdapter(executable=str(PI_FIXTURE)),
        ),
    ],
)
async def test_agent_session_preflight_accepts_portable_policy_with_capture_proof(
    harness: object, adapter: object
) -> None:
    server = StdioServer(name="special server", command="fixture")
    policy = RestrictiveToolPolicy(allowed_tools=("special server:tool/name",))
    spec = AgentSpec(
        harness=harness,  # type: ignore[arg-type]
        servers=(ServerBinding(server=server, alias="special server"),),
        tool_policy=policy,
    )
    record = ServerRecord(
        "special server",
        server,
        True,
        True,
        "connection-1",
        TransportKind.STDIO,
        tools=("tool/name",),
    )
    configuration = HarnessServerConfig(
        "special server",
        TransportKind.STDIO,
        True,
        True,
        "connection-1",
        command="fixture",
        tools=("tool/name",),
    )

    class CaptureProof:
        def enforces_portable_policy(self, connection_ids: object) -> bool:
            return tuple(connection_ids) == ("connection-1",)

    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot((record,)),
        (configuration,),
        policy,
        capture=CaptureProof(),
    )
    session = AsyncAgentSession(spec, adapter)  # type: ignore[arg-type]
    effective = await session._preflight_launch(launch)
    assert effective.tool_policy_evidence is not None
    assert effective.tool_policy_evidence.enforced == "portable"
    await session.aclose()


@pytest.mark.asyncio
async def test_pi_tool_observation_recovers_special_character_identity() -> None:
    server = "server / one"
    tool = "tool.name/with spaces"
    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    launch = _launch(
        Pi(model="fixture", executable=str(PI_FIXTURE)),
        configurations=(
            HarnessServerConfig(
                "server / one",
                TransportKind.STDIO,
                True,
                True,
                "c",
                command="fixture",
                tools=(tool,),
            ),
        ),
    )
    await adapter.preflight(launch)
    from datetime import datetime, timezone

    observations: list[object] = []
    qualified = qualified_tool_name(server, tool)
    terminal, _, _ = adapter.consume_frame(
        {
            "type": "tool_execution_start",
            "toolCallId": "call-1",
            "toolName": qualified,
            "args": {"x": 1},
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,  # type: ignore[arg-type]
    )
    assert terminal is False
    call = next(
        item
        for item in observations
        if getattr(item, "kind", "") == "tool_call_observed"
    )
    assert call.server == server
    assert call.tool == tool


@pytest.mark.asyncio
async def test_pi_next_frame_preserves_native_frame_when_control_is_ready_together() -> (
    None
):
    class Process:
        async def next(self, _timeout: float | None) -> dict[str, str]:
            await asyncio.sleep(0)
            return {"type": "agent_settled", "value": "native"}

    class Control:
        def __init__(self) -> None:
            self.frames = [{"type": "pending", "round_id": "round-1"}]

        async def receive(self, _timeout: float | None) -> dict[str, str]:
            await asyncio.sleep(0)
            return self.frames.pop(0)

    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    adapter._managed_input_runtime = object()
    adapter._control_channel = Control()  # type: ignore[assignment]
    observed: list[object] = []

    async def consume(_frame: object) -> None:
        observed.append(_frame)

    adapter._deliver_control_pending = consume  # type: ignore[method-assign]
    frame = await adapter.next_frame(Process(), 1.0)
    assert frame == {"type": "agent_settled", "value": "native"}
    assert adapter._buffered_native_frame is None
    assert observed == [{"type": "pending", "round_id": "round-1"}]


@pytest.mark.asyncio
async def test_pi_next_frame_handles_more_than_recursion_limit_control_frames() -> None:
    frame_count = 1_100
    delivered = asyncio.Event()
    process_tasks: list[asyncio.Task[object]] = []
    control_tasks: list[asyncio.Task[object]] = []

    class Process:
        async def next(self, _timeout: float | None) -> dict[str, str]:
            task = asyncio.current_task()
            assert task is not None
            process_tasks.append(task)
            await delivered.wait()
            return {"type": "agent_settled", "value": "native"}

    class Control:
        def __init__(self) -> None:
            self.frames = [
                {"type": "pending", "round_id": f"round-{index}"}
                for index in range(frame_count)
            ]

        async def receive(self, _timeout: float | None) -> dict[str, str]:
            task = asyncio.current_task()
            assert task is not None
            control_tasks.append(task)
            if self.frames:
                await asyncio.sleep(0)
                return self.frames.pop(0)
            await asyncio.Future()
            raise AssertionError("unreachable")

    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    adapter._managed_input_runtime = object()
    adapter._control_channel = Control()  # type: ignore[assignment]
    observed: list[object] = []

    async def consume(frame: object) -> None:
        observed.append(frame)
        if len(observed) == frame_count:
            delivered.set()

    adapter._deliver_control_pending = consume  # type: ignore[method-assign]
    frame = await adapter.next_frame(Process(), 1.0)

    assert frame == {"type": "agent_settled", "value": "native"}
    assert len(observed) == frame_count
    assert adapter._buffered_native_frame is None
    assert not adapter._buffered_native_ready
    assert adapter._buffered_control_frame is None
    assert len(process_tasks) == frame_count + 1
    assert len(control_tasks) == frame_count + 1
    assert all(task.done() for task in (*process_tasks, *control_tasks))


@pytest.mark.asyncio
async def test_pi_next_frame_discards_native_buffer_when_control_delivery_fails() -> (
    None
):
    class Process:
        async def next(self, _timeout: float | None) -> dict[str, str]:
            await asyncio.sleep(0)
            return {"type": "agent_settled", "value": "stale"}

    class Control:
        async def receive(self, _timeout: float | None) -> dict[str, str]:
            await asyncio.sleep(0)
            return {"type": "pending", "round_id": "round-1"}

    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    adapter._managed_input_runtime = object()
    adapter._control_channel = Control()  # type: ignore[assignment]

    async def fail(_frame: object) -> None:
        raise RuntimeError("delivery failed")

    adapter._deliver_control_pending = fail  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="delivery failed"):
        await adapter.next_frame(Process(), 1.0)
    assert adapter._buffered_native_frame is None
    assert not adapter._buffered_native_ready
    assert adapter._buffered_control_frame is None


@pytest.mark.asyncio
async def test_pi_tool_observation_recovers_duplicate_tool_names_per_server() -> None:
    tool = "工具/" + "x" * 180
    servers = ("alpha/服务", "beta/服务")
    adapter = PiHarnessAdapter(executable=str(PI_FIXTURE))
    launch = _launch(
        Pi(model="fixture", executable=str(PI_FIXTURE)),
        configurations=tuple(
            HarnessServerConfig(
                server,
                TransportKind.STDIO,
                True,
                True,
                "c",
                command="fixture",
                tools=(tool,),
            )
            for server in servers
        ),
    )
    await adapter.preflight(launch)
    observations: list[object] = []
    for index, server in enumerate(servers):
        terminal, _, _ = adapter.consume_frame(
            {
                "type": "tool_execution_start",
                "toolCallId": f"call-{index}",
                "toolName": qualified_tool_name(server, tool),
                "args": {},
            },
            index + 1,
            datetime.now(timezone.utc),
            0.0,
            observations,  # type: ignore[arg-type]
        )
        assert terminal is False
    calls = [
        item
        for item in observations
        if getattr(item, "kind", "") == "tool_call_observed"
    ]
    assert {(item.server, item.tool) for item in calls} == {
        (server, tool) for server in servers
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness", "adapter", "kind"),
    [
        (
            Codex(model="fixture", executable=str(CODEX_FIXTURE)),
            CodexHarnessAdapter(executable=str(CODEX_FIXTURE)),
            "codex",
        ),
        (
            Pi(model="fixture", executable=str(PI_FIXTURE)),
            PiHarnessAdapter(executable=str(PI_FIXTURE)),
            "pi",
        ),
    ],
)
async def test_native_runtime_trace_metadata_and_usage_round_trip(
    harness: object, adapter: object, kind: str
) -> None:
    spec = AgentSpec(
        harness=harness,
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=RestrictiveToolPolicy(),
    )  # type: ignore[arg-type]
    session = AsyncAgentSession(spec, adapter)  # type: ignore[arg-type]
    await session.__aenter__()
    turn = await session.send("trace this")
    assert turn.response is not None
    await session.aclose()
    trace = session.result.trace
    assert trace is not None
    runtime = trace.view().runtime
    assert runtime.kind == kind
    assert runtime.usage.state.value == "observed"
    assert runtime.model_id.state.value == "observed"
    if kind == "codex":
        assert runtime.thread_id.state.value == "observed"
        assert runtime.sandbox.state.value == "observed"
        assert runtime.usage.value.total_tokens.value == 3  # type: ignore[union-attr]
    else:
        assert runtime.session_id.state.value == "observed"
        assert runtime.provider_id.value == "fixture-provider"  # type: ignore[union-attr]
        assert runtime.model_id.value == "fixture-model"  # type: ignore[union-attr]
        assert runtime.finish_reason.value == "stop"  # type: ignore[union-attr]
        assert runtime.usage.value.total_tokens.value == 3  # type: ignore[union-attr]
    assert runtime.model_dump(mode="json") == type(runtime).model_validate(
        runtime.model_dump(mode="json")
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_codex_completed_item_does_not_duplicate_streamed_text() -> None:
    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    observations: list[object] = []
    adapter.consume_frame(
        {
            "method": "item/agentMessage/delta",
            "params": {"itemId": "m1", "delta": "hello"},
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,
    )  # type: ignore[arg-type]
    _, text, _ = adapter.consume_frame(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "id": "m1", "text": "hello"}},
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,
    )  # type: ignore[arg-type]
    assert text == ""


@pytest.mark.asyncio
async def test_codex_completion_only_agent_message_returns_text() -> None:
    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    observations: list[object] = []
    _, text, _ = adapter.consume_frame(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "id": "m1", "text": "hello"}},
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,
    )  # type: ignore[arg-type]
    assert text == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_cls", [CodexHarnessAdapter, PiHarnessAdapter])
async def test_native_timeout_closes_process_after_async_cancel(
    adapter_cls: object,
) -> None:
    class FakeProcess:
        owner = None

        def __init__(self, stale_frames: list[object]) -> None:
            self.closed = False
            self.frames: list[object] = []
            self.stale_frames = stale_frames
            self.next_calls = 0

        async def write(self, frame: object) -> None:
            if self.closed:
                raise RuntimeError("closed")
            self.frames.append(frame)

        async def next(self, timeout: float | None = None) -> object:
            if self.closed:
                raise RuntimeError("stale frames unavailable")
            self.next_calls += 1
            raise asyncio.TimeoutError

        async def close(self) -> None:
            self.closed = True

    adapter = adapter_cls(executable="fixture")  # type: ignore[operator]
    adapter.send_turn = lambda *args: asyncio.sleep(0)  # type: ignore[method-assign]
    stale_frames = (
        [{"method": "turn/interrupt/ack"}, {"method": "turn/completed"}]
        if adapter_cls is CodexHarnessAdapter
        else [{"type": "abort_ack"}, {"type": "agent_settled"}]
    )
    process = FakeProcess(stale_frames)
    result = await adapter._send(  # type: ignore[attr-defined]
        HarnessTurnRequest.from_message("blocked"), 1, process
    )
    assert result.status == "timed_out"
    assert process.closed is True
    followup = await adapter._send(  # type: ignore[attr-defined]
        HarnessTurnRequest.from_message("next"), 2, process
    )
    assert followup.status != "completed"
    assert process.stale_frames == stale_frames
    assert process.next_calls == 1
    if adapter_cls is PiHarnessAdapter:
        assert process.frames == [{"type": "abort"}]


@pytest.mark.asyncio
async def test_native_timeout_survives_cleanup_failure() -> None:
    class FailingCloseProcess:
        owner = None

        async def write(self, frame: object) -> None:
            del frame

        async def next(self, timeout: float | None = None) -> object:
            del timeout
            raise asyncio.TimeoutError

        async def close(self) -> None:
            raise RuntimeError("cleanup failed")

    adapter = CodexHarnessAdapter(executable="fixture")
    adapter.send_turn = lambda *args: asyncio.sleep(0)  # type: ignore[method-assign]
    result = await adapter._send(  # type: ignore[attr-defined]
        HarnessTurnRequest.from_message("blocked"), 1, FailingCloseProcess()
    )
    assert result.status == "timed_out"
    assert result.turn_evidence is not None
    assert "cleanup_failed" in result.turn_evidence.limitations


@pytest.mark.asyncio
async def test_codex_streamed_and_completed_message_returns_one_text() -> None:
    class FakeProcess:
        owner = None

        def __init__(self) -> None:
            self.frames = iter(
                [
                    {"id": 1, "result": {"turn": {"id": "turn-1"}}},
                    {
                        "method": "item/agentMessage/delta",
                        "params": {"itemId": "m1", "delta": "hello"},
                    },
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "agentMessage",
                                "id": "m1",
                                "text": "hello",
                            }
                        },
                    },
                    {"method": "turn/completed", "params": {"turn": {}}},
                ]
            )

        async def write(self, frame: object) -> None:
            del frame

        async def next(self, timeout: float | None = None) -> object:
            del timeout
            return next(self.frames)

        async def close(self) -> None:
            return None

    adapter = CodexHarnessAdapter(executable="fixture")
    adapter.send_turn = lambda *args: asyncio.sleep(0)  # type: ignore[method-assign]
    result = await adapter._send(  # type: ignore[attr-defined]
        HarnessTurnRequest.from_message("hello"), 1, FakeProcess()
    )
    assert result.status == "completed"
    assert result.response is not None
    assert result.response.content[0].text == "hello"


@pytest.mark.asyncio
async def test_closed_json_rpc_process_does_not_consume_stale_frames() -> None:
    from m3.harness._rpc_native import JsonRpcProcess

    process = JsonRpcProcess("fixture")
    await process._frames.put({"method": "turn/completed"})
    await process.close()
    assert await process.next() is None
    with pytest.raises(Exception, match="closed"):
        await process.write({"method": "turn/start"})
