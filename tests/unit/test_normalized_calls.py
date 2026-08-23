from mcp_pal.trace.normalized import SCHEMA_VERSION, from_claude_trace, from_opencode_events, build_opencode_trace

def _span(name, ident, start, output=None, status="completed", meta=None):
    return {"kind":"tool_call","name":name,"id":ident,"start_ms":start,"end_ms":start+3,"duration_ms":3,"input":{"name":"argument-value","x":ident},"output":output,"status":status,"metadata":{"mcp_selected":meta if meta is not None else True}}

def test_claude_excludes_builtin_and_preserves_argument_name_and_error():
    trace={"spans":[_span("Read","builtin",1,"file",meta=False),_span("mcp__draw__paint","m1",2,{"error":"bad"},"error")],"mcp_calls":[]}
    calls=from_claude_trace(trace,"draw","stdio")
    assert len(calls)==1 and calls[0]["tool"]=="paint" and calls[0]["arguments"]["name"]=="argument-value"
    assert calls[0]["error"]=="bad" and calls[0]["status"]=="error" and calls[0]["duration_ms"]==3

def test_claude_repeated_same_name_pairs_wire_in_order_and_exact_arguments_result():
    spans=[_span("mcp__draw__paint","m1",1,"one"),_span("mcp__draw__paint","m2",10,"two")]
    spans += [{"id":"w1","kind":"mcp","name":"tools/call","input":{"params":{"name":"paint","arguments":{"a":1}}},"output":{"result":{"n":1}},"duration_ms":2},{"id":"w2","kind":"mcp","name":"tools/call","input":{"params":{"name":"paint","arguments":{"a":2}}},"output":{"result":{"n":2}},"duration_ms":4}]
    calls=from_claude_trace({"spans":spans},"draw","http")
    assert [c["arguments"] for c in calls]==[{"a":1},{"a":2}]
    assert [c["wire_request"]["params"]["arguments"] for c in calls]==[{"a":1},{"a":2}]
    assert [c["wire_response"] for c in calls]==[{"result":{"n":1}},{"result":{"n":2}}]

def test_opencode_wrappers_recovery_duplicates_and_unknown_timing():
    events=[
        {"offset_ms":5,"raw_event":{"type":"tool_use","part":{"tool":"draw_paint","callID":"c1","state":{"input":{"x":1},"status":"running"}}}},
        {"offset_ms":38,"raw_event":{"type":"tool_use","part":{"tool":"draw_paint","callID":"c1","state":{"input":{"x":1},"output":{"ok":True},"status":"completed"},"time":{"start":100,"end":130}}}},
        {"offset_ms":9,"raw_event":{"type":"tool_use","part":{"tool":"draw_paint","callID":"c2","state":{"status":"error","error":"nope"}}}},
    ]
    calls=from_opencode_events(events,"draw","sse")
    assert len(calls)==2 and calls[0]["tool"]=="paint" and calls[0]["result"]=={"ok":True} and calls[0]["duration_ms"]==30
    assert calls[0]["start_ms"]==8 and calls[0]["end_ms"]==38
    assert calls[1]["status"]=="error" and calls[1]["error"]=="nope" and calls[1]["duration_ms"] is None
    trace=build_opencode_trace(events=events,selected_server="draw",transport="sse",status="completed")
    assert trace["mcp_calls_schema"]==SCHEMA_VERSION and trace["summary"]["transport"]=="sse"
    assert trace["mcp_calls"][0]["wire_request"] is None and trace["limitations"]

def test_opencode_pending_status_is_not_reported_as_completed():
    calls=from_opencode_events([{"type":"tool_use","part":{"tool":"draw_paint","callID":"p","state":{"status":"pending","input":{}}}}],"draw")
    assert calls[0]["status"]=="pending" and calls[0]["duration_ms"] is None

