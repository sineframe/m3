#!/usr/bin/env python3
"""Deterministic Pi RPC fixture for native adapter tests."""

from __future__ import annotations
import json
import sys
import os
import time

if "--help" in sys.argv:
    print("pi --mode rpc --help")
    raise SystemExit(0)

for line in sys.stdin:
    try:
        frame = json.loads(line)
    except json.JSONDecodeError:
        continue
    if frame.get("type") == "get_state":
        if os.environ.get("MCP_PAL_PI_FIXTURE_STARTUP_EVENT") == "1":
            print(
                json.dumps(
                    {
                        "type": "session_start",
                        "data": {"sessionId": "fixture-session"},
                    }
                ),
                flush=True,
            )
        print(
            json.dumps(
                {
                    "type": "response",
                    "command": "get_state",
                    "success": True,
                    "data": {
                        "sessionId": "fixture-session",
                        "model": {
                            "id": "fixture-model",
                            "provider": "fixture-provider",
                        },
                    },
                }
            ),
            flush=True,
        )
    elif frame.get("type") == "prompt":
        if os.environ.get("MCP_PAL_PI_FIXTURE_BLOCK") == "1":
            time.sleep(2)
        print(
            json.dumps(
                {
                    "type": "response",
                    "command": "prompt",
                    "id": frame.get("id"),
                    "success": True,
                }
            ),
            flush=True,
        )
        if os.environ.get("MCP_PAL_PI_FIXTURE_ERROR") == "1":
            print(
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "stopReason": "error",
                            "errorMessage": "fixture provider secret",
                        },
                    }
                ),
                flush=True,
            )
            print(json.dumps({"type": "agent_settled"}), flush=True)
            continue
        print(
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "fixture response",
                    },
                    "usage": {"input": 1, "output": 2, "totalTokens": 3},
                }
            ),
            flush=True,
        )
        print(
            json.dumps({"type": "message_end", "message": {"stopReason": "stop"}}),
            flush=True,
        )
        print(json.dumps({"type": "agent_settled"}), flush=True)
    elif frame.get("type") == "abort":
        print(json.dumps({"type": "agent_settled"}), flush=True)
