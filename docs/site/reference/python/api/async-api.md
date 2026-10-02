---
title: "m3.async_api"
description: "Public Python API reference for m3.async_api."
---

# `m3.async_api`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `AsyncAgentSession`

```python
m3.async_api.AsyncAgentSession(
    spec: AgentSpec,
    adapter: AgentAdapter,
    *,
    server_manager: Any = None,
    server_manager_factory: Callable[[], Any] | None = None,
    interaction_controller: Interactions | None = None,
    provenance: SessionSource | None = None,
    on_close: Callable[[AsyncAgentSession], None] | None = None,
    event_sink: _EventSink | None = None,
    trace_recorder: ExecutionTraceRecorder | None = None,
    trace_owner: bool = True,
    artifact_store: ArtifactStore | None = None,
    harness_cache_dir: str | None = None,
    runtime_manager: Any = None,
    runtime_invocation_dir: str | Path | None = None,
    runtime_project_root: str | Path | None = None,
    managed_input_runtime: ManagedInputRuntime | None = None,
) -> None
```

Lifecycle-safe async session over one injected harness adapter.


```python
send(
    self,
    message: str | UserMessage,
    *,
    timeout: float | None = None,
    metadata: Mapping[str, object] | None = None,
    elicitation: ElicitationPlan | None = None,
    elicitation_round_limit: int = 10,
) -> TurnResult
```

```python
enqueue_turn(
    self,
    message: str | UserMessage,
    *,
    timeout: float | None = None,
    metadata: Mapping[str, object] | None = None,
) -> QueuedTurn
```

```python
cancel(
    self,
) -> None
```

```python
snapshot(
    self,
) -> ExecutionState
```
- `result` (property)
- `provenance` (property): Immutable source linkage for a portable fork/replay session.
- `interactions` (property): Policy-gated permission, filesystem, terminal, and model handlers.

```python
fork(
    self,
    request: SessionForkRequest,
    *,
    adapter_factory: Callable[[AgentSpec, SessionSource], HarnessAdapter | Awaitable[HarnessAdapter]],
) -> AsyncAgentSession
```
Create a fresh child execution from this terminal session.

```python
aclose(
    self,
) -> None
```

## `HarnessAdapter`

```python
m3.async_api.HarnessAdapter(
    *args,
    **kwargs,
)
```

Minimal injected adapter contract for one continuing conversation.


```python
start(
    self,
    spec: AgentSpec,
) -> None
```

```python
send(
    self,
    message: UserMessage,
    *,
    timeout: float | None = None,
    metadata: Mapping[str, object] | None = None,
) -> TurnResponse | AdapterTurn
```

```python
close(
    self,
) -> None
```

## `AsyncProbes`

```python
m3.async_api.AsyncProbes(
    *,
    timeout_seconds: float = 5.0,
    output_limit: int = 65536,
) -> None
```

Async namespace for capability probes.


```python
probe_binary(
    self,
    name: str,
    executable: str | os.PathLike[str],
    *,
    args: Sequence[str] = ('--version',),
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> ProbeResult
```

```python
probe_protocol(
    self,
    name: str,
    executable: str | os.PathLike[str],
    *,
    args: Sequence[str] = ('--protocol-version',),
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    transport: str | TransportKind | None = None,
) -> ProbeResult
```

```python
probe_harness(
    self,
    name: str,
    executable: str | os.PathLike[str],
    *,
    args: Sequence[str] = ('--version',),
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    transport: str | TransportKind | None = None,
) -> ProbeResult
```

```python
probe_transport(
    self,
    name: str,
    *,
    transport: str | TransportKind,
    executable: str | os.PathLike[str] | None = None,
    args: Sequence[str] = ('--transport-ready',),
    module: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> ProbeResult
```

```python
probe_storage(
    self,
    name: str = 'memory',
    *,
    module: str | None = None,
) -> ProbeResult
```

```python
probe_requested(
    self,
    requests: Iterable[ProbeRequest],
) -> ProbeReport
```

## `AsyncDirectClient`

```python
m3.async_api.AsyncDirectClient(
    kit: AsyncMCPTestKit,
    server: _ServerValue,
    *,
    timeout: float,
    validate_schemas: bool,
    secret_resolver: _SecretResolver | None,
    bearer_token: _SecretReference | None,
    auth: httpx2.Auth | None,
    for_agent: bool,
    resolve_host: _HostResolver | None,
    raise_server_exceptions: bool,
    session_options: _Mapping[str, _Any],
    trace_bridge: _DirectTraceBridge | None = None,
    trace_owner: bool = True,
    workspace_root: str | None = None,
    server_bindings: _Iterable[_Mapping[str, _Any]] = (),
    prefer_modern_protocol: bool = False,
) -> None
```

Lifecycle-owned async direct client returned by ``AsyncMCPTestKit``.

- `transport_evidence` (property): Return sanitized partial/terminal transport evidence when available.

```python
aclose(
    self,
) -> None
```
- `final_trace` (property): Immutable finalized stable trace, if available.
- `initialization` (property)
- `timeout` (property)
- `trace` (property): Immutable live stable trace when a bridge is attached.

## `AsyncExecutionHandle`

```python
m3.async_api.AsyncExecutionHandle(
    controller: AsyncExecutionController,
    spec: ExecutionSpec,
    *,
    store: ExecutionStore | None = None,
    persistent: bool = False,
    execution_id: ExecutionId | str | None = None,
    run_id: str | None = None,
    human_input: HumanInput = 'fail',
    resolution_provenance: Sequence[Mapping[str, Any]] = (),
) -> None
```

Live, immutable view of one submitted execution.

- `execution_id` (property)
- `spec` (property)
- `submitted_spec` (property)

```python
snapshot(
    self,
) -> ExecutionState
```

```python
pending_elicitation(
    self,
) -> PendingElicitationRound | None
```
Return the current public managed-input round, if one is pending.

```python
respond_elicitation(
    self,
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *,
    idempotency_key: str,
) -> None
```
Atomically validate and commit keyed human responses.

```python
result(
    self,
    timeout: float | None = None,
) -> ExecutionResult
```

```python
cancel(
    self,
) -> None
```

```python
on_event(
    self,
    callback: Callable[[Event], Any],
) -> Callable[[], None]
```
Subscribe after commit; callback failures cannot affect execution.

```python
events(
    self,
    *,
    after_sequence: int = -1,
) -> AsyncIterator[Event]
```
Yield committed events in sequence order and finish at terminal.

## `AsyncMCPTestKit`

```python
m3.async_api.AsyncMCPTestKit(
    config: Config | _Mapping[str, _Any] | None = None,
    *,
    env: _Mapping[str, str] | None = None,
    cwd: str | _Path | None = None,
    probe_timeout_seconds: float = 5.0,
    probe_output_limit: int = 65536,
    adapter_registry: _HarnessAdapterRegistry | None = None,
    store: _ExecutionStore | None = None,
    embedded_worker: bool = True,
    run_id: _RunId | str | None = None,
    suite_name: str | None = None,
    project_id: _ProjectId | str | None = None,
    record_checks: bool = False,
    max_judge_requests: int | None = None,
    harness_cache_dir: str | _Path | None = None,
) -> None
```

Async twin of :class:`m3.sync_api.MCPTestKit`.

- `probes` (property): Asynchronous capability namespace owned by this kit.
- `store` (property): The optional execution store configured on this kit.
- `run_id` (property)

```python
get_trace(
    self,
    execution_id: _ExecutionId | str,
) -> _TraceResult
```
Return the finalized stable trace for an execution.

```python
get_trace_view(
    self,
    execution_id: _ExecutionId | str,
) -> TraceView
```
Return the finalized typed trace view for an execution.

```python
read_raw_evidence(
    self,
    reference: _EvidenceRef,
    *,
    max_bytes: int = 1048576,
) -> RawEvidence
```
Read bounded, redacted raw evidence by its durable reference.

```python
aclose(
    self,
) -> None
```
Close the shell; repeated calls are intentionally harmless.

```python
capabilities(
    self,
    requests: _Iterable[ProbeRequest] = (),
) -> ProbeReport
```

```python
register_evaluator(
    self,
    name: str,
    evaluator: _EvaluatorCallable | _AsyncEvaluator,
) -> None
```
Register an evaluator callback by its serializable name.

```python
evaluate(
    self,
    subject: _Any,
    evaluator: str | _EvaluatorCallable | _AsyncEvaluator,
    *,
    required: bool = False,
    goal: str | None = None,
    trace: _Any = None,
    artifacts: _Any = (),
    metadata: _Mapping[str, str | int | float | bool | None] | None = None,
    execution_id: _Any = None,
    turn_id: _Any = None,
    case_id: str | None = None,
) -> _EvaluationResult
```
Run and persist one evaluation without changing lifecycle.

```python
judge_response(
    self,
    *,
    name: str,
    input: str,
    actual: str,
    expected: str,
    judge: _LLMJudge,
    required: bool = False,
    execution_id: _Any = None,
    turn_id: _Any = None,
    case_id: str | None = None,
) -> _EvaluationResult
```

```python
evaluation_results(
    self,
) -> tuple[_EvaluationResult, ...]
```

```python
agents(
    self,
    selections: _Any,
    *,
    trials: int = 1,
) -> tuple[_Any, ...]
```
Expand ordered agent dictionaries without starting any I/O.

```python
run(
    self,
    spec: _DirectSpec | _AgentSpec,
) -> _ExecutionResult
```

```python
submit(
    self,
    spec: _ExecutionSpec,
    *,
    human_input: _HumanInput = 'fail',
) -> AsyncExecutionHandle
```

