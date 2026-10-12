"""Typed private requests for owned managed-runtime acquisition."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .._startup_deadlines import StartupBudget

RUNTIME_ENVIRONMENT = (
    "HOME",
    "USERPROFILE",
    "PATH",
    "XDG_CACHE_HOME",
    "SYSTEMROOT",
    "WINDIR",
    "M3_HARNESS_CACHE_DIR",
    "M3_RUNTIME_ARCH",
    "M3_ROSETTA",
    "M3_RUNTIME_LIBC",
    "M3_RUNTIME_AVX2",
    "M3_GITHUB_TOKEN",
)


@dataclass(frozen=True)
class RuntimeAcquisition:
    """Frozen acquisition inputs; paths travel only over private job IPC."""

    kind: str
    selector: str | Mapping[str, Any] = field(repr=False)
    project_root: Path = field(repr=False)
    cache_root: Path | None = field(default=None, repr=False)
    invocation_dir: Path | None = field(default=None, repr=False)
    progress_file: Path | None = field(default=None, repr=False)
    environment: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        from .core import KINDS, _version

        if not isinstance(self.kind, str):
            raise ValueError("unsupported runtime kind")
        kind = {"open-code": "opencode", "claude_code": "claude"}.get(
            self.kind.lower(), self.kind.lower()
        )
        if kind not in KINDS:
            raise ValueError("unsupported runtime kind")
        object.__setattr__(self, "kind", kind)
        if any(
            value is not None and not value.is_absolute()
            for value in (
                self.project_root,
                self.cache_root,
                self.invocation_dir,
                self.progress_file,
            )
        ):
            raise ValueError("owned runtime inputs require absolute paths")
        if any(
            key not in RUNTIME_ENVIRONMENT or not isinstance(value, str)
            for key, value in self.environment.items()
        ):
            raise ValueError("invalid owned runtime environment")
        if type(self.selector) not in {str, dict}:
            raise ValueError("owned runtime selector requires plain JSON inputs")
        try:
            snapshot = json.loads(json.dumps(self.selector, allow_nan=False))
        except (TypeError, ValueError):
            raise ValueError(
                "owned runtime selector requires plain JSON inputs"
            ) from None
        specification = {"version": snapshot} if isinstance(snapshot, str) else snapshot
        _version(specification.get("version") or specification.get("tag"))
        target_environment = specification.get("env")
        if target_environment is not None and (
            not isinstance(target_environment, dict)
            or any(not isinstance(value, str) for value in target_environment.values())
        ):
            raise ValueError("invalid runtime target environment")
        object.__setattr__(
            self,
            "selector",
            snapshot if isinstance(snapshot, str) else MappingProxyType(snapshot),
        )
        object.__setattr__(
            self, "environment", MappingProxyType(dict(self.environment))
        )

    def __getstate__(self) -> None:
        raise TypeError("owned runtime requests require private transport")

    def private_frame(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "environment": dict(self.environment),
            "selector": self.selector
            if isinstance(self.selector, str)
            else dict(self.selector),
            "project_root": os.fspath(self.project_root),
            "cache_root": os.fspath(self.cache_root)
            if self.cache_root is not None
            else None,
            "invocation_dir": os.fspath(self.invocation_dir)
            if self.invocation_dir is not None
            else None,
            "progress_file": os.fspath(self.progress_file)
            if self.progress_file is not None
            else None,
        }

    @classmethod
    def from_private_frame(cls, value: dict[str, Any]) -> RuntimeAcquisition:
        fields = {
            "kind",
            "selector",
            "project_root",
            "cache_root",
            "invocation_dir",
            "progress_file",
            "environment",
        }
        if set(value) != fields or not isinstance(value["kind"], str):
            raise ValueError("invalid owned runtime request")
        environment = value["environment"]
        if not isinstance(environment, dict) or any(
            key not in RUNTIME_ENVIRONMENT or not isinstance(item, str)
            for key, item in environment.items()
        ):
            raise ValueError("invalid owned runtime environment")
        if not isinstance(value["selector"], (str, dict)) or not isinstance(
            value["project_root"], str
        ):
            raise ValueError("invalid owned runtime request")
        if any(
            value[key] is not None and not isinstance(value[key], str)
            for key in fields - {"kind", "selector", "environment"}
        ):
            raise ValueError("invalid owned runtime request")
        return cls(
            value["kind"],
            value["selector"],
            Path(value["project_root"]),
            Path(value["cache_root"]) if value["cache_root"] is not None else None,
            Path(value["invocation_dir"])
            if value["invocation_dir"] is not None
            else None,
            Path(value["progress_file"])
            if value["progress_file"] is not None
            else None,
            environment,
        )


class RuntimeOperationOwner(Protocol):
    @property
    def cleanup_complete(self) -> bool: ...

    def release_runtime(
        self, path: str, device: int, inode: int, *, deadline: float
    ) -> None: ...
    def close(self, *, deadline: float) -> None: ...
    def cancel_pending(self) -> None: ...


class OwnedRuntimeLease:
    """A private package handle; native guardians retain acknowledged copies."""

    def __init__(
        self,
        descriptor: int,
        path: Path,
        operations: RuntimeOperationOwner,
        clock: StartupBudget,
    ) -> None:
        self.descriptor = descriptor
        self.path = path
        self.operations = operations
        self._clock = clock
        self._release_identity: tuple[int, int] | None = None
        self._marker_released = False
        self._release_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    def __repr__(self) -> str:
        return "OwnedRuntimeLease(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("owned runtime handles cannot be serialized")

    @property
    def descriptors(self) -> tuple[int, ...]:
        return (self.descriptor,) if self.descriptor >= 0 else ()

    async def release(self) -> None:
        async with self._release_lock:
            if self.descriptor >= 0:
                info = os.fstat(self.descriptor)
                self._release_identity = (info.st_dev, info.st_ino)
                os.close(self.descriptor)
                self.descriptor = -1
            deadline = self._clock.begin_cleanup()
            cancelled = False
            try:
                if not self._marker_released and self._release_identity is not None:
                    await asyncio.to_thread(
                        self.operations.release_runtime,
                        str(self.path),
                        *self._release_identity,
                        deadline=deadline,
                    )
                    self._marker_released = True
            except asyncio.CancelledError:
                cancelled = True
                self.operations.cancel_pending()
                raise
            finally:
                from .._startup_deadlines import retain_cleanup, wait_cleanup

                if self._close_task is None or self._close_task.done():

                    async def close_operations() -> None:
                        await asyncio.to_thread(
                            self.operations.close, deadline=deadline
                        )

                    self._close_task = retain_cleanup(close_operations)
                await wait_cleanup(self._close_task, self._clock, cancelled=cancelled)
            if not self.operations.cleanup_complete:
                from .core import RuntimeValidationError

                raise RuntimeValidationError("owned runtime cleanup is incomplete")
