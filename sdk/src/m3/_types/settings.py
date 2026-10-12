"""Sparse configuration values; construction does not configure a native harness."""

from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictStr,
    ValidationInfo,
    model_serializer,
    model_validator,
)

from .base import FrozenModel


class Unset(Enum):
    """An omitted native setting, distinct from an explicit disabled budget."""

    UNSET = "m3.unset"


UNSET = Unset.UNSET
_Unset = Annotated[Unset, Field(strict=True)]
_Seconds = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
_Count = Annotated[int, Field(strict=True, gt=0)]
_Name = Annotated[StrictStr, Field(min_length=1, max_length=4096)]


class _Settings(FrozenModel):
    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        values: dict[str, Any] = handler(self)
        return {
            name: value
            for name, value in values.items()
            if name in self.model_fields_set and getattr(self, name) is not UNSET
        }


class HarnessTimeouts(_Settings):
    """Native phase budgets in seconds; omitted phases retain native defaults.

    session_seconds bounds native session creation, not session lifetime or
    individual turns. request_seconds bounds a native startup protocol request.
    total_seconds optionally bounds the complete startup.
    """

    startup_seconds: _Seconds | _Unset = UNSET
    initialize_seconds: _Seconds | _Unset = UNSET
    readiness_seconds: _Seconds | _Unset = UNSET
    request_seconds: _Seconds | _Unset = UNSET
    session_seconds: _Seconds | _Unset = UNSET
    total_seconds: _Seconds | _Unset | None = UNSET


class McpTimeouts(_Settings):
    """Configuration values for MCP startup and request budgets in seconds."""

    startup_seconds: _Seconds | _Unset = UNSET
    request_seconds: _Seconds | _Unset = UNSET


class NativeFilesystemPermissions(_Settings):
    """Native profile path rules, separate from interactive approval policy."""

    rules: Mapping[_Name, Literal["read", "write", "deny"]] = Field(
        default_factory=dict
    )


class NativeNetworkPermissions(_Settings):
    """Native Codex profile network switch and domain decisions."""

    enabled: StrictBool | _Unset = UNSET
    domains: Mapping[_Name, Literal["allow", "deny"]] = Field(default_factory=dict)


class CodexPermissionProfile(_Settings):
    """A named profile definition for native Codex configuration."""

    name: _Name
    filesystem: NativeFilesystemPermissions = Field(
        default_factory=NativeFilesystemPermissions
    )
    network: NativeNetworkPermissions = Field(default_factory=NativeNetworkPermissions)

    @model_validator(mode="after")
    def _valid_name(self) -> "CodexPermissionProfile":
        if self.name.startswith(":") or self.name == "filesystem":
            raise ValueError("defined profile name is reserved")
        return self


class CodexPermissions(_Settings):
    """Choose a named/defined profile or legacy sandbox options, never both."""

    profile: _Name | CodexPermissionProfile | _Unset = UNSET
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | _Unset = (
        UNSET
    )
    network_access: StrictBool | _Unset = UNSET
    approval_policy: (
        Literal["untrusted", "on-failure", "on-request", "never"] | _Unset
    ) = UNSET

    @model_validator(mode="after")
    def _profile_excludes_legacy(self) -> "CodexPermissions":
        if self.profile is not UNSET and (
            self.sandbox is not UNSET or self.network_access is not UNSET
        ):
            raise ValueError("profile cannot be combined with legacy sandbox options")
        return self


class ClaudeCodePermissions(_Settings):
    """Claude Code interaction mode and explicit native tool rules."""

    permission_mode: (
        Literal["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]
        | _Unset
    ) = UNSET
    allowed_tools: tuple[_Name, ...] | _Unset = UNSET
    denied_tools: tuple[_Name, ...] | _Unset = UNSET


class OpenCodePermissions(_Settings):
    """OpenCode native tool/pattern rules; this is not an OS filesystem sandbox."""

    rules: Mapping[_Name, Literal["allow", "ask", "deny"]] = Field(default_factory=dict)


class CachePolicy(_Settings):
    """Isolated caches by default; immutable seeds require runtime support."""

    mode: Literal["isolated", "immutable-seed"] = "isolated"
    root: _Name | _Unset = UNSET

    @model_validator(mode="after")
    def _root_requires_seed(self, info: ValidationInfo) -> "CachePolicy":
        effective = isinstance(info.context, dict) and info.context.get(
            "m3_effective_settings"
        )
        if (
            self.mode == "isolated"
            and self.root is not UNSET
            and ("mode" in self.model_fields_set or effective)
        ):
            raise ValueError("cache root requires immutable-seed mode")
        return self


class StartupAdmission(_Settings):
    """Optional startup admission, disabled until explicitly enabled."""

    enabled: StrictBool = False
    slots: _Count = 4
    queue_timeout_seconds: _Seconds = 300
    normalized_load_threshold: _Seconds | _Unset | None = UNSET


class CliSettings(_Settings):
    """Project Python probe budget in seconds."""

    project_python_timeout_seconds: _Seconds | _Unset = UNSET


class ResultsSettings(_Settings):
    """SQLite writer wait budget in seconds."""

    busy_timeout_seconds: _Seconds | _Unset = UNSET


class WorkerBootstrapSettings(_Settings):
    """One total private worker bootstrap budget, not a per-frame timeout."""

    timeout_seconds: _Seconds = 30


class ServerSettings(_Settings):
    """Settings for an existing server alias; this never defines a server."""

    timeouts: McpTimeouts = Field(default_factory=McpTimeouts)


class HarnessSettings(_Settings):
    """Settings common to native harness selections."""

    timeouts: HarnessTimeouts = Field(default_factory=HarnessTimeouts)
    cache: CachePolicy = Field(default_factory=CachePolicy)
    admission: StartupAdmission = Field(default_factory=StartupAdmission)


class CodexSettings(HarnessSettings):
    permissions: CodexPermissions = Field(default_factory=CodexPermissions)


class ClaudeCodeSettings(HarnessSettings):
    permissions: ClaudeCodePermissions = Field(default_factory=ClaudeCodePermissions)


class OpenCodeSettings(HarnessSettings):
    permissions: OpenCodePermissions = Field(default_factory=OpenCodePermissions)


class HarnessSettingsMap(_Settings):
    codex: CodexSettings = Field(default_factory=CodexSettings)
    claude_code: ClaudeCodeSettings = Field(default_factory=ClaudeCodeSettings)
    opencode: OpenCodeSettings = Field(default_factory=OpenCodeSettings)
