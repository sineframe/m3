"""Contract-level tests for the continuing real ACP adapter."""

from __future__ import annotations

import asyncio
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from acp.schema import PermissionOption

from mcp_pal.harness.acp import AcpHarnessAdapter, _Client
from mcp_pal.harness.contracts import HarnessLaunch, HarnessStartupError, HarnessTurnRequest
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.interaction_handlers import (
    ElicitationResult,
    FilesystemRequest,
    FilesystemResult,
    Interactions,
    InteractionHandlers,
    InteractionReceipt,
    SamplingResult,
    TerminalRequest,
    TerminalResult,
)
from mcp_pal.server_group import HarnessServerConfig, ServerGroupSnapshot, ServerRecord
from mcp_pal.types import (
    ACPAgent,
    AgentSpec,
    ElicitationPolicy,
    FilesystemPolicy,
    PermissionPolicy,
    NativeToolPolicy,
    SamplingPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    HTTPServer,
    TerminalPolicy,
    TransportKind,
)
from mcp_pal.observability import ACPTrace, ObservationReason, ObservationState


pytestmark = pytest.mark.process_lifecycle


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


def _configured_agent(path: Path) -> str:
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    ident = request.get('id')
    if method == 'initialize':
        send({'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':1}})
    elif method == 'session/new':
        send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'configured-session','modes':{'currentModeId':'default','availableModes':[{'id':'fast','name':'Fast'}]},'configOptions':[{'type':'select','id':'quality','name':'Quality','currentValue':'normal','options':[{'value':'high','name':'High'}]}]}})
    elif method == 'session/set_mode':
        send({'jsonrpc':'2.0','id':ident,'result':{'modeId':'fast'}})
    elif method == 'session/set_config_option':
        send({'jsonrpc':'2.0','id':ident,'result':{'configOptions':[]}})
    elif method == 'session/prompt':
        send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'configured-session','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'configured'}}}})
        send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _tool_agent(path: Path) -> str:
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
for line in sys.stdin:
    request = json.loads(line); method = request.get('method'); ident = request.get('id')
    if method == 'initialize': send({'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':1}})
    elif method == 'session/new': send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'tool-session'}})
    elif method == 'session/prompt':
        send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'tool-session','update':{'sessionUpdate':'tool_call','toolCallId':'call-1','title':'Read_File'}}})
        send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'tool-session','update':{'sessionUpdate':'tool_call_update','toolCallId':'call-1','rawInput':{'path':'x'},'rawOutput':{'ok':True},'status':'completed'}}})
        send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _rich_agent(path: Path) -> str:
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
for line in sys.stdin:
    request = json.loads(line); method = request.get('method'); ident = request.get('id')
    if method == 'initialize': send({'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':1}})
    elif method == 'session/new': send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'rich-session'}})
    elif method == 'session/prompt':
        for update in (
            {'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'answer'}},
            {'sessionUpdate':'agent_thought_chunk','content':{'type':'text','text':'thinking'}},
            {'sessionUpdate':'plan','entries':[{'content':'step','status':'pending'}],'status':'updated'},
            {'sessionUpdate':'current_mode_update','currentModeId':'working'},
        ):
            send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'rich-session','update':update}})
        send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _launch(command: str) -> HarnessLaunch:
    spec = AgentSpec(
        harness=ACPAgent(model="fixture", manifest={"command": command, "protocol": "acp", "protocol_version": 1}),
        servers=(ServerBinding(server=StdioServer(name="unused", command="unused")),),
    )
    configuration = HarnessServerConfig(
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
async def test_acp_typed_observations_and_public_runtime_are_finalized(tmp_path: Path) -> None:
    """ACP frames and session metadata survive the public finalized view."""
    command = _agent(tmp_path / "typed-agent.py")
    spec = AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={"command": sys.executable, "args": [command], "protocol": "acp", "protocol_version": 1},
        ),
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
    )
    async with AsyncMCPTestKit(env={}, cwd=tmp_path) as kit:
        session = kit.agent_session(spec)
        async with session:
            result = await session.send("typed")
            assert result.response is not None
            assert result.response.text == "turn-1"
            active = session.adapter._active
            assert active is not None
            evidence = result.evidence
            assert evidence is not None
        view = session.result.trace_view
    assert isinstance(view.runtime, ACPTrace)
    assert view.runtime.protocol_version.state is ObservationState.OBSERVED
    assert view.runtime.session_id.state is ObservationState.OBSERVED
    assert view.runtime.usage.state is ObservationState.UNSUPPORTED
    assert view.processes
    assert view.processes[0].exit_code.state is ObservationState.NOT_EMITTED
    assert any(entry.kind == "raw_message" for entry in view.timeline)
    assert view.messages
    assert view.messages[0].message_id.state is ObservationState.NOT_EMITTED
    assert view.messages[0].message_id.reason is ObservationReason.PROVIDER_DID_NOT_EMIT
    raw_methods = set()
    raw_timing: list[tuple[str, float]] = []
    for entry in view.timeline:
        if entry.kind != "provider" or getattr(entry, "category", "") != "raw_frame":
            continue
        if entry.data.state is ObservationState.OBSERVED and isinstance(entry.data.value, Mapping):
            method = entry.data.value.get("method")
            if isinstance(method, str):
                raw_methods.add(method)
                raw_timing.append((method, entry.timing.start_offset_ms))
    assert {"initialize", "session/new", "session/prompt", "session/update"} <= raw_methods
    assert raw_timing
    assert min(offset for method, offset in raw_timing if method == "initialize") <= min(
        offset for method, offset in raw_timing if method == "session/prompt"
    )


