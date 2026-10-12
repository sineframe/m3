"""Pure SDK configuration resolution.

This module contains only the small, process-independent configuration
contract used by SDK callers.  Application settings (credentials, database,
and UI/runtime limits) intentionally remain in :mod:`m3_app.settings`.
"""

from __future__ import annotations

import os as _os
import re as _re
import sys as _sys
import typing as _typing
from collections.abc import Mapping as _Mapping
from enum import Enum as _Enum
from pathlib import Path as _Path
from typing import Any as _Any
from typing import Literal as _Literal

if _typing.TYPE_CHECKING:
    import tomli as _tomllib
elif _sys.version_info >= (
    3,
    11,
):  # pragma: no cover - branch depends on runtime Python
    import tomllib as _tomllib
else:  # pragma: no cover
    import tomli as _tomllib

from pydantic import (
    Field as _Field,
)
from pydantic import (
    SerializerFunctionWrapHandler as _SerializerFunctionWrapHandler,
)
from pydantic import (
    StrictStr as _StrictStr,
)
from pydantic import (
    ValidationError as _ValidationError,
)
from pydantic import (
    ValidationInfo as _ValidationInfo,
)
from pydantic import (
    field_validator as _field_validator,
)
from pydantic import (
    model_serializer as _model_serializer,
)
from pydantic import (
    model_validator as _model_validator,
)

from ._types.settings import (
    UNSET as _NATIVE_UNSET,
)
from ._types.settings import (
    CachePolicy as _CachePolicy,
)
from ._types.settings import (
    CliSettings as _CliSettings,
)
from ._types.settings import (
    HarnessSettingsMap as _HarnessSettingsMap,
)
from ._types.settings import (
    HarnessTimeouts as _HarnessTimeouts,
)
from ._types.settings import (
    McpTimeouts as _McpTimeouts,
)
from ._types.settings import (
    ResultsSettings as _ResultsSettings,
)
from ._types.settings import (
    ServerSettings as _ServerSettings,
)
from ._types.settings import (
    StartupAdmission as _StartupAdmission,
)
from ._types.settings import (
    WorkerBootstrapSettings as _WorkerBootstrapSettings,
)
from ._types.settings import (
    _Settings,
)
from .types import FrozenModel as _FrozenModel
from .types import _FrozenMapping as _FrozenMapping


class ConfigError(ValueError):
    """A strict, value-free configuration diagnostic."""

    code = "configuration_error"

    def __init__(
        self, *, field: str, origin: str, reason: str, code: str | None = None
    ) -> None:
        self.field = field
        self.origin = origin
        self.reason = reason
        if code is not None:
            self.code = code
        # Deliberately omit the rejected value.  Config values may be
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


_FIELDS = ("artifact_policy", "protocol_revision")
_ENV_FIELDS = {
    "M3_ARTIFACT_POLICY": "artifact_policy",
    "M3_PROTOCOL_REVISION": "protocol_revision",
}
_ARTIFACT_POLICIES = frozenset({"failed", "always", "never"})
_REVISION_PATTERN = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


_SettingsValue = _typing.TypeVar("_SettingsValue", bound=_Settings)


def _explicit_values(value: _Any) -> _Any:
    if isinstance(value, _FrozenModel):
        result = {}
        for name in value.model_fields_set:
            item = getattr(value, name)
            if name == "sources" or item is _NATIVE_UNSET:
                continue
            explicit = _explicit_values(item)
            if isinstance(item, _FrozenModel) and not explicit:
                continue
            result[name] = explicit
        return result
    if isinstance(value, _Mapping):
        return {key: _explicit_values(item) for key, item in value.items()}
    return value


def _overlay(base: dict[str, _Any], override: _Mapping[str, _Any]) -> dict[str, _Any]:
    result = dict(base)
    for name, value in override.items():
        if value is _NATIVE_UNSET:
            continue
        if (
            value
            and isinstance(value, _Mapping)
            and isinstance(result.get(name), _Mapping)
        ):
            result[name] = _overlay(dict(result[name]), value)
        else:
            result[name] = value
    return result


