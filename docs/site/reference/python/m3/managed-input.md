---
title: "Managed elicitation input API"
description: "Reference for persistent execution handles, typed pending rounds, keyed response submission, validation, and recovery limits."
---

# Managed elicitation input API

Managed input pauses an agent action at an MCP elicitation request and stores
the request until a caller submits a response. Enable it on an agent execution
with `human_input="managed"`. The execution store must provide persistent
managed-input storage. An in-memory store is not sufficient, and managed input
cannot be combined with an `AgentSpec` that already contains a predefined
elicitation plan.

```python
import os
import time

from m3 import ElicitationResponse, MCPTestKit
from m3.storage import SQLiteExecutionStore

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
                    "runtime": "managed",
                    "version": "0.156.1",
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

This fragment assumes the surrounding application has defined
`shipping_server` as a configured `StdioServer` binding. The model must be
available to the signed-in Codex account. For a complete executable agent workflow, see
[Submit input to a paused execution](../../../guides/elicitation/managed-input.md).

## Public execution API

`MCPTestKit.submit(spec, human_input="fail")` and
`AsyncMCPTestKit.submit(spec, *, human_input="fail")` accept
`HumanInput = Literal["fail", "managed"]`. The default preserves existing
behavior. `"managed"` is valid for `AgentSpec` submissions only and requires a
persistent store with a managed-input store resolver.

Both synchronous and asynchronous execution handles provide:

```python
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

The async handle methods are awaitable. `pending_elicitation()` returns the
single pending round for that execution or `None`. It raises
`ManagedInputStateError` if storage contains more than one pending round.
`respond_elicitation` requires a non-empty round ID and idempotency key.
`snapshot()` reports the execution lifecycle. `result()` waits for the terminal
execution result. `cancel()` cancels execution and is separate from an MCP
elicitation response with `action="cancel"`.

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
optional. `request_state` is opaque and is retained for the server retry.
Request objects include their mode-specific fields plus optional `meta`,
`task`, `server`, `operation_kind`, and `operation_name` context.
When a managed runtime waits on a round with a deadline, expiry raises
`OperationTimeout` and fails the round. The store's direct `submit_responses`
method enforces the worker lease and response state; it records `deadline`,
while the managed runtime owns deadline enforcement.

The pending view deliberately excludes internal worker lease tokens. A caller
uses only the `round_id`, exact keys in `requests`, and an
`idempotency_key` it creates and persists with the response. Never use the
author's sample IDs in an application.

## Response validation and idempotency

The response map must contain exactly the current pending request keys. Each
value must be an `ElicitationResponse`. A form `accept` requires mapping
content that validates against the request schema. A URL response rejects
form content; an accepted URL action only records consent and does not browse
to the URL. `decline` and `cancel` responses carry no content.

Response validation and the transition from `pending` to
`response_validated` commit atomically. Invalid keys or schema content leave
the round pending. Repeating a successful submission with the same
idempotency key and identical response returns the existing record. Reusing
that key with a different response raises `ManagedInputConflict`. Keep and
reuse the same key if a submission retry may follow a lost acknowledgement.
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

`round_index` is zero-based and must be less than `round_limit`. Response maps
are stored with an atomic keyed validation transaction. Persistence uses
SQLite transactions and compare-and-set owner and lease tokens to prevent a
stale worker from overwriting a replacement worker's state. `redacted_responses`
returns a diagnostic projection, not the operational response payload.

## Reopening a store and recovery boundary

Discard the store object and construct a new one on the same database path to
read its managed-input records.
`SQLiteExecutionStore` resolves `managed_input_store` lazily; the corresponding
`SQLiteManagedInputStore` constructed on the same database can read the round
and its response status. This store uses per-operation connections and has no
`close()` method. This storage read does not restart an execution worker or
reopen the native harness action. The managed-input guide demonstrates the
supported store-level check by creating its own execution and round, closing
and reopening SQLite, repeating an identical response with the same key, and
rejecting an expired lease.

Persistence of a round does not prove that a native harness process or its
interactive operation can resume. If a worker disappears while it owns an
unresolved native elicitation, worker recovery marks the managed round `failed`
with `recovery_unavailable` and terminalizes the execution as failed.
The round does not become pending again, and a late response cannot make it
resumable. A lease token, persisted native resume token, or delivery
idempotency key alone is not proof that retrying the native action is safe.
Persistent worker tests verify the reopened terminal result and prevent a
replacement worker from claiming it. See
[execution lifecycle](../../../concepts/lifecycle.md) for terminal outcomes.

## Typed errors

`ManagedInputValidationError` describes malformed IDs, response maps, actions,
or schema content. `ManagedInputConflict` describes stale ownership,
idempotency conflicts, and concurrent updates. `ManagedInputStateError`
describes a transition or response that is invalid for the current state.
`ManagedInputRecoveryError` indicates that the store cannot safely recover or
reclaim a round. Invalid `human_input` values or a predefined plan combined
with managed input raise `ModelValidationError` at submission.

See [Handle elicitation in agent tests](../../../guides/elicitation/agents.md),
[Handle elicitation manually](../../../guides/elicitation/manual.md), and the
[elicitation plan reference](elicitation.md).
