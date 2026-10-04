---
title: "m3.storage"
description: "Public Python API reference for m3.storage."
---

# `m3.storage`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `ACPProbeDimension`

```python
m3.storage.ACPProbeDimension(
    *,
    profile_id: str,
    revision_id: str,
    probe_type: m3.services.acp_probes.ACPProbeKind,
    transport: str = 'stdio',
    agent_mode_id: str | None = None,
    session_config: collections.abc.Mapping[str, JsonValue] = ...,
) -> None
```

Exact profile/revision and ACP request dimensions.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `profile_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `revision_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `probe_type` | `m3.services.acp_probes.ACPProbeKind` | Yes | — | — | — |
| `transport` | `str` | No | `'stdio'` | `min_length=1, max_length=32` | — |
| `agent_mode_id` | `str \| None` | No | `None` | `max_length=256` | — |
| `session_config` | `collections.abc.Mapping[str, JsonValue]` | No | `factory builtins.dict()` | — | — |
- `kind` (property)
- `mode_id` (property)
- `stable_session_config` (property)
- `stable_key` (property)

## `ACPProbeResult`

```python
m3.storage.ACPProbeResult(
    *,
    profile_id: str,
    revision_id: str,
    probe_type: m3.services.acp_probes.ACPProbeKind,
    transport: str = 'stdio',
    agent_mode_id: str | None = None,
    session_config: collections.abc.Mapping[str, JsonValue] = ...,
    id: str = ...,
    status: m3.services.acp_probes.ACPProbeStatus,
    agent_capabilities: collections.abc.Mapping[str, JsonValue] = ...,
    config_options: tuple[collections.abc.Mapping[str, JsonValue], ...] = (),
    evidence: collections.abc.Mapping[str, JsonValue] = ...,
    diagnostics: str | None = None,
    error: str | None = None,
    created_at: datetime.datetime = ...,
    started_at: datetime.datetime | None = None,
    finished_at: datetime.datetime | None = None,
    duration_ms: float | None = None,
    agent_identity: m3.services.acp_probes.ACPAgentIdentity | None = None,
    agent_modes: tuple[m3.services.acp_probes.ACPAgentMode, ...] = (),
    current_agent_mode_id: str | None = None,
) -> None
```

One terminal or in-flight probe observation.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `profile_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `revision_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `probe_type` | `m3.services.acp_probes.ACPProbeKind` | Yes | — | — | — |
| `transport` | `str` | No | `'stdio'` | `min_length=1, max_length=32` | — |
| `agent_mode_id` | `str \| None` | No | `None` | `max_length=256` | — |
| `session_config` | `collections.abc.Mapping[str, JsonValue]` | No | `factory builtins.dict()` | — | — |
| `id` | `str` | No | `factory m3.services.acp_probes.ACPProbeResult.<lambda>()` | `min_length=1, max_length=256` | — |
| `status` | `m3.services.acp_probes.ACPProbeStatus` | Yes | — | — | — |
| `agent_capabilities` | `collections.abc.Mapping[str, JsonValue]` | No | `factory builtins.dict()` | — | — |
| `config_options` | `tuple[collections.abc.Mapping[str, JsonValue], ...]` | No | `()` | — | — |
| `evidence` | `collections.abc.Mapping[str, JsonValue]` | No | `factory builtins.dict()` | — | — |
| `diagnostics` | `str \| None` | No | `None` | `max_length=65536` | — |
| `error` | `str \| None` | No | `None` | `max_length=2048` | — |
| `created_at` | `datetime.datetime` | No | `factory m3.services.acp_probes.ACPProbeResult.<lambda>()` | — | — |
| `started_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `finished_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `duration_ms` | `float \| None` | No | `None` | `ge=0` | — |
| `agent_identity` | `m3.services.acp_probes.ACPAgentIdentity \| None` | No | `None` | — | — |
| `agent_modes` | `tuple[m3.services.acp_probes.ACPAgentMode, ...]` | No | `()` | — | — |
| `current_agent_mode_id` | `str \| None` | No | `None` | `max_length=256` | — |
- `kind` (property)
- `mode_id` (property)
- `stable_key` (property)
- `stable_session_config` (property)

## `ACPProbeStore`

```python
m3.storage.ACPProbeStore(
    *args,
    **kwargs,
)
```


```python
save_acp_probe(
    self,
    result: ACPProbeResult,
) -> ACPProbeResult
```

```python
get_acp_probe(
    self,
    probe_id: str,
) -> ACPProbeResult | None
```

```python
list_acp_probes(
    self,
    dimension: ACPProbeDimension | None = None,
    *,
    include_inflight: bool = True,
) -> tuple[ACPProbeResult, ...]
```

```python
latest_acp_probe(
    self,
    dimension: ACPProbeDimension,
) -> ACPProbeResult | None
```

## `ArtifactNotFound`

```python
m3.storage.ArtifactNotFound
```

An artifact or content-addressed blob is not present.

## `ArtifactStore`

```python
m3.storage.ArtifactStore(
    *args,
    **kwargs,
)
```

Content-addressed artifact/blob store contract.


```python
put(
    self,
    execution_id: ExecutionId | str,
    name: str,
    content: bytes,
    *,
    media_type: str | None = None,
) -> ArtifactRef
```

```python
get(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> bytes
```

```python
get_ref(
    self,
    artifact_id: ArtifactId | str,
) -> ArtifactRef
```

```python
iter_refs(
    self,
    execution_id: ExecutionId | str | None = None,
) -> Iterator[ArtifactRef]
```

```python
delete(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> None
```

```python
cleanup(
    self,
) -> None
```

## `BlobIntegrityError`

```python
m3.storage.BlobIntegrityError
```

A compressed blob does not match its recorded hash or length.

## `BlobRecord`

```python
m3.storage.BlobRecord(
    sha256: str,
    size_bytes: int,
    compressed_size_bytes: int,
    path: Path,
) -> None
```

Verified metadata for one compressed content-addressed blob.

## `Command`

```python
m3.storage.Command(
    id: str,
    execution_id: ExecutionId,
    kind: str,
    status: str,
    payload: Mapping[str, Any],
    session_id: SessionId | None = None,
    turn_id: TurnId | None = None,
    queue_key: str | None = None,
) -> None
```

## `DurableSerializationError`

```python
m3.storage.DurableSerializationError(
    reason: str = 'durable value is invalid',
) -> None
```

A value cannot safely cross a durable specification boundary.

## `EventCallback`

```python
m3.storage.EventCallback(
    *args,
    **kwargs,
)
```

## `ExecutionStore`

```python
m3.storage.ExecutionStore(
    *args,
    **kwargs,
)
```

Store contract for immutable execution snapshots and event streams.


```python
create(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, object] | None = None,
    provenance: Mapping[str, object] | None = None,
    server_bindings: Sequence[Mapping[str, object]] = (),
    harness_binding: Mapping[str, object] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
get_snapshot(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionState | None
```

```python
get_execution_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```

```python
list_executions(
    self,
    *,
    limit: int = 50,
    offset: int = 0,
    lifecycle: ExecutionStatus | str | None = None,
    outcome: ExecutionOutcome | str | None = None,
    run_id: RunId | str | None = None,
    suite_id: int | None = None,
    project_id: str | None = None,
) -> ExecutionPage
```

```python
get_report(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
    event_limit: int | None = None,
    artifact_limit: int | None = None,
) -> ExecutionReport | None
```

```python
get_trace(
    self,
    execution_id: ExecutionId | str,
) -> TraceResult | None
```

```python
get_trace_view(
    self,
    execution_id: ExecutionId | str,
) -> TraceView | None
```

```python
save_evaluation(
    self,
    execution_id: ExecutionId | str,
    result: EvaluationResult | Mapping[str, object],
    *,
    evaluation_id: str | None = None,
    turn_id: TurnId | str | None = None,
) -> str
```

```python
evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
aggregate_evaluations(
    self,
    query: EvaluationQuery,
) -> EvaluationReport
```

