"""Explicit settings resolve before any native process or MCP server starts."""

import pytest
from pydantic import ValidationError

from m3._native_settings import native_literal_inputs, resolve_native_settings
from m3._types.specs import AgentSpec
from m3.configuration import Config, merge_settings
from m3.errors import ModelValidationError
from m3.types import (
    ClaudeCode,
    ClaudeCodePermissions,
    Codex,
    CodexPermissionProfile,
    CodexPermissions,
    CodexSettings,
    HarnessSettingsMap,
    McpTimeouts,
    NativeFilesystemPermissions,
    NativeNetworkPermissions,
    OpenCode,
    OpenCodePermissions,
    ServerBinding,
    ServerSettings,
    StdioServer,
)


def _binding(name: str, **kwargs: object) -> ServerBinding:
    return ServerBinding(server=StdioServer(name=name, command="fixture", **kwargs))


def test_default_native_options_are_omitted_from_old_serialized_shapes() -> None:
    spec = AgentSpec(harness=Codex(model="fixture"), servers=(_binding("slow"),))
    encoded = spec.model_dump(mode="json")
    assert "permissions" not in encoded["harness"]
    assert "harness_permissions" not in encoded
    assert "timeouts" not in encoded["servers"][0]
    assert "timeouts" not in encoded["servers"][0]["server"]
    assert AgentSpec.model_validate_json(spec.model_dump_json()) == spec


@pytest.mark.parametrize(
    "harness,permissions",
    [
        (Codex, CodexPermissions()),
        (ClaudeCode, ClaudeCodePermissions()),
        (OpenCode, OpenCodePermissions()),
    ],
)
def test_session_override_type_survives_json(
    harness: type, permissions: object
) -> None:
    spec = AgentSpec(
        harness=harness(model="fixture"),
        servers=(_binding("slow"),),
        harness_permissions=permissions,
    )
    restored = AgentSpec.model_validate_json(spec.model_dump_json())
    assert type(restored.harness_permissions) is type(permissions)


def test_profile_selector_switch_inherits_explicit_definitions() -> None:
    read = CodexPermissionProfile(
        name="read", filesystem=NativeFilesystemPermissions(rules={":root": "read"})
    )
    write = CodexPermissionProfile(
        name="write",
        filesystem=NativeFilesystemPermissions(rules={":project_roots": "write"}),
    )
    lower = CodexPermissions(profile="read", profiles=(read, write))
    merged = merge_settings(lower, CodexPermissions(profile="write"))
    assert merged.profiles == (read, write)
    assert merged.profile == "write"
    config = Config(
        harnesses=HarnessSettingsMap(codex=CodexSettings(permissions=lower))
    )
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(_binding("slow"),),
        harness_permissions=CodexPermissions(profile="write"),
    )
    resolved = resolve_native_settings(spec, config)
    assert resolved.harness.permissions == merged


def test_profile_definitions_reject_duplicates_and_inline_conflicts() -> None:
    profile = CodexPermissionProfile(name="demo")
    with pytest.raises(ValidationError):
        CodexPermissions(profiles=(profile, profile))
    with pytest.raises(ValidationError):
        CodexPermissions(
            profile=profile,
            profiles=(
                CodexPermissionProfile(
                    name="demo",
                    filesystem=NativeFilesystemPermissions(rules={":root": "read"}),
                ),
            ),
        )
    with pytest.raises(ModelValidationError, match="explicit definition"):
        resolve_native_settings(
            AgentSpec(
                harness=Codex(
                    model="fixture", permissions=CodexPermissions(profile="missing")
                ),
                servers=(_binding("slow"),),
            ),
            Config(),
        )


def test_global_alias_server_binding_and_session_settings_merge_by_presence() -> None:
    config = Config(
        mcp_timeouts=McpTimeouts(startup_seconds=60, request_seconds=40),
        servers={"slow": ServerSettings(timeouts=McpTimeouts(request_seconds=50))},
    )
    binding = ServerBinding(
        server=StdioServer(
            name="slow", command="fixture", timeouts=McpTimeouts(startup_seconds=70)
        ),
        timeouts=McpTimeouts(request_seconds=80),
    )
    resolved = resolve_native_settings(
        AgentSpec(harness=Codex(model="fixture"), servers=(binding,)), config
    )
    assert resolved.servers[0].server.timeouts == McpTimeouts(
        startup_seconds=70, request_seconds=80
    )
    assert (
        binding.server.timeouts.request_seconds
        is not resolved.servers[0].server.timeouts.request_seconds
    )


