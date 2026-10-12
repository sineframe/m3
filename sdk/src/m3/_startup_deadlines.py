"""Active startup budgets and absolute native phase deadlines."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ._types.settings import UNSET, HarnessTimeouts


class StartupDeadlineExceeded(asyncio.TimeoutError):
    def __init__(self, phase: str) -> None:
        super().__init__(f"harness startup deadline exceeded during {phase}")
        self.phase = phase


@dataclass(frozen=True)
class PhaseDeadline:
    phase: str
    deadline: float | None

    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise StartupDeadlineExceeded(self.phase)
        return remaining


def earliest(*deadlines: float | None) -> float | None:
    present = [value for value in deadlines if value is not None]
    return min(present) if present else None


class StartupBudget:
    """Caller idle and admission queue suspend total, never native clocks."""

    def __init__(self, settings: HarnessTimeouts) -> None:
        self.settings = settings
        total = settings.total_seconds
        self._remaining = None if total is UNSET else total
        self._active_deadline: float | None = None
        self._caller_deadline: float | None = None
        self._native_deadline: float | None = None
        self._preparation_deadline: float | None = None
        self._active = False
        self._native = False
        self._finished = False
        self.cleanup_deadline: float | None = None

    @property
    def requires_ownership(self) -> bool:
        return any(value is not None for value in self.settings.model_dump().values())

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def bounded_preparation(self) -> bool:
        return self._remaining is not None

    @property
    def deadline(self) -> float | None:
        if self._finished:
            return None
        return earliest(
            self._active_deadline,
            self._caller_deadline,
            self._native_deadline,
            self._preparation_deadline,
        )

    def resume(self, *, caller_deadline: float | None = None) -> None:
        if self._finished:
            return
        self._caller_deadline = earliest(self._caller_deadline, caller_deadline)
        if not self._active:
            self._active = True
            if self._remaining is not None:
                self._active_deadline = time.monotonic() + self._remaining
        PhaseDeadline("preparation", self.deadline).remaining()

    def pause(self) -> None:
        if self._native:
            raise RuntimeError("native startup cannot suspend its deadline")
        if self._active_deadline is not None:
            self._remaining = self._active_deadline - time.monotonic()
            if self._remaining <= 0:
                raise StartupDeadlineExceeded("preparation")
        self._active_deadline = None
        self._active = False

    def begin_cleanup(self) -> float:
        if self.cleanup_deadline is None:
            self.cleanup_deadline = time.monotonic() + 10.0
        return self.cleanup_deadline

    def begin_owned_preparation(self, default_seconds: float = 30.0) -> float:
        """Share one configuration preparation ceiling across owned IO jobs."""
        self.resume()
        if self._preparation_deadline is None:
            value = self.settings.startup_seconds
            seconds = default_seconds if value is UNSET else value
            self._preparation_deadline = time.monotonic() + seconds
        deadline = self.deadline
        assert deadline is not None
        PhaseDeadline("configuration preparation", deadline).remaining()
        return deadline

    def clear_owned_preparation(self) -> None:
        self._preparation_deadline = None

    def begin_native(self) -> None:
        if self._native:
            PhaseDeadline("native startup", self.deadline).remaining()
            return
        self.resume()
        self._native = True
        value = self.settings.startup_seconds
        if value is not UNSET:
            self._native_deadline = time.monotonic() + value
        PhaseDeadline("native startup", self.deadline).remaining()

    def phase(self, name: str, default_seconds: float | None) -> PhaseDeadline:
        value = getattr(self.settings, name + "_seconds")
        seconds = default_seconds if value is UNSET else value
        own = None if seconds is None else time.monotonic() + seconds
        return PhaseDeadline(name, earliest(own, self.deadline))

    def finish(self) -> None:
        PhaseDeadline("native startup", self.deadline).remaining()
        self._finished = True


def native_phase(
    clock: StartupBudget | None, name: str, default_seconds: float
) -> PhaseDeadline:
    """Use an explicit startup ceiling for readiness/session defaults."""
    if clock is None:
        return PhaseDeadline(name, time.monotonic() + default_seconds)
    fallback = default_seconds
    if name in {"session", "readiness"} and clock.settings.startup_seconds is not UNSET:
        fallback = clock.settings.startup_seconds
    return clock.phase(name, fallback)