@pytest.mark.asyncio
async def test_acp_tool_observation_preserves_builtin_identity_and_result(tmp_path: Path) -> None:
    command = _tool_agent(tmp_path / "tool-agent.py")
    session = await AcpHarnessAdapter().open(_launch(command))
    result = await session.send(HarnessTurnRequest.from_message("tool"))
    assert result.turn_evidence is not None
    calls = [item for item in result.turn_evidence.observations if item.kind == "tool_call_observed"]
    results = [item for item in result.turn_evidence.observations if item.kind == "tool_result_observed"]
    assert len(calls) == len(results) == 1
    assert calls[0].server is None and calls[0].tool == "Read_File"
    assert calls[0].arguments == {"path": "x"}
    assert results[0].result == {"ok": True} and results[0].status == "success"
    await session.close()


@pytest.mark.asyncio
async def test_acp_applies_typed_mode_and_config_before_first_prompt(tmp_path: Path) -> None:
    command = _configured_agent(tmp_path / "configured-agent.py")
    launch = _launch(command)
    spec = launch.spec.model_copy(update={
        "harness": ACPAgent(
            model="fixture",
            manifest={"command": command, "protocol": "acp", "protocol_version": 1},
            agent_mode_id="fast",
            session_config={"quality": "high"},
        )
    })
    launch = HarnessLaunch(spec, launch.servers, launch.configurations, spec.tool_policy, capture=launch.capture)
    adapter = AcpHarnessAdapter()
    session = await adapter.open(launch)
    result = await session.send(HarnessTurnRequest.from_message("first"))
    assert result.status == "completed"
    frames = cast(Any, session)._frames
    methods = [frame["payload"].get("method") for frame in frames if isinstance(frame.get("payload"), dict)]
    assert methods.index("session/set_mode") < methods.index("session/set_config_option") < methods.index("session/prompt")
    await session.close()


@pytest.mark.asyncio
async def test_acp_public_runtime_projects_modes_and_selected_config(tmp_path: Path) -> None:
    command = _configured_agent(tmp_path / "runtime-agent.py")
    base = _launch(command)
    spec = base.spec.model_copy(update={
        "harness": ACPAgent(
            model="fixture",
            manifest={"command": sys.executable, "args": [command], "protocol": "acp", "protocol_version": 1},
            agent_mode_id="fast",
            session_config={"quality": "high"},
        )
    })
    async with AsyncMCPTestKit(env={}, cwd=tmp_path) as kit:
        session = kit.agent_session(spec)
        async with session:
            result = await session.send("runtime")
            assert result.response is not None and result.response.text == "configured"
        view = session.result.trace_view
    assert isinstance(view.runtime, ACPTrace)
    assert view.runtime.available_modes.state is ObservationState.OBSERVED
    assert view.runtime.available_modes.value is not None
    assert [
        {key: dict(item).get(key) for key in ("id", "name")}
        for item in view.runtime.available_modes.value
    ] == [{"id": "fast", "name": "Fast"}]
    assert view.runtime.current_mode.state is ObservationState.OBSERVED
    assert view.runtime.current_mode.value == "fast"
    assert view.runtime.selected_config.state is ObservationState.OBSERVED
    assert view.runtime.selected_config.value == {"quality": "high"}
    assert view.runtime.plan_state_available.state is ObservationState.NOT_EMITTED


