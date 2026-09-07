# MCP Pal test patterns

Install project test support with `uv add "mcp-pal[pytest]"`. This installs the
SDK, not the standalone CLI or UI. When CLI installation or project setup is
part of the task, read [cli-runner.md](cli-runner.md). Prefer the target
project's existing server fixture.

## Choose persistence explicitly

Direct SDK and pytest use keeps executions in memory unless storage is
selected. When the test opens saved data again, install
`mcp-pal[pytest,storage]` and pass a SQLite store:

```python
from mcp_pal import MCPTestKit
from mcp_pal.storage import SQLiteExecutionStore

store = SQLiteExecutionStore(".mcp-pal/executions.sqlite")
with MCPTestKit(store=store, env={}) as kit:
    result = kit.run(spec)
execution_id = result.snapshot.execution_id
store.close()

reopened = SQLiteExecutionStore(".mcp-pal/executions.sqlite")
view = reopened.get_trace_view(execution_id)
reopened.close()
```

`mcp-pal test` selects SQLite automatically by loading the pytest plugin with
`--mcp-pal-results-db`; direct pytest can opt into the same default-store
behavior:

```bash
uv run pytest -p mcp_pal.pytest_plugin \
  --mcp-pal-results-db .mcp-pal/executions.sqlite tests
```

An explicit `store=` takes precedence over the plugin default. Current SQLite
storage saves execution specs/snapshots, recorded events/traces,
sessions/turns, stored artifacts/evidence, and evaluations explicitly attached
to executions. The explicit `store=SQLiteExecutionStore(path)` or
`--mcp-pal-results-db PATH` selection is required; otherwise SDK storage is in
memory. It does not persist pytest item outcomes, ordinary Python assertions,
or matrix/trial summary rows. Use `store.aggregate_evaluations(...)` for
pass-rate summaries. Execution outcome, MCP activity health,
and evaluation/test verdict are separate; `completed` alone does not mean
passed.

MCP Pal has no built-in LLM judge. An SDK user may put an LLM call inside a
sync or async evaluator callback and return an `EvaluationDecision` (with
redacted source details); it is not a separate post-evaluator layer. Chained
direct calls share one client execution, agent evaluations can use `turn_id`,
and matrix trials carry matrix/cell/trial metadata. API v2 only reads the
saved result and provenance; it does not run the callback.

For a user-supplied LLM evaluator, the client and credentials stay in your
application:

```python
from mcp_pal import EvaluationDecision, EvaluationSource, EvaluationStatus

def answer_quality_with_llm(context):
    verdict = my_llm_client.score(context.subject)  # your client and key
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if verdict.ok else EvaluationStatus.FAILED,
        score=verdict.score, rationale=verdict.reason,
        provenance=EvaluationSource(kind="user_llm", provider="acme", model="judge-1"),
    )

kit.register_evaluator("project.answer-quality.llm.v1", answer_quality_with_llm)
kit.evaluate(report, "project.answer-quality.llm.v1")
```

With SQLite selected, MCP Pal persists the returned result and provenance,
not the client, key, prompt, or hidden LLM state. Keep credentials out of
rationale and metadata. API v2 later groups that saved output and provenance;
it never calls the LLM.

## Streamable HTTP: deployed endpoint

Use `HTTPServer` for one deployed MCP endpoint. This generic pattern
does not assume a provider's tool names or result shape:

```python
from mcp_pal import MCPTestKit
from mcp_pal.types import HTTPServer

server = HTTPServer(name="catalog", url="https://example.test/mcp")

with MCPTestKit(env={}) as kit:
    with kit.direct(server) as client:
        assert client.initialization is not None
        tools = client.list_all_tools()
        assert tools
        result = client.call_tool("known_tool", {"query": "fixture"})
        assert result.is_error is False

evidence = client.transport_evidence
assert evidence is not None and evidence.state == "closed"
trace = client.final_trace
assert trace is not None
assert trace.view().transports
```

The URL is an MCP protocol endpoint, not a REST route. A direct public endpoint
can use the default `UNTRUSTED` trust. For a bearer credential, keep the value
in the environment and reference it through direct-client authentication,
without embedding it in the URL:

```python
from mcp_pal.types import SecretReference

token = SecretReference(source="environment", name="CATALOG_MCP_TOKEN")
with MCPTestKit(env={}) as kit:
    with kit.direct(server, bearer_token=token) as client:
        tools = client.list_all_tools()
        assert tools
```

Static non-secret headers may be declared on the server. Never put credentials
literally or in URL query parameters. The kit owns connection and client
cleanup and finalizes the trace, but does not start or stop a deployed HTTP
service; keep operations inside the client context and inspect finalized trace
data after closure.

Use `SSEServer` only for an existing legacy HTTP+SSE endpoint. Use `StdioServer`
when the project owns a local command and should test its subprocess boundary.

## Stdio: local command

```python
import os
import sys

import pytest
from mcp_pal import (
    AgentSpec,
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


def test_agent_selects_shipping_quote(shipping_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(shipping_server) as client:
        advertised = {tool.name for tool in client.list_all_tools()}
    assert "shipping_quote" in advertised
    assert advertised - {"shipping_quote"}, (
        "selection requires at least one realistic safe alternative"
    )

    spec = AgentSpec(
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
# Export ANTHROPIC_API_KEY securely first and follow the target project's
# nondeterministic-test isolation convention.
MCP_PAL_CLAUDE_MODEL=your-enabled-model \
  mcp-pal test -- tests/test_shipping.py
```

The standalone CLI delegates everything after `--` to pytest and records MCP
Pal executions. Keep nondeterministic external/provider tests isolated
according to the target project's convention; do not assume a universal flag.
When the user wants to inspect the run locally, place `--ui`
before the separator; the viewer stays open until interrupted:

```bash
mcp-pal test --ui -- tests/test_shipping.py
```

If `mcp-pal` is unavailable, `mcp-pal doctor` says the project is not ready,
or the project defines its own test command, run pytest directly instead:

```bash
uv run pytest tests/test_shipping.py
```
