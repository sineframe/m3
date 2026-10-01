---
title: "Configure credentials"
description: "Map endpoint, agent, judge, and M3 credentials to the process that needs each one."
---

# Configure credentials

Use this guide when a local MCP endpoint, agent, judge, or M3 upload needs a credential. The result is a test that sends a dummy credential to a local MCP endpoint and confirms that captured evidence does not contain it. Each credential has a separate destination; an endpoint key is not an agent key, judge key, or M3 personal access token (PAT).

## Requirements

Use Python 3.10 or later. From the M3 checkout root, install this development candidate and its pytest extra:

```sh
python -m pip install -e 'sdk[pytest]'
```

This checkout is an untagged development candidate, not the published `0.2.18` package. The example uses no external provider or account and sends only fixed dummy strings to loopback. The SDK's pytest extra installs pytest, pytest-asyncio, `httpx2`, and MCP dependencies.

## Complete local project

Create a directory named `credentials` and add these files. The project starts a loopback HTTP endpoint and stdio MCP server, maps named environment references to both, and asserts actual returned tool content and structured results. Its deterministic ACP fixture additionally checks that an environment reference reached the agent without echoing the key, that ACP received a temporary HOME, and that a real local MCP tool call returned its captured result.

`stdio_server.py`:

```python
from __future__ import annotations

import os

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="credential_check",
                description="Return whether the expected dummy service credential arrived.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult:
    expected = os.environ.get("DEMO_SERVICE_TOKEN")
    if params.name != "credential_check" or expected != "dummy-service-token":
        return types.CallToolResult(
            content=[types.TextContent(text="credential check failed")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="stdio credential accepted")],
        structured_content={"credential_present": True},
    )


async def main() -> None:
    server: Server[object] = Server(
        "credential-demo", on_list_tools=list_tools, on_call_tool=call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

`shipping_server.py`:

```python
from __future__ import annotations

from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="shipping_quote",
                description="Calculate a fixed local shipping quote.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "weight_kg": {"type": "number"},
                        "zone": {"type": "string", "enum": ["local"]},
                    },
                    "required": ["weight_kg", "zone"],
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    if (
        params.name != "shipping_quote"
        or (params.arguments or {}).get("weight_kg") != 2
        or (params.arguments or {}).get("zone") != "local"
    ):
        return types.CallToolResult(
            content=[types.TextContent(text="unexpected quote request")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="9.00 USD")],
        structured_content={"amount": 9.0, "currency": "USD"},
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping", on_list_tools=list_tools, on_call_tool=call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

`acp_credential_agent.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys


def send(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def call_shipping_tool(server: dict[str, object]) -> dict[str, object]:
    environment = {
        name: os.environ[name]
        for name in ("HOME", "PATH", "LANG", "LC_ALL", "TZ", "PYTHONIOENCODING")
        if name in os.environ
    }
    for item in server.get("env", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            environment[item["name"]] = str(item.get("value", ""))
    process = subprocess.Popen(
        [
            str(server["command"]),
            *(str(argument) for argument in server.get("args", [])),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=environment,
        cwd=server.get("cwd"),
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        requests = (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "credentials-example", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "shipping_quote",
                    "arguments": {"weight_kg": 2, "zone": "local"},
                },
            },
        )
        result: dict[str, object] = {}
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            if "id" in request:
                response = json.loads(process.stdout.readline())
                if "error" in response:
                    raise RuntimeError("MCP request failed")
                if request["method"] == "tools/call":
                    result = response["result"]
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


session_id = "credential-session"
servers: list[dict[str, object]] = []
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    identifier = request.get("id")
    params = request.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": identifier, "result": {"protocolVersion": 1}})
    elif method == "session/new":
        servers = params.get("mcpServers", [])
        send(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {"sessionId": session_id},
            }
        )
    elif method == "session/prompt":
        actual_key = os.environ.get("PROVIDER_API_KEY")
        expected_home = os.environ.get("EXPECTED_PARENT_HOME")
        isolated_home = os.environ.get("HOME")
        if not actual_key or actual_key != "dummy-agent-key":
            raise RuntimeError("ACP credential reference was not resolved")
        if not expected_home or expected_home == isolated_home:
            raise RuntimeError("ACP HOME was not isolated from parent HOME")
        server = next(item for item in servers if item.get("name") == "shipping")
        result = call_shipping_tool(server)
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "shipping-call",
                        "title": "shipping:shipping_quote",
                        "rawInput": {"weight_kg": 2, "zone": "local"},
                        "rawOutput": result,
                        "status": "completed",
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": "Dummy credential resolved; ACP HOME isolated.",
                        },
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {"stopReason": "end_turn"},
            }
        )
    elif identifier is not None:
        send({"jsonrpc": "2.0", "id": identifier, "result": {}})
```