def test_opencode_wire_frames_correlate_repeated_calls_and_errors():
    events=[{"type":"tool_use","part":{"tool":"draw_echo","callID":"a","state":{"status":"completed","input":{"name":"inside"},"output":"one"}}},{"type":"tool_use","part":{"tool":"draw_echo","callID":"b","state":{"status":"completed","input":{"name":"inside2"},"output":"two"}}}]
    protocol=[{"offset_ms":1,"direction":"client_to_server","payload":{"id":1,"method":"tools/call","params":{"name":"echo","arguments":{"name":"inside"}}}},{"offset_ms":3,"direction":"server_to_client","payload":{"id":1,"result":"one"}},{"offset_ms":4,"direction":"client_to_server","payload":{"id":2,"method":"tools/call","params":{"name":"echo","arguments":{"name":"inside2"}}}},{"offset_ms":6,"direction":"server_to_client","payload":{"id":2,"error":{"message":"bad"}}}]
    trace=build_opencode_trace(events=events,protocol_events=protocol,selected_server="draw",transport="stdio",status="completed")
    assert [call["arguments"] for call in trace["mcp_calls"]] == [{"name":"inside"},{"name":"inside2"}]
    assert trace["mcp_calls"][0]["wire_request"]["params"]["arguments"]=={"name":"inside"}
    assert trace["mcp_calls"][0]["provenance"]["wire"] is True and trace["mcp_calls"][0]["server_latency_ms"]==2
    assert trace["mcp_calls"][0]["limitations"] == [] and trace["limitations"] == []
    assert trace["mcp_calls"][1]["status"]=="error" and trace["mcp_calls"][1]["error"]=={"message":"bad"}
    tool_spans=[span for span in trace["spans"] if span["kind"]=="tool_call"]
    wire_spans=[span for span in trace["spans"] if span["kind"]=="mcp" and span["name"]=="tools/call"]
    assert len(tool_spans)==2 and len(wire_spans)==2
    assert [span["parent_id"] for span in wire_spans] == [span["id"] for span in tool_spans]

def test_opencode_waterfall_preserves_steps_reasoning_text_and_summary():
    events=[
        {"offset_ms":1,"raw_event":{"type":"step_start","part":{"type":"step-start"}}},
        {"offset_ms":2,"raw_event":{"type":"reasoning","part":{"type":"reasoning","text":"thinking"}}},
        {"offset_ms":3,"raw_event":{"type":"tool_use","part":{"type":"tool","callID":"c1","tool":"draw_echo","state":{"status":"completed","input":{},"output":"ok"}}}},
        {"offset_ms":7,"raw_event":{"type":"step_finish","part":{"type":"step-finish","cost":0.01,"tokens":{"input":4,"output":2}}}},
        {"offset_ms":8,"raw_event":{"type":"step_start","part":{"type":"step-start"}}},
        {"offset_ms":9,"raw_event":{"type":"text","part":{"type":"text","text":"done"}}},
        {"offset_ms":10,"raw_event":{"type":"step_finish","part":{"type":"step-finish","cost":0.02,"tokens":{"input":1,"output":3}}}},
    ]
    protocol=[
        {"offset_ms":3.5,"direction":"client_to_server","payload":{"id":1,"method":"tools/call","params":{"name":"echo","arguments":{"text":"actual"}}}},
        {"offset_ms":6,"direction":"server_to_client","payload":{"id":1,"result":"ok"}},
    ]
    trace=build_opencode_trace(events=events,protocol_events=protocol,selected_server="draw",transport="stdio",status="completed")
    assert trace["schema"] == "opencode.v2"
    turns=[span for span in trace["spans"] if span["kind"] == "model_turn"]
    assert len(turns) == 2
    assert [[step["kind"] for step in turn["steps"]] for turn in turns] == [["thinking", "tool_call"], ["text"]]
    assert turns[0]["steps"][0]["output"] == "thinking"
    assert turns[1]["steps"][0]["output"] == "done"
    assert not [span for span in trace["spans"] if span["kind"] in {"thinking", "text"}]
    assert trace["summary"] == {"transport":"stdio","duration_ms":10.0,"turns":2,"mcp_calls":1,"input_tokens":5,"output_tokens":5,"total_tokens":10,"cost_usd":0.03,"thinking":{"state":"visible","count":1}}
    tool=next(span for span in trace["spans"] if span["kind"]=="tool_call")
    wire=next(span for span in trace["spans"] if span["kind"]=="mcp" and span["name"]=="tools/call")
    assert tool["input"]=={"text":"actual"} and wire["parent_id"]==tool["id"] and wire["duration_ms"]==2.5


