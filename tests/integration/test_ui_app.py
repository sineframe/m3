from streamlit.testing.v1 import AppTest
from pathlib import Path

def test_documented_streamlit_entrypoint_smoke(monkeypatch):
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def json(self):
                if url.endswith("/health"):
                    return {"status": "connected", "ready": True, "run_ready": True, "checks": {"api_key": True, "database": True, "claude_executable": True, "required_cli_flags": {"ok": True}}}
                if url.endswith("/capabilities"):
                    return {"models": ["test-model"]}
                return []
        return Response()

    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py").run()
    assert not app.exception
    assert any("New Run" in item.value for item in app.title) or any("New Run" in item.value for item in app.header)

def test_harness_change_refreshes_model_options_before_submit(monkeypatch):
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def json(self):
                if url.endswith("/health"):
                    return {"status":"connected","ready":True,"run_ready":True,"harnesses":{"claude-code":{"ready":True},"opencode":{"ready":True}},"checks":{}}
                if url.endswith("/capabilities"):
                    return {"harnesses":["claude-code","opencode"],"models":["claude-model"],"models_by_harness":{"claude-code":["claude-model"],"opencode":["opencode/big-pickle"]}}
                if url.endswith("/profiles/profile-1"):
                    return {"id":"profile-1","name":"Profile","current_revision_id":"revision-1","revisions":[{"id":"revision-1","revision_number":1,"mcp_json":{"mcpServers":{"draw":{"command":"echo"}}}}]}
                if url.endswith("/profiles"):
                    return [{"id":"profile-1","name":"Profile","current_revision_id":"revision-1"}]
                return []
        return Response()

    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py").run()
    harness = next(widget for widget in app.selectbox if widget.label == "Harness")
    app = harness.select("opencode").run()
    model = next(widget for widget in app.selectbox if widget.label == "Model")
    assert model.value == "opencode/big-pickle"

def test_completed_opencode_report_shows_configured_transport(monkeypatch):
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def json(self):
                if url.endswith("/health"):
                    return {"status":"connected","ready":True,"run_ready":True,"harnesses":{"opencode":{"ready":True}},"checks":{}}
                if url.endswith("/capabilities"):
                    return {"harnesses":["opencode"],"models_by_harness":{"opencode":["opencode/big-pickle"]}}
                if url.endswith("/profiles/profile-1"):
                    return {"id":"profile-1","name":"Profile","current_revision_id":"revision-1","revisions":[{"id":"revision-1","revision_number":1,"mcp_json":{"mcpServers":{"draw":{"command":"echo"}}}}]}
                if url.endswith("/profiles"):
                    return [{"id":"profile-1","name":"Profile","current_revision_id":"revision-1"}]
                if url.endswith("/runs/run-1/report"):
                    return {"run":{"id":"run-1","harness":"opencode","status":"completed","expected_output":"ok"},"assertions":{},"trace":{"available":True,"harness":"opencode","schema":"opencode.v1","summary":{"transport":"stdio","duration_ms":12,"turns":1},"protocol_events":[],"spans":[{"id":"turn-1","parent_id":"run","kind":"model_turn","name":"Agent step 1","start_ms":0,"end_ms":12,"duration_ms":12,"metadata":{"harness":"opencode"}},{"id":"tool-1","parent_id":"turn-1","kind":"tool_call","name":"MCP tool · echo","start_ms":2,"end_ms":8,"duration_ms":6,"metadata":{"harness":"opencode","mcp_selected":True}},{"id":"wire-1","parent_id":"tool-1","kind":"mcp","name":"tools/call","start_ms":3,"end_ms":7,"duration_ms":4,"metadata":{"harness":"opencode"}}],"mcp_calls":[{"server":"draw","tool":"echo","status":"completed","harness":"opencode","transport":"stdio","arguments":{"text":"actual"},"result":"ok","wire_request":{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"text":"actual"}}},"wire_response":{"jsonrpc":"2.0","id":3,"result":{"content":[]}},"server_latency_ms":4.0,"provenance":{"wire":True}}]}}
                if url.endswith("/runs/run-1"):
                    return {"id":"run-1","status":"completed"}
                return []
        return Response()

    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py")
    app.session_state["active_run"] = "run-1"
    app = app.run()
    transport = next(metric for metric in app.metric if metric.label == "Transport")
    assert transport.value == "STDIO"
    assert next(metric for metric in app.metric if metric.label == "OpenCode steps").value == "1"
    assert any(toggle.label == "Show protocol events" for toggle in app.toggle)
    assert any("server latency" in str(item.value).lower() for item in app.caption)
    assert not any("Wire capture unavailable" in str(item.value) for item in app.caption)


