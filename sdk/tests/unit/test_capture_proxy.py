from __future__ import annotations

import json
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import pytest
from mcp import types
from mcp.server.lowlevel import Server

from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.harness import HarnessAdapterRegistry
from m3.harness.contracts import (
    DeterministicHarnessAdapter,
    HarnessLaunch,
    HarnessSession,
    HarnessTurnRequest,
)
from m3.server_group import HarnessServerConfig, ServerGroupManager
from m3.trace.capture import CaptureWriter
from m3.transport.capture_proxy import McpCaptureManager
from m3.types import (
    ClaudeCode,
    HTTPServer,
    InProcessServer,
    SecretReference,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    TrustLevel,
    UserMessage,
)


def _server() -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[types.Tool(name="draw", input_schema={"type": "object"})]
        )

    async def call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(text=params.name)])

    return Server("capture-fixture", on_list_tools=list_tools, on_call_tool=call_tool)


def test_capture_correlates_typed_ids_and_tool_latency(tmp_path: Path) -> None:
    manager = McpCaptureManager(tmp_path)
    writer = manager.writer_for("connection-a")
    writer.write(
        transport="stdio",
        direction="client_to_server",
        payload={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "draw", "arguments": {"x": 1}},
        },
    )
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={"jsonrpc": "2.0", "id": 7, "result": {"content": [{"text": "ok"}]}},
    )
    writer.write(
        transport="stdio",
        direction="client_to_server",
        payload={
            "jsonrpc": "2.0",
            "id": "7",
            "method": "tools/call",
            "params": {"name": "draw", "arguments": {}},
        },
    )
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={
            "jsonrpc": "2.0",
            "id": "7",
            "error": {"code": -1, "message": "failed"},
        },
    )
    snapshot = manager.snapshot("connection-a")
    assert [event.jsonrpc_id for event in snapshot.events] == [7, 7, "7", "7"]
    assert snapshot.events[0].request_sequence == 1
    assert snapshot.events[2].request_sequence == 2
    assert snapshot.events[1].response_to_sequence == 1
    assert snapshot.events[3].response_to_sequence == 2
    assert snapshot.events[1].tool == "draw"
    assert snapshot.events[1].latency_ms is not None
    assert snapshot.events[3].error == {"code": -1, "message": "failed"}


def test_capture_preserves_mrtr_params_for_requests_and_responses(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    writer = manager.writer_for("connection-mrtr")
    params = {
        "name": "book_shipment",
        "arguments": {"weight_kg": 2},
        "requestState": "opaque-state",
        "inputResponses": {
            "shipping_address": {
                "action": "accept",
                "content": {"city": "Pune"},
            }
        },
    }
    writer.write(
        transport="stdio",
        direction="client_to_server",
        payload={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params},
    )
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={"jsonrpc": "2.0", "id": 7, "result": {"content": []}},
    )

    request, response = manager.snapshot("connection-mrtr").events
    assert request.params == response.params == params


@pytest.mark.asyncio
async def test_stdio_configuration_is_rewritten_to_transparent_capture_proxy(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection-stdio",
        command="echo-server",
        args=("--fixture",),
    )
    instrumented = (await manager.instrument((config,)))[0]
    assert instrumented.command == sys.executable
    assert "m3.transport.stdio_proxy" in instrumented.args
    assert "--" in instrumented.args
    marker = instrumented.args.index("--")
    assert instrumented.args[marker + 1 :] == ("echo-server", "--fixture")
    await manager.close()


@pytest.mark.asyncio
async def test_http_instrumentation_keeps_credentials_only_in_proxy(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"remote"})
    config = HarnessServerConfig(
        key="remote",
        transport=TransportKind.STREAMABLE_HTTP,
        required=True,
        available=True,
        connection_id="remote",
        endpoint="http://127.0.0.1:1/mcp",
        headers={"Authorization": "Bearer canary"},
    )
    instrumented = (await manager.instrument((config,)))[0]
    assert instrumented.headers == {}
    target = manager._targets[config.connection_id]
    assert target.proxy is not None
    assert target.proxy.configured_headers["Authorization"] == "Bearer canary"
    await manager.close()


