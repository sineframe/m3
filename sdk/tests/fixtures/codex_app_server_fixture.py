#!/usr/bin/env python3
"""Deterministic Codex App Server JSONL fixture for native adapter tests."""

from __future__ import annotations

import json
import os
import sys

if os.environ.get("M3_DOCS_LOCAL_PROVIDER") == "1":
    from docs_live_mcp_client import codex_call, prompt_text
else:
    codex_call = None
    prompt_text = None

if "--version" in sys.argv:
    print(f"codex-cli {os.environ.get('M3_DOCS_FIXTURE_VERSION', '0.156.1')}")
    raise SystemExit(0)

if "--help" in sys.argv:
    print("codex app-server --help")
    raise SystemExit(0)

thread = "fixture-thread"
turn = 0
approval = os.environ.get("M3_CODEX_FIXTURE_APPROVAL") == "1"
# "none" starts logged out, like a fresh CODEX_HOME without auth.json.
account = (
    None
    if os.environ.get("M3_CODEX_FIXTURE_ACCOUNT") == "none"
    else {"type": "chatgpt", "email": "fixture@example.com", "planType": "plus"}
)
reject_login = os.environ.get("M3_CODEX_FIXTURE_REJECT_LOGIN") == "1"
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
    elif method == "account/login/start":
        params = frame.get("params", {})
        if reject_login or params.get("type") != "apiKey" or not params.get("apiKey"):
            reply = {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": -32600, "message": "login rejected"},
            }
        else:
            account = {"type": "apiKey"}
            reply = {"jsonrpc": "2.0", "id": ident, "result": {"type": "apiKey"}}
        print(json.dumps(reply), flush=True)
    elif method == "account/read":
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "result": {"account": account, "requiresOpenaiAuth": True},
                }
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
        if codex_call is not None and prompt_text is not None:
            prompt = prompt_text(frame.get("params", {}))
            server, tool, arguments, result = codex_call(prompt)
            call_id = f"fixture-call-{turn}"
            print(
                json.dumps(
                    {
                        "method": "item/started",
                        "params": {
                            "threadId": thread,
                            "turnId": f"fixture-turn-{turn}",
                            "item": {
                                "type": "mcpToolCall",
                                "id": call_id,
                                "server": server,
                                "tool": tool,
                                "arguments": arguments,
                                "status": "inProgress",
                            },
                        },
                    }
                ),
                flush=True,
            )
            print(
                json.dumps(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread,
                            "turnId": f"fixture-turn-{turn}",
                            "item": {
                                "type": "mcpToolCall",
                                "id": call_id,
                                "server": server,
                                "tool": tool,
                                "arguments": arguments,
                                "status": "completed",
                                "result": result,
                            },
                        },
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
                            "delta": "The local fixture completed the requested MCP call.",
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
            continue
        if approval:
            arguments = {"weight_kg": 2, "zone": "local"}
            print(
                json.dumps(
                    {
                        "method": "item/started",
                        "params": {
                            "item": {
                                "type": "mcpToolCall",
                                "id": "fixture-call",
                                "server": "fixture",
                                "tool": "shipping_quote",
                                "arguments": arguments,
                                "status": "inProgress",
                            }
                        },
                    }
                ),
                flush=True,
            )
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 500,
                        "method": "mcpServer/elicitation/request",
                        "params": {
                            "threadId": thread,
                            "turnId": f"fixture-turn-{turn}",
                            "serverName": "fixture",
                            "_meta": {
                                "codex_approval_kind": "mcp_tool_call",
                                "tool_params": arguments,
                            },
                            "message": "Allow the fixture MCP server to run tool?",
                            "mode": "form",
                            "requestedSchema": {"type": "object", "properties": {}},
                        },
                    }
                ),
                flush=True,
            )
            continue
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
    elif approval and ident == 500 and method is None:
        accepted = frame.get("result", {}).get("action") == "accept"
        if accepted:
            print(
                json.dumps(
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "mcpToolCall",
                                "id": "fixture-call",
                                "server": "fixture",
                                "tool": "shipping_quote",
                                "arguments": {"weight_kg": 2, "zone": "local"},
                                "status": "completed",
                                "result": {"amount": 9, "currency": "USD"},
                            }
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
                        },
                    },
                }
            ),
            flush=True,
        )
