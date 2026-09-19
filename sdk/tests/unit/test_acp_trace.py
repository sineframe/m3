from m3.trace.acp import build_acp_trace


def test_acp_trace_wire_authoritative_and_unknown_updates():
    trace = build_acp_trace(
        acp_frames=[
            {
                "offset_ms": 1,
                "payload": {
                    "method": "session/update",
                    "params": {"update": {"sessionUpdate": "agent_thought_chunk"}},
                },
            }
        ],
        mcp_frames=[
            {
                "offset_ms": 2,
                "direction": "client_to_server",
                "payload": {
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"text": "x"}},
                },
            },
            {
                "offset_ms": 5,
                "direction": "server_to_client",
                "payload": {"id": 7, "result": {"content": [{"text": "x"}]}},
            },
        ],
        selected_server="echo",
        configured_transport="http",
        instrumented_transport="http",
        status="completed",
    )
    assert (
        trace["schema"] == "acp.v2"
        and trace["mcp_calls"][0]["arguments"] == {"text": "x"}
        and trace["mcp_calls"][0]["server_latency_ms"] == 3
    )
    turn = next(s for s in trace["spans"] if s["kind"] == "model_turn")
    assert [step["kind"] for step in turn["steps"]] == ["thinking", "tool_call"]
    assert not [s for s in trace["spans"] if s["kind"] == "thinking"]


def _frame(offset, direction, payload):
    return {"offset_ms": offset, "direction": direction, "payload": payload}


def test_acp_tool_lifecycle_and_message_plan_state_updates_are_normalized():
    acp = [
        _frame(
            1,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "plan",
                        "entries": [{"status": "pending"}],
                    }
                },
            },
        ),
        _frame(
            2,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "t1",
                        "title": "echo",
                        "rawInput": {"x": 1},
                        "status": "running",
                    }
                },
            },
        ),
        _frame(
            4,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "rawOutput": {"ok": 1},
                        "status": "completed",
                    }
                },
            },
        ),
        _frame(
            5,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "current_mode_update",
                        "currentModeId": "safe",
                    }
                },
            },
        ),
        _frame(
            6,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "done"},
                    }
                },
            },
        ),
    ]
    trace = build_acp_trace(
        acp_frames=acp,
        selected_server="s",
        configured_transport="http",
        instrumented_transport="proxy",
    )
    call = trace["mcp_calls"][0]
    assert (
        call["tool"] == "echo"
        and call["arguments"] == {"x": 1}
        and call["result"] == {"ok": 1}
    )
    assert call["provenance"] == {"model": True, "wire": False, "correlation": "acp"}
    assert "correlated MCP transport" in call["limitations"][0]
    assert {span["kind"] for span in trace["spans"]} >= {
        "model_turn",
        "tool_call",
        "update",
        "plan",
        "state",
    }
    turn = next(span for span in trace["spans"] if span["kind"] == "model_turn")
    assert [step["kind"] for step in turn["steps"]] == [
        "plan",
        "tool_call",
        "update",
        "state",
        "text",
    ]
    assert turn["steps"][-1]["output"] == "done"
    assert not [span for span in trace["spans"] if span["kind"] in {"thinking", "text"}]
    assert trace["schema"] == "acp.v2"
    assert trace["summary"]["configured_transport"] == "http"
    assert trace["summary"]["instrumented_transport"] == "proxy"


def test_wire_ids_are_typed_and_repeated_ids_use_request_sequence():
    wire = [
        _frame(
            1,
            "client_to_server",
            {
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"n": 1}},
            },
        ),
        _frame(
            2,
            "client_to_server",
            {
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"n": 2}},
            },
        ),
        _frame(3, "server_to_client", {"id": 1, "result": {"n": 1}}),
        _frame(
            4,
            "client_to_server",
            {
                "id": "1",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"n": "string"}},
            },
        ),
        _frame(
            5, "server_to_client", {"id": 1, "error": {"code": 9, "message": "bad"}}
        ),
        _frame(6, "server_to_client", {"id": "1", "result": {"n": "string"}}),
    ]
    calls = build_acp_trace(acp_frames=[], mcp_frames=wire, selected_server="s")[
        "mcp_calls"
    ]
    assert [call["request_sequence"] for call in calls] == [1, 2, 4]
    assert calls[0]["result"] == {"n": 1} and calls[0]["status"] == "completed"
    assert (
        calls[1]["error"] == {"code": 9, "message": "bad"}
        and calls[1]["status"] == "error"
    )
    assert calls[2]["result"] == {"n": "string"} and calls[2]["status"] == "completed"
    assert [call["server_latency_ms"] for call in calls] == [2.0, 3.0, 2.0]


