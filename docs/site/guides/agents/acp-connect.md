---
title: "Connect an ACP-compatible agent"
description: "Run a local test through an already available ACP agent and assert its MCP result."
---

# Connect an ACP-compatible agent

Connect an agent that already implements ACP when you want M3 to launch it for a test and record the MCP interaction. This complete local example starts a deterministic ACP fixture and a local shipping server. It makes no provider request. The fixture is a small teaching process, not a general ACP conformance test or an LLM.

## Requirements

Use Python 3.10 or later. From the repository root, install this development candidate with pytest:

```sh
python -m pip install -e 'sdk[pytest]'
```

The project uses the ACP dependency pinned by the SDK (`agent-client-protocol==0.12.1`). The local test uses no API key. It needs a process-capable environment that can launch Python subprocesses. Installing `sf-m3[pytest]` from the package index selects a published release; this guide’s candidate comes from the checkout command above.

## Complete local project

Create a directory named `acp-connect` and add these files.

`shipping_server.py`:

```python
from __future__ import annotations

import json
import sys


def reply(message: dict[str, object]) -> None:
    print(json.dumps(message, separators=(",", ":")), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    identifier = request.get("id")
    if method == "initialize":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "shipping", "version": "1"},
                },
            }
        )
    elif method == "tools/list":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "tools": [
                        {
                            "name": "shipping_quote",
                            "description": "Calculate a deterministic shipping quote",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "weight_kg": {"type": "number"},
                                    "zone": {
                                        "type": "string",
                                        "enum": ["local", "regional"],
                                    },
                                },
                                "required": ["weight_kg", "zone"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        arguments = request.get("params", {}).get("arguments", {})
        rate = 2 if arguments["zone"] == "local" else 3.5
        amount = round(5 + float(arguments["weight_kg"]) * rate, 2)
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "content": [{"type": "text", "text": f"{amount:.2f} USD"}],
                    "structuredContent": {"amount": amount, "currency": "USD"},
                },
            }
        )
    elif identifier is not None:
        reply({"jsonrpc": "2.0", "id": identifier, "result": {}})
```

`deterministic_acp_agent.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys


def send(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def call_mcp(
    server: dict[str, object],
    method: str,
    params: dict[str, object],
    request_id: int,
) -> dict[str, object]:
    command = str(server["command"])
    environment = os.environ.copy()
    for item in server.get("env", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            environment[item["name"]] = str(item.get("value", ""))
    process = subprocess.Popen(
        [command, *(str(arg) for arg in server.get("args", []))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=environment,
        cwd=server.get("cwd"),
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "local-acp-example", "version": "1"},
            },
        }
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if "error" in response:
            raise RuntimeError("MCP initialization failed")
        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        for identifier, current_method, current_params in (
            (2, "tools/list", {}),
            (request_id, method, params),
        ):
            request = {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": current_method,
                "params": current_params,
            }
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            result = json.loads(process.stdout.readline())
            if "error" in result:
                raise RuntimeError("MCP request failed")
        return result["result"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


session_id = "local-acp-session"
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
        send({"jsonrpc": "2.0", "id": identifier, "result": {"sessionId": session_id}})
    elif method == "session/prompt":
        instruction = json.loads(
            " ".join(
                item["text"]
                for item in params.get("prompt", [])
                if item.get("type") == "text"
            )
        )
        server = next(item for item in servers if item["name"] == instruction["server"])
        result = call_mcp(
            server,
            "tools/call",
            {"name": instruction["tool"], "arguments": instruction["arguments"]},
            3,
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "shipping-call-1",
                        "title": f"{instruction['server']}:{instruction['tool']}",
                        "rawInput": instruction["arguments"],
                        "rawOutput": result,
                        "status": "completed",
                    },
                },
            }
        )
        text = "".join(
            item.get("text", "")
            for item in result.get("content", [])
            if isinstance(item, dict)
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            }
        )
        send({"jsonrpc": "2.0", "id": identifier, "result": {"stopReason": "end_turn"}})
    elif identifier is not None:
        send({"jsonrpc": "2.0", "id": identifier, "result": {}})
```

`test_connect.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_existing_local_acp_agent_calls_shipping_tool() -> None:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": sys.executable,
        "args": [str(HERE / "deterministic_acp_agent.py")],
    }
    selection = {"harness": "acp", "models": ["fixture"], "manifest": manifest}
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    prompt = json.dumps(
        {
            "server": "shipping",
            "tool": "shipping_quote",
            "arguments": {"weight_kg": 2, "zone": "local"},
        }
    )
    with MCPTestKit(env={}) as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["shipping:shipping_quote"], timeout=20
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    captured = result.trace_view.tool_calls[0].result.value
    assert captured.content[0].text == "9.00 USD"
    assert captured.structured_content.value == {
        "amount": 9.0,
        "currency": "USD",
    }
```

## Run and inspect the result

Run the command from the `acp-connect` project directory:

```sh
python -m pytest -q test_connect.py
```

The verified local output is:

```text
1 passed
```

The execution assertion checks completion. `to_have_tool_call` checks the recorded tool name, server, arguments, success status, and count. The final assertion reads the recorded MCP tool result from M3’s trace; it checks the server’s `9.00 USD` response. The ACP message alone is not used as proof.

The sample `tools` selection asks M3 to enforce the named tool through its capture proxy. Enforcement readiness requires proof that the active capture path applies the portable policy. ACP does not provide a portable way to restrict exact tools within an agent; see [tool-policy limits](../../reference/acp.md#tool-policy-and-evidence).

Complete source project: [`sdk/examples/docs/acp-connect`](../../../../sdk/examples/docs/acp-connect).

## Use an externally supplied agent

For an externally supplied provider agent, keep the local project and add this complete `test_connect_external.py`. It reads the executable, child credential variable name, model, and secret from your environment. The manifest contains only a reference to the secret. Set those values using [credential configuration](../credentials.md); do not place the credential value in the file.

`test_connect_external.py`:

```python
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_external_acp_agent_calls_shipping_tool() -> None:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": os.environ["ACP_AGENT_COMMAND"],
        "args": json.loads(os.environ.get("ACP_AGENT_ARGS", "[]")),
        "env": {os.environ["ACP_AGENT_CREDENTIAL_ENV"]: "${M3_DOCS_PROVIDER_API_KEY}"},
    }
    selection = {
        "harness": "acp",
        "models": [os.environ["M3_DOCS_AGENT_MODEL"]],
        "manifest": manifest,
    }
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    prompt = (
        "Call shipping_quote once for weight_kg 2 in zone local. "
        "Return the amount from the tool result."
    )
    with MCPTestKit() as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["shipping:shipping_quote"], timeout=120
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    captured = result.trace_view.tool_calls[0].result.value
    assert captured.content[0].text == "9.00 USD"
```

Run this variation from the `acp-connect` project directory with `python -m pytest -q test_connect_external.py`. It has not been verified here. Its model, credential variable, session options, required MCP transport, approval behavior, and provider costs depend on the selected agent. Confirm those requirements with the agent vendor before running it. The assertion proves the shipping MCP server response was captured; it does not establish that the provider’s model behavior is correct.

If preflight reports `acp_executable_missing`, check that the command resolves on the test machine. If startup reports a missing environment reference, set the referenced parent variable. A stale `agent_mode_id` or `session_config` fails session startup; use values advertised by that agent in `session/new`.

Next: [expose a custom agent through ACP](acp-wrapper.md), or compare ACP’s limits with [native harnesses](harnesses.md). Exact fields and failure behavior are in the [ACP reference](../../reference/acp.md).
