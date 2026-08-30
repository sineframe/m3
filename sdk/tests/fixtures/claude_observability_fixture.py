#!/usr/bin/env python3
"""Deterministic rich Claude stream-json fixture for typed observability."""

from __future__ import annotations

import json
import sys

if "--help" in sys.argv:
    print("--input-format stream-json --output-format stream-json")
    raise SystemExit(0)


def send(value: object) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    if request.get("type") != "user":
        continue
    text = "".join(
        item.get("text", "")
        for item in request.get("message", {}).get("content", ())
        if isinstance(item, dict)
    )
    send(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "claude-session",
            "model": "fixture-model",
        }
    )
    send(
        {
            "type": "assistant",
            "message": {
                "id": "msg-1",
                "role": "assistant",
                "model": "fixture-model",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "reasoning",
                        "signature": "opaque-signature",
                    },
                    {
                        "type": "thinking",
                        "thinking": "",
                        "signature": "encrypted-signature",
                    },
                    {"type": "text", "text": "before-tool"},
                    {
                        "type": "tool_use",
                        "id": "toolu-1",
                        "name": "mcp__e2e-mcp__echo",
                        "input": {"text": text},
                    },
                ],
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 4,
                },
            },
        }
    )
    send(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu-1",
                        "content": [{"type": "text", "text": text}],
                    }
                ],
            },
        }
    )
    send(
        {
            "type": "result",
            "subtype": "success",
            "session_id": "claude-session",
            "model": "fixture-model",
            "stop_reason": "end_turn",
            "service_tier": "standard",
            "duration_api_ms": 12.5,
            "total_cost_usd": 0.25,
            "usage": {
                "input_tokens": 2,
                "output_tokens": 3,
                "cache_read_input_tokens": 4,
            },
            "result": text,
        }
    )
