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
