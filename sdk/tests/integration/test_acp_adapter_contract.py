"""Contract-level tests for the continuing real ACP adapter."""

from __future__ import annotations

import asyncio
import stat
import sys
from types import SimpleNamespace
from typing import Any, cast
from pathlib import Path

import pytest

from acp.schema import PermissionOption

from mcp_pal.harness.acp import AcpHarnessAdapter, _Client
from mcp_pal.harness.contracts import HarnessLaunch, HarnessStartupError, HarnessTurnRequest
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.interaction_handlers import (
    ElicitationResult,
    FilesystemRequest,
    FilesystemResult,
    InteractionController,
    InteractionHandlers,
    InteractionReceipt,
    SamplingResult,
    TerminalRequest,
    TerminalResult,
)
from mcp_pal.server_group import HarnessServerConfiguration, ServerGroupSnapshot
from mcp_pal.types import (
    ACPAgent,
    AgentExecutionSpec,
    ElicitationPolicy,
    FilesystemPolicy,
    PermissionPolicy,
    NativeToolPolicy,
    SamplingPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    StreamableHTTPServer,
    TerminalPolicy,
    TransportKind,
)


def _agent(path: Path) -> str:
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
count = 0
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    ident = request.get('id')
    if method == 'initialize':
        send({'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':1}})
    elif method == 'session/new':
        send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'persistent-session'}})
    elif method == 'session/prompt':
        count += 1
        send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'persistent-session','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'turn-' + str(count)}}}})
        send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _launch(command: str) -> HarnessLaunch:
    spec = AgentExecutionSpec(
        harness=ACPAgent(model="fixture", manifest={"command": command, "protocol": "acp", "protocol_version": 1}),
        servers=(ServerBinding(server=StdioServer(name="unused", command="unused")),),
    )
    configuration = HarnessServerConfiguration(
        key="unused",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="contract-policy-proxy",
        command="unused",
    )
    capture = SimpleNamespace(enforces_portable_policy=lambda values: tuple(values) == ("contract-policy-proxy",))
    return HarnessLaunch(
        spec,
        ServerGroupSnapshot(),
        (configuration,),
        spec.tool_policy,
        capture=capture,
    )


@pytest.mark.asyncio
async def test_acp_adapter_keeps_one_session_across_turns(tmp_path: Path) -> None:
    adapter = AcpHarnessAdapter()
    launch = _launch(_agent(tmp_path / "agent.py"))
    readiness = await adapter.preflight(launch)
    assert readiness.ready
    session = await adapter.open(launch)
    first = await session.send(HarnessTurnRequest.from_message("first"))
    second = await session.send(HarnessTurnRequest.from_message("second"))
    assert first.response is not None and first.response.text == "turn-1"
    assert second.response is not None and second.response.text == "turn-2"
    assert first.evidence["session_id"] == second.evidence["session_id"] == "persistent-session"
    assert first.evidence["usage_requested"] is False
    assert first.evidence["usage_enforced"] is False
    assert first.evidence["usage_observed"] is False
    assert first.evidence["usage_unavailable"] is True
    assert session.snapshot().turns == 2
    await session.close()
    assert session.snapshot().closed


@pytest.mark.asyncio
async def test_acp_adapter_missing_binary_is_typed_not_fallback() -> None:
    adapter = AcpHarnessAdapter()
    launch = _launch("/definitely/missing/mcp-pal-acp")
    readiness = await adapter.preflight(launch)
    assert not readiness.ready
    assert readiness.reason == "acp_executable_missing"


@pytest.mark.asyncio
async def test_acp_policy_capability_requires_proxy_proof_and_rejects_native_policy() -> None:
    base = _launch(sys.executable)
    adapter = AcpHarnessAdapter()
    unproved = HarnessLaunch(
        base.spec,
        base.servers,
        base.configurations,
        base.tool_policy,
    )
    readiness = await adapter.preflight(unproved)
    assert readiness.ready is False
    assert readiness.reason == "tool_policy_unsupported"
    assert adapter.capabilities.supports_tool_policy is False

    native_policy = NativeToolPolicy(
        harness="acp",
        policy={"allow": "all"},
        nonportable_reason="provider-owned policy",
    )
    native = HarnessLaunch(
        base.spec.model_copy(update={"tool_policy": native_policy}),
        base.servers,
        base.configurations,
        native_policy,
        capture=base.capture,
    )
    native_readiness = await adapter.preflight(native)
    assert native_readiness.ready is False
    assert native_readiness.reason == "tool_policy_unsupported"
    assert adapter.capabilities.supports_tool_policy is False


@pytest.mark.asyncio
async def test_acp_adapter_rejects_credential_query_and_unresolved_secret() -> None:
    command = sys.executable
    query_spec = AgentExecutionSpec(
        harness=ACPAgent(model="fixture", manifest={"command": command}),
        servers=(ServerBinding(server=StreamableHTTPServer(name="remote", url="https://example.test/mcp?token=canary")),),
    )
    query_launch = HarnessLaunch(query_spec, ServerGroupSnapshot(), (HarnessServerConfiguration(
        key="remote", transport=TransportKind.STREAMABLE_HTTP, required=True, available=True,
        connection_id="c", endpoint="https://example.test/mcp?token=canary",
    ),), query_spec.tool_policy)
    # No resolved server configuration means the URL policy is evaluated only
    # when a manager supplies its immutable configuration; this is a separate
    # direct check for the unresolved credential reference boundary.
    secret_spec = AgentExecutionSpec(
        harness=ACPAgent(model="fixture", manifest={"command": command}),
        servers=(ServerBinding(server=StdioServer(name="local", command="echo", environment={"TOKEN": SecretReference(source="environment", name="TOKEN")})),),
    )
    secret_launch = HarnessLaunch(secret_spec, ServerGroupSnapshot(), (HarnessServerConfiguration(
        key="local", transport=TransportKind.STDIO, required=True, available=True,
        connection_id="c", command="echo", environment={"TOKEN": SecretReference(source="environment", name="TOKEN")},
    ),), secret_spec.tool_policy)
    assert (await AcpHarnessAdapter().preflight(query_launch)).reason == "credential_query_unsupported"
    assert (await AcpHarnessAdapter().preflight(secret_launch)).reason == "credential_unavailable"


