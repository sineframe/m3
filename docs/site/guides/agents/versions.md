---
title: "Compare agent harnesses and versions"
description: "Run the same test across pinned agent harnesses in isolated runtimes and compare each execution's recorded tool-call result."
---

# Compare agent harnesses and versions

Use `m3 test --runtime managed` to run one test across agent harnesses and their
versions, with a separate runtime identity and tool-call result for each
selection. M3 acquires each pinned executable and gives every execution its
own temporary home and harness configuration. Downloaded binaries are shared
through the runtime cache.

Start with two Codex versions and one model to compare a change in harness
version. Then add two Pi versions to the same command without changing the
test. Your installed command-line clients are left in place.

## Requirements

Install `sf-m3[pytest]` and the standalone M3 CLI on matching releases. Sign in
to Codex and choose a model available to that login. Select two distinct
explicit versions with release assets for your OS and CPU. Each pin needs
network access for acquisition or a valid cache entry; model access is separate.

Install the SDK in the Python environment used to run the test:

```sh
python -m pip install 'sf-m3[pytest]'
```

See [Install and update M3](../../start/install.md) for the standalone CLI.
Set these values in Bash or zsh. `M3_DOCS_PYTHON` selects the project interpreter
that has the SDK installed:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
export M3_DOCS_CODEX_VERSION_A='<first explicit version>'
export M3_DOCS_CODEX_VERSION_B='<second explicit version>'
export M3_DOCS_PYTHON="$(command -v python)"
```

## Shared tool-use test

Keep `shipping_server.py` and `test_versions.py` in the same directory. The
server returns a deterministic shipping quote. M3's `agent` fixture receives
each harness/version selected by the CLI, producing a separate test result
for each selection. Runtime selection stays in the command, so the test is
unchanged when another version or harness is added.

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

`test_versions.py`:

```python
import json
import sys
from pathlib import Path

import pytest

from m3 import ExecutionOutcome, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}

pytestmark = pytest.mark.m3(suite_name="runtime-comparison")


def test_shipping_quote_across_runtimes(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    result = agent.run(PROMPT, server=server, timeout=180)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    identity = result.snapshot.agent
    assert identity is not None
    runtime = identity.harness
    assert runtime.runtime == "managed"
    assert runtime.requested_selector != "latest"
    assert runtime.requested_selector == runtime.resolved_version
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": EXPECTED_QUOTE},
        result_partial=True,
    )
    quote = result.trace_view.tool_calls[0].result.value.structured_content.value
    print(
        f"\n{runtime.kind}: requested={runtime.requested_selector} "
        f"resolved={runtime.resolved_version} quote={json.dumps(dict(quote), sort_keys=True)}"
    )
    print(f"execution={result.snapshot.execution_id}")
```

## Compare two versions

Run from the directory containing the two files. `--runtime managed` and
`--harness` configure M3. Arguments after `--` are passed to pytest: `-v`
shows the version in each test case name, and `-s` shows the resolved identity
and returned quote printed by the test:

```sh
m3 test --python "$M3_DOCS_PYTHON" --runtime managed \
  --harness "codex@${M3_DOCS_CODEX_VERSION_A}=${M3_DOCS_CODEX_MODEL}" \
  --harness "codex@${M3_DOCS_CODEX_VERSION_B}=${M3_DOCS_CODEX_MODEL}" \
  -- -v -s test_versions.py
```

One test expands to two cases under one M3 run. Compare the requested and
resolved versions, the quote, and each case's `PASSED` or `FAILED` result.
The two execution IDs identify the separate recorded interactions. A passing
case returned the expected 9 USD quote through one successful shipping-tool
call. A failing case retains its harness/version label and assertion details,
so the other selection's result remains visible.

The server, model, prompt, and assertions stay fixed. Provider responses can
still vary, so one passing case establishes this tool-use contract for that
execution.

To inspect why a selection failed, run `m3 ui` from the same directory and
open the saved run printed by `m3 test`. Each execution records its harness,
resolved version, model, and observed interactions. See
[open the saved run](../results/viewer.md).

## Add another harness

For Pi, select a provider/model available to your account and configure its
credential. This variation uses Pi's OpenAI provider: set `M3_DOCS_PI_MODEL` to
`openai/<model>` and set `M3_DOCS_PI_API_KEY` to your existing OpenAI API key.
Pi's isolated home does not inherit a host login. No preinstalled Pi executable
is needed for managed selection.

```sh
export M3_DOCS_PI_MODEL='openai/<model available to your account>'
export M3_DOCS_PI_VERSION_A='<first explicit Pi version>'
export M3_DOCS_PI_VERSION_B='<second explicit Pi version>'
```

Run from the same directory:

```sh
m3 test --python "$M3_DOCS_PYTHON" --runtime managed \
  --harness "codex@${M3_DOCS_CODEX_VERSION_A}=${M3_DOCS_CODEX_MODEL}" \
  --harness "codex@${M3_DOCS_CODEX_VERSION_B}=${M3_DOCS_CODEX_MODEL}" \
  --harness "pi@${M3_DOCS_PI_VERSION_A}=${M3_DOCS_PI_MODEL}" \
  --harness "pi@${M3_DOCS_PI_VERSION_B}=${M3_DOCS_PI_MODEL}" \
  --credential-env pi:OPENAI_API_KEY=M3_DOCS_PI_API_KEY \
  -- -v -s test_versions.py
```

The unchanged test now produces four cases:

| Harness | Pins | Model | Assertion in each case |
| --- | --- | --- | --- |
| Codex | `M3_DOCS_CODEX_VERSION_A`, `M3_DOCS_CODEX_VERSION_B` | `M3_DOCS_CODEX_MODEL` | One successful shipping call returning 9 USD |
| Pi | `M3_DOCS_PI_VERSION_A`, `M3_DOCS_PI_VERSION_B` | `M3_DOCS_PI_MODEL` | One successful shipping call returning 9 USD |

Each execution has separate writable home/configuration state and reports its
own runtime identity and quote. Compare versions within each harness with the
same model. Across harnesses, account for differences in the model/provider
selection as well as the harness.

The same selector syntax supports `claude_code` and `opencode`; their provider
credentials and model identifiers are described in
[Choose an agent harness](harnesses.md) and the
[credential reference](../../reference/credentials.md). ACP agents supply
their own executable and cannot join a command with global `--runtime managed`.

## Repeat with cached runtimes

Run either comparison command again. Explicit pins reuse verified cache entries
automatically, and the CLI reports `loaded from cache` for those assets. Reuse
avoids downloading the same releases for each comparison. The test still
creates fresh executions with separate writable state and makes new provider
requests; model responses are not cached.

By default, assets remain in M3's external user cache. To choose another
external cache, pass `--harness-cache-dir PATH` before `--` in either command.
This is an M3 CLI option, not a pytest option. In CI, retain that directory
between jobs to reuse downloaded releases. Cleanup is optional;
see [cache configuration and cleanup](../../reference/managed-runtimes.md#cache-location-and-precedence).

M3's runtime isolation concerns executable selection and per-execution state.
It is not an operating-system filesystem or network sandbox.

See [pinned runtime selection](managed-runtimes.md) and
[SDK runtime selection](../../reference/managed-runtimes.md#runtime-and-version-selection)
for configuration outside this CLI workflow.

Example source: [`sdk/examples/docs/agents-runtime-versions`](../../../../sdk/examples/docs/agents-runtime-versions).
