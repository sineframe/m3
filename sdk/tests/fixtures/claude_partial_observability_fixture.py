#!/usr/bin/env python3
"""Real-shaped Claude partial stream and complete replay fixture."""

import json
import sys

if "--help" in sys.argv:
    print("--input-format stream-json --output-format stream-json")
    raise SystemExit(0)


def emit(value: object) -> None:
    if isinstance(value, dict) and value.get("type") in {
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    }:
        value = {"type": "stream_event", "event": value}
    print(json.dumps(value, separators=(",", ":")), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    if request.get("type") != "user":
        continue
    text = request["message"]["content"][0]["text"]
    tool_name = "Read_File" if text == "builtin" else "mcp__demo__echo"
    emit(
        {
            "type": "message_start",
            "message": {
                "id": "partial-1",
                "model": "partial-model",
                "usage": {"input_tokens": 2},
            },
        }
    )
    emit(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }
    )
    emit(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "think"},
        }
    )
    emit(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "signature_delta",
                "signature": "secret-signature-canary",
            },
        }
    )
    emit({"type": "content_block_stop", "index": 0})
    emit(
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": ""},
        }
    )
    emit(
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "same"},
        }
    )
    emit(
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "same"},
        }
    )
    emit({"type": "content_block_stop", "index": 1})
    emit(
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "tool_use",
                "id": "call-1",
                "name": tool_name,
            },
        }
    )
    emit(
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"text":"ok"}'},
        }
    )
    emit({"type": "content_block_stop", "index": 2})
    emit(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "usage": {"output_tokens": 3}},
        }
    )
    emit({"type": "message_stop"})
    if text != "partial-only":
        emit(
            {
                "type": "assistant",
                "message": {
                    "id": "partial-1",
                    "role": "assistant",
                    "model": "partial-model",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "think",
                            "signature": "secret-signature-canary",
                        },
                        {"type": "text", "text": "samesame"},
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": tool_name,
                            "input": {"text": "ok"},
                        },
                    ],
                },
            }
        )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "session_id": "partial-session",
            "usage": {"input_tokens": 2, "output_tokens": 3},
            "result": text,
        }
    )
