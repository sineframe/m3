# MCP Pal test patterns

Install test support with `uv add "mcp-pal[pytest]"`. Prefer the target
project's existing server fixture. This example uses stdio:

```python
import os
import sys

import pytest
from mcp_pal import (
    AgentExecutionSpec,
    ClaudeCode,
    MCPTestKit,
    NativeToolPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    expect,
)


@pytest.fixture
def shipping_server() -> StdioServer:
    return StdioServer(
        name="shipping",
        command=sys.executable,
        args=("-m", "your_package.mcp_server"),
    )


def test_shipping_quote_contract(shipping_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(
        shipping_server, validate_schemas=True
    ) as client:
        tool = next(
            item for item in client.list_all_tools()
            if item.name == "shipping_quote"
        )
        assert set(tool.input_schema["required"]) == {"weight_kg", "zone"}
        result = client.call_tool(
            "shipping_quote", {"weight_kg": 2, "zone": "local"}
        )

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}


@pytest.mark.skipif(
    os.environ.get("MCP_PAL_RUN_LIVE_CLAUDE") != "1",
    reason="set MCP_PAL_RUN_LIVE_CLAUDE=1 to run the live agent test",
)
def test_agent_selects_shipping_quote(shipping_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(shipping_server) as client:
        advertised = {tool.name for tool in client.list_all_tools()}
    assert "shipping_quote" in advertised
    assert advertised - {"shipping_quote"}, (
        "selection requires at least one realistic safe alternative"
    )

    spec = AgentExecutionSpec(
        harness=ClaudeCode(
            model=os.environ["MCP_PAL_CLAUDE_MODEL"],
            credential_references={
                "ANTHROPIC_API_KEY": SecretReference(
                    source="environment", name="ANTHROPIC_API_KEY"
                )
            },
        ),
        servers=(ServerBinding(server=shipping_server, alias="shipping"),),
        # Server scope retains choice among this server's safe tools.
        tool_policy=NativeToolPolicy(
            harness="claude-code",
            policy={"mode": "mcp_only", "server": "shipping"},
            nonportable_reason="Claude Code CLI tool policy",
        ),
    )

    with MCPTestKit(env={}) as kit:
        with kit.agent_session(spec) as session:
            turn = session.send(
                "Get a local shipping quote for a 2 kg parcel.", timeout=120
            )

    expect(session.result).to_have_tool_call(
        "shipping_quote",
        turn=turn,
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    turn_view = session.result.trace_view.for_turn(turn)
    assert [call.tool.value for call in turn_view.tool_calls] == ["shipping_quote"]
```

## Inspect trace metadata

After the client or session closes, `trace.view()` or `result.trace_view`
returns the public immutable `TraceView`. For local exploration, one line shows
its complete JSON-compatible shape without a custom serializer:

```python
view = session.result.trace_view
public_trace = view.model_dump(mode="json")
```

Top-level metadata includes `schema_id`, `schema_version`, `trace_id`,
`execution_id`, `outcome`, `completeness`, `limitations`, `runtime`, and
`summary`. Use `timeline` for ordered evidence or the typed indexes `messages`,
`reasoning`, `tool_calls`, `protocol`, `transports`, `interactions`, `processes`,
`diagnostics`, and `raw_messages`. Scope with `for_turn`, `for_session`,
`for_server`, or `between`.

Provider-dependent fields are `Observation` values. Check `state` and `reason`
before `value`; unavailable, unsupported, hidden, encrypted, redacted, or
truncated data must not be treated as observed. Raw content is separate: while
the owning kit or store is open, resolve a `raw_messages` `evidence_ref` with
`read_raw_evidence(reference, max_bytes=...)`. Do not emit full trace dumps or
raw evidence to shared CI logs.

Replace the module, schema, arguments, expected result, model, and prompt with
facts from the target project. For OpenCode, replace `ClaudeCode` with
`OpenCode` and reference the provider credential expected by that installation.

Use `AsyncMCPTestKit` with `async with` and `await` when the surrounding test is
async. Use `ToolMatrix` only for repeated known calls; it does not test agent
selection. Use `HarnessMatrix` when the same prompt/selection claim must run
against multiple harnesses or server configurations.

Run the narrow test first:

```bash
# Export ANTHROPIC_API_KEY securely first.
MCP_PAL_RUN_LIVE_CLAUDE=1 MCP_PAL_CLAUDE_MODEL=your-enabled-model \
  uv run pytest -q tests/test_shipping.py
```
