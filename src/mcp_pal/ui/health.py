"""Pure translation of API health responses into UI status and actions."""
def health_state(payload=None, request_error=False):
    if request_error or payload is None:
        return {"connected": False, "runner_ready": False, "title": "Backend unavailable", "messages": ["Start the API and check its URL."]}
    checks = payload.get("checks", {})
    messages = []
    if not checks.get("api_key"):
        messages.append("ANTHROPIC_API_KEY is missing. Add it to .env and restart the API.")
    if not checks.get("claude_executable"):
        messages.append("Claude executable is unavailable. Install Claude Code or set CLAUDE_EXECUTABLE, then restart the API.")
    flags = checks.get("required_cli_flags", {})
    if not flags.get("ok"):
        messages.append("Claude CLI flags are incomplete. Upgrade/configure Claude Code and restart the API.")
    if not checks.get("database"):
        messages.append("Database is unavailable. Check DATABASE_PATH permissions and restart the API.")
    return {"connected": True, "runner_ready": bool(payload.get("run_ready", payload.get("ready", False))), "title": "Backend connected", "messages": messages}
