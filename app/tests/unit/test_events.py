from mcp_pal_app.domain.events import derive_mcp_assertion, derive_mcp_summary, normalize_event

def call(ok=True):
    return {"type":"user","tool_name":"mcp__draw__create","tool_use_result":("ok" if ok else {"is_error":True,"error":"bad"})}
def use(): return {"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__draw__create"}]}}

def test_normalization_and_assertions():
    assert normalize_event(use())[0] == "tool_call"
    assert derive_mcp_assertion([use(),call()],"draw") == "passed"
    assert derive_mcp_assertion([use(),call(False)],"draw") == "failed"
    assert derive_mcp_assertion([use(),call(),call(False)],"draw") == "warning"
    assert derive_mcp_assertion([],"draw") == "failed"

def test_other_server_does_not_count():
    assert derive_mcp_assertion([{"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__other__x"}]}},call()],"draw") == "failed"

def test_initialization_summary_requires_selected_status():
    generic={"type":"system","subtype":"init"}
    assert derive_mcp_summary([generic],"draw")["initialization_state"] == "init_observed"
    connected={"type":"system","subtype":"init","mcp_servers":{"draw":{"status":"connected"}}}
    failed={"type":"system","subtype":"init","mcp_servers":{"draw":{"status":"failed"}}}
    assert derive_mcp_summary([connected],"draw")["initialization_state"] == "connected"
    assert derive_mcp_summary([failed],"draw")["initialization_state"] == "failed"
    list_connected={"type":"system","subtype":"init","mcp_servers":[{"name":"draw","status":"connected"}]}
    list_failed={"type":"system","subtype":"init","mcp_servers":[{"name":"draw","status":"failed"}]}
    assert derive_mcp_summary([list_connected],"draw")["initialization_state"] == "connected"
    assert derive_mcp_summary([list_failed],"draw")["initialization_state"] == "failed"

def test_opencode_tool_part_is_stable_and_correlated():
    event={"type":"tool_use","sessionID":"ses","part":{"type":"tool","callID":"call","tool":"draw_create","state":{"status":"completed","input":{},"output":"ok"}}}
    assert derive_mcp_assertion([event],"draw") == "passed"
    summary=derive_mcp_summary([event],"draw")
    assert summary["selected_server_call_names"] == ["draw_create"] and summary["success_count"] == 1
