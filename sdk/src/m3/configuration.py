"""Pure SDK configuration resolution.

This module contains only the small, process-independent configuration
contract used by SDK callers.  Application settings (credentials, database,
and UI/runtime limits) intentionally remain in :mod:`m3_app.settings`.
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import os as _os
import re as _re
import sys as _sys
import threading as _threading
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
    StrictStr as _StrictStr,
)
from pydantic import (
    ValidationError as _ValidationError,
)
from pydantic import (
    field_validator as _field_validator,
)
from pydantic import (
    model_validator as _model_validator,
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


class CliSettings(_FrozenModel):
    """Limits used by the m3 CLI before pytest starts."""

    project_python_timeout_seconds: float = _Field(
        default=15.0, gt=0, le=3600, allow_inf_nan=False, strict=True
    )


class ResultsSettings(_FrozenModel):
    """Results-store settings applied to stores the pytest plugin opens."""

    busy_timeout_seconds: float = _Field(
        default=5.0, ge=0, le=3600, allow_inf_nan=False, strict=True
    )


_SETTING_NAMES: tuple[str, ...] = (
    "artifact_policy",
    "protocol_revision",
    "cli.project_python_timeout_seconds",
    "results.busy_timeout_seconds",
)
_SECTIONS: dict[str, type[_FrozenModel]] = {
    "cli": CliSettings,
    "results": ResultsSettings,
}


def _leaf(config: Config, name: str) -> _Any:
    section, _, key = name.partition(".")
    return getattr(getattr(config, section), key) if key else getattr(config, section)


def _default(name: str) -> _Any:
    section, _, key = name.partition(".")
    model = _SECTIONS[section] if key else Config
    return model.model_fields[key or section].default


_ENV_FIELDS = {
    "M3_ARTIFACT_POLICY": "artifact_policy",
    "M3_PROTOCOL_REVISION": "protocol_revision",
}
_ARTIFACT_POLICIES = frozenset({"failed", "always", "never"})
_REVISION_PATTERN = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _default_origins() -> dict[str, ConfigOrigin]:
    return {
        name: ConfigOrigin(source=ConfigSource.DEFAULT, origin="default")
        for name in _SETTING_NAMES
    }


class Config(_FrozenModel):
    """Effective SDK-wide settings and the origin of each setting."""

    artifact_policy: _Literal["failed", "always", "never"] = "failed"
    protocol_revision: _StrictStr = "auto"
    cli: CliSettings = _Field(default_factory=CliSettings)
    results: ResultsSettings = _Field(default_factory=ResultsSettings)
    sources: _Mapping[str, ConfigOrigin] = _Field(default_factory=_default_origins)

    @_field_validator("protocol_revision")
    @classmethod
    def _valid_protocol_revision(cls, value: str) -> str:
        if not value or _REVISION_PATTERN.fullmatch(value) is None:
            raise ValueError("must be auto or a non-empty protocol revision identifier")
        return value

    @_model_validator(mode="after")
    def _validate_sources(self) -> Config:
        if set(self.sources) != set(_SETTING_NAMES):
            raise ValueError(
                "sources must identify every supported configuration field"
            )
        if "sources" not in self.__pydantic_fields_set__:
            # Direct construction is an explicit source for supplied fields;
            # omitted fields retain the default origin.  The mapping is
            # frozen here because this validator runs after the base model's
            # recursive-freeze hook.
            def explicit(name: str) -> bool:
                section, _, key = name.partition(".")
                if not key:
                    return name in self.__pydantic_fields_set__
                return (
                    section in self.__pydantic_fields_set__
                    and key in getattr(self, section).model_fields_set
                )

            inferred = {
                name: ConfigOrigin(
                    source=ConfigSource.EXPLICIT, origin=f"argument:{name}"
                )
                if explicit(name)
                else ConfigOrigin(source=ConfigSource.DEFAULT, origin="default")
                for name in _SETTING_NAMES
            }
            object.__setattr__(self, "sources", _FrozenMapping(inferred))
        else:
            for name in _SETTING_NAMES:
                if self.sources[name].source is ConfigSource.DEFAULT and _leaf(
                    self, name
                ) != _default(name):
                    raise ValueError(
                        f"{name} has a non-default value but default provenance"
                    )
        return self

    @property
    def provenance(self) -> _Mapping[str, ConfigOrigin]:
        """Compatibility name for callers that call origins provenance."""

        return self.sources

    def source_for(self, field: str) -> ConfigOrigin:
        if field not in _SETTING_NAMES:
            raise ConfigError(
                field=field,
                origin="runtime",
                reason="unknown setting",
                code="unknown_setting",
            )
        return self.sources[field]


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
    section, _, key = field.partition(".")
    if key and section in _SECTIONS:
        try:
            validated = _SECTIONS[section].model_validate({key: value})
        except _ValidationError as exc:
            raise _error(field, origin, exc.errors()[0]["msg"]) from None
        return getattr(validated, key)
    raise _error(field, origin, "unknown setting", code="unknown_setting")


def _flatten(values: _Mapping[str, _Any], origin: str) -> dict[str, _Any]:
    flat: dict[str, _Any] = {}
    unknown: list[str] = []
    for key, value in values.items():
        if key in _SECTIONS:
            if not isinstance(value, _Mapping):
                raise _error(key, origin, "must be a table")
            for leaf, leaf_value in value.items():
                name = f"{key}.{leaf}"
                if name in _SETTING_NAMES:
                    flat[name] = leaf_value
                else:
                    unknown.append(name)
        elif key in _SETTING_NAMES and "." not in key:
            flat[key] = value
        else:
            unknown.append(key)
    if unknown:
        raise _error(
            ", ".join(sorted(unknown)),
            origin,
            "unknown setting",
            code="unknown_setting",
        )
    return flat


def _validate_mapping(values: _Mapping[str, _Any], origin: str) -> dict[str, _Any]:
    values = _flatten(values, origin)
    return {
        field: _validate(
            field,
            values[field],
            f"argument:{field}" if origin == "explicit" else origin,
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


def _read_m3_table(path: _Path, origin: str) -> dict[str, _Any] | None:
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
    if "m3" not in tool:
        return None
    values = tool["m3"]
    if not isinstance(values, dict):
        raise _error(
            "project", origin, "[tool.m3] must be a table", code="invalid_project"
        )
    return values


def _project_values(start: str | _Path | None) -> tuple[dict[str, _Any], str]:
    path = _find_pyproject(start)
    if path is None:
        return {}, "project:none"
    origin = f"pyproject:{path}"
    table = _read_m3_table(path, origin)
    return _validate_mapping(table or {}, origin), origin


def _file_values(config_file: str | _Path) -> tuple[dict[str, _Any], str]:
    path = _Path(config_file).expanduser().absolute()
    origin = f"config-file:{path}"
    if not path.is_file():
        raise _error("config_file", origin, "file not found", code="invalid_project")
    table = _read_m3_table(path, origin)
    if table is None:
        raise _error(
            "project", origin, "missing [tool.m3] table", code="invalid_project"
        )
    return _validate_mapping(table, origin), origin


def _environment_values(environment: _Mapping[str, str]) -> dict[str, _Any]:
    values: dict[str, _Any] = {}
    for name, field in _ENV_FIELDS.items():
        if name in environment:
            values[field] = _validate(field, environment[name], f"env:{name}")
    return values


def _build(values: _Mapping[str, _Any], origins: _Mapping[str, ConfigOrigin]) -> Config:
    sections: dict[str, _Any] = {
        section: model(
            **{
                name.partition(".")[2]: values[name]
                for name in _SETTING_NAMES
                if name.startswith(f"{section}.")
            }
        )
        for section, model in _SECTIONS.items()
    }
    top = {name: values[name] for name in _SETTING_NAMES if "." not in name}
    return Config(**top, **sections, sources=dict(origins))


def load_config(
    explicit: _Mapping[str, _Any] | None = None,
    *,
    env: _Mapping[str, str] | None = None,
    cwd: str | _Path | None = None,
    config_file: str | _Path | None = None,
    artifact_policy: _Any = _UNSET,
    protocol_revision: _Any = _UNSET,
) -> Config:
    """Resolve SDK settings using explicit, environment, project (or `config_file`), default order.

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
    explicit_values = _validate_mapping(provided, "explicit")
    environment_values = _environment_values(env if env is not None else _os.environ)
    project_values, project_origin = (
        _file_values(config_file) if config_file is not None else _project_values(cwd)
    )

    values = {name: _default(name) for name in _SETTING_NAMES}
    origins: dict[str, ConfigOrigin] = {
        name: ConfigOrigin(source=ConfigSource.DEFAULT, origin="default")
        for name in _SETTING_NAMES
    }
    for name, value in project_values.items():
        values[name] = value
        origins[name] = ConfigOrigin(source=ConfigSource.PROJECT, origin=project_origin)
    for field, value in environment_values.items():
        values[field] = value
        origins[field] = ConfigOrigin(
            source=ConfigSource.ENVIRONMENT, origin=f"env:M3_{field.upper()}"
        )
    for field, value in explicit_values.items():
        values[field] = value
        origins[field] = ConfigOrigin(
            source=ConfigSource.EXPLICIT, origin=f"argument:{field}"
        )
    return _build(values, origins)


