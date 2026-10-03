---
title: "m3.types"
description: "Public Python API reference for m3.types."
---

# `m3.types`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `EVENT_SCHEMA_ID`

`m3.types.EVENT_SCHEMA_ID`

## `EVENT_SCHEMA_VERSION`

`m3.types.EVENT_SCHEMA_VERSION`

## `ExecutionOutcome`

```python
m3.types.ExecutionOutcome(
    *values,
)
```

- `COMPLETED` = `'completed'`
- `FAILED` = `'failed'`
- `TIMED_OUT` = `'timed_out'`
- `CANCELLED` = `'cancelled'`
- `INTERRUPTED` = `'interrupted'`

## `ExecutionResult`

```python
m3.types.ExecutionResult(
    *,
    snapshot: m3.types.ExecutionState,
    turns: tuple[m3.types.TurnResult, ...] = (),
    trace: m3.types.TraceResult | None = None,
    direct_result: m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult | None = None,
    evaluations: tuple[m3.types.EvaluationResult, ...] = (),
    artifacts: tuple[m3.types.ArtifactRef, ...] = (),
    activity_health: m3.types.ActivityHealth = ActivityHealth.NO_CALLS,
    error: m3.types.ErrorInfo | None = None,
    provenance: m3.types.SessionSource | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `snapshot` | `m3.types.ExecutionState` | Yes | — | — | — |
| `turns` | `tuple[m3.types.TurnResult, ...]` | No | `()` | — | — |
| `trace` | `m3.types.TraceResult \| None` | No | `None` | — | — |
| `direct_result` | `m3.types.ListToolsResult \| m3.types.ListResourcesResult \| m3.types.ListTemplatesResult \| m3.types.ListPromptsResult \| m3.types.CallToolResult \| m3.types.ReadResourceResult \| m3.types.GetPromptResult \| m3.types.PingResult \| None` | No | `None` | `variant 1: discriminator='kind'` | — |
| `evaluations` | `tuple[m3.types.EvaluationResult, ...]` | No | `()` | — | — |
| `artifacts` | `tuple[m3.types.ArtifactRef, ...]` | No | `()` | — | — |
| `activity_health` | `m3.types.ActivityHealth` | No | `ActivityHealth.NO_CALLS ('no_calls')` | — | — |
| `error` | `m3.types.ErrorInfo \| None` | No | `None` | — | — |
| `provenance` | `m3.types.SessionSource \| None` | No | `None` | — | — |
- `trace_view` (property): Return the finalized typed view for this execution trace.

## `TurnOutcome`

```python
m3.types.TurnOutcome(
    *values,
)
```

- `COMPLETED` = `'completed'`
- `FAILED` = `'failed'`
- `TIMED_OUT` = `'timed_out'`
- `CANCELLED` = `'cancelled'`
- `INTERRUPTED` = `'interrupted'`

## `TurnResult`

```python
m3.types.TurnResult(
    *,
    snapshot: m3.types.TurnState,
    response: m3.types.TurnResponse | None = None,
    error: m3.types.ErrorInfo | None = None,
    trace: m3.types.TraceResult | None = None,
    evidence: collections.abc.Mapping[str, Any] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `snapshot` | `m3.types.TurnState` | Yes | — | — | — |
| `response` | `m3.types.TurnResponse \| None` | No | `None` | — | — |
| `error` | `m3.types.ErrorInfo \| None` | No | `None` | — | — |
| `trace` | `m3.types.TraceResult \| None` | No | `None` | — | — |
| `evidence` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |
- `turn_id` (property): Stable identifier usable to scope finalized session assertions.

## `HTTPServer`

```python
m3.types.HTTPServer(
    *,
    name: str,
    trust: m3.types.TrustLevel = TrustLevel.UNTRUSTED,
    kind: Literal['streamable_http'] = 'streamable_http',
    url: str,
    headers: collections.abc.Mapping[str, m3.types.SecretReference | str] = ...,
    loopback_only: bool = False,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `trust` | `m3.types.TrustLevel` | No | `TrustLevel.UNTRUSTED ('untrusted')` | — | — |
| `kind` | `Literal['streamable_http']` | No | `'streamable_http'` | — | — |
| `url` | `str` | Yes | — | `min_length=1` | — |
| `headers` | `collections.abc.Mapping[str, m3.types.SecretReference \| str]` | No | `factory builtins.dict()` | — | — |
| `loopback_only` | `bool` | No | `False` | — | — |

## `StdioServer`

```python
m3.types.StdioServer(
    *,
    name: str,
    trust: m3.types.TrustLevel = TrustLevel.UNTRUSTED,
    kind: Literal['stdio'] = 'stdio',
    command: str,
    args: tuple[str, ...] = (),
    environment: collections.abc.Mapping[str, m3.types.SecretReference | str] = ...,
    cwd: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `trust` | `m3.types.TrustLevel` | No | `TrustLevel.UNTRUSTED ('untrusted')` | — | — |
| `kind` | `Literal['stdio']` | No | `'stdio'` | — | — |
| `command` | `str` | Yes | — | `min_length=1` | — |
| `args` | `tuple[str, ...]` | No | `()` | — | — |
| `environment` | `collections.abc.Mapping[str, m3.types.SecretReference \| str]` | No | `factory builtins.dict()` | — | — |
| `cwd` | `str \| None` | No | `None` | — | — |

## `InProcessServer`

```python
m3.types.InProcessServer(
    *,
    name: str,
    trust: m3.types.TrustLevel = TrustLevel.SDK_LOOPBACK,
    kind: Literal['in_process'] = 'in_process',
    factory: Any,
    descriptor: collections.abc.Mapping[str, Any] = ...,
    origin: str = 'python_registration',
) -> None
```

Runtime-only server descriptor; the factory is excluded from serialization.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `trust` | `m3.types.TrustLevel` | No | `TrustLevel.SDK_LOOPBACK ('sdk_loopback')` | — | — |
| `kind` | `Literal['in_process']` | No | `'in_process'` | — | — |
| `factory` | `Any` | Yes | — | — | — |
| `descriptor` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |
| `origin` | `str` | No | `'python_registration'` | `min_length=1, max_length=256` | — |

## `SecretReference`

```python
m3.types.SecretReference(
    *,
    source: Literal['environment', 'provider'],
    name: str,
) -> None
```

Reference to a secret; resolved values are deliberately not modelled.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `source` | `Literal['environment', 'provider']` | Yes | — | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |

## `ServerBinding`

```python
m3.types.ServerBinding(
    *,
    server: m3.types.StdioServer | m3.types.HTTPServer | m3.types.InProcessServer | None = None,
    profile: m3.types.ServerProfileRef | None = None,
    alias: str | None = None,
    required: bool = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `m3.types.StdioServer \| m3.types.HTTPServer \| m3.types.InProcessServer \| None` | No | `None` | `variant 1: discriminator='kind'` | — |
| `profile` | `m3.types.ServerProfileRef \| None` | No | `None` | — | — |
| `alias` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `required` | `bool` | No | `True` | — | — |

## `ServerValue`

```python
m3.types.ServerValue(
    *args,
    **kwargs,
)
```

## `ToolPolicy`

```python
m3.types.ToolPolicy(
    *args,
    **kwargs,
)
```

## `UserMessage`

```python
m3.types.UserMessage(
    *,
    content: tuple[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, ...],
    metadata: collections.abc.Mapping[str, Any] = ...,
) -> None
```

Typed user message; a string is accepted as text shorthand.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `content` | `tuple[m3.types.TextContent \| m3.types.FileContent \| m3.types.ImageContent \| m3.types.AudioContent \| m3.types.ResourceLink \| m3.types.OpaqueContent, ...]` | Yes | — | `type argument 1: discriminator='kind'` | — |
| `metadata` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |

## `TextContent`

```python
m3.types.TextContent(
    *,
    kind: Literal['text'] = 'text',
    text: str,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `Literal['text']` | No | `'text'` | — | — |
| `text` | `str` | Yes | — | — | — |

## `DirectSpec`

```python
m3.types.DirectSpec(
    *,
    run_id: m3.types.RunId | None = None,
    project_id: m3.types.ProjectId | None = None,
    project_name: str | None = None,
    suite_name: str | None = None,
    case_id: str | None = None,
    servers: tuple[m3.types.ServerBinding, ...] = (),
    protocol: m3.types.ProtocolConstraint = ...,
    timeout_seconds: float | None = None,
    goal: str | None = None,
    evaluations: tuple[m3.types.EvaluationRegistration, ...] = (),
    artifact_policy: m3.types.ArtifactPolicy = ArtifactPolicy.FAILED,
    declared_artifacts: tuple[str, ...] = (),
    workspace: m3.types.WorkspacePolicy = ...,
    tool_policy: m3.types.RestrictiveToolPolicy | m3.types.FullToolPolicy | m3.types.NativeToolPolicy = ...,
    permission_policy: m3.types.PermissionPolicy = ...,
    sampling_policy: m3.types.SamplingPolicy = ...,
    filesystem_policy: m3.types.FilesystemPolicy = ...,
    terminal_policy: m3.types.TerminalPolicy = ...,
    metadata: collections.abc.Mapping[str, str | int | float | bool | None] = ...,
    kind: Literal['direct'] = 'direct',
    operation: m3.types.ListTools | m3.types.ListResources | m3.types.ListTemplates | m3.types.ListPrompts | m3.types.CallTool | m3.types.ReadResource | m3.types.GetPrompt | m3.types.Ping,
    validate_schemas: bool = False,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `run_id` | `m3.types.RunId \| None` | No | `None` | — | — |
| `project_id` | `m3.types.ProjectId \| None` | No | `None` | — | — |
| `project_name` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `suite_name` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `case_id` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `servers` | `tuple[m3.types.ServerBinding, ...]` | No | `()` | — | — |
| `protocol` | `m3.types.ProtocolConstraint` | No | `factory m3.types.ProtocolConstraint()` | — | — |
| `timeout_seconds` | `float \| None` | No | `None` | `gt=0` | — |
| `goal` | `str \| None` | No | `None` | `max_length=32768` | — |
| `evaluations` | `tuple[m3.types.EvaluationRegistration, ...]` | No | `()` | — | — |
| `artifact_policy` | `m3.types.ArtifactPolicy` | No | `ArtifactPolicy.FAILED ('failed')` | — | — |
| `declared_artifacts` | `tuple[str, ...]` | No | `()` | — | — |
| `workspace` | `m3.types.WorkspacePolicy` | No | `factory m3.types.WorkspacePolicy()` | — | — |
| `tool_policy` | `m3.types.RestrictiveToolPolicy \| m3.types.FullToolPolicy \| m3.types.NativeToolPolicy` | No | `factory m3.types.RestrictiveToolPolicy()` | `discriminator='kind'` | — |
| `permission_policy` | `m3.types.PermissionPolicy` | No | `factory m3.types.PermissionPolicy()` | — | — |
| `sampling_policy` | `m3.types.SamplingPolicy` | No | `factory m3.types.SamplingPolicy()` | — | — |
| `filesystem_policy` | `m3.types.FilesystemPolicy` | No | `factory m3.types.FilesystemPolicy()` | — | — |
| `terminal_policy` | `m3.types.TerminalPolicy` | No | `factory m3.types.TerminalPolicy()` | — | — |
| `metadata` | `collections.abc.Mapping[str, str \| int \| float \| bool \| None]` | No | `factory builtins.dict()` | — | — |
| `kind` | `Literal['direct']` | No | `'direct'` | — | — |
| `operation` | `m3.types.ListTools \| m3.types.ListResources \| m3.types.ListTemplates \| m3.types.ListPrompts \| m3.types.CallTool \| m3.types.ReadResource \| m3.types.GetPrompt \| m3.types.Ping` | Yes | — | `discriminator='kind'` | — |
| `validate_schemas` | `bool` | No | `False` | — | — |

## `DirectOperation`

```python
m3.types.DirectOperation(
    *args,
    **kwargs,
)
```

## `CallTool`

```python
m3.types.CallTool(
    *,
    server: str | None = None,
    kind: Literal['call_tool'] = 'call_tool',
    name: str,
    arguments: collections.abc.Mapping[str, Any] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['call_tool']` | No | `'call_tool'` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `arguments` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |

## `ListTools`

```python
m3.types.ListTools(
    *,
    server: str | None = None,
    kind: Literal['list_tools'] = 'list_tools',
    cursor: str | None = None,
    all_pages: bool = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['list_tools']` | No | `'list_tools'` | — | — |
| `cursor` | `str \| None` | No | `None` | `max_length=256` | — |
| `all_pages` | `bool` | No | `True` | — | — |

## `ListResources`

```python
m3.types.ListResources(
    *,
    server: str | None = None,
    kind: Literal['list_resources'] = 'list_resources',
    cursor: str | None = None,
    all_pages: bool = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['list_resources']` | No | `'list_resources'` | — | — |
| `cursor` | `str \| None` | No | `None` | `max_length=256` | — |
| `all_pages` | `bool` | No | `True` | — | — |

## `ListTemplates`

```python
m3.types.ListTemplates(
    *,
    server: str | None = None,
    kind: Literal['list_resource_templates'] = 'list_resource_templates',
    cursor: str | None = None,
    all_pages: bool = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['list_resource_templates']` | No | `'list_resource_templates'` | — | — |
| `cursor` | `str \| None` | No | `None` | `max_length=256` | — |
| `all_pages` | `bool` | No | `True` | — | — |

## `ListPrompts`

```python
m3.types.ListPrompts(
    *,
    server: str | None = None,
    kind: Literal['list_prompts'] = 'list_prompts',
    cursor: str | None = None,
    all_pages: bool = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['list_prompts']` | No | `'list_prompts'` | — | — |
| `cursor` | `str \| None` | No | `None` | `max_length=256` | — |
| `all_pages` | `bool` | No | `True` | — | — |

## `ReadResource`

```python
m3.types.ReadResource(
    *,
    server: str | None = None,
    kind: Literal['read_resource'] = 'read_resource',
    uri: str,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['read_resource']` | No | `'read_resource'` | — | — |
| `uri` | `str` | Yes | — | `min_length=1, max_length=4096` | — |

## `GetPrompt`

```python
m3.types.GetPrompt(
    *,
    server: str | None = None,
    kind: Literal['get_prompt'] = 'get_prompt',
    name: str,
    arguments: collections.abc.Mapping[str, Any] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['get_prompt']` | No | `'get_prompt'` | — | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `arguments` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |

## `Ping`

```python
m3.types.Ping(
    *,
    server: str | None = None,
    kind: Literal['ping'] = 'ping',
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `kind` | `Literal['ping']` | No | `'ping'` | — | — |

## `EvaluationResult`

```python
m3.types.EvaluationResult(
    *,
    evaluation_id: m3.types.EvaluationId,
    name: str,
    status: m3.types.EvaluationStatus,
    required: bool = False,
    message: str | None = None,
    context: m3.types.EvaluationContext | None = None,
    score: float | None = None,
    rationale: str | None = None,
    metrics: collections.abc.Mapping[str, float] = ...,
    provenance: m3.types.EvaluationSource | None = None,
    details: collections.abc.Mapping[str, Any] = ...,
    judge_evidence: m3.types.JudgeEvidence | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `evaluation_id` | `m3.types.EvaluationId` | Yes | — | — | — |
| `name` | `str` | Yes | — | — | — |
| `status` | `m3.types.EvaluationStatus` | Yes | — | — | — |
| `required` | `bool` | No | `False` | — | — |
| `message` | `str \| None` | No | `None` | — | — |
| `context` | `m3.types.EvaluationContext \| None` | No | `None` | — | — |
| `score` | `float \| None` | No | `None` | — | — |
| `rationale` | `str \| None` | No | `None` | — | — |
| `metrics` | `collections.abc.Mapping[str, float]` | No | `factory builtins.dict()` | — | — |
| `provenance` | `m3.types.EvaluationSource \| None` | No | `None` | — | — |
| `details` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |
| `judge_evidence` | `m3.types.JudgeEvidence \| None` | No | `None` | — | — |

## `EvaluationRecord`

```python
m3.types.EvaluationRecord(
    *,
    evaluation_id: m3.types.EvaluationId,
    execution_id: m3.types.ExecutionId,
    suite_id: m3.types.SuiteId | None = None,
    suite_name: str | None = None,
    case_id: str | None = None,
    turn_id: m3.types.TurnId | None = None,
    name: str,
    status: m3.types.EvaluationStatus,
    required: bool = False,
    message: str | None = None,
    score: float | None = None,
    rationale: str | None = None,
    metrics: collections.abc.Mapping[str, float] = ...,
    provenance: m3.types.EvaluationSource | None = None,
    details: collections.abc.Mapping[str, Any] = ...,
    judge_evidence: m3.types.JudgeEvidence | None = None,
    goal: str | None = None,
    metadata: collections.abc.Mapping[str, str | int | float | bool | None] = ...,
    subject_kind: str = 'unknown',
    subject_digest: str | None = None,
    run_id: m3.types.RunId | None = None,
    created_at: datetime.datetime = ...,
) -> None
```

Compact durable evaluation row linked to an execution report.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `evaluation_id` | `m3.types.EvaluationId` | Yes | — | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `suite_id` | `m3.types.SuiteId \| None` | No | `None` | — | — |
| `suite_name` | `str \| None` | No | `None` | — | — |
| `case_id` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `name` | `str` | Yes | — | — | — |
| `status` | `m3.types.EvaluationStatus` | Yes | — | — | — |
| `required` | `bool` | No | `False` | — | — |
| `message` | `str \| None` | No | `None` | — | — |
| `score` | `float \| None` | No | `None` | — | — |
| `rationale` | `str \| None` | No | `None` | — | — |
| `metrics` | `collections.abc.Mapping[str, float]` | No | `factory builtins.dict()` | — | — |
| `provenance` | `m3.types.EvaluationSource \| None` | No | `None` | — | — |
| `details` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |
| `judge_evidence` | `m3.types.JudgeEvidence \| None` | No | `None` | — | — |
| `goal` | `str \| None` | No | `None` | — | — |
| `metadata` | `collections.abc.Mapping[str, str \| int \| float \| bool \| None]` | No | `factory builtins.dict()` | — | — |
| `subject_kind` | `str` | No | `'unknown'` | — | — |
| `subject_digest` | `str \| None` | No | `None` | `pattern='^[0-9a-f]{64}$'` | — |
| `run_id` | `m3.types.RunId \| None` | No | `None` | — | — |
| `created_at` | `datetime.datetime` | No | `factory m3._types.base._utc_now()` | — | — |

## `EvaluationContext`

```python
m3.types.EvaluationContext(
    *,
    subject: Any = None,
    subject_kind: str = 'unknown',
    execution_id: m3.types.ExecutionId | None = None,
    suite_id: m3.types.SuiteId | None = None,
    suite_name: str | None = None,
    case_id: str | None = None,
    turn_id: m3.types.TurnId | None = None,
    goal: str | None = None,
    trace: m3.types.TraceResult | None = None,
    artifacts: tuple[m3.types.ArtifactRef, ...] = (),
    metadata: collections.abc.Mapping[str, str | int | float | bool | None] = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `subject` | `Any` | No | `None` | — | — |
| `subject_kind` | `str` | No | `'unknown'` | — | — |
| `execution_id` | `m3.types.ExecutionId \| None` | No | `None` | — | — |
| `suite_id` | `m3.types.SuiteId \| None` | No | `None` | — | — |
| `suite_name` | `str \| None` | No | `None` | — | — |
| `case_id` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `turn_id` | `m3.types.TurnId \| None` | No | `None` | — | — |
| `goal` | `str \| None` | No | `None` | — | — |
| `trace` | `m3.types.TraceResult \| None` | No | `None` | — | — |
| `artifacts` | `tuple[m3.types.ArtifactRef, ...]` | No | `()` | — | — |
| `metadata` | `collections.abc.Mapping[str, str \| int \| float \| bool \| None]` | No | `factory builtins.dict()` | — | — |

## `EvaluationDecision`

```python
m3.types.EvaluationDecision(
    *,
    status: m3.types.EvaluationStatus,
    score: float | None = None,
    rationale: str | None = None,
    metrics: collections.abc.Mapping[str, float] = ...,
    provenance: m3.types.EvaluationSource | None = None,
    details: collections.abc.Mapping[str, Any] = ...,
    judge_evidence: m3.types.JudgeEvidence | None = None,
) -> None
```

Structured evaluator output, compatible with scalar verdicts.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `status` | `m3.types.EvaluationStatus` | Yes | — | — | — |
| `score` | `float \| None` | No | `None` | — | — |
| `rationale` | `str \| None` | No | `None` | — | — |
| `metrics` | `collections.abc.Mapping[str, float]` | No | `factory builtins.dict()` | — | — |
| `provenance` | `m3.types.EvaluationSource \| None` | No | `None` | — | — |
| `details` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |
| `judge_evidence` | `m3.types.JudgeEvidence \| None` | No | `None` | — | — |

## `EvaluationStatus`

```python
m3.types.EvaluationStatus(
    *values,
)
```

- `PASSED` = `'passed'`
- `FAILED` = `'failed'`
- `INCONCLUSIVE` = `'inconclusive'`
- `ERROR` = `'error'`
- `NOT_RUN` = `'not_run'`

## `EvaluationSource`

```python
m3.types.EvaluationSource(
    *,
    kind: str,
    provider: str | None = None,
    model: str | None = None,
    rubric_id: str | None = None,
    rubric_version: str | None = None,
    config_digest: str | None = None,
) -> None
```

Optional, redaction-safe provenance for a structured judgment.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `str` | Yes | — | `min_length=1, max_length=128` | — |
| `provider` | `str \| None` | No | `None` | `max_length=256` | — |
| `model` | `str \| None` | No | `None` | `max_length=256` | — |
| `rubric_id` | `str \| None` | No | `None` | `max_length=256` | — |
| `rubric_version` | `str \| None` | No | `None` | `max_length=128` | — |
| `config_digest` | `str \| None` | No | `None` | `max_length=256` | — |

## `JudgeEvidence`

```python
m3.types.JudgeEvidence(
    *,
    schema_version: Literal['m3.judge_evidence.v1'] = 'm3.judge_evidence.v1',
    input: str | None = None,
    reference: str | None = None,
    claims: tuple[str, ...] | None = None,
    candidate: str | None = None,
    rubric: str | None = None,
    threshold: float | None = None,
    rubric_digest: str | None = None,
    config_digest: str | None = None,
    truncated: tuple[str, ...] = (),
) -> None
```

Redacted, bounded evidence for auditing one judge verdict.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `schema_version` | `Literal['m3.judge_evidence.v1']` | No | `'m3.judge_evidence.v1'` | — | — |
| `input` | `str \| None` | No | `None` | — | — |
| `reference` | `str \| None` | No | `None` | — | — |
| `claims` | `tuple[str, ...] \| None` | No | `None` | — | — |
| `candidate` | `str \| None` | No | `None` | — | — |
| `rubric` | `str \| None` | No | `None` | — | — |
| `threshold` | `float \| None` | No | `None` | — | — |
| `rubric_digest` | `str \| None` | No | `None` | `max_length=256` | — |
| `config_digest` | `str \| None` | No | `None` | `max_length=256` | — |
| `truncated` | `tuple[str, ...]` | No | `()` | — | — |

## `TraceResult`

```python
m3.types.TraceResult(
    *,
    trace_id: m3.types.TraceId,
    execution_id: m3.types.ExecutionId,
    completeness: Literal['complete', 'partial'] = 'complete',
    highest_sequence: int = 0,
    events: tuple[m3.types.Event, ...] = (),
    limitations: tuple[str, ...] = (),
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `trace_id` | `m3.types.TraceId` | Yes | — | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `completeness` | `Literal['complete', 'partial']` | No | `'complete'` | — | — |
| `highest_sequence` | `int` | No | `0` | `ge=0` | — |
| `events` | `tuple[m3.types.Event, ...]` | No | `()` | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |

```python
view(
    self,
) -> _TraceView
```
Project this finalized stable trace into the typed view.

## `EvidenceRef`

```python
m3.types.EvidenceRef(
    *,
    evidence_id: str,
    sha256: str | None = None,
    size_bytes: int | None = None,
    media_type: str | None = None,
    storage_key: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `evidence_id` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `sha256` | `str \| None` | No | `None` | `pattern='^[0-9a-f]{64}$'` | — |
| `size_bytes` | `int \| None` | No | `None` | `ge=0` | — |
| `media_type` | `str \| None` | No | `None` | `max_length=256` | — |
| `storage_key` | `str \| None` | No | `None` | `min_length=1, max_length=1024` | — |

## `ArtifactRef`

```python
m3.types.ArtifactRef(
    *,
    artifact_id: m3.types.ArtifactId,
    execution_id: m3.types.ExecutionId,
    name: str,
    media_type: str | None = None,
    size_bytes: int,
    sha256: str,
    redacted: Literal[True] = True,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `artifact_id` | `m3.types.ArtifactId` | Yes | — | — | — |
| `execution_id` | `m3.types.ExecutionId` | Yes | — | — | — |
| `name` | `str` | Yes | — | `min_length=1` | — |
| `media_type` | `str \| None` | No | `None` | — | — |
| `size_bytes` | `int` | Yes | — | `ge=0` | — |
| `sha256` | `str` | Yes | — | `pattern='^[0-9a-f]{64}$'` | — |
| `redacted` | `Literal[True]` | No | `True` | — | — |

## `ExecutionReport`

```python
m3.types.ExecutionReport(
    *,
    snapshot: m3.types.ExecutionState,
    agent: m3.types.AgentIdentity | None = None,
    events: tuple[m3.types.Event, ...] = (),
    artifacts: tuple[m3.types.ArtifactRef, ...] = (),
    direct_result: m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult | None = None,
    error: m3.types.ErrorInfo | None = None,
    evidence: m3.types.ExecutionEvidence | None = None,
    turns: tuple[m3.types.TurnResult, ...] = (),
    evaluations: tuple[m3.types.EvaluationRecord, ...] = (),
    event_count: int = 0,
    events_truncated: bool = False,
    next_after_sequence: int | None = None,
    artifact_count: int = 0,
    artifacts_truncated: bool = False,
) -> None
```

Portable evidence that is actually persisted by an execution store.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `snapshot` | `m3.types.ExecutionState` | Yes | — | — | — |
| `agent` | `m3.types.AgentIdentity \| None` | No | `None` | — | — |
| `events` | `tuple[m3.types.Event, ...]` | No | `()` | — | — |
| `artifacts` | `tuple[m3.types.ArtifactRef, ...]` | No | `()` | — | — |
| `direct_result` | `m3.types.ListToolsResult \| m3.types.ListResourcesResult \| m3.types.ListTemplatesResult \| m3.types.ListPromptsResult \| m3.types.CallToolResult \| m3.types.ReadResourceResult \| m3.types.GetPromptResult \| m3.types.PingResult \| None` | No | `None` | `variant 1: discriminator='kind'` | — |
| `error` | `m3.types.ErrorInfo \| None` | No | `None` | — | — |
| `evidence` | `m3.types.ExecutionEvidence \| None` | No | `None` | — | — |
| `turns` | `tuple[m3.types.TurnResult, ...]` | No | `()` | — | — |
| `evaluations` | `tuple[m3.types.EvaluationRecord, ...]` | No | `()` | — | — |
| `event_count` | `int` | No | `0` | `ge=0` | — |
| `events_truncated` | `bool` | No | `False` | — | — |
| `next_after_sequence` | `int \| None` | No | `None` | `ge=-1` | — |
| `artifact_count` | `int` | No | `0` | `ge=0` | — |
| `artifacts_truncated` | `bool` | No | `False` | — | — |

## `ExecutionEvidence`

```python
m3.types.ExecutionEvidence(
    *,
    completeness: Literal['complete', 'partial'],
    limitations: tuple[str, ...] = (),
    reason: str | None = None,
) -> None
```

Typed completeness markers persisted in stable terminal events.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `completeness` | `Literal['complete', 'partial']` | Yes | — | — | — |
| `limitations` | `tuple[str, ...]` | No | `()` | — | — |
| `reason` | `str \| None` | No | `None` | — | — |

## `Capability`

```python
m3.types.Capability(
    *,
    name: str,
    status: m3.types.CapabilityStatus,
    reason: str | None = None,
    detected_version: str | None = None,
    protocol_version: str | None = None,
    transport: m3.types.TransportKind | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `name` | `str` | Yes | — | — | — |
| `status` | `m3.types.CapabilityStatus` | Yes | — | — | — |
| `reason` | `str \| None` | No | `None` | — | — |
| `detected_version` | `str \| None` | No | `None` | — | — |
| `protocol_version` | `str \| None` | No | `None` | — | — |
| `transport` | `m3.types.TransportKind \| None` | No | `None` | — | — |

## `Readiness`

```python
m3.types.Readiness(
    *,
    ready: bool,
    capabilities: tuple[m3.types.Capability, ...] = (),
    reason: str | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `ready` | `bool` | Yes | — | — | — |
| `capabilities` | `tuple[m3.types.Capability, ...]` | No | `()` | — | — |
| `reason` | `str \| None` | No | `None` | — | — |

## `ProtocolConstraint`

```python
m3.types.ProtocolConstraint(
    *,
    revision: str | None = None,
    transport: m3.types.TransportKind | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `revision` | `str \| None` | No | `None` | `min_length=1, max_length=128` | — |
| `transport` | `m3.types.TransportKind \| None` | No | `None` | — | — |

## `WorkspacePolicy`

```python
m3.types.WorkspacePolicy(
    *,
    kind: m3.types.WorkspaceKind = WorkspaceKind.TEMPORARY,
    source: str | None = None,
    acknowledge_risk: bool = False,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `kind` | `m3.types.WorkspaceKind` | No | `WorkspaceKind.TEMPORARY ('temporary')` | — | — |
| `source` | `str \| None` | No | `None` | — | — |
| `acknowledge_risk` | `bool` | No | `False` | — | — |

## `PermissionPolicy`

```python
m3.types.PermissionPolicy(
    *,
    mode: Literal['deny', 'prompt', 'allow'] = 'deny',
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `mode` | `Literal['deny', 'prompt', 'allow']` | No | `'deny'` | — | — |

## `SamplingPolicy`

```python
m3.types.SamplingPolicy(
    *,
    mode: Literal['deny', 'allow'] = 'deny',
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `mode` | `Literal['deny', 'allow']` | No | `'deny'` | — | — |

## `FilesystemPolicy`

```python
m3.types.FilesystemPolicy(
    *,
    mode: Literal['deny', 'read_only', 'read_write'] = 'deny',
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `mode` | `Literal['deny', 'read_only', 'read_write']` | No | `'deny'` | — | — |

## `TerminalPolicy`

```python
m3.types.TerminalPolicy(
    *,
    mode: Literal['deny', 'allow'] = 'deny',
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `mode` | `Literal['deny', 'allow']` | No | `'deny'` | — | — |
