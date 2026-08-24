#!/usr/bin/env python3
"""Emit hostile bounded-output cases for native adapter tests."""

from __future__ import annotations

import sys


if "--help" in sys.argv:
    print("--input-format stream-json --output-format stream-json")
    raise SystemExit(0)

for line in sys.stdin:
    if "binary" in line:
        sys.stdout.buffer.write(b"\xff" * (1024 * 1024 + 128))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write("x" * (1024 * 1024 + 128))
        sys.stdout.flush()
