"""Short, git-style run labels derived from run IDs."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator

_MIN_HEX = 7
_UUID_HEX = re.compile(r"[0-9a-f]{32}")


def run_label_candidates(run_id: str) -> Iterator[str]:
    """'Run <hex prefix>' labels, shortest first, starting at 7 hex characters."""
    digits = run_id.removeprefix("run-")
    if not _UUID_HEX.fullmatch(digits):
        digits = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    for length in range(_MIN_HEX, len(digits) + 1):
        yield f"Run {digits[:length]}"