```python
turns(
    self,
    execution_id: ExecutionId | str,
) -> tuple[tuple[TurnState, TurnResult | None], ...]
```

```python
save_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
append_events(
    self,
    events: Sequence[Event],
) -> None
```

```python
append_event(
    self,
    event: Event,
    content: bytes,
    *,
    media_type: str,
) -> Event
```

```python
iter_events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> Iterator[Event]
```

```python
transaction(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionTransaction
```

```python
release(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```

```python
subscribe(
    self,
    execution_id: ExecutionId | str,
    callback: EventCallback,
) -> Callable[[], None]
```

```python
put_raw_evidence(
    self,
    event_id: EventId | str,
    content: bytes,
    *,
    media_type: str,
) -> EvidenceCapture
```

```python
read_raw_evidence(
    self,
    reference: EvidenceRef,
    *,
    max_bytes: int = 1048576,
) -> RawEvidence
```

```python
delete_execution(
    self,
    execution_id: ExecutionId | str,
) -> None
```

## `ExecutionTransaction`

```python
m3.storage.ExecutionTransaction(
    *args,
    **kwargs,
)
```

Uncommitted event batch used by :class:`ExecutionStore`.


```python
append(
    self,
    events: Sequence[Event],
) -> None
```

```python
commit(
    self,
) -> None
```

```python
rollback(
    self,
) -> None
```

## `FilesystemBlobStore`

```python
m3.storage.FilesystemBlobStore(
    root: str | os.PathLike[str],
    *,
    max_read_bytes: int = 536870912,
) -> None
```

Atomic, compressed, content-addressed filesystem storage.

- `root` (property)

```python
path_for(
    self,
    sha256: str,
) -> Path
```

```python
put(
    self,
    content: bytes,
    *,
    sha256: str | None = None,
    size_bytes: int | None = None,
) -> BlobRecord
```
Atomically persist ``content`` and return verified metadata.

```python
put_blob(
    self,
    content: bytes,
    *,
    sha256: str | None = None,
    size_bytes: int | None = None,
) -> BlobRecord
```
Atomically persist ``content`` and return verified metadata.

```python
read(
    self,
    sha256: str,
    *,
    size_bytes: int | None = None,
) -> bytes
```

```python
get(
    self,
    sha256: str,
    *,
    size_bytes: int | None = None,
) -> bytes
```

```python
read_blob(
    self,
    sha256: str,
    *,
    size_bytes: int | None = None,
) -> bytes
```

```python
verify(
    self,
    sha256: str,
    size_bytes: int,
) -> BlobRecord
```

```python
iter_records(
    self,
) -> tuple[BlobRecord, ...]
```

```python
garbage_collect(
    self,
    references: Mapping[str, int] | Iterable[str],
) -> tuple[str, ...]
```
Delete only explicitly unreferenced, valid blobs.

```python
collect_garbage(
    self,
    references: Mapping[str, int] | Iterable[str],
) -> tuple[str, ...]
```
Delete only explicitly unreferenced, valid blobs.

```python
cleanup_temporary_files(
    self,
) -> tuple[Path, ...]
```
Remove incomplete temp files left by interrupted writers.

## `InMemoryArtifactStore`

```python
m3.storage.InMemoryArtifactStore(
    *,
    config: RedactionConfig | None = None,
) -> None
```

In-memory compressed, content-addressed artifact store.


```python
put(
    self,
    execution_id: ExecutionId | str,
    name: str,
    content: bytes,
    *,
    media_type: str | None = None,
) -> ArtifactRef
```

```python
get(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> bytes
```

```python
get_ref(
    self,
    artifact_id: ArtifactId | str,
) -> ArtifactRef
```

```python
iter_refs(
    self,
    execution_id: ExecutionId | str | None = None,
) -> Iterator[ArtifactRef]
```

```python
delete(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> None
```

```python
cleanup(
    self,
) -> None
```

```python
close(
    self,
) -> None
```

## `InMemoryExecutionStore`

```python
m3.storage.InMemoryExecutionStore(
    *,
    config: RedactionConfig | None = None,
    capture_config: CaptureOptions | None = None,
) -> None
```

Thread-safe execution metadata store with commit-gated visibility.


```python
save_acp_probe(
    self,
    result: ACPProbeResult,
) -> ACPProbeResult
```

```python
get_acp_probe(
    self,
    probe_id: str,
) -> ACPProbeResult | None
```

```python
list_acp_probes(
    self,
    dimension: ACPProbeDimension | None = None,
    *,
    include_inflight: bool = True,
) -> tuple[ACPProbeResult, ...]
```

```python
latest_acp_probe(
    self,
    dimension: ACPProbeDimension,
) -> ACPProbeResult | None
```

```python
create(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, object] | None = None,
    provenance: Mapping[str, object] | None = None,
    server_bindings: Sequence[Mapping[str, object]] = (),
    harness_binding: Mapping[str, object] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
ensure_project(
    self,
    project_id: str,
    project_name: str,
) -> tuple[str, str]
```

```python
get_project(
    self,
    project_id: str,
) -> tuple[str, str] | None
```

```python
ensure_suite(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite
```

```python
get_suite(
    self,
    suite_id: SuiteId | str,
) -> Suite | None
```

```python
get_suite_by_name(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite | None
```

```python
list_suites(
    self,
) -> tuple[Suite, ...]
```

```python
create_execution(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, object] | None = None,
    provenance: Mapping[str, object] | None = None,
    server_bindings: Sequence[Mapping[str, object]] = (),
    harness_binding: Mapping[str, object] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
save_test_run(
    self,
    run_id: str,
    value: Mapping[str, object],
) -> None
```

```python
get_test_run(
    self,
    run_id: str,
) -> Mapping[str, object] | None
```

```python
list_test_runs(
    self,
) -> tuple[Mapping[str, object], ...]
```

```python
list_test_run_page(
    self,
    *,
    limit: int | None = None,
    offset: int = 0,
    suite_id: int | None = None,
    project_id: str | None = None,
    q: str | None = None,
) -> tuple[tuple[Mapping[str, object], ...], int]
```
Return newest-first run manifests with their suites, and the total.

```python
save_test_result(
    self,
    run_id: str,
    attempt_id: str,
    value: Mapping[str, object],
) -> None
```

```python
list_test_results(
    self,
    run_id: str,
) -> tuple[Mapping[str, object], ...]
```

```python
get_snapshot(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionState | None
```

```python
get_execution_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```

```python
list_executions(
    self,
    *,
    limit: int = 50,
    offset: int = 0,
    lifecycle: ExecutionStatus | str | None = None,
    outcome: ExecutionOutcome | str | None = None,
    run_id: RunId | str | None = None,
    suite_id: int | None = None,
    project_id: str | None = None,
) -> ExecutionPage
```

```python
get_report(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
    event_limit: int | None = None,
    artifact_limit: int | None = None,
) -> ExecutionReport | None
```

```python
save_evaluation(
    self,
    execution_id: ExecutionId | str,
    result: EvaluationResult | Mapping[str, object],
    *,
    evaluation_id: str | None = None,
    turn_id: object | None = None,
) -> str
```

```python
save_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | None = None,
) -> None
```

```python
append_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | None = None,
) -> None
```

```python
turns(
    self,
    execution_id: ExecutionId | str,
) -> tuple[tuple[TurnState, TurnResult | None], ...]
```

```python
evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: object | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
aggregate_evaluations(
    self,
    query: EvaluationQuery,
) -> EvaluationReport
```
Calculate summaries from the evaluations currently in memory.

```python
get_trace(
    self,
    execution_id: ExecutionId | str,
) -> TraceResult | None
```

```python
get_trace_view(
    self,
    execution_id: ExecutionId | str,
) -> TraceView | None
```

```python
tool_call_counts(
    self,
    execution_ids: Sequence[ExecutionId | str],
) -> dict[str, tuple[int, int]]
```
Return ``{execution_id: (total, successful)}`` tool-call counts.

