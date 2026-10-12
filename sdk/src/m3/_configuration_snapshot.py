"""Configuration transport descriptors with run-owned private payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from ._configuration_context import ConfigurationContext
from ._literal_store import LiteralHandle, LiteralTransferError, RunLiteralStore
from .configuration import Config
from .types import FrozenModel


class ConfigurationSnapshotError(ValueError):
    """Value-free failure at the invocation snapshot boundary."""


class _LiteralReference(FrozenModel):
    run_id: StrictStr = Field(min_length=1, max_length=4096)
    generation: StrictStr = Field(min_length=1, max_length=4096)
    slot: StrictStr = Field(min_length=1, max_length=4096)

    def handle(self) -> LiteralHandle:
        return LiteralHandle(self.run_id, self.generation, self.slot)


class ConfigurationSnapshot(FrozenModel):
    version: Literal[1] = 1
    digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    settings: Config | None = Field(default=None, repr=False)
    reference: _LiteralReference | None = None

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid snapshot version")
        return value

    @model_validator(mode="after")
    def _valid_payload(self) -> ConfigurationSnapshot:
        if (self.settings is None) == (self.reference is None):
            raise ValueError("snapshot requires one configuration source")
        if self.settings is not None and self.settings != Config():
            raise ValueError("configured settings require private transport")
        return self

    @property
    def handles(self) -> tuple[LiteralHandle, ...]:
        return () if self.reference is None else (self.reference.handle(),)


def configuration_digest(config: Config) -> str:
    """Hash diagnostic field/source information, never the private payload."""
    projection: dict[str, Any] = config.redacted_projection()
    projection.pop("sources", None)
    if "native_settings" in projection:
        projection["native_settings"] = sorted(
            projection["native_settings"],
            key=lambda item: (item["field"], item["source"]),
        )
    projection["budgets"] = {
        "harness": config.harness_timeouts.model_dump(mode="json"),
        "mcp": config.mcp_timeouts.model_dump(mode="json"),
        "cli": config.cli.model_dump(mode="json"),
        "results": config.results.model_dump(mode="json"),
        "worker_bootstrap": config.worker_bootstrap.model_dump(mode="json"),
        "admission": config.admission.model_dump(mode="json"),
        "harnesses": {
            name: {
                "timeouts": settings.timeouts.model_dump(mode="json"),
                "admission": settings.admission.model_dump(mode="json"),
                "cache_mode": settings.cache.mode,
            }
            for name, settings in (
                ("codex", config.harnesses.codex),
                ("claude_code", config.harnesses.claude_code),
                ("opencode", config.harnesses.opencode),
            )
        },
        "servers": sorted(
            json.dumps(server.timeouts.model_dump(mode="json"), sort_keys=True)
            for server in config.servers.values()
        ),
        "cache_mode": config.cache.mode,
    }
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse_configuration_snapshot(raw: str) -> ConfigurationSnapshot:
    """Reject malformed transport metadata without exposing its input."""
    if len(raw) > 1024 * 1024:
        raise ConfigurationSnapshotError("configuration snapshot exceeds limit")
    try:
        return ConfigurationSnapshot.model_validate_json(raw)
    except (ValueError, TypeError, RecursionError):
        raise ConfigurationSnapshotError("configuration snapshot is invalid") from None


def freeze_configuration(
    config: Config, registry: RunLiteralStore | None
) -> ConfigurationSnapshot:
    digest = configuration_digest(config)
    if config == Config():
        return ConfigurationSnapshot(digest=digest, settings=config)
    if registry is None:
        raise ConfigurationSnapshotError("configured settings need a private registry")
    handle = registry.freeze(config.model_dump_json())
    return ConfigurationSnapshot(
        digest=digest, reference=_LiteralReference(**handle.metadata())
    )


def restore_configuration_snapshot(
    snapshot: ConfigurationSnapshot, registry: RunLiteralStore | None
) -> ConfigurationContext:
    if snapshot.settings is not None:
        config = snapshot.settings
    else:
        if registry is None or snapshot.reference is None:
            raise ConfigurationSnapshotError("configuration registry is unavailable")
        try:
            config = Config.model_validate_json(
                registry.resolve(snapshot.reference.handle())
            )
        except (LiteralTransferError, ValueError, TypeError):
            raise ConfigurationSnapshotError(
                "configuration snapshot is invalid"
            ) from None
    if configuration_digest(config) != snapshot.digest:
        raise ConfigurationSnapshotError("configuration snapshot projection mismatch")
    return ConfigurationContext(config, registry)
