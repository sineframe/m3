#!/usr/bin/env python3
"""Print a compact view of a run_scenario.py transcript: tool calls, denials, text.

Usage: python3 benchmarks/directory-readiness/show_transcript.py TRANSCRIPT [--width N]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--width", type=int, default=160)
    args = parser.parse_args()
    width = args.width
    for raw in args.transcript.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            print("RAW ", raw[:width])
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if isinstance(message, dict):
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_use":
                    print("TOOL", block["name"], json.dumps(block["input"])[:width])
                elif kind == "text":
                    print("TEXT", block["text"][:width].replace("\n", " "))
                elif kind == "tool_result" and block.get("is_error"):
                    content = block.get("content")
                    text = content if isinstance(content, str) else json.dumps(content)
                    print("ERR ", text[:width].replace("\n", " "))
        elif event.get("type") == "result":
            print("DONE", event.get("subtype"), str(event.get("result"))[:width])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
