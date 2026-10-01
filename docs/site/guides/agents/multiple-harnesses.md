---
title: "Run the same test across agent harnesses"
description: "Select Codex, Pi, Claude Code, and OpenCode for one unchanged pytest test and compare the recorded tool call for each execution."
---

# Run the same test across agent harnesses

Use one pytest test and select multiple native harnesses at collection time.
The test below starts the same local MCP server and sends the same prompt to
every selected agent. It checks the requested tool, arguments, successful
status, and returned structured quote. The assertion reads the recorded tool
result; it does not require each harness to format its final prose identically.

## Requirements

Install `sf-m3[pytest]`, `sf-m3-cli`, and the four native command-line clients:
Codex CLI, Pi, Claude Code, and OpenCode. From the repository root, install
this development candidate, including its CLI package, before running the
example:

```sh
python -m pip install -e 'sdk[pytest]' -e app -e cli
```

Configure each client independently before setting its model selection:

| Harness | Login/configuration | Model identifier passed by M3 | Cost and approval |
| --- | --- | --- | --- |
| Codex CLI | Use `codex login` or the login method configured for that Codex installation. M3 copies the host `CODEX_HOME/auth.json` (default `~/.codex/auth.json`) to its temporary child home when no explicit credential map supplies auth. | Codex model identifier accepted by the installed CLI/account. M3 selects the configured string; it does not choose a default. | Provider billing applies. Review Codex's own MCP approval behavior. |
| Pi | Configure Pi's provider credentials in the environment or supported provider configuration. The installed CLI exposes `pi auth check --provider PROVIDER --model PROVIDER/MODEL`; it does not expose a `pi auth login` command. M3 launches with an isolated temporary home, so host Pi login/config state is not assumed present. The example maps a named provider credential into the child environment. | Pi accepts `provider/model` or a model ID; M3 passes the selected model to Pi's `--model` argument. | Provider billing applies. Review Pi's tool settings and approval behavior. |
| Claude Code | Sign in with `claude auth login` or map the environment credential required by its configured provider mode. M3 uses an isolated temporary home and MCP configuration; do not assume host login state is copied. | Claude Code identifier accepted by the installed CLI/account; M3 passes it as `--model`. | Provider billing applies. Review Claude Code's approval mode. Exact tool restrictions are not supported by this adapter. |
| OpenCode | Configure the provider with `opencode auth login` or the provider's supported environment/API-key setup. M3 starts an isolated OpenCode home and does not copy host settings. The example maps a named provider credential into the child environment. | Use `provider/model`, or set the provider separately in SDK configuration. M3 rejects a qualified provider that conflicts with an explicit provider. | Provider billing applies. Review OpenCode's provider and permission configuration. |

For non-interactive credential injection, export provider credentials under
environment variable names that are not printed or committed, and set each
`M3_DOCS_*_KEY_NAME` variable to the target name expected by that provider.
The commands below map those sources for Pi, Claude Code, and OpenCode. Codex
uses its native login copy. See [provider credentials](../credentials.md) for
adapter boundaries and startup behavior.
Choose model IDs that the selected account accepts. Check provider pricing
before running because the command sends eight live agent requests. Review
each client's tool approval policy before allowing execution.

This preview environment has Codex CLI 0.159.3, Pi 0.85.1, Claude Code 2.1.278,
and OpenCode 1.18.31 installed. It has no selected model values. Safe
authentication-status checks did not establish whether any native login is
available. No provider requests were made, so the live result table is
unverified for all four harnesses.

Set model identifiers for accounts you have configured. For example, in Bash
or zsh:

