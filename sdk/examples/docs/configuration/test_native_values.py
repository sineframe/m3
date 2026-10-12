from m3.types import (
    Codex,
    CodexPermissionProfile,
    CodexPermissions,
    McpTimeouts,
    NativeFilesystemPermissions,
    NativeNetworkPermissions,
    StdioServer,
)

profile = CodexPermissionProfile(
    name="project",
    filesystem=NativeFilesystemPermissions(
        rules={":minimal": "read", ":project_roots": "write"},
    ),
    network=NativeNetworkPermissions(enabled=False),
)
harness = Codex(
    model="your-codex-model",
    runtime="managed",
    version="0.162.1",
    permissions=CodexPermissions(profile="project", profiles=(profile,)),
)
server = StdioServer(
    name="slow",
    command="python3",
    args=("slow_mcp.py",),
    timeouts=McpTimeouts(startup_seconds=60, request_seconds=120),
)


def test_native_values() -> None:
    assert harness.permissions.profile == "project"
    assert harness.permissions.profiles == (profile,)
    assert server.timeouts.startup_seconds == 60
    assert server.timeouts.request_seconds == 120
    assert CodexPermissions(profile=profile).profile == profile
