<!-- Generated from docs/site/reference/python/m3/managed-input.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Managed elicitation input API

With `human_input="managed"`, an agent action pauses at an MCP elicitation
request while M3 stores the pending round. The caller can then read the request
and submit a response. This mode requires persistent managed-input storage and
cannot be combined with an `AgentSpec` that already has an elicitation plan.

```python
import os
import time

from m3 import ElicitationResponse, MCPTestKit
from m3.storage import SQLiteExecutionStore
from m3.types import ExecutionStatus

# `shipping_server` is the application's configured stdio server binding.
# Set this to a model enabled for the signed-in Codex account.
store = SQLiteExecutionStore("executions.sqlite")
try:
    with MCPTestKit(store=store, env={}) as kit:
        agent = kit.agents(
            [
                {
                    "harness": "codex",
                    "models": [os.environ["M3_DOCS_CODEX_MODEL"]],
                }
            ]
        )[0]
        handle = agent.submit(
            "Ask for the delivery address, then book the shipment.",
            server=shipping_server,
            tools=["shipping:book_shipment"],
            human_input="managed",
        )
        deadline = time.monotonic() + 120
        pending = handle.pending_elicitation()
        while pending is None and time.monotonic() < deadline:
            snapshot = handle.snapshot()
            if snapshot.lifecycle is ExecutionStatus.FINISHED:
                raise RuntimeError(
                    "execution finished before it requested managed input: "
                    f"{snapshot.outcome}"
                )
            time.sleep(0.2)
            pending = handle.pending_elicitation()
        if pending is None:
            raise TimeoutError("execution did not request managed input")
        handle.respond_elicitation(
            pending.round_id,
            {
                "shipping_address": ElicitationResponse(
                    action="accept",
                    content={"street": "1 Main Street", "city": "Pune"},
                )
            },
            idempotency_key=f"address-{pending.round_id}",
        )
        result = handle.result(timeout=120)
finally:
    store.close()
```

The surrounding application must define `shipping_server` as a configured
`StdioServer` binding, and the signed-in Codex account must have access to the
selected model. `tools=["shipping:book_shipment"]` limits the agent to that
tool, and M3 approves Codex's calls to it from that selection.
`permission_policy` only covers native prompts outside the selected tools. For
a complete agent
workflow, see
[Submit input to a paused execution](guides-elicitation-managed-input.md).

## Public execution API

`MCPTestKit.submit(spec, human_input="fail")` and
`AsyncMCPTestKit.submit(spec, *, human_input="fail")` accept
`HumanInput = Literal["fail", "managed"]`. The default preserves existing
behavior. `"managed"` is valid for `AgentSpec` submissions only and requires a
persistent store with a managed-input store resolver.

Both synchronous and asynchronous execution handles provide:

```text
handle.pending_elicitation() -> PendingElicitationRound | None

handle.respond_elicitation(
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *,
    idempotency_key: str,
) -> None

handle.snapshot() -> ExecutionState
handle.result(timeout: float | None = None) -> ExecutionResult
handle.cancel() -> None
```

The asynchronous handle methods are awaitable. `pending_elicitation()` returns
the single pending round for that execution or `None`. It raises
`ManagedInputStateError` if storage contains more than one pending round.
`respond_elicitation` requires a non-empty round ID and idempotency key.
`snapshot()` reports the execution lifecycle. `result()` waits for the terminal
execution result. `cancel()` cancels the execution; `action="cancel"` is an MCP
elicitation response to one request.

## Pending request fields

`PendingElicitationRound` has these required fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `round_id` | `str` | Durable identifier for this request round. |
| `execution_id` | `str` | Execution that owns the round. |
| `logical_operation_id` | `str` | Logical tool, prompt, or resource action identity. |
| `server` | `str` | Server name that produced the request. |
| `operation_kind` | `Literal["tool", "prompt", "resource"]` | MCP operation category. |
| `operation_name` | `str` | Tool name, prompt name, or resource URI. |
| `requests` | `Mapping[str, FormElicitationRequest \| UrlElicitationRequest]` | Non-empty requests keyed by each request's matching `request_key`. |
| `created_at` | `datetime` | Round creation time. |