def test_wire_only_and_acp_only_calls_are_preserved_and_inference_is_unambiguous():
    acp = [
        _frame(
            10,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "a",
                        "title": "echo",
                        "rawInput": {"x": 1},
                    }
                },
            },
        )
    ]
    wire = [
        _frame(
            11,
            "client_to_server",
            {
                "id": 7,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"x": 1}},
            },
        ),
        _frame(12, "server_to_client", {"id": 7, "result": {"ok": True}}),
        _frame(
            50,
            "client_to_server",
            {
                "id": 8,
                "method": "tools/call",
                "params": {"name": "other", "arguments": {}},
            },
        ),
    ]
    trace = build_acp_trace(
        acp_frames=acp, mcp_frames=wire, selected_server="s", status="timed_out"
    )
    assert trace["capture_status"] == "partial"
    assert len(trace["mcp_calls"]) == 2
    linked = next(call for call in trace["mcp_calls"] if call["id"] == 7)
    assert linked["provenance"] == {
        "model": True,
        "wire": True,
        "correlation": "inferred",
    }
    wire_only = next(call for call in trace["mcp_calls"] if call["id"] == 8)
    assert wire_only["provenance"] == {
        "model": False,
        "wire": True,
        "correlation": "wire",
    }

    # Two ACP candidates at the same time make inference intentionally
    # ambiguous; neither is silently joined to the wire record.
    acp += [
        _frame(
            20,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "b",
                        "title": "echo",
                    }
                },
            },
        )
    ]
    ambiguous = build_acp_trace(
        acp_frames=acp, mcp_frames=wire[:2], selected_server="s"
    )
    assert sum(call["provenance"]["wire"] for call in ambiguous["mcp_calls"]) == 1
    assert sum(call["provenance"]["model"] for call in ambiguous["mcp_calls"]) == 2


def test_inferred_link_ignores_initialize_and_tools_list_mcp_event_spans():
    acp = [
        _frame(
            10,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "t",
                        "title": "echo",
                        "rawInput": {"x": 1},
                        "status": "running",
                    }
                },
            },
        )
    ]
    wire = [
        _frame(1, "client_to_server", {"id": 1, "method": "initialize", "params": {}}),
        _frame(2, "server_to_client", {"id": 1, "result": {"capabilities": {}}}),
        _frame(3, "client_to_server", {"id": 2, "method": "tools/list", "params": {}}),
        _frame(4, "server_to_client", {"id": 2, "result": {"tools": []}}),
        _frame(
            11,
            "client_to_server",
            {
                "id": 3,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"x": 1}},
            },
        ),
        _frame(12, "server_to_client", {"id": 3, "result": {"ok": True}}),
    ]
    trace = build_acp_trace(acp_frames=acp, mcp_frames=wire, selected_server="s")
    call = next(call for call in trace["mcp_calls"] if call["id"] == 3)
    assert call["provenance"] == {
        "model": True,
        "wire": True,
        "correlation": "inferred",
    }
    assert any(
        span["kind"] == "mcp_event" and span["name"] == "initialize"
        for span in trace["spans"]
    )


def test_malformed_and_unknown_frames_remain_separate_and_tolerated():
    trace = build_acp_trace(
        acp_frames=[
            "not-json",
            _frame(
                2,
                "server_to_client",
                {"method": "vendor/unknown", "params": {"_meta": {"x": 1}}},
            ),
        ],
        mcp_frames=["not-json"],
        selected_server="s",
    )
    assert trace["protocol_events"][0]["payload"] == "not-json"
    assert trace["mcp_protocol_events"][0]["payload"] == "not-json"
    assert any(
        span["kind"] == "update" and span["metadata"].get("unknown")
        for span in trace["spans"]
    )
    assert any(span["kind"] == "mcp_event" for span in trace["spans"])


def test_acp_adjacent_thought_chunks_are_combined_before_message_and_tool_steps():
    acp = [
        _frame(
            1,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "first "},
                    }
                },
            },
        ),
        _frame(
            2,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "second"},
                    }
                },
            },
        ),
        _frame(
            3,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "answer"},
                    }
                },
            },
        ),
        _frame(
            4,
            "server_to_client",
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "t1",
                        "title": "echo",
                        "rawInput": {"x": 1},
                        "status": "running",
                    }
                },
            },
        ),
    ]
    trace = build_acp_trace(acp_frames=acp, selected_server="s")
    turn = next(span for span in trace["spans"] if span["kind"] == "model_turn")

    assert trace["schema"] == "acp.v2"
    assert [(step["kind"], step["output"]) for step in turn["steps"]] == [
        ("thinking", "first second"),
        ("text", "answer"),
        ("tool_call", None),
    ]
    assert len(turn["steps"][0]["source_span_ids"]) == 2
    assert not [span for span in trace["spans"] if span["kind"] in {"thinking", "text"}]
