"""Private owned configuration staging for bounded native startup."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path

from .._bootstrap_operations import OwnedBootstrapOperations, PrivateBytes
from .._startup_deadlines import StartupBudget
from .contracts import HarnessLaunch
from .native import ProcessOwner


class NativePreparation:
    """Own staged native files until their process group has stopped."""

    def __init__(
        self, operations: OwnedBootstrapOperations, root: Path, clock: StartupBudget
    ) -> None:
        self.operations = operations
        self.root = root
        self.clock = clock

    @classmethod
    async def create(
        cls, launch: HarnessLaunch, harness: str, files: Mapping[str, bytes]
    ) -> NativePreparation:
        clock = launch._startup_clock
        if clock is None:
            raise RuntimeError("owned native preparation needs a startup budget")
        deadline = clock.begin_owned_preparation()
        assert deadline is not None
        operations = OwnedBootstrapOperations()
        try:
            resource = await asyncio.to_thread(
                operations.stage_native_launch,
                harness,
                {name: PrivateBytes(value) for name, value in files.items()},
                deadline=deadline,
            )
            return cls(operations, resource.root, clock)
        except BaseException:
            # The operation facade retains unresolved ownership if this bounded
            # cleanup attempt cannot confirm writer death.
            try:
                await asyncio.to_thread(
                    operations.close, deadline=clock.begin_cleanup()
                )
            except BaseException:
                # The original preparation error remains the caller's cause;
                # the facade retains unresolved resources for run teardown.
                pass
            raise

    def adopt(self, owner: ProcessOwner) -> None:
        owner._owned_preparation = self

    async def close(self) -> None:
        deadline = self.clock.begin_cleanup()
        await asyncio.to_thread(self.operations.close, deadline=deadline)
        if not self.operations.cleanup_complete:
            from ..errors import CleanupError

            raise CleanupError("native configuration cleanup incomplete")


def owned_native_preparation(launch: HarnessLaunch) -> bool:
    clock = launch._startup_clock
    cache = getattr(launch, "_cache_policy", None) or getattr(
        launch.spec, "cache", None
    )
    return clock is not None and (
        clock.requires_ownership
        or launch.spec.admission.enabled
        or getattr(cache, "mode", None) == "immutable-seed"
    )


def validate_owned_startup(launch: HarnessLaunch) -> None:
    if not owned_native_preparation(launch):
        return
    if os.name != "posix":
        from ..errors import UnsupportedFeature

        raise UnsupportedFeature("owned native startup requires POSIX child status")
