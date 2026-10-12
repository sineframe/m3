"""Validate requested native settings against offline runtime support facts."""

from decimal import Decimal

from .._types.specs import AgentSpec
from ..errors import ModelValidationError, UnsupportedFeature
from ..server_group import HarnessServerConfig
from ..types import (
    UNSET,
    ClaudeCode,
    Codex,
    CodexPermissionProfile,
    CodexPermissions,
    NativeToolPolicy,
    OpenCode,
)
from .native_capabilities import (
    NativeRuntimeIdentity,
    NativeSupport,
    discover_native_support,
)

_SPECIAL_PATHS = {
    ":root",
    ":minimal",
    ":project_roots",
    ":workspace_roots",
    ":tmpdir",
    ":slash_tmp",
}
_BUILTIN_PROFILES = {":read-only", ":workspace", ":danger-full-access"}


def milliseconds(value: float, *, minimum: int = 1) -> int:
    """Reject budgets the native integer timer would round, clamp, or ignore."""
    converted = Decimal(str(value)) * 1000
    if (
        converted != converted.to_integral_value()
        or not minimum <= converted <= 2_147_483_647
    ):
        raise ModelValidationError(
            "MCP budget is outside the native millisecond timer range"
        )
    return int(converted)


def selected_profile(permissions: CodexPermissions) -> str | None:
    profile = permissions.profile
    return (
        profile.name
        if isinstance(profile, CodexPermissionProfile)
        else profile
        if isinstance(profile, str)
        else None
    )


def profile_definitions(
    permissions: CodexPermissions,
) -> tuple[CodexPermissionProfile, ...]:
    definitions = permissions.profiles
    if isinstance(permissions.profile, CodexPermissionProfile) and not any(
        value.name == permissions.profile.name for value in definitions
    ):
        return (*definitions, permissions.profile)
    return definitions


def _codex_permissions(harness: Codex, support: NativeSupport) -> None:
    permissions = harness.permissions
    profile = selected_profile(permissions)
    if (
        profile is not None
        or permissions.profiles
        or permissions.network_access is not UNSET
    ):
        if support.source_revision is None:
            raise UnsupportedFeature(
                "native Codex configuration support is unknown for this release"
            )
    if profile is not None:
        if (
            "permissions" not in support.rpc_parameters
            or not support.reports_active_profile
        ):
            raise UnsupportedFeature(
                "native Codex cannot select and confirm a permission profile"
            )
        if profile.startswith(":") and profile not in _BUILTIN_PROFILES:
            raise UnsupportedFeature("native Codex permission profile is not supported")
    for definition in profile_definitions(permissions):
        for path, access in definition.filesystem.rules.items():
            if path.startswith(":") and path not in _SPECIAL_PATHS:
                raise UnsupportedFeature(
                    "native Codex filesystem special path is not supported"
                )
            exact = path.removesuffix("/**")
            if access != "deny" and any(char in exact for char in "*?["):
                raise UnsupportedFeature(
                    "native Codex read/write rules require an exact path or trailing subtree"
                )
            if (
                access == "deny"
                and "**" in path
                and not support.platform.startswith("darwin-")
            ):
                raise UnsupportedFeature(
                    "native Codex unbounded deny globs are not supported on this platform"
                )
    if (
        permissions.sandbox is not UNSET
        and permissions.sandbox not in support.sandboxes
    ):
        raise UnsupportedFeature("native Codex sandbox mode is not supported")
    if (
        permissions.approval_policy is not UNSET
        and permissions.approval_policy not in support.approval_policies
    ):
        raise UnsupportedFeature("native Codex approval policy is not supported")
    if (
        permissions.network_access is not UNSET
        and permissions.sandbox != "workspace-write"
    ):
        raise ModelValidationError(
            "legacy Codex network access requires an explicit workspace-write sandbox"
        )