```python
save_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
update_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
append_events(
    self,
    events: Sequence[Event],
) -> None
```

```python
append(
    self,
    events: Sequence[Event],
) -> None
```

```python
append_event(
    self,
    event: Event,
    content: bytes,
    *,
    media_type: str,
) -> Event
```
Commit an event and its raw blob as one in-memory operation.

```python
iter_events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> Iterator[Event]
```

```python
events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> tuple[Event, ...]
```

```python
transaction(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionTransaction
```

```python
subscribe(
    self,
    execution_id: ExecutionId | str,
    callback: EventCallback,
) -> Callable[[], None]
```

```python
allocate(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```
Reserve the next per-execution sequence numbers.

```python
allocate_sequence(
    self,
    execution_id: ExecutionId | str,
) -> int
```

```python
allocate_sequences(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```
Reserve the next per-execution sequence numbers.

```python
release(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```
Release uncommitted reservations after producer cancellation.

```python
release_sequences(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```
Release uncommitted reservations after producer cancellation.

```python
close(
    self,
) -> None
```

```python
put_raw_evidence(
    self,
    event_id: EventId | str,
    content: bytes,
    *,
    media_type: str,
) -> EvidenceCapture
```

```python
read_raw_evidence(
    self,
    reference: EvidenceRef,
    *,
    max_bytes: int = 1048576,
) -> RawEvidence
```

```python
delete_execution(
    self,
    execution_id: ExecutionId | str,
) -> None
```

```python
delete(
    self,
    execution_id: ExecutionId | str,
) -> None
```

## `Lease`

```python
m3.storage.Lease(
    execution_id: ExecutionId,
    owner_id: str,
    lease_token: str,
    expires_at: datetime,
) -> None
```

## `ManagedInputLease`

```python
m3.storage.ManagedInputLease(
    *,
    owner_id: str,
    lease_token: str,
    expires_at: datetime.datetime,
) -> None
```

The compare-and-set token currently allowed to mutate a round.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `owner_id` | `str` | Yes | — | `min_length=1` | — |
| `lease_token` | `str` | Yes | — | `min_length=1` | — |
| `expires_at` | `datetime.datetime` | Yes | — | — | — |

## `ManagedInputRecord`

```python
m3.storage.ManagedInputRecord(
    *,
    pending: m3.elicitation.PendingElicitationRound,
    round_index: int,
    round_limit: int,
    status: Literal['pending', 'response_validated', 'delivery_started', 'delivered', 'resolved', 'failed'],
    lease: m3.storage.managed_input.ManagedInputLease,
    responses: collections.abc.Mapping[str, m3.elicitation.ElicitationResponse] | None = None,
    response_idempotency_key: str | None = None,
    harness_session_id: str | None = None,
    native_resume_token: str | None = None,
    delivery_idempotency_key: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    operation_parameters: collections.abc.Mapping[str, object] = ...,
    delivery_attempts: int = 0,
    created_at: datetime.datetime,
    updated_at: datetime.datetime,
    response_validated_at: datetime.datetime | None = None,
    delivery_started_at: datetime.datetime | None = None,
    delivered_at: datetime.datetime | None = None,
    resolved_at: datetime.datetime | None = None,
    failed_at: datetime.datetime | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> None
```