`acp_manifest.json`:

```json
{
  "schema_version": "m3.harness.v1",
  "protocol": "acp",
  "protocol_version": 1,
  "command": "python",
  "args": ["acp_credential_agent.py"],
  "env": {
    "PROVIDER_API_KEY": "${M3_DEMO_AGENT_KEY}",
    "EXPECTED_PARENT_HOME": "${M3_DEMO_PARENT_HOME}"
  }
}
```

`test_credentials.py`:

```python
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.async_api import AsyncMCPTestKit
from m3.types import HTTPServer, SecretReference, StdioServer, TrustLevel

HERE = Path(__file__).resolve().parent
_PROTOCOL = "2025-11-25"
_DUMMY_TOKEN = "dummy-endpoint-token"


def _response(request: dict[str, Any], result: dict[str, Any]) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request["id"], "result": result},
        separators=(",", ":"),
    ).encode()


async def _start_server(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
) -> tuple[asyncio.AbstractServer, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1])


@pytest.mark.asyncio
async def test_http_secret_reference_reaches_local_endpoint_without_entering_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str]] = []

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            header_bytes = await reader.readuntil(b"\r\n\r\n")
            lines = header_bytes[:-4].split(b"\r\n")
            headers = {
                line.decode().split(":", 1)[0].lower(): line.decode()
                .split(":", 1)[1]
                .strip()
                for line in lines[1:]
            }
            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b"{}"
            request = json.loads(body)
            requests.append(
                (headers.get("x-demo-key", ""), headers.get("authorization", ""))
            )
            if (
                headers.get("x-demo-key") != "dummy-endpoint-token"
                or headers.get("authorization") != "Bearer dummy-bearer-token"
            ):
                status, payload = "401 Unauthorized", b"unauthorized"
            elif request.get("method") == "initialize":
                status = "200 OK"
                payload = _response(
                    request,
                    {
                        "protocolVersion": _PROTOCOL,
                        "capabilities": {},
                        "serverInfo": {"name": "credential-endpoint", "version": "1"},
                    },
                )
            elif request.get("method") == "tools/list":
                status = "200 OK"
                payload = _response(
                    request,
                    {
                        "tools": [
                            {
                                "name": "credential_check",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                )
            elif request.get("method") == "tools/call":
                status = "200 OK"
                payload = _response(
                    request,
                    {
                        "content": [
                            {"type": "text", "text": "http credential accepted"}
                        ],
                        "structuredContent": {"credential_present": True},
                        "isError": False,
                    },
                )
            else:
                status, payload = "202 Accepted", b""
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await _start_server(handler)
    monkeypatch.setenv("DEMO_ENDPOINT_KEY", _DUMMY_TOKEN)
    monkeypatch.setenv("DEMO_BEARER_TOKEN", "dummy-bearer-token")
    kit = AsyncMCPTestKit(env={})
    try:
        binding = HTTPServer(
            name="credential-endpoint",
            url=f"http://127.0.0.1:{port}/mcp",
            headers={
                "X-Demo-Key": SecretReference(
                    source="environment", name="DEMO_ENDPOINT_KEY"
                )
            },
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        async with kit.direct(
            binding,
            bearer_token=SecretReference(
                source="environment", name="DEMO_BEARER_TOKEN"
            ),
        ) as client:
            result = await client.call_tool("credential_check", {})
            assert result.content[0]["text"] == "http credential accepted"
            assert result.structured_content == {"credential_present": True}
            assert client.trace is not None
            assert "dummy-endpoint-token" not in repr(
                client.trace.model_dump(mode="json")
            )
            assert "dummy-bearer-token" not in repr(
                client.trace.model_dump(mode="json")
            )
        assert requests
        assert all(key == "dummy-endpoint-token" for key, _ in requests)
        assert all(auth == "Bearer dummy-bearer-token" for _, auth in requests)
    finally:
        await kit.aclose()
        server.close()
        await server.wait_closed()


def test_stdio_server_resolves_named_parent_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        kit = AsyncMCPTestKit(env={})
        try:
            binding = StdioServer(
                name="credential-stdio",
                command=sys.executable,
                args=("-u", str(HERE / "stdio_server.py")),
                cwd=str(HERE),
                environment={
                    "DEMO_SERVICE_TOKEN": SecretReference(
                        source="environment", name="M3_DEMO_SERVICE_KEY"
                    )
                },
            )
            # The test process sets this dummy value only for this subprocess test.
            monkeypatch.setenv("M3_DEMO_SERVICE_KEY", "dummy-service-token")
            async with kit.direct(binding) as client:
                result = await client.call_tool("credential_check", {})
                assert result.content[0]["text"] == "stdio credential accepted"
                assert result.structured_content == {"credential_present": True}
        finally:
            await kit.aclose()

    asyncio.run(run())


def test_acp_manifest_resolves_key_in_isolated_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M3_DEMO_AGENT_KEY", "dummy-agent-key")
    monkeypatch.setenv("M3_DEMO_PARENT_HOME", str(Path.home()))
    manifest = json.loads((HERE / "acp_manifest.json").read_text(encoding="utf-8"))
    manifest["command"] = sys.executable
    manifest["args"] = [str(HERE / "acp_credential_agent.py")]
    selection = {
        "harness": "acp",
        "models": ["fixture"],
        "manifest": manifest,
    }
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=("-u", str(HERE / "shipping_server.py")),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([selection])[0]
        result = agent.run(
            "Use shipping:shipping_quote with weight_kg 2 and zone local.",
            server=server,
            tools=["shipping:shipping_quote"],
            timeout=20,
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    assert result.trace_view is not None
    assert "dummy-agent-key" not in repr(result.trace_view)
    assert any(
        content.text == "Dummy credential resolved; ACP HOME isolated."
        for message in result.trace_view.messages
        for content in message.content
        if hasattr(content, "text")
    )
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    captured = result.trace_view.tool_calls[0].result.value
    assert captured.content[0].text == "9.00 USD"
    assert captured.structured_content.value == {"amount": 9.0, "currency": "USD"}
```