@pytest.mark.asyncio
async def test_default_registry_runs_three_turn_public_agent_session(tmp_path: Path) -> None:
    command = _agent(tmp_path / "persistent-agent.py")
    spec = AgentExecutionSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={"command": sys.executable, "args": [command], "protocol": "acp", "protocol_version": 1},
        ),
        servers=(
            ServerBinding(server=StdioServer(name="first", command="echo")),
            ServerBinding(server=StdioServer(name="second", command="echo"), required=False),
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd=tmp_path) as kit:
        session = kit.agent_session(spec)
        async with session:
            results = [await session.send(value) for value in ("one", "two", "three")]
            assert [result.response.text for result in results if result.response is not None] == [
                "turn-1", "turn-2", "turn-3"
            ]
            assert all(result.evidence["usage_requested"] is False for result in results)
            assert all(result.evidence["usage_enforced"] is False for result in results)
            assert all(result.evidence["usage_observed"] is False for result in results)
            assert all(result.evidence["usage_unavailable"] is True for result in results)
            adapter = session.adapter
            assert getattr(adapter, "name", None) == "acp"
            active = getattr(adapter, "_active", None)
            assert active is not None
            assert active.snapshot().turns == 3
            assert active.snapshot().server_configuration_count == 2
        assert session.result.snapshot.outcome is not None


@pytest.mark.asyncio
async def test_acp_transport_rejects_huge_unterminated_frame_before_allocation(tmp_path: Path) -> None:
    path = tmp_path / "no-newline.py"
    path.write_text(
        "import os\nos.write(1, b'x' * (4 * 1024 * 1024 + 1))\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    adapter = AcpHarnessAdapter()
    with pytest.raises(HarnessStartupError):
        await adapter.open(_launch(str(path)))


@pytest.mark.asyncio
async def test_acp_send_preserves_cancellation_exception(tmp_path: Path) -> None:
    adapter = AcpHarnessAdapter()
    session = await adapter.open(_launch(_agent(tmp_path / "agent.py")))

    async def stop_process(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise asyncio.CancelledError()

    session._connection.prompt = stop_process
    with pytest.raises(asyncio.CancelledError):
        await session.send(HarnessTurnRequest.from_message("prompt"))
    await adapter.close()


@pytest.mark.asyncio
async def test_acp_callbacks_use_interaction_controller_and_keep_receipts_safe() -> None:
    canary = "CALLBACK-CANARY"
    receipt = InteractionReceipt("test", "test", "allow", "fixture")

    async def filesystem(_request: FilesystemRequest) -> FilesystemResult:
        return FilesystemResult(True, canary.encode(), receipt)

    async def terminal_handler(_request: TerminalRequest) -> TerminalResult:
        return TerminalResult(True, 0, canary.encode(), b"", False, False, receipt)

    controller = InteractionController(
        permission_policy=PermissionPolicy(mode="prompt"),
        elicitation_policy=ElicitationPolicy(mode="allow"),
        sampling_policy=SamplingPolicy(mode="allow"),
        filesystem_policy=FilesystemPolicy(mode="read_write"),
        terminal_policy=TerminalPolicy(mode="allow"),
        handlers=InteractionHandlers(
            permission=lambda _request: True,
            elicitation=lambda _request: ElicitationResult(True, {"value": canary}, receipt),
            sampling=lambda _request: SamplingResult(True, canary, receipt),
            filesystem=cast(Any, filesystem),
            terminal=cast(Any, terminal_handler),
        ),
    )
    client = _Client([], [], interactions=controller)

    permission = await client.request_permission(
        "session",
        SimpleNamespace(title=canary),
        [PermissionOption(option_id="once", name="Allow once", kind="allow_once")],
    )
    elicitation = await client.create_elicitation(canary, "form")
    sampling = await client.ext_method("sampling/createMessage", {"prompt": canary})
    read = await client.read_text_file("session", "safe.txt", limit=100)
    written = await client.write_text_file("session", "safe.txt", canary)
    terminal_response = await client.create_terminal("session", "echo", [canary])
    output = await client.terminal_output("session", terminal_response.terminal_id)
    await client.wait_for_terminal_exit("session", terminal_response.terminal_id)
    await client.release_terminal("session", terminal_response.terminal_id)

    assert permission.outcome.outcome == "selected"
    assert elicitation.action == "accept"
    assert sampling["content"][0]["text"] == canary
    assert read.content == canary
    assert written is not None
    assert output.output == canary
    assert all(canary not in repr(item) for item in controller.receipts())
    with pytest.raises(RuntimeError, match="acp_auth_required"):
        await client.authenticate("unused")