def validate_support(
    spec: AgentSpec,
    configurations: tuple[HarnessServerConfig, ...],
    support: NativeSupport,
) -> None:
    """Validate each final request; cached facts never cache this decision."""
    harness = spec.harness
    if isinstance(harness, Codex):
        _codex_permissions(harness, support)
    elif isinstance(harness, ClaudeCode):
        permissions = harness.permissions
        if (
            permissions.permission_mode is not UNSET
            and permissions.permission_mode not in support.permission_modes
        ):
            raise UnsupportedFeature(
                "native Claude Code permission mode is not supported"
            )
        if (
            permissions.allowed_tools is not UNSET
            and "allowed_tools" not in support.rpc_parameters
        ):
            raise UnsupportedFeature(
                "native Claude Code tool allowlist is not supported"
            )
        if (
            permissions.denied_tools is not UNSET
            and "denied_tools" not in support.rpc_parameters
        ):
            raise UnsupportedFeature(
                "native Claude Code tool denylist is not supported"
            )
        if isinstance(spec.tool_policy, NativeToolPolicy) and permissions.model_dump():
            mode = spec.tool_policy.policy.get("mode")
            if mode != "full" and permissions.allowed_tools is not UNSET:
                server = spec.tool_policy.policy.get("server")
                read_only = spec.tool_policy.policy.get("read_only_tools") or ()
                allowed = {
                    f"mcp__{server}__*",
                    *(read_only if mode == "mcp_read_only" else ()),
                }
                if any(tool not in allowed for tool in permissions.allowed_tools):
                    raise ModelValidationError(
                        "Claude native allowlist would widen the selected tool policy"
                    )
    elif isinstance(harness, OpenCode):
        if support.source_revision is None or support.dialect != "legacy":
            raise UnsupportedFeature(
                "native OpenCode configuration support is unknown for this release"
            )
        if harness.dialect not in ("auto", support.dialect):
            raise UnsupportedFeature(
                "native OpenCode configuration dialect does not match the runtime"
            )
        if (
            isinstance(spec.tool_policy, NativeToolPolicy)
            and spec.tool_policy.policy.get("mode") != "full"
        ):
            policy = spec.tool_policy.policy
            read_only = policy.get("read_only_tools") or ()
            allowed = {
                f"{policy.get('server')}_*",
                *(read_only if policy.get("mode") == "mcp_read_only" else ()),
            }
            if any(
                action != "deny" and tool not in allowed
                for tool, action in harness.permissions.rules.items()
            ):
                raise ModelValidationError(
                    "OpenCode native rules would widen the selected tool policy"
                )
    else:
        raise UnsupportedFeature("selected harness does not support native settings")
    startups = {config.timeouts.startup_seconds for config in configurations}
    if isinstance(harness, ClaudeCode) and len(startups) > 1:
        raise ModelValidationError(
            "Claude Code requires matching MCP startup budgets for every bound server"
        )
    for config in configurations:
        budgets = config.timeouts
        if not budgets.model_dump():
            continue
        if support.source_revision is None:
            raise UnsupportedFeature(
                "native MCP timeout support is unknown for this release"
            )
        if (
            isinstance(harness, OpenCode)
            and budgets.startup_seconds is not UNSET
            and budgets.request_seconds is not UNSET
            and budgets.startup_seconds != budgets.request_seconds
        ):
            raise ModelValidationError(
                "OpenCode uses one MCP budget for startup and requests"
            )
        for name in ("startup_seconds", "request_seconds"):
            value = getattr(budgets, name)
            if value is UNSET:
                continue
            if isinstance(harness, Codex):
                if value >= 2**64:
                    raise ModelValidationError(
                        "MCP budget is outside the native duration range"
                    )
            else:
                milliseconds(
                    value,
                    minimum=1000
                    if isinstance(harness, ClaudeCode) and name == "request_seconds"
                    else 1,
                )


async def validate_native_settings(
    executable: str,
    spec: AgentSpec,
    configurations: tuple[HarnessServerConfig, ...],
    runtime_identity: NativeRuntimeIdentity | None = None,
    *,
    deadline: float | None = None,
) -> NativeSupport:
    harness = spec.harness
    assert isinstance(harness, (Codex, ClaudeCode, OpenCode))
    if deadline is not None:
        from .native_capabilities import discover_native_support_owned

        support = await discover_native_support_owned(
            executable, harness.kind, runtime_identity, deadline=deadline
        )
    else:
        support = await discover_native_support(
            executable, harness.kind, runtime_identity
        )
    validate_support(spec, configurations, support)
    return support
