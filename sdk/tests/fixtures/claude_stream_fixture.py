#!/usr/bin/env python3
"""Deterministic stream-json process used by native adapter tests."""

from __future__ import annotations

import json
import sys
import time


if "--help" in sys.argv:
    print("--input-format stream-json --output-format stream-json")
    raise SystemExit(0)


for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    if request.get("type") != "user":
        continue
    content = request.get("message", {}).get("content", ())
    text = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    if text == "sleep":
        time.sleep(2)
    print(json.dumps({"type": "assistant", "delta": text}), flush=True)
    print(json.dumps({"type": "result", "result": text, "usage": {"input_tokens": 1}}), flush=True)