def test_acp_selection_id_controls_readiness_defaults_and_payload(monkeypatch):
    calls=[]
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def __init__(self, value): self.value = value
            def json(self): return self.value
        if url.endswith("/health"):
            # Global native health is deliberately false; the selected local
            # ACP descriptor must still be runnable.
            return Response({"status":"degraded","ready":False,"run_ready":False,"checks":{}})
        if url.endswith("/capabilities"):
            def descriptor(selection_id, name, revision_id, ready):
                return {"selection_id":selection_id,"kind":"acp","harness":"acp","name":name,"profile_id":selection_id.rsplit(":",1)[-1],"revision_id":revision_id,"ready":ready,"local_ready":ready,"models":["agent-default"],"tool_modes":["agent_default"],"agent_modes":[{"id":"safe","name":"Safe"}],"session_config_options":[{"id":"allow_tools","name":"Allow tools","type":"boolean","default":True},{"id":"quality","name":"Quality","type":"select","default":"high","options":[{"value":"low","name":"Low"},{"value":"high","name":"High"}]}],"warnings":[] if ready and name == "Ready" else ["Harness is not fully verified"],"full_verified":name == "Ready"}
            return Response({"harnesses":[descriptor("profile:one","Ready","hrev-one",True),descriptor("profile:two","Unavailable","hrev-two",False)]})
        if url.endswith("/profiles"):
            return Response([{"id":"mcp-1","name":"MCP","current_revision_id":"mrev-1"}])
        if url.endswith("/profiles/mcp-1"):
            return Response({"id":"mcp-1","name":"MCP","revisions":[{"id":"mrev-1","revision_number":1,"mcp_json":{"mcpServers":{"echo":{"command":"echo"}}}}]})
        if url.endswith("/runs") and method == "POST":
            calls.append(kwargs.get("json")); return Response({"id":"run-1"})
        if url.endswith("/runs/run-1"):
            return Response({"id":"run-1","status":"completed"})
        if url.endswith("/runs/run-1/report"):
            return Response({"run":{"id":"run-1","harness":"acp","final_output":"done"},"assertions":{},"trace":{"available":True,"schema":"acp.v1","harness":"acp","summary":{},"mcp_calls":[]}})
        return Response([])

    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py").run()
    harness = next(widget for widget in app.selectbox if widget.label == "Harness")
    assert harness.options == ["Ready · profile:one", "Unavailable · profile:two"]
    assert next(widget for widget in app.checkbox if widget.label == "Allow tools").value is True
    app.text_area[0].set_value("Use echo")
    app.text_area[1].set_value("It echoes")
    app.button[0].click()  # Run; the fully verified descriptor needs no confirmation.
    app.run()
    assert calls == [{"harness":"acp","model":"agent-default","prompt":"Use echo","expected_output":"It echoes","profile_revision_id":"mrev-1","enabled_server":"echo","tool_mode":"agent_default","harness_revision_id":"hrev-one","agent_mode_id":"safe","session_config":{"allow_tools":True,"quality":"high"}}]


