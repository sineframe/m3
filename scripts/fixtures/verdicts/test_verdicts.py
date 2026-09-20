"""Deliberately mixed verdicts for the installed CLI, SDK, and Reports gate."""

import pytest
from mcp import types
from mcp.server.lowlevel import Server

from m3 import MCPTestKit, expect
from m3.errors import ProtocolError
from m3.testing import FaultInjector
from m3.types import CallTool, DirectSpec, InProcessServer, ServerBinding


def server():
    async def list_tools(_context, _params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="bad",
                    input_schema={"type": "object"},
                    output_schema={"type": "object", "required": ["value"]},
                )
            ]
        )

    async def call_tool(_context, _params):
        return types.CallToolResult(
            content=[types.TextContent(text="expected tool failure")], is_error=True
        )

    return Server("verdict-fixture", on_list_tools=list_tools, on_call_tool=call_tool)


def test_expected_tool_error():
    with MCPTestKit() as kit:
        with kit.direct(
            InProcessServer(name="fixture", factory=server), validate_schemas=True
        ) as client:
            result = client.call_tool("bad", {})
            assert result.is_error is True


def test_failed_matcher():
    with MCPTestKit() as kit:
        with kit.direct(InProcessServer(name="fixture", factory=server)) as client:
            client.call_tool("bad", {})
        expect(client.final_trace).to_have_text("absent assertion text")


def test_protocol_error():
    fault = FaultInjector().protocol_error("tools/call", code=-32042)
    spec = DirectSpec(
        servers=(ServerBinding(server=fault.stdio_server(), alias="fault"),),
        operation=CallTool(server="fault", name="echo", arguments={}),
    )
    with MCPTestKit() as kit:
        result = kit.run(spec)
    if result.error is not None:
        raise ProtocolError(result.error.message)


@pytest.fixture
def broken():
    raise RuntimeError("setup failed")


def test_setup_error(broken):
    pass


def test_plain_pass():
    pass
