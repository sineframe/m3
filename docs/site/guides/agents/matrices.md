---
title: "Run agent cases as a matrix"
description: "Expand multiple MCP server and tool cases across native harnesses and trials, with explicit handling of Claude Code's server-scope policy."
---

# Run agent cases as a matrix

`HarnessMatrix.each_tool()` expands each server-owned tool case across selected
harnesses and trials. This example has two servers, two tools per server, four
native harnesses, and two trials: 2 × 2 × 4 × 2 = 32 executions. One local
pytest test runs each case and checks the tool call recorded for that case.

## Requirements

Install `sf-m3[pytest]` and all four native CLIs. Configure Codex, Pi, Claude
Code, and OpenCode independently, and select a provider/model supported by
each account. Native sessions make provider requests and may incur charges.
Review each client's approval configuration. This code maps Pi, Claude Code,
and OpenCode credential values from named environment variables into the
provider-specific child variable you choose. Codex uses its host
`CODEX_HOME/auth.json` login unless you add a `credential_references` map.

Export `M3_DOCS_PI_KEY_NAME`, `M3_DOCS_CLAUDE_KEY_NAME`, and
`M3_DOCS_OPENCODE_KEY_NAME` as the environment variable names required by the
selected providers. Export each matching `M3_DOCS_*_API_KEY` with that
account's credential. Also export `M3_DOCS_PI_PROVIDER` and
`M3_DOCS_OPENCODE_PROVIDER` with the providers selected for their models. Do
not print or commit credential values. See [provider credentials](../credentials.md).
The selected execution case contains one server, so Claude Code's server-scope
policy applies without attempting exact tool restriction.

From the M3 repository root, install the development candidate used in this
preview:

```sh
python -m pip install -e 'sdk[pytest]'
```

Set model identifiers for your configured accounts before running:

```sh
export M3_DOCS_CODEX_MODEL='<Codex model identifier>'
export M3_DOCS_PI_MODEL='<Pi model identifier>'
export M3_DOCS_CLAUDE_MODEL='<Claude Code model identifier>'
export M3_DOCS_OPENCODE_MODEL='<OpenCode model identifier>'
export M3_DOCS_PI_PROVIDER='<Pi provider identifier>'
export M3_DOCS_OPENCODE_PROVIDER='<OpenCode provider identifier>'
export M3_DOCS_PI_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_CLAUDE_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_OPENCODE_KEY_NAME='<provider credential environment variable name>'
export M3_DOCS_PI_API_KEY='<Pi provider credential>'
export M3_DOCS_CLAUDE_API_KEY='<Claude provider credential>'
export M3_DOCS_OPENCODE_API_KEY='<OpenCode provider credential>'
```

## Complete project