def test_acp_sdk_snake_case_defaults_and_current_mode_are_typed_and_submitted(monkeypatch):
    calls=[]
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def __init__(self, value): self.value = value
            def json(self): return self.value
        if url.endswith("/health"): return Response({"status":"degraded","ready":False,"run_ready":False,"checks":{}})
        if url.endswith("/capabilities"):
            return Response({"harnesses":[{"selection_id":"profile:sdk","kind":"acp","harness":"acp","name":"SDK","profile_id":"sdk","revision_id":"hrev-sdk","ready":True,"models":["agent-default"],"tool_modes":["agent_default"],"current_agent_mode_id":"fast","agent_modes":[{"id":"safe","name":"Safe"},{"id":"fast","name":"Fast"}],"session_config_options":[{"id":"allow_tools","type":"boolean","current_value":False,"default":True},{"id":"quality","type":"select","current_value":"low","default":"high","options":[{"value":"low","name":"Low"},{"value":"high","name":"High"}]}],"full_verified":True,"warnings":[]}]})
        if url.endswith("/profiles"): return Response([{"id":"mcp-sdk","name":"MCP","current_revision_id":"mrev-sdk"}])
        if url.endswith("/profiles/mcp-sdk"): return Response({"id":"mcp-sdk","name":"MCP","revisions":[{"id":"mrev-sdk","revision_number":1,"mcp_json":{"mcpServers":{"echo":{"command":"echo"}}}}]})
        if url.endswith("/runs") and method == "POST": calls.append(kwargs.get("json")); return Response({"id":"run-sdk"})
        if url.endswith("/runs/run-sdk"): return Response({"id":"run-sdk","status":"completed"})
        if url.endswith("/runs/run-sdk/report"): return Response({"run":{"id":"run-sdk","harness":"acp","final_output":"done"},"assertions":{},"trace":{"available":True,"schema":"acp.v1","summary":{},"mcp_calls":[]}})
        return Response([])

    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py").run()
    # The descriptor's snake_case current values are displayed, preserving
    # bool/string types rather than falling back to schema defaults.
    assert next(widget for widget in app.checkbox if widget.label == "allow_tools").value is False
    assert next(widget for widget in app.selectbox if widget.label == "quality").value == "low"
    assert next(widget for widget in app.selectbox if widget.label == "Agent mode").value == "fast"
    app.text_area[0].set_value("Use echo")
    app.text_area[1].set_value("done")
    app.button[0].click(); app.run()
    assert calls == [{"harness":"acp","model":"agent-default","prompt":"Use echo","expected_output":"done","profile_revision_id":"mrev-sdk","enabled_server":"echo","tool_mode":"agent_default","harness_revision_id":"hrev-sdk","agent_mode_id":"fast","session_config":{"allow_tools":False,"quality":"low"}}]


def test_unverified_second_acp_profile_requires_confirmation_and_uses_its_revision(monkeypatch):
    calls=[]
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def __init__(self, value): self.value = value
            def json(self): return self.value
        if url.endswith("/health"): return Response({"status":"degraded","ready":False,"run_ready":False,"checks":{}})
        if url.endswith("/capabilities"):
            return Response({"harnesses":[
                {"selection_id":"profile:one","kind":"acp","harness":"acp","name":"Ready","profile_id":"one","revision_id":"hrev-one","ready":True,"local_ready":True,"models":["agent-default"],"tool_modes":["agent_default"],"full_verified":True,"warnings":[]},
                {"selection_id":"profile:two","kind":"acp","harness":"acp","name":"Unverified","profile_id":"two","revision_id":"hrev-two","ready":True,"local_ready":True,"models":["agent-default"],"tool_modes":["agent_default"],"full_verified":False,"warnings":["Harness is not fully verified"]},
            ]})
        if url.endswith("/profiles"): return Response([{"id":"mcp-1","name":"MCP","current_revision_id":"mrev-1"}])
        if url.endswith("/profiles/mcp-1"): return Response({"id":"mcp-1","name":"MCP","revisions":[{"id":"mrev-1","revision_number":1,"mcp_json":{"mcpServers":{"echo":{"command":"echo"}}}}]})
        if url.endswith("/runs") and method == "POST": calls.append(kwargs.get("json")); return Response({"id":"run-1"})
        if url.endswith("/runs/run-1"): return Response({"id":"run-1","status":"completed"})
        if url.endswith("/runs/run-1/report"): return Response({"run":{"id":"run-1","harness":"acp"},"assertions":{},"trace":{"schema":"acp.v1","available":True,"mcp_calls":[]}})
        return Response([])

    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py").run()
    app.selectbox[1].select("profile:two")
    app.run()
    assert any(item.label == "I understand this harness is unverified and will run unsandboxed" for item in app.checkbox)
    app.text_area[0].set_value("Use echo")
    app.text_area[1].set_value("It echoes")
    # The submit button is disabled until the explicit confirmation.
    assert app.button[0].proto.disabled is True
    app.checkbox[-1].set_value(True)
    app.button[0].click()
    app.run()
    assert calls[0]["harness_revision_id"] == "hrev-two"
    assert calls[0]["model"] == "agent-default" and calls[0]["tool_mode"] == "agent_default"