def merge_settings(*layers: _SettingsValue) -> _SettingsValue:
    """Merge explicit leaves of one settings type, then validate the result.

    Omitted fields and UNSET inherit. Explicit None disables only optional
    budgets. Layers must be the same concrete type; precedence runs left to right.
    """
    if not layers:
        raise ValueError("at least one settings layer is required")
    model = type(layers[0])
    values: dict[str, _Any] = {}
    for layer in layers:
        if type(layer) is not model:
            raise TypeError("settings layers must have the same concrete type")
        values = _overlay(values, _explicit_values(layer))
    return model.model_validate(values, context={"m3_effective_settings": True})


def _leaves(value: _Any, prefix: str = "") -> dict[str, _Any]:
    entries: _Mapping[str, _Any]
    if isinstance(value, _FrozenModel):
        entries = {
            name: getattr(value, name)
            for name in type(value).model_fields
            if name != "sources"
        }
    elif isinstance(value, _Mapping):
        entries = value
    else:
        return {prefix: value}
    if not entries:
        return {prefix: {}} if prefix else {}
    result: dict[str, _Any] = {}
    for name, item in entries.items():
        escaped_name = name.replace("\\", "\\\\").replace(".", "\\.")
        path = f"{prefix}.{escaped_name}" if prefix else escaped_name
        result.update(_leaves(item, path))
    return result


class Config(_FrozenModel):
    """SDK settings, native configuration values, and leaf origins."""

    artifact_policy: _Literal["failed", "always", "never"] = "failed"
    protocol_revision: _StrictStr = "auto"
    harness_timeouts: _HarnessTimeouts = _Field(default_factory=_HarnessTimeouts)
    mcp_timeouts: _McpTimeouts = _Field(default_factory=_McpTimeouts)
    harnesses: _HarnessSettingsMap = _Field(default_factory=_HarnessSettingsMap)
    servers: _Mapping[_StrictStr, _ServerSettings] = _Field(default_factory=dict)
    cli: _CliSettings = _Field(default_factory=_CliSettings)
    results: _ResultsSettings = _Field(default_factory=_ResultsSettings)
    worker_bootstrap: _WorkerBootstrapSettings = _Field(
        default_factory=_WorkerBootstrapSettings
    )
    cache: _CachePolicy = _Field(default_factory=_CachePolicy)
    admission: _StartupAdmission = _Field(default_factory=_StartupAdmission)
    sources: _Mapping[str, ConfigOrigin] = _Field(default_factory=dict)

    @_model_serializer(mode="wrap")
    def _sparse_native_export(
        self, handler: _SerializerFunctionWrapHandler
    ) -> dict[str, _Any]:
        values: dict[str, _Any] = handler(self)
        for name in type(self).model_fields:
            if name not in (*_FIELDS, "sources") and name not in self.model_fields_set:
                values.pop(name, None)
        return values

    @_field_validator("protocol_revision")
    @classmethod
    def _valid_protocol_revision(cls, value: str) -> str:
        if not value or _REVISION_PATTERN.fullmatch(value) is None:
            raise ValueError("must be auto or a non-empty protocol revision identifier")
        return value

    @_field_validator("servers")
    @classmethod
    def _valid_aliases(
        cls, values: _Mapping[str, _ServerSettings]
    ) -> _Mapping[str, _ServerSettings]:
        if any(
            not name or len(name) > 128 or any(c in name for c in "\r\n\0")
            for name in values
        ):
            raise ValueError("server aliases must be non-empty single-line identifiers")
        return values

    @_model_validator(mode="after")
    def _validate_effective_settings(self, info: _ValidationInfo) -> Config:
        if isinstance(info.context, dict) and info.context.get("m3_partial_layer"):
            return self
        merge_settings(self.cache)
        harness_caches = (
            self.harnesses.codex.cache,
            self.harnesses.claude_code.cache,
            self.harnesses.opencode.cache,
        )
        for cache in harness_caches:
            merge_settings(self.cache, cache)
        return self

    @_model_validator(mode="after")
    def _validate_sources(self) -> Config:
        leaves = _leaves(self)
        explicit = _leaves(_explicit_values(self))
        if "sources" not in self.model_fields_set:
            inferred = {
                path: ConfigOrigin(
                    source=ConfigSource.EXPLICIT
                    if path in explicit
                    else ConfigSource.DEFAULT,
                    origin=f"argument:{path}" if path in explicit else "default",
                )
                for path in leaves
            }
            object.__setattr__(self, "sources", _FrozenMapping(inferred))
        else:
            if set(self.sources) != set(leaves):
                raise ValueError("sources must identify every configuration leaf")
            defaults = _leaves(
                type(self).model_construct(
                    servers={alias: _ServerSettings() for alias in self.servers}
                )
            )
            for path, source in self.sources.items():
                if source.source is ConfigSource.DEFAULT and (
                    path not in defaults or leaves[path] != defaults[path]
                ):
                    raise ValueError("non-default setting has default provenance")
        return self

    @property
    def provenance(self) -> _Mapping[str, ConfigOrigin]:
        """Origins for all supported leaves, including omitted native settings."""
        return self.sources

    def source_for(self, field: str) -> ConfigOrigin:
        if field not in self.sources:
            raise ConfigError(
                field=field,
                origin="runtime",
                reason="unknown setting",
                code="unknown_setting",
            )
        return self.sources[field]

    def redacted_projection(self) -> dict[str, _Any]:
        """Legacy SDK diagnostics plus a value-free native settings projection.

        This is suitable for logging. model_dump is a full configuration export
        and must not be used as a redacted diagnostic projection.
        """
        legacy = self.model_dump(mode="json", include=set(_FIELDS))
        legacy["sources"] = {
            field: self.sources[field].model_dump(mode="json") for field in _FIELDS
        }
        entries = []
        for path, origin in self.sources.items():
            if path in _FIELDS or origin.source is ConfigSource.DEFAULT:
                continue
            parts = path.split(".")
            if parts[0] == "servers" and len(parts) > 1:
                parts = ["servers", "<alias>", "timeouts", parts[-1]]
            elif "permissions" in parts:
                parts = parts[: parts.index("permissions") + 1]
            entries.append({"field": ".".join(parts), "source": origin.source.value})
        if entries:
            legacy["native_settings"] = entries
        return legacy


