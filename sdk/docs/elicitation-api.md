# Elicitation API reference

This page lists the MRTR API, response binding, action boundaries, manual and managed input, and trace fields. For runnable test patterns, start with the [elicitation guide](elicitation.md).
The wire behavior follows the [MCP elicitation specification (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation).

## Public API inventory

The following names were checked against the branch's focused public manifests:
m3.sync_api.__all__, m3.async_api.__all__, m3.errors.__all__, and
m3.observability.__all__. The root package keeps explicit-import convenience
for the elicitation value/error names, but its reduced m3.__all__ /
PUBLIC_EXPORTS["m3"] tier does not list them. m3.types does not add an
elicitation-specific export.

1. m3.ElicitationPlan
2. m3.ElicitationResponse
3. m3.FormElicitationRequest
4. m3.UrlElicitationRequest
5. m3.PendingElicitationRound
6. m3.expect_form
7. m3.maybe_form
8. m3.expect_url
9. m3.maybe_url
10. m3.sequence
11. m3.optional
12. m3.one_of
13. m3.round_of
14. m3.ElicitationExpectationError
15. m3.ElicitationRoundLimitError
16. m3.errors.ManagedInputError
17. m3.errors.ManagedInputConflict
18. m3.errors.ManagedInputValidationError
19. m3.errors.ManagedInputStateError
20. m3.errors.ManagedInputRecoveryError
21. DirectClient/AsyncDirectClient action-bound elicitation parameter
22. DirectClient/AsyncDirectClient elicitation round-limit parameter
23. DirectClient/AsyncDirectClient manual allow-input-required parameter
24. Selected-agent run action binding and round-limit parameters
25. Selected-agent submit action binding, round-limit, and human-input parameters
26. AgentSession/AsyncAgentSession turn action binding and round-limit parameters
27. ExecutionHandle.pending_elicitation
28. ExecutionHandle.respond_elicitation
29. AsyncExecutionHandle.pending_elicitation
30. AsyncExecutionHandle.respond_elicitation
31. m3.observability.ElicitationEntry
32. m3.observability.ProtocolCallAttempt
33. m3.observability.ToolCallAttempt
34. TraceView.elicitations
35. ToolCallEntry.attempts and ProtocolEntry.attempts

The module-level alias m3.elicitation.ElicitationRequest is intentionally not
part of the supported top-level inventory: it is a type alias for
FormElicitationRequest | UrlElicitationRequest, not a constructor. Callers
should use the numbered models and helpers above.
InputRequiredResult is the existing typed manual escape-hatch result and is
imported from m3.sync_api or m3.async_api.

## Models and errors

### 1. ElicitationPlan

ElicitationPlan is the immutable tree returned by the helper functions. Its
public model fields are node, request, response, children, and
optional_occurrence. Construct leaves with the helpers below so their request
shape is validated. A plan must be complete before it is attached to an
operation.

The public response-binding methods are:

~~~python
from m3 import expect_form, expect_url

form = expect_form("address")
accepted_form = form.accept({"city": "Pune"})
declined_form = expect_form("marketing").decline()
cancelled_form = expect_form("address").cancel()
accepted_url = expect_url(
    "checkout", url="https://example.test/checkout/123"
).accept()

assert accepted_form.is_complete
assert accepted_url.is_complete
assert accepted_form.canonical_json() == accepted_form.canonical_identity()
~~~

.accept(mapping) is required for a form and .accept() is the URL form.
.decline() and .cancel() carry no content. Each returns a new plan.
canonical_identity() and canonical_json() are available on a complete plan.
The matcher implementation is an internal evaluation detail; callers should
use the numbered expectation helpers and bind responses with accept(),
decline(), or cancel().

### 2. ElicitationResponse

The constructor signature is:

~~~text
ElicitationResponse(
    *,
    action: Literal["accept", "decline", "cancel"],
    content: Mapping[str, object] | None = None,
    meta: Mapping[str, object] | None = None,
)
~~~

Use content only with action="accept"; URL acceptance has no form content:

~~~python
from m3 import ElicitationResponse

response = ElicitationResponse(
    action="accept", content={"street": "1 Main Street", "city": "Pune"}
)
decline = ElicitationResponse(action="decline")
cancel = ElicitationResponse(action="cancel", meta={"source": "test"})
~~~

### 3. FormElicitationRequest

The normalized observed request model has this signature:

~~~text
FormElicitationRequest(
    *,
    request_key: str,
    mode: Literal["form"] = "form",
    message: str,
    requested_schema: Mapping[str, object],
    meta: Mapping[str, object] | None = None,
    task: Mapping[str, object] | None = None,
    server: str | None = None,
    operation_kind: Literal["tool", "prompt", "resource"] | None = None,
    operation_name: str | None = None,
)
~~~

~~~python
from m3 import FormElicitationRequest

request = FormElicitationRequest(
    request_key="address",
    message="Where should we deliver?",
    requested_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    operation_kind="tool",
    operation_name="book_shipment",
)
assert request.mode == "form"
~~~

### 4. UrlElicitationRequest

The normalized URL request model has this signature:

~~~text
UrlElicitationRequest(
    *,
    request_key: str,
    mode: Literal["url"] = "url",
    message: str,
    url: str,
    elicitation_id: str | None = None,
    meta: Mapping[str, object] | None = None,
    task: Mapping[str, object] | None = None,
    server: str | None = None,
    operation_kind: Literal["tool", "prompt", "resource"] | None = None,
    operation_name: str | None = None,
)
~~~

~~~python
from m3 import UrlElicitationRequest

request = UrlElicitationRequest(
    request_key="checkout",
    message="Continue checkout",
    url="https://example.test/checkout/123",
)
assert request.url.startswith("https://")
~~~

### 5. PendingElicitationRound

This immutable model describes a durable managed-input round:

~~~text
PendingElicitationRound(
    *,
    round_id: str,
    execution_id: str,
    logical_operation_id: str,
    server: str,
    operation_kind: Literal["tool", "prompt", "resource"],
    operation_name: str,
    request_state: str | None = None,
    requests: Mapping[str, FormElicitationRequest | UrlElicitationRequest],
    created_at: datetime,
    deadline: datetime | None = None,
)
~~~

~~~python
from datetime import datetime, timezone
from m3 import FormElicitationRequest, PendingElicitationRound

pending = PendingElicitationRound(
    round_id="round-1", execution_id="execution-1",
    logical_operation_id="tool-1", server="shipping",
    operation_kind="tool", operation_name="book_shipment",
    requests={"address": FormElicitationRequest(
        request_key="address", message="Address",
        requested_schema={"type": "object"},
    )},
    created_at=datetime.now(timezone.utc),
)
assert pending.requests["address"].request_key == "address"
~~~

### 14–15. Elicitation errors

Both errors use the common constructor
ErrorType(message: str, *, details: Mapping[str, object] | None = None).

- ElicitationExpectationError has code elicitation_expectation_failed for an
  unmatched request, response, or plan.
- ElicitationRoundLimitError has code elicitation_round_limit and terminal is
  True when the bound is exceeded.

~~~python
from m3 import ElicitationExpectationError, ElicitationRoundLimitError

try:
    raise ElicitationExpectationError("unexpected request")
except ElicitationExpectationError as error:
    assert error.code == "elicitation_expectation_failed"

try:
    raise ElicitationRoundLimitError("too many rounds")
except ElicitationRoundLimitError as error:
    assert error.terminal is True
~~~

Plan construction and response-binding validation raises the existing
ModelValidationError; it is not a server tool error.

### 16–20. Managed-input errors

The managed-input error hierarchy is exported by m3.errors. Each class uses
the same constructor as the elicitation errors. ManagedInputError is the base;
ManagedInputConflict reports an ownership or idempotency conflict;
ManagedInputValidationError reports an invalid round or response map;
ManagedInputStateError reports an invalid lifecycle transition; and
ManagedInputRecoveryError reports that a persisted interaction cannot be
resumed safely after worker loss. Runtime recovery treats this as terminal:
callers must not redeliver a response when delivery state is ambiguous.

~~~python
from m3.errors import ManagedInputConflict, ManagedInputRecoveryError

try:
    raise ManagedInputConflict("response key was already used")
except ManagedInputConflict as error:
    assert error.code == "managed_input_conflict"

try:
    raise ManagedInputRecoveryError("delivery cannot be inspected")
except ManagedInputRecoveryError as error:
    assert error.code == "managed_input_recovery_unavailable"
~~~

## Building plans

All four leaf helpers return ElicitationPlan. Their signatures include every
matching parameter:

~~~text
expect_form(
    request_key: str, *, message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: Literal["tool", "prompt", "resource"] | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan

maybe_form(
    request_key: str, *, message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: Literal["tool", "prompt", "resource"] | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan

expect_url(
    request_key: str, *, message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: Literal["tool", "prompt", "resource"] | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan

maybe_url(
    request_key: str, *, message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: Literal["tool", "prompt", "resource"] | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
~~~

### 6–7. expect_form and maybe_form

expect_form requires one matching form request. maybe_form is a zero-or-one
leaf. Both accept the optional message, schema, named server, and operation
metadata:

~~~python
from m3 import expect_form, maybe_form, sequence

required = expect_form(
    "address", message="Delivery address", schema={"type": "object"},
    server="shipping", operation_kind="tool", operation_name="book_shipment",
).accept({"city": "Pune"})
optional_note = maybe_form(
    "delivery_note", schema={"type": "object"},
).accept({"note": "Reception"})
plan = sequence(required, optional_note)
assert plan.is_complete
~~~

An accepted form must contain a mapping; decline and cancel do not contain
content. Responses are keyed by the observed request_key, not child index.

### 8–9. expect_url and maybe_url

expect_url requires one URL request. maybe_url allows zero or one. URL
acceptance has no content argument:

~~~python
from m3 import expect_url, maybe_url, sequence

required = expect_url(
    "checkout", message="Continue checkout",
    url="https://example.test/checkout/123",
    server="shipping", operation_kind="tool", operation_name="book_shipment",
).accept()
optional_verification = maybe_url(
    "verification", url="https://example.test/verify/123"
).accept()
plan = sequence(required, optional_verification)
~~~

URL assertions compare the URL and the declared request fields (request key,
message, server, and operation). They never perform an HTTP request, navigate
a browser, or follow the URL. `elicitation_id` remains an optional matcher
field for transports that surface it. MCP `2026-07-28` removed that field, so
do not set the assertion in current-protocol tests: SDK 2.0.0 exposes it as
`None` even when a low-level fixture constructs a URL request with an ID.

### 10. sequence

~~~python
from m3 import expect_form, expect_url, sequence

plan = sequence(
    expect_form("address").accept({"city": "Pune"}),
    expect_url("checkout", url="https://example.test/pay/123").accept(),
)
~~~

sequence(*children: ElicitationPlan) consumes complete rounds in order.
Repeated request keys are valid in different sequence rounds.

### 11. optional

~~~python
from m3 import expect_form, optional

plan = optional(expect_form("delivery_note").accept({"note": "Reception"}))
~~~

optional(child: ElicitationPlan) consumes zero or one occurrence. Its child
must already be complete.

### 12. one_of

~~~python
from m3 import expect_form, one_of

plan = one_of(
    expect_form("business_address").accept({"kind": "business"}),
    expect_form("home_address").accept({"kind": "home"}),
)
~~~

one_of(*children: ElicitationPlan) consumes exactly one distinct viable
branch. Ambiguous response maps raise ElicitationExpectationError before a
response is released.

### 13. round_of

~~~python
from m3 import expect_form, round_of

plan = round_of(
    expect_form("address").accept({"city": "Pune"}),
    expect_form("contact").accept({"phone": "+91-555-0100"}),
)
~~~

round_of(*children: ElicitationPlan) matches one non-empty, unordered round
containing exactly its required direct leaf keys. Keys must be unique within a
round; responses use those keys.

The helper signatures intentionally have no meta or task matcher parameters.
Observed meta/task fields are preserved on request models, but helper matching
does not compare them. Use request_key, message, schema or URL, server, and
operation fields when those assertions are needed.

## Trace models

### 31. ElicitationEntry

ElicitationEntry is exported from m3.observability and represents one keyed
observed request in the finalized trace. Its exact model signature is the
common TraceEntryBase fields followed by these elicitation fields:

~~~text
ElicitationEntry(
    *,
    entry_id: str, kind: Literal["elicitation"] = "elicitation",
    parent_id: str | None = None, execution_id: ExecutionId,
    session_id: SessionId | None = None, turn_id: TurnId | None = None,
    server_binding: str | None = None, connection_id: ConnectionId | None = None,
    sequence_start: int, sequence_end: int,
    timing: TraceTiming = TraceTiming(), status: TraceStatus = COMPLETED,
    provenance: tuple[EventSource, ...] = (), limitations: tuple[str, ...] = (),
    server: str | None = None,
    operation_kind: Literal["tool", "prompt", "resource"],
    operation_name: str, logical_operation_id: str, round_index: int,
    request_key: str, mode: Literal["form", "url"],
    message: Observation[str] = not_emitted,
    requested_schema: Observation[JsonValue] = not_emitted,
    url: Observation[str] = not_emitted,
    elicitation_id: Observation[str] = not_emitted,
    request_state: Observation[str] = not_emitted,
    input_responses: Observation[JsonValue] = not_emitted,
    action: Literal["accept", "decline", "cancel"] | None = None,
    content: Observation[JsonValue] = not_emitted,
)
~~~

The not_emitted defaults are produced by the observation model; callers should
read an observation's state and value rather than treating None as evidence.

~~~python
from m3.observability import ElicitationEntry
from m3.types import ExecutionId

entry = ElicitationEntry(
    entry_id="elicitation-1", execution_id=ExecutionId("execution-1"),
    sequence_start=1, sequence_end=2, operation_kind="tool",
    operation_name="book_shipment", logical_operation_id="tool-1",
    round_index=1, request_key="address", mode="form", action="accept",
)
assert entry.request_key == "address"
assert entry.operation_kind == "tool"
~~~

### 32–33. ProtocolCallAttempt and ToolCallAttempt

Their exact signatures are:

~~~text
ProtocolCallAttempt(
    *, attempt_index: int, jsonrpc_id: Observation[JsonRpcId] = not_emitted,
    request_state: Observation[str] = not_emitted,
    continuation_state: Observation[str] = not_emitted,
    input_responses: Observation[JsonValue] = not_emitted,
    operation_params: Observation[JsonValue] = not_emitted,
    input_required: bool = False,
    result: Observation[JsonValue] = not_emitted,
    raw_result: Observation[JsonValue] = not_emitted,
    status: TraceStatus = incomplete,
    sequence_start: int, sequence_end: int,
    timing: TraceTiming = TraceTiming(),
)
ToolCallAttempt(
    *, attempt_index: int, jsonrpc_id: Observation[JsonRpcId] = not_emitted,
    request_state: Observation[str] = not_emitted,
    continuation_state: Observation[str] = not_emitted,
    input_responses: Observation[JsonValue] = not_emitted,
    operation_params: Observation[JsonValue] = not_emitted,
    input_required: bool = False,
    result: Observation[ToolResult] = not_emitted,
    raw_result: Observation[JsonValue] = not_emitted,
    status: ToolCallStatus = incomplete,
    sequence_start: int, sequence_end: int,
    timing: TraceTiming = TraceTiming(),
)
~~~

Use ProtocolCallAttempt for prompt/resource attempts and ToolCallAttempt for
tool attempts. The models preserve each wire attempt while the parent call
remains one logical operation:

~~~python
from m3.observability import ProtocolCallAttempt, ToolCallAttempt

tool_attempt = ToolCallAttempt(
    attempt_index=0, sequence_start=10, sequence_end=11, input_required=True,
)
protocol_attempt = ProtocolCallAttempt(
    attempt_index=0, sequence_start=20, sequence_end=21, input_required=True,
)
assert tool_attempt.input_required
assert protocol_attempt.input_required
~~~

### 34. TraceView.elicitations

TraceView.elicitations is a read-only property with signature
elicitations -> tuple[ElicitationEntry, ...]. It filters finalized timeline
entries by kind:

~~~python
view = result.trace_view
for entry in view.elicitations:
    assert entry.request_key
    assert entry.mode in {"form", "url"}
~~~

### 35. Attempts on parent trace entries

ToolCallEntry.attempts has type tuple[ToolCallAttempt, ...], and
ProtocolEntry.attempts has type tuple[ProtocolCallAttempt, ...]. The fields are
empty for ordinary one-shot calls and contain every elicitation retry when a
call asks for input:

~~~python
call = result.trace_view.tool_calls[0]
assert call.attempts[0].input_required is True
assert call.attempts[-1].status.value == "completed"
~~~

## Attach a plan to an action

A plan belongs to the operation or turn that may ask for input. It is not a
session-wide policy.

### Inventory 21–23: direct action parameters

21. The direct action-bound elicitation parameter is
   elicitation: ElicitationPlan | None = None on call_tool, get_prompt, and
   read_resource. It selects the complete plan that owns matching and retries.
22. elicitation_round_limit: int = 10 is the positive bound on input-required
   rounds for those operations and for agent/session actions below.
23. allow_input_required: bool = False is the manual escape hatch on all three
   direct methods. It returns InputRequiredResult without retrying; it cannot be
   combined with elicitation.

The async direct signatures are:

~~~text
await client.call_tool(
    name: str, arguments: Mapping[str, Any] | None = None, *,
    timeout: float | None = None, progress_callback: Any = None,
    input_responses: Any = None, request_state: str | None = None,
    meta: Any = None, allow_input_required: bool = False,
    allow_claimed: bool = False, elicitation: ElicitationPlan | None = None,
    elicitation_round_limit: int = 10,
) -> ToolCallResult | InputRequiredResult

await client.get_prompt(
    name: str, arguments: Mapping[str, str] | None = None, *,
    input_responses: Any = None, request_state: str | None = None,
    meta: Any = None, allow_input_required: bool = False,
    elicitation: ElicitationPlan | None = None,
    elicitation_round_limit: int = 10,
) -> PromptResult | InputRequiredResult

await client.read_resource(
    uri: str, *, input_responses: Any = None,
    request_state: str | None = None, meta: Any = None,
    allow_input_required: bool = False,
    elicitation: ElicitationPlan | None = None,
    elicitation_round_limit: int = 10,
) -> ResourceReadResult | InputRequiredResult
~~~

The blocking DirectClient exposes the same operation names and keyword
arguments through its wrapper:

Planned direct elicitation uses the current MCP protocol. Select it on the
direct client or in the kit configuration; the examples below select it on
`direct()`:

~~~python
from m3 import expect_form, expect_url

form = expect_form("address").accept({"city": "Pune"})
url = expect_url("checkout", url="https://example.test/pay/123").accept()

with kit.direct(server, protocol="2026-07-28") as client:
    tool_result = client.call_tool("book_shipment", {"weight_kg": 2}, elicitation=form)
    prompt_result = client.get_prompt("checkout_prompt", {}, elicitation=url)
    resource_result = client.read_resource("shipping://checkout", elicitation=url)
~~~

A planned call owns the bounded retry loop. input_responses, request_state,
and allow_input_required are manual continuation controls and cannot be
combined with elicitation. The manual example below is the exact example for
inventory item 23.

### Manual escape hatch

Without a plan, an unexpected elicitation request fails. Sampling and roots
requests can still be handled by their configured callbacks. Pass
allow_input_required=True when the test deliberately wants the first typed
pending result and will continue it itself:

~~~python
from m3.sync_api import InputRequiredResult
from mcp import types

with kit.direct(server, protocol="2026-07-28") as client:
    pending = client.call_tool(
        "book_shipment", {"weight_kg": 2}, allow_input_required=True
    )
    assert isinstance(pending, InputRequiredResult)
    result = client.call_tool(
        "book_shipment", {"weight_kg": 2},
        request_state=pending.request_state,
        input_responses={
            "address": types.ElicitResult(action="accept", content={"city": "Pune"})
        },
    )
~~~

For complete server and assertion wiring, use
[test_modern_mrtr_sdk.py](../examples/tests/test_modern_mrtr_sdk.py), rather than
copying a server implementation into a test.

### Inventory 24–26: agent and session action parameters

24. Selected-agent run accepts elicitation and elicitation_round_limit and
   passes both to one execution action.
25. Selected-agent submit accepts elicitation, elicitation_round_limit, and
   human_input: Literal["fail", "managed"] = "fail". Managed is the only
   value that enables pending/respond handling.
26. AgentSession.send and AsyncAgentSession.send accept elicitation and
   elicitation_round_limit on one turn; agent.session itself does not accept
   either parameter.

The public selected-agent methods have these signatures:

~~~text
agent.run(
    message: str | UserMessage, *,
    server: object | None = None, servers: object | None = None,
    tools: object | None = None, elicitation: ElicitationPlan | None = None,
    elicitation_round_limit: int = 10, **options: object,
)

agent.submit(
    message: str | UserMessage, *,
    server: object | None = None, servers: object | None = None,
    tools: object | None = None, elicitation: ElicitationPlan | None = None,
    elicitation_round_limit: int = 10,
    human_input: Literal["fail", "managed"] = "fail", **options: object,
)

session.send(
    message: str | UserMessage, *, timeout: float | None = None,
    metadata: Mapping[str, object] | None = None,
    elicitation: ElicitationPlan | None = None,
    elicitation_round_limit: int = 10,
)
~~~

server and servers are mutually exclusive. tools=None leaves advertised tools
available; tools=[] denies them. agent.run waits for the completed execution.
agent.submit returns an execution handle.

~~~python
plan = expect_form("address").accept({"city": "Pune"})
result = agent.run("Book the shipment", server=server, elicitation=plan)

handle = agent.submit(
    "Book the shipment", server=server, elicitation=plan,
    elicitation_round_limit=3,
)
result = handle.result(timeout=30)

with agent.session(server=server) as session:
    ordinary = session.send("Say hello")
    planned = session.send(
        "Book the shipment", elicitation=plan, elicitation_round_limit=3
    )
~~~

agent.session(..., elicitation=...) is rejected. Attach a plan to the specific
session.send that can elicit. The default bound is ten input-required rounds.
Ten matching rounds receive a final completion attempt; an eleventh
input-required round raises ElicitationRoundLimitError. Sampling-only and
roots-only rounds do not consume plan state, but still count.

The async kit has the same selected-agent surface. AsyncMCPTestKit.agents(...)
returns selections whose run is awaitable; AsyncExecutionHandle methods are
awaitable:

~~~python
import asyncio

async def main():
    async with AsyncMCPTestKit() as kit:
        agent = kit.agents(selections)[0]
        result = await agent.run("Book the shipment", server=server, elicitation=plan)
        handle = agent.submit("Book the shipment", server=server)
        result = await handle.result(timeout=30)

asyncio.run(main())
~~~

The selected-agent and session examples are runnable with the fixtures and
harness selection shown in the maintained Pi examples.

## Managed submission

Managed input is agent-only and separate from a predefined plan. It requires a
persistent managed-input-capable store, such as SQLiteExecutionStore. The
verified Pi path uses installed Pi 0.85.1 with the local deterministic provider
and SQLiteExecutionStore; sync and async same-worker delivery are covered for
form, multi-round, and URL requests. Worker/process restart redelivery is not a
supported guarantee.

### Inventory 27–28: synchronous managed handle methods

27. ExecutionHandle.pending_elicitation() -> PendingElicitationRound | None
   reads the current pending round, or None when no round is published.
28. ExecutionHandle.respond_elicitation(round_id, responses, *,
   idempotency_key) -> None validates and commits keyed responses for the
   pending round.

Their exact signatures are:

~~~text
handle.pending_elicitation() -> PendingElicitationRound | None
handle.respond_elicitation(
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *, idempotency_key: str,
) -> None
~~~

~~~python
from m3 import ElicitationResponse
from m3.storage import SQLiteExecutionStore
from m3.sync_api import MCPTestKit

store = SQLiteExecutionStore("m3-managed.sqlite")
with MCPTestKit(store=store) as kit:
    agent = kit.agents(selections)[0]
    handle = agent.submit("Book the shipment", server=server, human_input="managed")
    pending = handle.pending_elicitation()
    assert pending is not None
    handle.respond_elicitation(
        pending.round_id,
        {"address": ElicitationResponse(action="accept", content={"city": "Pune"})},
        idempotency_key="address-response-1",
    )
    result = handle.result(timeout=30)
~~~

Poll pending_elicitation until the worker publishes the round or use the event
stream. Repeating the same response with the same idempotency key is safe;
reusing the key for a different response is a conflict. A predefined plan
cannot be combined with human_input="managed".

### Inventory 29–30: asynchronous managed handle methods

29. AsyncExecutionHandle.pending_elicitation() ->
   Awaitable[PendingElicitationRound | None] is the async pending-round read.
30. AsyncExecutionHandle.respond_elicitation(round_id, responses, *,
   idempotency_key) -> Awaitable[None] is the async keyed response commit.

AsyncExecutionHandle has corresponding awaitable methods:

~~~text
await handle.pending_elicitation() -> PendingElicitationRound | None
await handle.respond_elicitation(
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *, idempotency_key: str,
) -> None
~~~

~~~python
import asyncio

async def main():
    async with AsyncMCPTestKit(store=store) as kit:
        agent = kit.agents(selections)[0]
        handle = agent.submit("Book the shipment", server=server, human_input="managed")
        pending = await handle.pending_elicitation()
        assert pending is not None
        await handle.respond_elicitation(
            pending.round_id,
            {"address": ElicitationResponse(action="accept", content={"city": "Pune"})},
            idempotency_key="address-response-1",
        )
        result = await handle.result(timeout=30)

asyncio.run(main())
~~~

Worker/process restart, resume tokens, and redelivery after lost ownership are
not supported guarantees. When delivery or worker state is not unambiguous,
callers must observe the terminal recovery error rather than redeliver a
response. The real Pi gate linked above is the evidence for the supported
same-worker managed path.

## Trace assertions

Retries are attempts of one logical operation. Assert the finalized trace,
not the number of wire requests:

~~~python
with kit.direct(server, protocol="2026-07-28") as client:
    result = client.call_tool(
        "book_shipment", {"weight_kg": 2},
        elicitation=expect_form("address").accept({"city": "Pune"}),
    )

view = client.final_trace.view()
call = next(item for item in view.tool_calls if item.tool.value == "book_shipment")
assert len(call.attempts) == 2
assert call.attempts[0].input_required is True
assert call.attempts[1].input_responses.value["address"]["action"] == "accept"
assert call.result.value.is_error is False
assert len(view.elicitations) == 1
assert view.elicitations[0].request_key == "address"
~~~

The complete harness assertion uses the same public trace shape:
session.result.trace_view, TraceView.for_turn(turn), ToolCall.attempts,
attempt.input_required, attempt.request_state, and attempt.input_responses.
A capture observer may record original wire requests, but it does not own
tool choice, dispatch, retries, permissions, or cancellation. For the
Codex-specific protocol boundary and current support status, see
[Codex App Server support and limitations](#codex-app-server-support-and-limitations).

## Codex App Server support and limitations

This is the canonical location for Codex-specific MRTR limitations. The
[harness parity inventory](../tests/mrtr-harness-parity.md) maps every Pi
MRTR scenario to Codex evidence or an explicit pending gap. The integration
uses the unmodified Codex App Server: M3 does not patch Codex, replace its MCP
client, or decide when a tool runs or retries.

**Implementation status: pending full M3 conformance gates.** Real, unmodified
Codex CLI 0.156.1 native characterization is covered by
[`test_real_codex_native_mrtr.py`](../tests/e2e/test_real_codex_native_mrtr.py).
M3 action-association and managed-delivery tests are being added in
[`test_codex_mrtr_action.py`](../tests/unit/test_codex_mrtr_action.py),
[`test_codex_mrtr_association.py`](../tests/unit/test_codex_mrtr_association.py),
and [`test_real_codex_managed_mrtr.py`](../tests/e2e/test_real_codex_managed_mrtr.py).
Do not treat source presence or a skipped installed-binary test as a passing
gate. The required native and managed commands are in the
[E2E test README](../tests/e2e/README.md).

### Ownership boundary

Codex remains authoritative for native elicitation requests, tool selection
and execution, MCP retry scheduling, permission decisions, turn completion,
and cancellation. M3 subscribes to the original MCP request/response envelopes
and the Codex App Server's native elicitation requests. It associates those
observations with the current action plan and, only for an unambiguously
matched native elicitation request, writes the JSON-RPC result using that
request's App Server request ID. Codex then emits its `serverRequest/resolved`
notification and performs the MCP retry itself. M3 does not call the
notification method.

The observer preserves original JSON-RPC IDs and JSON types and continues
forwarding the original transport exchange. It does not synthesize MCP calls,
retry requests, approve tools, visit elicitation URLs, or infer a match from
arrival order or timing. Policy-denied events are excluded. Captures persisted
by M3 remain redacted. A missing, malformed, oversized, ambiguous, or
incomplete observation fails the planned action rather than guessing.

The action subscription starts before Codex can issue the planned operation.
At turn completion, its barrier drains observations already accepted by the
M3 capture manager, including relay-thread callbacks already queued to the
event loop, and waits for the action observer to acknowledge consumed events.
That barrier cannot prove a child-side relay event has arrived when it is still
queued outside M3. The adapter therefore also waits, with a bound, for the
expected native-plan and exact keyed MCP retry evidence before completing the
action. Missing or incomplete evidence is an error.

### Proven Codex 0.156.1 wire behavior

The current deterministic real-binary fixture exercises the native app server
against a local MCP fixture and local deterministic Responses API endpoint; it
does not require a paid provider. It proves these native behaviors only:

| Observation | Proven behavior | M3 handling or remaining gap |
| --- | --- | --- |
| Capability negotiation | Codex uses `server/discover` with 2026 metadata and advertises form and URL elicitation. | M3 must preserve the discovered MCP connection and its original traffic. |
| Request identity | App Server omits the MCP `inputRequests` key, `requestState`, and logical round ID from `mcpServer/elicitation/request`. | M3 matches standard exposed fields plus server identity; it never invents the missing key. Exact association integration remains a required M3 gate. |
| Prompt order | A multi-request round emits all native prompts before any answer is resolved; real Codex emitted the business prompt before the home prompt even though the MCP map was home then business. | Treat prompts as an unordered multiset. Never zip arrival order to map order. |
| Identical prompts | Two requests with identical exposed fields can be distinguished if exposed optional metadata differs. If they are still indistinguishable and their planned answers differ, no safe answer can be chosen. | Send no answer for the ambiguous group; fail the action before partial answering. Equal responses may be used as an indistinguishable multiset only after the complete group is validated. |
| Form schema | Codex exposes `requestedSchema` as JSON. Object key ordering is canonicalized for comparison; JSON value types and array order remain significant. | Compare exact JSON values, not semantic JSON Schema equivalence. A normalization mismatch or schema-value difference fails closed. |
| Metadata | Native params can contain `_meta: null` where MCP omitted `_meta`; server-supplied distinct request metadata survives when Codex exposes it. | Treat null as absent only for this known absence representation. Compare and forward metadata when exposed; do not assume hidden metadata exists. |
| URL response | Codex resolves an accepted URL request as an MCP retry response with `content: {}`. | Normalize the URL accept to the MCP API's accepted empty content representation; never navigate to the URL. |
| Decline and cancel | For form and URL, native decline/cancel reaches the MCP retry with `action` and response `_meta` when present, but no `content`. | Preserve the action and metadata exactly and omit content. M3 adapter conformance tests must verify this mapping. |
| Empty input request map | A state-only `input_required` result with an empty request map is auto-retried by Codex with `requestState` and no `inputResponses`; it creates no native prompt. | Do not consume a plan step or manufacture an answer. Confirm the exact retry through passive wire observation. |
| Round limit | Codex completes nine consecutive MRTR prompts. The tenth request is rejected by Codex with `input_required did not complete within 10 MRTR rounds`; it does not surface a tenth native prompt. | For Codex, the effective supported plan limit is at most nine. Do not retry an unseen tenth prompt. This differs from Pi's tested ten-round capacity. |
| Approval | Tool approval is a separate App Server request marked `_meta.codex_approval_kind=mcp_tool_call`. | Leave approval with Codex and its permission policy. Never answer it from an MRTR plan. |
| Interruption | Cancelling before an answer interrupts the turn; Codex emits no MCP retry/cancel notification and does not resolve the outstanding native request. | Let Codex own cancellation. M3 must fail or terminalize the pending planned action and must not fabricate a retry or resolution. |

### API and example coverage boundary

The public direct sync/async APIs remain harness-independent and cover tools,
prompts, resources, manual `allow_input_required`, form/URL, accept/decline/
cancel, current-round keyed responses, schema validation, sampling/roots
callbacks, round limits, and trace projection in
[`test_direct_client_mrtr.py`](../tests/integration/test_direct_client_mrtr.py)
and related shared tests. These are common API tests; they are not duplicated
for each harness.

The Codex harness boundary is action-bound `agent.run`, `agent.submit`, and
`session.send`; `agent.session(elicitation=...)` remains invalid because a
plan belongs to one specific action. Native Codex MRTR currently surfaces tool
elicitation. Prompt/resource direct-client examples and direct sampling/roots
tests do not prove that Codex can provide those callbacks. Treat non-tool
harness operations and sampling/roots inside a Codex native round as unsupported
unless an explicit native capability and M3 test are added. See the parity
matrix for each mapped example and test.
