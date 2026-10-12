"""Invocation-owned configuration and private worker bootstrap lifetime."""

from __future__ import annotations

import math
import time
from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator, model_validator

from ._configuration_context import ConfigurationContext
from ._configuration_snapshot import (
    ConfigurationSnapshot,
    ConfigurationSnapshotError,
    freeze_configuration,
    restore_configuration_snapshot,
)
from ._literal_store import (
    LiteralBootstrapBroker,
    LiteralTransferError,
    RunLiteralStore,
    WorkerBootstrap,
    bootstrap_worker,
)
from .configuration import Config
from .types import FrozenModel, HTTPServer, StdioServer


class _WorkerDescriptor(FrozenModel):
    endpoint: StrictStr = Field(min_length=1, max_length=4096)
    capsule: StrictStr = Field(min_length=1, max_length=4096)
    run_id: StrictStr = Field(min_length=1, max_length=4096)
    generation: StrictStr = Field(min_length=1, max_length=4096)
    slots: tuple[StrictStr, ...]

    def descriptor(self) -> WorkerBootstrap:
        return WorkerBootstrap(
            self.endpoint, self.capsule, self.run_id, self.generation, self.slots
        )


class ConfigurationTransfer(FrozenModel):
    version: Literal[1] = 1
    snapshot: ConfigurationSnapshot
    bootstrap: _WorkerDescriptor | None = None
    deadline: float | None = Field(default=None, strict=True, allow_inf_nan=False)

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid configuration transfer version")
        return value

    @model_validator(mode="after")
    def _complete_manifest(self) -> ConfigurationTransfer:
        if not self.snapshot.handles:
            if self.bootstrap is not None or self.deadline is not None:
                raise ValueError("default configuration requires no bootstrap")
            return self
        if self.bootstrap is None or self.deadline is None:
            raise ValueError("private configuration requires a complete bootstrap")
        handle = self.snapshot.handles[0]
        if (
            self.bootstrap.run_id != handle.run_id
            or self.bootstrap.generation != handle.generation
            or self.bootstrap.slots != (handle.slot,)
        ):
            raise ValueError("configuration bootstrap manifest mismatch")
        return self


class ConfigurationInvocation:
    """Retain frozen settings for workers, replacements and later sessions."""

    def __init__(
        self,
        config: Config,
        *,
        run_id: str | None = None,
        literal_registry: RunLiteralStore | None = None,
        selected_file: str | None = None,
        server_selections: tuple[HTTPServer | StdioServer, ...] | None = None,
    ) -> None:
        self._closed = False
        self._broker: LiteralBootstrapBroker | None = None
        self._owns_registry = literal_registry is None
        self._registry = literal_registry
        if (
            config != Config()
            or selected_file is not None
            or server_selections is not None
        ) and self._registry is None:
            self._registry = RunLiteralStore(run_id or f"run-{uuid4().hex}")
        self.context = ConfigurationContext(
            config, self._registry, selected_file, server_selections
        )
        try:
            self.snapshot = freeze_configuration(
                config,
                self._registry,
                selected_file=selected_file,
                server_selections=server_selections,
            )
        except BaseException:
            if self._owns_registry and self._registry is not None:
                self._registry.close()
            raise

    def __repr__(self) -> str:
        return "ConfigurationInvocation(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("configuration invocation requires private transport")

    def issue(self) -> ConfigurationTransfer:
        if self._closed:
            raise ConfigurationSnapshotError("configuration invocation is closed")
        if not self.snapshot.handles:
            return ConfigurationTransfer(snapshot=self.snapshot)
        assert self._registry is not None
        timeout = self.context.config.worker_bootstrap.timeout_seconds
        try:
            if self._broker is None:
                self._broker = LiteralBootstrapBroker(
                    self._registry, timeout_seconds=timeout
                )
            deadline = time.monotonic() + timeout
            descriptor = self._broker.issue(self.snapshot.handles, deadline=deadline)
        except LiteralTransferError:
            raise ConfigurationSnapshotError(
                "configuration worker bootstrap is unavailable"
            ) from None
        if time.monotonic() >= deadline:
            raise ConfigurationSnapshotError(
                "configuration bootstrap deadline exceeded"
            )
        return ConfigurationTransfer(
            snapshot=self.snapshot,
            bootstrap=_WorkerDescriptor(**descriptor.metadata()),
            deadline=deadline,
        )

    def close(self) -> None:
        self._closed = True
        if self._broker is not None:
            self._broker.close()
            if not self._broker.cleanup_complete:
                raise ConfigurationSnapshotError("configuration cleanup is incomplete")
        if self._owns_registry and self._registry is not None:
            self._registry.close()


def accept_configuration_transfer(raw: str) -> ConfigurationContext:
    """Accept local settings once; later kit construction uses memory only."""
    registry: RunLiteralStore | None = None
    try:
        if len(raw) > 1024 * 1024:
            raise ConfigurationSnapshotError("configuration transfer exceeds limit")
        transfer = ConfigurationTransfer.model_validate_json(raw)
        if transfer.bootstrap is not None:
            assert transfer.deadline is not None
            remaining = transfer.deadline - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise ConfigurationSnapshotError(
                    "configuration bootstrap deadline exceeded"
                )
            registry = bootstrap_worker(
                transfer.bootstrap.descriptor(),
                timeout_seconds=remaining,
                deadline=transfer.deadline,
            )
        context = restore_configuration_snapshot(transfer.snapshot, registry)
        if transfer.deadline is not None and time.monotonic() >= transfer.deadline:
            raise ConfigurationSnapshotError(
                "configuration bootstrap deadline exceeded"
            )
        return context
    except (LiteralTransferError, ValueError, TypeError, RecursionError):
        if registry is not None:
            registry.close()
        raise ConfigurationSnapshotError(
            "configuration transfer is unavailable"
        ) from None