## Run and inspect the result

From the directory containing `credentials/`, run:

```sh
cd credentials
python -m pytest -q test_credentials.py
```

Captured candidate run:

```text
...                                                                      [100%]
3 passed in 2.02s
```

The HTTP test checks that the endpoint received both the referenced `X-Demo-Key` and bearer header, returned the actual tool result, and that neither dummy value appears in the trace representation. The stdio test maps parent variable `M3_DEMO_SERVICE_KEY` to child variable `DEMO_SERVICE_TOKEN` and checks the expected tool result. The ACP test maps a parent key into the child manifest, verifies temporary HOME differs from an explicitly referenced parent HOME, and checks the actual MCP tool result captured by M3. These tests use local fixtures; they do not verify a vendor endpoint, an agent login, or cloud credential storage.

Complete source project: [`sdk/examples/docs/credentials`](../../../sdk/examples/docs/credentials).

## Map credentials by consumer

For an SDK server definition, use `SecretReference(source="environment", name="NAME")` for a header or stdio environment variable. The default direct-transport resolver reads the named value from `os.environ`; `MCPTestKit(env=...)` is not the source for those references. HTTP `headers` may contain a literal non-secret value or a reference; prefer references for secrets. `bearer_token` is a separate option and M3 adds `Authorization: Bearer …`. If `headers` already contains an Authorization header, setting `bearer_token` fails with a typed `TransportConnectionError` during authentication. Use one source for that header. A custom `httpx2.Auth` callback is another HTTP option for challenge-based auth; it runs at the HTTP client boundary and is not serialized as a secret reference.