```python
direct(
    self,
    server: _ServerValue | _ServerBinding,
    *,
    protocol: object | None = None,
    timeout: float | None = None,
    validate_schemas: bool = False,
    secret_resolver: _SecretResolver | None = None,
    bearer_token: _SecretReference | None = None,
    auth: httpx2.Auth | None = None,
    for_agent: bool = False,
    resolve_host: _HostResolver | None = None,
    raise_server_exceptions: bool = True,
    sampling_callback: _Any = None,
    elicitation_callback: _RemovedElicitationCallback = ...,
    list_roots_callback: _Any = None,
    logging_callback: _Any = None,
    message_handler: _Any = None,
    client_info: _Any = None,
    log_level: _Any = None,
    sampling_capabilities: _Any = None,
    result_claims: _Any = None,
    extensions: _Mapping[str, _Mapping[str, _Any]] | None = None,
    notification_bindings: _Iterable[_Any] | None = None,
    dispatcher: _Any = None,
    trace_bridge: _DirectTraceBridge | None = None,
    trace_owner: bool = True,
    workspace_root: str | None = None,
) -> AsyncDirectClient
```

```python
agent_session(
    self,
    spec: _AgentSpec,
    *,
    adapter: _AgentAdapter | None = None,
    runtime_servers: _Iterable[_Any] = (),
    _event_sink: _Any = None,
    interaction_handlers: InteractionHandlers | None = None,
    _trace_recorder: _ExecutionTraceRecorder | None = None,
    _trace_owner: bool = True,
    _execution_id: _Any = None,
    _artifact_store: _ArtifactStore | None = None,
    _managed_input_runtime: _Any = None,
    harness_cache_dir: str | _Path | None = None,
) -> AsyncAgentSession
```

## `CallToolResult`

