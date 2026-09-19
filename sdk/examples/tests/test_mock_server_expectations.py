"""Use the SDK's deterministic MCP server when a test owns the server contract."""

from __future__ import annotations

from m3 import MCPTestKit
from m3.sync_api import ToolCallResult
from m3.testing import MockMCPServer


def test_script_a_mock_server_and_verify_tool_usage() -> None:
    server = MockMCPServer(name="contract-example")

    @server.tool(
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
    )
    def greet(arguments: dict[str, str]) -> dict[str, str]:
        return {"message": f"Hello, {arguments['name']}!"}

    # Expectations are checked in order and fail on unexpected calls.
    server.expect_tool_call("greet", {"name": "Ada"})
    with MCPTestKit(env={}) as kit, kit.direct(server.in_process()) as client:
        result = client.call_tool("greet", {"name": "Ada"})

    server.verify()
    assert isinstance(result, ToolCallResult)
    assert result.structured_content == {"message": "Hello, Ada!"}


def test_record_a_mock_interaction_as_json() -> None:
    server = MockMCPServer(name="recording-example")

    @server.tool
    def identity(arguments: dict[str, str]) -> str:
        return arguments["value"]

    server.expect_tool_call("identity", {"value": "stable"})
    with MCPTestKit(env={}) as kit, kit.direct(server.in_process()) as client:
        client.call_tool("identity", {"value": "stable"})

    recording = server.recording()
    restored = type(recording).from_json(recording.to_json())
    assert restored.server_name == "recording-example"
    assert any(item.method == "tools/call" for item in restored.interactions)
