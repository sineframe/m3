#!/usr/bin/env python3
"""Deterministic stream-json process used by native adapter tests."""

from __future__ import annotations

import json
import os
import sys
import time

if os.environ.get("M3_DOCS_LOCAL_PROVIDER") == "1":
    from docs_live_mcp_client import claude_call
else:
    claude_call = None

if "--version" in sys.argv:
    print(os.environ.get("M3_DOCS_FIXTURE_VERSION", "1.0.0"))
    raise SystemExit(0)

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
    text = "".join(
        block.get("text", "") for block in content if isinstance(block, dict)
    )
    if claude_call is not None:
        server, tool, _native_name, arguments, result = claude_call(text, sys.argv)
        call_id = "docs-claude-tool-1"
        frames = (
            {
                "type": "system",
                "subtype": "init",
                "session_id": "docs-session",
                "model": "fixture-model",
            },
            {
                "type": "assistant",
                "message": {
                    "id": "docs-message-1",
                    "role": "assistant",
                    "model": "fixture-model",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": f"mcp__{server}__{tool}",
                            "input": arguments,
                        }
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": result,
                        }
                    ],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "session_id": "docs-session",
                "model": "fixture-model",
                "stop_reason": "end_turn",
                "result": "The local fixture completed the requested MCP call.",
            },
        )
        for frame in frames:
            print(json.dumps(frame, separators=(",", ":")), flush=True)
        continue
    if text == "sleep":
        time.sleep(2)
    print(json.dumps({"type": "assistant", "delta": text}), flush=True)
    print(
        json.dumps({"type": "result", "result": text, "usage": {"input_tokens": 1}}),
        flush=True,
    )