@pytest.mark.asyncio
async def test_stdio_capture_resolves_secret_reference_in_one_shot_0600_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M3_CAPTURE_SECRET", "capture-secret-value")
    manager = McpCaptureManager(tmp_path)
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection-secret",
        command="echo-server",
        environment={
            "TOKEN": SecretReference(source="environment", name="M3_CAPTURE_SECRET")
        },
    )
    instrumented = (await manager.instrument((config,)))[0]
    env_file = Path(instrumented.args[instrumented.args.index("--env-file") + 1])
    assert env_file.stat().st_mode & 0o777 == 0o600
    handoff = json.loads(env_file.read_text(encoding="utf-8"))
    assert handoff["environment"]["TOKEN"] == "capture-secret-value"
    assert handoff["environment"]["PATH"]
    assert handoff["canaries"] == ["capture-secret-value"]
    writer = manager.writer_for("connection-secret")
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={"result": "capture-secret-value"},
    )
    assert "capture-secret-value" not in str(
        manager.snapshot("connection-secret").events
    )
    await manager.close()
    assert not env_file.exists()


@pytest.mark.asyncio
async def test_server_group_exposes_capture_context_and_preserves_connection_identity() -> (
    None
):
    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="echo", command="echo-server")),)
    )
    await manager.start()
    try:
        config = manager.configurations()[0]
        assert manager.capture is not None
        assert config.connection_id == manager.snapshot().records[0].connection_id
        assert config.command == sys.executable
        assert manager.capture.evidence()["capture_server_count"] == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_in_process_loopback_is_observed_without_rewriting_endpoint() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=InProcessServer(name="fixture", factory=_server)),)
    )
    await manager.start()
    endpoint = manager.configurations()[0].endpoint
    assert endpoint is not None
    try:
        async with AsyncMCPTestKit() as kit:
            async with kit.direct(
                HTTPServer(name="loopback", url=endpoint, trust=TrustLevel.SDK_LOOPBACK)
            ) as client:
                await client.list_tools()
                await client.call_tool("draw", {})
        assert manager.capture is not None
        snapshot = manager.capture.snapshots()[0]
        assert snapshot.transport == "in_process"
        assert any(event.method == "tools/list" for event in snapshot.events)
        assert any(event.method == "tools/call" for event in snapshot.events)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_agent_execution_projects_wire_capture_into_stable_trace() -> None:
    writer_holder: dict[str, CaptureWriter] = {}

    async def handler(
        _request: HarnessTurnRequest, _state: MutableMapping[str, Any]
    ) -> str:
        writer = writer_holder["writer"]
        writer.write(
            transport="stdio",
            direction="client_to_server",
            payload={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "draw", "arguments": {"token": "secret-canary"}},
            },
        )
        writer.write(
            transport="stdio",
            direction="server_to_client",
            payload={
                "jsonrpc": "2.0",
                "id": 7,
                "result": {"content": [{"text": "ok"}]},
            },
        )
        return "done"

    class WireAdapter(DeterministicHarnessAdapter):
        async def open(self, launch: HarnessLaunch) -> HarnessSession:
            configurations = launch.configurations
            capture = launch.capture
            assert capture is not None
            writer_holder["writer"] = capture.writer_for(
                configurations[0].connection_id
            )
            return await super().open(launch)

    adapter = WireAdapter(handler=handler)
    registry = HarnessAdapterRegistry({"claude_code": lambda _harness: adapter})
    spec = AgentSpec(
        harness=ClaudeCode(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        message=UserMessage(content=(TextContent(text="draw"),)),
    )
    async with AsyncMCPTestKit(
        adapter_registry=registry, env={}, cwd="/tmp/m3-no-project"
    ) as kit:
        result = await kit.run(spec)
    assert result.trace is not None
    wire = [
        event
        for event in result.trace.events
        if event.provenance.origin.value == "wire_observed"
    ]
    assert any(event.kind.value == "tool.call_requested" for event in wire)
    assert any(event.kind.value == "tool.result_received" for event in wire)
    request = next(event for event in wire if event.kind.value == "tool.call_requested")
    assert request.connection_id is not None
    assert request.correlation is not None and request.correlation.jsonrpc_id == 7
    assert result.activity_health.value == "all_succeeded"
    assert "secret-canary" not in str(result.trace.model_dump(mode="json"))
