"""Real ACP -> local HTTP/SSE MCP proxy integration tests.

The agent fixture below is intentionally a subprocess, rather than an SDK
mock.  It consumes the server from ``session/new`` and performs the complete
MCP initialize/list/call exchange through the URL supplied by ACP.
"""

import asyncio
import json
import os
import socket
import stat
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

from mcp_pal.harness.acp import AcpHarnessRunner
from mcp_pal.harness.base import AcpRunSpec


def executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class LocalMcpUpstream:
    """Small deterministic MCP server for streamable HTTP and legacy SSE."""

    def __init__(self, transport: str):
        self.transport = transport
        self.server: asyncio.AbstractServer | None = None
        self.requests: list[dict] = []
        self._sse_writer: asyncio.StreamWriter | None = None
        self._sse_result: asyncio.Future | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._connection, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/mcp" if self.transport == "http" else f"http://127.0.0.1:{port}/sse"

    async def close(self) -> None:
        if self._sse_writer:
            self._sse_writer.close()
            await self._sse_writer.wait_closed()
            self._sse_writer = None
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            header_bytes = await reader.readuntil(b"\r\n\r\n")
            header_lines = header_bytes.decode("latin1").split("\r\n")
            method, path, _ = header_lines[0].split(" ", 2)
            headers = {}
            for line in header_lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            payload = json.loads(body) if body else None
            self.requests.append({"method": method, "path": path, "headers": headers, "payload": payload})
            if self.transport == "sse":
                await self._handle_sse(method, path, payload, writer)
            else:
                await self._handle_http(payload, writer)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            # The SSE GET remains open until its tool result is delivered.
            if writer is not self._sse_writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

    @staticmethod
    def _mcp_result(payload: dict) -> dict:
        method = payload.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "local-fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
        elif method == "tools/call":
            text = (payload.get("params") or {}).get("arguments", {}).get("text", "")
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            result = {}
        return {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}

    async def _handle_http(self, payload: dict | None, writer: asyncio.StreamWriter):
        response = self._mcp_result(payload or {})
        encoded = json.dumps(response, separators=(",", ":")).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(encoded)).encode() + b"\r\nConnection: close\r\n\r\n" + encoded)
        await writer.drain()

    async def _handle_sse(self, method: str, path: str, payload: dict | None, writer: asyncio.StreamWriter):
        if method == "GET":
            self._sse_writer = writer
            self._sse_result = asyncio.get_running_loop().create_future()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n")
            writer.write(b"event: endpoint\ndata: /message?session=fixture\n\n")
            await writer.drain()
            response = await self._sse_result
            writer.write(b"data: " + json.dumps(response, separators=(",", ":")).encode() + b"\n\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            self._sse_writer = None
            return
        if method == "POST":
            response = self._mcp_result(payload or {})
            if self._sse_result and not self._sse_result.done():
                self._sse_result.set_result(response)
            writer.write(b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()


def _agent_body(record_path: Path, transport: str) -> str:
    # Keep this fixture dependency-free so it exercises exactly the injected
    # ACP server definition and the proxy's wire behavior.
    return f'''
import json, sys
from urllib.request import Request, build_opener, ProxyHandler
record = {str(record_path)!r}
transport = {transport!r}
opener = build_opener(ProxyHandler({{}}))
server = None
counter = 0
def send(message):
    print(json.dumps(message, separators=(",", ":")), flush=True)
def http_rpc(url, method, params):
    global counter
    counter += 1
    body = json.dumps({{"jsonrpc":"2.0", "id":counter, "method":method, "params":params}}).encode()
    req = Request(url, data=body, headers={{"Content-Type":"application/json"}}, method="POST")
    if transport == "http":
        return json.loads(opener.open(req, timeout=10).read())
    # Legacy SSE uses a GET event stream for responses and POST for requests.
    stream = opener.open(url, timeout=10)
    endpoint = None
    while True:
        line = stream.readline().decode()
        if not line: break
        if line.startswith("data:"):
            endpoint = line.split(":", 1)[1].strip()
            break
    if endpoint and endpoint.startswith("/"):
        from urllib.parse import urlsplit, urlunsplit
        base = urlsplit(url); endpoint = urlunsplit((base.scheme, base.netloc, endpoint, "", ""))
    opener.open(Request(endpoint, data=body, headers={{"Content-Type":"application/json"}}, method="POST"), timeout=10).read()
    while True:
        line = stream.readline().decode()
        if not line: break
        if line.startswith("data:"):
            return json.loads(line.split(":", 1)[1].strip())
    raise RuntimeError("SSE response missing")
for line in sys.stdin:
    request = json.loads(line); method = request.get("method"); ident = request.get("id")
    if method == "initialize":
        send({{"jsonrpc":"2.0", "id":ident, "result":{{"protocolVersion":1, "agentInfo":{{"name":"transport-fixture", "version":"1"}}, "agentCapabilities":{{"mcpCapabilities":{{transport:True}}}}}}}})
    elif method == "session/new":
        server = (request.get("params") or {{}}).get("mcpServers", [{{}}])[0]
        open(record, "w").write(json.dumps(server))
        send({{"jsonrpc":"2.0", "id":ident, "result":{{"sessionId":"fixture-session"}}}})
    elif method == "session/prompt":
        nonce = "nonce-http-sse-42"
        http_rpc(server["url"], "initialize", {{"protocolVersion":"2024-11-05", "capabilities":{{}}, "clientInfo":{{"name":"transport-fixture", "version":"1"}}}})
        http_rpc(server["url"], "tools/list", {{}})
        response = http_rpc(server["url"], "tools/call", {{"name":"echo", "arguments":{{"text":nonce}}}})
        send({{"jsonrpc":"2.0", "method":"session/update", "params":{{"sessionId":"fixture-session", "update":{{"sessionUpdate":"agent_thought_chunk", "content":{{"type":"text", "text":"thinking"}}}}}}}})
        send({{"jsonrpc":"2.0", "method":"session/update", "params":{{"sessionId":"fixture-session", "update":{{"sessionUpdate":"tool_call", "toolCallId":"call-1", "title":"echo"}}}}}})
        send({{"jsonrpc":"2.0", "method":"session/update", "params":{{"sessionId":"fixture-session", "update":{{"sessionUpdate":"tool_call_update", "toolCallId":"call-1", "status":"completed", "content":[{{"type":"content", "content":{{"type":"text", "text":response["result"]["content"][0]["text"]}}}}]}}}}}})
        send({{"jsonrpc":"2.0", "method":"session/update", "params":{{"sessionId":"fixture-session", "update":{{"sessionUpdate":"agent_message_chunk", "content":{{"type":"text", "text":response["result"]["content"][0]["text"]}}}}}}}})
        send({{"jsonrpc":"2.0", "id":ident, "result":{{"stopReason":"end_turn"}}}})
'''


async def _run_real_transport(tmp_path: Path, transport: str):
    upstream = LocalMcpUpstream(transport)
    upstream_url = await upstream.start()
    record_path = tmp_path / f"{transport}-server.json"
    agent = executable(tmp_path / f"{transport}-agent.py", _agent_body(record_path, transport))
    spec = AcpRunSpec(
        "call echo", "agent-default",
        {"mcpServers": {"fixture": {"type": transport, "url": upstream_url, "headers": {"Authorization": "Bearer upstream-secret"}}}},
        "fixture", {"command": agent}, timeout_seconds=10, allow_private_upstream=True,
    )
    runner = AcpHarnessRunner()
    created_workspaces = []
    import mcp_pal.harness.acp as acp_runtime
    real_mkdtemp = acp_runtime.tempfile.mkdtemp
    def tracked_mkdtemp(**kwargs):
        path = real_mkdtemp(**kwargs)
        created_workspaces.append(Path(path))
        return path
    try:
        with patch.object(acp_runtime.tempfile, "mkdtemp", side_effect=tracked_mkdtemp):
            result = await runner.run(spec)
        injected = json.loads(record_path.read_text())
        return result, injected, upstream, upstream_url, runner, created_workspaces
    finally:
        await upstream.close()


def _assert_real_round_trip(result, injected, upstream, transport, upstream_url):
    assert result.status == "completed", result.error
    assert result.final_text == "nonce-http-sse-42"
    assert result.transport == result.configured_transport == result.instrumented_transport == transport
    assert injected["type"] == transport
    assert injected["url"].startswith("http://127.0.0.1:") and injected["url"] != upstream_url
    # Credentials belong only to the proxy's upstream configuration; ACP gets
    # a local URL and an empty header list.
    assert injected.get("headers") == []
    assert all("upstream-secret" not in json.dumps(frame) for frame in result.event_records)
    payloads = [frame.get("payload") for frame in result.protocol_events if isinstance(frame.get("payload"), dict)]
    assert [payload.get("method") for payload in payloads if payload.get("method")] == ["initialize", "tools/list", "tools/call"]
    call = next(payload for payload in payloads if payload.get("method") == "tools/call")
    assert call["params"]["arguments"] == {"text": "nonce-http-sse-42"}
    response = next(payload for payload in payloads if payload.get("id") == call["id"] and payload.get("result"))
    assert response["result"]["content"][0]["text"] == "nonce-http-sse-42"
    request_frame = next(frame for frame in result.protocol_events if frame.get("payload") == call)
    response_frame = next(frame for frame in result.protocol_events if frame.get("payload") == response)
    latency_ms = float(response_frame["offset_ms"]) - float(request_frame["offset_ms"])
    assert latency_ms >= 0 and isinstance(latency_ms, float)
    assert any(req["payload"].get("method") == "tools/call" for req in upstream.requests if req.get("payload"))
    updates = [frame.get("payload", {}).get("params", {}).get("update", {}) for frame in result.event_records if frame.get("payload", {}).get("method") == "session/update"]
    assert {update.get("sessionUpdate") for update in updates} >= {"agent_thought_chunk", "tool_call", "tool_call_update", "agent_message_chunk"}
    assert "nonce-http-sse-42" in json.dumps(updates)
    assert any(frame.get("payload", {}).get("result", {}).get("stopReason") == "end_turn" for frame in result.event_records)


def _assert_proxy_gone(injected):
    host = injected["url"].split("//", 1)[1].split("/", 1)[0]
    port = int(host.rsplit(":", 1)[1])
    with socket.socket() as probe:
        probe.settimeout(1)
        try:
            probe.connect(("127.0.0.1", port))
        except OSError:
            return
    raise AssertionError("ACP HTTP proxy still accepting connections")


def test_http_real_acp_mcp_round_trip_through_local_proxy(tmp_path):
    result, injected, upstream, url, runner, workspaces = asyncio.run(_run_real_transport(tmp_path, "http"))
    _assert_real_round_trip(result, injected, upstream, "http", url)
    assert runner._process is None
    assert workspaces and all(not workspace.exists() for workspace in workspaces)
    _assert_proxy_gone(injected)


def test_legacy_sse_real_acp_mcp_round_trip_through_local_proxy(tmp_path):
    result, injected, upstream, url, runner, workspaces = asyncio.run(_run_real_transport(tmp_path, "sse"))
    _assert_real_round_trip(result, injected, upstream, "sse", url)
    assert runner._process is None
    assert workspaces and all(not workspace.exists() for workspace in workspaces)
    _assert_proxy_gone(injected)


def test_private_upstream_is_explicitly_opt_in(tmp_path):
    assert AcpRunSpec("p", "agent-default", {}, "x", {}).allow_private_upstream is False


def test_missing_advertised_http_capability_fails_before_session_new(tmp_path):
    marker = tmp_path / "session-new"
    agent = executable(tmp_path / "agent.py", f'''
import json, sys
for line in sys.stdin:
 request = json.loads(line)
 if request.get("method") == "initialize":
  print(json.dumps({{"jsonrpc":"2.0", "id":request["id"], "result":{{"protocolVersion":1, "agentCapabilities":{{}}}}}}), flush=True)
 elif request.get("method") == "session/new":
  open({str(marker)!r}, "w").write("called")
''')
    spec = AcpRunSpec("p", "agent-default", {"mcpServers": {"x": {"type": "http", "url": "http://127.0.0.1:9/mcp"}}}, "x", {"command": agent}, allow_private_upstream=True)
    result = asyncio.run(AcpHarnessRunner().run(spec))
    assert result.status == "failed" and result.error == "acp_transport_not_advertised: http"
    assert not marker.exists()


def test_failed_run_keeps_partial_acp_capture(tmp_path):
    agent = executable(tmp_path / "agent.py", "print('not-json', flush=True)")
    result = asyncio.run(AcpHarnessRunner().run(AcpRunSpec("p", "agent-default", {"mcpServers": {"x": {"command": "echo"}}}, "x", {"command": agent})))
    assert result.status == "failed"
    assert result.event_records


def test_cancelled_run_keeps_partial_capture_and_reaps_process(tmp_path):
    pid_file = tmp_path / "agent.pid"
    agent = executable(tmp_path / "hang.py", f'''
import json, os, sys, time
open({str(pid_file)!r}, "w").write(str(os.getpid()))
for line in sys.stdin:
 request = json.loads(line)
 if request.get("method") == "initialize": print(json.dumps({{"jsonrpc":"2.0", "id":request["id"], "result":{{"protocolVersion":1}}}}), flush=True)
 elif request.get("method") == "session/new": print(json.dumps({{"jsonrpc":"2.0", "id":request["id"], "result":{{"sessionId":"s"}}}}), flush=True)
 elif request.get("method") == "session/prompt": time.sleep(30)
''')
    runner = AcpHarnessRunner()
    async def cancel():
        task = asyncio.create_task(runner.run(AcpRunSpec("hang", "agent-default", {"mcpServers": {"x": {"command": "echo"}}}, "x", {"command": agent}, timeout_seconds=10)))
        await asyncio.sleep(.2)
        runner.request_cancel()
        return await task
    result = asyncio.run(cancel())
    assert result.status == "cancelled"
    assert result.event_records
    assert any(frame.get("payload", {}).get("method") == "session/cancel" for frame in result.event_records)
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("cancelled ACP process still alive")
