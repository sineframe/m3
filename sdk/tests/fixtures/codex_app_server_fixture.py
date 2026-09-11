#!/usr/bin/env python3
"""Deterministic Codex App Server JSONL fixture for native adapter tests."""

from __future__ import annotations

import json
import sys

if "--help" in sys.argv:
    print("codex app-server --help")
    raise SystemExit(0)

thread = "fixture-thread"
turn = 0
for line in sys.stdin:
    try:
        frame = json.loads(line)
    except json.JSONDecodeError:
        continue
    method = frame.get("method")
    ident = frame.get("id")
    if method == "initialize":
        print(
            json.dumps(
                {"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": 1}}
            ),
            flush=True,
        )
    elif method == "thread/start":
        print(
            json.dumps(
                {"jsonrpc": "2.0", "id": ident, "result": {"thread": {"id": thread}}}
            ),
            flush=True,
        )
    elif method == "turn/start":
        turn += 1
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "result": {"turn": {"id": f"fixture-turn-{turn}"}},
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread,
                        "turnId": f"fixture-turn-{turn}",
                        "delta": "fixture response",
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": thread,
                        "turnId": f"fixture-turn-{turn}",
                        "tokenUsage": {
                            "last": {
                                "inputTokens": 1,
                                "outputTokens": 2,
                                "cachedInputTokens": 0,
                                "cacheWriteInputTokens": 0,
                                "reasoningOutputTokens": 0,
                                "totalTokens": 3,
                            },
                            "total": {
                                "inputTokens": 1,
                                "outputTokens": 2,
                                "cachedInputTokens": 0,
                                "cacheWriteInputTokens": 0,
                                "reasoningOutputTokens": 0,
                                "totalTokens": 3,
                            },
                            "modelContextWindow": 8192,
                        },
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread,
                        "turn": {
                            "id": f"fixture-turn-{turn}",
                            "status": "completed",
                            "model": "fixture",
                        },
                    },
                }
            ),
            flush=True,
        )