```python
m3.async_api.CallToolResult(
    *,
    raw: Any = None,
    content: tuple[collections.abc.Mapping[str, Any], ...] = (),
    structured_content: Any = None,
    is_error: bool = False,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `content` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |
| `structured_content` | `Any` | No | `None` | — | — |
| `is_error` | `bool` | No | `False` | — | — |

## `CompletionResult`

```python
m3.async_api.CompletionResult(
    *,
    raw: Any = None,
    values: tuple[str, ...] = (),
    total: int | None = None,
    has_more: bool | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `values` | `tuple[str, ...]` | No | `()` | — | — |
| `total` | `int \| None` | No | `None` | — | — |
| `has_more` | `bool \| None` | No | `None` | — | — |

## `ConfigOrigin`

```python
m3.async_api.ConfigOrigin(
    *,
    source: m3.configuration.ConfigSource,
    origin: str,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `source` | `m3.configuration.ConfigSource` | Yes | — | — | — |
| `origin` | `str` | Yes | — | `min_length=1, max_length=4096` | — |

## `ConfigSource`

```python
m3.async_api.ConfigSource(
    *values,
)
```

- `EXPLICIT` = `'explicit'`
- `ENVIRONMENT` = `'environment'`
- `PROJECT` = `'project'`
- `DEFAULT` = `'default'`

## `Config`

```python
m3.async_api.Config(
    *,
    artifact_policy: Literal['failed', 'always', 'never'] = 'failed',
    protocol_revision: str = 'auto',
    telemetry_enabled: bool = False,
    sources: collections.abc.Mapping[str, m3.configuration.ConfigOrigin] = ...,
) -> None
```

Effective SDK-wide settings and the origin of each setting.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `artifact_policy` | `Literal['failed', 'always', 'never']` | No | `'failed'` | — | — |
| `protocol_revision` | `str` | No | `'auto'` | `strict=True` | — |
| `telemetry_enabled` | `bool` | No | `False` | `strict=True` | — |
| `sources` | `collections.abc.Mapping[str, m3.configuration.ConfigOrigin]` | No | `factory m3.configuration._default_origins()` | — | — |
- `provenance` (property): Compatibility name for callers that call origins provenance.

```python
source_for(
    self,
    field: str,
) -> ConfigOrigin
```

## `ConfigError`

```python
m3.async_api.ConfigError(
    *,
    field: str,
    origin: str,
    reason: str,
    code: str | None = None,
) -> None
```

A strict, value-free configuration diagnostic.

## `PromptInfo`

```python
m3.async_api.PromptInfo(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    description: str | None = None,
    arguments: tuple[collections.abc.Mapping[str, Any], ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `description` | `str \| None` | No | `None` | — | — |
| `arguments` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |

## `ResourceInfo`

```python
m3.async_api.ResourceInfo(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    uri: str,
    description: str | None = None,
    mime_type: str | None = None,
    size: int | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `uri` | `str` | Yes | — | `min_length=1, max_length=4096` | — |
| `description` | `str \| None` | No | `None` | — | — |
| `mime_type` | `str \| None` | No | `None` | — | — |
| `size` | `int \| None` | No | `None` | `ge=0` | — |

## `TemplateInfo`

```python
m3.async_api.TemplateInfo(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    uri_template: str,
    description: str | None = None,
    mime_type: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `uri_template` | `str` | Yes | — | `min_length=1, max_length=4096` | — |
| `description` | `str \| None` | No | `None` | — | — |
| `mime_type` | `str \| None` | No | `None` | — | — |

## `ToolInfo`

```python
m3.async_api.ToolInfo(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    description: str | None = None,
    input_schema: collections.abc.Mapping[str, Any] | bool = ...,
    output_schema: collections.abc.Mapping[str, Any] | bool | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `description` | `str \| None` | No | `None` | — | — |
| `input_schema` | `collections.abc.Mapping[str, Any] \| bool` | No | `factory builtins.dict()` | — | — |
| `output_schema` | `collections.abc.Mapping[str, Any] \| bool \| None` | No | `None` | — | — |

## `EmptyResult`

```python
m3.async_api.EmptyResult(
    *,
    raw: Any = None,
    result_type: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `result_type` | `str \| None` | No | `None` | — | — |

## `GetPromptResult`

```python
m3.async_api.GetPromptResult(
    *,
    raw: Any = None,
    description: str | None = None,
    messages: tuple[collections.abc.Mapping[str, Any], ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `description` | `str \| None` | No | `None` | — | — |
| `messages` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |

## `InitializeResult`

```python
m3.async_api.InitializeResult(
    *,
    raw: Any = None,
    protocol_version: str,
    server_info: collections.abc.Mapping[str, Any],
    instructions: str | None = None,
    capabilities: collections.abc.Mapping[str, Any] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `protocol_version` | `str` | Yes | — | — | — |
| `server_info` | `collections.abc.Mapping[str, Any]` | Yes | — | — | — |
| `instructions` | `str \| None` | No | `None` | — | — |
| `capabilities` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |

## `InitializationResult`

```python
m3.async_api.InitializationResult(
    *,
    raw: Any = None,
    protocol_version: str,
    server_info: collections.abc.Mapping[str, Any],
    instructions: str | None = None,
    capabilities: collections.abc.Mapping[str, Any] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `protocol_version` | `str` | Yes | — | — | — |
| `server_info` | `collections.abc.Mapping[str, Any]` | Yes | — | — | — |
| `instructions` | `str \| None` | No | `None` | — | — |
| `capabilities` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |

## `InputRequiredResult`

```python
m3.async_api.InputRequiredResult(
    *,
    raw: Any = None,
    result_type: Literal['input_required'] = 'input_required',
    input_requests: collections.abc.Mapping[str, Any] | None = None,
    request_state: str | None = None,
) -> None
```

Official MCP interactive result, preserved instead of coercing empty data.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `result_type` | `Literal['input_required']` | No | `'input_required'` | — | — |
| `input_requests` | `collections.abc.Mapping[str, Any] \| None` | No | `None` | — | — |
| `request_state` | `str \| None` | No | `None` | — | — |

## `ListPromptsResult`

```python
m3.async_api.ListPromptsResult(
    *,
    raw: Any = None,
    prompts: tuple[m3.types.PromptInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `prompts` | `tuple[m3.types.PromptInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ListResourcesResult`

```python
m3.async_api.ListResourcesResult(
    *,
    raw: Any = None,
    resources: tuple[m3.types.ResourceInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `resources` | `tuple[m3.types.ResourceInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ListResourceTemplatesResult`

```python
m3.async_api.ListResourceTemplatesResult(
    *,
    raw: Any = None,
    resource_templates: tuple[m3.types.TemplateInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `resource_templates` | `tuple[m3.types.TemplateInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ListToolsResult`

```python
m3.async_api.ListToolsResult(
    *,
    raw: Any = None,
    tools: tuple[m3.types.ToolInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `tools` | `tuple[m3.types.ToolInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ProbeEvidence`

```python
m3.async_api.ProbeEvidence(
    *,
    kind: m3.services.probes.ProbeKind,
    target: str,
    command: tuple[str, ...] = (),
    resolved_executable: str | None = None,
    detected_version: str | None = None,
    protocol_version: str | None = None,
    output: str = '',
    details: collections.abc.Mapping[str, Any] = ...,
) -> None
```

Safe evidence collected by one probe.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `m3.services.probes.ProbeKind` | Yes | — | — | — |
| `target` | `str` | Yes | — | `min_length=1, max_length=512` | — |
| `command` | `tuple[str, ...]` | No | `()` | — | — |
| `resolved_executable` | `str \| None` | No | `None` | — | — |
| `detected_version` | `str \| None` | No | `None` | — | — |
| `protocol_version` | `str \| None` | No | `None` | — | — |
| `output` | `str` | No | `''` | `max_length=65536` | — |
| `details` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |

## `ProbeKind`

```python
m3.async_api.ProbeKind(
    *values,
)
```

The independently requestable capability categories.

- `CONFIGURATION` = `'configuration'`
- `BINARY` = `'binary'`
- `PROTOCOL` = `'protocol'`
- `TRANSPORT` = `'transport'`
- `STORAGE` = `'storage'`
- `HARNESS` = `'harness'`

## `ProbeReport`

```python
m3.async_api.ProbeReport(
    *,
    readiness: m3.types.Readiness,
    results: tuple[m3.services.probes.ProbeResult, ...] = (),
) -> None
```

Aggregate readiness for exactly the requested probes.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `readiness` | `m3.types.Readiness` | Yes | — | — | — |
| `results` | `tuple[m3.services.probes.ProbeResult, ...]` | No | `()` | — | — |
- `capabilities` (property)

```python
result_for(
    self,
    name: str,
) -> ProbeResult | None
```
Return the result for ``name`` without guessing another target.

## `ProbeRequest`

```python
m3.async_api.ProbeRequest(
    kind: ProbeKind,
    name: str,
    executable: str | None = None,
    args: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
    module: str | None = None,
    transport: str | None = None,
    timeout_seconds: float | None = None,
) -> None
```

Typed request used by :meth:`Probes.probe_requested`.

## `ProbeResult`

```python
m3.async_api.ProbeResult(
    *,
    capability: m3.types.Capability,
    evidence: m3.services.probes.ProbeEvidence,
) -> None
```

One capability result and its separately inspectable evidence.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `capability` | `m3.types.Capability` | Yes | — | — | — |
| `evidence` | `m3.services.probes.ProbeEvidence` | Yes | — | — | — |
- `status` (property)

## `Prompt`

```python
m3.async_api.Prompt(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    description: str | None = None,
    arguments: tuple[collections.abc.Mapping[str, Any], ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `description` | `str \| None` | No | `None` | — | — |
| `arguments` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |

## `PromptPage`

```python
m3.async_api.PromptPage(
    *,
    raw: Any = None,
    prompts: tuple[m3.types.PromptInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `prompts` | `tuple[m3.types.PromptInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `PromptResult`

```python
m3.async_api.PromptResult(
    *,
    raw: Any = None,
    description: str | None = None,
    messages: tuple[collections.abc.Mapping[str, Any], ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `description` | `str \| None` | No | `None` | — | — |
| `messages` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |

## `ReadResourceResult`

```python
m3.async_api.ReadResourceResult(
    *,
    raw: Any = None,
    contents: tuple[collections.abc.Mapping[str, Any], ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `contents` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |
- `text` (property)

## `Resource`

```python
m3.async_api.Resource(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    uri: str,
    description: str | None = None,
    mime_type: str | None = None,
    size: int | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `uri` | `str` | Yes | — | `min_length=1, max_length=4096` | — |
| `description` | `str \| None` | No | `None` | — | — |
| `mime_type` | `str \| None` | No | `None` | — | — |
| `size` | `int \| None` | No | `None` | `ge=0` | — |

## `ResourcePage`

```python
m3.async_api.ResourcePage(
    *,
    raw: Any = None,
    resources: tuple[m3.types.ResourceInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `resources` | `tuple[m3.types.ResourceInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ResourceReadResult`

```python
m3.async_api.ResourceReadResult(
    *,
    raw: Any = None,
    contents: tuple[collections.abc.Mapping[str, Any], ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `contents` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |
- `text` (property)

## `ResourceTemplate`

```python
m3.async_api.ResourceTemplate(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    uri_template: str,
    description: str | None = None,
    mime_type: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `uri_template` | `str` | Yes | — | `min_length=1, max_length=4096` | — |
| `description` | `str \| None` | No | `None` | — | — |
| `mime_type` | `str \| None` | No | `None` | — | — |

## `ResourceTemplatePage`

```python
m3.async_api.ResourceTemplatePage(
    *,
    raw: Any = None,
    resource_templates: tuple[m3.types.TemplateInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `resource_templates` | `tuple[m3.types.TemplateInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ResourceTemplatesPage`

```python
m3.async_api.ResourceTemplatesPage(
    *,
    raw: Any = None,
    resource_templates: tuple[m3.types.TemplateInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `resource_templates` | `tuple[m3.types.TemplateInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ResourcesPage`

```python
m3.async_api.ResourcesPage(
    *,
    raw: Any = None,
    resources: tuple[m3.types.ResourceInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `resources` | `tuple[m3.types.ResourceInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `Tool`

```python
m3.async_api.Tool(
    *,
    raw: Any = None,
    name: str,
    title: str | None = None,
    description: str | None = None,
    input_schema: collections.abc.Mapping[str, Any] | bool = ...,
    output_schema: collections.abc.Mapping[str, Any] | bool | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `title` | `str \| None` | No | `None` | — | — |
| `description` | `str \| None` | No | `None` | — | — |
| `input_schema` | `collections.abc.Mapping[str, Any] \| bool` | No | `factory builtins.dict()` | — | — |
| `output_schema` | `collections.abc.Mapping[str, Any] \| bool \| None` | No | `None` | — | — |

## `ToolCallResult`

```python
m3.async_api.ToolCallResult(
    *,
    raw: Any = None,
    content: tuple[collections.abc.Mapping[str, Any], ...] = (),
    structured_content: Any = None,
    is_error: bool = False,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `content` | `tuple[collections.abc.Mapping[str, Any], ...]` | No | `()` | — | — |
| `structured_content` | `Any` | No | `None` | — | — |
| `is_error` | `bool` | No | `False` | — | — |

## `ToolPage`

```python
m3.async_api.ToolPage(
    *,
    raw: Any = None,
    tools: tuple[m3.types.ToolInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `tools` | `tuple[m3.types.ToolInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `ToolsPage`

```python
m3.async_api.ToolsPage(
    *,
    raw: Any = None,
    tools: tuple[m3.types.ToolInfo, ...] = (),
    next_cursor: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `raw` | `Any` | No | `None` | — | — |
| `tools` | `tuple[m3.types.ToolInfo, ...]` | No | `()` | — | — |
| `next_cursor` | `str \| None` | No | `None` | — | — |

## `load_config`

```python
m3.async_api.load_config(
    explicit: _Mapping[str, _Any] | None = None,
    *,
    env: _Mapping[str, str] | None = None,
    cwd: str | _Path | None = None,
    artifact_policy: _Any = ...,
    protocol_revision: _Any = ...,
    telemetry_enabled: _Any = ...,
) -> Config
```

Resolve SDK settings using explicit, environment, project, default order.

## `AllowedCommands`

```python
m3.async_api.AllowedCommands(
    *,
    allowed_executables: Sequence[str],
    root: str | Path,
    environment: Mapping[str, str] | None = None,
    allowed_environment: Sequence[str] = (),
) -> None
```

Safe argv-only terminal handler with cwd, timeout, and output bounds.

## `FilesystemHandler`

```python
m3.async_api.FilesystemHandler(
    *args,
    **kwargs,
)
```

## `FilesystemRequest`

```python
m3.async_api.FilesystemRequest(
    operation: FilesystemOperation,
    path: str,
    data: bytes | None = None,
    max_bytes: int = 1048576,
) -> None
```

## `FilesystemResult`

```python
m3.async_api.FilesystemResult(
    allowed: bool,
    data: bytes | tuple[str, ...] | None,
    receipt: InteractionReceipt,
) -> None
```

## `Interactions`

```python
m3.async_api.Interactions(
    *,
    permission_policy: PermissionPolicy | None = None,
    sampling_policy: SamplingPolicy | None = None,
    filesystem_policy: FilesystemPolicy | None = None,
    terminal_policy: TerminalPolicy | None = None,
    handlers: InteractionHandlers | None = None,
) -> None
```

Apply immutable policies around explicit interaction callbacks.


```python
receipts(
    self,
) -> tuple[InteractionReceipt, ...]
```

```python
permission(
    self,
    request: PermissionRequest,
) -> PermissionResult
```

```python
sample(
    self,
    request: SamplingRequest,
) -> SamplingResult
```

```python
filesystem(
    self,
    request: FilesystemRequest,
) -> FilesystemResult
```

```python
terminal(
    self,
    request: TerminalRequest,
) -> TerminalResult
```

## `InteractionHandlers`

```python
m3.async_api.InteractionHandlers(
    permission: PermissionCallback | None = None,
    sampling: SamplingCallback | None = None,
    filesystem: FilesystemHandler | None = None,
    terminal: TerminalHandler | None = None,
) -> None
```

Optional callbacks; absent callbacks are always default-deny.

## `InteractionReceipt`

```python
m3.async_api.InteractionReceipt(
    request_id: str,
    kind: str,
    decision: Decision,
    reason: str,
    timestamp: datetime = ...,
) -> None
```

Safe decision evidence; request values and handler errors are excluded.

## `PermissionRequest`

```python
m3.async_api.PermissionRequest(
    operation: str,
    resource: str = '',
    destructive: bool = False,
) -> None
```

## `PermissionResult`

```python
m3.async_api.PermissionResult(
    allowed: bool,
    receipt: InteractionReceipt,
    confirmation_required: bool = False,
) -> None
```

## `PermissionHandler`

```python
m3.async_api.PermissionHandler(
    *args,
    **kwargs,
)
```

## `SamplingRequest`

```python
m3.async_api.SamplingRequest(
    prompt: str,
    model: str | None = None,
    metadata: Mapping[str, str | int | float | bool | None] = ...,
) -> None
```

## `SamplingResult`

```python
m3.async_api.SamplingResult(
    accepted: bool,
    content: str | None,
    receipt: InteractionReceipt,
) -> None
```

## `SamplingHandler`

```python
m3.async_api.SamplingHandler(
    *args,
    **kwargs,
)
```

## `TerminalHandler`

```python
m3.async_api.TerminalHandler(
    *args,
    **kwargs,
)
```

## `TerminalRequest`

```python
m3.async_api.TerminalRequest(
    argv: tuple[str, ...],
    cwd: str | None = None,
    environment: Mapping[str, str] = ...,
    timeout_seconds: float = 30.0,
    max_output_bytes: int = 1048576,
) -> None
```

## `TerminalResult`

```python
m3.async_api.TerminalResult(
    allowed: bool,
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    timed_out: bool,
    truncated: bool,
    receipt: InteractionReceipt,
) -> None
```

## `WorkspaceFiles`

```python
m3.async_api.WorkspaceFiles(
    root: str | Path,
    *,
    mode: Literal['read_only', 'read_write'] = 'read_only',
    max_bytes: int = 1048576,
) -> None
```

Bounded filesystem handler rooted inside one owned workspace.

## `ElicitationPlan`

```python
m3.async_api.ElicitationPlan(
    *,
    node: Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of'] = 'leaf',
    request: m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation | None = None,
    response: m3.elicitation.ElicitationResponse | None = None,
    children: tuple[m3.elicitation.ElicitationPlan, ...] = (),
    optional_occurrence: bool = False,
) -> None
```

An immutable, serializable elicitation expectation tree.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `node` | `Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of']` | No | `'leaf'` | — | — |
| `request` | `m3.elicitation._FormExpectation \| m3.elicitation._UrlExpectation \| None` | No | `None` | `variant 1: discriminator='mode'` | — |
| `response` | `m3.elicitation.ElicitationResponse \| None` | No | `None` | — | — |
| `children` | `tuple[m3.elicitation.ElicitationPlan, ...]` | No | `()` | — | — |
| `optional_occurrence` | `bool` | No | `False` | — | — |
- `is_complete` (property)
- `mode` (property)
- `requested_schema` (property)
- `optional` (property)

```python
accept(
    self,
    content: Mapping[str, object] | None = None,
) -> ElicitationPlan
```

```python
decline(
    self,
) -> ElicitationPlan
```

```python
cancel(
    self,
) -> ElicitationPlan
```

```python
canonical_identity(
    self,
) -> str
```

```python
canonical_json(
    self,
) -> str
```

```python
model_dump(
    self,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]
```

```python
model_dump_json(
    self,
    *args: Any,
    **kwargs: Any,
) -> str
```

```python
matcher(
    self,
) -> PlanMatcher
```

## `ElicitationResponse`

```python
m3.async_api.ElicitationResponse(
    *,
    action: Literal['accept', 'decline', 'cancel'],
    content: collections.abc.Mapping[str, object] | None = None,
    meta: collections.abc.Mapping[str, object] | None = None,
) -> None
```

The response that will be associated with one request key.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `action` | `Literal['accept', 'decline', 'cancel']` | Yes | — | — | — |
| `content` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `meta` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |

## `FormElicitationRequest`

```python
m3.async_api.FormElicitationRequest(
    *,
    request_key: str,
    mode: Literal['form'] = 'form',
    message: str,
    requested_schema: collections.abc.Mapping[str, object],
    meta: collections.abc.Mapping[str, object] | None = None,
    task: collections.abc.Mapping[str, object] | None = None,
    server: str | None = None,
    operation_kind: Literal['tool', 'prompt', 'resource'] | None = None,
    operation_name: str | None = None,
) -> None
```

A normalized form-mode elicitation request.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `request_key` | `str` | Yes | — | `min_length=1` | — |
| `mode` | `Literal['form']` | No | `'form'` | — | — |
| `message` | `str` | Yes | — | — | — |
| `requested_schema` | `collections.abc.Mapping[str, object]` | Yes | — | — | — |
| `meta` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `task` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `server` | `str \| None` | No | `None` | — | — |
| `operation_kind` | `Literal['tool', 'prompt', 'resource'] \| None` | No | `None` | — | — |
| `operation_name` | `str \| None` | No | `None` | — | — |

## `PendingElicitationRound`

```python
m3.async_api.PendingElicitationRound(
    *,
    round_id: str,
    execution_id: str,
    logical_operation_id: str,
    server: str,
    operation_kind: Literal['tool', 'prompt', 'resource'],
    operation_name: str,
    request_state: str | None = None,
    requests: collections.abc.Mapping[str, m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest],
    created_at: datetime.datetime,
    deadline: datetime.datetime | None = None,
) -> None
```

A persisted, keyed set of elicitation requests awaiting responses.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `round_id` | `str` | Yes | — | `min_length=1` | — |
| `execution_id` | `str` | Yes | — | `min_length=1` | — |
| `logical_operation_id` | `str` | Yes | — | `min_length=1` | — |
| `server` | `str` | Yes | — | `min_length=1` | — |
| `operation_kind` | `Literal['tool', 'prompt', 'resource']` | Yes | — | — | — |
| `operation_name` | `str` | Yes | — | `min_length=1` | — |
| `request_state` | `str \| None` | No | `None` | — | — |
| `requests` | `collections.abc.Mapping[str, m3.elicitation.FormElicitationRequest \| m3.elicitation.UrlElicitationRequest]` | Yes | — | `type argument 2: discriminator='mode'` | — |
| `created_at` | `datetime.datetime` | Yes | — | — | — |
| `deadline` | `datetime.datetime \| None` | No | `None` | — | — |

## `UrlElicitationRequest`

```python
m3.async_api.UrlElicitationRequest(
    *,
    request_key: str,
    mode: Literal['url'] = 'url',
    message: str,
    url: str,
    elicitation_id: str | None = None,
    meta: collections.abc.Mapping[str, object] | None = None,
    task: collections.abc.Mapping[str, object] | None = None,
    server: str | None = None,
    operation_kind: Literal['tool', 'prompt', 'resource'] | None = None,
    operation_name: str | None = None,
) -> None
```

A normalized URL-mode elicitation request.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `request_key` | `str` | Yes | — | `min_length=1` | — |
| `mode` | `Literal['url']` | No | `'url'` | — | — |
| `message` | `str` | Yes | — | — | — |
| `url` | `str` | Yes | — | — | — |
| `elicitation_id` | `str \| None` | No | `None` | — | — |
| `meta` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `task` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `server` | `str \| None` | No | `None` | — | — |
| `operation_kind` | `Literal['tool', 'prompt', 'resource'] \| None` | No | `None` | — | — |
| `operation_name` | `str \| None` | No | `None` | — | — |

## `expect_form`

```python
m3.async_api.expect_form(
    request_key: str,
    *,
    message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `expect_url`

```python
m3.async_api.expect_url(
    request_key: str,
    *,
    message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `maybe_form`

```python
m3.async_api.maybe_form(
    request_key: str,
    *,
    message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `maybe_url`

```python
m3.async_api.maybe_url(
    request_key: str,
    *,
    message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `one_of`

```python
m3.async_api.one_of(
    *children: ElicitationPlan,
) -> ElicitationPlan
```

## `optional`

```python
m3.async_api.optional(
    child: ElicitationPlan,
) -> ElicitationPlan
```

## `round_of`

```python
m3.async_api.round_of(
    *children: ElicitationPlan,
) -> ElicitationPlan
```

## `sequence`

```python
m3.async_api.sequence(
    *children: ElicitationPlan,
) -> ElicitationPlan
```

## `ACPTrace`

```python
m3.async_api.ACPTrace(
    *,
    kind: Literal['acp'] = 'acp',
    session_id: m3.observability.Observation[str] = ...,
    protocol_version: m3.observability.Observation[str] = ...,
    agent_identity: m3.observability.Observation[JsonValue] = ...,
    available_modes: m3.observability.Observation[JsonValue] = ...,
    current_mode: m3.observability.Observation[str] = ...,
    config_options: m3.observability.Observation[JsonValue] = ...,
    selected_config: m3.observability.Observation[JsonValue] = ...,
    plan_state_available: m3.observability.Observation[bool] = ...,
    usage: m3.observability.Observation[m3.observability.UsageValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `Literal['acp']` | No | `'acp'` | — | — |
| `session_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `protocol_version` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `agent_identity` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `available_modes` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `current_mode` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `config_options` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `selected_config` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `plan_state_available` | `m3.observability.Observation[bool]` | No | `factory m3.observability._not_emitted()` | — | — |
| `usage` | `m3.observability.Observation[m3.observability.UsageValue]` | No | `factory m3.observability.ACPTrace.<lambda>()` | — | — |

## `ArtifactEntry`

```python
m3.async_api.ArtifactEntry(
    *,
    entry_id: str,
    kind: Literal['artifact'] = 'artifact',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    artifact: m3.types.ArtifactRef,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['artifact']` | No | `'artifact'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `artifact` | `m3.types.ArtifactRef` | Yes | — | — | — |

## `CaptureOptions`

```python
m3.async_api.CaptureOptions(
    *,
    capture_raw_evidence: bool = True,
    capture_provider_messages: bool = True,
    capture_stderr: bool = True,
    raw_preview_bytes: int = 65536,
    raw_frame_bytes: int = 1048576,
    raw_execution_bytes: int = 67108864,
) -> None
```

Boundaries for redacted provider/MCP evidence capture.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `capture_raw_evidence` | `bool` | No | `True` | — | — |
| `capture_provider_messages` | `bool` | No | `True` | — | — |
| `capture_stderr` | `bool` | No | `True` | — | — |
| `raw_preview_bytes` | `int` | No | `65536` | `gt=0` | — |
| `raw_frame_bytes` | `int` | No | `1048576` | `gt=0` | — |
| `raw_execution_bytes` | `int` | No | `67108864` | `gt=0` | — |

## `ClaudeCodeTrace`

```python
m3.async_api.ClaudeCodeTrace(
    *,
    kind: Literal['claude_code'] = 'claude_code',
    session_id: m3.observability.Observation[str] = ...,
    model_id: m3.observability.Observation[str] = ...,
    result_subtype: m3.observability.Observation[str] = ...,
    stop_reason: m3.observability.Observation[str] = ...,
    service_tier: m3.observability.Observation[str] = ...,
    api_duration_ms: m3.observability.Observation[float] = ...,
    encrypted_reasoning: m3.observability.Observation[bool] = ...,
    usage: m3.observability.Observation[m3.observability.UsageValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `Literal['claude_code']` | No | `'claude_code'` | — | — |
| `session_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `model_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `result_subtype` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `stop_reason` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `service_tier` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `api_duration_ms` | `m3.observability.Observation[float]` | No | `factory m3.observability._not_emitted()` | — | — |
| `encrypted_reasoning` | `m3.observability.Observation[bool]` | No | `factory m3.observability._not_emitted()` | — | — |
| `usage` | `m3.observability.Observation[m3.observability.UsageValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `CodexTrace`

```python
m3.async_api.CodexTrace(
    *,
    kind: Literal['codex'] = 'codex',
    thread_id: m3.observability.Observation[str] = ...,
    turn_id: m3.observability.Observation[str] = ...,
    model_id: m3.observability.Observation[str] = ...,
    finish_reason: m3.observability.Observation[str] = ...,
    sandbox: m3.observability.Observation[str] = ...,
    usage: m3.observability.Observation[m3.observability.UsageValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `Literal['codex']` | No | `'codex'` | — | — |
| `thread_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `turn_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `model_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `finish_reason` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `sandbox` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `usage` | `m3.observability.Observation[m3.observability.UsageValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `CorrelationState`

```python
m3.async_api.CorrelationState(
    *values,
)
```

- `CORRELATED` = `'correlated'`
- `REPORTED_ONLY` = `'reported_only'`
- `WIRE_ONLY` = `'wire_only'`
- `AMBIGUOUS` = `'ambiguous'`
- `UNAVAILABLE` = `'unavailable'`

## `DiagnosticEntry`

```python
m3.async_api.DiagnosticEntry(
    *,
    entry_id: str,
    kind: Literal['diagnostic'] = 'diagnostic',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    code: str,
    message: str,
    stage: str | None = None,
    operation: str | None = None,
    elapsed_seconds: float | None = None,
    timeout_seconds: float | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['diagnostic']` | No | `'diagnostic'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `code` | `str` | Yes | — | `min_length=1, max_length=128` | — |
| `message` | `str` | Yes | — | `min_length=1, max_length=4096` | — |
| `stage` | `str \| None` | No | `None` | `max_length=128` | — |
| `operation` | `str \| None` | No | `None` | `max_length=256` | — |
| `elapsed_seconds` | `float \| None` | No | `None` | `ge=0` | — |
| `timeout_seconds` | `float \| None` | No | `None` | `gt=0` | — |

## `DirectTrace`

```python
m3.async_api.DirectTrace(
    *,
    kind: Literal['direct'] = 'direct',
    transport: m3.observability.Observation[m3.types.TransportKind] = ...,
    protocol: m3.observability.Observation[str] = ...,
    initialization: m3.observability.Observation[m3.observability.InitializationValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `Literal['direct']` | No | `'direct'` | — | — |
| `transport` | `m3.observability.Observation[m3.types.TransportKind]` | No | `factory m3.observability._not_emitted()` | — | — |
| `protocol` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `initialization` | `m3.observability.Observation[m3.observability.InitializationValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `ElicitationEntry`

```python
m3.async_api.ElicitationEntry(
    *,
    entry_id: str,
    kind: Literal['elicitation'] = 'elicitation',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    server: str | None = None,
    operation_kind: Literal['tool', 'prompt', 'resource'],
    operation_name: str,
    logical_operation_id: str,
    round_index: int,
    request_key: str,
    mode: Literal['form', 'url'],
    message: m3.observability.Observation[str] = ...,
    requested_schema: m3.observability.Observation[JsonValue] = ...,
    url: m3.observability.Observation[str] = ...,
    elicitation_id: m3.observability.Observation[str] = ...,
    request_state: m3.observability.Observation[str] = ...,
    input_responses: m3.observability.Observation[JsonValue] = ...,
    action: Literal['accept', 'decline', 'cancel'] | None = None,
    content: m3.observability.Observation[JsonValue] = ...,
) -> None
```

One keyed elicitation embedded in an MRTR input-required round.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['elicitation']` | No | `'elicitation'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `operation_kind` | `Literal['tool', 'prompt', 'resource']` | Yes | — | — | — |
| `operation_name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `logical_operation_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `round_index` | `int` | Yes | — | `ge=1` | — |
| `request_key` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `mode` | `Literal['form', 'url']` | Yes | — | — | — |
| `message` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `requested_schema` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `url` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `elicitation_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `request_state` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `input_responses` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `action` | `Literal['accept', 'decline', 'cancel'] \| None` | No | `None` | — | — |
| `content` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `EvaluationEntry`

```python
m3.async_api.EvaluationEntry(
    *,
    entry_id: str,
    kind: Literal['evaluation'] = 'evaluation',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    evaluation: m3.types.EvaluationResult,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['evaluation']` | No | `'evaluation'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `evaluation` | `m3.types.EvaluationResult` | Yes | — | — | — |

## `EvidenceCapture`

```python
m3.async_api.EvidenceCapture(
    *,
    reference: m3.types.EvidenceRef,
    preview: m3.observability.Observation[str],
    original_size_bytes: int,
    stored_size_bytes: int,
    redacted: bool,
    truncated: bool,
) -> None
```

Typed result of bounded, redacted raw-evidence capture.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `reference` | `m3.types.EvidenceRef` | Yes | — | — | — |
| `preview` | `m3.observability.Observation[str]` | Yes | — | — | — |
| `original_size_bytes` | `int` | Yes | — | `ge=0` | — |
| `stored_size_bytes` | `int` | Yes | — | `ge=0` | — |
| `redacted` | `bool` | Yes | — | — | — |
| `truncated` | `bool` | Yes | — | — | — |

## `EvidenceConflict`

```python
m3.async_api.EvidenceConflict(
    *,
    field: Literal['server', 'tool', 'arguments', 'result', 'status'],
    reported: m3.observability.Observation[JsonValue],
    wire: m3.observability.Observation[JsonValue],
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `field` | `Literal['server', 'tool', 'arguments', 'result', 'status']` | Yes | — | — | — |
| `reported` | `m3.observability.Observation[JsonValue]` | Yes | — | — | — |
| `wire` | `m3.observability.Observation[JsonValue]` | Yes | — | — | — |

## `HttpExchange`

```python
m3.async_api.HttpExchange(
    *,
    method: str,
    status_code: int,
    headers: tuple[m3.observability.SafeHttpHeader, ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `method` | `str` | Yes | — | `min_length=1` | — |
| `status_code` | `int` | Yes | — | `ge=100, le=599` | — |
| `headers` | `tuple[m3.observability.SafeHttpHeader, ...]` | No | `()` | — | — |

## `InitializationEntry`

```python
m3.async_api.InitializationEntry(
    *,
    entry_id: str,
    kind: Literal['initialization'] = 'initialization',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    protocol_version: m3.observability.Observation[str] = ...,
    server_name: m3.observability.Observation[str] = ...,
    server_version: m3.observability.Observation[str] = ...,
    instructions: m3.observability.Observation[str] = ...,
    capabilities: m3.observability.Observation[JsonValue] = ...,
    tools: m3.observability.Observation[tuple[m3.types.ToolInfo, ...]] = ...,
    resources: m3.observability.Observation[tuple[m3.types.ResourceInfo, ...]] = ...,
    resource_templates: m3.observability.Observation[tuple[m3.types.TemplateInfo, ...]] = ...,
    prompts: m3.observability.Observation[tuple[m3.types.PromptInfo, ...]] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['initialization']` | No | `'initialization'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `protocol_version` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `server_name` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `server_version` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `instructions` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `capabilities` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `tools` | `m3.observability.Observation[tuple[m3.types.ToolInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |
| `resources` | `m3.observability.Observation[tuple[m3.types.ResourceInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |
| `resource_templates` | `m3.observability.Observation[tuple[m3.types.TemplateInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |
| `prompts` | `m3.observability.Observation[tuple[m3.types.PromptInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |

## `InitializationValue`

```python
m3.async_api.InitializationValue(
    *,
    protocol_version: m3.observability.Observation[str] = ...,
    server_name: m3.observability.Observation[str] = ...,
    server_version: m3.observability.Observation[str] = ...,
    instructions: m3.observability.Observation[str] = ...,
    capabilities: m3.observability.Observation[JsonValue] = ...,
    tools: m3.observability.Observation[tuple[m3.types.ToolInfo, ...]] = ...,
    resources: m3.observability.Observation[tuple[m3.types.ResourceInfo, ...]] = ...,
    resource_templates: m3.observability.Observation[tuple[m3.types.TemplateInfo, ...]] = ...,
    prompts: m3.observability.Observation[tuple[m3.types.PromptInfo, ...]] = ...,
) -> None
```

Value-only initialization metadata used by runtime information.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `protocol_version` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `server_name` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `server_version` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `instructions` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `capabilities` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `tools` | `m3.observability.Observation[tuple[m3.types.ToolInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |
| `resources` | `m3.observability.Observation[tuple[m3.types.ResourceInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |
| `resource_templates` | `m3.observability.Observation[tuple[m3.types.TemplateInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |
| `prompts` | `m3.observability.Observation[tuple[m3.types.PromptInfo, ...]]` | No | `factory m3.observability._not_emitted()` | — | — |

## `InteractionEntry`

```python
m3.async_api.InteractionEntry(
    *,
    entry_id: str,
    kind: Literal['interaction'] = 'interaction',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    interaction_kind: str,
    request: m3.observability.Observation[JsonValue] = ...,
    response: m3.observability.Observation[JsonValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['interaction']` | No | `'interaction'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `interaction_kind` | `str` | Yes | — | `min_length=1, max_length=128` | — |
| `request` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `response` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `LifecycleEntry`

```python
m3.async_api.LifecycleEntry(
    *,
    entry_id: str,
    kind: Literal['lifecycle'] = 'lifecycle',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    phase: str,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['lifecycle']` | No | `'lifecycle'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `phase` | `str` | Yes | — | `min_length=1, max_length=128` | — |

## `MessageEntry`

```python
m3.async_api.MessageEntry(
    *,
    entry_id: str,
    kind: Literal['message'] = 'message',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    message_id: m3.observability.Observation[str] = ...,
    role: m3.observability.MessageRole = MessageRole.ASSISTANT,
    content: tuple[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, ...] = (),
    stop_reason: m3.observability.Observation[str] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['message']` | No | `'message'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `message_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `role` | `m3.observability.MessageRole` | No | `MessageRole.ASSISTANT ('assistant')` | — | — |
| `content` | `tuple[m3.types.TextContent \| m3.types.FileContent \| m3.types.ImageContent \| m3.types.AudioContent \| m3.types.ResourceLink \| m3.types.OpaqueContent, ...]` | No | `()` | `type argument 1: discriminator='kind'` | — |
| `stop_reason` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |

## `MessageRole`

```python
m3.async_api.MessageRole(
    *values,
)
```

- `USER` = `'user'`
- `ASSISTANT` = `'assistant'`
- `SYSTEM` = `'system'`
- `TOOL` = `'tool'`

## `Observation`

```python
m3.async_api.Observation(
    *,
    state: m3.observability.ObservationState,
    value: _T | None = None,
    reason: m3.observability.ObservationReason | None = None,
    provenance: tuple[m3.types.EventSource, ...] = (),
    evidence_ref: m3.types.EvidenceRef | None = None,
) -> None
```

A typed value with explicit availability and provenance.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `state` | `m3.observability.ObservationState` | Yes | — | — | — |
| `value` | `_T \| None` | No | `None` | — | — |
| `reason` | `m3.observability.ObservationReason \| None` | No | `None` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `evidence_ref` | `m3.types.EvidenceRef \| None` | No | `None` | — | — |

## `ObservationReason`

```python
m3.async_api.ObservationReason(
    *values,
)
```

- `PROVIDER_DID_NOT_EMIT` = `'provider_did_not_emit'`
- `PROVIDER_HIDDEN` = `'provider_hidden'`
- `PROVIDER_ENCRYPTED` = `'provider_encrypted'`
- `HARNESS_UNSUPPORTED` = `'harness_unsupported'`
- `TRANSPORT_NOT_APPLICABLE` = `'transport_not_applicable'`
- `CAPTURE_DISABLED` = `'capture_disabled'`
- `CAPTURE_FAILED` = `'capture_failed'`
- `EVIDENCE_TRUNCATED` = `'evidence_truncated'`
- `REDACTED_BY_POLICY` = `'redacted_by_policy'`
- `CORRELATION_UNAVAILABLE` = `'correlation_unavailable'`
- `MALFORMED_SOURCE` = `'malformed_source'`

## `ObservationState`

```python
m3.async_api.ObservationState(
    *values,
)
```

How completely a provider-dependent value was observed.

- `OBSERVED` = `'observed'`
- `NOT_EMITTED` = `'not_emitted'`
- `UNSUPPORTED` = `'unsupported'`
- `UNAVAILABLE` = `'unavailable'`
- `PROVIDER_HIDDEN` = `'provider_hidden'`
- `ENCRYPTED` = `'encrypted'`
- `REDACTED` = `'redacted'`
- `TRUNCATED` = `'truncated'`

## `OpenCodeTrace`

```python
m3.async_api.OpenCodeTrace(
    *,
    kind: Literal['opencode'] = 'opencode',
    session_id: m3.observability.Observation[str] = ...,
    provider_id: m3.observability.Observation[str] = ...,
    model_id: m3.observability.Observation[str] = ...,
    finish_reason: m3.observability.Observation[str] = ...,
    http_lifecycle: m3.observability.Observation[JsonValue] = ...,
    usage: m3.observability.Observation[m3.observability.UsageValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `Literal['opencode']` | No | `'opencode'` | — | — |
| `session_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `provider_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `model_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `finish_reason` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `http_lifecycle` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `usage` | `m3.observability.Observation[m3.observability.UsageValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `PiTrace`

```python
m3.async_api.PiTrace(
    *,
    kind: Literal['pi'] = 'pi',
    session_id: m3.observability.Observation[str] = ...,
    provider_id: m3.observability.Observation[str] = ...,
    model_id: m3.observability.Observation[str] = ...,
    finish_reason: m3.observability.Observation[str] = ...,
    usage: m3.observability.Observation[m3.observability.UsageValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `Literal['pi']` | No | `'pi'` | — | — |
| `session_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `provider_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `model_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `finish_reason` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `usage` | `m3.observability.Observation[m3.observability.UsageValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `ProcessEntry`

```python
m3.async_api.ProcessEntry(
    *,
    entry_id: str,
    kind: Literal['process'] = 'process',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    executable: m3.observability.Observation[str] = ...,
    pid: m3.observability.Observation[int] = ...,
    exit_code: m3.observability.Observation[int] = ...,
    signal: m3.observability.Observation[int] = ...,
    stderr: m3.observability.Observation[str] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['process']` | No | `'process'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `executable` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `pid` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `exit_code` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `signal` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `stderr` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |

## `ProtocolCallAttempt`

```python
m3.async_api.ProtocolCallAttempt(
    *,
    attempt_index: int,
    jsonrpc_id: m3.observability.Observation[int | str] = ...,
    request_state: m3.observability.Observation[str] = ...,
    continuation_state: m3.observability.Observation[str] = ...,
    input_responses: m3.observability.Observation[JsonValue] = ...,
    input_required: bool = False,
    status: m3.observability.TraceStatus = TraceStatus.INCOMPLETE,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
) -> None
```

One wire-level attempt belonging to a prompt or resource call.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `attempt_index` | `int` | Yes | — | `ge=0` | — |
| `jsonrpc_id` | `m3.observability.Observation[int \| str]` | No | `factory m3.observability._not_emitted()` | `variant 1: strict=True, variant 2: strict=True` | — |
| `request_state` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `continuation_state` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `input_responses` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `input_required` | `bool` | No | `False` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.INCOMPLETE ('incomplete')` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |

## `ProtocolEntry`

```python
m3.async_api.ProtocolEntry(
    *,
    entry_id: str,
    kind: Literal['protocol'] = 'protocol',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    protocol: m3.observability.ProtocolKind,
    method: m3.observability.Observation[str] = ...,
    direction: m3.types.EventDirection = EventDirection.INTERNAL,
    jsonrpc_id: m3.observability.Observation[int | str] = ...,
    request: m3.observability.Observation[JsonValue] = ...,
    response: m3.observability.Observation[JsonValue] = ...,
    error: m3.observability.Observation[m3.observability.ProtocolErrorInfo] = ...,
    http: m3.observability.Observation[m3.observability.HttpExchange] = ...,
    operation_kind: Literal['prompt', 'resource'] | None = None,
    operation_name: m3.observability.Observation[str] = ...,
    attempts: tuple[m3.observability.ProtocolCallAttempt, ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['protocol']` | No | `'protocol'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `protocol` | `m3.observability.ProtocolKind` | Yes | — | — | — |
| `method` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `direction` | `m3.types.EventDirection` | No | `EventDirection.INTERNAL ('internal')` | — | — |
| `jsonrpc_id` | `m3.observability.Observation[int \| str]` | No | `factory m3.observability._not_emitted()` | `variant 1: strict=True, variant 2: strict=True` | — |
| `request` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `response` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `error` | `m3.observability.Observation[m3.observability.ProtocolErrorInfo]` | No | `factory m3.observability._not_emitted()` | — | — |
| `http` | `m3.observability.Observation[m3.observability.HttpExchange]` | No | `factory m3.observability._not_emitted()` | — | — |
| `operation_kind` | `Literal['prompt', 'resource'] \| None` | No | `None` | — | — |
| `operation_name` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `attempts` | `tuple[m3.observability.ProtocolCallAttempt, ...]` | No | `()` | — | — |

## `ProtocolErrorInfo`

```python
m3.async_api.ProtocolErrorInfo(
    *,
    code: int | str | None = None,
    message: str,
    data: m3.observability.Observation[JsonValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `code` | `int \| str \| None` | No | `None` | — | — |
| `message` | `str` | Yes | — | `min_length=1, max_length=4096` | — |
| `data` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `ProtocolKind`

```python
m3.async_api.ProtocolKind(
    *values,
)
```

- `MCP` = `'mcp'`
- `ACP` = `'acp'`
- `PROVIDER_HTTP` = `'provider_http'`
- `PROVIDER_STREAM` = `'provider_stream'`

## `ProviderEntry`

```python
m3.async_api.ProviderEntry(
    *,
    entry_id: str,
    kind: Literal['provider'] = 'provider',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    provider: str,
    category: str,
    data: m3.observability.Observation[JsonValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['provider']` | No | `'provider'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `provider` | `str` | Yes | — | `min_length=1, max_length=128` | — |
| `category` | `str` | Yes | — | `min_length=1, max_length=128` | — |
| `data` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |

## `RawEvidence`

```python
m3.async_api.RawEvidence(
    *,
    reference: m3.types.EvidenceRef,
    media_type: str,
    content: JsonValue | str,
    size_bytes: int,
    returned_size_bytes: int,
    truncated: bool = False,
    redacted: Literal[True] = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `reference` | `m3.types.EvidenceRef` | Yes | — | — | — |
| `media_type` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `content` | `JsonValue \| str` | Yes | — | — | — |
| `size_bytes` | `int` | Yes | — | `ge=0` | — |
| `returned_size_bytes` | `int` | Yes | — | `ge=0` | — |
| `truncated` | `bool` | No | `False` | — | — |
| `redacted` | `Literal[True]` | No | `True` | — | — |

## `RawEvidenceSource`

```python
m3.async_api.RawEvidenceSource(
    *values,
)
```

- `MCP` = `'mcp'`
- `ACP` = `'acp'`
- `OPENCODE` = `'opencode'`
- `CLAUDE_CODE` = `'claude_code'`
- `PROCESS_STDERR` = `'process_stderr'`

## `RawMessageEntry`

```python
m3.async_api.RawMessageEntry(
    *,
    entry_id: str,
    kind: Literal['raw_message'] = 'raw_message',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    source: m3.observability.RawEvidenceSource,
    direction: m3.types.EventDirection = EventDirection.INTERNAL,
    media_type: str,
    preview: m3.observability.Observation[JsonValue | str] = ...,
    evidence_ref: m3.types.EvidenceRef | None = None,
    size_bytes: int = 0,
    redacted: Literal[True] = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['raw_message']` | No | `'raw_message'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `source` | `m3.observability.RawEvidenceSource` | Yes | — | — | — |
| `direction` | `m3.types.EventDirection` | No | `EventDirection.INTERNAL ('internal')` | — | — |
| `media_type` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `preview` | `m3.observability.Observation[JsonValue \| str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `evidence_ref` | `m3.types.EvidenceRef \| None` | No | `None` | — | — |
| `size_bytes` | `int` | No | `0` | `ge=0` | — |
| `redacted` | `Literal[True]` | No | `True` | — | — |

## `ReasoningEntry`

```python
m3.async_api.ReasoningEntry(
    *,
    entry_id: str,
    kind: Literal['reasoning'] = 'reasoning',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    block_id: m3.observability.Observation[str] = ...,
    content: m3.observability.Observation[tuple[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, ...]] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['reasoning']` | No | `'reasoning'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `block_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `content` | `m3.observability.Observation[tuple[m3.types.TextContent \| m3.types.FileContent \| m3.types.ImageContent \| m3.types.AudioContent \| m3.types.ResourceLink \| m3.types.OpaqueContent, ...]]` | No | `factory m3.observability._not_emitted()` | `type argument 1: discriminator='kind'` | — |

## `ReportedToolCall`

```python
m3.async_api.ReportedToolCall(
    *,
    provider_call_id: m3.observability.Observation[str] = ...,
    server: m3.observability.Observation[str] = ...,
    tool: m3.observability.Observation[str] = ...,
    arguments: m3.observability.Observation[JsonValue] = ...,
    result: m3.observability.Observation[JsonValue] = ...,
    status: m3.observability.Observation[str] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `provider_call_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `server` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `tool` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `arguments` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `result` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `status` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |

## `RuntimeTraceInfo`

```python
m3.async_api.RuntimeTraceInfo(
    *args,
    **kwargs,
)
```

## `SafeHttpHeader`

```python
m3.async_api.SafeHttpHeader(
    *,
    name: Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id'],
    value: str,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `name` | `Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id']` | Yes | — | — | — |
| `value` | `str` | Yes | — | — | — |

## `ToolCallAttempt`

```python
m3.async_api.ToolCallAttempt(
    *,
    attempt_index: int,
    jsonrpc_id: m3.observability.Observation[int | str] = ...,
    request_state: m3.observability.Observation[str] = ...,
    continuation_state: m3.observability.Observation[str] = ...,
    input_responses: m3.observability.Observation[JsonValue] = ...,
    input_required: bool = False,
    status: m3.observability.ToolCallStatus = ToolCallStatus.INCOMPLETE,
    latency_ms: m3.observability.Observation[float] = ...,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
) -> None
```

One wire-level attempt belonging to a logical tool call.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `attempt_index` | `int` | Yes | — | `ge=0` | — |
| `jsonrpc_id` | `m3.observability.Observation[int \| str]` | No | `factory m3.observability._not_emitted()` | `variant 1: strict=True, variant 2: strict=True` | — |
| `request_state` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `continuation_state` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `input_responses` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `input_required` | `bool` | No | `False` | — | — |
| `status` | `m3.observability.ToolCallStatus` | No | `ToolCallStatus.INCOMPLETE ('incomplete')` | — | — |
| `latency_ms` | `m3.observability.Observation[float]` | No | `factory m3.observability._not_emitted()` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |

## `ToolCallEntry`

```python
m3.async_api.ToolCallEntry(
    *,
    entry_id: str,
    kind: Literal['tool_call'] = 'tool_call',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    call_id: str,
    provider_call_id: m3.observability.Observation[str] = ...,
    server: m3.observability.Observation[str] = ...,
    tool: m3.observability.Observation[str] = ...,
    arguments: m3.observability.Observation[JsonValue] = ...,
    result: m3.observability.Observation[m3.observability.ToolResult] = ...,
    tool_status: m3.observability.ToolCallStatus = ToolCallStatus.INCOMPLETE,
    correlation: m3.observability.CorrelationState = CorrelationState.UNAVAILABLE,
    jsonrpc_id: m3.observability.Observation[int | str] = ...,
    server_latency_ms: m3.observability.Observation[float] = ...,
    policy: m3.observability.Observation[m3.policy.ToolPolicyDecision] = ...,
    reported: m3.observability.Observation[m3.observability.ReportedToolCall] = ...,
    conflicts: tuple[m3.observability.EvidenceConflict, ...] = (),
    attempts: tuple[m3.observability.ToolCallAttempt, ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['tool_call']` | No | `'tool_call'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `call_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `provider_call_id` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `server` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `tool` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |
| `arguments` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `result` | `m3.observability.Observation[m3.observability.ToolResult]` | No | `factory m3.observability._not_emitted()` | — | — |
| `tool_status` | `m3.observability.ToolCallStatus` | No | `ToolCallStatus.INCOMPLETE ('incomplete')` | — | — |
| `correlation` | `m3.observability.CorrelationState` | No | `CorrelationState.UNAVAILABLE ('unavailable')` | — | — |
| `jsonrpc_id` | `m3.observability.Observation[int \| str]` | No | `factory m3.observability._not_emitted()` | `variant 1: strict=True, variant 2: strict=True` | — |
| `server_latency_ms` | `m3.observability.Observation[float]` | No | `factory m3.observability._not_emitted()` | — | — |
| `policy` | `m3.observability.Observation[m3.policy.ToolPolicyDecision]` | No | `factory m3.observability._not_emitted()` | — | — |
| `reported` | `m3.observability.Observation[m3.observability.ReportedToolCall]` | No | `factory m3.observability._not_emitted()` | — | — |
| `conflicts` | `tuple[m3.observability.EvidenceConflict, ...]` | No | `()` | — | — |
| `attempts` | `tuple[m3.observability.ToolCallAttempt, ...]` | No | `()` | — | — |

## `ToolCallStatus`

```python
m3.async_api.ToolCallStatus(
    *values,
)
```

- `SUCCESS` = `'success'`
- `TOOL_ERROR` = `'tool_error'`
- `PROTOCOL_ERROR` = `'protocol_error'`
- `TRANSPORT_ERROR` = `'transport_error'`
- `CANCELLED` = `'cancelled'`
- `TIMED_OUT` = `'timed_out'`
- `INCOMPLETE` = `'incomplete'`

## `ToolResult`

```python
m3.async_api.ToolResult(
    *,
    content: tuple[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, ...] = (),
    structured_content: m3.observability.Observation[JsonValue] = ...,
    is_error: bool = False,
    error: m3.observability.Observation[m3.types.ErrorInfo] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `content` | `tuple[m3.types.TextContent \| m3.types.FileContent \| m3.types.ImageContent \| m3.types.AudioContent \| m3.types.ResourceLink \| m3.types.OpaqueContent, ...]` | No | `()` | `type argument 1: discriminator='kind'` | — |
| `structured_content` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `is_error` | `bool` | No | `False` | — | — |
| `error` | `m3.observability.Observation[m3.types.ErrorInfo]` | No | `factory m3.observability._not_emitted()` | — | — |

## `TraceEntry`

```python
m3.async_api.TraceEntry(
    *args,
    **kwargs,
)
```

## `TraceEntryBase`

```python
m3.async_api.TraceEntryBase(
    *,
    entry_id: str,
    kind: str,
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `str` | Yes | — | `min_length=1, max_length=64` | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |

## `TraceStatus`

```python
m3.async_api.TraceStatus(
    *values,
)
```

- `COMPLETED` = `'completed'`
- `FAILED` = `'failed'`
- `TOOL_ERROR` = `'tool_error'`
- `PROTOCOL_ERROR` = `'protocol_error'`
- `TRANSPORT_ERROR` = `'transport_error'`
- `TIMED_OUT` = `'timed_out'`
- `CANCELLED` = `'cancelled'`
- `INTERRUPTED` = `'interrupted'`
- `INCOMPLETE` = `'incomplete'`
- `UNAVAILABLE` = `'unavailable'`

## `TraceSummary`

```python
m3.async_api.TraceSummary(
    *,
    timing: m3.observability.TraceTiming = ...,
    usage: m3.observability.Observation[m3.observability.UsageValue] = ...,
    turn_count: int = 0,
    message_count: int = 0,
    reasoning_count: int = 0,
    tool_call_count: int = 0,
    successful_tool_call_count: int = 0,
    failed_tool_call_count: int = 0,
    protocol_error_count: int = 0,
    activity_health: m3.types.ActivityHealth = ActivityHealth.NO_CALLS,
    cleanup_status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `usage` | `m3.observability.Observation[m3.observability.UsageValue]` | No | `factory m3.observability._not_emitted()` | — | — |
| `turn_count` | `int` | No | `0` | `ge=0` | — |
| `message_count` | `int` | No | `0` | `ge=0` | — |
| `reasoning_count` | `int` | No | `0` | `ge=0` | — |
| `tool_call_count` | `int` | No | `0` | `ge=0` | — |
| `successful_tool_call_count` | `int` | No | `0` | `ge=0` | — |
| `failed_tool_call_count` | `int` | No | `0` | `ge=0` | — |
| `protocol_error_count` | `int` | No | `0` | `ge=0` | — |
| `activity_health` | `m3.types.ActivityHealth` | No | `ActivityHealth.NO_CALLS ('no_calls')` | — | — |
| `cleanup_status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |

## `TraceTiming`

```python
m3.async_api.TraceTiming(
    *,
    started_at: datetime.datetime = ...,
    finished_at: datetime.datetime | None = None,
    start_offset_ms: float = 0,
    end_offset_ms: float = 0,
    duration_ms: float = 0,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `started_at` | `datetime.datetime` | No | `factory m3.observability.TraceTiming.<lambda>()` | — | — |
| `finished_at` | `datetime.datetime \| None` | No | `None` | — | — |
| `start_offset_ms` | `float` | No | `0` | `ge=0` | — |
| `end_offset_ms` | `float` | No | `0` | `ge=0` | — |
| `duration_ms` | `float` | No | `0` | `ge=0` | — |

## `TraceView`

```python
m3.async_api.TraceView(
    *,
    schema_id: Literal['m3.trace_view'] = 'm3.trace_view',
    schema_version: Literal['2.0'] = '2.0',
    trace_id: m3.types.TraceId,
    execution_id: m3.types.ExecutionId,
    outcome: m3.types.ExecutionOutcome = ExecutionOutcome.COMPLETED,
    completeness: Literal['complete', 'partial'] = 'complete',
    limitations: tuple[str, ...] = (),
    agent: m3.types.AgentIdentity | None = None,
    runtime: m3.observability.DirectTrace | m3.observability.OpenCodeTrace | m3.observability.ClaudeCodeTrace | m3.observability.CodexTrace | m3.observability.PiTrace | m3.observability.ACPTrace = ...,
    summary: m3.observability.TraceSummary = ...,
    timeline: tuple[m3.observability.LifecycleEntry | m3.observability.MessageEntry | m3.observability.ReasoningEntry | m3.observability.ToolCallEntry | m3.observability.ProtocolEntry | m3.observability.TransportEntry | m3.observability.InitializationEntry | m3.observability.UsageEntry | m3.observability.InteractionEntry | m3.observability.ElicitationEntry | m3.observability.ProcessEntry | m3.observability.WorkspaceEntry | m3.observability.ArtifactEntry | m3.observability.EvaluationEntry | m3.observability.DiagnosticEntry | m3.observability.RawMessageEntry | m3.observability.ProviderEntry, ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `schema_id` | `Literal['m3.trace_view']` | No | `'m3.trace_view'` | — | — |
| `schema_version` | `Literal['2.0']` | No | `'2.0'` | — | — |
| `trace_id` | `m3.types.TraceId` | Yes | — | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `outcome` | `m3.types.ExecutionOutcome` | No | `ExecutionOutcome.COMPLETED ('completed')` | — | — |
| `completeness` | `Literal['complete', 'partial']` | No | `'complete'` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `agent` | `m3.types.AgentIdentity \| None` | No | `None` | — | — |
| `runtime` | `m3.observability.DirectTrace \| m3.observability.OpenCodeTrace \| m3.observability.ClaudeCodeTrace \| m3.observability.CodexTrace \| m3.observability.PiTrace \| m3.observability.ACPTrace` | No | `factory m3.observability.DirectTrace()` | `discriminator='kind'` | — |
| `summary` | `m3.observability.TraceSummary` | No | `factory m3.observability.TraceSummary()` | — | — |
| `timeline` | `tuple[m3.observability.LifecycleEntry \| m3.observability.MessageEntry \| m3.observability.ReasoningEntry \| m3.observability.ToolCallEntry \| m3.observability.ProtocolEntry \| m3.observability.TransportEntry \| m3.observability.InitializationEntry \| m3.observability.UsageEntry \| m3.observability.InteractionEntry \| m3.observability.ElicitationEntry \| m3.observability.ProcessEntry \| m3.observability.WorkspaceEntry \| m3.observability.ArtifactEntry \| m3.observability.EvaluationEntry \| m3.observability.DiagnosticEntry \| m3.observability.RawMessageEntry \| m3.observability.ProviderEntry, ...]` | No | `()` | `type argument 1: discriminator='kind'` | — |
- `tool_calls` (property)
- `messages` (property)
- `reasoning` (property)
- `protocol` (property)
- `transports` (property)
- `raw_messages` (property)
- `interactions` (property)
- `elicitations` (property): Return keyed elicitation interactions correlated to MRTR rounds.
- `processes` (property)
- `diagnostics` (property)

```python
for_turn(
    self,
    turn: _TurnResult | _TurnState | _TurnId | str,
) -> TraceView
```
Return the finalized evidence belonging to one turn.

```python
for_session(
    self,
    session_id: _SessionId | str,
) -> TraceView
```

```python
for_server(
    self,
    server_binding: str,
) -> TraceView
```

```python
between(
    self,
    start_offset_ms: float,
    end_offset_ms: float,
) -> TraceView
```

## `TransportEntry`

```python
m3.async_api.TransportEntry(
    *,
    entry_id: str,
    kind: Literal['transport'] = 'transport',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    phase: Literal['connected', 'disconnected'],
    configured: m3.observability.Observation[m3.types.TransportKind] = ...,
    instrumented: m3.observability.Observation[m3.types.TransportKind] = ...,
) -> None
```

A stable MCP transport lifecycle observation.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['transport']` | No | `'transport'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `phase` | `Literal['connected', 'disconnected']` | Yes | — | — | — |
| `configured` | `m3.observability.Observation[m3.types.TransportKind]` | No | `factory m3.observability._not_emitted()` | — | — |
| `instrumented` | `m3.observability.Observation[m3.types.TransportKind]` | No | `factory m3.observability._not_emitted()` | — | — |

## `UsageEntry`

```python
m3.async_api.UsageEntry(
    *,
    entry_id: str,
    kind: Literal['usage'] = 'usage',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    input_tokens: m3.observability.Observation[int] = ...,
    output_tokens: m3.observability.Observation[int] = ...,
    reasoning_tokens: m3.observability.Observation[int] = ...,
    cache_creation_tokens: m3.observability.Observation[int] = ...,
    cache_read_tokens: m3.observability.Observation[int] = ...,
    cache_write_tokens: m3.observability.Observation[int] = ...,
    total_tokens: m3.observability.Observation[int] = ...,
    cost: m3.observability.Observation[float] = ...,
    currency: m3.observability.Observation[str] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['usage']` | No | `'usage'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `input_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `output_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `reasoning_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cache_creation_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cache_read_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cache_write_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `total_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cost` | `m3.observability.Observation[float]` | No | `factory m3.observability._not_emitted()` | — | — |
| `currency` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |

## `UsageValue`

```python
m3.async_api.UsageValue(
    *,
    input_tokens: m3.observability.Observation[int] = ...,
    output_tokens: m3.observability.Observation[int] = ...,
    reasoning_tokens: m3.observability.Observation[int] = ...,
    cache_creation_tokens: m3.observability.Observation[int] = ...,
    cache_read_tokens: m3.observability.Observation[int] = ...,
    cache_write_tokens: m3.observability.Observation[int] = ...,
    total_tokens: m3.observability.Observation[int] = ...,
    cost: m3.observability.Observation[float] = ...,
    currency: m3.observability.Observation[str] = ...,
) -> None
```

Value-only usage aggregate used by summaries and runtime metadata.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `input_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `output_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `reasoning_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cache_creation_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cache_read_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cache_write_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `total_tokens` | `m3.observability.Observation[int]` | No | `factory m3.observability._not_emitted()` | — | — |
| `cost` | `m3.observability.Observation[float]` | No | `factory m3.observability._not_emitted()` | — | — |
| `currency` | `m3.observability.Observation[str]` | No | `factory m3.observability._not_emitted()` | — | — |

## `WorkspaceEntry`

```python
m3.async_api.WorkspaceEntry(
    *,
    entry_id: str,
    kind: Literal['workspace'] = 'workspace',
    parent_id: str | None = None,
    execution_id: m3.types.ExecutionId,
    session_id: m3.types.SessionId | None = None,
    turn_id: m3.types.TurnId | None = None,
    server_binding: str | None = None,
    connection_id: m3.types.ConnectionId | None = None,
    sequence_start: int,
    sequence_end: int,
    timing: m3.observability.TraceTiming = ...,
    status: m3.observability.TraceStatus = TraceStatus.COMPLETED,
    provenance: tuple[m3.types.EventSource, ...] = (),
    limitations: tuple[str, ...] = (),
    change: m3.observability.Observation[JsonValue] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `entry_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `kind` | `Literal['workspace']` | No | `'workspace'` | — | — |
| `parent_id` | `str \| None` | No | `None` | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `session_id` | `m3.types.SessionId \| None` | No | `None` | — | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `server_binding` | `str \| None` | No | `None` | — | — |
| `connection_id` | `m3.types.ConnectionId \| None` | No | `None` | — | — |
| `sequence_start` | `int` | Yes | — | `ge=0` | — |
| `sequence_end` | `int` | Yes | — | `ge=0` | — |
| `timing` | `m3.observability.TraceTiming` | No | `factory m3.observability.TraceTiming()` | — | — |
| `status` | `m3.observability.TraceStatus` | No | `TraceStatus.COMPLETED ('completed')` | — | — |
| `provenance` | `tuple[m3.types.EventSource, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `change` | `m3.observability.Observation[JsonValue]` | No | `factory m3.observability._not_emitted()` | — | — |