Create a directory named `agent-matrix`. Add these files. Each server exposes
two tools so matrix expansion covers two independent tool contracts per
server.

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
                        "zone": {"type": "string", "enum": ["local", "regional"]},
                    },
                    "required": ["weight_kg", "zone"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="shipping_window",
                description="Return the local delivery window",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    if params.name == "shipping_quote":
        rates = {"local": 2.0, "regional": 3.5}
        zone = arguments.get("zone")
        weight = arguments.get("weight_kg")
        if (
            zone not in rates
            or not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or weight <= 0
        ):
            return types.CallToolResult(
                content=[types.TextContent(text="invalid shipping request")],
                is_error=True,
            )
        result = {"amount": round(5 + weight * rates[zone], 2), "currency": "USD"}
    elif params.name == "shipping_window" and not arguments:
        result = {"days": 2, "zone": "local"}
    else:
        return types.CallToolResult(
            content=[types.TextContent(text="invalid shipping request")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="shipping result ready")],
        structured_content=result,
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

`inventory_server.py`:

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
                name="stock_count",
                description="Return stock for a SKU",
                input_schema={
                    "type": "object",
                    "properties": {"sku": {"type": "string"}},
                    "required": ["sku"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="reorder_status",
                description="Return the reorder status for a SKU",
                input_schema={
                    "type": "object",
                    "properties": {"sku": {"type": "string"}},
                    "required": ["sku"],
                    "additionalProperties": False,
                },
            ),
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    sku = (params.arguments or {}).get("sku")
    if not isinstance(sku, str) or not sku:
        return types.CallToolResult(
            content=[types.TextContent(text="sku is required")], is_error=True
        )
    if sku != "widget-1":
        return types.CallToolResult(
            content=[types.TextContent(text="unknown sku")], is_error=True
        )
    if params.name == "stock_count":
        result = {"sku": sku, "quantity": 12}
    elif params.name == "reorder_status":
        result = {"sku": sku, "reorder": False}
    else:
        return types.CallToolResult(
            content=[types.TextContent(text="unknown tool")], is_error=True
        )
    return types.CallToolResult(
        content=[types.TextContent(text="inventory result ready")],
        structured_content=result,
    )


async def main() -> None:
    server: Server[object] = Server(
        "inventory",
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

`test_agent_matrix.py`:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path

from m3 import (
    ClaudeCode,
    Codex,
    ExecutionOutcome,
    HarnessCase,
    HarnessMatrix,
    MCPTestKit,
    OpenCode,
    Pi,
    ServerCase,
    ToolCase,
    expect,
)
from m3.types import SecretReference, StdioServer

HERE = Path(__file__).resolve().parent


def test_tools_across_servers_harnesses_and_trials() -> None:
    servers = (
        ServerCase(
            name="shipping",
            server=StdioServer(
                name="shipping",
                command=sys.executable,
                args=(str(HERE / "shipping_server.py"),),
                cwd=str(HERE),
            ),
            tools=(
                ToolCase(
                    name="shipping_quote",
                    id="local",
                    arguments={"weight_kg": 2, "zone": "local"},
                    prompt="Call shipping_quote with weight_kg 2 and zone local.",
                ),
                ToolCase(
                    name="shipping_window",
                    id="window",
                    arguments={},
                    prompt="Call shipping_window.",
                ),
            ),
        ),
        ServerCase(
            name="inventory",
            server=StdioServer(
                name="inventory",
                command=sys.executable,
                args=(str(HERE / "inventory_server.py"),),
                cwd=str(HERE),
            ),
            tools=(
                ToolCase(
                    name="stock_count",
                    id="stock",
                    arguments={"sku": "widget-1"},
                    prompt="Call stock_count for sku widget-1.",
                ),
                ToolCase(
                    name="reorder_status",
                    id="reorder",
                    arguments={"sku": "widget-1"},
                    prompt="Call reorder_status for sku widget-1.",
                ),
            ),
        ),
    )
    harnesses = (
        HarnessCase(
            name="codex", harness=Codex(model=os.environ["M3_DOCS_CODEX_MODEL"])
        ),
        HarnessCase(
            name="pi",
            harness=Pi(
                provider=os.environ["M3_DOCS_PI_PROVIDER"],
                model=os.environ["M3_DOCS_PI_MODEL"],
                credential_references={
                    os.environ["M3_DOCS_PI_KEY_NAME"]: SecretReference(
                        source="environment", name="M3_DOCS_PI_API_KEY"
                    )
                },
            ),
        ),
        HarnessCase(
            name="claude_code",
            harness=ClaudeCode(
                model=os.environ["M3_DOCS_CLAUDE_MODEL"],
                credential_references={
                    os.environ["M3_DOCS_CLAUDE_KEY_NAME"]: SecretReference(
                        source="environment", name="M3_DOCS_CLAUDE_API_KEY"
                    )
                },
            ),
        ),
        HarnessCase(
            name="opencode",
            harness=OpenCode(
                provider=os.environ["M3_DOCS_OPENCODE_PROVIDER"],
                model=os.environ["M3_DOCS_OPENCODE_MODEL"],
                credential_references={
                    os.environ["M3_DOCS_OPENCODE_KEY_NAME"]: SecretReference(
                        source="environment", name="M3_DOCS_OPENCODE_API_KEY"
                    )
                },
            ),
        ),
    )
    matrix = HarnessMatrix.each_tool(
        servers=servers, harnesses=harnesses, trials=2, id="agent-tools-by-server"
    )
    cases = matrix.cases()
    assert len(cases) == 32

    with MCPTestKit(env={}) as kit:
        results = tuple(case.run(kit=kit, timeout=180) for case in cases)

    for case, result in zip(cases, results, strict=True):
        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
        expected_result = {
            "shipping_quote": {"amount": 9.0, "currency": "USD"},
            "shipping_window": {"days": 2, "zone": "local"},
            "stock_count": {"sku": "widget-1", "quantity": 12},
            "reorder_status": {"sku": "widget-1", "reorder": False},
        }[case.tool.name]
        expect(result).to_have_tool_call(
            case.tool.name,
            server=case.server.name,
            arguments=case.tool.arguments,
            status="success",
            count=1,
            result={"structured_content": expected_result},
            result_partial=True,
        )
```

From the `agent-matrix` directory, run the test after selecting the four
native models and exporting the provider variables above:

```sh
python -m pytest -q test_agent_matrix.py
```

The `len(cases) == 32` assertion checks local matrix expansion before agents
run. The completion and matcher assertions check each live result's selected
tool, server, arguments, success status, and structured result. This command
makes live provider requests; no native matrix run was made in this preview.

Complete source project: [`sdk/examples/docs/agents-matrices`](../../../../sdk/examples/docs/agents-matrices).

## Choose a server expansion mode

| Mode | Case expansion | Claude Code with multiple servers |
| --- | --- | --- |
| `HarnessMatrix.each_tool()` | One selected server and tool per case, crossed with harnesses and trials. | Supported; each case contains one server and uses Claude Code's server-scope policy. |
| `HarnessMatrix.each_server()` | One server per case, crossed with harnesses and trials. | Supported; each case contains one server. |
| `HarnessMatrix.all_servers()` | Every listed server is attached to each harness/trial case. | Rejected when Claude Code is among the harnesses and more than one server is listed. Construction raises `UnsupportedFeature("HarnessMatrix.all_servers does not support ClaudeCode with multiple servers")`. |

Use `each_server` or `each_tool` when Claude Code must participate in a
multi-server test. A harness matrix does not make provider behavior
deterministic, and trials are separate executions rather than retries. For
selection through CLI pytest fixtures, see [run the same test across agent
harnesses](multiple-harnesses.md); for model and credential setup, see
[choose an agent harness](harnesses.md) and [provider credentials](../credentials.md).
