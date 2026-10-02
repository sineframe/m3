---
title: "Run a pinned agent harness"
description: "Acquire an explicit native harness version, run an agent test, and check the requested and resolved runtime identity recorded by M3."
---

# Run a pinned agent harness

Set `runtime="managed"` and an explicit version when the test must identify
which native harness executable it used. M3 resolves the release for the
current target, verifies the archive digest, checks the executable's reported
version, and records the resolved identity with the execution.

## Requirements

This complete project uses Codex CLI and one local stdio MCP server. Install
`sf-m3[pytest]`, sign in to Codex, choose a model available to that login, and
set both variables before running the test. Runtime download and provider
authentication are separate: the managed executable does not create a Codex
login or provide model access.

Install the SDK in the Python environment used to run the test:

```sh
python -m pip install 'sf-m3[pytest]'
```

For the CLI variation, install the standalone CLI on the same M3 release as
the SDK. See [Install and update M3](../../start/install.md).

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
export M3_DOCS_CODEX_VERSION='<explicit Codex CLI version>'
export M3_DOCS_PYTHON="$(command -v python)"
```

Use a semantic version such as `0.155.1`; `latest` is not a pin. The native
adapter name is `codex`. The same selection field names apply to `pi`,
`claude_code`, and `opencode`; see [the compatibility reference](../../reference/compatibility.md)
for native support and [compare harness versions](versions.md) for a two-pin
run.

## Complete project

Create a directory named `pinned-agent` and add both files. The server returns
the structured quote `{ "amount": 9.0, "currency": "USD" }` for the requested
weight and zone. The test checks that result and the runtime identity M3
recorded.

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
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_codex_pin_is_recorded(tmp_path: Path) -> None:
    model = os.environ["M3_DOCS_CODEX_MODEL"]
    version = os.environ["M3_DOCS_CODEX_VERSION"]
    if version == "latest":
        raise ValueError("M3_DOCS_CODEX_VERSION must be an explicit version")

    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    selection = {
        "harness": "codex",
        "models": [model],
        "runtime": "managed",
        "version": version,
    }

    with MCPTestKit(env={}, harness_cache_dir=tmp_path / "harness-cache") as kit:
        agent = kit.agents([selection])[0]
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
    assert identity.harness.requested_selector == version
    assert identity.harness.resolved_version == version
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

Run this command from the `pinned-agent` directory:

```sh
python -m pytest -q test_managed_runtime.py
```

The outcome assertion checks that the execution completed. The tool matcher
checks one successful call, its server, arguments, and structured quote. The
identity assertions compare the requested pin with M3's resolved version and
require a target and SHA-256 digest. They do not prove that every harness or
operating system has an asset for every version.

## Verify local result and evaluation mechanics

This separate deterministic test calls only the local MCP server. It checks
the returned structured data, matches the recorded direct tool call, and
evaluates that observed result with a registered evaluator. It does not test a
managed binary or make a provider request. Add `test_local_contract.py` beside
`shipping_server.py`:

```python
import sys
from pathlib import Path

from m3 import MCPTestKit, expect
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus, StdioServer

HERE = Path(__file__).resolve().parent
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}


def quote_evaluator(context):
    passed = context.subject == EXPECTED_QUOTE
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        score=1.0 if passed else 0.0,
        rationale="structured shipping quote matches the local contract",
    )


def test_local_server_matcher_and_evaluation() -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        kit.register_evaluator("shipping.quote.v1", quote_evaluator)
        with kit.direct(server) as client:
            result = client.call_tool(
                "shipping_quote", {"weight_kg": 2, "zone": "local"}
            )
        trace = client.final_trace

        assert result.structured_content == EXPECTED_QUOTE
        assert trace is not None
        expect(trace).to_have_tool_call(
            "shipping_quote",
            server="shipping",
            arguments={"weight_kg": 2, "zone": "local"},
            status="success",
            count=1,
            result={"structured_content": EXPECTED_QUOTE},
            result_partial=True,
        )
        evaluation = kit.evaluate(
            result.structured_content,
            "shipping.quote.v1",
            execution_id=trace.execution_id,
        )

    assert evaluation.status is EvaluationStatus.PASSED
```

From the `pinned-agent` directory, run the deterministic local test:

```sh
python -m pytest -q test_local_contract.py
```

The passing local assertion does not stand in for the pin or provider tests.

Complete source project: [`sdk/examples/docs/agents-managed-runtimes`](../../../../sdk/examples/docs/agents-managed-runtimes).

## Choose the runtime source

`runtime="system"` is the default. It uses the executable supplied by the
native adapter's system lookup and does not guarantee an exact version. Use
`runtime="managed"` with `version="latest"` when the test intentionally
follows the release currently resolved for that invocation. Use a concrete
semantic version for a repeatable pin. `latest` is resolved through release
metadata and pinned for the current CLI invocation, including workers in one
pytest run; the next invocation may resolve a newer release.

For a CLI-selected pytest fixture, add this separate file to the same project.
It uses the same local server but receives the pinned agent from M3's `agent`
fixture. The result assertion checks the actual execution outcome, call, and
requested/resolved identity.

`test_managed_runtime_fixture.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import ExecutionOutcome, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent

pytestmark = pytest.mark.m3(suite_name="pinned-runtime")


def test_cli_selected_pin_is_recorded(agent) -> None:
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
    assert identity.harness.runtime == "managed"
    assert identity.harness.requested_selector == identity.harness.resolved_version
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

From the `pinned-agent` directory, run:

```sh
m3 test --python "$M3_DOCS_PYTHON" --runtime managed --harness "codex@${M3_DOCS_CODEX_VERSION}=${M3_DOCS_CODEX_MODEL}" -- test_managed_runtime_fixture.py
```

`@VERSION` pins the CLI fixture selection. The global `--runtime managed` is
required for a versioned CLI selector. For SDK selection and cache options,
see the [managed runtime reference](../../reference/managed-runtimes.md).

For selection, cache locations, and recorded identity fields, see the [managed
runtime reference](../../reference/managed-runtimes.md). For auth setup, see
[environment configuration](../../reference/configuration.md).
