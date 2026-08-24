"""Pure SDK configuration resolution.

This module contains only the small, process-independent configuration
contract used by SDK callers.  Application settings (credentials, database,
and UI/runtime limits) intentionally remain in :mod:`mcp_pal.config`.
"""

from __future__ import annotations

import os as _os
import re as _re
import sys as _sys
from enum import Enum as _Enum
from pathlib import Path as _Path
from typing import Any as _Any, Literal as _Literal, Mapping as _Mapping

if _sys.version_info >= (3, 11):  # pragma: no cover - branch depends on runtime Python
    import tomllib as _tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover
    import tomli as _tomli

    _tomllib = _tomli

from pydantic import (
    Field as _Field,
    StrictBool as _StrictBool,
    StrictStr as _StrictStr,
    field_validator as _field_validator,
    model_validator as _model_validator,
)

from .types import FrozenModel as _FrozenModel, _FrozenMapping as _FrozenMapping


class ConfigurationError(ValueError):
    """A strict, value-free configuration diagnostic."""

    code = "configuration_error"

    def __init__(self, *, field: str, origin: str, reason: str, code: str | None = None) -> None:
        self.field = field
        self.origin = origin
        self.reason = reason
        if code is not None:
            self.code = code
        # Deliberately omit the rejected value.  Configuration values may be
        # credentials or tokens even when the field is not secret today.
        super().__init__(f"{self.code}: {field} from {origin}: {reason}")


class ConfigSource(str, _Enum):
    EXPLICIT = "explicit"
    ENVIRONMENT = "environment"
    PROJECT = "project"
    DEFAULT = "default"


class ConfigOrigin(_FrozenModel):
    source: ConfigSource
    origin: str = _Field(min_length=1, max_length=4096)


_FIELDS = ("artifact_policy", "protocol_revision", "telemetry_enabled")
_ENV_FIELDS = {
    "MCP_PAL_ARTIFACT_POLICY": "artifact_policy",
    "MCP_PAL_PROTOCOL_REVISION": "protocol_revision",
    "MCP_PAL_TELEMETRY_ENABLED": "telemetry_enabled",
}
_ARTIFACT_POLICIES = frozenset({"failed", "always", "never"})
_REVISION_PATTERN = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _default_origins() -> dict[str, ConfigOrigin]:
    return {field: ConfigOrigin(source=ConfigSource.DEFAULT, origin="default") for field in _FIELDS}


class SDKConfig(_FrozenModel):
    """Effective SDK-wide settings and the origin of each setting."""

    artifact_policy: _Literal["failed", "always", "never"] = "failed"
    protocol_revision: _StrictStr = "auto"
    telemetry_enabled: _StrictBool = False
    sources: _Mapping[str, ConfigOrigin] = _Field(default_factory=_default_origins)

    @_field_validator("protocol_revision")
    @classmethod
    def _valid_protocol_revision(cls, value: str) -> str:
        if not value or _REVISION_PATTERN.fullmatch(value) is None:
            raise ValueError("must be auto or a non-empty protocol revision identifier")
        return value

    @_model_validator(mode="after")
    def _validate_sources(self) -> "SDKConfig":
        if set(self.sources) != set(_FIELDS):
            raise ValueError("sources must identify every supported configuration field")
        if "sources" not in self.__pydantic_fields_set__:
            # Direct construction is an explicit source for supplied fields;
            # omitted fields retain the default origin.  The mapping is
            # frozen here because this validator runs after the base model's
            # recursive-freeze hook.
            inferred = {
                field: ConfigOrigin(
                    source=ConfigSource.EXPLICIT if field in self.__pydantic_fields_set__ else ConfigSource.DEFAULT,
                    origin=f"argument:{field}" if field in self.__pydantic_fields_set__ else "default",
                )
                for field in _FIELDS
            }
            object.__setattr__(self, "sources", _FrozenMapping(inferred))
        else:
            defaults = {"artifact_policy": "failed", "protocol_revision": "auto", "telemetry_enabled": False}
            for field in _FIELDS:
                source = self.sources[field]
                if source.source is ConfigSource.DEFAULT and getattr(self, field) != defaults[field]:
                    raise ValueError(f"{field} has a non-default value but default provenance")
        return self

    @property
    def provenance(self) -> _Mapping[str, ConfigOrigin]:
        """Compatibility name for callers that call origins provenance."""

        return self.sources

    def source_for(self, field: str) -> ConfigOrigin:
        if field not in _FIELDS:
            raise ConfigurationError(field=field, origin="runtime", reason="unknown setting", code="unknown_setting")
        return self.sources[field]


# Short descriptive aliases keep the contract discoverable without requiring
# application settings to be imported.
Configuration = SDKConfig
MCPConfig = SDKConfig


_UNSET = object()


def _error(field: str, origin: str, reason: str, *, code: str = "invalid_configuration") -> ConfigurationError:
    return ConfigurationError(field=field, origin=origin, reason=reason, code=code)


