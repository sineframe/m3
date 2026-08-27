#!/usr/bin/env python3
"""Block during MCP initialization and expose the owned process ID."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time


def main() -> int:
    marker = os.environ.get("MCP_PAL_E2E_PID_FILE")
    if not marker:
        return 2
    Path(marker).write_text(str(os.getpid()), encoding="utf-8")
    sys.stdin.readline()
    time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
