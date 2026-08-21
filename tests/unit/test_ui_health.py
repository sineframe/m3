from mcp_pal.ui.health import health_state

def test_backend_unavailable():
    assert health_state(request_error=True)["connected"] is False

def test_connected_and_ready():
    state = health_state({"ready": True, "checks": {"api_key": True, "database": True, "claude_executable": True, "required_cli_flags": {"ok": True}}})
    assert state["connected"] and state["runner_ready"]

def test_connected_missing_api_key_is_not_backend_down():
    state = health_state({"ready": False, "checks": {"api_key": False, "database": True, "claude_executable": True, "required_cli_flags": {"ok": True}}})
    assert state["connected"] and not state["runner_ready"]
    assert "ANTHROPIC_API_KEY is missing" in state["messages"][0]
