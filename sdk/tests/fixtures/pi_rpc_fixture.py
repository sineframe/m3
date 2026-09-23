#!/usr/bin/env python3
"""Deterministic Pi RPC fixture for native adapter tests."""

from __future__ import annotations

import json
import os
import socket
import sys
import time

control_socket: socket.socket | None = None
control_host = os.environ.get("M3_PI_CONTROL_HOST")
control_port = os.environ.get("M3_PI_CONTROL_PORT")
control_session = os.environ.get("M3_PI_CONTROL_SESSION")
control_token = os.environ.get("M3_PI_CONTROL_TOKEN")
if (
    control_host
    and control_port
    and control_session
    and control_token
    and os.environ.get("M3_PI_FIXTURE_NO_CONTROL") != "1"
):
    control_socket = socket.create_connection(
        (control_host, int(control_port)), timeout=5
    )
    control_socket.sendall(
        (
            json.dumps(
                {
                    "type": "hello",
                    "version": 1,
                    "session_id": control_session,
                    "token": control_token,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    )
    hello = b""
    while not hello.endswith(b"\n"):
        hello += control_socket.recv(4096)
    if json.loads(hello).get("accepted") is not True:
        raise RuntimeError("Pi control handshake failed")

if "--version" in sys.argv:
    print("0.85.1")
    raise SystemExit(0)

if "--help" in sys.argv:
    print("pi --mode rpc --help")
    raise SystemExit(0)

for line in sys.stdin:
    try:
        frame = json.loads(line)
    except json.JSONDecodeError:
        continue
    if frame.get("type") == "get_state":
        if os.environ.get("M3_PI_FIXTURE_STARTUP_EVENT") == "1":
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
        if os.environ.get("M3_PI_FIXTURE_BLOCK") == "1":
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
        if os.environ.get("M3_PI_FIXTURE_ERROR") == "1":
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
