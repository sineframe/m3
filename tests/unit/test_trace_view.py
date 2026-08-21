from mcp_pal.ui.trace_view import (
    actor,
    display_name,
    format_duration,
    server_latency_for,
    visible_spans,
    wire_unavailable_message,
)


def test_overview_is_chronological_and_hides_bookkeeping_by_default():
    spans = [
        {"id": "run", "kind": "run", "start_ms": 0, "end_ms": 100},
        {"id": "session", "kind": "mcp_session", "start_ms": 2, "end_ms": 90},
        {"id": "tool", "kind": "tool_call", "start_ms": 30, "end_ms": 60},
        {"id": "turn", "kind": "model_turn", "start_ms": 10, "end_ms": 70},
        {"id": "thinking", "kind": "thinking", "start_ms": 20, "end_ms": 25},
        {"id": "protocol", "kind": "mcp", "start_ms": 35, "end_ms": 50},
        {"id": "result", "kind": "result", "start_ms": 100, "end_ms": 100},
    ]

    assert [span["id"] for span in visible_spans(spans)] == ["turn", "thinking", "tool"]
    assert [span["id"] for span in visible_spans(spans, include_protocol=True)] == [
        "turn",
        "thinking",
        "tool",
        "protocol",
    ]


def test_trace_labels_are_human_readable():
    tool = {"kind": "tool_call", "name": "MCP tool · mcp__draw__create_element"}
    protocol = {"kind": "mcp", "name": "tools/call"}

    assert display_name(tool) == "MCP · create_element"
    assert actor(tool) == "MCP call"
    assert display_name(protocol) == "Server · tools/call"
    assert actor(protocol) == "MCP server"
    assert format_duration(10890.6) == "10.89 s"
    assert format_duration(3) == "3.00 ms"
    assert format_duration(None) == "—"
    builtin = {"kind": "tool_call", "name": "Read", "metadata": {"mcp_selected": False}}
    assert display_name(builtin) == "Tool · Read"
    assert actor(builtin) == "Claude tool"
    assert "OpenCode" in wire_unavailable_message("opencode")
    assert "correlated transport" in wire_unavailable_message("claude-code")


def test_server_latency_is_read_from_correlated_protocol_child():
    tool = {"id": "tool-1", "kind": "tool_call"}
    spans = [
        tool,
        {"id": "mcp-1", "parent_id": "tool-1", "kind": "mcp", "name": "tools/call", "duration_ms": 15},
        {"id": "mcp-2", "parent_id": "tool-1", "kind": "mcp", "name": "tools/list", "duration_ms": 4},
    ]

    assert server_latency_for(tool, spans) == 15