@pytest.mark.asyncio
async def test_acp_public_source_shape_preserves_message_reasoning_plan_and_state(tmp_path: Path) -> None:
    command = _rich_agent(tmp_path / "rich-agent.py")
    spec = AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={"command": sys.executable, "args": [command], "protocol": "acp", "protocol_version": 1},
        ),
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
    )
    async with AsyncMCPTestKit(env={}, cwd=tmp_path) as kit:
        session = kit.agent_session(spec)
        async with session:
            result = await session.send("rich")
            assert result.response is not None
        view = session.result.trace_view
    assert view.messages
    assert view.messages[0].content
    assert view.reasoning
    assert view.reasoning[0].content.state is ObservationState.OBSERVED
    providers = [entry for entry in view.timeline if entry.kind == "provider"]
    assert any(getattr(entry, "category", None) == "plan" for entry in providers)
    assert any(getattr(entry, "category", None) == "state" for entry in providers)
    assert view.runtime.plan_state_available.state is ObservationState.OBSERVED


@pytest.mark.asyncio
async def test_acp_rejects_stale_typed_session_option(tmp_path: Path) -> None:
    command = _agent(tmp_path / "stale-agent.py")
    base = _launch(command)
    spec = base.spec.model_copy(update={
        "harness": ACPAgent(
            model="fixture",
            manifest={"command": command, "protocol": "acp", "protocol_version": 1},
            session_config={"missing": "value"},
        )
    })
    launch = HarnessLaunch(spec, base.servers, base.configurations, spec.tool_policy, capture=base.capture)
    with pytest.raises(HarnessStartupError, match="ACP harness could not start"):
        await AcpHarnessAdapter().open(launch)


@pytest.mark.asyncio
async def test_acp_native_agent_default_policy_is_explicitly_nonportable(tmp_path: Path) -> None:
    del tmp_path
    server = StdioServer(name="selected", command="python")
    policy = NativeToolPolicy(harness="acp", policy={"mode": "agent_default", "server": "selected"}, nonportable_reason="ACP owns MCP tool selection")
    spec = AgentSpec(
        harness=ACPAgent(model="fixture", manifest={"command": sys.executable}),
        servers=(ServerBinding(server=server, alias="selected"),),
        tool_policy=policy,
    )
    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot((ServerRecord("selected", server, True, True, "selected", TransportKind.STDIO),)),
        (HarnessServerConfig(key="selected", transport=TransportKind.STDIO, required=True, available=True, connection_id="selected", command="python"),),
        policy,
    )
    adapter = AcpHarnessAdapter()
    readiness = await adapter.preflight(launch)
    assert readiness.ready
    assert adapter.last_policy_evidence is not None
    assert adapter.last_policy_evidence.enforced == "native"
    assert adapter.last_policy_evidence.portable is False


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
    query_spec = AgentSpec(
        harness=ACPAgent(model="fixture", manifest={"command": command}),
        servers=(ServerBinding(server=HTTPServer(name="remote", url="https://example.test/mcp?token=canary")),),
    )
    query_launch = HarnessLaunch(query_spec, ServerGroupSnapshot(), (HarnessServerConfig(
        key="remote", transport=TransportKind.STREAMABLE_HTTP, required=True, available=True,
        connection_id="c", endpoint="https://example.test/mcp?token=canary",
    ),), query_spec.tool_policy)
    # No resolved server configuration means the URL policy is evaluated only
    # when a manager supplies its immutable configuration; this is a separate
    # direct check for the unresolved credential reference boundary.
    secret_spec = AgentSpec(
        harness=ACPAgent(model="fixture", manifest={"command": command}),
        servers=(ServerBinding(server=StdioServer(name="local", command="echo", environment={"TOKEN": SecretReference(source="environment", name="TOKEN")})),),
    )
    secret_launch = HarnessLaunch(secret_spec, ServerGroupSnapshot(), (HarnessServerConfig(
        key="local", transport=TransportKind.STDIO, required=True, available=True,
        connection_id="c", command="echo", environment={"TOKEN": SecretReference(source="environment", name="TOKEN")},
    ),), secret_spec.tool_policy)
    assert (await AcpHarnessAdapter().preflight(query_launch)).reason == "credential_query_unsupported"
    assert (await AcpHarnessAdapter().preflight(secret_launch)).reason == "credential_unavailable"


@pytest.mark.asyncio
async def test_default_registry_runs_three_turn_public_agent_session(tmp_path: Path) -> None:
    command = _agent(tmp_path / "persistent-agent.py")
    spec = AgentSpec(
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

    controller = Interactions(
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
    assert {
        kind
        for kind, _request, _response in client.interaction_events
    } >= {
        "filesystem.read",
        "filesystem.write",
        "terminal.create",
        "terminal.output",
        "terminal.wait",
        "terminal.release",
    }
    with pytest.raises(RuntimeError, match="acp_auth_required"):
        await client.authenticate("unused")
