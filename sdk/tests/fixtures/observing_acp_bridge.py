"""Test-only ACP wrapper that records inbound lifecycle messages.

The wrapped implementation remains the published reference bridge, so this
fixture observes the black-box protocol without adding test hooks to it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from typing import TextIO

from mcp_pal.bridge import reference


class _ObservingStdin:
    def __init__(self, stream: TextIO, marker: str) -> None:
        self._stream = stream
        self._marker = marker

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        line = self._stream.readline()
        if not line:
            raise StopIteration
        try:
            message = json.loads(line)
            method = message.get("method") if isinstance(message, dict) else None
        except json.JSONDecodeError:
            method = None
        with open(self._marker, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"method": method, "pid": os.getpid()}) + "\n")
        return line


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        marker_index = arguments.index("--observation-marker")
        marker = arguments.pop(marker_index + 1)
        arguments.pop(marker_index)
    except (ValueError, IndexError) as exc:
        raise RuntimeError("--observation-marker is required") from exc
    sys.stdin = _ObservingStdin(sys.stdin, marker)
    return reference.main(arguments)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