Immutable view of one durable managed-input round.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `pending` | `m3.elicitation.PendingElicitationRound` | Yes | — | — | — |
| `round_index` | `int` | Yes | — | `ge=0` | — |
| `round_limit` | `int` | Yes | — | `gt=0` | — |
| `status` | `Literal['pending', 'response_validated', 'delivery_started', 'delivered', 'resolved', 'failed']` | Yes | — | — | — |
| `lease` | `m3.storage.managed_input.ManagedInputLease` | Yes | — | — | — |
| `responses` | `collections.abc.Mapping[str, m3.elicitation.ElicitationResponse] \| None` | No | `None` | — | — |
| `response_idempotency_key` | `str \| None` | No | `None` | — | — |
| `harness_session_id` | `str \| None` | No | `None` | — | — |
| `native_resume_token` | `str \| None` | No | `None` | — | — |
| `delivery_idempotency_key` | `str \| None` | No | `None` | — | — |
| `session_id` | `str \| None` | No | `None` | — | — |
| `turn_id` | `str \| None` | No | `None` | — | — |
| `operation_parameters` | `collections.abc.Mapping[str, object]` | No | `factory builtins.dict()` | — | — |
| `delivery_attempts` | `int` | No | `0` | `ge=0` | — |
| `created_at` | `datetime.datetime` | Yes | — | — | — |
| `updated_at` | `datetime.datetime` | Yes | — | — | — |
| `response_validated_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `delivery_started_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `delivered_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `resolved_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `failed_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `failure_code` | `str \| None` | No | `None` | — | — |
| `failure_message` | `str \| None` | No | `None` | — | — |
- `execution_id` (property)
- `round_id` (property)
- `request_state` (property)
- `lease_token` (property)
- `owner_id` (property)

## `ManagedInputStatus`

```python
m3.storage.ManagedInputStatus(
    *args,
    **kwargs,
)
```

## `ManagedInputStore`

```python
m3.storage.ManagedInputStore(
    *args,
    **kwargs,
)
```

Storage contract consumed by future execution handles.


```python
create_round(
    self,
    pending: PendingElicitationRound,
    *,
    round_index: int,
    round_limit: int,
    owner_id: str,
    lease_seconds: float,
    lease_token: str | None = None,
    harness_session_id: str | None = None,
    native_resume_token: str | None = None,
    delivery_idempotency_key: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    operation_parameters: Mapping[str, object] | None = None,
) -> ManagedInputRecord
```

```python
get_round(
    self,
    execution_id: str,
    round_id: str,
) -> ManagedInputRecord | None
```

```python
list_rounds(
    self,
    execution_id: str,
) -> tuple[ManagedInputRecord, ...]
```

```python
claim_round(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_seconds: float,
    expected_lease_token: str | None = None,
    delivery_state: Literal['not_started', 'not_delivered', 'delivered'] | None = None,
    idempotent_delivery: bool = False,
) -> ManagedInputRecord
```

```python
renew(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
    lease_seconds: float,
) -> ManagedInputRecord
```

```python
submit_responses(
    self,
    execution_id: str,
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *,
    owner_id: str,
    lease_token: str,
    response_idempotency_key: str,
) -> ManagedInputRecord
```

```python
start_delivery(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
) -> ManagedInputRecord
```

```python
mark_delivered(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
) -> ManagedInputRecord
```

```python
resolve(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
) -> ManagedInputRecord
```

```python
fail(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
    code: str,
    message: str,
) -> ManagedInputRecord
```

```python
fail_recovery(
    self,
    execution_id: str,
    round_id: str,
    *,
    expected_lease_token: str,
    message: str,
) -> ManagedInputRecord
```

```python
redacted_responses(
    self,
    execution_id: str,
    round_id: str,
) -> Mapping[str, object] | None
```

## `PersistentExecutionStore`

```python
m3.storage.PersistentExecutionStore(
    database: str | Path,
    *,
    blob_root: str | Path | None = None,
    config: RedactionConfig | None = None,
    capture_config: CaptureOptions | None = None,
    payload_blob_threshold: int = 65536,
    execution_queue: str | None = None,
    **kwargs: Any,
) -> None
```

SQLite implementation of the public :class:`ExecutionStore` protocol.

- `managed_input_store` (property): Lazily open the managed-input tables for opted-in executions.

```python
resolve_managed_input_store(
    self,
) -> SQLiteManagedInputStore
```
Resolve managed-input storage lazily for opted-in executions.

```python
ensure_project(
    self,
    project_id: str,
    project_name: str,
) -> tuple[str, str]
```
Register a stable project identity and refresh its display name.

```python
get_project(
    self,
    project_id: str,
) -> tuple[str, str] | None
```

```python
ensure_suite(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite
```

```python
get_suite(
    self,
    suite_id: SuiteId | str,
) -> Suite | None
```

```python
get_suite_by_name(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite | None
```

```python
list_suites(
    self,
) -> tuple[Suite, ...]
```

```python
create(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    server_bindings: Sequence[Mapping[str, Any]] = (),
    harness_binding: Mapping[str, Any] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
create_execution(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    server_bindings: Sequence[Mapping[str, Any]] = (),
    harness_binding: Mapping[str, Any] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
save_test_run(
    self,
    run_id: str,
    value: Mapping[str, object],
) -> None
```

```python
get_test_run(
    self,
    run_id: str,
) -> Mapping[str, object] | None
```

```python
list_test_runs(
    self,
) -> tuple[Mapping[str, object], ...]
```

```python
list_test_run_page(
    self,
    *,
    limit: int | None = None,
    offset: int = 0,
    suite_id: int | None = None,
    project_id: str | None = None,
    q: str | None = None,
) -> tuple[tuple[Mapping[str, object], ...], int]
```
Return newest-first run manifests with their suites, and the total.

```python
save_test_result(
    self,
    run_id: str,
    attempt_id: str,
    value: Mapping[str, object],
) -> None
```

```python
list_test_results(
    self,
    run_id: str,
) -> tuple[Mapping[str, object], ...]
```

```python
get_snapshot(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionState | None
```

```python
get_execution_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```
Return the immutable typed submission spec, if one was saved.

```python
get_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```
Return the immutable typed submission spec, if one was saved.

```python
list_executions(
    self,
    *,
    limit: int = 50,
    offset: int = 0,
    lifecycle: ExecutionStatus | str | None = None,
    outcome: ExecutionOutcome | str | None = None,
    run_id: str | None = None,
    suite_id: int | None = None,
    project_id: str | None = None,
) -> ExecutionPage
```

```python
get_report(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
    event_limit: int | None = None,
    artifact_limit: int | None = None,
) -> ExecutionReport | None
```

```python
get_trace(
    self,
    execution_id: ExecutionId | str,
) -> TraceResult | None
```

```python
get_trace_view(
    self,
    execution_id: ExecutionId | str,
) -> TraceView | None
```

```python
tool_call_counts(
    self,
    execution_ids: Sequence[ExecutionId | str],
) -> dict[str, tuple[int, int]]
```
Return ``{execution_id: (total, successful)}`` tool-call counts.

```python
save_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
update_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
append_events(
    self,
    events: Sequence[Event],
) -> None
```

```python
append(
    self,
    events: Sequence[Event],
) -> None
```

```python
append_event(
    self,
    event: Event,
    content: bytes,
    *,
    media_type: str,
) -> Event
```
Commit an event and its raw blob in one SQLite transaction.

```python
iter_events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> Iterator[Event]
```

```python
events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> tuple[Event, ...]
```

```python
create_session(
    self,
    execution_id: ExecutionId | str,
    session_id: SessionId | str,
    *,
    state: str = 'open',
) -> SessionId
```

```python
close_session(
    self,
    session_id: SessionId | str,
) -> None
```

```python
save_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | Mapping[str, Any] | None = None,
) -> None
```

```python
append_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | Mapping[str, Any] | None = None,
) -> None
```

```python
turns(
    self,
    execution_id: ExecutionId | str,
) -> tuple[tuple[TurnState, TurnResult | None], ...]
```

```python
save_evaluation(
    self,
    execution_id: ExecutionId | str,
    result: Mapping[str, Any] | Any,
    *,
    evaluation_id: str | None = None,
    turn_id: TurnId | str | None = None,
) -> str
```

```python
reserve_judge_request(
    self,
    run_id: str,
    limit: int,
) -> bool
```
Atomically reserve one judge request for a run across workers.

```python
save(
    self,
    result: EvaluationResult,
) -> None
```

```python
get(
    self,
    evaluation_id: str,
) -> EvaluationResult | None
```

```python
all(
    self,
) -> tuple[EvaluationResult, ...]
```

```python
evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
aggregate_evaluations(
    self,
    query: EvaluationQuery,
) -> EvaluationReport
```
Calculate summaries from persisted evaluations and execution traces.

```python
persisted_evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
evaluation_json(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[Mapping[str, Any], ...]
```

```python
transaction(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionTransaction
```

```python
allocate(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```

```python
allocate_sequence(
    self,
    execution_id,
)
```

```python
allocate_sequences(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```

```python
release(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```

```python
release_sequences(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```

```python
subscribe(
    self,
    execution_id: ExecutionId | str,
    callback: EventCallback,
) -> Callable[[], None]
```

```python
save_acp_probe(
    self,
    result: ACPProbeResult,
) -> ACPProbeResult
```
Persist one redacted ACP probe result and return its safe copy.

```python
get_acp_probe(
    self,
    probe_id: str,
) -> ACPProbeResult | None
```

```python
list_acp_probes(
    self,
    dimension: ACPProbeDimension | None = None,
    *,
    include_inflight: bool = True,
) -> tuple[ACPProbeResult, ...]
```

```python
latest_acp_probe(
    self,
    dimension: ACPProbeDimension,
) -> ACPProbeResult | None
```

```python
create_profile(
    self,
    kind: str,
    name: str,
    value: Mapping[str, Any],
    *,
    description: str = '',
    profile_id: str | None = None,
    revision_id: str | None = None,
) -> ProfileRecord
```

```python
create_server_profile(
    self,
    name: str,
    value: Mapping[str, Any],
    **kwargs: Any,
) -> ProfileRecord
```

```python
create_harness_profile(
    self,
    name: str,
    value: Mapping[str, Any],
    **kwargs: Any,
) -> ProfileRecord
```

```python
get_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord | None
```

```python
resolve_profile(
    self,
    profile_id: str,
    selection: RevisionSelection,
    *,
    kind: Literal['server', 'harness'],
) -> tuple[ProfileRecord | None, ProfileRevisionRecord | None]
```
Read one profile and its selected immutable revision.

```python
list_profiles(
    self,
    kind: str,
    *,
    include_archived: bool = False,
) -> tuple[ProfileRecord, ...]
```
List one profile family in stable name/id order.

```python
list_profile_revisions(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> tuple[ProfileRevisionRecord, ...]
```
Return every revision ordered by revision number then id.

```python
update_profile(
    self,
    profile_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```
Update mutable metadata without changing the immutable revision.

```python
add_revision(
    self,
    profile_id: str,
    value: Mapping[str, Any],
    *,
    revision_id: str | None = None,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord
```

```python
archive_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```

```python
restore_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```

```python
resolve_revision(
    self,
    profile_id: str,
    selection: RevisionSelection | str = 'latest',
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord
```

```python
get_revision(
    self,
    revision_id: RevisionId | str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord | None
```

```python
enqueue_command(
    self,
    execution_id: ExecutionId | str,
    kind: str = 'execution',
    payload: Mapping[str, Any] | None = None,
    *,
    command_id: str | None = None,
    session_id: SessionId | str | None = None,
    turn_id: TurnId | str | None = None,
) -> Command
```

```python
enqueue(
    self,
    execution_id: ExecutionId | str,
    kind: str = 'execution',
    payload: Mapping[str, Any] | None = None,
    *,
    command_id: str | None = None,
    session_id: SessionId | str | None = None,
    turn_id: TurnId | str | None = None,
) -> Command
```

```python
get_command(
    self,
    command_id: str,
) -> Command | None
```

```python
claim_next(
    self,
    owner_id: str,
    *,
    lease_seconds: float = 30.0,
) -> tuple[Command, Lease] | None
```

```python
claim(
    self,
    owner_id: str,
    *,
    lease_seconds: float = 30.0,
) -> tuple[Command, Lease] | None
```

```python
heartbeat(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
    lease_seconds: float = 30.0,
) -> bool
```

```python
renew_lease(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
    lease_seconds: float = 30.0,
) -> bool
```

```python
mark_interrupted_if_lease_lost(
    self,
    lease: Lease,
    *,
    reason: str = 'worker lease lost',
) -> bool
```
Atomically close work whose owner can no longer renew its lease.

```python
mark_managed_recovery_unavailable_if_lease_lost(
    self,
    lease: Lease,
    *,
    reason: str = 'managed interaction cannot be resumed safely after worker loss',
) -> bool
```
Terminalize managed input when its owning worker is lost.

```python
release_lease(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
) -> bool
```

```python
complete_command(
    self,
    command_id: str,
    *,
    owner_id: str,
    lease_token: str,
    status: str = 'done',
) -> bool
```
Mark one claimed command terminal under its current lease.

```python
request_cancel(
    self,
    execution_id: ExecutionId | str,
    reason: str | None = None,
) -> bool
```

```python
cancel(
    self,
    execution_id: ExecutionId | str,
    reason: str | None = None,
) -> bool
```

```python
finalize_cancelled(
    self,
    execution_id: ExecutionId | str,
    *,
    reason: str = 'cancelled',
) -> bool
```
Persist a cancellation terminal event when a worker observed it.

```python
cancellation_requested(
    self,
    execution_id: ExecutionId | str,
) -> bool
```

```python
mark_stale_interrupted(
    self,
    *,
    now: datetime | None = None,
) -> tuple[ExecutionId, ...]
```

```python
delete_execution(
    self,
    execution_id: ExecutionId | str,
) -> None
```

```python
delete(
    self,
    execution_id: ExecutionId | str,
) -> None
```

```python
put_raw_evidence(
    self,
    event_id: EventId | str,
    content: bytes,
    *,
    media_type: str,
) -> EvidenceCapture
```
Redact, bound, and durably associate evidence with one event.

```python
read_raw_evidence(
    self,
    reference: EvidenceRef,
    *,
    max_bytes: int = 1048576,
) -> RawEvidence
```

```python
clone_execution(
    self,
    execution_id: ExecutionId | str,
    *,
    use_latest: bool = False,
) -> ExecutionId
```

```python
clone(
    self,
    execution_id: ExecutionId | str,
    *,
    use_latest: bool = False,
) -> ExecutionId
```

```python
resolved_bindings(
    self,
    execution_id: ExecutionId | str,
) -> Mapping[str, Any]
```
Return the immutable, submission-time profile binding snapshot.

```python
close(
    self,
) -> None
```

## `ProfileRecord`

```python
m3.storage.ProfileRecord(
    id: str,
    kind: str,
    name: str,
    description: str,
    archived: bool,
    current_revision_id: RevisionId | None,
    created_at: datetime,
    updated_at: datetime,
) -> None
```

## `ProfileResolver`

```python
m3.storage.ProfileResolver(
    *args,
    **kwargs,
)
```

Read-only saved-profile lookup used by execution runtimes.


```python
resolve_profile(
    self,
    profile_id: str,
    selection: RevisionSelection,
    *,
    kind: Literal['server', 'harness'],
) -> tuple[Any, Any]
```

## `ProfileRevisionRecord`

```python
m3.storage.ProfileRevisionRecord(
    id: RevisionId,
    profile_id: str,
    revision_number: int,
    value: Mapping[str, Any],
    created_at: datetime,
) -> None
```

## `SQLiteArtifactStore`

```python
m3.storage.SQLiteArtifactStore(
    database: str | Path,
    blob_root: str | Path | None = None,
    **kwargs: Any,
) -> None
```

Filesystem-backed content-addressed artifact store with SQLite refs.


```python
put(
    self,
    execution_id: ExecutionId | str,
    name: str,
    content: bytes,
    *,
    media_type: str | None = None,
) -> ArtifactRef
```

```python
get(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> bytes
```

```python
get_ref(
    self,
    artifact_id: ArtifactId | str,
) -> ArtifactRef
```

```python
iter_refs(
    self,
    execution_id: ExecutionId | str | None = None,
) -> Iterator[ArtifactRef]
```

```python
delete(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> None
```

```python
cleanup(
    self,
) -> None
```
- `blob_store` (property): The app-owned content-addressed store used by metadata rows.

## `SQLiteExecutionStore`

```python
m3.storage.SQLiteExecutionStore(
    database: str | Path,
    *,
    blob_root: str | Path | None = None,
    config: RedactionConfig | None = None,
    capture_config: CaptureOptions | None = None,
    payload_blob_threshold: int = 65536,
    execution_queue: str | None = None,
    **kwargs: Any,
) -> None
```

SQLite implementation of the public :class:`ExecutionStore` protocol.

- `managed_input_store` (property): Lazily open the managed-input tables for opted-in executions.

```python
resolve_managed_input_store(
    self,
) -> SQLiteManagedInputStore
```
Resolve managed-input storage lazily for opted-in executions.

```python
ensure_project(
    self,
    project_id: str,
    project_name: str,
) -> tuple[str, str]
```
Register a stable project identity and refresh its display name.

```python
get_project(
    self,
    project_id: str,
) -> tuple[str, str] | None
```

```python
ensure_suite(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite
```

```python
get_suite(
    self,
    suite_id: SuiteId | str,
) -> Suite | None
```

```python
get_suite_by_name(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite | None
```

```python
list_suites(
    self,
) -> tuple[Suite, ...]
```

```python
create(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    server_bindings: Sequence[Mapping[str, Any]] = (),
    harness_binding: Mapping[str, Any] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
create_execution(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    server_bindings: Sequence[Mapping[str, Any]] = (),
    harness_binding: Mapping[str, Any] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
save_test_run(
    self,
    run_id: str,
    value: Mapping[str, object],
) -> None
```

```python
get_test_run(
    self,
    run_id: str,
) -> Mapping[str, object] | None
```

```python
list_test_runs(
    self,
) -> tuple[Mapping[str, object], ...]
```

```python
list_test_run_page(
    self,
    *,
    limit: int | None = None,
    offset: int = 0,
    suite_id: int | None = None,
    project_id: str | None = None,
    q: str | None = None,
) -> tuple[tuple[Mapping[str, object], ...], int]
```
Return newest-first run manifests with their suites, and the total.

```python
save_test_result(
    self,
    run_id: str,
    attempt_id: str,
    value: Mapping[str, object],
) -> None
```

```python
list_test_results(
    self,
    run_id: str,
) -> tuple[Mapping[str, object], ...]
```

```python
get_snapshot(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionState | None
```

```python
get_execution_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```
Return the immutable typed submission spec, if one was saved.

```python
get_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```
Return the immutable typed submission spec, if one was saved.

```python
list_executions(
    self,
    *,
    limit: int = 50,
    offset: int = 0,
    lifecycle: ExecutionStatus | str | None = None,
    outcome: ExecutionOutcome | str | None = None,
    run_id: str | None = None,
    suite_id: int | None = None,
    project_id: str | None = None,
) -> ExecutionPage
```

```python
get_report(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
    event_limit: int | None = None,
    artifact_limit: int | None = None,
) -> ExecutionReport | None
```

```python
get_trace(
    self,
    execution_id: ExecutionId | str,
) -> TraceResult | None
```

```python
get_trace_view(
    self,
    execution_id: ExecutionId | str,
) -> TraceView | None
```

```python
tool_call_counts(
    self,
    execution_ids: Sequence[ExecutionId | str],
) -> dict[str, tuple[int, int]]
```
Return ``{execution_id: (total, successful)}`` tool-call counts.

```python
save_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
update_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
append_events(
    self,
    events: Sequence[Event],
) -> None
```

```python
append(
    self,
    events: Sequence[Event],
) -> None
```

```python
append_event(
    self,
    event: Event,
    content: bytes,
    *,
    media_type: str,
) -> Event
```
Commit an event and its raw blob in one SQLite transaction.

```python
iter_events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> Iterator[Event]
```

```python
events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> tuple[Event, ...]
```

```python
create_session(
    self,
    execution_id: ExecutionId | str,
    session_id: SessionId | str,
    *,
    state: str = 'open',
) -> SessionId
```

```python
close_session(
    self,
    session_id: SessionId | str,
) -> None
```

```python
save_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | Mapping[str, Any] | None = None,
) -> None
```

```python
append_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | Mapping[str, Any] | None = None,
) -> None
```

```python
turns(
    self,
    execution_id: ExecutionId | str,
) -> tuple[tuple[TurnState, TurnResult | None], ...]
```

```python
save_evaluation(
    self,
    execution_id: ExecutionId | str,
    result: Mapping[str, Any] | Any,
    *,
    evaluation_id: str | None = None,
    turn_id: TurnId | str | None = None,
) -> str
```

```python
reserve_judge_request(
    self,
    run_id: str,
    limit: int,
) -> bool
```
Atomically reserve one judge request for a run across workers.

```python
save(
    self,
    result: EvaluationResult,
) -> None
```

```python
get(
    self,
    evaluation_id: str,
) -> EvaluationResult | None
```

```python
all(
    self,
) -> tuple[EvaluationResult, ...]
```

```python
evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
aggregate_evaluations(
    self,
    query: EvaluationQuery,
) -> EvaluationReport
```
Calculate summaries from persisted evaluations and execution traces.

```python
persisted_evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
evaluation_json(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[Mapping[str, Any], ...]
```

```python
transaction(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionTransaction
```

```python
allocate(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```

```python
allocate_sequence(
    self,
    execution_id,
)
```

```python
allocate_sequences(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```

```python
release(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```

```python
release_sequences(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```

```python
subscribe(
    self,
    execution_id: ExecutionId | str,
    callback: EventCallback,
) -> Callable[[], None]
```

```python
save_acp_probe(
    self,
    result: ACPProbeResult,
) -> ACPProbeResult
```
Persist one redacted ACP probe result and return its safe copy.

```python
get_acp_probe(
    self,
    probe_id: str,
) -> ACPProbeResult | None
```

```python
list_acp_probes(
    self,
    dimension: ACPProbeDimension | None = None,
    *,
    include_inflight: bool = True,
) -> tuple[ACPProbeResult, ...]
```

```python
latest_acp_probe(
    self,
    dimension: ACPProbeDimension,
) -> ACPProbeResult | None
```

```python
create_profile(
    self,
    kind: str,
    name: str,
    value: Mapping[str, Any],
    *,
    description: str = '',
    profile_id: str | None = None,
    revision_id: str | None = None,
) -> ProfileRecord
```

```python
create_server_profile(
    self,
    name: str,
    value: Mapping[str, Any],
    **kwargs: Any,
) -> ProfileRecord
```

```python
create_harness_profile(
    self,
    name: str,
    value: Mapping[str, Any],
    **kwargs: Any,
) -> ProfileRecord
```

```python
get_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord | None
```

```python
resolve_profile(
    self,
    profile_id: str,
    selection: RevisionSelection,
    *,
    kind: Literal['server', 'harness'],
) -> tuple[ProfileRecord | None, ProfileRevisionRecord | None]
```
Read one profile and its selected immutable revision.

```python
list_profiles(
    self,
    kind: str,
    *,
    include_archived: bool = False,
) -> tuple[ProfileRecord, ...]
```
List one profile family in stable name/id order.

```python
list_profile_revisions(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> tuple[ProfileRevisionRecord, ...]
```
Return every revision ordered by revision number then id.

```python
update_profile(
    self,
    profile_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```
Update mutable metadata without changing the immutable revision.

```python
add_revision(
    self,
    profile_id: str,
    value: Mapping[str, Any],
    *,
    revision_id: str | None = None,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord
```

```python
archive_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```

```python
restore_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```

```python
resolve_revision(
    self,
    profile_id: str,
    selection: RevisionSelection | str = 'latest',
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord
```

```python
get_revision(
    self,
    revision_id: RevisionId | str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord | None
```

```python
enqueue_command(
    self,
    execution_id: ExecutionId | str,
    kind: str = 'execution',
    payload: Mapping[str, Any] | None = None,
    *,
    command_id: str | None = None,
    session_id: SessionId | str | None = None,
    turn_id: TurnId | str | None = None,
) -> Command
```

```python
enqueue(
    self,
    execution_id: ExecutionId | str,
    kind: str = 'execution',
    payload: Mapping[str, Any] | None = None,
    *,
    command_id: str | None = None,
    session_id: SessionId | str | None = None,
    turn_id: TurnId | str | None = None,
) -> Command
```

```python
get_command(
    self,
    command_id: str,
) -> Command | None
```

```python
claim_next(
    self,
    owner_id: str,
    *,
    lease_seconds: float = 30.0,
) -> tuple[Command, Lease] | None
```

```python
claim(
    self,
    owner_id: str,
    *,
    lease_seconds: float = 30.0,
) -> tuple[Command, Lease] | None
```

```python
heartbeat(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
    lease_seconds: float = 30.0,
) -> bool
```

```python
renew_lease(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
    lease_seconds: float = 30.0,
) -> bool
```

```python
mark_interrupted_if_lease_lost(
    self,
    lease: Lease,
    *,
    reason: str = 'worker lease lost',
) -> bool
```
Atomically close work whose owner can no longer renew its lease.

```python
mark_managed_recovery_unavailable_if_lease_lost(
    self,
    lease: Lease,
    *,
    reason: str = 'managed interaction cannot be resumed safely after worker loss',
) -> bool
```
Terminalize managed input when its owning worker is lost.

```python
release_lease(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
) -> bool
```

```python
complete_command(
    self,
    command_id: str,
    *,
    owner_id: str,
    lease_token: str,
    status: str = 'done',
) -> bool
```
Mark one claimed command terminal under its current lease.

```python
request_cancel(
    self,
    execution_id: ExecutionId | str,
    reason: str | None = None,
) -> bool
```

```python
cancel(
    self,
    execution_id: ExecutionId | str,
    reason: str | None = None,
) -> bool
```

```python
finalize_cancelled(
    self,
    execution_id: ExecutionId | str,
    *,
    reason: str = 'cancelled',
) -> bool
```
Persist a cancellation terminal event when a worker observed it.

```python
cancellation_requested(
    self,
    execution_id: ExecutionId | str,
) -> bool
```

```python
mark_stale_interrupted(
    self,
    *,
    now: datetime | None = None,
) -> tuple[ExecutionId, ...]
```

```python
delete_execution(
    self,
    execution_id: ExecutionId | str,
) -> None
```

```python
delete(
    self,
    execution_id: ExecutionId | str,
) -> None
```

```python
put_raw_evidence(
    self,
    event_id: EventId | str,
    content: bytes,
    *,
    media_type: str,
) -> EvidenceCapture
```
Redact, bound, and durably associate evidence with one event.

```python
read_raw_evidence(
    self,
    reference: EvidenceRef,
    *,
    max_bytes: int = 1048576,
) -> RawEvidence
```

```python
clone_execution(
    self,
    execution_id: ExecutionId | str,
    *,
    use_latest: bool = False,
) -> ExecutionId
```

```python
clone(
    self,
    execution_id: ExecutionId | str,
    *,
    use_latest: bool = False,
) -> ExecutionId
```

```python
resolved_bindings(
    self,
    execution_id: ExecutionId | str,
) -> Mapping[str, Any]
```
Return the immutable, submission-time profile binding snapshot.

```python
close(
    self,
) -> None
```

## `SQLiteManagedInputStore`

```python
m3.storage.SQLiteManagedInputStore(
    database: str | Path,
    *,
    busy_timeout_ms: int = 5000,
    clock: Callable[[], datetime] = m3.storage.managed_input._now,
) -> None
```

SQLite-backed managed-input storage sharing a database path safely.


```python
get_round(
    self,
    execution_id: str,
    round_id: str,
) -> ManagedInputRecord | None
```

```python
list_rounds(
    self,
    execution_id: str,
) -> tuple[ManagedInputRecord, ...]
```

```python
create_round(
    self,
    pending: PendingElicitationRound,
    *,
    round_index: int,
    round_limit: int,
    owner_id: str,
    lease_seconds: float,
    lease_token: str | None = None,
    harness_session_id: str | None = None,
    native_resume_token: str | None = None,
    delivery_idempotency_key: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    operation_parameters: Mapping[str, object] | None = None,
) -> ManagedInputRecord
```

```python
claim_round(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_seconds: float,
    expected_lease_token: str | None = None,
    delivery_state: Literal['not_started', 'not_delivered', 'delivered'] | None = None,
    idempotent_delivery: bool = False,
) -> ManagedInputRecord
```

```python
renew(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
    lease_seconds: float,
) -> ManagedInputRecord
```
Renew an active local wait without changing its compare-and-set token.

```python
submit_responses(
    self,
    execution_id: str,
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *,
    owner_id: str,
    lease_token: str,
    response_idempotency_key: str,
) -> ManagedInputRecord
```

```python
start_delivery(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
) -> ManagedInputRecord
```

```python
mark_delivered(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
) -> ManagedInputRecord
```

```python
resolve(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
) -> ManagedInputRecord
```

```python
fail(
    self,
    execution_id: str,
    round_id: str,
    *,
    owner_id: str,
    lease_token: str,
    code: str,
    message: str,
) -> ManagedInputRecord
```

```python
fail_recovery(
    self,
    execution_id: str,
    round_id: str,
    *,
    expected_lease_token: str,
    message: str,
) -> ManagedInputRecord
```

```python
redacted_responses(
    self,
    execution_id: str,
    round_id: str,
) -> Mapping[str, object] | None
```

## `SQLiteStore`

```python
m3.storage.SQLiteStore(
    database: str | Path,
    *,
    blob_root: str | Path | None = None,
    config: RedactionConfig | None = None,
    capture_config: CaptureOptions | None = None,
    payload_blob_threshold: int = 65536,
    execution_queue: str | None = None,
    **kwargs: Any,
) -> None
```

SQLite implementation of the public :class:`ExecutionStore` protocol.

- `managed_input_store` (property): Lazily open the managed-input tables for opted-in executions.

```python
resolve_managed_input_store(
    self,
) -> SQLiteManagedInputStore
```
Resolve managed-input storage lazily for opted-in executions.

```python
ensure_project(
    self,
    project_id: str,
    project_name: str,
) -> tuple[str, str]
```
Register a stable project identity and refresh its display name.

```python
get_project(
    self,
    project_id: str,
) -> tuple[str, str] | None
```

```python
ensure_suite(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite
```

```python
get_suite(
    self,
    suite_id: SuiteId | str,
) -> Suite | None
```

```python
get_suite_by_name(
    self,
    suite_name: str,
    project_id: str | None = None,
) -> Suite | None
```

```python
list_suites(
    self,
) -> tuple[Suite, ...]
```

```python
create(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    server_bindings: Sequence[Mapping[str, Any]] = (),
    harness_binding: Mapping[str, Any] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
create_execution(
    self,
    snapshot: ExecutionState,
    *,
    specification: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    server_bindings: Sequence[Mapping[str, Any]] = (),
    harness_binding: Mapping[str, Any] | None = None,
    parent_execution_id: ExecutionId | str | None = None,
    run_id: RunId | str | None = None,
) -> None
```

```python
save_test_run(
    self,
    run_id: str,
    value: Mapping[str, object],
) -> None
```

```python
get_test_run(
    self,
    run_id: str,
) -> Mapping[str, object] | None
```

```python
list_test_runs(
    self,
) -> tuple[Mapping[str, object], ...]
```

```python
list_test_run_page(
    self,
    *,
    limit: int | None = None,
    offset: int = 0,
    suite_id: int | None = None,
    project_id: str | None = None,
    q: str | None = None,
) -> tuple[tuple[Mapping[str, object], ...], int]
```
Return newest-first run manifests with their suites, and the total.

```python
save_test_result(
    self,
    run_id: str,
    attempt_id: str,
    value: Mapping[str, object],
) -> None
```

```python
list_test_results(
    self,
    run_id: str,
) -> tuple[Mapping[str, object], ...]
```

```python
get_snapshot(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionState | None
```

```python
get_execution_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```
Return the immutable typed submission spec, if one was saved.

```python
get_spec(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionSpec | None
```
Return the immutable typed submission spec, if one was saved.

```python
list_executions(
    self,
    *,
    limit: int = 50,
    offset: int = 0,
    lifecycle: ExecutionStatus | str | None = None,
    outcome: ExecutionOutcome | str | None = None,
    run_id: str | None = None,
    suite_id: int | None = None,
    project_id: str | None = None,
) -> ExecutionPage
```

```python
get_report(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
    event_limit: int | None = None,
    artifact_limit: int | None = None,
) -> ExecutionReport | None
```

```python
get_trace(
    self,
    execution_id: ExecutionId | str,
) -> TraceResult | None
```

```python
get_trace_view(
    self,
    execution_id: ExecutionId | str,
) -> TraceView | None
```

```python
tool_call_counts(
    self,
    execution_ids: Sequence[ExecutionId | str],
) -> dict[str, tuple[int, int]]
```
Return ``{execution_id: (total, successful)}`` tool-call counts.

```python
save_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
update_snapshot(
    self,
    snapshot: ExecutionState,
) -> None
```

```python
append_events(
    self,
    events: Sequence[Event],
) -> None
```

```python
append(
    self,
    events: Sequence[Event],
) -> None
```

```python
append_event(
    self,
    event: Event,
    content: bytes,
    *,
    media_type: str,
) -> Event
```
Commit an event and its raw blob in one SQLite transaction.

```python
iter_events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> Iterator[Event]
```

```python
events(
    self,
    execution_id: ExecutionId | str,
    *,
    after_sequence: int = -1,
) -> tuple[Event, ...]
```

```python
create_session(
    self,
    execution_id: ExecutionId | str,
    session_id: SessionId | str,
    *,
    state: str = 'open',
) -> SessionId
```

```python
close_session(
    self,
    session_id: SessionId | str,
) -> None
```

```python
save_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | Mapping[str, Any] | None = None,
) -> None
```

```python
append_turn(
    self,
    snapshot: TurnState,
    result: TurnResult | Mapping[str, Any] | None = None,
) -> None
```

```python
turns(
    self,
    execution_id: ExecutionId | str,
) -> tuple[tuple[TurnState, TurnResult | None], ...]
```

```python
save_evaluation(
    self,
    execution_id: ExecutionId | str,
    result: Mapping[str, Any] | Any,
    *,
    evaluation_id: str | None = None,
    turn_id: TurnId | str | None = None,
) -> str
```

```python
reserve_judge_request(
    self,
    run_id: str,
    limit: int,
) -> bool
```
Atomically reserve one judge request for a run across workers.

```python
save(
    self,
    result: EvaluationResult,
) -> None
```

```python
get(
    self,
    evaluation_id: str,
) -> EvaluationResult | None
```

```python
all(
    self,
) -> tuple[EvaluationResult, ...]
```

```python
evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
aggregate_evaluations(
    self,
    query: EvaluationQuery,
) -> EvaluationReport
```
Calculate summaries from persisted evaluations and execution traces.

```python
persisted_evaluations(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[EvaluationRecord, ...]
```

```python
evaluation_json(
    self,
    execution_id: ExecutionId | str,
    *,
    turn_id: TurnId | str | None = None,
) -> tuple[Mapping[str, Any], ...]
```

```python
transaction(
    self,
    execution_id: ExecutionId | str,
) -> ExecutionTransaction
```

```python
allocate(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```

```python
allocate_sequence(
    self,
    execution_id,
)
```

```python
allocate_sequences(
    self,
    execution_id: ExecutionId | str,
    *,
    count: int = 1,
) -> tuple[int, ...]
```

```python
release(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```

```python
release_sequences(
    self,
    execution_id: ExecutionId | str,
    sequences: Sequence[int],
) -> None
```

```python
subscribe(
    self,
    execution_id: ExecutionId | str,
    callback: EventCallback,
) -> Callable[[], None]
```

```python
save_acp_probe(
    self,
    result: ACPProbeResult,
) -> ACPProbeResult
```
Persist one redacted ACP probe result and return its safe copy.

```python
get_acp_probe(
    self,
    probe_id: str,
) -> ACPProbeResult | None
```

```python
list_acp_probes(
    self,
    dimension: ACPProbeDimension | None = None,
    *,
    include_inflight: bool = True,
) -> tuple[ACPProbeResult, ...]
```

```python
latest_acp_probe(
    self,
    dimension: ACPProbeDimension,
) -> ACPProbeResult | None
```

```python
create_profile(
    self,
    kind: str,
    name: str,
    value: Mapping[str, Any],
    *,
    description: str = '',
    profile_id: str | None = None,
    revision_id: str | None = None,
) -> ProfileRecord
```

```python
create_server_profile(
    self,
    name: str,
    value: Mapping[str, Any],
    **kwargs: Any,
) -> ProfileRecord
```

```python
create_harness_profile(
    self,
    name: str,
    value: Mapping[str, Any],
    **kwargs: Any,
) -> ProfileRecord
```

```python
get_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord | None
```

```python
resolve_profile(
    self,
    profile_id: str,
    selection: RevisionSelection,
    *,
    kind: Literal['server', 'harness'],
) -> tuple[ProfileRecord | None, ProfileRevisionRecord | None]
```
Read one profile and its selected immutable revision.

```python
list_profiles(
    self,
    kind: str,
    *,
    include_archived: bool = False,
) -> tuple[ProfileRecord, ...]
```
List one profile family in stable name/id order.

```python
list_profile_revisions(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> tuple[ProfileRevisionRecord, ...]
```
Return every revision ordered by revision number then id.

```python
update_profile(
    self,
    profile_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```
Update mutable metadata without changing the immutable revision.

```python
add_revision(
    self,
    profile_id: str,
    value: Mapping[str, Any],
    *,
    revision_id: str | None = None,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord
```

```python
archive_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```

```python
restore_profile(
    self,
    profile_id: str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRecord
```

```python
resolve_revision(
    self,
    profile_id: str,
    selection: RevisionSelection | str = 'latest',
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord
```

```python
get_revision(
    self,
    revision_id: RevisionId | str,
    *,
    kind: Literal['server', 'harness'] | None = None,
) -> ProfileRevisionRecord | None
```

```python
enqueue_command(
    self,
    execution_id: ExecutionId | str,
    kind: str = 'execution',
    payload: Mapping[str, Any] | None = None,
    *,
    command_id: str | None = None,
    session_id: SessionId | str | None = None,
    turn_id: TurnId | str | None = None,
) -> Command
```

```python
enqueue(
    self,
    execution_id: ExecutionId | str,
    kind: str = 'execution',
    payload: Mapping[str, Any] | None = None,
    *,
    command_id: str | None = None,
    session_id: SessionId | str | None = None,
    turn_id: TurnId | str | None = None,
) -> Command
```

```python
get_command(
    self,
    command_id: str,
) -> Command | None
```

```python
claim_next(
    self,
    owner_id: str,
    *,
    lease_seconds: float = 30.0,
) -> tuple[Command, Lease] | None
```

```python
claim(
    self,
    owner_id: str,
    *,
    lease_seconds: float = 30.0,
) -> tuple[Command, Lease] | None
```

```python
heartbeat(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
    lease_seconds: float = 30.0,
) -> bool
```

```python
renew_lease(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
    lease_seconds: float = 30.0,
) -> bool
```

```python
mark_interrupted_if_lease_lost(
    self,
    lease: Lease,
    *,
    reason: str = 'worker lease lost',
) -> bool
```
Atomically close work whose owner can no longer renew its lease.

```python
mark_managed_recovery_unavailable_if_lease_lost(
    self,
    lease: Lease,
    *,
    reason: str = 'managed interaction cannot be resumed safely after worker loss',
) -> bool
```
Terminalize managed input when its owning worker is lost.

```python
release_lease(
    self,
    lease: Lease | str,
    *,
    owner_id: str | None = None,
) -> bool
```

```python
complete_command(
    self,
    command_id: str,
    *,
    owner_id: str,
    lease_token: str,
    status: str = 'done',
) -> bool
```
Mark one claimed command terminal under its current lease.

```python
request_cancel(
    self,
    execution_id: ExecutionId | str,
    reason: str | None = None,
) -> bool
```

```python
cancel(
    self,
    execution_id: ExecutionId | str,
    reason: str | None = None,
) -> bool
```

```python
finalize_cancelled(
    self,
    execution_id: ExecutionId | str,
    *,
    reason: str = 'cancelled',
) -> bool
```
Persist a cancellation terminal event when a worker observed it.

```python
cancellation_requested(
    self,
    execution_id: ExecutionId | str,
) -> bool
```

```python
mark_stale_interrupted(
    self,
    *,
    now: datetime | None = None,
) -> tuple[ExecutionId, ...]
```

```python
delete_execution(
    self,
    execution_id: ExecutionId | str,
) -> None
```

```python
delete(
    self,
    execution_id: ExecutionId | str,
) -> None
```

```python
put_raw_evidence(
    self,
    event_id: EventId | str,
    content: bytes,
    *,
    media_type: str,
) -> EvidenceCapture
```
Redact, bound, and durably associate evidence with one event.

```python
read_raw_evidence(
    self,
    reference: EvidenceRef,
    *,
    max_bytes: int = 1048576,
) -> RawEvidence
```

```python
clone_execution(
    self,
    execution_id: ExecutionId | str,
    *,
    use_latest: bool = False,
) -> ExecutionId
```

```python
clone(
    self,
    execution_id: ExecutionId | str,
    *,
    use_latest: bool = False,
) -> ExecutionId
```

```python
resolved_bindings(
    self,
    execution_id: ExecutionId | str,
) -> Mapping[str, Any]
```
Return the immutable, submission-time profile binding snapshot.

```python
close(
    self,
) -> None
```

## `SQLiteStoreWorker`

```python
m3.storage.SQLiteStoreWorker(
    store: Any,
    runner: Callable[[Any, Any, Any], Any],
    *,
    worker_id: str | None = None,
    lease_seconds: float = 30.0,
    cancel_runner: Callable[[Any], Any] | None = None,
) -> None
```

Embedded worker facade for :class:`SQLiteExecutionStore`.


```python
run_once(
    self,
) -> bool
```

```python
stop(
    self,
) -> None
```

## `SequenceConflict`

```python
m3.storage.SequenceConflict
```

An appended event does not continue the committed sequence.

## `StorageConflict`

```python
m3.storage.StorageConflict
```

The append or snapshot operation conflicts with committed state.

## `StorageError`

```python
m3.storage.StorageError
```

Base class for expected ephemeral storage failures.

## `TerminalConflict`

```python
m3.storage.TerminalConflict
```

The execution is finished and cannot receive more events.

## `TemporaryArtifactStore`

```python
m3.storage.TemporaryArtifactStore(
    root: str | os.PathLike[str] | None = None,
    *,
    config: RedactionConfig | None = None,
) -> None
```

Filesystem-backed temporary artifact store with atomic blob placement.

- `root` (property)

```python
put(
    self,
    execution_id: ExecutionId | str,
    name: str,
    content: bytes,
    *,
    media_type: str | None = None,
) -> ArtifactRef
```

```python
get(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> bytes
```

```python
delete(
    self,
    artifact: ArtifactRef | ArtifactId | str,
) -> None
```

```python
cleanup(
    self,
) -> None
```

## `serialize_durable`

```python
m3.storage.serialize_durable(
    value: Any,
    *,
    config: RedactionConfig | None = None,
    path: str = '$',
) -> Any
```

Return strict JSON-compatible durable data while preserving references.