def test_opencode_adjacent_reasoning_chunks_are_combined_under_model_turn():
    events = [
        {"offset_ms": 1, "raw_event": {"type": "step_start", "part": {"type": "step-start"}}},
        {"offset_ms": 2, "raw_event": {"type": "reasoning", "part": {"type": "reasoning", "text": "first "}}},
        {"offset_ms": 3, "raw_event": {"type": "reasoning", "part": {"type": "reasoning", "text": "second"}}},
        {"offset_ms": 4, "raw_event": {"type": "text", "part": {"type": "text", "text": "answer"}}},
        {"offset_ms": 5, "raw_event": {"type": "step_finish", "part": {"type": "step-finish"}}},
    ]
    trace = build_opencode_trace(events=events, selected_server="draw", transport="stdio", status="completed")
    turn = next(span for span in trace["spans"] if span["kind"] == "model_turn")

    assert trace["schema"] == "opencode.v2"
    assert [(step["kind"], step["output"]) for step in turn["steps"]] == [
        ("thinking", "first second"),
        ("text", "answer"),
    ]
    assert len(turn["steps"][0]["source_span_ids"]) == 2
    assert not [span for span in trace["spans"] if span["kind"] in {"thinking", "text"}]

def test_opencode_unmatched_call_has_precise_limitation_only():
    call=from_opencode_events([{"type":"tool_use","part":{"tool":"draw_echo","callID":"u","state":{"status":"completed","input":{},"output":"ok"}}}],"draw")[0]
    assert call["wire_request"] is None and "correlated" in call["limitations"][0] and "interception" not in call["limitations"][0]

def test_opencode_protocol_metadata_is_sorted_and_stable():
    trace=build_opencode_trace(events=[],protocol_events=[{"offset_ms":4,"direction":"server_to_client","payload":{"id":2,"result":{}}},{"offset_ms":1,"direction":"client_to_server","payload":{"id":1,"method":"initialize"}}],selected_server="draw",transport="stdio",status="completed")
    assert [x["sequence"] for x in trace["protocol_events"]]==[1,2] and trace["protocol_events"][0]["method"]=="initialize" and trace["protocol_events"][1]["status"]=="response"

def test_opencode_empty_trace_has_no_false_limitation():
    trace=build_opencode_trace(events=[],selected_server="draw",transport="stdio",status="completed")
    assert trace["limitations"]==[] and trace["capture_status"]=="empty"

def test_opencode_partial_trace_is_partial():
    trace=build_opencode_trace(events=[{"type":"text","part":{"text":"x"}}],selected_server="draw",transport="stdio",status="timed_out")
    assert trace["capture_status"]=="partial"

def test_opencode_running_and_pending_are_preserved():
    events=[{"type":"tool_use","part":{"tool":"draw_a","callID":"r","state":{"status":"running"}}},{"type":"tool_use","part":{"tool":"draw_b","callID":"p","state":{"status":"pending"}}}]
    assert [c["status"] for c in from_opencode_events(events,"draw")] == ["running","pending"]

def test_opencode_native_duration_uses_elapsed_start_end():
    event={"type":"tool_use","part":{"tool":"draw_a","callID":"t","state":{"status":"completed"},"time":{"start":1000000,"end":1000042}}}
    call=from_opencode_events([event],"draw")[0]
    assert call["duration_ms"]==42 and call["end_ms"]-call["start_ms"]==42

def test_opencode_wire_error_marks_call_error():
    event={"type":"tool_use","part":{"tool":"draw_a","callID":"e","state":{"status":"completed","output":"bad"}}}
    protocol=[{"offset_ms":1,"direction":"client_to_server","payload":{"id":1,"method":"tools/call","params":{"name":"a","arguments":{}}}},{"offset_ms":2,"direction":"server_to_client","payload":{"id":1,"error":{"code":-1}}}]
    call=build_opencode_trace(events=[event],protocol_events=protocol,selected_server="draw",transport="stdio",status="completed")["mcp_calls"][0]
    assert call["status"]=="error" and call["error"]=={"code":-1}