def _validate(field: str, value: _Any, origin: str, *, environment: bool = False) -> _Any:
    if field == "artifact_policy":
        if not isinstance(value, str) or value not in _ARTIFACT_POLICIES:
            raise _error(field, origin, "must be one of failed, always, or never")
        return value
    if field == "protocol_revision":
        if not isinstance(value, str) or not value or _REVISION_PATTERN.fullmatch(value) is None:
            raise _error(field, origin, "must be auto or a non-empty protocol revision identifier")
        return value
    if field == "telemetry_enabled":
        if environment:
            if not isinstance(value, str):
                raise _error(field, origin, "must be true or false")
            parsed = {"true": True, "false": False}
            if value.lower() not in parsed:
                raise _error(field, origin, "must be true or false")
            return parsed[value.lower()]
        if not isinstance(value, bool):
            raise _error(field, origin, "must be a boolean")
        return value
    raise _error(field, origin, "unknown setting", code="unknown_setting")


def _validate_mapping(values: _Mapping[str, _Any], origin: str, *, environment: bool = False) -> dict[str, _Any]:
    unknown = sorted(set(values) - set(_FIELDS))
    if unknown:
        raise _error(", ".join(unknown), origin, "unknown setting", code="unknown_setting")
    return {
        field: _validate(
            field,
            values[field],
            f"argument:{field}" if origin == "explicit" else origin,
            environment=environment,
        )
        for field in values
    }


def _find_pyproject(start: str | _Path | None) -> _Path | None:
    location = _Path(start) if start is not None else _Path.cwd()
    location = location if location.is_dir() else location.parent
    for directory in (location, *location.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _project_values(start: str | _Path | None) -> tuple[dict[str, _Any], str]:
    path = _find_pyproject(start)
    if path is None:
        return {}, "project:none"
    origin = f"pyproject:{path}"
    try:
        document = _tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise _error("project", origin, "invalid TOML", code="invalid_project") from None
    tool = document.get("tool", {})
    if not isinstance(tool, dict):
        raise _error("project", origin, "tool table must be a table", code="invalid_project")
    values = tool.get("mcp-pal", {})
    if not isinstance(values, dict):
        raise _error("project", origin, "[tool.mcp-pal] must be a table", code="invalid_project")
    return _validate_mapping(values, origin), origin


def _environment_values(environment: _Mapping[str, str]) -> dict[str, _Any]:
    unknown = sorted(key for key in environment if key.startswith("MCP_PAL_") and key not in _ENV_FIELDS)
    if unknown:
        raise _error(", ".join(unknown), "environment", "unknown setting", code="unknown_setting")
    values: dict[str, _Any] = {}
    for name, field in _ENV_FIELDS.items():
        if name in environment:
            values[field] = _validate(field, environment[name], f"env:{name}", environment=True)
    return values


def resolve_config(
    explicit: _Mapping[str, _Any] | None = None,
    *,
    env: _Mapping[str, str] | None = None,
    cwd: str | _Path | None = None,
    artifact_policy: _Any = _UNSET,
    protocol_revision: _Any = _UNSET,
    telemetry_enabled: _Any = _UNSET,
) -> SDKConfig:
    """Resolve SDK settings using explicit, environment, project, default order.

    ``env`` is injectable for deterministic callers and tests; omitted means
    the ambient process environment.  Project discovery reads only the
    nearest parent ``pyproject.toml`` and never reads ``.env`` files.
    """

    provided = dict(explicit or {})
    keyword_values = {
        field: value
        for field, value in {
            "artifact_policy": artifact_policy,
            "protocol_revision": protocol_revision,
            "telemetry_enabled": telemetry_enabled,
        }.items()
        if value is not _UNSET
    }
    duplicates = sorted(set(provided) & set(keyword_values))
    if duplicates:
        raise _error(", ".join(duplicates), "explicit", "setting supplied more than once", code="duplicate_setting")
    provided.update(keyword_values)
    explicit_values = _validate_mapping(provided, "explicit")
    environment_values = _environment_values(env if env is not None else _os.environ)
    project_values, project_origin = _project_values(cwd)

    values: dict[str, _Any] = {"artifact_policy": "failed", "protocol_revision": "auto", "telemetry_enabled": False}
    origins: dict[str, ConfigOrigin] = {
        field: ConfigOrigin(source=ConfigSource.DEFAULT, origin="default") for field in _FIELDS
    }
    for field, source_values, source, origin in (
        ("artifact_policy", project_values, ConfigSource.PROJECT, project_origin),
        ("protocol_revision", project_values, ConfigSource.PROJECT, project_origin),
        ("telemetry_enabled", project_values, ConfigSource.PROJECT, project_origin),
    ):
        if field in source_values:
            values[field] = source_values[field]
            origins[field] = ConfigOrigin(source=source, origin=origin)
    for field, value in environment_values.items():
        values[field] = value
        origins[field] = ConfigOrigin(source=ConfigSource.ENVIRONMENT, origin=f"env:MCP_PAL_{field.upper()}")
    for field, value in explicit_values.items():
        values[field] = value
        origins[field] = ConfigOrigin(source=ConfigSource.EXPLICIT, origin=f"argument:{field}")
    return SDKConfig(**values, sources=origins)


load_config = resolve_config


__all__ = [
    "ConfigOrigin",
    "ConfigSource",
    "Configuration",
    "ConfigurationError",
    "MCPConfig",
    "SDKConfig",
    "load_config",
    "resolve_config",
]
