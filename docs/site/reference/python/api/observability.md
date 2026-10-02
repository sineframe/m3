---
title: "m3.observability"
description: "Public Python API reference for m3.observability."
---

# `m3.observability`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `ACPTrace`

```python
m3.observability.ACPTrace(
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
m3.observability.ArtifactEntry(
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
m3.observability.CaptureOptions(
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
m3.observability.ClaudeCodeTrace(
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
m3.observability.CodexTrace(
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
m3.observability.CorrelationState(
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
m3.observability.DiagnosticEntry(
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
m3.observability.DirectTrace(
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
m3.observability.ElicitationEntry(
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
m3.observability.EvaluationEntry(
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
m3.observability.EvidenceCapture(
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
m3.observability.EvidenceConflict(
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
m3.observability.HttpExchange(
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
m3.observability.InitializationEntry(
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
m3.observability.InitializationValue(
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
m3.observability.InteractionEntry(
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
m3.observability.LifecycleEntry(
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
m3.observability.MessageEntry(
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
m3.observability.MessageRole(
    *values,
)
```

- `USER` = `'user'`
- `ASSISTANT` = `'assistant'`
- `SYSTEM` = `'system'`
- `TOOL` = `'tool'`

## `Observation`

```python
m3.observability.Observation(
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
m3.observability.ObservationReason(
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
m3.observability.ObservationState(
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
m3.observability.OpenCodeTrace(
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
m3.observability.PiTrace(
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
m3.observability.ProcessEntry(
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
m3.observability.ProtocolCallAttempt(
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
m3.observability.ProtocolEntry(
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
m3.observability.ProtocolErrorInfo(
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
m3.observability.ProtocolKind(
    *values,
)
```

- `MCP` = `'mcp'`
- `ACP` = `'acp'`
- `PROVIDER_HTTP` = `'provider_http'`
- `PROVIDER_STREAM` = `'provider_stream'`

## `ProviderEntry`

```python
m3.observability.ProviderEntry(
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
m3.observability.RawEvidence(
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
m3.observability.RawEvidenceSource(
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
m3.observability.RawMessageEntry(
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
m3.observability.ReasoningEntry(
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
m3.observability.ReportedToolCall(
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
m3.observability.RuntimeTraceInfo(
    *args,
    **kwargs,
)
```

## `SafeHttpHeader`

```python
m3.observability.SafeHttpHeader(
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
m3.observability.ToolCallAttempt(
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
m3.observability.ToolCallEntry(
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
m3.observability.ToolCallStatus(
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
m3.observability.ToolResult(
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
m3.observability.TraceEntry(
    *args,
    **kwargs,
)
```

## `TraceEntryBase`

```python
m3.observability.TraceEntryBase(
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
m3.observability.TraceStatus(
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
m3.observability.TraceSummary(
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
m3.observability.TraceTiming(
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
m3.observability.TraceView(
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
m3.observability.TransportEntry(
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
m3.observability.UsageEntry(
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
m3.observability.UsageValue(
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
m3.observability.WorkspaceEntry(
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