An ACP manifest names the *child variable* on the left and references an existing parent environment variable on the right. The complete `acp_manifest.json` and `acp_credential_agent.py` files above work with this test project. For your ACP agent, replace `command` and `args` with its executable and arguments, and change `PROVIDER_API_KEY` to the variable it documents:

```json
{
  "schema_version": "m3.harness.v1",
  "protocol": "acp",
  "protocol_version": 1,
  "command": "python",
  "args": ["agent.py"],
  "env": {
    "PROVIDER_API_KEY": "${M3_DEMO_PROVIDER_KEY}"
  }
}
```

The test sets `M3_DEMO_AGENT_KEY` and `M3_DEMO_PARENT_HOME` in its process for this run. The manifest validator accepts only `${ENV_NAME}` values, not literal credentials. ACP gets a temporary HOME/XDG environment, an executable search path derived from the agent command and system default, fixed locale/time values, and the explicitly configured child variables; it does not receive arbitrary ambient variables. Missing or empty referenced values fail before launch with a typed harness startup error. ACP does not download a native managed runtime. This mapping is source-supported; which variable a third-party agent expects and whether the provider accepts the key depend on that agent/provider and are not verified here. See [Connect an ACP-compatible agent](agents/acp-connect.md) and the [ACP reference](../reference/acp.md).

The local fixture starts its MCP subprocess with a small allowlist (`HOME`, `PATH`, and fixed locale/encoding values) plus the server-specific variables delivered in the ACP `mcpServers` entry. A real wrapper that launches a second process should make the same explicit choice; the ACP child credential is not copied to this MCP server.

Native Codex, Pi, Claude Code, and OpenCode selections use `credential_env` to map target names in the child process to source names in the invoking environment. CLI selection accepts `--credential-env codex:OPENAI_API_KEY=MY_AGENT_KEY` and `--credential-env judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY`; `--credential-env` does not inject credentials into an ACP manifest. Mapping syntax accepts `TARGET=SOURCE` with an optional `KIND:` scope. Supported inferred targets are adapter-specific:

| Adapter | Common provider key | Login-state behavior |
| --- | --- | --- |
| Codex | `OPENAI_API_KEY` | With no explicit credential references, M3 copies a regular, non-symlink host `auth.json` into its temporary `CODEX_HOME` with mode `0600`. If references are present, host auth is not copied. |
| Claude Code | `ANTHROPIC_API_KEY` | Uses isolated temporary HOME/XDG folders; host Claude login files are not copied. |
| Pi | `opencode/...`, `openai/...`, and `anthropic/...` map `OPENCODE_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`; `openai-codex/...` maps `PI_CODING_AGENT_DIR` (a provider data directory, not an API key). | No generic host CLI login copy; provider/model selection and credential needs depend on Pi configuration. |
| OpenCode | `opencode/...`, `openai/...`, and `anthropic/...` map `OPENCODE_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` respectively. | Uses isolated temporary HOME/XDG folders; host provider login files are not copied. |

The inferred source must be non-empty in the invoking process. The target receives that value; an explicit mapping names the source-to-target choice when your setup differs. `PI_CODING_AGENT_DIR` is a provider data directory, not a key. Missing/empty references fail at startup. A local readiness check does not authenticate with a provider. See the [credential reference](../reference/credentials.md) for exact mappings and process behavior.

The LLM judge uses `M3_JUDGE_API_KEY` by default, independently of the agent’s key. A judge with no configured credential reports `judge_credentials_missing`; it does not borrow an agent key. Use a separate CI secret and mapping when running a judge.

## Configure CI upload credentials

The M3 upload PAT is for publishing run data. It cannot be mapped into an agent or judge target. This GitHub Actions example assumes the checked-out M3 project has project configuration, a Codex agent test and an LLM judge test in `tests/`, and the root `uv` workspace used by this development candidate. It syncs the CLI, SDK, application, and judge extra from that workspace. The sample omits `M3_CONTROL_PLANE_URL` and uses the CLI's configured default; set that variable only when using a different HTTPS origin. This job can call the configured agent and judge providers and may incur their normal cost; it is a configuration example, not a live-verified workflow. It uses repository/environment secrets and does not print them. Replace the test path, model variable, harness, and credential targets to match your project. The `pytest` extra installs pytest and pytest-asyncio for the CLI's project test run. `uv sync --all-packages --extra judge --extra pytest` selects candidate workspace packages, not a published PyPI release.