`request_state: str | None = None` and `deadline: datetime | None = None` are
optional. M3 retains the opaque `request_state` for the server retry.
Request objects include their mode-specific fields plus optional `meta`,
`task`, `server`, `operation_kind`, and `operation_name` context.
When a managed runtime waits on a round with a deadline, expiry raises
`OperationTimeout` and fails the round. The store's direct `submit_responses`
method enforces the worker lease and response state. `deadline` is stored
with the pending round when `create_round` persists it, and the managed runtime
enforces it.

`PendingElicitationRound` excludes internal worker lease tokens. Callers use
the `round_id`, the exact keys in `requests`, and an `idempotency_key` created
and persisted with the response. Application code must use IDs from its own
pending round, not the sample IDs shown here.

## Response validation and idempotency

The response map must contain exactly the current pending request keys. Each
value must be an `ElicitationResponse`. A form `accept` requires mapping
content that validates against the request schema. A URL response rejects
form content; an accepted URL action only records consent and does not browse
to the URL. `decline` and `cancel` responses carry no content.

The store validates the response and changes `pending` to
`response_validated` in one transaction. Invalid keys or schema content leave
the round pending. Repeating a successful submission with the same idempotency
key and response returns the existing record; using that key for a different
response raises `ManagedInputConflict`. Persist the key with the response so a
retry after a lost acknowledgement reuses both.
Expired leases, a replaced worker owner, a nonexistent round, and terminal
rounds reject stale writes with typed managed-input errors.

## Persistent store contract

`ManagedInputStore` is the protocol resolved by persistent execution stores.
Its record types are public in `m3.storage`:

- `ManagedInputLease(owner_id, lease_token, expires_at)` is the compare-and-set
  worker lease.
- `ManagedInputRecord` contains `pending`, `round_index`, `round_limit`,
  `status`, `lease`, optional responses and idempotency keys, optional harness
  session/resume and turn metadata, `operation_parameters` (empty mapping by
  default), `delivery_attempts` (default `0`), lifecycle timestamps, and
  optional failure code/message.
- `ManagedInputStatus` is one of `pending`, `response_validated`,
  `delivery_started`, `delivered`, `resolved`, or `failed`.
- `SQLiteManagedInputStore(database, *, busy_timeout_ms=5000, clock=...)` is
  the provided SQLite implementation. When omitted, `clock` uses current UTC
  time. A supplied `clock` must return timezone-aware datetimes.

The store protocol methods are `create_round`, `get_round`, `list_rounds`,
`claim_round`, `renew`, `submit_responses`, `start_delivery`, `mark_delivered`,
`resolve`, `fail`, `fail_recovery`, and `redacted_responses`. These are storage
and worker coordination APIs; application code should submit through the
execution handle so the worker is notified after a response commit.

`round_index` is zero-based and must be less than `round_limit`. The store
uses SQLite transactions and compare-and-set owner and lease tokens to stop a
stale worker from overwriting a replacement worker's state.
`redacted_responses` returns a diagnostic projection instead of the operational
response payload.

## Reopening a store and recovery boundary

To read managed-input records after reopening a database, discard the store
object and construct another one with the same path. `SQLiteExecutionStore`
resolves `managed_input_store` lazily, and a `SQLiteManagedInputStore` on the
same database can read the round and its response status. The managed-input
store uses one connection per operation and has no `close()` method. Reading
the stored round does not restart a worker or reopen the native harness action.

Persisting a round does not make a native harness process resumable. If a
worker disappears while it owns an unresolved native elicitation, recovery
marks the round `failed` with `recovery_unavailable` and finishes the execution
as failed. The round does not become pending again, and a late response cannot
resume it. A lease token, native resume token, or delivery idempotency key is
not enough to make a retry safe. The reopened execution keeps its terminal
result, and a replacement worker cannot claim the round. See
[execution lifecycle](concepts-lifecycle.md) for terminal outcomes.

## Typed errors

`ManagedInputValidationError` describes malformed IDs, response maps, actions,
or schema content. `ManagedInputConflict` describes stale ownership,
idempotency conflicts, and concurrent updates. `ManagedInputStateError`
describes a transition or response that is invalid for the current state.
`ManagedInputRecoveryError` indicates that the store cannot safely recover or
reclaim a round. Invalid `human_input` values or a predefined plan combined
with managed input raise `ModelValidationError` at submission.

See [Handle elicitation in agent tests](guides-elicitation-agents.md),
[Handle elicitation manually](guides-elicitation-manual.md), and the
[elicitation plan reference](reference-python-m3-elicitation.md).