_UNSET = object()


def _error(
    field: str, origin: str, reason: str, *, code: str = "invalid_configuration"
) -> ConfigError:
    return ConfigError(field=field, origin=origin, reason=reason, code=code)


def _validate(field: str, value: _Any, origin: str) -> _Any:
    if field == "artifact_policy":
        if not isinstance(value, str) or value not in _ARTIFACT_POLICIES:
            raise _error(field, origin, "must be one of failed, always, or never")
        return value
    if field == "protocol_revision":
        if (
            not isinstance(value, str)
            or not value
            or _REVISION_PATTERN.fullmatch(value) is None
        ):
            raise _error(
                field,
                origin,
                "must be auto or a non-empty protocol revision identifier",
            )
        return value
    raise _error(field, origin, "unknown setting", code="unknown_setting")


def _validate_mapping(
    values: _Mapping[str, _Any], origin: str, *, partial: bool = False
) -> dict[str, _Any]:
    if "sources" in values:
        raise _error("sources", origin, "unknown setting", code="unknown_setting")
    for field in _FIELDS:
        if field in values:
            _validate(
                field,
                values[field],
                f"argument:{field}" if origin == "explicit" else origin,
            )
    try:
        validated = Config.model_validate(values, context={"m3_partial_layer": partial})
    except _ValidationError as error:
        first = error.errors(
            include_input=False, include_context=False, include_url=False
        )[0]
        location = first["loc"]
        # Mapping keys and arbitrary native strings are not trusted diagnostics.
        fields = set(Config.model_fields)
        field = (
            str(location[0]) if location and location[0] in fields else "configuration"
        )
        unknown = first["type"] == "extra_forbidden"
        if (
            unknown
            and len(location) == 1
            and isinstance(location[0], str)
            and _REVISION_PATTERN.fullmatch(location[0])
        ):
            field = location[0][:128]
        raise _error(
            field,
            f"argument:{field}" if origin == "explicit" else origin,
            "unknown setting"
            if unknown
            else "invalid setting or incompatible settings",
            code="unknown_setting" if unknown else "invalid_configuration",
        ) from None
    return _typing.cast(dict[str, _Any], _explicit_values(validated))