def test_history_uses_backend_harness_profile_filter(monkeypatch):
    import streamlit as st
    requests_seen=[]
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def json(self): return self.value
            def __init__(self, value): self.value = value
        if url.endswith("/health"): return Response({"status":"degraded","ready":False,"run_ready":False,"checks":{}})
        if url.endswith("/capabilities"): return Response({"harnesses":[]})
        if url.endswith("/runs"):
            requests_seen.append(kwargs.get("params")); return Response([])
        return Response([])

    monkeypatch.setattr("requests.request", request)
    class HistoryNavigation:
        title = "Run History"
        def run(self): pass
    monkeypatch.setattr(st, "navigation", lambda pages: HistoryNavigation())
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py").run()
    app.selectbox[0].select("completed")
    app.selectbox[1].select("acp")
    app.text_input[1].set_value("harness-profile-1")
    app.run()
    assert requests_seen[-1] == {"status":"completed","harness":"acp","harness_profile_id":"harness-profile-1"}


def test_history_clone_preserves_builtin_and_acp_selection_ids(monkeypatch):
    import streamlit as st
    run={"id":"run-acp","created_at":"now","status":"completed","model":"agent-default","harness":"acp","harness_profile_id":"hp-1","profile_revision_id":"mrev-1","enabled_server":"echo","tool_mode":"agent_default","prompt":"p","expected_output":"x","harness_snapshot":{"revision_id":"hrev-1","session_config":{}}}
    def request(method, url, **kwargs):
        class Response:
            status_code=200; content=b"{}"
            def __init__(self,value): self.value=value
            def json(self): return self.value
        if url.endswith("/health"): return Response({"status":"connected","ready":True,"run_ready":True,"checks":{}})
        if url.endswith("/capabilities"): return Response({"harnesses":[]})
        if url.endswith("/runs/run-acp/report"): return Response({"run":run,"assertions":{},"trace":{"available":False}})
        if url.endswith("/runs"): return Response([run])
        return Response([])
    monkeypatch.setattr("requests.request",request)
    class HistoryNavigation:
        title="Run History"
        def run(self): pass
    monkeypatch.setattr(st,"navigation",lambda pages:HistoryNavigation())
    app=AppTest.from_file(Path(__file__).parents[2]/"src/mcp_pal/ui/app.py").run()
    next(button for button in app.button if button.label=="Clone to New Run").click(); app.run()
    assert app.session_state["prefill"]["selection_id"] == "profile:hp-1"


def test_acp_report_uses_final_output_and_acp_waterfall_labels(monkeypatch):
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def __init__(self, value): self.value = value
            def json(self): return self.value
        if url.endswith("/health"): return Response({"status":"degraded","ready":False,"run_ready":False,"checks":{}})
        if url.endswith("/capabilities"): return Response({"harnesses":[]})
        if url.endswith("/profiles"): return Response([{"id":"mcp-1","name":"MCP","current_revision_id":"mrev-1"}])
        if url.endswith("/profiles/mcp-1"): return Response({"id":"mcp-1","name":"MCP","revisions":[{"id":"mrev-1","revision_number":1,"mcp_json":{"mcpServers":{"echo":{"command":"echo"}}}}]})
        if url.endswith("/runs/run-acp"): return Response({"id":"run-acp","status":"completed"})
        if url.endswith("/runs/run-acp/report"):
            return Response({"run":{"id":"run-acp","harness":"acp","final_output":"ACP says hello","expected_output":"hello"},"assertions":{},"trace":{"available":True,"schema":"acp.v1","harness":"acp","summary":{"duration_ms":10,"turns":1},"spans":[{"id":"turn","kind":"model_turn","name":"ACP turn","start_ms":0,"end_ms":10,"duration_ms":10,"metadata":{}}],"mcp_calls":[]}})
        return Response([])
    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py")
    app.session_state["active_run"] = "run-acp"
    app = app.run()
    assert next(item for item in app.metric if item.label == "ACP turn").value == "1"
    assert any(item.value == "ACP says hello" for item in app.code)
    assert any("acp.v1" in item.value for item in app.markdown)


