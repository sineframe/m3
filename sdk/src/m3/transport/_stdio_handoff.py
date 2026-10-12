"""Validated launch data passed privately to the stdio capture process."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class ObserverHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: StrictStr
    port: StrictStr
    connection_id: StrictStr
    token: StrictStr = Field(repr=False)


class StdioHandoff(BaseModel):
    """Resolved values that a harness must neither expand nor inherit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: dict[StrictStr, StrictStr] = Field(repr=False)
    canaries: tuple[StrictStr, ...] = Field(repr=False)
    command: tuple[StrictStr, ...] | None = Field(default=None, repr=False)
    cwd: StrictStr | None = Field(default=None, repr=False)
    observer: ObserverHandoff | None = None

    @field_validator("command")
    @classmethod
    def valid_command(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and (
            not value or not value[0] or any("\x00" in part for part in value)
        ):
            raise ValueError("invalid stdio command")
        return value

    @field_validator("environment")
    @classmethod
    def valid_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not name or "=" in name or "\x00" in name or "\x00" in text
            for name, text in value.items()
        ):
            raise ValueError("invalid stdio environment")
        return value

    @field_validator("cwd")
    @classmethod
    def valid_cwd(cls, value: str | None) -> str | None:
        if value is not None and (not value or "\x00" in value):
            raise ValueError("invalid stdio working directory")
        return value

    @field_validator("observer", mode="before")
    @classmethod
    def optional_observer(cls, value: object) -> object:
        # Observation is passive: malformed observer metadata must not block
        # an otherwise valid MCP launch. Child launch fields remain strict.
        if value is None or isinstance(value, ObserverHandoff):
            return value
        if (
            isinstance(value, Mapping)
            and set(value) == {"host", "port", "connection_id", "token"}
            and all(isinstance(item, str) for item in value.values())
        ):
            return value
        return None
