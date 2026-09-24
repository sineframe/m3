#!/usr/bin/env python3
"""Block during MCP initialization and expose the owned process ID."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    marker = os.environ.get("M3_E2E_PID_FILE")
    if not marker:
        return 2
    marker_path = Path(marker)
    temporary = marker_path.with_name(f"{marker_path.name}.{os.getpid()}.tmp")
    temporary.write_text(str(os.getpid()), encoding="utf-8")
    temporary.replace(marker_path)
    sys.stdin.readline()
    time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
