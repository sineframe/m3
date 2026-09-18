# MCP Pal test patterns

Preferred agent pattern:

```python
import pytest
from mcp_pal import expect

@pytest.mark.mcp_pal
def test_tool_choice(agent, shipping_server):
    result = agent.run("Get a quote", server=shipping_server)
    expect(result).to_have_tool_call("shipping_quote", server=shipping_server.name,
                                     status="success")
```

## Same suite across files

Use the existing `mcp_pal` marker; do not add another marker:

```python
# tests/catalog_tools.py and tests/catalog_prompts.py
import pytest
pytestmark = pytest.mark.mcp_pal(suite_name="catalog")
```

Run both files with `mcp-pal test --suite catalog -- tests/catalog_tools.py
tests/catalog_prompts.py`. Combine this with `--harness`, `--trials`, `-k`, and
`-m`; all filters apply together. The results database assigns one suite ID to
the exact trimmed name. For a standalone SDK run, pass `suite_name` to
`MCPTestKit` or the execution specification instead.

Install project test support with `uv add "mcp-pal[pytest]"`. This installs the
SDK, not the standalone CLI or UI. When CLI installation or project setup is
part of the task, read [cli-runner.md](cli-runner.md). Prefer the target
project's existing server fixture.

## Choose persistence explicitly

Direct SDK and pytest use keeps executions in memory unless storage is
selected. When the test opens saved data again, install
`mcp-pal[pytest,storage]` and pass a SQLite store:

```python
import sys
from pathlib import Path
from mcp_pal import MCPTestKit, StdioServer
from mcp_pal.storage import SQLiteExecutionStore

root = Path("sdk/examples")
shipping_server = StdioServer(name="example-mcp", command=sys.executable,
                              args=(str(root / "servers/example_mcp_server.py"),))
manifest = {"protocol": "acp", "protocol_version": 1,
            "command": sys.executable,
            "args": [str(root / "servers/deterministic_acp_agent.py")]}

store = SQLiteExecutionStore(".mcp-pal/executions.sqlite")
with MCPTestKit(store=store, env={}) as kit:
    agent = kit.agents([{"harness": "acp", "models": ["fixture"], "manifest": manifest}])[0]
    result = agent.run("Get a quote", server=shipping_server)
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
memory. With the MCP Pal pytest plugin, pytest item outcomes are saved in
internal run records and MCP Pal matcher checks as execution evaluations.
Other Python assertions and matrix/trial summary rows are not persisted. Use
`store.aggregate_evaluations(...)` for
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
from mcp_pal.evaluations import EvaluationDecision
from mcp_pal.types import EvaluationSource, EvaluationStatus

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

Define the server fixture yourself. The plugin supplies only the selected
`agent` for a marked test:

```python
import sys
import pytest
from mcp_pal import MCPTestKit, StdioServer, expect

@pytest.fixture
def shipping_server():
    return StdioServer(
        name="shipping", command=sys.executable,
        args=("-m", "your_package.mcp_server"),
    )

def test_shipping_quote_contract(shipping_server):
    with MCPTestKit() as kit, kit.direct(
        shipping_server, validate_schemas=True
    ) as client:
        tool = next(item for item in client.list_all_tools()
                    if item.name == "shipping_quote")
        assert set(tool.input_schema["required"]) == {"weight_kg", "zone"}
        result = client.call_tool(
            "shipping_quote", {"weight_kg": 2, "zone": "local"}
        )
    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}

@pytest.mark.mcp_pal
def test_agent_selects_shipping_quote(agent, shipping_server):
    result = agent.run(
        "Get a local shipping quote for a 2 kg parcel.",
        server=shipping_server,
    )
    expect(result).to_have_tool_call(
        "shipping_quote", server="shipping", status="success"
    )
```

Run one test for each CLI-selected harness/model and independent trial:

```bash
mcp-pal test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol --trials 2 -- tests/test_shipping.py
```

This creates four agent items. The `.env` file contains
`OPENCODE_API_KEY` and `OPENAI_API_KEY` when those routes need provider keys;
Codex can also use its existing native login. The CLI reads `.env` only when
requested. For a custom provider variable, use
`--credential-env VENDOR_API_KEY=MY_VENDOR_KEY`; add `opencode:` before the
target when only OpenCode needs that mapping. The values never belong in flags
or test code.

For a notebook or normal Python file, iterate over a plain list:

```python
agents = [
    {"harness": "opencode", "models": ["opencode/big-pickle", "openai/gpt-5.6-sol"]},
    {"harness": "codex", "models": ["gpt-5.6-sol"]},
]
with MCPTestKit() as kit:
    for agent in kit.agents(agents):
        result = agent.run("Find the shipping tool", server=shipping_server)
        print(agent.harness, agent.model,
              [call.tool.value for call in result.trace_view.tool_calls])
```

Omitting `tools` leaves the server's advertised MCP tools available.
`tools=[]` denies them. For choice tests, keep realistic safe alternatives
available and assert captured wire evidence.

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

For a cost ceiling after a single agent turn, use the finalized view:

```python
usage = session.result.trace_view.summary.usage.value
assert usage is not None, "usage was not reported"
assert usage.cost.value is not None, "cost was not reported"
assert usage.cost.value < 100.0
```

Cost and currency may be absent; check `usage.currency.value` if a specific
currency is required. `summary.usage` is the latest usage entry, not a sum of
all entries. CLI-selected SQLite stores the underlying usage trace when the
harness emits it; a plain Python budget assertion is saved only as part of the
pytest item outcome.

Replace the module, schema, arguments, expected result, model, and prompt with
facts from the target project. Direct pytest needs `-p mcp_pal.pytest_plugin`
when using the `agent` fixture; `mcp-pal test` loads it automatically.

Use `AsyncMCPTestKit` with `async with` and `await` when the surrounding script
is async. Use ToolMatrix for repeated known calls; combine
`@matrix.parametrize()` with a marked `agent` test for tool choice across
harnesses.

## Score nondeterministic harness trials

`--trials N` creates N independent executions for every selected
harness/model and ordinary pytest parameter combination. Keep every attempt,
including failures; never rerun only failures and report the best attempt.
One hundred logical cases across two harnesses with two trials produce four
hundred executions.

Use ordinary `@pytest.mark.parametrize` for the logical cases, then mark the
same test with `@pytest.mark.mcp_pal`. The plugin keeps a stable logical case
ID across harnesses and trials. Call `kit.evaluate(...)` once per completed
execution or turn and aggregate saved decisions by `metadata.harness_config`.
In a script, use `kit.agents([...], trials=N)` and pass the same explicit
`case_id` for every selection of a logical case. See the
[SDK evaluation guide](../../../sdk/docs/evaluations.md) for a worked example.
