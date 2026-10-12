"""Resolve explicit configuration layers before a native session can start."""

from pydantic import ValidationError

from ._types.specs import AgentSpec
from .configuration import Config, merge_settings
from .errors import ModelValidationError, UnsupportedFeature
from .types import (
    UNSET,
    ClaudeCode,
    ClaudeCodePermissions,
    Codex,
    CodexPermissionProfile,
    CodexPermissions,
    HarnessTimeouts,
    McpTimeouts,
    OpenCode,
    OpenCodePermissions,
    ServerBinding,
    Unset,
)

NativePermissions = CodexPermissions | ClaudeCodePermissions | OpenCodePermissions


def timeout_override(spec: AgentSpec, timeouts: HarnessTimeouts | Unset) -> AgentSpec:
    if timeouts is UNSET:
        return spec
    if not isinstance(timeouts, HarnessTimeouts):
        raise ModelValidationError("harness timeout override must be typed")
    return spec.model_copy(
        update={"harness_timeouts": merge_settings(spec.harness_timeouts, timeouts)}
    )


def permission_override(
    spec: AgentSpec, permissions: NativePermissions | Unset
) -> AgentSpec:
    """Apply a session override without serializing unrelated runtime values."""
    if permissions is UNSET:
        return spec
    if not isinstance(
        permissions, (CodexPermissions, ClaudeCodePermissions, OpenCodePermissions)
    ):
        raise ModelValidationError("harness permissions require a typed override")
    return spec.model_copy(update={"harness_permissions": permissions})


def _server_settings(config: Config, binding: ServerBinding) -> ServerBinding:
    server = binding.server
    if server is None:
        raise ModelValidationError("native settings require a resolved server binding")
    alias_settings = config.servers.get(binding.alias or server.name)
    layers = [config.mcp_timeouts]
    if alias_settings is not None:
        layers.append(alias_settings.timeouts)
    layers.extend((server.timeouts, binding.timeouts))
    timeouts = merge_settings(*layers)
    return binding.model_copy(
        update={
            "server": server.model_copy(update={"timeouts": timeouts}),
            "timeouts": McpTimeouts(),
        }
    )


def _validate_codex(permissions: CodexPermissions) -> None:
    profile = permissions.profile
    if isinstance(profile, str) and not profile.startswith(":"):
        if not any(definition.name == profile for definition in permissions.profiles):
            raise ModelValidationError(
                "selected Codex profile requires an explicit definition"
            )


def _validate_timeouts(spec: AgentSpec) -> None:
    timeouts = tuple(
        binding.server.timeouts
        for binding in spec.servers
        if binding.server is not None
    )
    if isinstance(spec.harness, ClaudeCode):
        # Claude has one process-wide budget for each phase. Applying one
        # server's budget to an otherwise unspecified server changes its default.
        for name in ("startup_seconds",):
            values = {getattr(value, name) for value in timeouts}
            if len(values) > 1:
                raise ModelValidationError(
                    "Claude Code requires matching MCP budgets for every bound server"
                )
    elif isinstance(spec.harness, OpenCode):
        for value in timeouts:
            if (
                value.startup_seconds is not UNSET
                and value.request_seconds is not UNSET
                and value.startup_seconds != value.request_seconds
            ):
                raise ModelValidationError(
                    "OpenCode uses one MCP budget for startup and requests"
                )
    elif not isinstance(spec.harness, Codex) and any(
        value.model_dump() for value in timeouts
    ):
        raise UnsupportedFeature(
            "selected harness does not support native MCP settings"
        )


def resolve_native_settings(
    spec: AgentSpec,
    config: Config,
    *,
    kit_timeouts: HarnessTimeouts | None = None,
) -> AgentSpec:
    """Merge file, harness, server, binding, and session explicit leaves.

    This validates structure only. The resolved executable's capabilities are
    checked separately before MCP startup.
    """
    try:
        servers = tuple(_server_settings(config, binding) for binding in spec.servers)
        harness = spec.harness
        lower: NativePermissions
        if isinstance(harness, Codex):
            lower = config.harnesses.codex.permissions
            lower_timeouts = config.harnesses.codex.timeouts
        elif isinstance(harness, ClaudeCode):
            lower = config.harnesses.claude_code.permissions
            lower_timeouts = config.harnesses.claude_code.timeouts
        elif isinstance(harness, OpenCode):
            lower = config.harnesses.opencode.permissions
            lower_timeouts = config.harnesses.opencode.timeouts
        else:
            if spec.harness_permissions is not None:
                raise UnsupportedFeature(
                    "selected harness does not support native permissions"
                )
            result = spec.model_copy(update={"servers": servers})
            _validate_timeouts(result)
            return result
        layers = [lower, harness.permissions]
        if spec.harness_permissions is not None:
            if type(spec.harness_permissions) is not type(lower):
                raise ModelValidationError(
                    "permission override does not match the selected harness"
                )
            layers.append(spec.harness_permissions)
        permissions = merge_settings(*layers)
        if isinstance(permissions, CodexPermissions):
            _validate_codex(permissions)
        timeouts = merge_settings(
            lower_timeouts,
            kit_timeouts or HarnessTimeouts(),
            harness.timeouts,
            spec.harness_timeouts,
        )
        harness = harness.model_copy(
            update={"permissions": permissions, "timeouts": timeouts}
        )
        result = spec.model_copy(update={"harness": harness, "servers": servers})
        _validate_timeouts(result)
        return result
    except ValidationError:
        raise ModelValidationError("effective native settings are invalid") from None


def resolve_runtime_settings(
    bindings: tuple[ServerBinding, ...], config: Config
) -> tuple[ServerBinding, ...]:
    """Apply the same server layers to runtime-only in-process registrations."""
    try:
        return tuple(_server_settings(config, binding) for binding in bindings)
    except ValidationError:
        raise ModelValidationError("effective native settings are invalid") from None


def validate_bound_settings(
    spec: AgentSpec, bindings: tuple[ServerBinding, ...]
) -> None:
    """Validate process-wide constraints including runtime-only registrations."""
    _validate_timeouts(spec.model_copy(update={"servers": bindings}))


def requested_native_settings(spec: AgentSpec) -> bool:
    """Default sessions require no additional native capability discovery."""
    harness = spec.harness
    return bool(
        (
            isinstance(harness, (Codex, ClaudeCode, OpenCode))
            and harness.permissions.model_dump()
        )
        or any(
            binding.server is not None and binding.server.timeouts.model_dump()
            for binding in spec.servers
        )
    )


def native_literal_inputs(spec: AgentSpec) -> tuple[str, ...]:
    """Return exactly the explicit freeform strings emitted in native settings.

    Profile definitions may be parsed even when they are not selected, so their
    inputs are included. Framework permission decisions and omitted defaults
    are never treated as credentials.
    """
    values: set[str] = set()
    harness = spec.harness
    if isinstance(harness, Codex):
        permissions = harness.permissions
        definitions = permissions.profiles
        if isinstance(permissions.profile, CodexPermissionProfile):
            definitions = (*definitions, permissions.profile)
        elif isinstance(
            permissions.profile, str
        ) and not permissions.profile.startswith(":"):
            values.add(permissions.profile)
        for definition in definitions:
            values.add(definition.name)
            values.update(definition.filesystem.rules)
            values.update(definition.network.domains)
    elif isinstance(harness, ClaudeCode):
        for tools in (
            harness.permissions.allowed_tools,
            harness.permissions.denied_tools,
        ):
            if tools is not UNSET:
                values.update(tools)
    elif isinstance(harness, OpenCode):
        values.update(harness.permissions.rules)
    return tuple(sorted(values))
