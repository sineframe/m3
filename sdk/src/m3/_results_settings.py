"""Translate resolved result settings at owned SQLite store construction."""

from __future__ import annotations

import math

from .types import ResultsSettings, Unset

_DEFAULT_BUSY_TIMEOUT_MS = 5000
_MAX_BUSY_TIMEOUT_MS = 2**31 - 1


def sqlite_busy_timeout_ms(settings: ResultsSettings) -> int:
    """Preserve the existing default and round positive budgets up to milliseconds."""

    seconds = settings.busy_timeout_seconds
    if isinstance(seconds, Unset):
        return _DEFAULT_BUSY_TIMEOUT_MS
    if seconds > _MAX_BUSY_TIMEOUT_MS / 1000:
        raise ValueError("results busy timeout exceeds SQLite's millisecond range")
    return math.ceil(seconds * 1000)
