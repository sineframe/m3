---
title: "Compare harness versions"
description: "Run one unchanged agent test with two pinned native harness versions and compare execution identity and deterministic evaluations."
---

# Compare harness versions

Run the same prompt, server, and assertions twice with explicit runtime pins
when you need to compare harness releases. Each run gets a separate execution
identity. This example uses one Codex model and two versions so the harness
version is the only changed selection.

## Requirements

Install `sf-m3[pytest]`, sign in to Codex, and choose a model available to that
login. Select two distinct version strings that have a release asset for your
OS and CPU. M3 downloads the executable independently of provider login.

For this development preview, install the candidate from the M3 repository
root before running the standalone project:

```sh
python -m pip install -e 'sdk[pytest]'
```

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
export M3_DOCS_CODEX_VERSION_A='<first explicit version>'
export M3_DOCS_CODEX_VERSION_B='<second explicit version>'
```

## Complete project

Create a directory named `compare-versions`. Add this deterministic shipping
server and one test that applies the same prompt and call/result assertions to
both pins.

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
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus, StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}


def quote_evaluator(context):
    passed = context.subject == EXPECTED_QUOTE
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        score=1.0 if passed else 0.0,
        rationale="structured shipping quote matches the local contract",
    )


def test_two_codex_versions_have_distinct_recorded_identity() -> None:
    model = os.environ["M3_DOCS_CODEX_MODEL"]
    versions = (
        os.environ["M3_DOCS_CODEX_VERSION_A"],
        os.environ["M3_DOCS_CODEX_VERSION_B"],
    )
    if versions[0] == versions[1] or "latest" in versions:
        raise ValueError("choose two different explicit Codex versions")

    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    selections = [
        {
            "harness": "codex",
            "models": [model],
            "runtime": "managed",
            "version": version,
        }
        for version in versions
    ]

    with MCPTestKit(env={}) as kit:
        kit.register_evaluator("shipping.quote.v1", quote_evaluator)
        results = []
        evaluations = []
        for selection in selections:
            agent = kit.agents([selection])[0]
            result = agent.run(
                PROMPT,
                server=server,
                tools=["shipping:shipping_quote"],
                timeout=180,
            )
            assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
            results.append(result)
            calls = result.trace_view.tool_calls
            assert len(calls) == 1
            assert calls[0].tool.value == "shipping_quote"
            expect(result).to_have_tool_call(
                "shipping_quote",
                server="shipping",
                arguments={"weight_kg": 2, "zone": "local"},
                status="success",
                count=1,
                result={"structured_content": EXPECTED_QUOTE},
                result_partial=True,
            )
            quote = calls[0].result.value.structured_content.value
            assert quote == EXPECTED_QUOTE
            evaluations.append(
                kit.evaluate(
                    quote,
                    "shipping.quote.v1",
                    execution_id=result.snapshot.execution_id,
                )
            )

    assert len({result.snapshot.execution_id for result in results}) == 2
    assert len({item.evaluation_id for item in evaluations}) == 2
    assert [item.status for item in evaluations] == [
        EvaluationStatus.PASSED,
        EvaluationStatus.PASSED,
    ]
    for version, result in zip(versions, results, strict=True):
        identity = result.snapshot.agent
        assert identity is not None
        assert identity.harness.requested_selector == version
        assert identity.harness.resolved_version == version
```

Run from the `compare-versions` directory:

```sh
python -m pytest -q test_versions.py
```

The two runs use the same server, model, prompt, arguments, and matcher. The
test checks distinct execution IDs, each requested and resolved pin, each
successful structured result, and a passing deterministic evaluation for each
execution. A provider can still behave differently across runs; matching the
local evaluation does not establish general model quality.

This live-provider example is not verified in the current development preview.
The four installed native CLI versions are listed in the development
verification record, but model variables are unset. See [pinned runtime
selection](managed-runtimes.md) and [runtime cache management](runtime-cache.md).

Complete source project: [`sdk/examples/docs/agents-runtime-versions`](../../../../sdk/examples/docs/agents-runtime-versions).
