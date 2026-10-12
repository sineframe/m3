from m3.configuration import Config, load_config, merge_settings
from m3.types import (
    UNSET,
    CachePolicy,
    ClaudeCodePermissions,
    ClaudeCodeSettings,
    CliSettings,
    CodexPermissionProfile,
    CodexPermissions,
    CodexSettings,
    HarnessSettings,
    HarnessSettingsMap,
    HarnessTimeouts,
    McpTimeouts,
    NativeFilesystemPermissions,
    NativeNetworkPermissions,
    OpenCodePermissions,
    OpenCodeSettings,
    ResultsSettings,
    ServerSettings,
    StartupAdmission,
    Unset,
    WorkerBootstrapSettings,
)


def test_configuration_values(tmp_path):
    assert UNSET is Unset.UNSET
    timeouts = merge_settings(
        HarnessTimeouts(startup_seconds=90, total_seconds=120),
        HarnessTimeouts(initialize_seconds=15, session_seconds=30),
    )
    assert timeouts.startup_seconds == 90
    assert timeouts.total_seconds == 120
    profile = CodexPermissionProfile(
        name="project",
        filesystem=NativeFilesystemPermissions(
            rules={":workspace_roots": "write", "**/.env": "deny"}
        ),
        network=NativeNetworkPermissions(enabled=False),
    )
    config = Config(
        harness_timeouts=timeouts,
        mcp_timeouts=McpTimeouts(startup_seconds=60),
        harnesses=HarnessSettingsMap(
            codex=CodexSettings(permissions=CodexPermissions(profile=profile)),
            claude_code=ClaudeCodeSettings(
                permissions=ClaudeCodePermissions(permission_mode="plan")
            ),
            opencode=OpenCodeSettings(
                permissions=OpenCodePermissions(rules={"edit": "deny"})
            ),
        ),
        servers={"slow": ServerSettings(timeouts=McpTimeouts(startup_seconds=75))},
        cli=CliSettings(project_python_timeout_seconds=60),
        results=ResultsSettings(busy_timeout_seconds=30),
        worker_bootstrap=WorkerBootstrapSettings(timeout_seconds=30),
        cache=CachePolicy(mode="isolated"),
        admission=StartupAdmission(enabled=False),
    )
    assert HarnessSettings().timeouts.startup_seconds is UNSET
    assert config.servers["slow"].timeouts.startup_seconds == 75
    assert "profile" not in str(config.redacted_projection().get("native_settings"))
    path = tmp_path / "run.toml"
    path.write_text("[tool.m3.harness_timeouts]\nstartup_seconds=90\n")
    resolved = load_config(config_file=path, env={})
    assert resolved.harness_timeouts.startup_seconds == 90