def _find_pyproject(start: str | _Path | None) -> _Path | None:
    location = _Path(start) if start is not None else _Path.cwd()
    location = location if location.is_dir() else location.parent
    for directory in (location, *location.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _project_values(
    start: str | _Path | None, config_file: str | _Path | None = None
) -> tuple[dict[str, _Any], str]:
    path = _Path(config_file) if config_file is not None else _find_pyproject(start)
    if path is None:
        return {}, "project:none"
    origin = f"file:{path}" if config_file is not None else f"pyproject:{path}"
    try:
        document = _tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise _error(
            "project", origin, "invalid TOML", code="invalid_project"
        ) from None
    tool = document.get("tool", {})
    if not isinstance(tool, dict):
        raise _error(
            "project", origin, "tool table must be a table", code="invalid_project"
        )
    if config_file is not None and "m3" not in tool:
        raise _error(
            "project",
            origin,
            "explicit file requires [tool.m3]",
            code="invalid_project",
        )
    values = tool.get("m3", {})
    if not isinstance(values, dict):
        raise _error(
            "project", origin, "[tool.m3] must be a table", code="invalid_project"
        )
    normalized = _normalize_toml(values)
    return _validate_mapping(normalized, origin, partial=True), origin


def _normalize_toml(values: _Mapping[str, _Any]) -> dict[str, _Any]:
    """Only documented optional budgets may use TOML false as disabled."""
    result: dict[str, _Any] = {}
    for name, value in values.items():
        if isinstance(value, _Mapping):
            result[name] = _normalize_toml(value)
        elif value is False and name in {"total_seconds", "normalized_load_threshold"}:
            result[name] = None
        else:
            result[name] = value
    return result


def _environment_values(environment: _Mapping[str, str]) -> dict[str, _Any]:
    values: dict[str, _Any] = {}
    for name, field in _ENV_FIELDS.items():
        if name in environment:
            values[field] = _validate(field, environment[name], f"env:{name}")
    return values


def load_config(
    explicit: _Mapping[str, _Any] | None = None,
    *,
    env: _Mapping[str, str] | None = None,
    cwd: str | _Path | None = None,
    config_file: str | _Path | None = None,
    artifact_policy: _Any = _UNSET,
    protocol_revision: _Any = _UNSET,
) -> Config:
    """Resolve SDK settings using explicit, environment, project, default order.

    ``env`` is injectable for deterministic callers and tests; omitted means
    the ambient process environment.  Project discovery reads only the
    nearest parent ``pyproject.toml`` and never reads ``.env`` files. An explicit
    ``config_file`` replaces discovery and uses the same ``[tool.m3]`` table.
    Native settings are parsed and merged, but runtime application is separate.
    """

    provided = dict(explicit or {})
    keyword_values = {
        field: value
        for field, value in {
            "artifact_policy": artifact_policy,
            "protocol_revision": protocol_revision,
        }.items()
        if value is not _UNSET
    }
    duplicates = sorted(set(provided) & set(keyword_values))
    if duplicates:
        raise _error(
            ", ".join(duplicates),
            "explicit",
            "setting supplied more than once",
            code="duplicate_setting",
        )
    provided.update(keyword_values)
    explicit_values = _validate_mapping(provided, "explicit", partial=True)
    environment_values = _environment_values(env if env is not None else _os.environ)
    project_values, project_origin = _project_values(cwd, config_file)
    values: dict[str, _Any] = {}
    origins: dict[str, ConfigOrigin] = dict(Config().sources)
    for source_values, source, origin in (
        (project_values, ConfigSource.PROJECT, project_origin),
        (environment_values, ConfigSource.ENVIRONMENT, "environment"),
        (explicit_values, ConfigSource.EXPLICIT, "explicit"),
    ):
        values = _overlay(values, source_values)
        for path in _leaves(source_values):
            leaf_origin = (
                f"argument:{path}"
                if source is ConfigSource.EXPLICIT
                else f"env:M3_{path.upper()}"
                if source is ConfigSource.ENVIRONMENT
                else origin
            )
            origins[path] = ConfigOrigin(source=source, origin=leaf_origin)
    # Revalidate the effective merge: individually valid layers can conflict.
    effective = _validate_mapping(values, "effective")
    result = Config.model_validate(effective)
    final_origins = {
        path: origins.get(path, result.sources[path]) for path in result.sources
    }
    return Config(**effective, sources=final_origins)


__all__ = [
    "Config",
    "ConfigError",
    "ConfigOrigin",
    "ConfigSource",
    "load_config",
    "merge_settings",
]
