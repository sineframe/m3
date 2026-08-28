"""Executable ACP fixture shared by direct SDK and legacy integration tests."""

from __future__ import annotations

import stat
import textwrap
from pathlib import Path


def probe_agent(path: Path, behavior: str = "ok") -> str:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(f'''\
        import json, re, subprocess, sys
        from urllib.request import Request, build_opener, ProxyHandler
        from urllib.parse import urlsplit, urlunsplit
        opener = build_opener(ProxyHandler({{}})); server = None; seq = 0
        behavior = {behavior!r}
        def send(x): print(json.dumps(x, separators=(",", ":")), flush=True)
        def rpc_stdio(method, params):
            global seq
            seq += 1; q = {{"jsonrpc":"2.0", "id":seq, "method":method, "params":params}}
            server["proc"].stdin.write((json.dumps(q)+"\\n").encode()); server["proc"].stdin.flush()
            return json.loads(server["proc"].stdout.readline())
        def rpc_http(method, params):
            global seq
            seq += 1; body = json.dumps({{"jsonrpc":"2.0", "id":seq, "method":method, "params":params}}).encode()
            if server["type"] == "http":
                return json.loads(opener.open(Request(server["url"], data=body, headers={{"Content-Type":"application/json"}}, method="POST"), timeout=10).read())
            stream = opener.open(server["url"], timeout=10); endpoint = None
            while True:
                line = stream.readline().decode()
                if not line: break
                if line.startswith("data:"): endpoint = line.split(":", 1)[1].strip(); break
            if endpoint.startswith("/"):
                base = urlsplit(server["url"]); endpoint = urlunsplit((base.scheme, base.netloc, endpoint, "", ""))
            opener.open(Request(endpoint, data=body, headers={{"Content-Type":"application/json"}}, method="POST"), timeout=10).read()
            while True:
                line = stream.readline().decode()
                if not line: break
                if line.startswith("data:"): return json.loads(line.split(":", 1)[1].strip())
            raise RuntimeError("missing SSE data")
        def rpc(method, params):
            return rpc_stdio(method, params) if server.get("type", "stdio") == "stdio" else rpc_http(method, params)
        for line in sys.stdin:
            request = json.loads(line); method = request.get("method"); ident = request.get("id"); params = request.get("params") or {{}}
            if method == "initialize":
                caps = {{"mcpCapabilities": {{"http": True, "sse": True}}}}
                identity = {{}} if behavior == "no_identity" else {{"agentInfo":{{"name":"probe-fixture", "version":"1"}}}}
                send({{"jsonrpc":"2.0", "id":ident, "result":{{"protocolVersion":1, "agentCapabilities":caps, **identity}}}})
            elif method == "session/new":
                server = params["mcpServers"][0].copy(); server["type"] = server.get("type", "stdio")
                if server["type"] == "stdio": server["proc"] = subprocess.Popen([server["command"], *server.get("args", [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                send({{"jsonrpc":"2.0", "id":ident, "result":{{"sessionId":"probe-session", "modes":{{"currentModeId":"default", "availableModes":[{{"id":"mode-a", "name":"Mode A"}}]}}, "configOptions":[{{"id":"quality", "name":"Quality", "type":"select", "options":[{{"value":"high", "name":"High"}}]}}]}}}})
            elif method == "session/set_mode":
                send({{"jsonrpc":"2.0", "id":ident, "result":{{"currentModeId":params.get("modeId")}}}})
            elif method == "session/set_config_option":
                send({{"jsonrpc":"2.0", "id":ident, "result":{{"configOptions":[]}}}})
            elif method == "session/prompt":
                nonce = re.search(r"(mcp-pal-probe-[0-9a-f]+)", params["prompt"][0]["text"]).group(1)
                rpc("initialize", {{"protocolVersion":"2024-11-05", "capabilities":{{}}, "clientInfo":{{"name":"fixture", "version":"1"}}}})
                rpc("tools/list", {{}})
                call = rpc("tools/call", {{"name":"echo", "arguments":{{"text":nonce}}}})
                if behavior == "wrong_args": rpc("tools/call", {{"name":"echo", "arguments":{{"text":"wrong"}}}})
                shown = "wrong" if behavior == "wrong_result" else nonce
                if behavior != "no_update": send({{"jsonrpc":"2.0", "method":"session/update", "params":{{"sessionId":"probe-session", "update":{{"sessionUpdate":"tool_call_update", "toolCallId":"call", "status":"completed", "content":[{{"type":"content", "content":{{"type":"text", "text":shown}}}}]}}}}}})
                chunks = [shown[:len(shown)//2], shown[len(shown)//2:]] if behavior == "split_message" else [shown]
                for chunk in chunks: send({{"jsonrpc":"2.0", "method":"session/update", "params":{{"sessionId":"probe-session", "update":{{"sessionUpdate":"agent_message_chunk", "content":{{"type":"text", "text":chunk}}}}}}}})
                stop = "max_tokens" if behavior == "wrong_stop" else "end_turn"
                send({{"jsonrpc":"2.0", "id":ident, "result":{{"stopReason":stop}}}})
'''))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return str(path)
