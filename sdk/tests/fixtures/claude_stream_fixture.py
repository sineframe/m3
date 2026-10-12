#!/usr/bin/env python3
"""Deterministic stream-json process used by native adapter tests."""

from __future__ import annotations

import json
import os
import sys
import time

if "--help" in sys.argv:
    print("--input-format stream-json --output-format stream-json")
    raise SystemExit(0)


initialized = False

for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    if request.get("type") != "user":
        continue
    if not initialized and os.environ.get("M3_CLAUDE_EMIT_INIT") == "1":
        ready_at = time.monotonic() + float(os.environ.get("M3_CLAUDE_INIT_DELAY", "0"))
        while time.monotonic() < ready_at:
            print(json.dumps({"type": "system", "subtype": "status"}), flush=True)
            time.sleep(0.025)
        print(
            json.dumps({"type": "system", "subtype": "init", "mcp_servers": []}),
            flush=True,
        )
        initialized = True
    content = request.get("message", {}).get("content", ())
    text = "".join(
        block.get("text", "") for block in content if isinstance(block, dict)
    )
    if text == "sleep":
        time.sleep(2)
    print(json.dumps({"type": "assistant", "delta": text}), flush=True)
    print(
        json.dumps({"type": "result", "result": text, "usage": {"input_tokens": 1}}),
        flush=True,
    )
