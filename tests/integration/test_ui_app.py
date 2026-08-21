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
