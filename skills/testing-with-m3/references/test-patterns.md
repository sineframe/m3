# M3 test patterns

Start with the claim and an expected result from the target project's contract.
Inspect the real server and its existing test fixtures before replacing the
placeholder names and values below. A green connection or a nonempty response
is only a smoke test; assert the behavior the user needs to protect.
Give each pytest test function a concise docstring stating the behavior it
checks. M3 saves that docstring as the test description shown in the UI.
At least one deterministic test must use `MCPTestKit` to call the server and
produce a linked execution. A plain `assert` in a pytest function is not M3
coverage even if `m3 test` reports a pass.

For a new test, use this progression when the server exposes the surface:
catalog and schema, representative valid results, boundary and expected error
cases, stateful or resource/prompt behavior, then agent choice and quality.
Keep direct tests deterministic. Place external provider tests in a separately
selected file or marked suite. A `-k` filter does not prevent an unselected
`agent` fixture from being collected; keep direct-only runs in a separate file
or supply a harness selection for the collected agent test.

Start with [a local stdio contract](#stdio-local-command) or
[a deployed HTTP contract](#streamable-http-deployed-endpoint). Add
[agent choice](#agent-choice-and-multiple-turns) when the claim involves an
agent. Use [evaluations](#evaluations-that-gate-pytest) for quality rules and
[saved feedback](feedback-iteration.md) for repeat runs.

## Same suite across files

Use the existing `m3` marker; do not add another marker:

```python
# tests/catalog_tools.py and tests/catalog_prompts.py
import pytest
pytestmark = pytest.mark.m3(suite_name="catalog")
```

Run both files with `m3 test --suite catalog -- tests/catalog_tools.py
tests/catalog_prompts.py`. Combine this with `--harness`, `--trials`, `-k`, and
`-m`; all filters apply together. The results database assigns one suite ID to
the exact trimmed name. For a standalone SDK run, pass `suite_name` to
`MCPTestKit` or the execution specification instead.

The private SDK release is not installed by bare `uv add "m3[pytest]"`.
Follow [cli-runner.md](cli-runner.md) and `m3 setup` for a version-matched
project SDK. If the user specifically needs
SDK-only use, install the exact release wheel through the target project's
approved dependency workflow and include the required extras. Prefer its
existing server fixture.

## Choose persistence explicitly

`m3 test` loads the pytest plugin and saves run history to SQLite automatically.
Direct SDK/pytest use is in memory unless the kit receives
`store=SQLiteExecutionStore(path)` or pytest loads
`-p m3.pytest_plugin --results-db PATH`. An explicit kit store takes
precedence. See [feedback and iteration](feedback-iteration.md) to reopen and
inspect saved runs. Ordinary Python assertions are represented by the pytest
case outcome; only explicit evaluations and M3 matcher checks become saved
evaluation records.

## Evaluations that gate pytest

Use a deterministic evaluator first when the rule can be computed from the
result. Register it on the same kit that produced the execution. For a direct
call, close the client to finalize its trace, then evaluate while the kit is
still open. Associate the decision with that execution when possible:

```python
from m3.types import EvaluationStatus

kit.register_evaluator(
    "project.quote-currency.v1",
    lambda context: context.subject["currency"] == "USD",
)
decision = kit.evaluate(
    dict(result.structured_content),
    "project.quote-currency.v1",
    execution_id=client.final_trace.view().execution_id,
    required=True,
)
assert decision.status == EvaluationStatus.PASSED
```

The evaluator name is a versioned quality contract: change it when the rule
changes. For an agent result, use `result.snapshot.execution_id` instead.
A saved failed or error decision with `required=False` does not fail pytest.

M3 provides an LLM judge through `m3 setup` or the SDK's `judge` extra.
Register it directly for the standard subject shape, or use `judge_response`:

```python
from m3.judges import LLMJudge

judge = LLMJudge(model="judge-model")
kit.register_evaluator("answer.correctness.v1", judge)
result = kit.evaluate(
    {"input": prompt, "expected": reference, "actual": answer},
    "answer.correctness.v1",
    required=True,
)
assert result.status.value == "passed"
```

The judge receives JSON strings for `input`, `expected`, and `actual`, plus an
optional configured rubric. It returns one JSON object with exactly `score`,
`rationale`, and `abstain`. `score` is a number from 0 to 1, `rationale` is a
concise string, and `abstain` is a boolean. For evidence that cannot be
assessed, use `score: null` and `abstain: true`. The default `json_schema`
response mode enforces this object at the provider; `json_text` parses and
validates its required fields, types, and score range locally. Scores at or
above the configured threshold become PASSED and lower scores become FAILED.
Abstentions and invalid output become ERROR evaluations.

Use `required=True` to persist then raise for failed or error results. The
result exposes `status`, `score`, `rationale`, and safe `details`/provenance.
For advanced subjects, a configured `LLMJudge` can be called inside a
sync or async evaluator callback and return an `EvaluationDecision`. Chained
direct calls share one client execution, agent evaluations can use `turn_id`,
and matrix trials carry matrix/cell/trial metadata. API v2 only reads the
saved result and provenance; it does not run the callback.

For a user-supplied LLM evaluator, the client and credentials stay in your
application:

```python
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationSource, EvaluationStatus

def answer_quality_with_llm(context):
    verdict = my_llm_client.score(context.subject)  # your client and key
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if verdict.ok else EvaluationStatus.FAILED,
        score=verdict.score, rationale=verdict.reason,
        provenance=EvaluationSource(kind="user_llm", provider="acme", model="judge-1"),
    )

kit.register_evaluator("project.answer-quality.llm.v1", answer_quality_with_llm)
kit.evaluate(report, "project.answer-quality.llm.v1", required=True)
```

With SQLite selected, M3 persists the returned result and provenance,
not the client, key, prompt, or hidden LLM state. Keep credentials out of
rationale and metadata. API v2 later groups that saved output and provenance;
it never calls the LLM.

## Streamable HTTP: deployed endpoint

Use `HTTPServer` for one deployed MCP endpoint. This generic pattern
does not assume a provider's tool names or result shape:

```python
from m3 import MCPTestKit
from m3.types import HTTPServer

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

The code above is only a connection smoke test. Replace `assert tools` and
`assert result.is_error is False` with expected catalog, schema, result, and
negative-case assertions before calling it a contract test.

The URL is an MCP protocol endpoint, not a REST route. A direct public endpoint
can use the default `UNTRUSTED` trust. For an agent run, set
`trust=TrustLevel.PUBLIC` on a public endpoint or
`trust=TrustLevel.TRUSTED_PRIVATE` on a private endpoint you own. For a bearer credential, keep the value
in the environment and reference it through direct-client authentication,
without embedding it in the URL:

```python
from m3.types import SecretReference

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
from m3 import MCPTestKit, StdioServer, expect
from m3.types import PermissionPolicy

@pytest.fixture
def shipping_server():
    return StdioServer(
        name="shipping", command=sys.executable,
        args=("-m", "your_package.mcp_server"),
    )

def test_shipping_quote_contract(shipping_server):
    """A local 2 kg quote returns the documented USD amount."""
    with MCPTestKit() as kit, kit.direct(
        shipping_server, validate_schemas=True
    ) as client:
        tool = next(item for item in client.list_all_tools()
                    if item.name == "shipping_quote")
        assert set(tool.input_schema["required"]) == {"weight_kg", "zone"}
        assert "weight" in tool.description.lower()
        result = client.call_tool(
            "shipping_quote", {"weight_kg": 2, "zone": "local"}
        )
    assert result.is_error is False
    assert dict(result.structured_content) == {"amount": 9.0, "currency": "USD"}

@pytest.mark.m3
def test_agent_selects_shipping_quote(agent, shipping_server):
    """The agent uses the shipping service for a local parcel quote."""
    result = agent.run(
        "Get a local shipping quote for a 2 kg parcel.",
        server=shipping_server,
        permission_policy=PermissionPolicy(mode="allow"),
    )
    expect(result).to_have_tool_call(
        "shipping_quote", server="shipping", status="success"
    )
    expect(result).to_not_have_tool_call("always_fails", server="shipping")
```

SDK nested results use immutable mappings and tuples. Treat them as
read-only; compare with `dict(...)` or `list(...)` when an exact standard
container is needed, and access resource/prompt content through its mapping
fields. Do not mutate a returned schema or assume an enum is a list.

When launching a local server or ACP manifest from a script, resolve its path
from `Path(__file__).resolve()`; a subprocess may run from another working
directory. An installed `-m package.module` target is another stable choice.

Run one test for each CLI-selected harness/model and independent trial:

```bash
m3 test --env-file .env \
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
        result = agent.run(
            "Find the shipping tool", server=shipping_server,
            permission_policy=PermissionPolicy(mode="allow"),
        )
        print(agent.harness, agent.model,
              [call.tool.value for call in result.trace_view.tool_calls])
```

Omitting `tools` leaves the server's advertised MCP tools available.
`tools=[]` denies them. For choice tests, keep realistic safe alternatives
available and assert captured wire evidence.

## Expand deterministic coverage

The first passing call is a starting point. Add boundary values and expected
domain errors using the project's contract. A server-returned tool error is a
result with `is_error=True`; local input-schema validation raises an exception
when `validate_schemas=True`. Keep each expected failure in a separate test so
its reason remains visible. Do not assert a specific exception class until
confirmed from the installed release.

```python
def test_unknown_order_is_expected_error(shipping_server):
    """Looking up an unknown order returns a domain error."""
    with MCPTestKit() as kit, kit.direct(shipping_server) as client:
        result = client.call_tool("get_order", {"order_id": "missing-fixture"})
    assert result.is_error is True
    assert "not found" in str(result.content).lower()
```

For a workflow that depends on state, keep one client open and derive the
second call's identifier from the first response:

```python
with MCPTestKit() as kit, kit.direct(shipping_server) as client:
    created = client.call_tool("create_order", {"item": "widget", "quantity": 2})
    assert created.is_error is False
    order_id = created.structured_content["order_id"]
    found = client.call_tool("get_order", {"order_id": order_id})
    assert found.is_error is False
    assert found.structured_content["quantity"] == 2
```

Use `client.list_resources()`, `client.read_resource(uri)`,
`client.list_prompts()`, and `client.get_prompt(name, arguments)` when the server
advertises those surfaces. Assert a known URI, content, prompt arguments and
returned messages. See the runnable
[resource and prompt examples](../../../sdk/docs/examples.md#8-test-resources-prompts-errors-and-schemas).
For async project code, use `AsyncMCPTestKit`, `async with`, and `await`; the
[async examples](../../../sdk/docs/examples.md#9-use-async-apis)
show the corresponding method calls.

For several known calls, `ToolMatrix` makes one collected pytest item per
case. It invokes named tools directly, so it checks their results without a
provider:

```python
import sys
from m3 import StdioServer
from m3.matrix import ServerCase, ToolCase, ToolMatrix

shipping_server = StdioServer(
    name="shipping", command=sys.executable,
    args=("-m", "your_package.mcp_server"),
)
matrix = ToolMatrix(servers=(ServerCase(
    name="shipping", server=shipping_server,
    tools=(
        ToolCase(name="shipping_quote",
                 arguments={"weight_kg": 2, "zone": "local"}),
        ToolCase(name="shipping_quote",
                 arguments={"weight_kg": 2, "zone": "regional"}),
    ),
),))

@matrix.parametrize()
def test_quote_matrix(case):
    """Each zone returns its documented shipping amount."""
    result = case.run().direct_result
    assert result is not None and result.is_error is False
    expected = {"local": 9.0, "regional": 12.0}
    assert result.structured_content["amount"] == expected[case.tool.arguments["zone"]]
```

Define the server at module scope for this matrix; a pytest fixture cannot be
used while the decorator is evaluated. Replace values with independently
documented expectations. See the
[full matrix example](../../../sdk/docs/examples.md#compose-toolmatrix-with-agent-selection).

## Agent choice and multiple turns

Use a natural user request that does not mention the target tool name. Keep
other safe, plausible tools visible. A marked test that requests `agent` needs
`--harness KIND=MODEL` or marker `agents=[...]`; otherwise collection cannot
make a selection. Identify the test service in the prompt when a generic
request could reasonably refer to real-world providers and missing details.
For a trusted native Codex server, allow its tool approvals explicitly:

```python
@pytest.mark.m3
def test_quote_choice(agent, shipping_server):
    """The agent chooses the shipping quote tool for a local parcel."""
    result = agent.run(
        "Use the shipping MCP service here to quote a 2 kg parcel in the "
        "local zone. What amount and currency does its calculator return?",
        server=shipping_server,
        permission_policy=PermissionPolicy(mode="allow"),
    )
    expect(result).to_have_tool_call(
        "shipping_quote", server=shipping_server.name, status="success",
        arguments={"weight_kg": 2, "zone": "local"},
        min_count=1, max_count=1,
    )
    expect(result).to_not_have_tool_call("always_fails")
```

The matcher uses wire-observed calls by default. Match arguments, status,
count, result, and absent calls to the actual claim; a final prose answer
cannot prove tool selection. If a positive matcher fails first, later negative
matchers will not run; inspect the finalized trace for those absent calls or
use grouped `check()` assertions to record both. For a conversation, keep one
session and scope each assertion to its completed turn:

```python
with agent.session(
    server=shipping_server,
    permission_policy=PermissionPolicy(mode="allow"),
) as session:
    first = session.send("Get a delivery quote for 2 kg nearby.")
    second = session.send("Now check the regional price for the same parcel.")

expect(session.result).to_have_tool_call("shipping_quote", turn=first)
expect(session.result).to_have_tool_call("shipping_quote", turn=second)
assert session.result.trace_view.for_turn(first).tool_calls
```

Test a continuing conversation only when the harness and project behavior
support it. If the second turn depends on the first, assert its arguments or
result as well. The [agent examples](../../../sdk/docs/examples.md#5-match-arguments-results-status-counts-and-choices)
cover argument predicates and exact call order.

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

`agent.run(..., timeout=...)` covers startup, turns, and cleanup;
`handle.result(timeout=...)` limits only waiting for a submitted run. For a
timeout diagnosis, inspect trace diagnostics for stage, operation, elapsed,
and configured seconds. Do not print event payloads while debugging.

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
facts from the target project. Direct pytest needs `-p m3.pytest_plugin`
when using the `agent` fixture; `m3 test` loads it automatically.

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
same test with `@pytest.mark.m3`. The plugin keeps a stable logical case
ID across harnesses and trials. Call `kit.evaluate(...)` once per completed
execution or turn and aggregate saved decisions by `metadata.harness_config`.
In a script, use `kit.agents([...], trials=N)` and pass the same explicit
`case_id` for every selection of a logical case. See the
[SDK evaluation guide](../../../sdk/docs/evaluations.md) for a worked example.