def test_harness_profile_form_probe_archive_and_import_actions(monkeypatch):
    import streamlit as streamlit
    class HarnessNavigation:
        title = "Harness Profiles"
        def run(self): return None
    monkeypatch.setattr(streamlit, "navigation", lambda pages: HarnessNavigation())
    calls=[]
    profile={"id":"hp-1","name":"Agent","description":"desc","archived":False,"current_revision_id":"hr-1","revisions":[{"id":"hr-1","revision_number":1,"manifest":{"command":"agent","args":[],"env":{}},"trusted_unsandboxed":True}]}
    def request(method, url, **kwargs):
        class Response:
            status_code = 200
            content = b"{}"
            def __init__(self, value): self.value = value
            def json(self): return self.value
        calls.append((method,url,kwargs))
        if url.endswith("/health"): return Response({"status":"connected","ready":True,"run_ready":True,"checks":{}})
        if url.endswith("/capabilities"): return Response({"harnesses":[{"selection_id":"profile:hp-1","kind":"acp","harness":"acp","name":"Agent","profile_id":"hp-1","revision_id":"hr-1","ready":True,"local_ready":True,"protocol_verified":True,"full_verified":False,"warnings":["Harness is not fully verified"]}]})
        if url.endswith("/harness-profiles") and method == "GET": return Response([profile])
        if url.endswith("/harness-profiles/hp-1/probes"): return Response([])
        if url.endswith("/harness-profiles/hp-1/export"): return Response({"name":"Agent","manifest":profile["revisions"][0]["manifest"]})
        if url.endswith("/harness-profiles/import"): return Response({"id":"imported"})
        if url.endswith("/harness-profiles") and method == "POST": return Response({"id":"created"})
        return Response({})
    monkeypatch.setattr("requests.request", request)
    app=AppTest.from_file(Path(__file__).parents[2] / "src/mcp_pal/ui/app.py").run()
    app.text_input[0].set_value("Created")
    app.text_input[1].set_value("A test agent")
    app.text_input[2].set_value("agent")
    app.text_area[0].set_value('["--acp", "--json"]')
    app.text_area[1].set_value('{"TOKEN":"${TEAM_TOKEN}"}')
    app.checkbox[0].set_value(True)
    app.button[0].click(); app.run()
    created=next(item for item in calls if item[0] == "POST" and item[1].endswith("/harness-profiles") and "trusted_unsandboxed" in (item[2].get("json") or {}))
    assert created[2]["json"]["manifest"]["args"] == ["--acp","--json"]
    assert created[2]["json"]["manifest"]["env"] == {"TOKEN":"${TEAM_TOKEN}"}
    # Import and probe controls are rendered on the same page; verify their
    # endpoint wiring independently of the create form rerun.
    app.file_uploader[0].upload("import.json", b'{"name":"Imported","manifest":{"command":"agent"}}', "application/json")
    app.run()
    next(item for item in app.button if item.label == "Import as unverified profile").click(); app.run()
    assert any(method == "POST" and url.endswith("/harness-profiles/import") for method,url,_ in calls)
    next(item for item in app.button if item.label == "Start protocol probe").click(); app.run()
    assert any(method == "POST" and url.endswith("/harness-profiles/hp-1/probe") and kwargs.get("params",{}).get("kind") == "protocol" for method,url,kwargs in calls)
    next(item for item in app.button if item.label == "Archive profile").click(); app.run()
    assert any(method == "POST" and url.endswith("/harness-profiles/hp-1/archive") for method,url,_ in calls)
