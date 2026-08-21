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
    assert [c["wire_request"]["params"]["arguments"] for c in calls]==[{"a":1},{"a":2}]
    assert [c["wire_response"] for c in calls]==[{"result":{"n":1}},{"result":{"n":2}}]

def test_opencode_wrappers_recovery_duplicates_and_unknown_timing():
    events=[
        {"offset_ms":5,"raw_event":{"type":"tool_use","part":{"tool":"draw_paint","callID":"c1","state":{"input":{"x":1},"status":"running"}}}},
        {"offset_ms":8,"raw_event":{"type":"tool_use","part":{"tool":"draw_paint","callID":"c1","state":{"input":{"x":1},"output":{"ok":True},"status":"completed"},"time":{"start":100,"end":130}}}},
        {"offset_ms":9,"raw_event":{"type":"tool_use","part":{"tool":"draw_paint","callID":"c2","state":{"status":"error","error":"nope"}}}},
    ]
    calls=from_opencode_events(events,"draw","sse")
    assert len(calls)==2 and calls[0]["tool"]=="paint" and calls[0]["result"]=={"ok":True} and calls[0]["duration_ms"]==30
    assert calls[1]["status"]=="error" and calls[1]["error"]=="nope" and calls[1]["duration_ms"] is None
    trace=build_opencode_trace(events=events,selected_server="draw",transport="sse",status="completed")
    assert trace["mcp_calls_schema"]==SCHEMA_VERSION and trace["summary"]["transport"]=="sse"
    assert trace["mcp_calls"][0]["wire_request"] is None and trace["limitations"]

def test_opencode_pending_status_is_not_reported_as_completed():
    calls=from_opencode_events([{"type":"tool_use","part":{"tool":"draw_paint","callID":"p","state":{"status":"pending","input":{}}}}],"draw")
    assert calls[0]["status"]=="pending" and calls[0]["duration_ms"] is None