```sh
export M3_DOCS_CODEX_MODEL='<Codex model identifier>'
export M3_DOCS_PI_MODEL='<Pi provider/model identifier>'
export M3_DOCS_CLAUDE_MODEL='<Claude Code model identifier>'
export M3_DOCS_OPENCODE_MODEL='<OpenCode provider/model identifier>'
export M3_DOCS_PYTHON="$(command -v python)"
export M3_DOCS_PI_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_CLAUDE_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_OPENCODE_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_PI_API_KEY='<Pi provider credential>'
export M3_DOCS_CLAUDE_API_KEY='<Claude provider credential>'
export M3_DOCS_OPENCODE_API_KEY='<OpenCode provider credential>'
```

## Complete project

Create a `multi-harness` project with `shipping_server.py` and
`tests/test_agents.py` as shown. The server's only tool returns a structured
USD quote. One server is deliberately used so no harness has to filter a
larger server set. The test does not pass `tools=[...]`: native adapters use
their supported server/tool exposure policy, and Claude Code rejects M3's exact
tool-restriction option. This test verifies the tool the agent actually called.

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
                description="Calculate a deterministic shipping quote",
                input_schema={
                    "type": "object",
                    "properties": {
                        "weight_kg": {"type": "number", "exclusiveMinimum": 0},
                        "zone": {
                            "type": "string",
                            "enum": ["local", "regional", "international"],
                        },
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
    rates = {"local": 2.0, "regional": 3.5, "international": 7.0}
    arguments = params.arguments or {}
    weight = arguments.get("weight_kg")
    zone = arguments.get("zone")
    if params.name != "shipping_quote" or zone not in rates:
        return types.CallToolResult(
            content=[types.TextContent(text="unknown tool or shipping zone")],
            is_error=True,
        )
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
        return types.CallToolResult(
            content=[types.TextContent(text="weight_kg must be positive")],
            is_error=True,
        )
    quote = {"amount": round(5 + weight * rates[zone], 2), "currency": "USD"}
    return types.CallToolResult(
        content=[types.TextContent(text="Quote calculated")],
        structured_content=quote,
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

`tests/test_agents.py`:

```python
import sys
from pathlib import Path

import pytest
from m3 import ExecutionOutcome, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}

ACP_MANIFEST = {
    "schema_version": "m3.harness.v1",
    "protocol": "acp",
    "protocol_version": 1,
    "command": sys.executable,
    "args": [str(HERE.parent / "deterministic_acp_agent.py")],
}
pytestmark = pytest.mark.m3(
    suite_name="shipping",
    agents=[
        {"harness": "acp", "models": ["fixture"], "manifest": ACP_MANIFEST}
    ],
)


def test_selected_agent_calls_shipping_tool(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE.parent / "shipping_server.py"),),
        cwd=str(HERE.parent),
    )
    result = agent.run(PROMPT, server=server, timeout=180)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": EXPECTED_QUOTE},
        result_partial=True,
    )
```

Run this from the `multi-harness` project directory in Bash or zsh:

```sh
m3 test --python "$M3_DOCS_PYTHON" \
  --harness "codex=$M3_DOCS_CODEX_MODEL" \
  --harness "pi=$M3_DOCS_PI_MODEL" \
  --harness "claude_code=$M3_DOCS_CLAUDE_MODEL" \
  --harness "opencode=$M3_DOCS_OPENCODE_MODEL" \
  --credential-env "pi:${M3_DOCS_PI_KEY_NAME}=M3_DOCS_PI_API_KEY" \
  --credential-env "claude_code:${M3_DOCS_CLAUDE_KEY_NAME}=M3_DOCS_CLAUDE_API_KEY" \
  --credential-env "opencode:${M3_DOCS_OPENCODE_KEY_NAME}=M3_DOCS_OPENCODE_API_KEY" \
  --trials 2 -- tests/test_agents.py
```

The CLI selection expands one test across four harnesses and two trials, for
eight executions. The command syntax and selection count are verified from the
CLI parser; this is not evidence that live provider calls pass. Each execution
has its own identity. The selected cases belong to the same CLI run, so inspect
both execution and run provenance when comparing results. A failed case should
be attributed to its recorded harness, model, runtime identity, and error; the
selection does not silently skip an unavailable provider. `--trials 2` runs
each case twice and is not a retry policy. Changing the harness and its model
together also changes both agent and model, so this comparison cannot isolate
one variable.

Complete source project: [`sdk/examples/docs/agents-multiple-harnesses`](../../../../sdk/examples/docs/agents-multiple-harnesses).

## Extend the selection to ACP

The marker in `tests/test_agents.py` supplies an ACP manifest for a
deterministic local wrapper. Add this complete file beside
`shipping_server.py`. It accepts the same natural-language prompt and calls
the same one-tool server. M3 records the server result through its MCP proxy;
the wrapper reports that result in its ACP update, which M3 records separately.

`deterministic_acp_agent.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys

PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."


def send(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def call_tool(server: dict[str, object]) -> dict[str, object]:
    environment = os.environ.copy()
    for item in server.get("env", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            environment[item["name"]] = str(item.get("value", ""))
    process = subprocess.Popen(
        [str(server["command"]), *(str(value) for value in server.get("args", []))],
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
                    "clientInfo": {"name": "local-acp-fixture", "version": "1"},
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
                    raise RuntimeError("local MCP request failed")
                result = response.get("result", {})
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


session_id = "local-multi-harness-acp"
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
        prompt = " ".join(
            item["text"] for item in params.get("prompt", []) if item.get("type") == "text"
        )
        if prompt != PROMPT:
            raise RuntimeError("unexpected prompt for deterministic ACP fixture")
        server = next(item for item in servers if item["name"] == "shipping")
        result = call_tool(server)
        arguments = {"weight_kg": 2, "zone": "local"}
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "shipping-call-1",
                        "title": "shipping:shipping_quote",
                        "rawInput": arguments,
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
                            "text": "The local quote is 9.00 USD.",
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

Run only the ACP fixture twice from the project directory:

```sh
m3 test --python "$M3_DOCS_PYTHON" --harness "acp=fixture" --trials 2 -- tests/test_agents.py
```

The command uses the ACP CLI choice to select the matching marker manifest; it
does not need native-provider credentials. To add ACP as the fifth selection
to the four-native command above, include `--harness "acp=fixture"` before
`--trials 2`:

```sh
m3 test --python "$M3_DOCS_PYTHON" \
  --harness "codex=$M3_DOCS_CODEX_MODEL" \
  --harness "pi=$M3_DOCS_PI_MODEL" \
  --harness "claude_code=$M3_DOCS_CLAUDE_MODEL" \
  --harness "opencode=$M3_DOCS_OPENCODE_MODEL" \
  --harness "acp=fixture" \
  --credential-env "pi:${M3_DOCS_PI_KEY_NAME}=M3_DOCS_PI_API_KEY" \
  --credential-env "claude_code:${M3_DOCS_CLAUDE_KEY_NAME}=M3_DOCS_CLAUDE_API_KEY" \
  --credential-env "opencode:${M3_DOCS_OPENCODE_KEY_NAME}=M3_DOCS_OPENCODE_API_KEY" \
  --trials 2 -- tests/test_agents.py
```

The CLI selection matches the ACP marker entry by harness kind, preserving its
manifest and using the CLI model value. Five selections × two trials collect
ten executions. The ACP fixture is deterministic local code, not a native
runtime and not evidence of a live provider. Do not add `--runtime managed` to
this mixed selection because managed acquisition supports native adapters,
not ACP.

## Select managed pins through the SDK

For SDK-controlled runs, use a complete `MCPTestKit.agents()` selection list.
This variation pins each native adapter independently and keeps ACP on its
system process. Add `test_sdk_pins.py` beside the server and ACP fixture from
this project:

Set four explicit version variables and the provider variables required by
the accounts you selected. For this example, Pi and OpenCode use explicit
providers; provider key variable names are configurable because each provider
expects its own environment variable:

```sh
export M3_DOCS_CODEX_VERSION='<Codex semantic version>'
export M3_DOCS_PI_VERSION='<Pi semantic version>'
export M3_DOCS_CLAUDE_VERSION='<Claude Code semantic version>'
export M3_DOCS_OPENCODE_VERSION='<OpenCode semantic version>'
export M3_DOCS_PI_PROVIDER='<Pi provider identifier>'
export M3_DOCS_OPENCODE_PROVIDER='<OpenCode provider identifier>'
export M3_DOCS_PI_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_CLAUDE_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_OPENCODE_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_PI_API_KEY='<Pi provider credential>'
export M3_DOCS_CLAUDE_API_KEY='<Claude provider credential>'
export M3_DOCS_OPENCODE_API_KEY='<OpenCode provider credential>'
```

```python
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import SecretReference, StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}


def test_native_pins_and_local_acp_share_assertions(tmp_path: Path) -> None:
    selections = [
        {
            "harness": "codex",
            "models": [os.environ["M3_DOCS_CODEX_MODEL"]],
            "runtime": "managed",
            "version": os.environ["M3_DOCS_CODEX_VERSION"],
        },
        {
            "harness": "pi",
            "models": [os.environ["M3_DOCS_PI_MODEL"]],
            "provider": os.environ["M3_DOCS_PI_PROVIDER"],
            "credential_references": {
                os.environ["M3_DOCS_PI_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_PI_API_KEY"
                )
            },
            "runtime": "managed",
            "version": os.environ["M3_DOCS_PI_VERSION"],
        },
        {
            "harness": "claude_code",
            "models": [os.environ["M3_DOCS_CLAUDE_MODEL"]],
            "credential_references": {
                os.environ["M3_DOCS_CLAUDE_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_CLAUDE_API_KEY"
                )
            },
            "runtime": "managed",
            "version": os.environ["M3_DOCS_CLAUDE_VERSION"],
        },
        {
            "harness": "opencode",
            "models": [os.environ["M3_DOCS_OPENCODE_MODEL"]],
            "provider": os.environ["M3_DOCS_OPENCODE_PROVIDER"],
            "credential_references": {
                os.environ["M3_DOCS_OPENCODE_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_OPENCODE_API_KEY"
                )
            },
            "runtime": "managed",
            "version": os.environ["M3_DOCS_OPENCODE_VERSION"],
        },
        {
            "harness": "acp",
            "models": ["fixture"],
            "manifest": {
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(HERE / "deterministic_acp_agent.py")],
            },
        },
    ]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(
        env={}, harness_cache_dir=tmp_path / "external-harness-cache"
    ) as kit:
        agents = kit.agents(selections, trials=2)
        assert len(agents) == 10
        for agent in agents:
            result = agent.run(PROMPT, server=server, timeout=180)
            assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
            expect(result).to_have_tool_call(
                "shipping_quote",
                server="shipping",
                arguments={"weight_kg": 2, "zone": "local"},
                status="success",
                count=1,
                result={"structured_content": EXPECTED_QUOTE},
                result_partial=True,
            )
```

Run from the project directory with `python -m pytest -q test_sdk_pins.py`.
It selects ten executions and will contact four configured providers; the
local ACP fifth entry does not make the native calls deterministic. The CLI
accepts versions on individual native selectors, such as
`codex@VERSION=MODEL`, when `--runtime managed` is selected. That runtime flag
is global, however, and managed mode rejects ACP. Use this SDK variation to
mix per-entry native pins with an ACP process in one selection.

PowerShell environment assignment and line continuation differ from Bash and
zsh. The parser accepts the same `--harness` options, but an equivalent
PowerShell command has not been run in this preview.

See [choose an agent harness](harnesses.md) for integration tradeoffs,
[managed runtimes](managed-runtimes.md) for pinned binaries, and
[HarnessMatrix](../../reference/python/m3/matrix.md) for SDK-defined case
expansion.