def configuration_digest(config: Config) -> str:
    """Stable digest of effective values; sources are excluded."""

    payload = config.model_dump(mode="json", exclude={"sources"})
    encoded = _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + _hashlib.sha256(encoded).hexdigest()


_run_config_lock = _threading.RLock()
_run_config: Config | None = None


def _install_run_config(config: Config | None) -> Config | None:
    """Install the invocation's resolved settings; return the previous value."""

    global _run_config
    with _run_config_lock:
        previous, _run_config = _run_config, config
        return previous


def _active_run_config() -> Config | None:
    with _run_config_lock:
        return _run_config


_SNAPSHOT_VERSION = 1


def _config_snapshot(config: Config) -> str:
    return _json.dumps(
        {"version": _SNAPSHOT_VERSION, "config": config.model_dump(mode="json")}
    )


def _config_from_snapshot(text: object) -> Config:
    try:
        payload = _json.loads(text) if isinstance(text, str) else None
        if not isinstance(payload, dict) or payload.get("version") != _SNAPSHOT_VERSION:
            raise ValueError
        return Config.model_validate(payload["config"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("M3 configuration snapshot is invalid") from None


def _resolve_kit_config(
    config: Config | _Mapping[str, _Any] | None,
    *,
    env: _Mapping[str, str] | None,
    cwd: str | _Path | None,
) -> Config:
    """A ready Config wins; inside a run, ambient discovery uses the run snapshot."""

    if isinstance(config, Config):
        return config
    active = _active_run_config()
    if active is None or env is not None or cwd is not None:
        return load_config(config, env=env, cwd=cwd)
    values = {name: _leaf(active, name) for name in _SETTING_NAMES}
    origins = dict(active.sources)
    for name, value in _validate_mapping(config or {}, "explicit").items():
        values[name] = value
        origins[name] = ConfigOrigin(
            source=ConfigSource.EXPLICIT, origin=f"argument:{name}"
        )
    return _build(values, origins)


__all__ = [
    "CliSettings",
    "Config",
    "ConfigError",
    "ConfigOrigin",
    "ConfigSource",
    "ResultsSettings",
    "configuration_digest",
    "load_config",
]