```yaml
name: M3 tests
on:
  workflow_dispatch:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      M3_ACCESS_TOKEN: ${{ secrets.M3_ACCESS_TOKEN }}
      MY_AGENT_KEY: ${{ secrets.AGENT_PROVIDER_KEY }}
      MY_JUDGE_KEY: ${{ secrets.JUDGE_PROVIDER_KEY }}
      M3_AGENT_MODEL: ${{ vars.AGENT_MODEL }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync --all-packages --extra judge --extra pytest
      - run: >-
          uv run --all-packages --extra judge --extra pytest m3 ci test --upload
          --harness "codex=$M3_AGENT_MODEL"
          --credential-env codex:OPENAI_API_KEY=MY_AGENT_KEY
          --credential-env judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY
          -- tests/ -q
```

CI has no keyring fallback: when `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` is set, `m3 ci test --upload` requires `M3_ACCESS_TOKEN`. The CLI strips that PAT from the test child environment. `--credential-env` copies only the named source into the selected harness or judge target. Do not configure the PAT as either source. GitHub metadata retrieval is separate: `M3_GITHUB_TOKEN` is read only for GitHub API metadata downloads and is not the M3 upload token.

For local CLI use, `m3 auth login` opens the M3 sign-in flow and stores a PAT in a supported OS credential manager. `m3 auth status` reports local presence, not server validity. `m3 auth logout` removes the local saved token; it does not revoke copies already exported elsewhere. The sign-in page says older developer tokens remain active until revoked there. Never run those account operations with a real user’s credentials as part of a docs test. CI should use an explicitly stored secret and rotate/revoke it using your organization’s normal secret-management procedure.

`m3 ci test --env-file PATH` reads only the file explicitly named. It does not discover `.env` automatically. Ambient environment wins over values in that file, even when the ambient value is empty; an empty `M3_ACCESS_TOKEN` is an error. Dotenv interpolation is disabled. The SDK itself does not load `.env`; it reads named values from `os.environ` or a resolver supplied to the direct client. Missing or empty SDK references raise typed authentication/startup failures. See the [credential reference](../reference/credentials.md), [CI guide](ci/run.md), and [multiple harnesses guide](agents/multiple-harnesses.md).

## Common failures

- An unresolved endpoint reference fails at connection with `TransportConnectionError` in the authentication phase. Check the exact source name in the environment passed to the process.
- A native or ACP credential reference can be syntactically valid but unavailable at launch. Use readiness to check local configuration, then inspect the typed startup failure; readiness does not contact the external provider.
- A present but malformed M3 PAT is rejected before upload. In CI, provide `M3_ACCESS_TOKEN` explicitly and keep it separate from test credentials.
- An HTTP `Authorization` header and `bearer_token` conflict. Keep one; do not depend on header ordering.

## Evidence and limits

Evidence: development candidate from untagged commit `25738ca` plus this docs change; source symbols `SecretReference`, `resolve_headers`, `EnvironmentSecretResolver`, `_isolated_environment`, `_credential_refs`, `validate_credential_mappings`, `resolved_environment`, and `access_token`; local project test `sdk/examples/docs/credentials/test_credentials.py` passed from a clean directory outside the repository on macOS arm64, Python 3.14.7 (`3 passed in 2.02s`); CLI tests `cli/tests/test_ci_credentials.py` and `cli/tests/test_cli_credentials_integration.py` passed (`20 passed in 34.30s`) in an isolated candidate environment with CLI/app and judge dependencies. The dummy request checks the local credential path and trace representation only. External agent/provider login and authentication, CI secret-store behavior, OS keyring backends on Windows/Linux, remote M3 PAT validity, revocation propagation, and custom auth callbacks against a real challenge server were not exercised.
