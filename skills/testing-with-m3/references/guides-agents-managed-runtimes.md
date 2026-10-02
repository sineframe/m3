<!-- Generated from docs/site/guides/agents/managed-runtimes.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Run a pinned agent harness

Run a Codex tool-use test with an explicit version selected by
`m3 test --runtime managed`. M3 downloads or reuses that release for your
platform and records the executable's identity with the execution. Your
installed Codex executable is left in place.

## Requirements

Use Python 3.10 or newer and install `sf-m3[pytest,storage,judge]` and the
standalone M3 CLI on matching releases. Sign in to Codex and select a model
available to that login. The selected version needs a release asset for your
OS and CPU, with network
access for acquisition or a valid cache entry. Runtime acquisition does not
provide model credentials.

Add the SDK to your uv project:

```sh
uv add 'sf-m3[pytest,storage,judge]'
```

See [Install and update M3](start-install.md) for the standalone CLI.
Set the following values in Bash or zsh from the project root.
`M3_DOCS_PYTHON` selects uv's project interpreter without requiring environment
activation:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
export M3_DOCS_CODEX_VERSION='<explicit Codex CLI version>'
export M3_DOCS_PYTHON="$(uv run python -c 'import sys; print(sys.executable)')"
```

Use a semantic version such as `0.155.1`; `latest` is not a pin. The native
adapter name is `codex`. The same selector syntax applies to `pi`,
`claude_code`, and `opencode`; see [the compatibility reference](reference-compatibility.md)
for native support. To run one test across several pins or harnesses, see
[Compare agent harnesses and versions](guides-agents-versions.md).

## Shipping tool test

The test uses a local MCP server returning a structured shipping quote.
Keep `shipping_server.py` and `test_managed_runtime.py` in the same directory.
M3's `agent` fixture receives the harness, model, and version selected by the
CLI. The assertions check the quote and the recorded runtime identity.

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

`test_managed_runtime.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import ExecutionOutcome, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent

pytestmark = pytest.mark.m3(suite_name="pinned-runtime")


def test_codex_pin_is_recorded(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    result = agent.run(
        "Use shipping:shipping_quote once with weight_kg 2 and zone local.",
        server=server,
        tools=["shipping:shipping_quote"],
        timeout=180,
    )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    identity = result.snapshot.agent
    assert identity is not None
    assert identity.harness.kind == "codex"
    assert identity.harness.runtime == "managed"
    assert identity.harness.requested_selector != "latest"
    assert identity.harness.requested_selector == identity.harness.resolved_version
    assert identity.harness.target is not None
    assert identity.harness.digest is not None
    assert len(identity.harness.digest) == 64
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": {"amount": 9.0, "currency": "USD"}},
        result_partial=True,
    )
```

## Run the pinned version

Run from the directory containing the two files:

```sh
m3 test --python "$M3_DOCS_PYTHON" --runtime managed \
  --harness "codex@${M3_DOCS_CODEX_VERSION}=${M3_DOCS_CODEX_MODEL}" \
  -- -v test_managed_runtime.py
```

`--runtime managed` and `--harness` configure M3's agent selection. The
versioned selector requires managed mode. Arguments after `--` are passed to
pytest; here, `-v` shows the selected version in the test case name.

On success, the execution contains one call to `shipping:shipping_quote` with
the requested arguments and a structured quote of 9 USD. Its requested and
resolved versions match your pin, and the runtime identity includes a target
and SHA-256 digest. Release availability depends on the harness and platform.

## Reuse the downloaded runtime

Repeat the same command. M3 reuses the verified binary from its persistent
user cache and reports `loaded from cache`. Each run creates a new execution
and makes new model requests; the cache contains runtime assets, not responses.

To select another external cache, pass `--harness-cache-dir PATH` before `--`.
This is an M3 CLI option, not a pytest option. Without an override, M3 uses
`M3_HARNESS_CACHE_DIR` or the operating-system default. See
[cache configuration and reuse](reference-managed-runtimes.md#cache-location-and-precedence).

Direct pytest can also run managed runtimes selected through the M3 plugin or
SDK, but it does not accept `--harness-cache-dir`. Those paths use
`M3_HARNESS_CACHE_DIR` or the SDK's `harness_cache_dir` argument.

Example source: [`sdk/examples/docs/agents-managed-runtimes`](../examples/agents-managed-runtimes).

For selection, cache locations, and recorded identity fields, see the [managed
runtime reference](reference-managed-runtimes.md). For auth setup, see
[environment configuration](reference-configuration.md).
