#!/usr/bin/env python3
"""Tiny dependency-free MCP stdio server for local/live smoke tests."""
import json
import sys

def reply(identifier, result=None, error=None):
    message={"jsonrpc":"2.0","id":identifier}
    message["error" if error else "result"] = error or result
    print(json.dumps(message,separators=(",",":")),flush=True)

for line in sys.stdin:
    try: request=json.loads(line)
    except json.JSONDecodeError: continue
    identifier=request.get("id"); method=request.get("method")
    if identifier is None: continue
    if method == "initialize":
        reply(identifier,{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"mcp-pal-echo","version":"0.1"}})
    elif method == "tools/list":
        reply(identifier,{"tools":[{"name":"echo","description":"Return the provided text unchanged.","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}]})
    elif method == "tools/call":
        text=request.get("params",{}).get("arguments",{}).get("text","")
        reply(identifier,{"content":[{"type":"text","text":text}],"isError":False})
    elif method == "ping": reply(identifier,{})
    else: reply(identifier,error={"code":-32601,"message":"Method not found"})