def test_late_profile_override_cannot_inherit_conflicting_legacy_options() -> None:
    spec = AgentSpec(
        harness=Codex(
            model="fixture", permissions=CodexPermissions(sandbox="read-only")
        ),
        servers=(_binding("slow"),),
        harness_permissions=CodexPermissions(
            profile=CodexPermissionProfile(name="demo")
        ),
    )
    with pytest.raises(ModelValidationError, match="effective native settings"):
        resolve_native_settings(spec, Config())


def test_claude_cannot_change_unspecified_servers_with_global_native_budget() -> None:
    spec = AgentSpec(
        harness=ClaudeCode(model="fixture"),
        servers=(
            _binding("one", timeouts=McpTimeouts(startup_seconds=60)),
            _binding("two"),
        ),
    )
    with pytest.raises(ModelValidationError, match="matching MCP budgets"):
        resolve_native_settings(spec, Config())
    resolved = resolve_native_settings(
        spec, Config(mcp_timeouts=McpTimeouts(startup_seconds=60))
    )
    assert all(
        binding.server.timeouts.startup_seconds == 60 for binding in resolved.servers
    )


def test_opencode_shared_native_timeout_rejects_distinct_phase_budgets() -> None:
    spec = AgentSpec(
        harness=OpenCode(model="fixture"),
        servers=(
            _binding(
                "slow", timeouts=McpTimeouts(startup_seconds=60, request_seconds=30)
            ),
        ),
    )
    with pytest.raises(ModelValidationError, match="one MCP budget"):
        resolve_native_settings(spec, Config())


def test_wrong_harness_override_rejected_before_adapter_resolution() -> None:
    spec = AgentSpec(
        harness=ClaudeCode(model="fixture"),
        servers=(_binding("slow"),),
        harness_permissions=CodexPermissions(),
    )
    with pytest.raises(ModelValidationError, match="does not match"):
        resolve_native_settings(spec, Config())


def test_native_literals_follow_effective_overrides_and_include_unused_definitions() -> (
    None
):
    selected = CodexPermissionProfile(
        name="active",
        filesystem=NativeFilesystemPermissions(rules={"/protected/path": "read"}),
        network=NativeNetworkPermissions(
            enabled=True, domains={"protected.example": "deny"}
        ),
    )
    unused = CodexPermissionProfile(
        name="unused",
        filesystem=NativeFilesystemPermissions(rules={"/unused/canary": "deny"}),
    )
    spec = AgentSpec(
        harness=Codex(
            model="fixture",
            permissions=CodexPermissions(profile="active", profiles=(selected, unused)),
        ),
        servers=(_binding("slow"),),
    )
    values = native_literal_inputs(resolve_native_settings(spec, Config()))
    assert set(values) == {
        "active",
        "unused",
        "/protected/path",
        "/unused/canary",
        "protected.example",
    }
    assert not {"read", "deny", "allow", "true"}.intersection(values)
    changed = spec.model_copy(
        update={
            "harness_permissions": CodexPermissions(
                profile=CodexPermissionProfile(
                    name="late",
                    filesystem=NativeFilesystemPermissions(
                        rules={"/late/canary": "write"}
                    ),
                ),
                profiles=(),
            )
        }
    )
    assert set(native_literal_inputs(resolve_native_settings(changed, Config()))) == {
        "late",
        "/late/canary",
    }


def test_native_literal_collector_does_not_register_builtin_selectors_or_modes() -> (
    None
):
    spec = AgentSpec(
        harness=Codex(
            model="fixture",
            permissions=CodexPermissions(profile=":read-only", approval_policy="never"),
        ),
        servers=(_binding("slow"),),
    )
    assert native_literal_inputs(resolve_native_settings(spec, Config())) == ()
