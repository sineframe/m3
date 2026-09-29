---
title: "Python API inventory"
description: "Exact signatures, model fields, and public members for the documented release. The capability pages explain how these objects work together."
---

# Python API inventory

The entries below list exact signatures, model fields, and public members for
this release. Use the capability pages to see how the objects work together.

## `m3`

### `__version__`

`m3.__version__`

Installed distribution version.

### `MCPTestKit`

`m3.MCPTestKit(config: 'Config | _Mapping[str, _Any] | None' = None, *, env: '_Mapping[str, str] | None' = None, cwd: 'str | _Path | None' = None, probe_timeout_seconds: 'float' = 5.0, probe_output_limit: 'int' = 65536, store: '_ExecutionStore | None' = None, embedded_worker: 'bool' = True, adapter_registry: '_HarnessAdapterRegistry | None' = None, run_id: '_RunId | str | None' = None, suite_name: 'str | None' = None, project_id: '_ProjectId | str | None' = None, record_checks: 'bool' = False, max_judge_requests: 'int | None' = None, harness_cache_dir: 'str | _Path | None' = None) -&gt; 'None'`

Lifecycle-safe synchronous configuration and capability shell.

Public fields and methods:

- `get_trace(self, execution_id: '_ExecutionId | str') -&gt; '_TraceResult'`: Return the finalized stable trace for an execution.
- `get_trace_view(self, execution_id: '_ExecutionId | str') -&gt; 'TraceView'`: Return the finalized typed trace view for an execution.
- `read_raw_evidence(self, reference: '_EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`: Read bounded, redacted raw evidence by its durable reference.
- `close(self) -&gt; 'None'`: Close the shell; repeated calls are intentionally harmless.
- `capabilities(self, requests: '_Iterable[ProbeRequest]' = ()) -&gt; 'ProbeReport'`: Return the baseline or exactly the explicitly requested probes.
- `register_evaluator(self, name: 'str', evaluator: '_EvaluatorCallable') -&gt; 'None'`: Register an evaluator callback by its serializable name.
- `evaluate(self, subject: '_Any', evaluator: 'str | _EvaluatorCallable', *, required: 'bool' = False, goal: 'str | None' = None, trace: '_Any' = None, artifacts: '_Any' = (), metadata: '_Mapping[str, str | int | float | bool | None] | None' = None, execution_id: '_Any' = None, turn_id: '_Any' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`: Run and persist one evaluation without changing lifecycle.
- `judge_response(self, *, name: 'str', input: 'str', actual: 'str', expected: 'str', judge: '_LLMJudge', required: 'bool' = False, execution_id: '_Any' = None, turn_id: '_Any' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`: Judge one response and persist the result through this kit's runner.
- `evaluation_results(self) -&gt; 'tuple[_EvaluationResult, ...]'`
- `agents(self, selections: '_Any', *, trials: 'int' = 1) -&gt; 'tuple[_Any, ...]'`: Expand ordered agent dictionaries without starting any I/O.
- `run(self, spec: '_DirectSpec | _AgentSpec') -&gt; '_ExecutionResult'`
- `submit(self, spec: '_ExecutionSpec', *, human_input: '_HumanInput' = 'fail') -&gt; 'ExecutionHandle'`
- `direct(self, server: '_ServerValue | _ServerBinding', *, protocol: 'object | None' = None, timeout: 'float | None' = None, validate_schemas: 'bool' = False, secret_resolver: '_Any' = None, bearer_token: '_Any' = None, auth: '_Any' = None, for_agent: 'bool' = False, resolve_host: '_Any' = None, raise_server_exceptions: 'bool' = True, sampling_callback: '_Any' = None, elicitation_callback: '_RemovedElicitationCallback' = &lt;m3.direct_client._RemovedElicitationCallback object&gt;, list_roots_callback: '_Any' = None, logging_callback: '_Any' = None, message_handler: '_Any' = None, client_info: '_Any' = None, log_level: '_Any' = None, sampling_capabilities: '_Any' = None, result_claims: '_Any' = None, extensions: '_Mapping[str, _Mapping[str, _Any]] | None' = None, notification_bindings: '_Iterable[_Any] | None' = None, dispatcher: '_Any' = None, trace_bridge: '_Any' = None, trace_owner: 'bool' = True, workspace_root: 'str | None' = None) -&gt; 'DirectClient'`
- `agent_session(self, spec: '_AgentSpec', *, adapter: '_AgentAdapter | None' = None, runtime_servers: '_Iterable[_Any]' = (), interaction_handlers: 'InteractionHandlers | None' = None, harness_cache_dir: 'str | _Path | None' = None) -&gt; 'AgentSession'`

### `StdioServer`

`m3.StdioServer(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], trust: m3.types.TrustLevel = &lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;, kind: Literal['stdio'] = 'stdio', command: Annotated[str, MinLen(min_length=1)], args: tuple[str, ...] = (), environment: collections.abc.Mapping[str, m3.types.SecretReference | str] = &lt;factory&gt;, cwd: str | None = None) -&gt; None`

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `trust: &lt;enum 'TrustLevel'&gt;` (default: `&lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;`).
- `kind: typing.Literal['stdio']` (default: `'stdio'`).
- `command: &lt;class 'str'&gt;` (required).
- `args: tuple[str, ...]` (default: `()`).
- `environment: collections.abc.Mapping[str, m3.types.SecretReference | str]` (required).
- `cwd: str | None` (default: `None`).

### `HTTPServer`

`m3.HTTPServer(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], trust: m3.types.TrustLevel = &lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;, kind: Literal['streamable_http'] = 'streamable_http', url: Annotated[str, MinLen(min_length=1)], headers: collections.abc.Mapping[str, m3.types.SecretReference | str] = &lt;factory&gt;, loopback_only: bool = False) -&gt; None`

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `trust: &lt;enum 'TrustLevel'&gt;` (default: `&lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;`).
- `kind: typing.Literal['streamable_http']` (default: `'streamable_http'`).
- `url: &lt;class 'str'&gt;` (required).
- `headers: collections.abc.Mapping[str, m3.types.SecretReference | str]` (required).
- `loopback_only: &lt;class 'bool'&gt;` (default: `False`).

### `InProcessServer`

`m3.InProcessServer(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], trust: m3.types.TrustLevel = &lt;TrustLevel.SDK_LOOPBACK: 'sdk_loopback'&gt;, kind: Literal['in_process'] = 'in_process', factory: Any, descriptor: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;, origin: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)] = 'python_registration') -&gt; None`

Runtime-only server descriptor; the factory is excluded from serialization.

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `trust: &lt;enum 'TrustLevel'&gt;` (default: `&lt;TrustLevel.SDK_LOOPBACK: 'sdk_loopback'&gt;`).
- `kind: typing.Literal['in_process']` (default: `'in_process'`).
- `factory: typing.Any` (required).
- `descriptor: collections.abc.Mapping[str, typing.Any]` (required).
- `origin: &lt;class 'str'&gt;` (default: `'python_registration'`).

### `ExecutionResult`

`m3.ExecutionResult(*, snapshot: m3.types.ExecutionState, turns: tuple[m3.types.TurnResult, ...] = (), trace: m3.types.TraceResult | None = None, direct_result: Optional[Annotated[m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]] = None, evaluations: tuple[m3.types.EvaluationResult, ...] = (), artifacts: tuple[m3.types.ArtifactRef, ...] = (), activity_health: m3.types.ActivityHealth = &lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;, error: m3.types.ErrorInfo | None = None, provenance: m3.types.SessionSource | None = None) -&gt; None`

Public fields and methods:

- `snapshot: &lt;class 'm3.types.ExecutionState'&gt;` (required).
- `turns: tuple[m3.types.TurnResult, ...]` (default: `()`).
- `trace: m3.types.TraceResult | None` (default: `None`).
- `direct_result: typing.Optional[typing.Annotated[m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]]` (default: `None`).
- `evaluations: tuple[m3.types.EvaluationResult, ...]` (default: `()`).
- `artifacts: tuple[m3.types.ArtifactRef, ...]` (default: `()`).
- `activity_health: &lt;enum 'ActivityHealth'&gt;` (default: `&lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;`).
- `error: m3.types.ErrorInfo | None` (default: `None`).
- `provenance: m3.types.SessionSource | None` (default: `None`).

### `ExecutionOutcome`

`m3.ExecutionOutcome(*values)`

### `TurnOutcome`

`m3.TurnOutcome(*values)`

### `expect`

`m3.expect(subject: '_SubjectT', *, redaction_config: '_RedactionConfig | None' = None) -&gt; 'Expectation[_SubjectT]'`

### `check`

`m3.check(*, redaction_config: '_RedactionConfig | None' = None) -&gt; 'CheckGroup'`

### `MCPError`

`m3.MCPError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

Base class for expected M3 failures.

## `m3.types`

### `EVENT_SCHEMA_ID`

`m3.types.EVENT_SCHEMA_ID`

str(object='') -&gt; str str(bytes_or_buffer[, encoding[, errors]]) -&gt; str

### `EVENT_SCHEMA_VERSION`

`m3.types.EVENT_SCHEMA_VERSION`

str(object='') -&gt; str str(bytes_or_buffer[, encoding[, errors]]) -&gt; str

### `ExecutionOutcome`

`m3.types.ExecutionOutcome(*values)`

### `ExecutionResult`

`m3.types.ExecutionResult(*, snapshot: m3.types.ExecutionState, turns: tuple[m3.types.TurnResult, ...] = (), trace: m3.types.TraceResult | None = None, direct_result: Optional[Annotated[m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]] = None, evaluations: tuple[m3.types.EvaluationResult, ...] = (), artifacts: tuple[m3.types.ArtifactRef, ...] = (), activity_health: m3.types.ActivityHealth = &lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;, error: m3.types.ErrorInfo | None = None, provenance: m3.types.SessionSource | None = None) -&gt; None`

Public fields and methods:

- `snapshot: &lt;class 'm3.types.ExecutionState'&gt;` (required).
- `turns: tuple[m3.types.TurnResult, ...]` (default: `()`).
- `trace: m3.types.TraceResult | None` (default: `None`).
- `direct_result: typing.Optional[typing.Annotated[m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]]` (default: `None`).
- `evaluations: tuple[m3.types.EvaluationResult, ...]` (default: `()`).
- `artifacts: tuple[m3.types.ArtifactRef, ...]` (default: `()`).
- `activity_health: &lt;enum 'ActivityHealth'&gt;` (default: `&lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;`).
- `error: m3.types.ErrorInfo | None` (default: `None`).
- `provenance: m3.types.SessionSource | None` (default: `None`).

### `TurnOutcome`

`m3.types.TurnOutcome(*values)`

### `TurnResult`

`m3.types.TurnResult(*, snapshot: m3.types.TurnState, response: m3.types.TurnResponse | None = None, error: m3.types.ErrorInfo | None = None, trace: m3.types.TraceResult | None = None, evidence: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `snapshot: &lt;class 'm3.types.TurnState'&gt;` (required).
- `response: m3.types.TurnResponse | None` (default: `None`).
- `error: m3.types.ErrorInfo | None` (default: `None`).
- `trace: m3.types.TraceResult | None` (default: `None`).
- `evidence: collections.abc.Mapping[str, typing.Any]` (required).

### `HTTPServer`

`m3.types.HTTPServer(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], trust: m3.types.TrustLevel = &lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;, kind: Literal['streamable_http'] = 'streamable_http', url: Annotated[str, MinLen(min_length=1)], headers: collections.abc.Mapping[str, m3.types.SecretReference | str] = &lt;factory&gt;, loopback_only: bool = False) -&gt; None`

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `trust: &lt;enum 'TrustLevel'&gt;` (default: `&lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;`).
- `kind: typing.Literal['streamable_http']` (default: `'streamable_http'`).
- `url: &lt;class 'str'&gt;` (required).
- `headers: collections.abc.Mapping[str, m3.types.SecretReference | str]` (required).
- `loopback_only: &lt;class 'bool'&gt;` (default: `False`).

### `StdioServer`

`m3.types.StdioServer(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], trust: m3.types.TrustLevel = &lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;, kind: Literal['stdio'] = 'stdio', command: Annotated[str, MinLen(min_length=1)], args: tuple[str, ...] = (), environment: collections.abc.Mapping[str, m3.types.SecretReference | str] = &lt;factory&gt;, cwd: str | None = None) -&gt; None`

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `trust: &lt;enum 'TrustLevel'&gt;` (default: `&lt;TrustLevel.UNTRUSTED: 'untrusted'&gt;`).
- `kind: typing.Literal['stdio']` (default: `'stdio'`).
- `command: &lt;class 'str'&gt;` (required).
- `args: tuple[str, ...]` (default: `()`).
- `environment: collections.abc.Mapping[str, m3.types.SecretReference | str]` (required).
- `cwd: str | None` (default: `None`).

### `InProcessServer`

`m3.types.InProcessServer(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], trust: m3.types.TrustLevel = &lt;TrustLevel.SDK_LOOPBACK: 'sdk_loopback'&gt;, kind: Literal['in_process'] = 'in_process', factory: Any, descriptor: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;, origin: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)] = 'python_registration') -&gt; None`

Runtime-only server descriptor; the factory is excluded from serialization.

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `trust: &lt;enum 'TrustLevel'&gt;` (default: `&lt;TrustLevel.SDK_LOOPBACK: 'sdk_loopback'&gt;`).
- `kind: typing.Literal['in_process']` (default: `'in_process'`).
- `factory: typing.Any` (required).
- `descriptor: collections.abc.Mapping[str, typing.Any]` (required).
- `origin: &lt;class 'str'&gt;` (default: `'python_registration'`).

### `SecretReference`

`m3.types.SecretReference(*, source: Literal['environment', 'provider'], name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)]) -&gt; None`

Reference to a secret; resolved values are deliberately not modelled.

Public fields and methods:

- `source: typing.Literal['environment', 'provider']` (required).
- `name: &lt;class 'str'&gt;` (required).

### `ServerBinding`

`m3.types.ServerBinding(*, server: Optional[Annotated[m3.types.StdioServer | m3.types.HTTPServer | m3.types.InProcessServer, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]] = None, profile: m3.types.ServerProfileRef | None = None, alias: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, required: bool = True) -&gt; None`

Public fields and methods:

- `server: typing.Optional[typing.Annotated[m3.types.StdioServer | m3.types.HTTPServer | m3.types.InProcessServer, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]]` (default: `None`).
- `profile: m3.types.ServerProfileRef | None` (default: `None`).
- `alias: str | None` (default: `None`).
- `required: &lt;class 'bool'&gt;` (default: `True`).

### `ServerValue`

`m3.types.ServerValue(*args, **kwargs)`

Runtime representation of an annotated type.

### `ToolPolicy`

`m3.types.ToolPolicy(*args, **kwargs)`

Runtime representation of an annotated type.

### `UserMessage`

`m3.types.UserMessage(*, content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...], metadata: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Typed user message; a string is accepted as text shorthand.

Public fields and methods:

- `content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (required).
- `metadata: collections.abc.Mapping[str, typing.Any]` (required).

### `TextContent`

`m3.types.TextContent(*, kind: Literal['text'] = 'text', text: str) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['text']` (default: `'text'`).
- `text: &lt;class 'str'&gt;` (required).

### `DirectSpec`

`m3.types.DirectSpec(*, run_id: m3.types.RunId | None = None, project_id: m3.types.ProjectId | None = None, project_name: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, suite_name: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, case_id: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, servers: tuple[m3.types.ServerBinding, ...] = (), protocol: m3.types.ProtocolConstraint = &lt;factory&gt;, timeout_seconds: Annotated[float | None, Gt(gt=0)] = None, goal: Annotated[str | None, MaxLen(max_length=32768)] = None, evaluations: tuple[m3.types.EvaluationRegistration, ...] = (), artifact_policy: m3.types.ArtifactPolicy = &lt;ArtifactPolicy.FAILED: 'failed'&gt;, declared_artifacts: tuple[str, ...] = (), workspace: m3.types.WorkspacePolicy = &lt;factory&gt;, tool_policy: m3.types.RestrictiveToolPolicy | m3.types.FullToolPolicy | m3.types.NativeToolPolicy = &lt;factory&gt;, permission_policy: m3.types.PermissionPolicy = &lt;factory&gt;, sampling_policy: m3.types.SamplingPolicy = &lt;factory&gt;, filesystem_policy: m3.types.FilesystemPolicy = &lt;factory&gt;, terminal_policy: m3.types.TerminalPolicy = &lt;factory&gt;, metadata: collections.abc.Mapping[str, str | int | float | bool | None] = &lt;factory&gt;, kind: Literal['direct'] = 'direct', operation: m3.types.ListTools | m3.types.ListResources | m3.types.ListTemplates | m3.types.ListPrompts | m3.types.CallTool | m3.types.ReadResource | m3.types.GetPrompt | m3.types.Ping, validate_schemas: bool = False) -&gt; None`

Public fields and methods:

- `run_id: m3.types.RunId | None` (default: `None`).
- `project_id: m3.types.ProjectId | None` (default: `None`).
- `project_name: str | None` (default: `None`).
- `suite_name: str | None` (default: `None`).
- `case_id: str | None` (default: `None`).
- `servers: tuple[m3.types.ServerBinding, ...]` (default: `()`).
- `protocol: &lt;class 'm3.types.ProtocolConstraint'&gt;` (required).
- `timeout_seconds: float | None` (default: `None`).
- `goal: str | None` (default: `None`).
- `evaluations: tuple[m3.types.EvaluationRegistration, ...]` (default: `()`).
- `artifact_policy: &lt;enum 'ArtifactPolicy'&gt;` (default: `&lt;ArtifactPolicy.FAILED: 'failed'&gt;`).
- `declared_artifacts: tuple[str, ...]` (default: `()`).
- `workspace: &lt;class 'm3.types.WorkspacePolicy'&gt;` (required).
- `tool_policy: m3.types.RestrictiveToolPolicy | m3.types.FullToolPolicy | m3.types.NativeToolPolicy` (required).
- `permission_policy: &lt;class 'm3.types.PermissionPolicy'&gt;` (required).
- `sampling_policy: &lt;class 'm3.types.SamplingPolicy'&gt;` (required).
- `filesystem_policy: &lt;class 'm3.types.FilesystemPolicy'&gt;` (required).
- `terminal_policy: &lt;class 'm3.types.TerminalPolicy'&gt;` (required).
- `metadata: collections.abc.Mapping[str, str | int | float | bool | None]` (required).
- `kind: typing.Literal['direct']` (default: `'direct'`).
- `operation: m3.types.ListTools | m3.types.ListResources | m3.types.ListTemplates | m3.types.ListPrompts | m3.types.CallTool | m3.types.ReadResource | m3.types.GetPrompt | m3.types.Ping` (required).
- `validate_schemas: &lt;class 'bool'&gt;` (default: `False`).

### `DirectOperation`

`m3.types.DirectOperation(*args, **kwargs)`

Runtime representation of an annotated type.

### `CallTool`

`m3.types.CallTool(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['call_tool'] = 'call_tool', name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], arguments: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['call_tool']` (default: `'call_tool'`).
- `name: &lt;class 'str'&gt;` (required).
- `arguments: collections.abc.Mapping[str, typing.Any]` (required).

### `ListTools`

`m3.types.ListTools(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['list_tools'] = 'list_tools', cursor: Annotated[str | None, MaxLen(max_length=256)] = None, all_pages: bool = True) -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['list_tools']` (default: `'list_tools'`).
- `cursor: str | None` (default: `None`).
- `all_pages: &lt;class 'bool'&gt;` (default: `True`).

### `ListResources`

`m3.types.ListResources(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['list_resources'] = 'list_resources', cursor: Annotated[str | None, MaxLen(max_length=256)] = None, all_pages: bool = True) -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['list_resources']` (default: `'list_resources'`).
- `cursor: str | None` (default: `None`).
- `all_pages: &lt;class 'bool'&gt;` (default: `True`).

### `ListTemplates`

`m3.types.ListTemplates(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['list_resource_templates'] = 'list_resource_templates', cursor: Annotated[str | None, MaxLen(max_length=256)] = None, all_pages: bool = True) -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['list_resource_templates']` (default: `'list_resource_templates'`).
- `cursor: str | None` (default: `None`).
- `all_pages: &lt;class 'bool'&gt;` (default: `True`).

### `ListPrompts`

`m3.types.ListPrompts(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['list_prompts'] = 'list_prompts', cursor: Annotated[str | None, MaxLen(max_length=256)] = None, all_pages: bool = True) -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['list_prompts']` (default: `'list_prompts'`).
- `cursor: str | None` (default: `None`).
- `all_pages: &lt;class 'bool'&gt;` (default: `True`).

### `ReadResource`

`m3.types.ReadResource(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['read_resource'] = 'read_resource', uri: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)]) -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['read_resource']` (default: `'read_resource'`).
- `uri: &lt;class 'str'&gt;` (required).

### `GetPrompt`

`m3.types.GetPrompt(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['get_prompt'] = 'get_prompt', name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], arguments: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['get_prompt']` (default: `'get_prompt'`).
- `name: &lt;class 'str'&gt;` (required).
- `arguments: collections.abc.Mapping[str, typing.Any]` (required).

### `Ping`

`m3.types.Ping(*, server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, kind: Literal['ping'] = 'ping') -&gt; None`

Public fields and methods:

- `server: str | None` (default: `None`).
- `kind: typing.Literal['ping']` (default: `'ping'`).

### `EvaluationResult`

`m3.types.EvaluationResult(*, evaluation_id: m3.types.EvaluationId, name: str, status: m3.types.EvaluationStatus, required: bool = False, message: str | None = None, context: m3.types.EvaluationContext | None = None, score: float | None = None, rationale: str | None = None, metrics: collections.abc.Mapping[str, float] = &lt;factory&gt;, provenance: m3.types.EvaluationSource | None = None, details: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `evaluation_id: &lt;class 'm3.types.EvaluationId'&gt;` (required).
- `name: &lt;class 'str'&gt;` (required).
- `status: &lt;enum 'EvaluationStatus'&gt;` (required).
- `required: &lt;class 'bool'&gt;` (default: `False`).
- `message: str | None` (default: `None`).
- `context: m3.types.EvaluationContext | None` (default: `None`).
- `score: float | None` (default: `None`).
- `rationale: str | None` (default: `None`).
- `metrics: collections.abc.Mapping[str, float]` (required).
- `provenance: m3.types.EvaluationSource | None` (default: `None`).
- `details: collections.abc.Mapping[str, typing.Any]` (required).

### `EvaluationRecord`

`m3.types.EvaluationRecord(*, evaluation_id: m3.types.EvaluationId, execution_id: m3.types.ExecutionId, suite_id: m3.types.SuiteId | None = None, suite_name: str | None = None, case_id: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, turn_id: m3.types.TurnId | None = None, name: str, status: m3.types.EvaluationStatus, required: bool = False, message: str | None = None, score: float | None = None, rationale: str | None = None, metrics: collections.abc.Mapping[str, float] = &lt;factory&gt;, provenance: m3.types.EvaluationSource | None = None, details: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;, goal: str | None = None, metadata: collections.abc.Mapping[str, str | int | float | bool | None] = &lt;factory&gt;, subject_kind: str = 'unknown', subject_digest: Annotated[str | None, _PydanticGeneralMetadata(pattern='^[0-9a-f]{64}$')] = None, run_id: m3.types.RunId | None = None, created_at: datetime.datetime = &lt;factory&gt;) -&gt; None`

Compact durable evaluation row linked to an execution report.

Public fields and methods:

- `evaluation_id: &lt;class 'm3.types.EvaluationId'&gt;` (required).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `suite_id: m3.types.SuiteId | None` (default: `None`).
- `suite_name: str | None` (default: `None`).
- `case_id: str | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `status: &lt;enum 'EvaluationStatus'&gt;` (required).
- `required: &lt;class 'bool'&gt;` (default: `False`).
- `message: str | None` (default: `None`).
- `score: float | None` (default: `None`).
- `rationale: str | None` (default: `None`).
- `metrics: collections.abc.Mapping[str, float]` (required).
- `provenance: m3.types.EvaluationSource | None` (default: `None`).
- `details: collections.abc.Mapping[str, typing.Any]` (required).
- `goal: str | None` (default: `None`).
- `metadata: collections.abc.Mapping[str, str | int | float | bool | None]` (required).
- `subject_kind: &lt;class 'str'&gt;` (default: `'unknown'`).
- `subject_digest: str | None` (default: `None`).
- `run_id: m3.types.RunId | None` (default: `None`).
- `created_at: &lt;class 'datetime.datetime'&gt;` (required).

### `EvaluationContext`

`m3.types.EvaluationContext(*, subject: Any = None, subject_kind: str = 'unknown', execution_id: m3.types.ExecutionId | None = None, suite_id: m3.types.SuiteId | None = None, suite_name: str | None = None, case_id: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, turn_id: m3.types.TurnId | None = None, goal: str | None = None, trace: m3.types.TraceResult | None = None, artifacts: tuple[m3.types.ArtifactRef, ...] = (), metadata: collections.abc.Mapping[str, str | int | float | bool | None] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `subject: typing.Any` (default: `None`).
- `subject_kind: &lt;class 'str'&gt;` (default: `'unknown'`).
- `execution_id: m3.types.ExecutionId | None` (default: `None`).
- `suite_id: m3.types.SuiteId | None` (default: `None`).
- `suite_name: str | None` (default: `None`).
- `case_id: str | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `goal: str | None` (default: `None`).
- `trace: m3.types.TraceResult | None` (default: `None`).
- `artifacts: tuple[m3.types.ArtifactRef, ...]` (default: `()`).
- `metadata: collections.abc.Mapping[str, str | int | float | bool | None]` (required).

### `EvaluationDecision`

`m3.types.EvaluationDecision(*, status: m3.types.EvaluationStatus, score: float | None = None, rationale: str | None = None, metrics: collections.abc.Mapping[str, float] = &lt;factory&gt;, provenance: m3.types.EvaluationSource | None = None, details: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Structured evaluator output, compatible with scalar verdicts.

Public fields and methods:

- `status: &lt;enum 'EvaluationStatus'&gt;` (required).
- `score: float | None` (default: `None`).
- `rationale: str | None` (default: `None`).
- `metrics: collections.abc.Mapping[str, float]` (required).
- `provenance: m3.types.EvaluationSource | None` (default: `None`).
- `details: collections.abc.Mapping[str, typing.Any]` (required).

### `EvaluationStatus`

`m3.types.EvaluationStatus(*values)`

### `EvaluationSource`

`m3.types.EvaluationSource(*, kind: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], provider: Annotated[str | None, MaxLen(max_length=256)] = None, model: Annotated[str | None, MaxLen(max_length=256)] = None, rubric_id: Annotated[str | None, MaxLen(max_length=256)] = None, rubric_version: Annotated[str | None, MaxLen(max_length=128)] = None, config_digest: Annotated[str | None, MaxLen(max_length=256)] = None) -&gt; None`

Optional, redaction-safe provenance for a structured judgment.

Public fields and methods:

- `kind: &lt;class 'str'&gt;` (required).
- `provider: str | None` (default: `None`).
- `model: str | None` (default: `None`).
- `rubric_id: str | None` (default: `None`).
- `rubric_version: str | None` (default: `None`).
- `config_digest: str | None` (default: `None`).

### `TraceResult`

`m3.types.TraceResult(*, trace_id: m3.types.TraceId, execution_id: m3.types.ExecutionId, completeness: Literal['complete', 'partial'] = 'complete', highest_sequence: Annotated[int, Ge(ge=0)] = 0, events: tuple[m3.types.Event, ...] = (), limitations: tuple[str, ...] = ()) -&gt; None`

Public fields and methods:

- `trace_id: &lt;class 'm3.types.TraceId'&gt;` (required).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `completeness: typing.Literal['complete', 'partial']` (default: `'complete'`).
- `highest_sequence: &lt;class 'int'&gt;` (default: `0`).
- `events: tuple[m3.types.Event, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `view(self) -&gt; '_TraceView'`: Project this finalized stable trace into the typed view.

### `EvidenceRef`

`m3.types.EvidenceRef(*, evidence_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], sha256: Annotated[str | None, _PydanticGeneralMetadata(pattern='^[0-9a-f]{64}$')] = None, size_bytes: Annotated[int | None, Ge(ge=0)] = None, media_type: Annotated[str | None, MaxLen(max_length=256)] = None, storage_key: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=1024)] = None) -&gt; None`

Public fields and methods:

- `evidence_id: &lt;class 'str'&gt;` (required).
- `sha256: str | None` (default: `None`).
- `size_bytes: int | None` (default: `None`).
- `media_type: str | None` (default: `None`).
- `storage_key: str | None` (default: `None`).

### `ArtifactRef`

`m3.types.ArtifactRef(*, artifact_id: m3.types.ArtifactId, execution_id: m3.types.ExecutionId, name: Annotated[str, MinLen(min_length=1)], media_type: str | None = None, size_bytes: Annotated[int, Ge(ge=0)], sha256: Annotated[str, _PydanticGeneralMetadata(pattern='^[0-9a-f]{64}$')], redacted: Literal[True] = True) -&gt; None`

Public fields and methods:

- `artifact_id: &lt;class 'm3.types.ArtifactId'&gt;` (required).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `name: &lt;class 'str'&gt;` (required).
- `media_type: str | None` (default: `None`).
- `size_bytes: &lt;class 'int'&gt;` (required).
- `sha256: &lt;class 'str'&gt;` (required).
- `redacted: typing.Literal[True]` (default: `True`).

### `ExecutionReport`

`m3.types.ExecutionReport(*, snapshot: m3.types.ExecutionState, agent: m3.types.AgentIdentity | None = None, events: tuple[m3.types.Event, ...] = (), artifacts: tuple[m3.types.ArtifactRef, ...] = (), direct_result: Optional[Annotated[m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]] = None, error: m3.types.ErrorInfo | None = None, evidence: m3.types.ExecutionEvidence | None = None, turns: tuple[m3.types.TurnResult, ...] = (), evaluations: tuple[m3.types.EvaluationRecord, ...] = (), event_count: Annotated[int, Ge(ge=0)] = 0, events_truncated: bool = False, next_after_sequence: Annotated[int | None, Ge(ge=-1)] = None, artifact_count: Annotated[int, Ge(ge=0)] = 0, artifacts_truncated: bool = False) -&gt; None`

Portable evidence that is actually persisted by an execution store.

Public fields and methods:

- `snapshot: &lt;class 'm3.types.ExecutionState'&gt;` (required).
- `agent: m3.types.AgentIdentity | None` (default: `None`).
- `events: tuple[m3.types.Event, ...]` (default: `()`).
- `artifacts: tuple[m3.types.ArtifactRef, ...]` (default: `()`).
- `direct_result: typing.Optional[typing.Annotated[m3.types.ListToolsResult | m3.types.ListResourcesResult | m3.types.ListTemplatesResult | m3.types.ListPromptsResult | m3.types.CallToolResult | m3.types.ReadResourceResult | m3.types.GetPromptResult | m3.types.PingResult, FieldInfo(annotation=NoneType, required=True, discriminator='kind')]]` (default: `None`).
- `error: m3.types.ErrorInfo | None` (default: `None`).
- `evidence: m3.types.ExecutionEvidence | None` (default: `None`).
- `turns: tuple[m3.types.TurnResult, ...]` (default: `()`).
- `evaluations: tuple[m3.types.EvaluationRecord, ...]` (default: `()`).
- `event_count: &lt;class 'int'&gt;` (default: `0`).
- `events_truncated: &lt;class 'bool'&gt;` (default: `False`).
- `next_after_sequence: int | None` (default: `None`).
- `artifact_count: &lt;class 'int'&gt;` (default: `0`).
- `artifacts_truncated: &lt;class 'bool'&gt;` (default: `False`).

### `ExecutionEvidence`

`m3.types.ExecutionEvidence(*, completeness: Literal['complete', 'partial'], limitations: tuple[str, ...] = (), reason: str | None = None) -&gt; None`

Typed completeness markers persisted in stable terminal events.

Public fields and methods:

- `completeness: typing.Literal['complete', 'partial']` (required).
- `limitations: tuple[str, ...]` (default: `()`).
- `reason: str | None` (default: `None`).

### `Capability`

`m3.types.Capability(*, name: str, status: m3.types.CapabilityStatus, reason: str | None = None, detected_version: str | None = None, protocol_version: str | None = None, transport: m3.types.TransportKind | None = None) -&gt; None`

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `status: &lt;enum 'CapabilityStatus'&gt;` (required).
- `reason: str | None` (default: `None`).
- `detected_version: str | None` (default: `None`).
- `protocol_version: str | None` (default: `None`).
- `transport: m3.types.TransportKind | None` (default: `None`).

### `Readiness`

`m3.types.Readiness(*, ready: bool, capabilities: tuple[m3.types.Capability, ...] = (), reason: str | None = None) -&gt; None`

Public fields and methods:

- `ready: &lt;class 'bool'&gt;` (required).
- `capabilities: tuple[m3.types.Capability, ...]` (default: `()`).
- `reason: str | None` (default: `None`).

### `ProtocolConstraint`

`m3.types.ProtocolConstraint(*, revision: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=128)] = None, transport: m3.types.TransportKind | None = None) -&gt; None`

Public fields and methods:

- `revision: str | None` (default: `None`).
- `transport: m3.types.TransportKind | None` (default: `None`).

### `WorkspacePolicy`

`m3.types.WorkspacePolicy(*, kind: m3.types.WorkspaceKind = &lt;WorkspaceKind.TEMPORARY: 'temporary'&gt;, source: str | None = None, acknowledge_risk: bool = False) -&gt; None`

Public fields and methods:

- `kind: &lt;enum 'WorkspaceKind'&gt;` (default: `&lt;WorkspaceKind.TEMPORARY: 'temporary'&gt;`).
- `source: str | None` (default: `None`).
- `acknowledge_risk: &lt;class 'bool'&gt;` (default: `False`).

### `PermissionPolicy`

`m3.types.PermissionPolicy(*, mode: Literal['deny', 'prompt', 'allow'] = 'deny') -&gt; None`

Public fields and methods:

- `mode: typing.Literal['deny', 'prompt', 'allow']` (default: `'deny'`).

### `SamplingPolicy`

`m3.types.SamplingPolicy(*, mode: Literal['deny', 'allow'] = 'deny') -&gt; None`

Public fields and methods:

- `mode: typing.Literal['deny', 'allow']` (default: `'deny'`).

### `FilesystemPolicy`

`m3.types.FilesystemPolicy(*, mode: Literal['deny', 'read_only', 'read_write'] = 'deny') -&gt; None`

Public fields and methods:

- `mode: typing.Literal['deny', 'read_only', 'read_write']` (default: `'deny'`).

### `TerminalPolicy`

`m3.types.TerminalPolicy(*, mode: Literal['deny', 'allow'] = 'deny') -&gt; None`

Public fields and methods:

- `mode: typing.Literal['deny', 'allow']` (default: `'deny'`).

## `m3.errors`

### `CleanupError`

`m3.errors.CleanupError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `ElicitationExpectationError`

`m3.errors.ElicitationExpectationError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

An elicitation round did not match the declared response plan.

### `ElicitationRoundLimitError`

`m3.errors.ElicitationRoundLimitError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

An elicitation operation exceeded its configured round limit.

### `ExecutionNotFound`

`m3.errors.ExecutionNotFound(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

The requested execution does not exist in the trace store.

### `InvalidTransitionError`

`m3.errors.InvalidTransitionError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `KitClosed`

`m3.errors.KitClosed(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

An operation was attempted after its owning test kit was closed.

### `MCPError`

`m3.errors.MCPError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

Base class for expected M3 failures.

### `ModelValidationError`

`m3.errors.ModelValidationError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `OperationCancelled`

`m3.errors.OperationCancelled(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `OperationTimeout`

`m3.errors.OperationTimeout(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `RawEvidenceIntegrityError`

`m3.errors.RawEvidenceIntegrityError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

Referenced raw evidence failed its digest or size integrity check.

### `RawEvidenceUnavailable`

`m3.errors.RawEvidenceUnavailable(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

Referenced redacted raw evidence cannot be read.

### `ProtocolError`

`m3.errors.ProtocolError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `SessionBusy`

`m3.errors.SessionBusy(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `SessionStillOpen`

`m3.errors.SessionStillOpen(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `TransportError`

`m3.errors.TransportError(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

### `TraceNotFinalized`

`m3.errors.TraceNotFinalized(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

A typed view was requested before terminal execution evidence arrived.

### `TraceUnavailable`

`m3.errors.TraceUnavailable(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

An execution exists but has no usable trace evidence.

### `UnsupportedFeature`

`m3.errors.UnsupportedFeature(message: 'str', *, details: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

## `m3.sync_api`

### `AgentSession`

`m3.sync_api.AgentSession(portal: '_SyncPortal', spec: '_AgentSpec', adapter: '_AgentAdapter | None' = None, runtime_servers: '_Iterable[_Any]' = (), interaction_handlers: 'InteractionHandlers | None' = None, _handle: 'int | None' = None, harness_cache_dir: 'str | _Path | None' = None) -&gt; 'None'`

Blocking proxy for the async session state machine.

Public fields and methods:

- `send(self, message: 'str | _UserMessage', *, timeout: 'float | None' = None, metadata: 'dict[str, object] | None' = None, elicitation: 'ElicitationPlan | None' = None, elicitation_round_limit: 'int' = 10) -&gt; '_TurnResult'`
- `enqueue_turn(self, message: 'str | _UserMessage', *, timeout: 'float | None' = None, metadata: 'dict[str, object] | None' = None) -&gt; '_Any'`
- `snapshot(self) -&gt; '_ExecutionState'`
- `cancel(self) -&gt; 'None'`
- `fork(self, request: '_SessionForkRequest', *, adapter_factory: '_Callable[..., _Any]') -&gt; 'AgentSession'`
- `close(self) -&gt; 'None'`

### `HarnessAdapter`

`m3.sync_api.HarnessAdapter(*args, **kwargs)`

Minimal injected adapter contract for one continuing conversation.

Public fields and methods:

- `start(self, spec: 'AgentSpec') -&gt; 'None'`
- `send(self, message: 'UserMessage', *, timeout: 'float | None' = None, metadata: 'Mapping[str, object] | None' = None) -&gt; 'TurnResponse | AdapterTurn'`
- `close(self) -&gt; 'None'`

### `Probes`

`m3.sync_api.Probes(*, timeout_seconds: 'float' = 5.0, output_limit: 'int' = 65536) -&gt; 'None'`

Probe only explicitly requested capability targets.

Public fields and methods:

- `probe_binary(self, name: 'str', executable: 'str | os.PathLike[str]', *, args: 'Sequence[str]' = ('--version',), env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None) -&gt; 'ProbeResult'`: Check one explicitly selected executable and record its version.
- `probe_protocol(self, name: 'str', executable: 'str | os.PathLike[str]', *, args: 'Sequence[str]' = ('--protocol-version',), env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None, transport: 'str | TransportKind | None' = None) -&gt; 'ProbeResult'`: Run the caller-selected protocol probe without version allowlists.
- `probe_harness(self, name: 'str', executable: 'str | os.PathLike[str]', *, args: 'Sequence[str]' = ('--version',), env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None, transport: 'str | TransportKind | None' = None) -&gt; 'ProbeResult'`: Probe exactly one harness executable; never select a fallback.
- `probe_transport(self, name: 'str', *, transport: 'str | TransportKind', executable: 'str | os.PathLike[str] | None' = None, args: 'Sequence[str]' = ('--transport-ready',), module: 'str | None' = None, env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None) -&gt; 'ProbeResult'`: Probe a selected transport through an explicit command or module.
- `probe_storage(self, name: 'str' = 'memory', *, module: 'str | None' = None) -&gt; 'ProbeResult'`: Report in-memory storage as ready; check optional storage lazily.
- `probe_requested(self, requests: 'Iterable[ProbeRequest]') -&gt; 'ProbeReport'`: Run only the supplied requests, retaining each independent result.

### `CallToolResult`

`m3.sync_api.CallToolResult(*, raw: Any = None, content: tuple[collections.abc.Mapping[str, typing.Any], ...] = (), structured_content: Any = None, is_error: bool = False) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `content: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).
- `structured_content: typing.Any` (default: `None`).
- `is_error: &lt;class 'bool'&gt;` (default: `False`).

### `CompletionResult`

`m3.sync_api.CompletionResult(*, raw: Any = None, values: tuple[str, ...] = (), total: int | None = None, has_more: bool | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `values: tuple[str, ...]` (default: `()`).
- `total: int | None` (default: `None`).
- `has_more: bool | None` (default: `None`).

### `ConfigOrigin`

`m3.sync_api.ConfigOrigin(*, source: m3.configuration.ConfigSource, origin: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)]) -&gt; None`

Public fields and methods:

- `source: &lt;enum 'ConfigSource'&gt;` (required).
- `origin: &lt;class 'str'&gt;` (required).

### `ConfigSource`

`m3.sync_api.ConfigSource(*values)`

### `Config`

`m3.sync_api.Config(*, artifact_policy: Literal['failed', 'always', 'never'] = 'failed', protocol_revision: Annotated[str, Strict(strict=True)] = 'auto', telemetry_enabled: Annotated[bool, Strict(strict=True)] = False, sources: collections.abc.Mapping[str, m3.configuration.ConfigOrigin] = &lt;factory&gt;) -&gt; None`

Effective SDK-wide settings and the origin of each setting.

Public fields and methods:

- `artifact_policy: typing.Literal['failed', 'always', 'never']` (default: `'failed'`).
- `protocol_revision: &lt;class 'str'&gt;` (default: `'auto'`).
- `telemetry_enabled: &lt;class 'bool'&gt;` (default: `False`).
- `sources: collections.abc.Mapping[str, m3.configuration.ConfigOrigin]` (required).
- `source_for(self, field: 'str') -&gt; 'ConfigOrigin'`

### `ConfigError`

`m3.sync_api.ConfigError(*, field: 'str', origin: 'str', reason: 'str', code: 'str | None' = None) -&gt; 'None'`

A strict, value-free configuration diagnostic.

### `DirectClient`

`m3.sync_api.DirectClient(portal: '_SyncPortal', server: '_ServerValue | _ServerBinding', options: '_Mapping[str, _Any]') -&gt; 'None'`

Synchronous proxy whose async protocol state remains in a portal thread.

Public fields and methods:

- `close(self) -&gt; 'None'`
- `initialize(self) -&gt; 'InitializationResult'`
- `list_tools(self, *, cursor: 'str | None' = None) -&gt; 'ListToolsResult'`
- `list_all_tools(self) -&gt; 'tuple[Tool, ...]'`
- `list_resources(self, *, cursor: 'str | None' = None) -&gt; 'ListResourcesResult'`
- `list_all_resources(self) -&gt; 'tuple[Resource, ...]'`
- `list_resource_templates(self, *, cursor: 'str | None' = None) -&gt; 'ListResourceTemplatesResult'`
- `list_all_resource_templates(self) -&gt; 'tuple[ResourceTemplate, ...]'`
- `list_prompts(self, *, cursor: 'str | None' = None) -&gt; 'ListPromptsResult'`
- `list_all_prompts(self) -&gt; 'tuple[PromptInfo, ...]'`
- `read_resource(self, uri: 'str', *, input_responses: '_Any' = None, request_state: 'str | None' = None, meta: '_Any' = None, allow_input_required: 'bool' = False, elicitation: 'ElicitationPlan | None' = None, elicitation_round_limit: 'int' = 10) -&gt; 'ResourceReadResult | InputRequiredResult'`
- `get_prompt(self, name: 'str', arguments: '_Mapping[str, str] | None' = None, *, input_responses: '_Any' = None, request_state: 'str | None' = None, meta: '_Any' = None, allow_input_required: 'bool' = False, elicitation: 'ElicitationPlan | None' = None, elicitation_round_limit: 'int' = 10) -&gt; 'PromptResult | InputRequiredResult'`
- `call_tool(self, name: 'str', arguments: '_Mapping[str, _Any] | None' = None, *, timeout: 'float | None' = None, progress_callback: '_Any' = None, input_responses: '_Any' = None, request_state: 'str | None' = None, meta: '_Any' = None, allow_input_required: 'bool' = False, allow_claimed: 'bool' = False, elicitation: 'ElicitationPlan | None' = None, elicitation_round_limit: 'int' = 10) -&gt; 'ToolCallResult | InputRequiredResult'`
- `complete(self, reference: '_Any', argument: '_Mapping[str, str]', context_arguments: '_Mapping[str, str] | None' = None) -&gt; 'CompletionResult'`
- `subscribe_resource(self, uri: 'str', *, meta: '_Any' = None) -&gt; 'EmptyResult'`
- `unsubscribe_resource(self, uri: 'str', *, meta: '_Any' = None) -&gt; 'EmptyResult'`
- `ping(self, *, meta: '_Any' = None) -&gt; 'EmptyResult'`
- `set_logging_level(self, level: 'str', *, meta: '_Any' = None) -&gt; 'EmptyResult'`
- `send_progress_notification(self, progress_token: 'str | int', progress: 'float', total: 'float | None' = None, message: 'str | None' = None, *, meta: '_Any' = None) -&gt; 'None'`
- `send_notification(self, notification: '_Any') -&gt; 'None'`
- `send_roots_list_changed(self) -&gt; 'None'`
- `register_callbacks(self, **callbacks: '_Any') -&gt; '_NoReturn'`

### `ExecutionHandle`

`m3.sync_api.ExecutionHandle(portal: '_SyncPortal', identifier: 'int') -&gt; 'None'`

Blocking twin of :class:`AsyncExecutionHandle` with no async leakage.

Public fields and methods:

- `snapshot(self) -&gt; '_ExecutionState'`
- `pending_elicitation(self) -&gt; 'PendingElicitationRound | None'`
- `respond_elicitation(self, round_id: 'str', responses: '_Mapping[str, ElicitationResponse]', *, idempotency_key: 'str') -&gt; 'None'`
- `result(self, timeout: 'float | None' = None) -&gt; '_ExecutionResult'`
- `cancel(self) -&gt; 'None'`
- `events(self, *, after_sequence: 'int' = -1) -&gt; '_Iterator[_Event]'`
- `on_event(self, callback: '_Callable[[_Event], _Any]') -&gt; '_Callable[[], None]'`

### `PromptInfo`

`m3.sync_api.PromptInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, description: str | None = None, arguments: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `description: str | None` (default: `None`).
- `arguments: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `ResourceInfo`

`m3.sync_api.ResourceInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None, size: Annotated[int | None, Ge(ge=0)] = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).
- `size: int | None` (default: `None`).

### `TemplateInfo`

`m3.sync_api.TemplateInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri_template: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri_template: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).

### `ToolInfo`

`m3.sync_api.ToolInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, description: str | None = None, input_schema: collections.abc.Mapping[str, typing.Any] | bool = &lt;factory&gt;, output_schema: collections.abc.Mapping[str, typing.Any] | bool | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `description: str | None` (default: `None`).
- `input_schema: collections.abc.Mapping[str, typing.Any] | bool` (required).
- `output_schema: collections.abc.Mapping[str, typing.Any] | bool | None` (default: `None`).

### `EmptyResult`

`m3.sync_api.EmptyResult(*, raw: Any = None, result_type: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `result_type: str | None` (default: `None`).

### `GetPromptResult`

`m3.sync_api.GetPromptResult(*, raw: Any = None, description: str | None = None, messages: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `description: str | None` (default: `None`).
- `messages: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `InitializationResult`

`m3.sync_api.InitializationResult(*, raw: Any = None, protocol_version: str, server_info: collections.abc.Mapping[str, typing.Any], instructions: str | None = None, capabilities: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `protocol_version: &lt;class 'str'&gt;` (required).
- `server_info: collections.abc.Mapping[str, typing.Any]` (required).
- `instructions: str | None` (default: `None`).
- `capabilities: collections.abc.Mapping[str, typing.Any]` (required).

### `InputRequiredResult`

`m3.sync_api.InputRequiredResult(*, raw: Any = None, result_type: Literal['input_required'] = 'input_required', input_requests: collections.abc.Mapping[str, typing.Any] | None = None, request_state: str | None = None) -&gt; None`

Official MCP interactive result, preserved instead of coercing empty data.

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `result_type: typing.Literal['input_required']` (default: `'input_required'`).
- `input_requests: collections.abc.Mapping[str, typing.Any] | None` (default: `None`).
- `request_state: str | None` (default: `None`).

### `ListPromptsResult`

`m3.sync_api.ListPromptsResult(*, raw: Any = None, prompts: tuple[m3.types.PromptInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `prompts: tuple[m3.types.PromptInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ListResourcesResult`

`m3.sync_api.ListResourcesResult(*, raw: Any = None, resources: tuple[m3.types.ResourceInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resources: tuple[m3.types.ResourceInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ListResourceTemplatesResult`

`m3.sync_api.ListResourceTemplatesResult(*, raw: Any = None, resource_templates: tuple[m3.types.TemplateInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resource_templates: tuple[m3.types.TemplateInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ListToolsResult`

`m3.sync_api.ListToolsResult(*, raw: Any = None, tools: tuple[m3.types.ToolInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `tools: tuple[m3.types.ToolInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `MCPTestKit`

`m3.sync_api.MCPTestKit(config: 'Config | _Mapping[str, _Any] | None' = None, *, env: '_Mapping[str, str] | None' = None, cwd: 'str | _Path | None' = None, probe_timeout_seconds: 'float' = 5.0, probe_output_limit: 'int' = 65536, store: '_ExecutionStore | None' = None, embedded_worker: 'bool' = True, adapter_registry: '_HarnessAdapterRegistry | None' = None, run_id: '_RunId | str | None' = None, suite_name: 'str | None' = None, project_id: '_ProjectId | str | None' = None, record_checks: 'bool' = False, max_judge_requests: 'int | None' = None, harness_cache_dir: 'str | _Path | None' = None) -&gt; 'None'`

Lifecycle-safe synchronous configuration and capability shell.

Public fields and methods:

- `get_trace(self, execution_id: '_ExecutionId | str') -&gt; '_TraceResult'`: Return the finalized stable trace for an execution.
- `get_trace_view(self, execution_id: '_ExecutionId | str') -&gt; 'TraceView'`: Return the finalized typed trace view for an execution.
- `read_raw_evidence(self, reference: '_EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`: Read bounded, redacted raw evidence by its durable reference.
- `close(self) -&gt; 'None'`: Close the shell; repeated calls are intentionally harmless.
- `capabilities(self, requests: '_Iterable[ProbeRequest]' = ()) -&gt; 'ProbeReport'`: Return the baseline or exactly the explicitly requested probes.
- `register_evaluator(self, name: 'str', evaluator: '_EvaluatorCallable') -&gt; 'None'`: Register an evaluator callback by its serializable name.
- `evaluate(self, subject: '_Any', evaluator: 'str | _EvaluatorCallable', *, required: 'bool' = False, goal: 'str | None' = None, trace: '_Any' = None, artifacts: '_Any' = (), metadata: '_Mapping[str, str | int | float | bool | None] | None' = None, execution_id: '_Any' = None, turn_id: '_Any' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`: Run and persist one evaluation without changing lifecycle.
- `judge_response(self, *, name: 'str', input: 'str', actual: 'str', expected: 'str', judge: '_LLMJudge', required: 'bool' = False, execution_id: '_Any' = None, turn_id: '_Any' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`: Judge one response and persist the result through this kit's runner.
- `evaluation_results(self) -&gt; 'tuple[_EvaluationResult, ...]'`
- `agents(self, selections: '_Any', *, trials: 'int' = 1) -&gt; 'tuple[_Any, ...]'`: Expand ordered agent dictionaries without starting any I/O.
- `run(self, spec: '_DirectSpec | _AgentSpec') -&gt; '_ExecutionResult'`
- `submit(self, spec: '_ExecutionSpec', *, human_input: '_HumanInput' = 'fail') -&gt; 'ExecutionHandle'`
- `direct(self, server: '_ServerValue | _ServerBinding', *, protocol: 'object | None' = None, timeout: 'float | None' = None, validate_schemas: 'bool' = False, secret_resolver: '_Any' = None, bearer_token: '_Any' = None, auth: '_Any' = None, for_agent: 'bool' = False, resolve_host: '_Any' = None, raise_server_exceptions: 'bool' = True, sampling_callback: '_Any' = None, elicitation_callback: '_RemovedElicitationCallback' = &lt;m3.direct_client._RemovedElicitationCallback object&gt;, list_roots_callback: '_Any' = None, logging_callback: '_Any' = None, message_handler: '_Any' = None, client_info: '_Any' = None, log_level: '_Any' = None, sampling_capabilities: '_Any' = None, result_claims: '_Any' = None, extensions: '_Mapping[str, _Mapping[str, _Any]] | None' = None, notification_bindings: '_Iterable[_Any] | None' = None, dispatcher: '_Any' = None, trace_bridge: '_Any' = None, trace_owner: 'bool' = True, workspace_root: 'str | None' = None) -&gt; 'DirectClient'`
- `agent_session(self, spec: '_AgentSpec', *, adapter: '_AgentAdapter | None' = None, runtime_servers: '_Iterable[_Any]' = (), interaction_handlers: 'InteractionHandlers | None' = None, harness_cache_dir: 'str | _Path | None' = None) -&gt; 'AgentSession'`

### `ProbeEvidence`

`m3.sync_api.ProbeEvidence(*, kind: m3.services.probes.ProbeKind, target: Annotated[str, MinLen(min_length=1), MaxLen(max_length=512)], command: tuple[str, ...] = (), resolved_executable: str | None = None, detected_version: str | None = None, protocol_version: str | None = None, output: Annotated[str, MaxLen(max_length=65536)] = '', details: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Safe evidence collected by one probe.

Public fields and methods:

- `kind: &lt;enum 'ProbeKind'&gt;` (required).
- `target: &lt;class 'str'&gt;` (required).
- `command: tuple[str, ...]` (default: `()`).
- `resolved_executable: str | None` (default: `None`).
- `detected_version: str | None` (default: `None`).
- `protocol_version: str | None` (default: `None`).
- `output: &lt;class 'str'&gt;` (default: `''`).
- `details: collections.abc.Mapping[str, typing.Any]` (required).

### `ProbeKind`

`m3.sync_api.ProbeKind(*values)`

The independently requestable capability categories.

### `ProbeReport`

`m3.sync_api.ProbeReport(*, readiness: m3.types.Readiness, results: tuple[m3.services.probes.ProbeResult, ...] = ()) -&gt; None`

Aggregate readiness for exactly the requested probes.

Public fields and methods:

- `readiness: &lt;class 'm3.types.Readiness'&gt;` (required).
- `results: tuple[m3.services.probes.ProbeResult, ...]` (default: `()`).
- `result_for(self, name: 'str') -&gt; 'ProbeResult | None'`: Return the result for ``name`` without guessing another target.

### `ProbeRequest`

`m3.sync_api.ProbeRequest(kind: 'ProbeKind', name: 'str', executable: 'str | None' = None, args: 'tuple[str, ...]' = (), env: 'Mapping[str, str] | None' = None, module: 'str | None' = None, transport: 'str | None' = None, timeout_seconds: 'float | None' = None) -&gt; None`

Typed request used by :meth:`Probes.probe_requested`.

### `ProbeResult`

`m3.sync_api.ProbeResult(*, capability: m3.types.Capability, evidence: m3.services.probes.ProbeEvidence) -&gt; None`

One capability result and its separately inspectable evidence.

Public fields and methods:

- `capability: &lt;class 'm3.types.Capability'&gt;` (required).
- `evidence: &lt;class 'm3.services.probes.ProbeEvidence'&gt;` (required).

### `PromptResult`

`m3.sync_api.PromptResult(*, raw: Any = None, description: str | None = None, messages: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `description: str | None` (default: `None`).
- `messages: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `ResourceReadResult`

`m3.sync_api.ResourceReadResult(*, raw: Any = None, contents: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `contents: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `ToolCallResult`

`m3.sync_api.ToolCallResult(*, raw: Any = None, content: tuple[collections.abc.Mapping[str, typing.Any], ...] = (), structured_content: Any = None, is_error: bool = False) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `content: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).
- `structured_content: typing.Any` (default: `None`).
- `is_error: &lt;class 'bool'&gt;` (default: `False`).

### `Tool`

`m3.sync_api.Tool(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, description: str | None = None, input_schema: collections.abc.Mapping[str, typing.Any] | bool = &lt;factory&gt;, output_schema: collections.abc.Mapping[str, typing.Any] | bool | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `description: str | None` (default: `None`).
- `input_schema: collections.abc.Mapping[str, typing.Any] | bool` (required).
- `output_schema: collections.abc.Mapping[str, typing.Any] | bool | None` (default: `None`).

### `Resource`

`m3.sync_api.Resource(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None, size: Annotated[int | None, Ge(ge=0)] = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).
- `size: int | None` (default: `None`).

### `ResourceTemplate`

`m3.sync_api.ResourceTemplate(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri_template: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri_template: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).

### `load_config`

`m3.sync_api.load_config(explicit: '_Mapping[str, _Any] | None' = None, *, env: '_Mapping[str, str] | None' = None, cwd: 'str | _Path | None' = None, artifact_policy: '_Any' = &lt;object object&gt;, protocol_revision: '_Any' = &lt;object object&gt;, telemetry_enabled: '_Any' = &lt;object object&gt;) -&gt; 'Config'`

Resolve SDK settings using explicit, environment, project, default order.

### `AllowedCommands`

`m3.sync_api.AllowedCommands(*, allowed_executables: 'Sequence[str]', root: 'str | Path', environment: 'Mapping[str, str] | None' = None, allowed_environment: 'Sequence[str]' = ()) -&gt; 'None'`

Safe argv-only terminal handler with cwd, timeout, and output bounds.

### `FilesystemHandler`

`m3.sync_api.FilesystemHandler(*args, **kwargs)`

### `FilesystemRequest`

`m3.sync_api.FilesystemRequest(operation: 'FilesystemOperation', path: 'str', data: 'bytes | None' = None, max_bytes: 'int' = 1048576) -&gt; None`

FilesystemRequest(operation: 'FilesystemOperation', path: 'str', data: 'bytes | None' = None, max_bytes: 'int' = 1048576)

### `FilesystemResult`

`m3.sync_api.FilesystemResult(allowed: 'bool', data: 'bytes | tuple[str, ...] | None', receipt: 'InteractionReceipt') -&gt; None`

FilesystemResult(allowed: 'bool', data: 'bytes | tuple[str, ...] | None', receipt: 'InteractionReceipt')

### `Interactions`

`m3.sync_api.Interactions(*, permission_policy: 'PermissionPolicy | None' = None, sampling_policy: 'SamplingPolicy | None' = None, filesystem_policy: 'FilesystemPolicy | None' = None, terminal_policy: 'TerminalPolicy | None' = None, handlers: 'InteractionHandlers | None' = None) -&gt; 'None'`

Apply immutable policies around explicit interaction callbacks.

Public fields and methods:

- `receipts(self) -&gt; 'tuple[InteractionReceipt, ...]'`
- `permission(self, request: 'PermissionRequest') -&gt; 'PermissionResult'`
- `sample(self, request: 'SamplingRequest') -&gt; 'SamplingResult'`
- `filesystem(self, request: 'FilesystemRequest') -&gt; 'FilesystemResult'`
- `terminal(self, request: 'TerminalRequest') -&gt; 'TerminalResult'`

### `InteractionHandlers`

`m3.sync_api.InteractionHandlers(permission: 'PermissionCallback | None' = None, sampling: 'SamplingCallback | None' = None, filesystem: 'FilesystemHandler | None' = None, terminal: 'TerminalHandler | None' = None) -&gt; None`

Optional callbacks; absent callbacks are always default-deny.

### `InteractionReceipt`

`m3.sync_api.InteractionReceipt(request_id: 'str', kind: 'str', decision: 'Decision', reason: 'str', timestamp: 'datetime' = &lt;factory&gt;) -&gt; None`

Safe decision evidence; request values and handler errors are excluded.

### `PermissionRequest`

`m3.sync_api.PermissionRequest(operation: 'str', resource: 'str' = '', destructive: 'bool' = False) -&gt; None`

PermissionRequest(operation: 'str', resource: 'str' = '', destructive: 'bool' = False)

### `PermissionResult`

`m3.sync_api.PermissionResult(allowed: 'bool', receipt: 'InteractionReceipt', confirmation_required: 'bool' = False) -&gt; None`

PermissionResult(allowed: 'bool', receipt: 'InteractionReceipt', confirmation_required: 'bool' = False)

### `PermissionHandler`

`m3.sync_api.PermissionHandler(*args, **kwargs)`

### `SamplingRequest`

`m3.sync_api.SamplingRequest(prompt: 'str', model: 'str | None' = None, metadata: 'Mapping[str, str | int | float | bool | None]' = &lt;factory&gt;) -&gt; None`

SamplingRequest(prompt: 'str', model: 'str | None' = None, metadata: 'Mapping[str, str | int | float | bool | None]' = &lt;factory&gt;)

### `SamplingResult`

`m3.sync_api.SamplingResult(accepted: 'bool', content: 'str | None', receipt: 'InteractionReceipt') -&gt; None`

SamplingResult(accepted: 'bool', content: 'str | None', receipt: 'InteractionReceipt')

### `SamplingHandler`

`m3.sync_api.SamplingHandler(*args, **kwargs)`

### `TerminalHandler`

`m3.sync_api.TerminalHandler(*args, **kwargs)`

### `TerminalRequest`

`m3.sync_api.TerminalRequest(argv: 'tuple[str, ...]', cwd: 'str | None' = None, environment: 'Mapping[str, str]' = &lt;factory&gt;, timeout_seconds: 'float' = 30.0, max_output_bytes: 'int' = 1048576) -&gt; None`

TerminalRequest(argv: 'tuple[str, ...]', cwd: 'str | None' = None, environment: 'Mapping[str, str]' = &lt;factory&gt;, timeout_seconds: 'float' = 30.0, max_output_bytes: 'int' = 1048576)

### `TerminalResult`

`m3.sync_api.TerminalResult(allowed: 'bool', returncode: 'int | None', stdout: 'bytes', stderr: 'bytes', timed_out: 'bool', truncated: 'bool', receipt: 'InteractionReceipt') -&gt; None`

TerminalResult(allowed: 'bool', returncode: 'int | None', stdout: 'bytes', stderr: 'bytes', timed_out: 'bool', truncated: 'bool', receipt: 'InteractionReceipt')

### `WorkspaceFiles`

`m3.sync_api.WorkspaceFiles(root: 'str | Path', *, mode: "Literal['read_only', 'read_write']" = 'read_only', max_bytes: 'int' = 1048576) -&gt; 'None'`

Bounded filesystem handler rooted inside one owned workspace.

### `ElicitationPlan`

`m3.sync_api.ElicitationPlan(*, node: Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of'] = 'leaf', request: Optional[Annotated[m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]] = None, response: m3.elicitation.ElicitationResponse | None = None, children: tuple[m3.elicitation.ElicitationPlan, ...] = (), optional_occurrence: bool = False) -&gt; None`

An immutable, serializable elicitation expectation tree.

Public fields and methods:

- `node: typing.Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of']` (default: `'leaf'`).
- `request: typing.Optional[typing.Annotated[m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]]` (default: `None`).
- `response: m3.elicitation.ElicitationResponse | None` (default: `None`).
- `children: tuple[m3.elicitation.ElicitationPlan, ...]` (default: `()`).
- `optional_occurrence: &lt;class 'bool'&gt;` (default: `False`).
- `accept(self, content: 'Mapping[str, object] | None' = None) -&gt; 'ElicitationPlan'`
- `decline(self) -&gt; 'ElicitationPlan'`
- `cancel(self) -&gt; 'ElicitationPlan'`
- `canonical_identity(self) -&gt; 'str'`
- `canonical_json(self) -&gt; 'str'`
- `model_dump(self, *args: 'Any', **kwargs: 'Any') -&gt; 'dict[str, Any]'`
- `model_dump_json(self, *args: 'Any', **kwargs: 'Any') -&gt; 'str'`
- `matcher(self) -&gt; 'PlanMatcher'`

### `ElicitationResponse`

`m3.sync_api.ElicitationResponse(*, action: Literal['accept', 'decline', 'cancel'], content: collections.abc.Mapping[str, object] | None = None, meta: collections.abc.Mapping[str, object] | None = None) -&gt; None`

The response that will be associated with one request key.

Public fields and methods:

- `action: typing.Literal['accept', 'decline', 'cancel']` (required).
- `content: collections.abc.Mapping[str, object] | None` (default: `None`).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).

### `FormElicitationRequest`

`m3.sync_api.FormElicitationRequest(*, request_key: Annotated[str, MinLen(min_length=1)], mode: Literal['form'] = 'form', message: str, requested_schema: collections.abc.Mapping[str, object], meta: collections.abc.Mapping[str, object] | None = None, task: collections.abc.Mapping[str, object] | None = None, server: str | None = None, operation_kind: Optional[Literal['tool', 'prompt', 'resource']] = None, operation_name: str | None = None) -&gt; None`

A normalized form-mode elicitation request.

Public fields and methods:

- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['form']` (default: `'form'`).
- `message: &lt;class 'str'&gt;` (required).
- `requested_schema: collections.abc.Mapping[str, object]` (required).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).
- `task: collections.abc.Mapping[str, object] | None` (default: `None`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Optional[typing.Literal['tool', 'prompt', 'resource']]` (default: `None`).
- `operation_name: str | None` (default: `None`).

### `PendingElicitationRound`

`m3.sync_api.PendingElicitationRound(*, round_id: Annotated[str, MinLen(min_length=1)], execution_id: Annotated[str, MinLen(min_length=1)], logical_operation_id: Annotated[str, MinLen(min_length=1)], server: Annotated[str, MinLen(min_length=1)], operation_kind: Literal['tool', 'prompt', 'resource'], operation_name: Annotated[str, MinLen(min_length=1)], request_state: str | None = None, requests: collections.abc.Mapping[str, typing.Annotated[m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]], created_at: datetime.datetime, deadline: datetime.datetime | None = None) -&gt; None`

A persisted, keyed set of elicitation requests awaiting responses.

Public fields and methods:

- `round_id: &lt;class 'str'&gt;` (required).
- `execution_id: &lt;class 'str'&gt;` (required).
- `logical_operation_id: &lt;class 'str'&gt;` (required).
- `server: &lt;class 'str'&gt;` (required).
- `operation_kind: typing.Literal['tool', 'prompt', 'resource']` (required).
- `operation_name: &lt;class 'str'&gt;` (required).
- `request_state: str | None` (default: `None`).
- `requests: collections.abc.Mapping[str, typing.Annotated[m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]]` (required).
- `created_at: &lt;class 'datetime.datetime'&gt;` (required).
- `deadline: datetime.datetime | None` (default: `None`).

### `UrlElicitationRequest`

`m3.sync_api.UrlElicitationRequest(*, request_key: Annotated[str, MinLen(min_length=1)], mode: Literal['url'] = 'url', message: str, url: str, elicitation_id: str | None = None, meta: collections.abc.Mapping[str, object] | None = None, task: collections.abc.Mapping[str, object] | None = None, server: str | None = None, operation_kind: Optional[Literal['tool', 'prompt', 'resource']] = None, operation_name: str | None = None) -&gt; None`

A normalized URL-mode elicitation request.

Public fields and methods:

- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['url']` (default: `'url'`).
- `message: &lt;class 'str'&gt;` (required).
- `url: &lt;class 'str'&gt;` (required).
- `elicitation_id: str | None` (default: `None`).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).
- `task: collections.abc.Mapping[str, object] | None` (default: `None`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Optional[typing.Literal['tool', 'prompt', 'resource']]` (default: `None`).
- `operation_name: str | None` (default: `None`).

### `expect_form`

`m3.sync_api.expect_form(request_key: 'str', *, message: 'str | None' = None, schema: 'Mapping[str, object] | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `expect_url`

`m3.sync_api.expect_url(request_key: 'str', *, message: 'str | None' = None, url: 'str | None' = None, elicitation_id: 'str | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `maybe_form`

`m3.sync_api.maybe_form(request_key: 'str', *, message: 'str | None' = None, schema: 'Mapping[str, object] | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `maybe_url`

`m3.sync_api.maybe_url(request_key: 'str', *, message: 'str | None' = None, url: 'str | None' = None, elicitation_id: 'str | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `one_of`

`m3.sync_api.one_of(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `optional`

`m3.sync_api.optional(child: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `round_of`

`m3.sync_api.round_of(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `sequence`

`m3.sync_api.sequence(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `ACPTrace`

`m3.sync_api.ACPTrace(*, kind: Literal['acp'] = 'acp', session_id: m3.observability.Observation[str] = &lt;factory&gt;, protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, agent_identity: m3.observability.Observation[JsonValue] = &lt;factory&gt;, available_modes: m3.observability.Observation[JsonValue] = &lt;factory&gt;, current_mode: m3.observability.Observation[str] = &lt;factory&gt;, config_options: m3.observability.Observation[JsonValue] = &lt;factory&gt;, selected_config: m3.observability.Observation[JsonValue] = &lt;factory&gt;, plan_state_available: m3.observability.Observation[bool] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['acp']` (default: `'acp'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `agent_identity: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `available_modes: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `current_mode: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `config_options: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `selected_config: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `plan_state_available: &lt;class 'm3.observability.Observation[bool]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `ArtifactEntry`

`m3.sync_api.ArtifactEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['artifact'] = 'artifact', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), artifact: m3.types.ArtifactRef) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['artifact']` (default: `'artifact'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `artifact: &lt;class 'm3.types.ArtifactRef'&gt;` (required).

### `CaptureOptions`

`m3.sync_api.CaptureOptions(*, capture_raw_evidence: bool = True, capture_provider_messages: bool = True, capture_stderr: bool = True, raw_preview_bytes: Annotated[int, Gt(gt=0)] = 65536, raw_frame_bytes: Annotated[int, Gt(gt=0)] = 1048576, raw_execution_bytes: Annotated[int, Gt(gt=0)] = 67108864) -&gt; None`

Boundaries for redacted provider/MCP evidence capture.

Public fields and methods:

- `capture_raw_evidence: &lt;class 'bool'&gt;` (default: `True`).
- `capture_provider_messages: &lt;class 'bool'&gt;` (default: `True`).
- `capture_stderr: &lt;class 'bool'&gt;` (default: `True`).
- `raw_preview_bytes: &lt;class 'int'&gt;` (default: `65536`).
- `raw_frame_bytes: &lt;class 'int'&gt;` (default: `1048576`).
- `raw_execution_bytes: &lt;class 'int'&gt;` (default: `67108864`).

### `ClaudeCodeTrace`

`m3.sync_api.ClaudeCodeTrace(*, kind: Literal['claude_code'] = 'claude_code', session_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, result_subtype: m3.observability.Observation[str] = &lt;factory&gt;, stop_reason: m3.observability.Observation[str] = &lt;factory&gt;, service_tier: m3.observability.Observation[str] = &lt;factory&gt;, api_duration_ms: m3.observability.Observation[float] = &lt;factory&gt;, encrypted_reasoning: m3.observability.Observation[bool] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['claude_code']` (default: `'claude_code'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `result_subtype: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `stop_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `service_tier: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `api_duration_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `encrypted_reasoning: &lt;class 'm3.observability.Observation[bool]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `CodexTrace`

`m3.sync_api.CodexTrace(*, kind: Literal['codex'] = 'codex', thread_id: m3.observability.Observation[str] = &lt;factory&gt;, turn_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, sandbox: m3.observability.Observation[str] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['codex']` (default: `'codex'`).
- `thread_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `turn_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `sandbox: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `CorrelationState`

`m3.sync_api.CorrelationState(*values)`

### `DiagnosticEntry`

`m3.sync_api.DiagnosticEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['diagnostic'] = 'diagnostic', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), code: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], message: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], stage: Annotated[str | None, MaxLen(max_length=128)] = None, operation: Annotated[str | None, MaxLen(max_length=256)] = None, elapsed_seconds: Annotated[float | None, Ge(ge=0)] = None, timeout_seconds: Annotated[float | None, Gt(gt=0)] = None) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['diagnostic']` (default: `'diagnostic'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `code: &lt;class 'str'&gt;` (required).
- `message: &lt;class 'str'&gt;` (required).
- `stage: str | None` (default: `None`).
- `operation: str | None` (default: `None`).
- `elapsed_seconds: float | None` (default: `None`).
- `timeout_seconds: float | None` (default: `None`).

### `DirectTrace`

`m3.sync_api.DirectTrace(*, kind: Literal['direct'] = 'direct', transport: m3.observability.Observation[TransportKind] = &lt;factory&gt;, protocol: m3.observability.Observation[str] = &lt;factory&gt;, initialization: m3.observability.Observation[InitializationValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['direct']` (default: `'direct'`).
- `transport: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).
- `protocol: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `initialization: &lt;class 'm3.observability.Observation[InitializationValue]'&gt;` (required).

### `ElicitationEntry`

`m3.sync_api.ElicitationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['elicitation'] = 'elicitation', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, operation_kind: Literal['tool', 'prompt', 'resource'], operation_name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], logical_operation_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], round_index: Annotated[int, Ge(ge=1)], request_key: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], mode: Literal['form', 'url'], message: m3.observability.Observation[str] = &lt;factory&gt;, requested_schema: m3.observability.Observation[JsonValue] = &lt;factory&gt;, url: m3.observability.Observation[str] = &lt;factory&gt;, elicitation_id: m3.observability.Observation[str] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, action: Optional[Literal['accept', 'decline', 'cancel']] = None, content: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

One keyed elicitation embedded in an MRTR input-required round.

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['elicitation']` (default: `'elicitation'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Literal['tool', 'prompt', 'resource']` (required).
- `operation_name: &lt;class 'str'&gt;` (required).
- `logical_operation_id: &lt;class 'str'&gt;` (required).
- `round_index: &lt;class 'int'&gt;` (required).
- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['form', 'url']` (required).
- `message: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `requested_schema: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `url: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `elicitation_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `action: typing.Optional[typing.Literal['accept', 'decline', 'cancel']]` (default: `None`).
- `content: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `EvaluationEntry`

`m3.sync_api.EvaluationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['evaluation'] = 'evaluation', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), evaluation: m3.types.EvaluationResult) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['evaluation']` (default: `'evaluation'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `evaluation: &lt;class 'm3.types.EvaluationResult'&gt;` (required).

### `EvidenceCapture`

`m3.sync_api.EvidenceCapture(*, reference: m3.types.EvidenceRef, preview: m3.observability.Observation[str], original_size_bytes: Annotated[int, Ge(ge=0)], stored_size_bytes: Annotated[int, Ge(ge=0)], redacted: bool, truncated: bool) -&gt; None`

Typed result of bounded, redacted raw-evidence capture.

Public fields and methods:

- `reference: &lt;class 'm3.types.EvidenceRef'&gt;` (required).
- `preview: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `original_size_bytes: &lt;class 'int'&gt;` (required).
- `stored_size_bytes: &lt;class 'int'&gt;` (required).
- `redacted: &lt;class 'bool'&gt;` (required).
- `truncated: &lt;class 'bool'&gt;` (required).

### `EvidenceConflict`

`m3.sync_api.EvidenceConflict(*, field: Literal['server', 'tool', 'arguments', 'result', 'status'], reported: m3.observability.Observation[JsonValue], wire: m3.observability.Observation[JsonValue]) -&gt; None`

Public fields and methods:

- `field: typing.Literal['server', 'tool', 'arguments', 'result', 'status']` (required).
- `reported: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `wire: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `HttpExchange`

`m3.sync_api.HttpExchange(*, method: Annotated[str, MinLen(min_length=1)], status_code: Annotated[int, Ge(ge=100), Le(le=599)], headers: tuple[m3.observability.SafeHttpHeader, ...] = ()) -&gt; None`

Public fields and methods:

- `method: &lt;class 'str'&gt;` (required).
- `status_code: &lt;class 'int'&gt;` (required).
- `headers: tuple[m3.observability.SafeHttpHeader, ...]` (default: `()`).

### `InitializationEntry`

`m3.sync_api.InitializationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['initialization'] = 'initialization', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, server_name: m3.observability.Observation[str] = &lt;factory&gt;, server_version: m3.observability.Observation[str] = &lt;factory&gt;, instructions: m3.observability.Observation[str] = &lt;factory&gt;, capabilities: m3.observability.Observation[JsonValue] = &lt;factory&gt;, tools: m3.observability.Observation[tuple[ToolInfo, ...]] = &lt;factory&gt;, resources: m3.observability.Observation[tuple[ResourceInfo, ...]] = &lt;factory&gt;, resource_templates: m3.observability.Observation[tuple[TemplateInfo, ...]] = &lt;factory&gt;, prompts: m3.observability.Observation[tuple[PromptInfo, ...]] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['initialization']` (default: `'initialization'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `instructions: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `capabilities: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `tools: &lt;class 'm3.observability.Observation[tuple[ToolInfo, ...]]'&gt;` (required).
- `resources: &lt;class 'm3.observability.Observation[tuple[ResourceInfo, ...]]'&gt;` (required).
- `resource_templates: &lt;class 'm3.observability.Observation[tuple[TemplateInfo, ...]]'&gt;` (required).
- `prompts: &lt;class 'm3.observability.Observation[tuple[PromptInfo, ...]]'&gt;` (required).

### `InitializationValue`

`m3.sync_api.InitializationValue(*, protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, server_name: m3.observability.Observation[str] = &lt;factory&gt;, server_version: m3.observability.Observation[str] = &lt;factory&gt;, instructions: m3.observability.Observation[str] = &lt;factory&gt;, capabilities: m3.observability.Observation[JsonValue] = &lt;factory&gt;, tools: m3.observability.Observation[tuple[ToolInfo, ...]] = &lt;factory&gt;, resources: m3.observability.Observation[tuple[ResourceInfo, ...]] = &lt;factory&gt;, resource_templates: m3.observability.Observation[tuple[TemplateInfo, ...]] = &lt;factory&gt;, prompts: m3.observability.Observation[tuple[PromptInfo, ...]] = &lt;factory&gt;) -&gt; None`

Value-only initialization metadata used by runtime information.

Public fields and methods:

- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `instructions: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `capabilities: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `tools: &lt;class 'm3.observability.Observation[tuple[ToolInfo, ...]]'&gt;` (required).
- `resources: &lt;class 'm3.observability.Observation[tuple[ResourceInfo, ...]]'&gt;` (required).
- `resource_templates: &lt;class 'm3.observability.Observation[tuple[TemplateInfo, ...]]'&gt;` (required).
- `prompts: &lt;class 'm3.observability.Observation[tuple[PromptInfo, ...]]'&gt;` (required).

### `InteractionEntry`

`m3.sync_api.InteractionEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['interaction'] = 'interaction', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), interaction_kind: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], request: m3.observability.Observation[JsonValue] = &lt;factory&gt;, response: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['interaction']` (default: `'interaction'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `interaction_kind: &lt;class 'str'&gt;` (required).
- `request: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `response: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `LifecycleEntry`

`m3.sync_api.LifecycleEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['lifecycle'] = 'lifecycle', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), phase: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)]) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['lifecycle']` (default: `'lifecycle'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `phase: &lt;class 'str'&gt;` (required).

### `MessageEntry`

`m3.sync_api.MessageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['message'] = 'message', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), message_id: m3.observability.Observation[str] = &lt;factory&gt;, role: m3.observability.MessageRole = &lt;MessageRole.ASSISTANT: 'assistant'&gt;, content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = (), stop_reason: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['message']` (default: `'message'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `message_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `role: &lt;enum 'MessageRole'&gt;` (default: `&lt;MessageRole.ASSISTANT: 'assistant'&gt;`).
- `content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `stop_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `MessageRole`

`m3.sync_api.MessageRole(*values)`

### `Observation`

`m3.sync_api.Observation(*, state: m3.observability.ObservationState, value: Optional[~_T] = None, reason: m3.observability.ObservationReason | None = None, provenance: tuple[m3.types.EventSource, ...] = (), evidence_ref: m3.types.EvidenceRef | None = None) -&gt; None`

A typed value with explicit availability and provenance.

Public fields and methods:

- `state: &lt;enum 'ObservationState'&gt;` (required).
- `value: typing.Optional[~_T]` (default: `None`).
- `reason: m3.observability.ObservationReason | None` (default: `None`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `evidence_ref: m3.types.EvidenceRef | None` (default: `None`).

### `ObservationReason`

`m3.sync_api.ObservationReason(*values)`

### `ObservationState`

`m3.sync_api.ObservationState(*values)`

How completely a provider-dependent value was observed.

### `OpenCodeTrace`

`m3.sync_api.OpenCodeTrace(*, kind: Literal['opencode'] = 'opencode', session_id: m3.observability.Observation[str] = &lt;factory&gt;, provider_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, http_lifecycle: m3.observability.Observation[JsonValue] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['opencode']` (default: `'opencode'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `provider_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `http_lifecycle: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `PiTrace`

`m3.sync_api.PiTrace(*, kind: Literal['pi'] = 'pi', session_id: m3.observability.Observation[str] = &lt;factory&gt;, provider_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['pi']` (default: `'pi'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `provider_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `ProcessEntry`

`m3.sync_api.ProcessEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['process'] = 'process', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), executable: m3.observability.Observation[str] = &lt;factory&gt;, pid: m3.observability.Observation[int] = &lt;factory&gt;, exit_code: m3.observability.Observation[int] = &lt;factory&gt;, signal: m3.observability.Observation[int] = &lt;factory&gt;, stderr: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['process']` (default: `'process'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `executable: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `pid: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `exit_code: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `signal: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `stderr: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `ProtocolCallAttempt`

`m3.sync_api.ProtocolCallAttempt(*, attempt_index: Annotated[int, Ge(ge=0)], jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, continuation_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, operation_params: m3.observability.Observation[JsonValue] = &lt;factory&gt;, input_required: bool = False, result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, raw_result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.INCOMPLETE: 'incomplete'&gt;, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;) -&gt; None`

One wire-level attempt belonging to a prompt or resource call.

Public fields and methods:

- `attempt_index: &lt;class 'int'&gt;` (required).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `continuation_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `operation_params: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `input_required: &lt;class 'bool'&gt;` (default: `False`).
- `result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `raw_result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.INCOMPLETE: 'incomplete'&gt;`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).

### `ProtocolEntry`

`m3.sync_api.ProtocolEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['protocol'] = 'protocol', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), protocol: m3.observability.ProtocolKind, method: m3.observability.Observation[str] = &lt;factory&gt;, direction: m3.types.EventDirection = &lt;EventDirection.INTERNAL: 'internal'&gt;, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request: m3.observability.Observation[JsonValue] = &lt;factory&gt;, response: m3.observability.Observation[JsonValue] = &lt;factory&gt;, error: m3.observability.Observation[ProtocolErrorInfo] = &lt;factory&gt;, http: m3.observability.Observation[HttpExchange] = &lt;factory&gt;, operation_kind: Optional[Literal['prompt', 'resource']] = None, operation_name: m3.observability.Observation[str] = &lt;factory&gt;, attempts: tuple[m3.observability.ProtocolCallAttempt, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['protocol']` (default: `'protocol'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `protocol: &lt;enum 'ProtocolKind'&gt;` (required).
- `method: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `direction: &lt;enum 'EventDirection'&gt;` (default: `&lt;EventDirection.INTERNAL: 'internal'&gt;`).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `response: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `error: &lt;class 'm3.observability.Observation[ProtocolErrorInfo]'&gt;` (required).
- `http: &lt;class 'm3.observability.Observation[HttpExchange]'&gt;` (required).
- `operation_kind: typing.Optional[typing.Literal['prompt', 'resource']]` (default: `None`).
- `operation_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `attempts: tuple[m3.observability.ProtocolCallAttempt, ...]` (default: `()`).

### `ProtocolErrorInfo`

`m3.sync_api.ProtocolErrorInfo(*, code: int | str | None = None, message: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], data: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `code: int | str | None` (default: `None`).
- `message: &lt;class 'str'&gt;` (required).
- `data: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `ProtocolKind`

`m3.sync_api.ProtocolKind(*values)`

### `ProviderEntry`

`m3.sync_api.ProviderEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['provider'] = 'provider', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), provider: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], category: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], data: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['provider']` (default: `'provider'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `provider: &lt;class 'str'&gt;` (required).
- `category: &lt;class 'str'&gt;` (required).
- `data: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `RawEvidence`

`m3.sync_api.RawEvidence(*, reference: m3.types.EvidenceRef, media_type: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], content: Union[JsonValue, str], size_bytes: Annotated[int, Ge(ge=0)], returned_size_bytes: Annotated[int, Ge(ge=0)], truncated: bool = False, redacted: Literal[True] = True) -&gt; None`

Public fields and methods:

- `reference: &lt;class 'm3.types.EvidenceRef'&gt;` (required).
- `media_type: &lt;class 'str'&gt;` (required).
- `content: typing.Union[JsonValue, str]` (required).
- `size_bytes: &lt;class 'int'&gt;` (required).
- `returned_size_bytes: &lt;class 'int'&gt;` (required).
- `truncated: &lt;class 'bool'&gt;` (default: `False`).
- `redacted: typing.Literal[True]` (default: `True`).

### `RawEvidenceSource`

`m3.sync_api.RawEvidenceSource(*values)`

### `RawMessageEntry`

`m3.sync_api.RawMessageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['raw_message'] = 'raw_message', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), source: m3.observability.RawEvidenceSource, direction: m3.types.EventDirection = &lt;EventDirection.INTERNAL: 'internal'&gt;, media_type: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], preview: m3.observability.Observation[Union[JsonValue, str]] = &lt;factory&gt;, evidence_ref: m3.types.EvidenceRef | None = None, size_bytes: Annotated[int, Ge(ge=0)] = 0, redacted: Literal[True] = True) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['raw_message']` (default: `'raw_message'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `source: &lt;enum 'RawEvidenceSource'&gt;` (required).
- `direction: &lt;enum 'EventDirection'&gt;` (default: `&lt;EventDirection.INTERNAL: 'internal'&gt;`).
- `media_type: &lt;class 'str'&gt;` (required).
- `preview: &lt;class 'm3.observability.Observation[Union[JsonValue, str]]'&gt;` (required).
- `evidence_ref: m3.types.EvidenceRef | None` (default: `None`).
- `size_bytes: &lt;class 'int'&gt;` (default: `0`).
- `redacted: typing.Literal[True]` (default: `True`).

### `ReasoningEntry`

`m3.sync_api.ReasoningEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['reasoning'] = 'reasoning', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), block_id: m3.observability.Observation[str] = &lt;factory&gt;, content: m3.observability.Observation[tuple[Annotated[Union[TextContent, FileContent, ImageContent, AudioContent, ResourceLink, OpaqueContent], FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['reasoning']` (default: `'reasoning'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `block_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `content: &lt;class 'm3.observability.Observation[tuple[Annotated[Union[TextContent, FileContent, ImageContent, AudioContent, ResourceLink, OpaqueContent], FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]]'&gt;` (required).

### `ReportedToolCall`

`m3.sync_api.ReportedToolCall(*, provider_call_id: m3.observability.Observation[str] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `provider_call_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `RuntimeTraceInfo`

`m3.sync_api.RuntimeTraceInfo(*args, **kwargs)`

Runtime representation of an annotated type.

### `SafeHttpHeader`

`m3.sync_api.SafeHttpHeader(*, name: Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id'], value: str) -&gt; None`

Public fields and methods:

- `name: typing.Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id']` (required).
- `value: &lt;class 'str'&gt;` (required).

### `ToolCallAttempt`

`m3.sync_api.ToolCallAttempt(*, attempt_index: Annotated[int, Ge(ge=0)], jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, continuation_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, operation_params: m3.observability.Observation[JsonValue] = &lt;factory&gt;, input_required: bool = False, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, raw_result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.ToolCallStatus = &lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;) -&gt; None`

One wire-level attempt belonging to a logical tool call.

Public fields and methods:

- `attempt_index: &lt;class 'int'&gt;` (required).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `continuation_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `operation_params: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `input_required: &lt;class 'bool'&gt;` (default: `False`).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `raw_result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;enum 'ToolCallStatus'&gt;` (default: `&lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).

### `ToolCallEntry`

`m3.sync_api.ToolCallEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['tool_call'] = 'tool_call', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), call_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], provider_call_id: m3.observability.Observation[str] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, tool_status: m3.observability.ToolCallStatus = &lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;, correlation: m3.observability.CorrelationState = &lt;CorrelationState.UNAVAILABLE: 'unavailable'&gt;, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, server_latency_ms: m3.observability.Observation[float] = &lt;factory&gt;, policy: m3.observability.Observation[ToolPolicyDecision] = &lt;factory&gt;, reported: m3.observability.Observation[ReportedToolCall] = &lt;factory&gt;, wire: m3.observability.Observation[WireToolCall] = &lt;factory&gt;, conflicts: tuple[m3.observability.EvidenceConflict, ...] = (), attempts: tuple[m3.observability.ToolCallAttempt, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['tool_call']` (default: `'tool_call'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `call_id: &lt;class 'str'&gt;` (required).
- `provider_call_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `tool_status: &lt;enum 'ToolCallStatus'&gt;` (default: `&lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;`).
- `correlation: &lt;enum 'CorrelationState'&gt;` (default: `&lt;CorrelationState.UNAVAILABLE: 'unavailable'&gt;`).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `server_latency_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `policy: &lt;class 'm3.observability.Observation[ToolPolicyDecision]'&gt;` (required).
- `reported: &lt;class 'm3.observability.Observation[ReportedToolCall]'&gt;` (required).
- `wire: &lt;class 'm3.observability.Observation[WireToolCall]'&gt;` (required).
- `conflicts: tuple[m3.observability.EvidenceConflict, ...]` (default: `()`).
- `attempts: tuple[m3.observability.ToolCallAttempt, ...]` (default: `()`).

### `ToolCallStatus`

`m3.sync_api.ToolCallStatus(*values)`

### `ToolResult`

`m3.sync_api.ToolResult(*, content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = (), structured_content: m3.observability.Observation[JsonValue] = &lt;factory&gt;, is_error: bool = False, error: m3.observability.Observation[ErrorInfo] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `structured_content: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `is_error: &lt;class 'bool'&gt;` (default: `False`).
- `error: &lt;class 'm3.observability.Observation[ErrorInfo]'&gt;` (required).

### `TraceEntry`

`m3.sync_api.TraceEntry(*args, **kwargs)`

Runtime representation of an annotated type.

### `TraceEntryBase`

`m3.sync_api.TraceEntryBase(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Annotated[str, MinLen(min_length=1), MaxLen(max_length=64)], parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: &lt;class 'str'&gt;` (required).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).

### `TraceStatus`

`m3.sync_api.TraceStatus(*values)`

### `TraceSummary`

`m3.sync_api.TraceSummary(*, timing: m3.observability.TraceTiming = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;, turn_count: Annotated[int, Ge(ge=0)] = 0, message_count: Annotated[int, Ge(ge=0)] = 0, reasoning_count: Annotated[int, Ge(ge=0)] = 0, tool_call_count: Annotated[int, Ge(ge=0)] = 0, successful_tool_call_count: Annotated[int, Ge(ge=0)] = 0, failed_tool_call_count: Annotated[int, Ge(ge=0)] = 0, protocol_error_count: Annotated[int, Ge(ge=0)] = 0, activity_health: m3.types.ActivityHealth = &lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;, cleanup_status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;) -&gt; None`

Public fields and methods:

- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).
- `turn_count: &lt;class 'int'&gt;` (default: `0`).
- `message_count: &lt;class 'int'&gt;` (default: `0`).
- `reasoning_count: &lt;class 'int'&gt;` (default: `0`).
- `tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `successful_tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `failed_tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `protocol_error_count: &lt;class 'int'&gt;` (default: `0`).
- `activity_health: &lt;enum 'ActivityHealth'&gt;` (default: `&lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;`).
- `cleanup_status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).

### `TraceTiming`

`m3.sync_api.TraceTiming(*, started_at: datetime.datetime = &lt;factory&gt;, finished_at: datetime.datetime | None = None, start_offset_ms: Annotated[float, Ge(ge=0)] = 0, end_offset_ms: Annotated[float, Ge(ge=0)] = 0, duration_ms: Annotated[float, Ge(ge=0)] = 0) -&gt; None`

Public fields and methods:

- `started_at: &lt;class 'datetime.datetime'&gt;` (required).
- `finished_at: datetime.datetime | None` (default: `None`).
- `start_offset_ms: &lt;class 'float'&gt;` (default: `0`).
- `end_offset_ms: &lt;class 'float'&gt;` (default: `0`).
- `duration_ms: &lt;class 'float'&gt;` (default: `0`).

### `TraceView`

`m3.sync_api.TraceView(*, schema_id: Literal['m3.trace_view'] = 'm3.trace_view', schema_version: Literal['1.1', '1.2'] = '1.1', trace_id: m3.types.TraceId, execution_id: m3.types.ExecutionId, outcome: m3.types.ExecutionOutcome = &lt;ExecutionOutcome.COMPLETED: 'completed'&gt;, completeness: Literal['complete', 'partial'] = 'complete', limitations: tuple[str, ...] = (), agent: m3.types.AgentIdentity | None = None, runtime: m3.observability.DirectTrace | m3.observability.OpenCodeTrace | m3.observability.ClaudeCodeTrace | m3.observability.CodexTrace | m3.observability.PiTrace | m3.observability.ACPTrace = &lt;factory&gt;, summary: m3.observability.TraceSummary = &lt;factory&gt;, timeline: tuple[typing.Annotated[m3.observability.LifecycleEntry | m3.observability.MessageEntry | m3.observability.ReasoningEntry | m3.observability.ToolCallEntry | m3.observability.ProtocolEntry | m3.observability.TransportEntry | m3.observability.InitializationEntry | m3.observability.UsageEntry | m3.observability.InteractionEntry | m3.observability.ElicitationEntry | m3.observability.ProcessEntry | m3.observability.WorkspaceEntry | m3.observability.ArtifactEntry | m3.observability.EvaluationEntry | m3.observability.DiagnosticEntry | m3.observability.RawMessageEntry | m3.observability.ProviderEntry, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = ()) -&gt; None`

Public fields and methods:

- `schema_id: typing.Literal['m3.trace_view']` (default: `'m3.trace_view'`).
- `schema_version: typing.Literal['1.1', '1.2']` (default: `'1.1'`).
- `trace_id: &lt;class 'm3.types.TraceId'&gt;` (required).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `outcome: &lt;enum 'ExecutionOutcome'&gt;` (default: `&lt;ExecutionOutcome.COMPLETED: 'completed'&gt;`).
- `completeness: typing.Literal['complete', 'partial']` (default: `'complete'`).
- `limitations: tuple[str, ...]` (default: `()`).
- `agent: m3.types.AgentIdentity | None` (default: `None`).
- `runtime: m3.observability.DirectTrace | m3.observability.OpenCodeTrace | m3.observability.ClaudeCodeTrace | m3.observability.CodexTrace | m3.observability.PiTrace | m3.observability.ACPTrace` (required).
- `summary: &lt;class 'm3.observability.TraceSummary'&gt;` (required).
- `timeline: tuple[typing.Annotated[m3.observability.LifecycleEntry | m3.observability.MessageEntry | m3.observability.ReasoningEntry | m3.observability.ToolCallEntry | m3.observability.ProtocolEntry | m3.observability.TransportEntry | m3.observability.InitializationEntry | m3.observability.UsageEntry | m3.observability.InteractionEntry | m3.observability.ElicitationEntry | m3.observability.ProcessEntry | m3.observability.WorkspaceEntry | m3.observability.ArtifactEntry | m3.observability.EvaluationEntry | m3.observability.DiagnosticEntry | m3.observability.RawMessageEntry | m3.observability.ProviderEntry, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `for_turn(self, turn: '_TurnResult | _TurnState | _TurnId | str') -&gt; 'TraceView'`: Return the finalized evidence belonging to one turn.
- `for_session(self, session_id: '_SessionId | str') -&gt; 'TraceView'`
- `for_server(self, server_binding: 'str') -&gt; 'TraceView'`
- `between(self, start_offset_ms: 'float', end_offset_ms: 'float') -&gt; 'TraceView'`

### `TransportEntry`

`m3.sync_api.TransportEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['transport'] = 'transport', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), phase: Literal['connected', 'disconnected'], configured: m3.observability.Observation[TransportKind] = &lt;factory&gt;, instrumented: m3.observability.Observation[TransportKind] = &lt;factory&gt;) -&gt; None`

A stable MCP transport lifecycle observation.

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['transport']` (default: `'transport'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `phase: typing.Literal['connected', 'disconnected']` (required).
- `configured: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).
- `instrumented: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).

### `UsageEntry`

`m3.sync_api.UsageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['usage'] = 'usage', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), input_tokens: m3.observability.Observation[int] = &lt;factory&gt;, output_tokens: m3.observability.Observation[int] = &lt;factory&gt;, reasoning_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_creation_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_read_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_write_tokens: m3.observability.Observation[int] = &lt;factory&gt;, total_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cost: m3.observability.Observation[float] = &lt;factory&gt;, currency: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['usage']` (default: `'usage'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `input_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `output_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `reasoning_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_creation_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_read_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_write_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `total_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cost: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `currency: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `UsageValue`

`m3.sync_api.UsageValue(*, input_tokens: m3.observability.Observation[int] = &lt;factory&gt;, output_tokens: m3.observability.Observation[int] = &lt;factory&gt;, reasoning_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_creation_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_read_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_write_tokens: m3.observability.Observation[int] = &lt;factory&gt;, total_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cost: m3.observability.Observation[float] = &lt;factory&gt;, currency: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Value-only usage aggregate used by summaries and runtime metadata.

Public fields and methods:

- `input_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `output_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `reasoning_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_creation_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_read_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_write_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `total_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cost: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `currency: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `WireToolCall`

`m3.sync_api.WireToolCall(*, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, latency_ms: m3.observability.Observation[float] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `latency_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).

### `WorkspaceEntry`

`m3.sync_api.WorkspaceEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['workspace'] = 'workspace', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), change: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['workspace']` (default: `'workspace'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `change: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

## `m3.async_api`

### `AsyncAgentSession`

`m3.async_api.AsyncAgentSession(spec: 'AgentSpec', adapter: 'AgentAdapter', *, server_manager: 'Any' = None, server_manager_factory: 'Callable[[], Any] | None' = None, interaction_controller: 'Interactions | None' = None, provenance: 'SessionSource | None' = None, on_close: 'Callable[[AsyncAgentSession], None] | None' = None, event_sink: '_EventSink | None' = None, trace_recorder: 'ExecutionTraceRecorder | None' = None, trace_owner: 'bool' = True, artifact_store: 'ArtifactStore | None' = None, harness_cache_dir: 'str | None' = None, runtime_manager: 'Any' = None, runtime_invocation_dir: 'str | Path | None' = None, runtime_project_root: 'str | Path | None' = None, managed_input_runtime: 'ManagedInputRuntime | None' = None) -&gt; 'None'`

Lifecycle-safe async session over one injected harness adapter.

Public fields and methods:

- `send(self, message: 'str | UserMessage', *, timeout: 'float | None' = None, metadata: 'Mapping[str, object] | None' = None, elicitation: 'ElicitationPlan | None' = None, elicitation_round_limit: 'int' = 10) -&gt; 'TurnResult'`
- `enqueue_turn(self, message: 'str | UserMessage', *, timeout: 'float | None' = None, metadata: 'Mapping[str, object] | None' = None) -&gt; 'QueuedTurn'`
- `cancel(self) -&gt; 'None'`
- `snapshot(self) -&gt; 'ExecutionState'`
- `fork(self, request: 'SessionForkRequest', *, adapter_factory: 'Callable[[AgentSpec, SessionSource], HarnessAdapter | Awaitable[HarnessAdapter]]') -&gt; 'AsyncAgentSession'`: Create a fresh child execution from this terminal session.
- `aclose(self) -&gt; 'None'`

### `HarnessAdapter`

`m3.async_api.HarnessAdapter(*args, **kwargs)`

Minimal injected adapter contract for one continuing conversation.

Public fields and methods:

- `start(self, spec: 'AgentSpec') -&gt; 'None'`
- `send(self, message: 'UserMessage', *, timeout: 'float | None' = None, metadata: 'Mapping[str, object] | None' = None) -&gt; 'TurnResponse | AdapterTurn'`
- `close(self) -&gt; 'None'`

### `AsyncProbes`

`m3.async_api.AsyncProbes(*, timeout_seconds: 'float' = 5.0, output_limit: 'int' = 65536) -&gt; 'None'`

Async namespace for capability probes.

Public fields and methods:

- `probe_binary(self, name: 'str', executable: 'str | os.PathLike[str]', *, args: 'Sequence[str]' = ('--version',), env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None) -&gt; 'ProbeResult'`
- `probe_protocol(self, name: 'str', executable: 'str | os.PathLike[str]', *, args: 'Sequence[str]' = ('--protocol-version',), env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None, transport: 'str | TransportKind | None' = None) -&gt; 'ProbeResult'`
- `probe_harness(self, name: 'str', executable: 'str | os.PathLike[str]', *, args: 'Sequence[str]' = ('--version',), env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None, transport: 'str | TransportKind | None' = None) -&gt; 'ProbeResult'`
- `probe_transport(self, name: 'str', *, transport: 'str | TransportKind', executable: 'str | os.PathLike[str] | None' = None, args: 'Sequence[str]' = ('--transport-ready',), module: 'str | None' = None, env: 'Mapping[str, str] | None' = None, timeout_seconds: 'float | None' = None) -&gt; 'ProbeResult'`
- `probe_storage(self, name: 'str' = 'memory', *, module: 'str | None' = None) -&gt; 'ProbeResult'`
- `probe_requested(self, requests: 'Iterable[ProbeRequest]') -&gt; 'ProbeReport'`

### `AsyncDirectClient`

`m3.async_api.AsyncDirectClient(kit: 'AsyncMCPTestKit', server: '_ServerValue', *, timeout: 'float', validate_schemas: 'bool', secret_resolver: '_SecretResolver | None', bearer_token: '_SecretReference | None', auth: 'httpx2.Auth | None', for_agent: 'bool', resolve_host: '_HostResolver | None', raise_server_exceptions: 'bool', session_options: '_Mapping[str, _Any]', trace_bridge: '_DirectTraceBridge | None' = None, trace_owner: 'bool' = True, workspace_root: 'str | None' = None, server_bindings: '_Iterable[_Mapping[str, _Any]]' = (), prefer_modern_protocol: 'bool' = False) -&gt; 'None'`

Lifecycle-owned async direct client returned by ``AsyncMCPTestKit``.

Public fields and methods:

- `aclose(self) -&gt; 'None'`

### `AsyncExecutionHandle`

`m3.async_api.AsyncExecutionHandle(controller: 'AsyncExecutionController', spec: 'ExecutionSpec', *, store: 'ExecutionStore | None' = None, persistent: 'bool' = False, execution_id: 'ExecutionId | str | None' = None, run_id: 'str | None' = None, human_input: 'HumanInput' = 'fail', resolution_provenance: 'Sequence[Mapping[str, Any]]' = ()) -&gt; 'None'`

Live, immutable view of one submitted execution.

Public fields and methods:

- `snapshot(self) -&gt; 'ExecutionState'`
- `pending_elicitation(self) -&gt; 'PendingElicitationRound | None'`: Return the current public managed-input round, if one is pending.
- `respond_elicitation(self, round_id: 'str', responses: 'Mapping[str, ElicitationResponse]', *, idempotency_key: 'str') -&gt; 'None'`: Atomically validate and commit keyed human responses.
- `result(self, timeout: 'float | None' = None) -&gt; 'ExecutionResult'`
- `cancel(self) -&gt; 'None'`
- `on_event(self, callback: 'Callable[[Event], Any]') -&gt; 'Callable[[], None]'`: Subscribe after commit; callback failures cannot affect execution.
- `events(self, *, after_sequence: 'int' = -1) -&gt; 'AsyncIterator[Event]'`: Yield committed events in sequence order and finish at terminal.

### `AsyncMCPTestKit`

`m3.async_api.AsyncMCPTestKit(config: 'Config | _Mapping[str, _Any] | None' = None, *, env: '_Mapping[str, str] | None' = None, cwd: 'str | _Path | None' = None, probe_timeout_seconds: 'float' = 5.0, probe_output_limit: 'int' = 65536, adapter_registry: '_HarnessAdapterRegistry | None' = None, store: '_ExecutionStore | None' = None, embedded_worker: 'bool' = True, run_id: '_RunId | str | None' = None, suite_name: 'str | None' = None, project_id: '_ProjectId | str | None' = None, record_checks: 'bool' = False, max_judge_requests: 'int | None' = None, harness_cache_dir: 'str | _Path | None' = None) -&gt; 'None'`

Async twin of :class:`m3.sync_api.MCPTestKit`.

Public fields and methods:

- `get_trace(self, execution_id: '_ExecutionId | str') -&gt; '_TraceResult'`: Return the finalized stable trace for an execution.
- `get_trace_view(self, execution_id: '_ExecutionId | str') -&gt; 'TraceView'`: Return the finalized typed trace view for an execution.
- `read_raw_evidence(self, reference: '_EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`: Read bounded, redacted raw evidence by its durable reference.
- `aclose(self) -&gt; 'None'`: Close the shell; repeated calls are intentionally harmless.
- `capabilities(self, requests: '_Iterable[ProbeRequest]' = ()) -&gt; 'ProbeReport'`
- `register_evaluator(self, name: 'str', evaluator: '_EvaluatorCallable | _AsyncEvaluator') -&gt; 'None'`: Register an evaluator callback by its serializable name.
- `evaluate(self, subject: '_Any', evaluator: 'str | _EvaluatorCallable | _AsyncEvaluator', *, required: 'bool' = False, goal: 'str | None' = None, trace: '_Any' = None, artifacts: '_Any' = (), metadata: '_Mapping[str, str | int | float | bool | None] | None' = None, execution_id: '_Any' = None, turn_id: '_Any' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`: Run and persist one evaluation without changing lifecycle.
- `judge_response(self, *, name: 'str', input: 'str', actual: 'str', expected: 'str', judge: '_LLMJudge', required: 'bool' = False, execution_id: '_Any' = None, turn_id: '_Any' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`
- `evaluation_results(self) -&gt; 'tuple[_EvaluationResult, ...]'`
- `agents(self, selections: '_Any', *, trials: 'int' = 1) -&gt; 'tuple[_Any, ...]'`: Expand ordered agent dictionaries without starting any I/O.
- `run(self, spec: '_DirectSpec | _AgentSpec') -&gt; '_ExecutionResult'`
- `submit(self, spec: '_ExecutionSpec', *, human_input: '_HumanInput' = 'fail') -&gt; 'AsyncExecutionHandle'`
- `direct(self, server: '_ServerValue | _ServerBinding', *, protocol: 'object | None' = None, timeout: 'float | None' = None, validate_schemas: 'bool' = False, secret_resolver: '_SecretResolver | None' = None, bearer_token: '_SecretReference | None' = None, auth: 'httpx2.Auth | None' = None, for_agent: 'bool' = False, resolve_host: '_HostResolver | None' = None, raise_server_exceptions: 'bool' = True, sampling_callback: '_Any' = None, elicitation_callback: '_RemovedElicitationCallback' = &lt;m3.direct_client._RemovedElicitationCallback object&gt;, list_roots_callback: '_Any' = None, logging_callback: '_Any' = None, message_handler: '_Any' = None, client_info: '_Any' = None, log_level: '_Any' = None, sampling_capabilities: '_Any' = None, result_claims: '_Any' = None, extensions: '_Mapping[str, _Mapping[str, _Any]] | None' = None, notification_bindings: '_Iterable[_Any] | None' = None, dispatcher: '_Any' = None, trace_bridge: '_DirectTraceBridge | None' = None, trace_owner: 'bool' = True, workspace_root: 'str | None' = None) -&gt; 'AsyncDirectClient'`
- `agent_session(self, spec: '_AgentSpec', *, adapter: '_AgentAdapter | None' = None, runtime_servers: '_Iterable[_Any]' = (), _event_sink: '_Any' = None, interaction_handlers: 'InteractionHandlers | None' = None, _trace_recorder: '_ExecutionTraceRecorder | None' = None, _trace_owner: 'bool' = True, _execution_id: '_Any' = None, _artifact_store: '_ArtifactStore | None' = None, _managed_input_runtime: '_Any' = None, harness_cache_dir: 'str | _Path | None' = None) -&gt; 'AsyncAgentSession'`

### `CallToolResult`

`m3.async_api.CallToolResult(*, raw: Any = None, content: tuple[collections.abc.Mapping[str, typing.Any], ...] = (), structured_content: Any = None, is_error: bool = False) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `content: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).
- `structured_content: typing.Any` (default: `None`).
- `is_error: &lt;class 'bool'&gt;` (default: `False`).

### `CompletionResult`

`m3.async_api.CompletionResult(*, raw: Any = None, values: tuple[str, ...] = (), total: int | None = None, has_more: bool | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `values: tuple[str, ...]` (default: `()`).
- `total: int | None` (default: `None`).
- `has_more: bool | None` (default: `None`).

### `ConfigOrigin`

`m3.async_api.ConfigOrigin(*, source: m3.configuration.ConfigSource, origin: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)]) -&gt; None`

Public fields and methods:

- `source: &lt;enum 'ConfigSource'&gt;` (required).
- `origin: &lt;class 'str'&gt;` (required).

### `ConfigSource`

`m3.async_api.ConfigSource(*values)`

### `Config`

`m3.async_api.Config(*, artifact_policy: Literal['failed', 'always', 'never'] = 'failed', protocol_revision: Annotated[str, Strict(strict=True)] = 'auto', telemetry_enabled: Annotated[bool, Strict(strict=True)] = False, sources: collections.abc.Mapping[str, m3.configuration.ConfigOrigin] = &lt;factory&gt;) -&gt; None`

Effective SDK-wide settings and the origin of each setting.

Public fields and methods:

- `artifact_policy: typing.Literal['failed', 'always', 'never']` (default: `'failed'`).
- `protocol_revision: &lt;class 'str'&gt;` (default: `'auto'`).
- `telemetry_enabled: &lt;class 'bool'&gt;` (default: `False`).
- `sources: collections.abc.Mapping[str, m3.configuration.ConfigOrigin]` (required).
- `source_for(self, field: 'str') -&gt; 'ConfigOrigin'`

### `ConfigError`

`m3.async_api.ConfigError(*, field: 'str', origin: 'str', reason: 'str', code: 'str | None' = None) -&gt; 'None'`

A strict, value-free configuration diagnostic.

### `PromptInfo`

`m3.async_api.PromptInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, description: str | None = None, arguments: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `description: str | None` (default: `None`).
- `arguments: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `ResourceInfo`

`m3.async_api.ResourceInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None, size: Annotated[int | None, Ge(ge=0)] = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).
- `size: int | None` (default: `None`).

### `TemplateInfo`

`m3.async_api.TemplateInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri_template: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri_template: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).

### `ToolInfo`

`m3.async_api.ToolInfo(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, description: str | None = None, input_schema: collections.abc.Mapping[str, typing.Any] | bool = &lt;factory&gt;, output_schema: collections.abc.Mapping[str, typing.Any] | bool | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `description: str | None` (default: `None`).
- `input_schema: collections.abc.Mapping[str, typing.Any] | bool` (required).
- `output_schema: collections.abc.Mapping[str, typing.Any] | bool | None` (default: `None`).

### `EmptyResult`

`m3.async_api.EmptyResult(*, raw: Any = None, result_type: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `result_type: str | None` (default: `None`).

### `GetPromptResult`

`m3.async_api.GetPromptResult(*, raw: Any = None, description: str | None = None, messages: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `description: str | None` (default: `None`).
- `messages: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `InitializeResult`

`m3.async_api.InitializeResult(*, raw: Any = None, protocol_version: str, server_info: collections.abc.Mapping[str, typing.Any], instructions: str | None = None, capabilities: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `protocol_version: &lt;class 'str'&gt;` (required).
- `server_info: collections.abc.Mapping[str, typing.Any]` (required).
- `instructions: str | None` (default: `None`).
- `capabilities: collections.abc.Mapping[str, typing.Any]` (required).

### `InitializationResult`

`m3.async_api.InitializationResult(*, raw: Any = None, protocol_version: str, server_info: collections.abc.Mapping[str, typing.Any], instructions: str | None = None, capabilities: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `protocol_version: &lt;class 'str'&gt;` (required).
- `server_info: collections.abc.Mapping[str, typing.Any]` (required).
- `instructions: str | None` (default: `None`).
- `capabilities: collections.abc.Mapping[str, typing.Any]` (required).

### `InputRequiredResult`

`m3.async_api.InputRequiredResult(*, raw: Any = None, result_type: Literal['input_required'] = 'input_required', input_requests: collections.abc.Mapping[str, typing.Any] | None = None, request_state: str | None = None) -&gt; None`

Official MCP interactive result, preserved instead of coercing empty data.

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `result_type: typing.Literal['input_required']` (default: `'input_required'`).
- `input_requests: collections.abc.Mapping[str, typing.Any] | None` (default: `None`).
- `request_state: str | None` (default: `None`).

### `ListPromptsResult`

`m3.async_api.ListPromptsResult(*, raw: Any = None, prompts: tuple[m3.types.PromptInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `prompts: tuple[m3.types.PromptInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ListResourcesResult`

`m3.async_api.ListResourcesResult(*, raw: Any = None, resources: tuple[m3.types.ResourceInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resources: tuple[m3.types.ResourceInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ListResourceTemplatesResult`

`m3.async_api.ListResourceTemplatesResult(*, raw: Any = None, resource_templates: tuple[m3.types.TemplateInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resource_templates: tuple[m3.types.TemplateInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ListToolsResult`

`m3.async_api.ListToolsResult(*, raw: Any = None, tools: tuple[m3.types.ToolInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `tools: tuple[m3.types.ToolInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ProbeEvidence`

`m3.async_api.ProbeEvidence(*, kind: m3.services.probes.ProbeKind, target: Annotated[str, MinLen(min_length=1), MaxLen(max_length=512)], command: tuple[str, ...] = (), resolved_executable: str | None = None, detected_version: str | None = None, protocol_version: str | None = None, output: Annotated[str, MaxLen(max_length=65536)] = '', details: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Safe evidence collected by one probe.

Public fields and methods:

- `kind: &lt;enum 'ProbeKind'&gt;` (required).
- `target: &lt;class 'str'&gt;` (required).
- `command: tuple[str, ...]` (default: `()`).
- `resolved_executable: str | None` (default: `None`).
- `detected_version: str | None` (default: `None`).
- `protocol_version: str | None` (default: `None`).
- `output: &lt;class 'str'&gt;` (default: `''`).
- `details: collections.abc.Mapping[str, typing.Any]` (required).

### `ProbeKind`

`m3.async_api.ProbeKind(*values)`

The independently requestable capability categories.

### `ProbeReport`

`m3.async_api.ProbeReport(*, readiness: m3.types.Readiness, results: tuple[m3.services.probes.ProbeResult, ...] = ()) -&gt; None`

Aggregate readiness for exactly the requested probes.

Public fields and methods:

- `readiness: &lt;class 'm3.types.Readiness'&gt;` (required).
- `results: tuple[m3.services.probes.ProbeResult, ...]` (default: `()`).
- `result_for(self, name: 'str') -&gt; 'ProbeResult | None'`: Return the result for ``name`` without guessing another target.

### `ProbeRequest`

`m3.async_api.ProbeRequest(kind: 'ProbeKind', name: 'str', executable: 'str | None' = None, args: 'tuple[str, ...]' = (), env: 'Mapping[str, str] | None' = None, module: 'str | None' = None, transport: 'str | None' = None, timeout_seconds: 'float | None' = None) -&gt; None`

Typed request used by :meth:`Probes.probe_requested`.

### `ProbeResult`

`m3.async_api.ProbeResult(*, capability: m3.types.Capability, evidence: m3.services.probes.ProbeEvidence) -&gt; None`

One capability result and its separately inspectable evidence.

Public fields and methods:

- `capability: &lt;class 'm3.types.Capability'&gt;` (required).
- `evidence: &lt;class 'm3.services.probes.ProbeEvidence'&gt;` (required).

### `Prompt`

`m3.async_api.Prompt(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, description: str | None = None, arguments: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `description: str | None` (default: `None`).
- `arguments: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `PromptPage`

`m3.async_api.PromptPage(*, raw: Any = None, prompts: tuple[m3.types.PromptInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `prompts: tuple[m3.types.PromptInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `PromptResult`

`m3.async_api.PromptResult(*, raw: Any = None, description: str | None = None, messages: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `description: str | None` (default: `None`).
- `messages: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `ReadResourceResult`

`m3.async_api.ReadResourceResult(*, raw: Any = None, contents: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `contents: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `Resource`

`m3.async_api.Resource(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None, size: Annotated[int | None, Ge(ge=0)] = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).
- `size: int | None` (default: `None`).

### `ResourcePage`

`m3.async_api.ResourcePage(*, raw: Any = None, resources: tuple[m3.types.ResourceInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resources: tuple[m3.types.ResourceInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ResourceReadResult`

`m3.async_api.ResourceReadResult(*, raw: Any = None, contents: tuple[collections.abc.Mapping[str, typing.Any], ...] = ()) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `contents: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).

### `ResourceTemplate`

`m3.async_api.ResourceTemplate(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, uri_template: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], description: str | None = None, mime_type: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `uri_template: &lt;class 'str'&gt;` (required).
- `description: str | None` (default: `None`).
- `mime_type: str | None` (default: `None`).

### `ResourceTemplatePage`

`m3.async_api.ResourceTemplatePage(*, raw: Any = None, resource_templates: tuple[m3.types.TemplateInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resource_templates: tuple[m3.types.TemplateInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ResourceTemplatesPage`

`m3.async_api.ResourceTemplatesPage(*, raw: Any = None, resource_templates: tuple[m3.types.TemplateInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resource_templates: tuple[m3.types.TemplateInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ResourcesPage`

`m3.async_api.ResourcesPage(*, raw: Any = None, resources: tuple[m3.types.ResourceInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `resources: tuple[m3.types.ResourceInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `Tool`

`m3.async_api.Tool(*, raw: Any = None, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], title: str | None = None, description: str | None = None, input_schema: collections.abc.Mapping[str, typing.Any] | bool = &lt;factory&gt;, output_schema: collections.abc.Mapping[str, typing.Any] | bool | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `name: &lt;class 'str'&gt;` (required).
- `title: str | None` (default: `None`).
- `description: str | None` (default: `None`).
- `input_schema: collections.abc.Mapping[str, typing.Any] | bool` (required).
- `output_schema: collections.abc.Mapping[str, typing.Any] | bool | None` (default: `None`).

### `ToolCallResult`

`m3.async_api.ToolCallResult(*, raw: Any = None, content: tuple[collections.abc.Mapping[str, typing.Any], ...] = (), structured_content: Any = None, is_error: bool = False) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `content: tuple[collections.abc.Mapping[str, typing.Any], ...]` (default: `()`).
- `structured_content: typing.Any` (default: `None`).
- `is_error: &lt;class 'bool'&gt;` (default: `False`).

### `ToolPage`

`m3.async_api.ToolPage(*, raw: Any = None, tools: tuple[m3.types.ToolInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `tools: tuple[m3.types.ToolInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `ToolsPage`

`m3.async_api.ToolsPage(*, raw: Any = None, tools: tuple[m3.types.ToolInfo, ...] = (), next_cursor: str | None = None) -&gt; None`

Public fields and methods:

- `raw: typing.Any` (default: `None`).
- `tools: tuple[m3.types.ToolInfo, ...]` (default: `()`).
- `next_cursor: str | None` (default: `None`).

### `load_config`

`m3.async_api.load_config(explicit: '_Mapping[str, _Any] | None' = None, *, env: '_Mapping[str, str] | None' = None, cwd: 'str | _Path | None' = None, artifact_policy: '_Any' = &lt;object object&gt;, protocol_revision: '_Any' = &lt;object object&gt;, telemetry_enabled: '_Any' = &lt;object object&gt;) -&gt; 'Config'`

Resolve SDK settings using explicit, environment, project, default order.

### `AllowedCommands`

`m3.async_api.AllowedCommands(*, allowed_executables: 'Sequence[str]', root: 'str | Path', environment: 'Mapping[str, str] | None' = None, allowed_environment: 'Sequence[str]' = ()) -&gt; 'None'`

Safe argv-only terminal handler with cwd, timeout, and output bounds.

### `FilesystemHandler`

`m3.async_api.FilesystemHandler(*args, **kwargs)`

### `FilesystemRequest`

`m3.async_api.FilesystemRequest(operation: 'FilesystemOperation', path: 'str', data: 'bytes | None' = None, max_bytes: 'int' = 1048576) -&gt; None`

FilesystemRequest(operation: 'FilesystemOperation', path: 'str', data: 'bytes | None' = None, max_bytes: 'int' = 1048576)

### `FilesystemResult`

`m3.async_api.FilesystemResult(allowed: 'bool', data: 'bytes | tuple[str, ...] | None', receipt: 'InteractionReceipt') -&gt; None`

FilesystemResult(allowed: 'bool', data: 'bytes | tuple[str, ...] | None', receipt: 'InteractionReceipt')

### `Interactions`

`m3.async_api.Interactions(*, permission_policy: 'PermissionPolicy | None' = None, sampling_policy: 'SamplingPolicy | None' = None, filesystem_policy: 'FilesystemPolicy | None' = None, terminal_policy: 'TerminalPolicy | None' = None, handlers: 'InteractionHandlers | None' = None) -&gt; 'None'`

Apply immutable policies around explicit interaction callbacks.

Public fields and methods:

- `receipts(self) -&gt; 'tuple[InteractionReceipt, ...]'`
- `permission(self, request: 'PermissionRequest') -&gt; 'PermissionResult'`
- `sample(self, request: 'SamplingRequest') -&gt; 'SamplingResult'`
- `filesystem(self, request: 'FilesystemRequest') -&gt; 'FilesystemResult'`
- `terminal(self, request: 'TerminalRequest') -&gt; 'TerminalResult'`

### `InteractionHandlers`

`m3.async_api.InteractionHandlers(permission: 'PermissionCallback | None' = None, sampling: 'SamplingCallback | None' = None, filesystem: 'FilesystemHandler | None' = None, terminal: 'TerminalHandler | None' = None) -&gt; None`

Optional callbacks; absent callbacks are always default-deny.

### `InteractionReceipt`

`m3.async_api.InteractionReceipt(request_id: 'str', kind: 'str', decision: 'Decision', reason: 'str', timestamp: 'datetime' = &lt;factory&gt;) -&gt; None`

Safe decision evidence; request values and handler errors are excluded.

### `PermissionRequest`

`m3.async_api.PermissionRequest(operation: 'str', resource: 'str' = '', destructive: 'bool' = False) -&gt; None`

PermissionRequest(operation: 'str', resource: 'str' = '', destructive: 'bool' = False)

### `PermissionResult`

`m3.async_api.PermissionResult(allowed: 'bool', receipt: 'InteractionReceipt', confirmation_required: 'bool' = False) -&gt; None`

PermissionResult(allowed: 'bool', receipt: 'InteractionReceipt', confirmation_required: 'bool' = False)

### `PermissionHandler`

`m3.async_api.PermissionHandler(*args, **kwargs)`

### `SamplingRequest`

`m3.async_api.SamplingRequest(prompt: 'str', model: 'str | None' = None, metadata: 'Mapping[str, str | int | float | bool | None]' = &lt;factory&gt;) -&gt; None`

SamplingRequest(prompt: 'str', model: 'str | None' = None, metadata: 'Mapping[str, str | int | float | bool | None]' = &lt;factory&gt;)

### `SamplingResult`

`m3.async_api.SamplingResult(accepted: 'bool', content: 'str | None', receipt: 'InteractionReceipt') -&gt; None`

SamplingResult(accepted: 'bool', content: 'str | None', receipt: 'InteractionReceipt')

### `SamplingHandler`

`m3.async_api.SamplingHandler(*args, **kwargs)`

### `TerminalHandler`

`m3.async_api.TerminalHandler(*args, **kwargs)`

### `TerminalRequest`

`m3.async_api.TerminalRequest(argv: 'tuple[str, ...]', cwd: 'str | None' = None, environment: 'Mapping[str, str]' = &lt;factory&gt;, timeout_seconds: 'float' = 30.0, max_output_bytes: 'int' = 1048576) -&gt; None`

TerminalRequest(argv: 'tuple[str, ...]', cwd: 'str | None' = None, environment: 'Mapping[str, str]' = &lt;factory&gt;, timeout_seconds: 'float' = 30.0, max_output_bytes: 'int' = 1048576)

### `TerminalResult`

`m3.async_api.TerminalResult(allowed: 'bool', returncode: 'int | None', stdout: 'bytes', stderr: 'bytes', timed_out: 'bool', truncated: 'bool', receipt: 'InteractionReceipt') -&gt; None`

TerminalResult(allowed: 'bool', returncode: 'int | None', stdout: 'bytes', stderr: 'bytes', timed_out: 'bool', truncated: 'bool', receipt: 'InteractionReceipt')

### `WorkspaceFiles`

`m3.async_api.WorkspaceFiles(root: 'str | Path', *, mode: "Literal['read_only', 'read_write']" = 'read_only', max_bytes: 'int' = 1048576) -&gt; 'None'`

Bounded filesystem handler rooted inside one owned workspace.

### `ElicitationPlan`

`m3.async_api.ElicitationPlan(*, node: Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of'] = 'leaf', request: Optional[Annotated[m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]] = None, response: m3.elicitation.ElicitationResponse | None = None, children: tuple[m3.elicitation.ElicitationPlan, ...] = (), optional_occurrence: bool = False) -&gt; None`

An immutable, serializable elicitation expectation tree.

Public fields and methods:

- `node: typing.Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of']` (default: `'leaf'`).
- `request: typing.Optional[typing.Annotated[m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]]` (default: `None`).
- `response: m3.elicitation.ElicitationResponse | None` (default: `None`).
- `children: tuple[m3.elicitation.ElicitationPlan, ...]` (default: `()`).
- `optional_occurrence: &lt;class 'bool'&gt;` (default: `False`).
- `accept(self, content: 'Mapping[str, object] | None' = None) -&gt; 'ElicitationPlan'`
- `decline(self) -&gt; 'ElicitationPlan'`
- `cancel(self) -&gt; 'ElicitationPlan'`
- `canonical_identity(self) -&gt; 'str'`
- `canonical_json(self) -&gt; 'str'`
- `model_dump(self, *args: 'Any', **kwargs: 'Any') -&gt; 'dict[str, Any]'`
- `model_dump_json(self, *args: 'Any', **kwargs: 'Any') -&gt; 'str'`
- `matcher(self) -&gt; 'PlanMatcher'`

### `ElicitationResponse`

`m3.async_api.ElicitationResponse(*, action: Literal['accept', 'decline', 'cancel'], content: collections.abc.Mapping[str, object] | None = None, meta: collections.abc.Mapping[str, object] | None = None) -&gt; None`

The response that will be associated with one request key.

Public fields and methods:

- `action: typing.Literal['accept', 'decline', 'cancel']` (required).
- `content: collections.abc.Mapping[str, object] | None` (default: `None`).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).

### `FormElicitationRequest`

`m3.async_api.FormElicitationRequest(*, request_key: Annotated[str, MinLen(min_length=1)], mode: Literal['form'] = 'form', message: str, requested_schema: collections.abc.Mapping[str, object], meta: collections.abc.Mapping[str, object] | None = None, task: collections.abc.Mapping[str, object] | None = None, server: str | None = None, operation_kind: Optional[Literal['tool', 'prompt', 'resource']] = None, operation_name: str | None = None) -&gt; None`

A normalized form-mode elicitation request.

Public fields and methods:

- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['form']` (default: `'form'`).
- `message: &lt;class 'str'&gt;` (required).
- `requested_schema: collections.abc.Mapping[str, object]` (required).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).
- `task: collections.abc.Mapping[str, object] | None` (default: `None`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Optional[typing.Literal['tool', 'prompt', 'resource']]` (default: `None`).
- `operation_name: str | None` (default: `None`).

### `PendingElicitationRound`

`m3.async_api.PendingElicitationRound(*, round_id: Annotated[str, MinLen(min_length=1)], execution_id: Annotated[str, MinLen(min_length=1)], logical_operation_id: Annotated[str, MinLen(min_length=1)], server: Annotated[str, MinLen(min_length=1)], operation_kind: Literal['tool', 'prompt', 'resource'], operation_name: Annotated[str, MinLen(min_length=1)], request_state: str | None = None, requests: collections.abc.Mapping[str, typing.Annotated[m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]], created_at: datetime.datetime, deadline: datetime.datetime | None = None) -&gt; None`

A persisted, keyed set of elicitation requests awaiting responses.

Public fields and methods:

- `round_id: &lt;class 'str'&gt;` (required).
- `execution_id: &lt;class 'str'&gt;` (required).
- `logical_operation_id: &lt;class 'str'&gt;` (required).
- `server: &lt;class 'str'&gt;` (required).
- `operation_kind: typing.Literal['tool', 'prompt', 'resource']` (required).
- `operation_name: &lt;class 'str'&gt;` (required).
- `request_state: str | None` (default: `None`).
- `requests: collections.abc.Mapping[str, typing.Annotated[m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]]` (required).
- `created_at: &lt;class 'datetime.datetime'&gt;` (required).
- `deadline: datetime.datetime | None` (default: `None`).

### `UrlElicitationRequest`

`m3.async_api.UrlElicitationRequest(*, request_key: Annotated[str, MinLen(min_length=1)], mode: Literal['url'] = 'url', message: str, url: str, elicitation_id: str | None = None, meta: collections.abc.Mapping[str, object] | None = None, task: collections.abc.Mapping[str, object] | None = None, server: str | None = None, operation_kind: Optional[Literal['tool', 'prompt', 'resource']] = None, operation_name: str | None = None) -&gt; None`

A normalized URL-mode elicitation request.

Public fields and methods:

- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['url']` (default: `'url'`).
- `message: &lt;class 'str'&gt;` (required).
- `url: &lt;class 'str'&gt;` (required).
- `elicitation_id: str | None` (default: `None`).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).
- `task: collections.abc.Mapping[str, object] | None` (default: `None`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Optional[typing.Literal['tool', 'prompt', 'resource']]` (default: `None`).
- `operation_name: str | None` (default: `None`).

### `expect_form`

`m3.async_api.expect_form(request_key: 'str', *, message: 'str | None' = None, schema: 'Mapping[str, object] | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `expect_url`

`m3.async_api.expect_url(request_key: 'str', *, message: 'str | None' = None, url: 'str | None' = None, elicitation_id: 'str | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `maybe_form`

`m3.async_api.maybe_form(request_key: 'str', *, message: 'str | None' = None, schema: 'Mapping[str, object] | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `maybe_url`

`m3.async_api.maybe_url(request_key: 'str', *, message: 'str | None' = None, url: 'str | None' = None, elicitation_id: 'str | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `one_of`

`m3.async_api.one_of(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `optional`

`m3.async_api.optional(child: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `round_of`

`m3.async_api.round_of(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `sequence`

`m3.async_api.sequence(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `ACPTrace`

`m3.async_api.ACPTrace(*, kind: Literal['acp'] = 'acp', session_id: m3.observability.Observation[str] = &lt;factory&gt;, protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, agent_identity: m3.observability.Observation[JsonValue] = &lt;factory&gt;, available_modes: m3.observability.Observation[JsonValue] = &lt;factory&gt;, current_mode: m3.observability.Observation[str] = &lt;factory&gt;, config_options: m3.observability.Observation[JsonValue] = &lt;factory&gt;, selected_config: m3.observability.Observation[JsonValue] = &lt;factory&gt;, plan_state_available: m3.observability.Observation[bool] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['acp']` (default: `'acp'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `agent_identity: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `available_modes: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `current_mode: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `config_options: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `selected_config: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `plan_state_available: &lt;class 'm3.observability.Observation[bool]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `ArtifactEntry`

`m3.async_api.ArtifactEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['artifact'] = 'artifact', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), artifact: m3.types.ArtifactRef) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['artifact']` (default: `'artifact'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `artifact: &lt;class 'm3.types.ArtifactRef'&gt;` (required).

### `CaptureOptions`

`m3.async_api.CaptureOptions(*, capture_raw_evidence: bool = True, capture_provider_messages: bool = True, capture_stderr: bool = True, raw_preview_bytes: Annotated[int, Gt(gt=0)] = 65536, raw_frame_bytes: Annotated[int, Gt(gt=0)] = 1048576, raw_execution_bytes: Annotated[int, Gt(gt=0)] = 67108864) -&gt; None`

Boundaries for redacted provider/MCP evidence capture.

Public fields and methods:

- `capture_raw_evidence: &lt;class 'bool'&gt;` (default: `True`).
- `capture_provider_messages: &lt;class 'bool'&gt;` (default: `True`).
- `capture_stderr: &lt;class 'bool'&gt;` (default: `True`).
- `raw_preview_bytes: &lt;class 'int'&gt;` (default: `65536`).
- `raw_frame_bytes: &lt;class 'int'&gt;` (default: `1048576`).
- `raw_execution_bytes: &lt;class 'int'&gt;` (default: `67108864`).

### `ClaudeCodeTrace`

`m3.async_api.ClaudeCodeTrace(*, kind: Literal['claude_code'] = 'claude_code', session_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, result_subtype: m3.observability.Observation[str] = &lt;factory&gt;, stop_reason: m3.observability.Observation[str] = &lt;factory&gt;, service_tier: m3.observability.Observation[str] = &lt;factory&gt;, api_duration_ms: m3.observability.Observation[float] = &lt;factory&gt;, encrypted_reasoning: m3.observability.Observation[bool] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['claude_code']` (default: `'claude_code'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `result_subtype: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `stop_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `service_tier: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `api_duration_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `encrypted_reasoning: &lt;class 'm3.observability.Observation[bool]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `CodexTrace`

`m3.async_api.CodexTrace(*, kind: Literal['codex'] = 'codex', thread_id: m3.observability.Observation[str] = &lt;factory&gt;, turn_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, sandbox: m3.observability.Observation[str] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['codex']` (default: `'codex'`).
- `thread_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `turn_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `sandbox: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `CorrelationState`

`m3.async_api.CorrelationState(*values)`

### `DiagnosticEntry`

`m3.async_api.DiagnosticEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['diagnostic'] = 'diagnostic', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), code: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], message: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], stage: Annotated[str | None, MaxLen(max_length=128)] = None, operation: Annotated[str | None, MaxLen(max_length=256)] = None, elapsed_seconds: Annotated[float | None, Ge(ge=0)] = None, timeout_seconds: Annotated[float | None, Gt(gt=0)] = None) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['diagnostic']` (default: `'diagnostic'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `code: &lt;class 'str'&gt;` (required).
- `message: &lt;class 'str'&gt;` (required).
- `stage: str | None` (default: `None`).
- `operation: str | None` (default: `None`).
- `elapsed_seconds: float | None` (default: `None`).
- `timeout_seconds: float | None` (default: `None`).

### `DirectTrace`

`m3.async_api.DirectTrace(*, kind: Literal['direct'] = 'direct', transport: m3.observability.Observation[TransportKind] = &lt;factory&gt;, protocol: m3.observability.Observation[str] = &lt;factory&gt;, initialization: m3.observability.Observation[InitializationValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['direct']` (default: `'direct'`).
- `transport: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).
- `protocol: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `initialization: &lt;class 'm3.observability.Observation[InitializationValue]'&gt;` (required).

### `ElicitationEntry`

`m3.async_api.ElicitationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['elicitation'] = 'elicitation', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, operation_kind: Literal['tool', 'prompt', 'resource'], operation_name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], logical_operation_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], round_index: Annotated[int, Ge(ge=1)], request_key: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], mode: Literal['form', 'url'], message: m3.observability.Observation[str] = &lt;factory&gt;, requested_schema: m3.observability.Observation[JsonValue] = &lt;factory&gt;, url: m3.observability.Observation[str] = &lt;factory&gt;, elicitation_id: m3.observability.Observation[str] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, action: Optional[Literal['accept', 'decline', 'cancel']] = None, content: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

One keyed elicitation embedded in an MRTR input-required round.

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['elicitation']` (default: `'elicitation'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Literal['tool', 'prompt', 'resource']` (required).
- `operation_name: &lt;class 'str'&gt;` (required).
- `logical_operation_id: &lt;class 'str'&gt;` (required).
- `round_index: &lt;class 'int'&gt;` (required).
- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['form', 'url']` (required).
- `message: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `requested_schema: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `url: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `elicitation_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `action: typing.Optional[typing.Literal['accept', 'decline', 'cancel']]` (default: `None`).
- `content: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `EvaluationEntry`

`m3.async_api.EvaluationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['evaluation'] = 'evaluation', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), evaluation: m3.types.EvaluationResult) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['evaluation']` (default: `'evaluation'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `evaluation: &lt;class 'm3.types.EvaluationResult'&gt;` (required).

### `EvidenceCapture`

`m3.async_api.EvidenceCapture(*, reference: m3.types.EvidenceRef, preview: m3.observability.Observation[str], original_size_bytes: Annotated[int, Ge(ge=0)], stored_size_bytes: Annotated[int, Ge(ge=0)], redacted: bool, truncated: bool) -&gt; None`

Typed result of bounded, redacted raw-evidence capture.

Public fields and methods:

- `reference: &lt;class 'm3.types.EvidenceRef'&gt;` (required).
- `preview: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `original_size_bytes: &lt;class 'int'&gt;` (required).
- `stored_size_bytes: &lt;class 'int'&gt;` (required).
- `redacted: &lt;class 'bool'&gt;` (required).
- `truncated: &lt;class 'bool'&gt;` (required).

### `EvidenceConflict`

`m3.async_api.EvidenceConflict(*, field: Literal['server', 'tool', 'arguments', 'result', 'status'], reported: m3.observability.Observation[JsonValue], wire: m3.observability.Observation[JsonValue]) -&gt; None`

Public fields and methods:

- `field: typing.Literal['server', 'tool', 'arguments', 'result', 'status']` (required).
- `reported: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `wire: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `HttpExchange`

`m3.async_api.HttpExchange(*, method: Annotated[str, MinLen(min_length=1)], status_code: Annotated[int, Ge(ge=100), Le(le=599)], headers: tuple[m3.observability.SafeHttpHeader, ...] = ()) -&gt; None`

Public fields and methods:

- `method: &lt;class 'str'&gt;` (required).
- `status_code: &lt;class 'int'&gt;` (required).
- `headers: tuple[m3.observability.SafeHttpHeader, ...]` (default: `()`).

### `InitializationEntry`

`m3.async_api.InitializationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['initialization'] = 'initialization', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, server_name: m3.observability.Observation[str] = &lt;factory&gt;, server_version: m3.observability.Observation[str] = &lt;factory&gt;, instructions: m3.observability.Observation[str] = &lt;factory&gt;, capabilities: m3.observability.Observation[JsonValue] = &lt;factory&gt;, tools: m3.observability.Observation[tuple[ToolInfo, ...]] = &lt;factory&gt;, resources: m3.observability.Observation[tuple[ResourceInfo, ...]] = &lt;factory&gt;, resource_templates: m3.observability.Observation[tuple[TemplateInfo, ...]] = &lt;factory&gt;, prompts: m3.observability.Observation[tuple[PromptInfo, ...]] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['initialization']` (default: `'initialization'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `instructions: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `capabilities: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `tools: &lt;class 'm3.observability.Observation[tuple[ToolInfo, ...]]'&gt;` (required).
- `resources: &lt;class 'm3.observability.Observation[tuple[ResourceInfo, ...]]'&gt;` (required).
- `resource_templates: &lt;class 'm3.observability.Observation[tuple[TemplateInfo, ...]]'&gt;` (required).
- `prompts: &lt;class 'm3.observability.Observation[tuple[PromptInfo, ...]]'&gt;` (required).

### `InitializationValue`

`m3.async_api.InitializationValue(*, protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, server_name: m3.observability.Observation[str] = &lt;factory&gt;, server_version: m3.observability.Observation[str] = &lt;factory&gt;, instructions: m3.observability.Observation[str] = &lt;factory&gt;, capabilities: m3.observability.Observation[JsonValue] = &lt;factory&gt;, tools: m3.observability.Observation[tuple[ToolInfo, ...]] = &lt;factory&gt;, resources: m3.observability.Observation[tuple[ResourceInfo, ...]] = &lt;factory&gt;, resource_templates: m3.observability.Observation[tuple[TemplateInfo, ...]] = &lt;factory&gt;, prompts: m3.observability.Observation[tuple[PromptInfo, ...]] = &lt;factory&gt;) -&gt; None`

Value-only initialization metadata used by runtime information.

Public fields and methods:

- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `instructions: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `capabilities: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `tools: &lt;class 'm3.observability.Observation[tuple[ToolInfo, ...]]'&gt;` (required).
- `resources: &lt;class 'm3.observability.Observation[tuple[ResourceInfo, ...]]'&gt;` (required).
- `resource_templates: &lt;class 'm3.observability.Observation[tuple[TemplateInfo, ...]]'&gt;` (required).
- `prompts: &lt;class 'm3.observability.Observation[tuple[PromptInfo, ...]]'&gt;` (required).

### `InteractionEntry`

`m3.async_api.InteractionEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['interaction'] = 'interaction', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), interaction_kind: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], request: m3.observability.Observation[JsonValue] = &lt;factory&gt;, response: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['interaction']` (default: `'interaction'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `interaction_kind: &lt;class 'str'&gt;` (required).
- `request: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `response: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `LifecycleEntry`

`m3.async_api.LifecycleEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['lifecycle'] = 'lifecycle', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), phase: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)]) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['lifecycle']` (default: `'lifecycle'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `phase: &lt;class 'str'&gt;` (required).

### `MessageEntry`

`m3.async_api.MessageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['message'] = 'message', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), message_id: m3.observability.Observation[str] = &lt;factory&gt;, role: m3.observability.MessageRole = &lt;MessageRole.ASSISTANT: 'assistant'&gt;, content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = (), stop_reason: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['message']` (default: `'message'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `message_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `role: &lt;enum 'MessageRole'&gt;` (default: `&lt;MessageRole.ASSISTANT: 'assistant'&gt;`).
- `content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `stop_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `MessageRole`

`m3.async_api.MessageRole(*values)`

### `Observation`

`m3.async_api.Observation(*, state: m3.observability.ObservationState, value: Optional[~_T] = None, reason: m3.observability.ObservationReason | None = None, provenance: tuple[m3.types.EventSource, ...] = (), evidence_ref: m3.types.EvidenceRef | None = None) -&gt; None`

A typed value with explicit availability and provenance.

Public fields and methods:

- `state: &lt;enum 'ObservationState'&gt;` (required).
- `value: typing.Optional[~_T]` (default: `None`).
- `reason: m3.observability.ObservationReason | None` (default: `None`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `evidence_ref: m3.types.EvidenceRef | None` (default: `None`).

### `ObservationReason`

`m3.async_api.ObservationReason(*values)`

### `ObservationState`

`m3.async_api.ObservationState(*values)`

How completely a provider-dependent value was observed.

### `OpenCodeTrace`

`m3.async_api.OpenCodeTrace(*, kind: Literal['opencode'] = 'opencode', session_id: m3.observability.Observation[str] = &lt;factory&gt;, provider_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, http_lifecycle: m3.observability.Observation[JsonValue] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['opencode']` (default: `'opencode'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `provider_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `http_lifecycle: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `PiTrace`

`m3.async_api.PiTrace(*, kind: Literal['pi'] = 'pi', session_id: m3.observability.Observation[str] = &lt;factory&gt;, provider_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['pi']` (default: `'pi'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `provider_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `ProcessEntry`

`m3.async_api.ProcessEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['process'] = 'process', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), executable: m3.observability.Observation[str] = &lt;factory&gt;, pid: m3.observability.Observation[int] = &lt;factory&gt;, exit_code: m3.observability.Observation[int] = &lt;factory&gt;, signal: m3.observability.Observation[int] = &lt;factory&gt;, stderr: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['process']` (default: `'process'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `executable: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `pid: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `exit_code: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `signal: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `stderr: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `ProtocolCallAttempt`

`m3.async_api.ProtocolCallAttempt(*, attempt_index: Annotated[int, Ge(ge=0)], jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, continuation_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, operation_params: m3.observability.Observation[JsonValue] = &lt;factory&gt;, input_required: bool = False, result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, raw_result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.INCOMPLETE: 'incomplete'&gt;, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;) -&gt; None`

One wire-level attempt belonging to a prompt or resource call.

Public fields and methods:

- `attempt_index: &lt;class 'int'&gt;` (required).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `continuation_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `operation_params: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `input_required: &lt;class 'bool'&gt;` (default: `False`).
- `result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `raw_result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.INCOMPLETE: 'incomplete'&gt;`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).

### `ProtocolEntry`

`m3.async_api.ProtocolEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['protocol'] = 'protocol', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), protocol: m3.observability.ProtocolKind, method: m3.observability.Observation[str] = &lt;factory&gt;, direction: m3.types.EventDirection = &lt;EventDirection.INTERNAL: 'internal'&gt;, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request: m3.observability.Observation[JsonValue] = &lt;factory&gt;, response: m3.observability.Observation[JsonValue] = &lt;factory&gt;, error: m3.observability.Observation[ProtocolErrorInfo] = &lt;factory&gt;, http: m3.observability.Observation[HttpExchange] = &lt;factory&gt;, operation_kind: Optional[Literal['prompt', 'resource']] = None, operation_name: m3.observability.Observation[str] = &lt;factory&gt;, attempts: tuple[m3.observability.ProtocolCallAttempt, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['protocol']` (default: `'protocol'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `protocol: &lt;enum 'ProtocolKind'&gt;` (required).
- `method: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `direction: &lt;enum 'EventDirection'&gt;` (default: `&lt;EventDirection.INTERNAL: 'internal'&gt;`).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `response: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `error: &lt;class 'm3.observability.Observation[ProtocolErrorInfo]'&gt;` (required).
- `http: &lt;class 'm3.observability.Observation[HttpExchange]'&gt;` (required).
- `operation_kind: typing.Optional[typing.Literal['prompt', 'resource']]` (default: `None`).
- `operation_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `attempts: tuple[m3.observability.ProtocolCallAttempt, ...]` (default: `()`).

### `ProtocolErrorInfo`

`m3.async_api.ProtocolErrorInfo(*, code: int | str | None = None, message: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], data: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `code: int | str | None` (default: `None`).
- `message: &lt;class 'str'&gt;` (required).
- `data: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `ProtocolKind`

`m3.async_api.ProtocolKind(*values)`

### `ProviderEntry`

`m3.async_api.ProviderEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['provider'] = 'provider', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), provider: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], category: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], data: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['provider']` (default: `'provider'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `provider: &lt;class 'str'&gt;` (required).
- `category: &lt;class 'str'&gt;` (required).
- `data: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `RawEvidence`

`m3.async_api.RawEvidence(*, reference: m3.types.EvidenceRef, media_type: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], content: Union[JsonValue, str], size_bytes: Annotated[int, Ge(ge=0)], returned_size_bytes: Annotated[int, Ge(ge=0)], truncated: bool = False, redacted: Literal[True] = True) -&gt; None`

Public fields and methods:

- `reference: &lt;class 'm3.types.EvidenceRef'&gt;` (required).
- `media_type: &lt;class 'str'&gt;` (required).
- `content: typing.Union[JsonValue, str]` (required).
- `size_bytes: &lt;class 'int'&gt;` (required).
- `returned_size_bytes: &lt;class 'int'&gt;` (required).
- `truncated: &lt;class 'bool'&gt;` (default: `False`).
- `redacted: typing.Literal[True]` (default: `True`).

### `RawEvidenceSource`

`m3.async_api.RawEvidenceSource(*values)`

### `RawMessageEntry`

`m3.async_api.RawMessageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['raw_message'] = 'raw_message', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), source: m3.observability.RawEvidenceSource, direction: m3.types.EventDirection = &lt;EventDirection.INTERNAL: 'internal'&gt;, media_type: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], preview: m3.observability.Observation[Union[JsonValue, str]] = &lt;factory&gt;, evidence_ref: m3.types.EvidenceRef | None = None, size_bytes: Annotated[int, Ge(ge=0)] = 0, redacted: Literal[True] = True) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['raw_message']` (default: `'raw_message'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `source: &lt;enum 'RawEvidenceSource'&gt;` (required).
- `direction: &lt;enum 'EventDirection'&gt;` (default: `&lt;EventDirection.INTERNAL: 'internal'&gt;`).
- `media_type: &lt;class 'str'&gt;` (required).
- `preview: &lt;class 'm3.observability.Observation[Union[JsonValue, str]]'&gt;` (required).
- `evidence_ref: m3.types.EvidenceRef | None` (default: `None`).
- `size_bytes: &lt;class 'int'&gt;` (default: `0`).
- `redacted: typing.Literal[True]` (default: `True`).

### `ReasoningEntry`

`m3.async_api.ReasoningEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['reasoning'] = 'reasoning', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), block_id: m3.observability.Observation[str] = &lt;factory&gt;, content: m3.observability.Observation[tuple[Annotated[Union[TextContent, FileContent, ImageContent, AudioContent, ResourceLink, OpaqueContent], FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['reasoning']` (default: `'reasoning'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `block_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `content: &lt;class 'm3.observability.Observation[tuple[Annotated[Union[TextContent, FileContent, ImageContent, AudioContent, ResourceLink, OpaqueContent], FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]]'&gt;` (required).

### `ReportedToolCall`

`m3.async_api.ReportedToolCall(*, provider_call_id: m3.observability.Observation[str] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `provider_call_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `RuntimeTraceInfo`

`m3.async_api.RuntimeTraceInfo(*args, **kwargs)`

Runtime representation of an annotated type.

### `SafeHttpHeader`

`m3.async_api.SafeHttpHeader(*, name: Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id'], value: str) -&gt; None`

Public fields and methods:

- `name: typing.Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id']` (required).
- `value: &lt;class 'str'&gt;` (required).

### `ToolCallAttempt`

`m3.async_api.ToolCallAttempt(*, attempt_index: Annotated[int, Ge(ge=0)], jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, continuation_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, operation_params: m3.observability.Observation[JsonValue] = &lt;factory&gt;, input_required: bool = False, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, raw_result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.ToolCallStatus = &lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;) -&gt; None`

One wire-level attempt belonging to a logical tool call.

Public fields and methods:

- `attempt_index: &lt;class 'int'&gt;` (required).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `continuation_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `operation_params: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `input_required: &lt;class 'bool'&gt;` (default: `False`).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `raw_result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;enum 'ToolCallStatus'&gt;` (default: `&lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).

### `ToolCallEntry`

`m3.async_api.ToolCallEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['tool_call'] = 'tool_call', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), call_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], provider_call_id: m3.observability.Observation[str] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, tool_status: m3.observability.ToolCallStatus = &lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;, correlation: m3.observability.CorrelationState = &lt;CorrelationState.UNAVAILABLE: 'unavailable'&gt;, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, server_latency_ms: m3.observability.Observation[float] = &lt;factory&gt;, policy: m3.observability.Observation[ToolPolicyDecision] = &lt;factory&gt;, reported: m3.observability.Observation[ReportedToolCall] = &lt;factory&gt;, wire: m3.observability.Observation[WireToolCall] = &lt;factory&gt;, conflicts: tuple[m3.observability.EvidenceConflict, ...] = (), attempts: tuple[m3.observability.ToolCallAttempt, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['tool_call']` (default: `'tool_call'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `call_id: &lt;class 'str'&gt;` (required).
- `provider_call_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `tool_status: &lt;enum 'ToolCallStatus'&gt;` (default: `&lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;`).
- `correlation: &lt;enum 'CorrelationState'&gt;` (default: `&lt;CorrelationState.UNAVAILABLE: 'unavailable'&gt;`).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `server_latency_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `policy: &lt;class 'm3.observability.Observation[ToolPolicyDecision]'&gt;` (required).
- `reported: &lt;class 'm3.observability.Observation[ReportedToolCall]'&gt;` (required).
- `wire: &lt;class 'm3.observability.Observation[WireToolCall]'&gt;` (required).
- `conflicts: tuple[m3.observability.EvidenceConflict, ...]` (default: `()`).
- `attempts: tuple[m3.observability.ToolCallAttempt, ...]` (default: `()`).

### `ToolCallStatus`

`m3.async_api.ToolCallStatus(*values)`

### `ToolResult`

`m3.async_api.ToolResult(*, content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = (), structured_content: m3.observability.Observation[JsonValue] = &lt;factory&gt;, is_error: bool = False, error: m3.observability.Observation[ErrorInfo] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `structured_content: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `is_error: &lt;class 'bool'&gt;` (default: `False`).
- `error: &lt;class 'm3.observability.Observation[ErrorInfo]'&gt;` (required).

### `TraceEntry`

`m3.async_api.TraceEntry(*args, **kwargs)`

Runtime representation of an annotated type.

### `TraceEntryBase`

`m3.async_api.TraceEntryBase(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Annotated[str, MinLen(min_length=1), MaxLen(max_length=64)], parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: &lt;class 'str'&gt;` (required).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).

### `TraceStatus`

`m3.async_api.TraceStatus(*values)`

### `TraceSummary`

`m3.async_api.TraceSummary(*, timing: m3.observability.TraceTiming = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;, turn_count: Annotated[int, Ge(ge=0)] = 0, message_count: Annotated[int, Ge(ge=0)] = 0, reasoning_count: Annotated[int, Ge(ge=0)] = 0, tool_call_count: Annotated[int, Ge(ge=0)] = 0, successful_tool_call_count: Annotated[int, Ge(ge=0)] = 0, failed_tool_call_count: Annotated[int, Ge(ge=0)] = 0, protocol_error_count: Annotated[int, Ge(ge=0)] = 0, activity_health: m3.types.ActivityHealth = &lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;, cleanup_status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;) -&gt; None`

Public fields and methods:

- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).
- `turn_count: &lt;class 'int'&gt;` (default: `0`).
- `message_count: &lt;class 'int'&gt;` (default: `0`).
- `reasoning_count: &lt;class 'int'&gt;` (default: `0`).
- `tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `successful_tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `failed_tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `protocol_error_count: &lt;class 'int'&gt;` (default: `0`).
- `activity_health: &lt;enum 'ActivityHealth'&gt;` (default: `&lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;`).
- `cleanup_status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).

### `TraceTiming`

`m3.async_api.TraceTiming(*, started_at: datetime.datetime = &lt;factory&gt;, finished_at: datetime.datetime | None = None, start_offset_ms: Annotated[float, Ge(ge=0)] = 0, end_offset_ms: Annotated[float, Ge(ge=0)] = 0, duration_ms: Annotated[float, Ge(ge=0)] = 0) -&gt; None`

Public fields and methods:

- `started_at: &lt;class 'datetime.datetime'&gt;` (required).
- `finished_at: datetime.datetime | None` (default: `None`).
- `start_offset_ms: &lt;class 'float'&gt;` (default: `0`).
- `end_offset_ms: &lt;class 'float'&gt;` (default: `0`).
- `duration_ms: &lt;class 'float'&gt;` (default: `0`).

### `TraceView`

`m3.async_api.TraceView(*, schema_id: Literal['m3.trace_view'] = 'm3.trace_view', schema_version: Literal['1.1', '1.2'] = '1.1', trace_id: m3.types.TraceId, execution_id: m3.types.ExecutionId, outcome: m3.types.ExecutionOutcome = &lt;ExecutionOutcome.COMPLETED: 'completed'&gt;, completeness: Literal['complete', 'partial'] = 'complete', limitations: tuple[str, ...] = (), agent: m3.types.AgentIdentity | None = None, runtime: m3.observability.DirectTrace | m3.observability.OpenCodeTrace | m3.observability.ClaudeCodeTrace | m3.observability.CodexTrace | m3.observability.PiTrace | m3.observability.ACPTrace = &lt;factory&gt;, summary: m3.observability.TraceSummary = &lt;factory&gt;, timeline: tuple[typing.Annotated[m3.observability.LifecycleEntry | m3.observability.MessageEntry | m3.observability.ReasoningEntry | m3.observability.ToolCallEntry | m3.observability.ProtocolEntry | m3.observability.TransportEntry | m3.observability.InitializationEntry | m3.observability.UsageEntry | m3.observability.InteractionEntry | m3.observability.ElicitationEntry | m3.observability.ProcessEntry | m3.observability.WorkspaceEntry | m3.observability.ArtifactEntry | m3.observability.EvaluationEntry | m3.observability.DiagnosticEntry | m3.observability.RawMessageEntry | m3.observability.ProviderEntry, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = ()) -&gt; None`

Public fields and methods:

- `schema_id: typing.Literal['m3.trace_view']` (default: `'m3.trace_view'`).
- `schema_version: typing.Literal['1.1', '1.2']` (default: `'1.1'`).
- `trace_id: &lt;class 'm3.types.TraceId'&gt;` (required).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `outcome: &lt;enum 'ExecutionOutcome'&gt;` (default: `&lt;ExecutionOutcome.COMPLETED: 'completed'&gt;`).
- `completeness: typing.Literal['complete', 'partial']` (default: `'complete'`).
- `limitations: tuple[str, ...]` (default: `()`).
- `agent: m3.types.AgentIdentity | None` (default: `None`).
- `runtime: m3.observability.DirectTrace | m3.observability.OpenCodeTrace | m3.observability.ClaudeCodeTrace | m3.observability.CodexTrace | m3.observability.PiTrace | m3.observability.ACPTrace` (required).
- `summary: &lt;class 'm3.observability.TraceSummary'&gt;` (required).
- `timeline: tuple[typing.Annotated[m3.observability.LifecycleEntry | m3.observability.MessageEntry | m3.observability.ReasoningEntry | m3.observability.ToolCallEntry | m3.observability.ProtocolEntry | m3.observability.TransportEntry | m3.observability.InitializationEntry | m3.observability.UsageEntry | m3.observability.InteractionEntry | m3.observability.ElicitationEntry | m3.observability.ProcessEntry | m3.observability.WorkspaceEntry | m3.observability.ArtifactEntry | m3.observability.EvaluationEntry | m3.observability.DiagnosticEntry | m3.observability.RawMessageEntry | m3.observability.ProviderEntry, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `for_turn(self, turn: '_TurnResult | _TurnState | _TurnId | str') -&gt; 'TraceView'`: Return the finalized evidence belonging to one turn.
- `for_session(self, session_id: '_SessionId | str') -&gt; 'TraceView'`
- `for_server(self, server_binding: 'str') -&gt; 'TraceView'`
- `between(self, start_offset_ms: 'float', end_offset_ms: 'float') -&gt; 'TraceView'`

### `TransportEntry`

`m3.async_api.TransportEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['transport'] = 'transport', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), phase: Literal['connected', 'disconnected'], configured: m3.observability.Observation[TransportKind] = &lt;factory&gt;, instrumented: m3.observability.Observation[TransportKind] = &lt;factory&gt;) -&gt; None`

A stable MCP transport lifecycle observation.

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['transport']` (default: `'transport'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `phase: typing.Literal['connected', 'disconnected']` (required).
- `configured: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).
- `instrumented: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).

### `UsageEntry`

`m3.async_api.UsageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['usage'] = 'usage', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), input_tokens: m3.observability.Observation[int] = &lt;factory&gt;, output_tokens: m3.observability.Observation[int] = &lt;factory&gt;, reasoning_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_creation_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_read_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_write_tokens: m3.observability.Observation[int] = &lt;factory&gt;, total_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cost: m3.observability.Observation[float] = &lt;factory&gt;, currency: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['usage']` (default: `'usage'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `input_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `output_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `reasoning_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_creation_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_read_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_write_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `total_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cost: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `currency: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `UsageValue`

`m3.async_api.UsageValue(*, input_tokens: m3.observability.Observation[int] = &lt;factory&gt;, output_tokens: m3.observability.Observation[int] = &lt;factory&gt;, reasoning_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_creation_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_read_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_write_tokens: m3.observability.Observation[int] = &lt;factory&gt;, total_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cost: m3.observability.Observation[float] = &lt;factory&gt;, currency: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Value-only usage aggregate used by summaries and runtime metadata.

Public fields and methods:

- `input_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `output_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `reasoning_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_creation_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_read_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_write_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `total_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cost: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `currency: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `WireToolCall`

`m3.async_api.WireToolCall(*, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, latency_ms: m3.observability.Observation[float] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `latency_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).

### `WorkspaceEntry`

`m3.async_api.WorkspaceEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['workspace'] = 'workspace', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), change: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['workspace']` (default: `'workspace'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `change: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

## `m3.matchers`

### `CheckGroup`

`m3.matchers.CheckGroup(redaction_config: '_RedactionConfig | None' = None) -&gt; 'None'`

Collect assertion failures and report them together on context exit.

Public fields and methods:

- `expect(self, subject: '_Any') -&gt; 'Expectation[_Any]'`

### `Expectation`

`m3.matchers.Expectation(subject: '_SubjectT', sink: '_FailureSink | None' = None, redaction_config: '_RedactionConfig | None' = None) -&gt; 'None'`

One-shot assertion facade; optional pytest recording persists checks.

Public fields and methods:

- `to_have_text(self, expected: 'str') -&gt; 'None'`
- `to_have_text_containing(self, expected: 'str') -&gt; 'None'`
- `to_have_text_matching(self, pattern: 'str', *, flags: 'int' = 0) -&gt; 'None'`
- `to_have_text_matching_regex(self, pattern: 'str', *, flags: 'int' = 0) -&gt; 'None'`
- `to_not_have_text(self, unexpected: 'str', *, current_snapshot: 'bool' = False) -&gt; 'None'`
- `to_not_have_text_containing(self, unexpected: 'str', *, current_snapshot: 'bool' = False) -&gt; 'None'`
- `to_have_structured_content(self, expected: 'object') -&gt; 'None'`
- `to_have_content(self, expected: '_Any', *, ordered: 'bool' = True) -&gt; 'None'`
- `to_have_ordered_content(self, expected: '_Any') -&gt; 'None'`
- `to_have_unordered_content(self, expected: '_Any') -&gt; 'None'`
- `to_have_tool_calls(self, expected: '_Sequence[str]', *, ordered: 'bool' = True, server: 'str | None' = None, turn: '_TurnSelector | None' = None, evidence: "_Literal['wire', 'reported', 'any']" = 'wire', current_snapshot: 'bool' = False) -&gt; 'None'`: Match the complete list of tool names, including repeated calls.
- `to_have_tool_call(self, tool: 'str | None' = None, *, name: 'str | None' = None, server: 'str | None' = None, arguments: '_Any' = None, arguments_partial: 'bool' = False, arguments_unordered: 'bool' = False, arguments_regex: 'bool' = False, arguments_tolerance: 'float | None' = None, argument_predicate: '_Callable[[_Any], bool] | None' = None, result: '_Any' = None, result_partial: 'bool' = False, arguments_observed_null: 'bool' = False, result_observed_null: 'bool' = False, status: 'str | None' = None, count: 'int | None' = None, min_count: 'int | None' = None, max_count: 'int | None' = None, turn: '_TurnSelector | None' = None, predicate: '_Callable[[_Mapping[str, _Any]], bool] | None' = None, server_name: 'str | None' = None, evidence: "_Literal['wire', 'reported', 'any']" = 'wire', min_latency_ms: 'float | None' = None, max_latency_ms: 'float | None' = None, current_snapshot: 'bool' = False) -&gt; 'None'`
- `to_not_have_tool_call(self, tool: 'str | None' = None, *, current_snapshot: 'bool' = False, **kwargs: '_Any') -&gt; 'None'`
- `to_have_no_tool_call(self, tool: 'str | None' = None, *, current_snapshot: 'bool' = False, **kwargs: '_Any') -&gt; 'None'`
- `to_have_reported_tool_call(self, tool: 'str | None' = None, **kwargs: '_Any') -&gt; 'None'`: Explicitly match provider/harness-reported calls without wire evidence.
- `to_have_duration(self, expected_ms: 'float | None' = None, *, min_ms: 'float | None' = None, max_ms: 'float | None' = None, tolerance_ms: 'float' = 0.0) -&gt; 'None'`
- `to_have_duration_between(self, min_ms: 'float', max_ms: 'float') -&gt; 'None'`
- `to_have_lifecycle(self, lifecycle: '_ExecutionStatus | _TurnStatus | str') -&gt; 'None'`
- `to_have_outcome(self, outcome: '_ExecutionOutcome | str') -&gt; 'None'`
- `to_be_completed(self) -&gt; 'None'`
- `to_have_terminal_outcome(self, outcome: '_ExecutionOutcome | str') -&gt; 'None'`
- `to_have_error(self, expected: '_Any' = None) -&gt; 'None'`
- `to_have_protocol_version(self, version: 'str') -&gt; 'None'`
- `to_have_transport(self, transport: 'str') -&gt; 'None'`
- `to_have_capability(self, name: 'str', *, status: '_CapabilityStatus | str | None' = None) -&gt; 'None'`
- `to_have_artifact(self, artifact: '_ArtifactRef | str', *, name: 'str | None' = None) -&gt; 'None'`
- `to_have_workspace_diff(self, expected: '_Any' = None, *, added: '_Any' = None, modified: '_Any' = None, deleted: '_Any' = None) -&gt; 'None'`: Assert the generic workspace projection exposed by a result/handle.
- `to_have_trace(self, *, completeness: 'str | None' = None, limitation: 'str | None' = None) -&gt; 'None'`
- `to_have_event(self, kind: '_EventKind | str', *, count: 'int | None' = None) -&gt; 'None'`
- `to_eventually(self, matcher: '_Callable[[_Any], None]', *, timeout: 'float' = 5.0) -&gt; 'None'`

### `check`

`m3.matchers.check(*, redaction_config: '_RedactionConfig | None' = None) -&gt; 'CheckGroup'`

### `expect`

`m3.matchers.expect(subject: '_SubjectT', *, redaction_config: '_RedactionConfig | None' = None) -&gt; 'Expectation[_SubjectT]'`

## `m3.testing`

### `ArtifactIntegrityError`

`m3.testing.ArtifactIntegrityError`

A recorded artifact does not match its content-addressed metadata.

### `ExpectedCall`

`m3.testing.ExpectedCall(method: 'str', matcher: '_Mapping[str, _Any]' = &lt;factory&gt;, response: '_Any' = None, optional_call: 'bool' = False, minimum: 'int' = 1, maximum: 'int | None' = 1, subset_match: 'bool' = False, unordered_group: 'str | None' = None, fallback_call: 'bool' = False, calls: 'int' = 0) -&gt; None`

ExpectedCall(method: 'str', matcher: '_Mapping[str, _Any]' = &lt;factory&gt;, response: '_Any' = None, optional_call: 'bool' = False, minimum: 'int' = 1, maximum: 'int | None' = 1, subset_match: 'bool' = False, unordered_group: 'str | None' = None, fallback_call: 'bool' = False, calls: 'int' = 0)

Public fields and methods:

- `optional(self) -&gt; 'ExpectedCall'`
- `repeated(self, minimum: 'int' = 0, maximum: 'int | None' = None) -&gt; 'ExpectedCall'`
- `subset(self) -&gt; 'ExpectedCall'`
- `unordered(self, group: 'str' = 'default') -&gt; 'ExpectedCall'`
- `fallback(self) -&gt; 'ExpectedCall'`
- `returns(self, value: '_Any') -&gt; 'ExpectedCall'`

### `FaultInjector`

`m3.testing.FaultInjector(delays: 'dict[str, float]' = &lt;factory&gt;, gates: 'dict[str, Gate]' = &lt;factory&gt;, disconnect_methods: 'set[str]' = &lt;factory&gt;, malformed_methods: 'set[str]' = &lt;factory&gt;, partial_methods: 'set[str]' = &lt;factory&gt;, invalid_schema_methods: 'set[str]' = &lt;factory&gt;, invalid_result_methods: 'set[str]' = &lt;factory&gt;, protocol_errors: 'dict[str, tuple[int, str]]' = &lt;factory&gt;, oversized_methods: 'dict[str, int]' = &lt;factory&gt;, reordered_methods: 'set[str]' = &lt;factory&gt;, duplicate_id_methods: 'set[str]' = &lt;factory&gt;, cancel_before_methods: 'set[str]' = &lt;factory&gt;, process_crash_methods: 'set[str]' = &lt;factory&gt;, race_gates: 'dict[str, Gate]' = &lt;factory&gt;, reorder_window: 'float' = 0.01) -&gt; None`

Deterministic faults for decoded in-process or literal stdio fixtures.

Public fields and methods:

- `delay(self, method: 'str', seconds: 'float') -&gt; 'FaultInjector'`
- `hang(self, method: 'str', gate: 'Gate | None' = None) -&gt; 'Gate'`
- `disconnect(self, method: 'str') -&gt; 'FaultInjector'`
- `malformed(self, method: 'str') -&gt; 'FaultInjector'`
- `partial_frame(self, method: 'str') -&gt; 'FaultInjector'`
- `invalid_schema(self, method: 'str') -&gt; 'FaultInjector'`
- `invalid_result(self, method: 'str') -&gt; 'FaultInjector'`: Advertise an output schema and return a violating structured result.
- `duplicate_id(self, method: 'str') -&gt; 'FaultInjector'`
- `reorder(self, method: 'str') -&gt; 'FaultInjector'`
- `oversized(self, method: 'str', size: 'int') -&gt; 'FaultInjector'`
- `cancel_before_dispatch(self, method: 'str') -&gt; 'FaultInjector'`
- `process_crash(self, method: 'str') -&gt; 'FaultInjector'`: Exit the dedicated stdio fixture with a non-zero status.
- `cancellation_race(self, method: 'str') -&gt; 'FaultInjector'`
- `race_gate(self, method: 'str') -&gt; 'Gate'`: Return the gate deciding whether a raced response is released.
- `protocol_error(self, method: 'str', code: 'int' = -32000, message: 'str' = 'mock protocol error') -&gt; 'FaultInjector'`
- `stdio_server(self, *, name: 'str' = 'wire-fault') -&gt; '_StdioServer'`: Return a safe stdio fixture that applies literal wire faults.
- `close_fixture(self) -&gt; 'None'`
- `before(self, method: 'str') -&gt; 'None'`
- `response(self, method: 'str', value: '_Any') -&gt; '_Any'`: Apply deterministic response-shape faults after handler output.

### `Gate`

`m3.testing.Gate(*, open: 'bool' = False) -&gt; 'None'`

Deterministic async gate for delay and hang tests.

Public fields and methods:

- `open(self) -&gt; 'None'`
- `release(self) -&gt; 'None'`
- `close(self) -&gt; 'None'`
- `wait(self, *, poll_seconds: 'float' = 0.001) -&gt; 'None'`
- `wait_async(self, *, poll_seconds: 'float' = 0.001) -&gt; 'None'`: Alias with an explicit name for composing deterministic races.

### `MockExpectationError`

`m3.testing.MockExpectationError`

A scripted mock interaction did not match.

### `MockMCPServer`

`m3.testing.MockMCPServer(name: 'str' = 'mock-mcp', *, version: 'str' = '1', strict: 'bool' = True, redaction_config: '_RedactionConfig | None' = None, faults: 'FaultInjector | None' = None, state: '_Mapping[str, _Any] | None' = None) -&gt; 'None'`

Decorator-defined and stateful official in-process MCP server.

Public fields and methods:

- `tool(self, name: 'str | _Callable[..., _Any] | None' = None, *, description: 'str | None' = None, input_schema: '_Mapping[str, _Any] | None' = None, output_schema: '_Mapping[str, _Any] | None' = None) -&gt; '_Any'`
- `resource(self, uri: 'str', *, name: 'str | None' = None, mime_type: 'str | None' = None) -&gt; '_Callable[[_Callable[_P, _R]], _Callable[_P, _R]]'`
- `resource_template(self, uri_template: 'str', *, name: 'str | None' = None, description: 'str | None' = None, mime_type: 'str | None' = None) -&gt; '_types.ResourceTemplate'`: Register a resource template exposed by ``resources/templates/list``.
- `prompt(self, name: 'str | _Callable[..., _Any] | None' = None, *, description: 'str | None' = None) -&gt; '_Any'`
- `expect(self, method: 'str', *, optional: 'bool' = False, repeat: 'int | tuple[int, int | None] | None' = None, subset: 'bool' = False, unordered: 'str | None' = None, fallback: 'bool' = False, **matcher: '_Any') -&gt; 'ExpectedCall'`
- `expect_call(self, method: 'str', *, optional: 'bool' = False, repeat: 'int | tuple[int, int | None] | None' = None, subset: 'bool' = False, unordered: 'str | None' = None, fallback: 'bool' = False, **matcher: '_Any') -&gt; 'ExpectedCall'`
- `expect_tool_call(self, name: 'str', arguments: '_Mapping[str, _Any] | None' = None, **options: '_Any') -&gt; 'ExpectedCall'`
- `server(self) -&gt; '_Server'`
- `in_process(self, *, name: 'str | None' = None) -&gt; '_InProcessServer'`: Return a decoded ``SessionMessage`` in-process fixture.
- `verify(self) -&gt; 'None'`
- `recording(self) -&gt; 'Recording'`
- `close(self) -&gt; 'None'`

### `MockProtocolError`

`m3.testing.MockProtocolError(code: 'int', message: 'str') -&gt; 'None'`

### `RecordedArtifact`

`m3.testing.RecordedArtifact(artifact_id: 'str', sha256: 'str', size_bytes: 'int', media_type: 'str | None' = None) -&gt; None`

Content-addressed artifact metadata carried by a recording.

Public fields and methods:

- `from_bytes(cls, artifact_id: 'str', content: 'bytes', *, media_type: 'str | None' = None) -&gt; 'RecordedArtifact'`
- `validate(self, content: 'bytes') -&gt; 'None'`
- `model(self) -&gt; 'dict[str, _JsonValue]'`

### `RecordedInteraction`

`m3.testing.RecordedInteraction(method: 'str', params: '_Mapping[str, _JsonValue]', response: '_JsonValue | None' = None, error: '_Mapping[str, _JsonValue] | None' = None, sequence: 'int' = 0, provenance: 'tuple[str, ...]' = ()) -&gt; None`

RecordedInteraction(method: 'str', params: '_Mapping[str, _JsonValue]', response: '_JsonValue | None' = None, error: '_Mapping[str, _JsonValue] | None' = None, sequence: 'int' = 0, provenance: 'tuple[str, ...]' = ())

Public fields and methods:

- `model(self) -&gt; 'dict[str, _JsonValue]'`

### `Recording`

`m3.testing.Recording(interactions: 'tuple[RecordedInteraction, ...]', redaction_bound: 'bool', provenance: 'tuple[str, ...]' = (), server_name: 'str' = 'mock-mcp', server_version: 'str' = '1', initialization: '_Mapping[str, _JsonValue]' = &lt;factory&gt;, artifacts: 'tuple[RecordedArtifact, ...]' = (), redaction_bindings: 'tuple[RedactionBinding, ...]' = &lt;factory&gt;, _runtime: '_RedactionRuntime' = &lt;factory&gt;) -&gt; None`

Recording(interactions: 'tuple[RecordedInteraction, ...]', redaction_bound: 'bool', provenance: 'tuple[str, ...]' = (), server_name: 'str' = 'mock-mcp', server_version: 'str' = '1', initialization: '_Mapping[str, _JsonValue]' = &lt;factory&gt;, artifacts: 'tuple[RecordedArtifact, ...]' = (), redaction_bindings: 'tuple[RedactionBinding, ...]' = &lt;factory&gt;, _runtime: '_RedactionRuntime' = &lt;factory&gt;)

Public fields and methods:

- `with_redaction_config(self, config: '_RedactionConfig') -&gt; 'Recording'`: Bind an explicit in-process redaction config without serializing secrets.
- `validate_artifacts(self, contents: '_Mapping[str, bytes]') -&gt; 'None'`: Validate every supplied artifact against the recorded digest/length.
- `model_dump(self, *, config: '_RedactionConfig | None' = None) -&gt; 'dict[str, _JsonValue]'`
- `to_json(self, *, config: '_RedactionConfig | None' = None) -&gt; 'str'`
- `redacted(self, *, config: '_RedactionConfig | None' = None) -&gt; 'Recording'`: Return a detached recording whose persisted values are redacted.
- `from_json(cls, value: 'str') -&gt; 'Recording'`

### `RedactionBinding`

`m3.testing.RedactionBinding(source: 'str' = 'explicit', version: 'str' = '1', wildcard_paths: 'tuple[str, ...]' = (), secret_references: 'tuple[str, ...]' = ()) -&gt; None`

Serializable redaction provenance without secret values.

Public fields and methods:

- `model(self) -&gt; 'dict[str, _JsonValue]'`

### `ReplayMismatch`

`m3.testing.ReplayMismatch`

A strict replay request differs from the recorded interaction.

### `ReplayServer`

`m3.testing.ReplayServer(recording: 'Recording', *, strict: 'bool' = True, redaction_config: '_RedactionConfig | None' = None, artifacts: '_Mapping[str, bytes] | None' = None, expected_server: 'tuple[str, str] | None' = None) -&gt; 'None'`

Strict JSON-only replay matcher.

Public fields and methods:

- `match(self, method: 'str', params: '_Mapping[str, _Any]') -&gt; 'RecordedInteraction'`
- `verify_replay(self) -&gt; 'None'`

### `VirtualClock`

`m3.testing.VirtualClock(start: 'float' = 0.0) -&gt; 'None'`

A process-local virtual clock with awaitable deterministic sleeps.

Public fields and methods:

- `sleep(self, duration: 'float') -&gt; 'None'`
- `advance(self, duration: 'float') -&gt; 'float'`

## `m3.evaluations`

### `AsyncEvaluator`

`m3.evaluations.AsyncEvaluator(*args, **kwargs)`

### `EvaluationDecision`

`m3.evaluations.EvaluationDecision(*, status: m3.types.EvaluationStatus, score: float | None = None, rationale: str | None = None, metrics: collections.abc.Mapping[str, float] = &lt;factory&gt;, provenance: m3.types.EvaluationSource | None = None, details: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;) -&gt; None`

Structured evaluator output, compatible with scalar verdicts.

Public fields and methods:

- `status: &lt;enum 'EvaluationStatus'&gt;` (required).
- `score: float | None` (default: `None`).
- `rationale: str | None` (default: `None`).
- `metrics: collections.abc.Mapping[str, float]` (required).
- `provenance: m3.types.EvaluationSource | None` (default: `None`).
- `details: collections.abc.Mapping[str, typing.Any]` (required).

### `EvaluationRunner`

`m3.evaluations.EvaluationRunner(*, registry: 'EvaluatorRegistry | None' = None, store: 'EvaluationStore | None' = None, durable_store: '_Any | None' = None, redaction_config: '_RedactionConfig | None' = None, max_judge_requests: 'int | None' = None, run_id: 'str | None' = None) -&gt; 'None'`

Run and persist deterministic evaluations in a separate store.

Public fields and methods:

- `register(self, name: 'str', evaluator: 'EvaluatorCallable | AsyncEvaluator') -&gt; 'EvaluatorRegistration'`
- `evaluate(self, subject: '_Any', evaluator: 'str | EvaluatorCallable', *, required: 'bool' = False, goal: 'str | None' = None, trace: '_TraceResult | None' = None, artifacts: '_Sequence[_ArtifactRef]' = (), metadata: '_Mapping[str, str | int | float | bool | None] | None' = None, evaluation_id: '_EvaluationId | str | None' = None, execution_id: '_ExecutionId | str | None' = None, turn_id: '_TurnId | str | None' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`
- `evaluate_async(self, subject: '_Any', evaluator: 'str | _Any', *, required: 'bool' = False, goal: 'str | None' = None, trace: '_TraceResult | None' = None, artifacts: '_Sequence[_ArtifactRef]' = (), metadata: '_Mapping[str, str | int | float | bool | None] | None' = None, evaluation_id: '_EvaluationId | str | None' = None, execution_id: '_ExecutionId | str | None' = None, turn_id: '_TurnId | str | None' = None, case_id: 'str | None' = None) -&gt; '_EvaluationResult'`
- `results(self) -&gt; 'tuple[_EvaluationResult, ...]'`

### `EvaluationStore`

`m3.evaluations.EvaluationStore(*args, **kwargs)`

Public fields and methods:

- `save(self, result: '_EvaluationResult') -&gt; 'None'`
- `get(self, evaluation_id: '_EvaluationId | str') -&gt; '_EvaluationResult | None'`
- `all(self) -&gt; 'tuple[_EvaluationResult, ...]'`

### `EvaluationVerdict`

`m3.evaluations.EvaluationVerdict`

Represent a PEP 604 union type

### `Evaluator`

`m3.evaluations.Evaluator(*args, **kwargs)`

An evaluator callback over an immutable context.

### `EvaluatorCallable`

`m3.evaluations.EvaluatorCallable(*args, **kwargs)`

### `EvaluatorRegistry`

`m3.evaluations.EvaluatorRegistry() -&gt; 'None'`

Explicit runtime registry for evaluator callables.

Public fields and methods:

- `register(self, name: 'str', evaluator: '_Any') -&gt; 'EvaluatorRegistration'`
- `get(self, name: 'str') -&gt; '_Any'`
- `names(self) -&gt; 'tuple[str, ...]'`

### `EvaluatorRegistration`

`m3.evaluations.EvaluatorRegistration(name: 'str', evaluator: 'EvaluatorCallable | AsyncEvaluator') -&gt; None`

Runtime-only registration; only its stable name is serializable.

### `InMemoryEvaluationStore`

`m3.evaluations.InMemoryEvaluationStore() -&gt; 'None'`

Small independent store; evaluations never rewrite execution state.

Public fields and methods:

- `save(self, result: '_EvaluationResult') -&gt; 'None'`
- `get(self, evaluation_id: '_EvaluationId | str') -&gt; '_EvaluationResult | None'`
- `all(self) -&gt; 'tuple[_EvaluationResult, ...]'`

### `RequiredEvaluationError`

`m3.evaluations.RequiredEvaluationError(result: '_EvaluationResult') -&gt; 'None'`

A required non-passing evaluation after its result was persisted.

### `register_builtin_evaluators`

`m3.evaluations.register_builtin_evaluators(registry: 'EvaluatorRegistry') -&gt; 'None'`

Install the reserved deterministic evaluators on a runtime registry.

## `m3.snapshots`

### `SnapshotOptions`

`m3.snapshots.SnapshotOptions(include_fields: 'frozenset[str]' = frozenset(), omitted_fields: 'frozenset[str]' = frozenset({'cost', 'cost_usd', 'created_at', 'duration', 'duration_ms', 'elapsed_ms', 'end_time', 'ended_at', 'execution_id', 'finished_at', 'input_cost', 'latency', 'latency_ms', 'output_cost', 'path', 'paths', 'port', 'ports', 'provider_metadata', 'run_id', 'run_ids', 'session_id', 'start_time', 'started_at', 'timestamp', 'timestamps', 'total_cost', 'trace_id', 'turn_id', 'updated_at', 'vendor_metadata'})) -&gt; None`

Controls stable snapshot omission and explicit field opt-ins.

### `snapshot`

`m3.snapshots.snapshot(value: '_Any', *, include_fields: '_Iterable[str]' = (), omitted_fields: '_Iterable[str] | None' = None, options: 'SnapshotOptions | None' = None, config: '_RedactionConfig | None' = None) -&gt; '_SnapshotValue'`

Return a deterministic, redacted, JSON-compatible snapshot value.

## `m3.policy`

### `ConfirmationHook`

`m3.policy.ConfirmationHook(*args, **kwargs)`

### `ToolDescriptor`

`m3.policy.ToolDescriptor(*, server: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], destructive: bool = False) -&gt; None`

A qualified advertised tool identity used during preflight.

Public fields and methods:

- `server: &lt;class 'str'&gt;` (required).
- `name: &lt;class 'str'&gt;` (required).
- `destructive: &lt;class 'bool'&gt;` (default: `False`).

### `ToolPolicyDecision`

`m3.policy.ToolPolicyDecision(*, allowed: bool, requires_confirmation: bool = False, reason: str, evidence: m3.policy.ToolPolicyEvidence) -&gt; None`

Decision for one qualified tool call.

Public fields and methods:

- `allowed: &lt;class 'bool'&gt;` (required).
- `requires_confirmation: &lt;class 'bool'&gt;` (default: `False`).
- `reason: &lt;class 'str'&gt;` (required).
- `evidence: &lt;class 'm3.policy.ToolPolicyEvidence'&gt;` (required).

### `ToolPolicyEvidence`

`m3.policy.ToolPolicyEvidence(*, requested: str, enforced: str | None = None, observed: str | None = None, unavailable: tuple[str, ...] = (), portable: bool = True, nonportable_reason: str | None = None) -&gt; None`

Truthful requested/enforced/observed policy status.

Public fields and methods:

- `requested: &lt;class 'str'&gt;` (required).
- `enforced: str | None` (default: `None`).
- `observed: str | None` (default: `None`).
- `unavailable: tuple[str, ...]` (default: `()`).
- `portable: &lt;class 'bool'&gt;` (default: `True`).
- `nonportable_reason: str | None` (default: `None`).

### `ToolPolicyEvaluator`

`m3.policy.ToolPolicyEvaluator(tools: '_Iterable[ToolDescriptor]') -&gt; 'None'`

Evaluate portable and explicitly native tool policies.

Public fields and methods:

- `preflight(self, policy: '_ToolPolicy', *, harness_name: 'str', supports_enforcement: 'bool') -&gt; 'ToolPolicyEvidence'`
- `decide(self, policy: '_ToolPolicy', descriptor: 'ToolDescriptor', *, harness_name: 'str', supports_enforcement: 'bool', confirm: 'ConfirmationHook | None' = None) -&gt; 'ToolPolicyDecision'`

### `evaluate_tool_policy`

`m3.policy.evaluate_tool_policy(policy: '_ToolPolicy', tools: '_Iterable[ToolDescriptor]', *, harness_name: 'str', supports_enforcement: 'bool') -&gt; 'ToolPolicyEvidence'`

Run policy preflight and return only truthful usage evidence.

## `m3.pytest_plugin`

### `pytest_addoption`

`m3.pytest_plugin.pytest_addoption(parser: '_Any') -&gt; 'None'`

### `pytest_configure`

`m3.pytest_plugin.pytest_configure(config: '_Any') -&gt; 'None'`

### `pytest_unconfigure`

`m3.pytest_plugin.pytest_unconfigure(config: '_Any') -&gt; 'None'`

### `pytest_generate_tests`

`m3.pytest_plugin.pytest_generate_tests(metafunc: '_Any') -&gt; 'None'`

### `m3_kit`

`m3.pytest_plugin.m3_kit(request: '_Any') -&gt; '_Any'`

### `agent`

`m3.pytest_plugin.agent(request: '_Any', m3_kit: '_Any') -&gt; '_Any'`

### `server`

`m3.pytest_plugin.server(request: '_Any') -&gt; '_Any'`

Selected typed server for an M3 marked test.

### `pytest_collection_modifyitems`

`m3.pytest_plugin.pytest_collection_modifyitems(config: '_Any', items: 'list[_Any]') -&gt; 'None'`

## `m3.matrix`

### `ToolCase`

`m3.matrix.ToolCase(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], id: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, arguments: collections.abc.Mapping[str, typing.Any] = &lt;factory&gt;, prompt: str | m3.types.UserMessage | None = None) -&gt; None`

One deterministic call definition owned by a server case.

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `id: str | None` (default: `None`).
- `arguments: collections.abc.Mapping[str, typing.Any]` (required).
- `prompt: str | m3.types.UserMessage | None` (default: `None`).

### `ServerCase`

`m3.matrix.ServerCase(*, name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], server: m3.types.StdioServer | m3.types.HTTPServer | m3.types.InProcessServer, tools: tuple[m3.matrix.ToolCase, ...]) -&gt; None`

A serializable MCP server and its owned logical tool cases.

Public fields and methods:

- `name: &lt;class 'str'&gt;` (required).
- `server: m3.types.StdioServer | m3.types.HTTPServer | m3.types.InProcessServer` (required).
- `tools: tuple[m3.matrix.ToolCase, ...]` (required).
- `tool(self, logical_id: 'str') -&gt; 'ToolCase'`: Return the tool with ``logical_id`` or raise a clear lookup error.

### `ToolMatrix`

`m3.matrix.ToolMatrix(*, servers: tuple[m3.matrix.ServerCase, ...], id: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, matrix_id: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, trials: Annotated[int, Strict(strict=True), Ge(ge=1)] = 1) -&gt; None`

Expand server-owned deterministic tool cases in declared order.

Public fields and methods:

- `servers: tuple[m3.matrix.ServerCase, ...]` (required).
- `id: str | None` (default: `None`).
- `matrix_id: str | None` (default: `None`).
- `trials: &lt;class 'int'&gt;` (default: `1`).
- `cases(self) -&gt; 'tuple[ToolMatrixCase, ...]'`
- `parametrize(self, argname: 'str' = 'case') -&gt; '_pytest.MarkDecorator'`: Return pytest's ordinary parametrization decorator for this matrix.

### `ToolMatrixCase`

`m3.matrix.ToolMatrixCase(*, id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=1024)], server: m3.matrix.ServerCase, tool: m3.matrix.ToolCase, matrix_id: str | None = None, cell_id: str | None = None, trial: Annotated[int, Strict(strict=True), Ge(ge=1)] = 1, trial_count: Annotated[int, Strict(strict=True), Ge(ge=1)] = 1) -&gt; None`

One server-owned deterministic tool cell.

Public fields and methods:

- `id: &lt;class 'str'&gt;` (required).
- `server: &lt;class 'm3.matrix.ServerCase'&gt;` (required).
- `tool: &lt;class 'm3.matrix.ToolCase'&gt;` (required).
- `matrix_id: str | None` (default: `None`).
- `cell_id: str | None` (default: `None`).
- `trial: &lt;class 'int'&gt;` (default: `1`).
- `trial_count: &lt;class 'int'&gt;` (default: `1`).
- `run(self, *, kit: '_MCPTestKit | None' = None, timeout: 'float | None' = None, validate_schemas: 'bool' = False, metadata: '_Mapping[str, _Scalar] | None' = None) -&gt; '_ExecutionResult'`: Run this cell through the normal synchronous execution boundary.
- `run_async(self, *, kit: '_AsyncMCPTestKit | None' = None, timeout: 'float | None' = None, validate_schemas: 'bool' = False, metadata: '_Mapping[str, _Scalar] | None' = None) -&gt; '_ExecutionResult'`: Run this cell through the normal asynchronous execution boundary.

## `m3.observability`

### `ACPTrace`

`m3.observability.ACPTrace(*, kind: Literal['acp'] = 'acp', session_id: m3.observability.Observation[str] = &lt;factory&gt;, protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, agent_identity: m3.observability.Observation[JsonValue] = &lt;factory&gt;, available_modes: m3.observability.Observation[JsonValue] = &lt;factory&gt;, current_mode: m3.observability.Observation[str] = &lt;factory&gt;, config_options: m3.observability.Observation[JsonValue] = &lt;factory&gt;, selected_config: m3.observability.Observation[JsonValue] = &lt;factory&gt;, plan_state_available: m3.observability.Observation[bool] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['acp']` (default: `'acp'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `agent_identity: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `available_modes: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `current_mode: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `config_options: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `selected_config: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `plan_state_available: &lt;class 'm3.observability.Observation[bool]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `ArtifactEntry`

`m3.observability.ArtifactEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['artifact'] = 'artifact', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), artifact: m3.types.ArtifactRef) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['artifact']` (default: `'artifact'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `artifact: &lt;class 'm3.types.ArtifactRef'&gt;` (required).

### `CaptureOptions`

`m3.observability.CaptureOptions(*, capture_raw_evidence: bool = True, capture_provider_messages: bool = True, capture_stderr: bool = True, raw_preview_bytes: Annotated[int, Gt(gt=0)] = 65536, raw_frame_bytes: Annotated[int, Gt(gt=0)] = 1048576, raw_execution_bytes: Annotated[int, Gt(gt=0)] = 67108864) -&gt; None`

Boundaries for redacted provider/MCP evidence capture.

Public fields and methods:

- `capture_raw_evidence: &lt;class 'bool'&gt;` (default: `True`).
- `capture_provider_messages: &lt;class 'bool'&gt;` (default: `True`).
- `capture_stderr: &lt;class 'bool'&gt;` (default: `True`).
- `raw_preview_bytes: &lt;class 'int'&gt;` (default: `65536`).
- `raw_frame_bytes: &lt;class 'int'&gt;` (default: `1048576`).
- `raw_execution_bytes: &lt;class 'int'&gt;` (default: `67108864`).

### `ClaudeCodeTrace`

`m3.observability.ClaudeCodeTrace(*, kind: Literal['claude_code'] = 'claude_code', session_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, result_subtype: m3.observability.Observation[str] = &lt;factory&gt;, stop_reason: m3.observability.Observation[str] = &lt;factory&gt;, service_tier: m3.observability.Observation[str] = &lt;factory&gt;, api_duration_ms: m3.observability.Observation[float] = &lt;factory&gt;, encrypted_reasoning: m3.observability.Observation[bool] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['claude_code']` (default: `'claude_code'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `result_subtype: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `stop_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `service_tier: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `api_duration_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `encrypted_reasoning: &lt;class 'm3.observability.Observation[bool]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `CodexTrace`

`m3.observability.CodexTrace(*, kind: Literal['codex'] = 'codex', thread_id: m3.observability.Observation[str] = &lt;factory&gt;, turn_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, sandbox: m3.observability.Observation[str] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['codex']` (default: `'codex'`).
- `thread_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `turn_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `sandbox: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `CorrelationState`

`m3.observability.CorrelationState(*values)`

### `DiagnosticEntry`

`m3.observability.DiagnosticEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['diagnostic'] = 'diagnostic', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), code: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], message: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], stage: Annotated[str | None, MaxLen(max_length=128)] = None, operation: Annotated[str | None, MaxLen(max_length=256)] = None, elapsed_seconds: Annotated[float | None, Ge(ge=0)] = None, timeout_seconds: Annotated[float | None, Gt(gt=0)] = None) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['diagnostic']` (default: `'diagnostic'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `code: &lt;class 'str'&gt;` (required).
- `message: &lt;class 'str'&gt;` (required).
- `stage: str | None` (default: `None`).
- `operation: str | None` (default: `None`).
- `elapsed_seconds: float | None` (default: `None`).
- `timeout_seconds: float | None` (default: `None`).

### `DirectTrace`

`m3.observability.DirectTrace(*, kind: Literal['direct'] = 'direct', transport: m3.observability.Observation[TransportKind] = &lt;factory&gt;, protocol: m3.observability.Observation[str] = &lt;factory&gt;, initialization: m3.observability.Observation[InitializationValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['direct']` (default: `'direct'`).
- `transport: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).
- `protocol: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `initialization: &lt;class 'm3.observability.Observation[InitializationValue]'&gt;` (required).

### `ElicitationEntry`

`m3.observability.ElicitationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['elicitation'] = 'elicitation', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), server: Annotated[str | None, MinLen(min_length=1), MaxLen(max_length=256)] = None, operation_kind: Literal['tool', 'prompt', 'resource'], operation_name: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], logical_operation_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], round_index: Annotated[int, Ge(ge=1)], request_key: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], mode: Literal['form', 'url'], message: m3.observability.Observation[str] = &lt;factory&gt;, requested_schema: m3.observability.Observation[JsonValue] = &lt;factory&gt;, url: m3.observability.Observation[str] = &lt;factory&gt;, elicitation_id: m3.observability.Observation[str] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, action: Optional[Literal['accept', 'decline', 'cancel']] = None, content: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

One keyed elicitation embedded in an MRTR input-required round.

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['elicitation']` (default: `'elicitation'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Literal['tool', 'prompt', 'resource']` (required).
- `operation_name: &lt;class 'str'&gt;` (required).
- `logical_operation_id: &lt;class 'str'&gt;` (required).
- `round_index: &lt;class 'int'&gt;` (required).
- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['form', 'url']` (required).
- `message: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `requested_schema: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `url: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `elicitation_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `action: typing.Optional[typing.Literal['accept', 'decline', 'cancel']]` (default: `None`).
- `content: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `EvaluationEntry`

`m3.observability.EvaluationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['evaluation'] = 'evaluation', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), evaluation: m3.types.EvaluationResult) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['evaluation']` (default: `'evaluation'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `evaluation: &lt;class 'm3.types.EvaluationResult'&gt;` (required).

### `EvidenceCapture`

`m3.observability.EvidenceCapture(*, reference: m3.types.EvidenceRef, preview: m3.observability.Observation[str], original_size_bytes: Annotated[int, Ge(ge=0)], stored_size_bytes: Annotated[int, Ge(ge=0)], redacted: bool, truncated: bool) -&gt; None`

Typed result of bounded, redacted raw-evidence capture.

Public fields and methods:

- `reference: &lt;class 'm3.types.EvidenceRef'&gt;` (required).
- `preview: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `original_size_bytes: &lt;class 'int'&gt;` (required).
- `stored_size_bytes: &lt;class 'int'&gt;` (required).
- `redacted: &lt;class 'bool'&gt;` (required).
- `truncated: &lt;class 'bool'&gt;` (required).

### `EvidenceConflict`

`m3.observability.EvidenceConflict(*, field: Literal['server', 'tool', 'arguments', 'result', 'status'], reported: m3.observability.Observation[JsonValue], wire: m3.observability.Observation[JsonValue]) -&gt; None`

Public fields and methods:

- `field: typing.Literal['server', 'tool', 'arguments', 'result', 'status']` (required).
- `reported: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `wire: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `HttpExchange`

`m3.observability.HttpExchange(*, method: Annotated[str, MinLen(min_length=1)], status_code: Annotated[int, Ge(ge=100), Le(le=599)], headers: tuple[m3.observability.SafeHttpHeader, ...] = ()) -&gt; None`

Public fields and methods:

- `method: &lt;class 'str'&gt;` (required).
- `status_code: &lt;class 'int'&gt;` (required).
- `headers: tuple[m3.observability.SafeHttpHeader, ...]` (default: `()`).

### `InitializationEntry`

`m3.observability.InitializationEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['initialization'] = 'initialization', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, server_name: m3.observability.Observation[str] = &lt;factory&gt;, server_version: m3.observability.Observation[str] = &lt;factory&gt;, instructions: m3.observability.Observation[str] = &lt;factory&gt;, capabilities: m3.observability.Observation[JsonValue] = &lt;factory&gt;, tools: m3.observability.Observation[tuple[ToolInfo, ...]] = &lt;factory&gt;, resources: m3.observability.Observation[tuple[ResourceInfo, ...]] = &lt;factory&gt;, resource_templates: m3.observability.Observation[tuple[TemplateInfo, ...]] = &lt;factory&gt;, prompts: m3.observability.Observation[tuple[PromptInfo, ...]] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['initialization']` (default: `'initialization'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `instructions: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `capabilities: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `tools: &lt;class 'm3.observability.Observation[tuple[ToolInfo, ...]]'&gt;` (required).
- `resources: &lt;class 'm3.observability.Observation[tuple[ResourceInfo, ...]]'&gt;` (required).
- `resource_templates: &lt;class 'm3.observability.Observation[tuple[TemplateInfo, ...]]'&gt;` (required).
- `prompts: &lt;class 'm3.observability.Observation[tuple[PromptInfo, ...]]'&gt;` (required).

### `InitializationValue`

`m3.observability.InitializationValue(*, protocol_version: m3.observability.Observation[str] = &lt;factory&gt;, server_name: m3.observability.Observation[str] = &lt;factory&gt;, server_version: m3.observability.Observation[str] = &lt;factory&gt;, instructions: m3.observability.Observation[str] = &lt;factory&gt;, capabilities: m3.observability.Observation[JsonValue] = &lt;factory&gt;, tools: m3.observability.Observation[tuple[ToolInfo, ...]] = &lt;factory&gt;, resources: m3.observability.Observation[tuple[ResourceInfo, ...]] = &lt;factory&gt;, resource_templates: m3.observability.Observation[tuple[TemplateInfo, ...]] = &lt;factory&gt;, prompts: m3.observability.Observation[tuple[PromptInfo, ...]] = &lt;factory&gt;) -&gt; None`

Value-only initialization metadata used by runtime information.

Public fields and methods:

- `protocol_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server_version: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `instructions: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `capabilities: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `tools: &lt;class 'm3.observability.Observation[tuple[ToolInfo, ...]]'&gt;` (required).
- `resources: &lt;class 'm3.observability.Observation[tuple[ResourceInfo, ...]]'&gt;` (required).
- `resource_templates: &lt;class 'm3.observability.Observation[tuple[TemplateInfo, ...]]'&gt;` (required).
- `prompts: &lt;class 'm3.observability.Observation[tuple[PromptInfo, ...]]'&gt;` (required).

### `InteractionEntry`

`m3.observability.InteractionEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['interaction'] = 'interaction', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), interaction_kind: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], request: m3.observability.Observation[JsonValue] = &lt;factory&gt;, response: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['interaction']` (default: `'interaction'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `interaction_kind: &lt;class 'str'&gt;` (required).
- `request: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `response: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `LifecycleEntry`

`m3.observability.LifecycleEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['lifecycle'] = 'lifecycle', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), phase: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)]) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['lifecycle']` (default: `'lifecycle'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `phase: &lt;class 'str'&gt;` (required).

### `MessageEntry`

`m3.observability.MessageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['message'] = 'message', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), message_id: m3.observability.Observation[str] = &lt;factory&gt;, role: m3.observability.MessageRole = &lt;MessageRole.ASSISTANT: 'assistant'&gt;, content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = (), stop_reason: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['message']` (default: `'message'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `message_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `role: &lt;enum 'MessageRole'&gt;` (default: `&lt;MessageRole.ASSISTANT: 'assistant'&gt;`).
- `content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `stop_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `MessageRole`

`m3.observability.MessageRole(*values)`

### `Observation`

`m3.observability.Observation(*, state: m3.observability.ObservationState, value: Optional[~_T] = None, reason: m3.observability.ObservationReason | None = None, provenance: tuple[m3.types.EventSource, ...] = (), evidence_ref: m3.types.EvidenceRef | None = None) -&gt; None`

A typed value with explicit availability and provenance.

Public fields and methods:

- `state: &lt;enum 'ObservationState'&gt;` (required).
- `value: typing.Optional[~_T]` (default: `None`).
- `reason: m3.observability.ObservationReason | None` (default: `None`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `evidence_ref: m3.types.EvidenceRef | None` (default: `None`).

### `ObservationReason`

`m3.observability.ObservationReason(*values)`

### `ObservationState`

`m3.observability.ObservationState(*values)`

How completely a provider-dependent value was observed.

### `OpenCodeTrace`

`m3.observability.OpenCodeTrace(*, kind: Literal['opencode'] = 'opencode', session_id: m3.observability.Observation[str] = &lt;factory&gt;, provider_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, http_lifecycle: m3.observability.Observation[JsonValue] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['opencode']` (default: `'opencode'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `provider_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `http_lifecycle: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `PiTrace`

`m3.observability.PiTrace(*, kind: Literal['pi'] = 'pi', session_id: m3.observability.Observation[str] = &lt;factory&gt;, provider_id: m3.observability.Observation[str] = &lt;factory&gt;, model_id: m3.observability.Observation[str] = &lt;factory&gt;, finish_reason: m3.observability.Observation[str] = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `kind: typing.Literal['pi']` (default: `'pi'`).
- `session_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `provider_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `model_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `finish_reason: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).

### `ProcessEntry`

`m3.observability.ProcessEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['process'] = 'process', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), executable: m3.observability.Observation[str] = &lt;factory&gt;, pid: m3.observability.Observation[int] = &lt;factory&gt;, exit_code: m3.observability.Observation[int] = &lt;factory&gt;, signal: m3.observability.Observation[int] = &lt;factory&gt;, stderr: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['process']` (default: `'process'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `executable: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `pid: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `exit_code: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `signal: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `stderr: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `ProtocolCallAttempt`

`m3.observability.ProtocolCallAttempt(*, attempt_index: Annotated[int, Ge(ge=0)], jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, continuation_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, operation_params: m3.observability.Observation[JsonValue] = &lt;factory&gt;, input_required: bool = False, result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, raw_result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.INCOMPLETE: 'incomplete'&gt;, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;) -&gt; None`

One wire-level attempt belonging to a prompt or resource call.

Public fields and methods:

- `attempt_index: &lt;class 'int'&gt;` (required).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `continuation_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `operation_params: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `input_required: &lt;class 'bool'&gt;` (default: `False`).
- `result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `raw_result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.INCOMPLETE: 'incomplete'&gt;`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).

### `ProtocolEntry`

`m3.observability.ProtocolEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['protocol'] = 'protocol', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), protocol: m3.observability.ProtocolKind, method: m3.observability.Observation[str] = &lt;factory&gt;, direction: m3.types.EventDirection = &lt;EventDirection.INTERNAL: 'internal'&gt;, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request: m3.observability.Observation[JsonValue] = &lt;factory&gt;, response: m3.observability.Observation[JsonValue] = &lt;factory&gt;, error: m3.observability.Observation[ProtocolErrorInfo] = &lt;factory&gt;, http: m3.observability.Observation[HttpExchange] = &lt;factory&gt;, operation_kind: Optional[Literal['prompt', 'resource']] = None, operation_name: m3.observability.Observation[str] = &lt;factory&gt;, attempts: tuple[m3.observability.ProtocolCallAttempt, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['protocol']` (default: `'protocol'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `protocol: &lt;enum 'ProtocolKind'&gt;` (required).
- `method: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `direction: &lt;enum 'EventDirection'&gt;` (default: `&lt;EventDirection.INTERNAL: 'internal'&gt;`).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `response: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `error: &lt;class 'm3.observability.Observation[ProtocolErrorInfo]'&gt;` (required).
- `http: &lt;class 'm3.observability.Observation[HttpExchange]'&gt;` (required).
- `operation_kind: typing.Optional[typing.Literal['prompt', 'resource']]` (default: `None`).
- `operation_name: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `attempts: tuple[m3.observability.ProtocolCallAttempt, ...]` (default: `()`).

### `ProtocolErrorInfo`

`m3.observability.ProtocolErrorInfo(*, code: int | str | None = None, message: Annotated[str, MinLen(min_length=1), MaxLen(max_length=4096)], data: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `code: int | str | None` (default: `None`).
- `message: &lt;class 'str'&gt;` (required).
- `data: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `ProtocolKind`

`m3.observability.ProtocolKind(*values)`

### `ProviderEntry`

`m3.observability.ProviderEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['provider'] = 'provider', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), provider: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], category: Annotated[str, MinLen(min_length=1), MaxLen(max_length=128)], data: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['provider']` (default: `'provider'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `provider: &lt;class 'str'&gt;` (required).
- `category: &lt;class 'str'&gt;` (required).
- `data: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

### `RawEvidence`

`m3.observability.RawEvidence(*, reference: m3.types.EvidenceRef, media_type: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], content: Union[JsonValue, str], size_bytes: Annotated[int, Ge(ge=0)], returned_size_bytes: Annotated[int, Ge(ge=0)], truncated: bool = False, redacted: Literal[True] = True) -&gt; None`

Public fields and methods:

- `reference: &lt;class 'm3.types.EvidenceRef'&gt;` (required).
- `media_type: &lt;class 'str'&gt;` (required).
- `content: typing.Union[JsonValue, str]` (required).
- `size_bytes: &lt;class 'int'&gt;` (required).
- `returned_size_bytes: &lt;class 'int'&gt;` (required).
- `truncated: &lt;class 'bool'&gt;` (default: `False`).
- `redacted: typing.Literal[True]` (default: `True`).

### `RawEvidenceSource`

`m3.observability.RawEvidenceSource(*values)`

### `RawMessageEntry`

`m3.observability.RawMessageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['raw_message'] = 'raw_message', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), source: m3.observability.RawEvidenceSource, direction: m3.types.EventDirection = &lt;EventDirection.INTERNAL: 'internal'&gt;, media_type: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], preview: m3.observability.Observation[Union[JsonValue, str]] = &lt;factory&gt;, evidence_ref: m3.types.EvidenceRef | None = None, size_bytes: Annotated[int, Ge(ge=0)] = 0, redacted: Literal[True] = True) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['raw_message']` (default: `'raw_message'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `source: &lt;enum 'RawEvidenceSource'&gt;` (required).
- `direction: &lt;enum 'EventDirection'&gt;` (default: `&lt;EventDirection.INTERNAL: 'internal'&gt;`).
- `media_type: &lt;class 'str'&gt;` (required).
- `preview: &lt;class 'm3.observability.Observation[Union[JsonValue, str]]'&gt;` (required).
- `evidence_ref: m3.types.EvidenceRef | None` (default: `None`).
- `size_bytes: &lt;class 'int'&gt;` (default: `0`).
- `redacted: typing.Literal[True]` (default: `True`).

### `ReasoningEntry`

`m3.observability.ReasoningEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['reasoning'] = 'reasoning', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), block_id: m3.observability.Observation[str] = &lt;factory&gt;, content: m3.observability.Observation[tuple[Annotated[Union[TextContent, FileContent, ImageContent, AudioContent, ResourceLink, OpaqueContent], FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['reasoning']` (default: `'reasoning'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `block_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `content: &lt;class 'm3.observability.Observation[tuple[Annotated[Union[TextContent, FileContent, ImageContent, AudioContent, ResourceLink, OpaqueContent], FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]]'&gt;` (required).

### `ReportedToolCall`

`m3.observability.ReportedToolCall(*, provider_call_id: m3.observability.Observation[str] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `provider_call_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `RuntimeTraceInfo`

`m3.observability.RuntimeTraceInfo(*args, **kwargs)`

Runtime representation of an annotated type.

### `SafeHttpHeader`

`m3.observability.SafeHttpHeader(*, name: Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id'], value: str) -&gt; None`

Public fields and methods:

- `name: typing.Literal['content-type', 'content-length', 'retry-after', 'request-id', 'x-request-id']` (required).
- `value: &lt;class 'str'&gt;` (required).

### `ToolCallAttempt`

`m3.observability.ToolCallAttempt(*, attempt_index: Annotated[int, Ge(ge=0)], jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, request_state: m3.observability.Observation[str] = &lt;factory&gt;, continuation_state: m3.observability.Observation[str] = &lt;factory&gt;, input_responses: m3.observability.Observation[JsonValue] = &lt;factory&gt;, operation_params: m3.observability.Observation[JsonValue] = &lt;factory&gt;, input_required: bool = False, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, raw_result: m3.observability.Observation[JsonValue] = &lt;factory&gt;, status: m3.observability.ToolCallStatus = &lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;) -&gt; None`

One wire-level attempt belonging to a logical tool call.

Public fields and methods:

- `attempt_index: &lt;class 'int'&gt;` (required).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `request_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `continuation_state: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `input_responses: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `operation_params: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `input_required: &lt;class 'bool'&gt;` (default: `False`).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `raw_result: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `status: &lt;enum 'ToolCallStatus'&gt;` (default: `&lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).

### `ToolCallEntry`

`m3.observability.ToolCallEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['tool_call'] = 'tool_call', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), call_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], provider_call_id: m3.observability.Observation[str] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, tool_status: m3.observability.ToolCallStatus = &lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;, correlation: m3.observability.CorrelationState = &lt;CorrelationState.UNAVAILABLE: 'unavailable'&gt;, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, server_latency_ms: m3.observability.Observation[float] = &lt;factory&gt;, policy: m3.observability.Observation[ToolPolicyDecision] = &lt;factory&gt;, reported: m3.observability.Observation[ReportedToolCall] = &lt;factory&gt;, wire: m3.observability.Observation[WireToolCall] = &lt;factory&gt;, conflicts: tuple[m3.observability.EvidenceConflict, ...] = (), attempts: tuple[m3.observability.ToolCallAttempt, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['tool_call']` (default: `'tool_call'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `call_id: &lt;class 'str'&gt;` (required).
- `provider_call_id: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `tool_status: &lt;enum 'ToolCallStatus'&gt;` (default: `&lt;ToolCallStatus.INCOMPLETE: 'incomplete'&gt;`).
- `correlation: &lt;enum 'CorrelationState'&gt;` (default: `&lt;CorrelationState.UNAVAILABLE: 'unavailable'&gt;`).
- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `server_latency_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `policy: &lt;class 'm3.observability.Observation[ToolPolicyDecision]'&gt;` (required).
- `reported: &lt;class 'm3.observability.Observation[ReportedToolCall]'&gt;` (required).
- `wire: &lt;class 'm3.observability.Observation[WireToolCall]'&gt;` (required).
- `conflicts: tuple[m3.observability.EvidenceConflict, ...]` (default: `()`).
- `attempts: tuple[m3.observability.ToolCallAttempt, ...]` (default: `()`).

### `ToolCallStatus`

`m3.observability.ToolCallStatus(*values)`

### `ToolResult`

`m3.observability.ToolResult(*, content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = (), structured_content: m3.observability.Observation[JsonValue] = &lt;factory&gt;, is_error: bool = False, error: m3.observability.Observation[ErrorInfo] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `content: tuple[typing.Annotated[m3.types.TextContent | m3.types.FileContent | m3.types.ImageContent | m3.types.AudioContent | m3.types.ResourceLink | m3.types.OpaqueContent, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `structured_content: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `is_error: &lt;class 'bool'&gt;` (default: `False`).
- `error: &lt;class 'm3.observability.Observation[ErrorInfo]'&gt;` (required).

### `TraceEntry`

`m3.observability.TraceEntry(*args, **kwargs)`

Runtime representation of an annotated type.

### `TraceEntryBase`

`m3.observability.TraceEntryBase(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Annotated[str, MinLen(min_length=1), MaxLen(max_length=64)], parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = ()) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: &lt;class 'str'&gt;` (required).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).

### `TraceStatus`

`m3.observability.TraceStatus(*values)`

### `TraceSummary`

`m3.observability.TraceSummary(*, timing: m3.observability.TraceTiming = &lt;factory&gt;, usage: m3.observability.Observation[UsageValue] = &lt;factory&gt;, turn_count: Annotated[int, Ge(ge=0)] = 0, message_count: Annotated[int, Ge(ge=0)] = 0, reasoning_count: Annotated[int, Ge(ge=0)] = 0, tool_call_count: Annotated[int, Ge(ge=0)] = 0, successful_tool_call_count: Annotated[int, Ge(ge=0)] = 0, failed_tool_call_count: Annotated[int, Ge(ge=0)] = 0, protocol_error_count: Annotated[int, Ge(ge=0)] = 0, activity_health: m3.types.ActivityHealth = &lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;, cleanup_status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;) -&gt; None`

Public fields and methods:

- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `usage: &lt;class 'm3.observability.Observation[UsageValue]'&gt;` (required).
- `turn_count: &lt;class 'int'&gt;` (default: `0`).
- `message_count: &lt;class 'int'&gt;` (default: `0`).
- `reasoning_count: &lt;class 'int'&gt;` (default: `0`).
- `tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `successful_tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `failed_tool_call_count: &lt;class 'int'&gt;` (default: `0`).
- `protocol_error_count: &lt;class 'int'&gt;` (default: `0`).
- `activity_health: &lt;enum 'ActivityHealth'&gt;` (default: `&lt;ActivityHealth.NO_CALLS: 'no_calls'&gt;`).
- `cleanup_status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).

### `TraceTiming`

`m3.observability.TraceTiming(*, started_at: datetime.datetime = &lt;factory&gt;, finished_at: datetime.datetime | None = None, start_offset_ms: Annotated[float, Ge(ge=0)] = 0, end_offset_ms: Annotated[float, Ge(ge=0)] = 0, duration_ms: Annotated[float, Ge(ge=0)] = 0) -&gt; None`

Public fields and methods:

- `started_at: &lt;class 'datetime.datetime'&gt;` (required).
- `finished_at: datetime.datetime | None` (default: `None`).
- `start_offset_ms: &lt;class 'float'&gt;` (default: `0`).
- `end_offset_ms: &lt;class 'float'&gt;` (default: `0`).
- `duration_ms: &lt;class 'float'&gt;` (default: `0`).

### `TraceView`

`m3.observability.TraceView(*, schema_id: Literal['m3.trace_view'] = 'm3.trace_view', schema_version: Literal['1.1', '1.2'] = '1.1', trace_id: m3.types.TraceId, execution_id: m3.types.ExecutionId, outcome: m3.types.ExecutionOutcome = &lt;ExecutionOutcome.COMPLETED: 'completed'&gt;, completeness: Literal['complete', 'partial'] = 'complete', limitations: tuple[str, ...] = (), agent: m3.types.AgentIdentity | None = None, runtime: m3.observability.DirectTrace | m3.observability.OpenCodeTrace | m3.observability.ClaudeCodeTrace | m3.observability.CodexTrace | m3.observability.PiTrace | m3.observability.ACPTrace = &lt;factory&gt;, summary: m3.observability.TraceSummary = &lt;factory&gt;, timeline: tuple[typing.Annotated[m3.observability.LifecycleEntry | m3.observability.MessageEntry | m3.observability.ReasoningEntry | m3.observability.ToolCallEntry | m3.observability.ProtocolEntry | m3.observability.TransportEntry | m3.observability.InitializationEntry | m3.observability.UsageEntry | m3.observability.InteractionEntry | m3.observability.ElicitationEntry | m3.observability.ProcessEntry | m3.observability.WorkspaceEntry | m3.observability.ArtifactEntry | m3.observability.EvaluationEntry | m3.observability.DiagnosticEntry | m3.observability.RawMessageEntry | m3.observability.ProviderEntry, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...] = ()) -&gt; None`

Public fields and methods:

- `schema_id: typing.Literal['m3.trace_view']` (default: `'m3.trace_view'`).
- `schema_version: typing.Literal['1.1', '1.2']` (default: `'1.1'`).
- `trace_id: &lt;class 'm3.types.TraceId'&gt;` (required).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `outcome: &lt;enum 'ExecutionOutcome'&gt;` (default: `&lt;ExecutionOutcome.COMPLETED: 'completed'&gt;`).
- `completeness: typing.Literal['complete', 'partial']` (default: `'complete'`).
- `limitations: tuple[str, ...]` (default: `()`).
- `agent: m3.types.AgentIdentity | None` (default: `None`).
- `runtime: m3.observability.DirectTrace | m3.observability.OpenCodeTrace | m3.observability.ClaudeCodeTrace | m3.observability.CodexTrace | m3.observability.PiTrace | m3.observability.ACPTrace` (required).
- `summary: &lt;class 'm3.observability.TraceSummary'&gt;` (required).
- `timeline: tuple[typing.Annotated[m3.observability.LifecycleEntry | m3.observability.MessageEntry | m3.observability.ReasoningEntry | m3.observability.ToolCallEntry | m3.observability.ProtocolEntry | m3.observability.TransportEntry | m3.observability.InitializationEntry | m3.observability.UsageEntry | m3.observability.InteractionEntry | m3.observability.ElicitationEntry | m3.observability.ProcessEntry | m3.observability.WorkspaceEntry | m3.observability.ArtifactEntry | m3.observability.EvaluationEntry | m3.observability.DiagnosticEntry | m3.observability.RawMessageEntry | m3.observability.ProviderEntry, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` (default: `()`).
- `for_turn(self, turn: '_TurnResult | _TurnState | _TurnId | str') -&gt; 'TraceView'`: Return the finalized evidence belonging to one turn.
- `for_session(self, session_id: '_SessionId | str') -&gt; 'TraceView'`
- `for_server(self, server_binding: 'str') -&gt; 'TraceView'`
- `between(self, start_offset_ms: 'float', end_offset_ms: 'float') -&gt; 'TraceView'`

### `TransportEntry`

`m3.observability.TransportEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['transport'] = 'transport', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), phase: Literal['connected', 'disconnected'], configured: m3.observability.Observation[TransportKind] = &lt;factory&gt;, instrumented: m3.observability.Observation[TransportKind] = &lt;factory&gt;) -&gt; None`

A stable MCP transport lifecycle observation.

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['transport']` (default: `'transport'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `phase: typing.Literal['connected', 'disconnected']` (required).
- `configured: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).
- `instrumented: &lt;class 'm3.observability.Observation[TransportKind]'&gt;` (required).

### `UsageEntry`

`m3.observability.UsageEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['usage'] = 'usage', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), input_tokens: m3.observability.Observation[int] = &lt;factory&gt;, output_tokens: m3.observability.Observation[int] = &lt;factory&gt;, reasoning_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_creation_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_read_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_write_tokens: m3.observability.Observation[int] = &lt;factory&gt;, total_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cost: m3.observability.Observation[float] = &lt;factory&gt;, currency: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['usage']` (default: `'usage'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `input_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `output_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `reasoning_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_creation_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_read_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_write_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `total_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cost: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `currency: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `UsageValue`

`m3.observability.UsageValue(*, input_tokens: m3.observability.Observation[int] = &lt;factory&gt;, output_tokens: m3.observability.Observation[int] = &lt;factory&gt;, reasoning_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_creation_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_read_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cache_write_tokens: m3.observability.Observation[int] = &lt;factory&gt;, total_tokens: m3.observability.Observation[int] = &lt;factory&gt;, cost: m3.observability.Observation[float] = &lt;factory&gt;, currency: m3.observability.Observation[str] = &lt;factory&gt;) -&gt; None`

Value-only usage aggregate used by summaries and runtime metadata.

Public fields and methods:

- `input_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `output_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `reasoning_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_creation_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_read_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cache_write_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `total_tokens: &lt;class 'm3.observability.Observation[int]'&gt;` (required).
- `cost: &lt;class 'm3.observability.Observation[float]'&gt;` (required).
- `currency: &lt;class 'm3.observability.Observation[str]'&gt;` (required).

### `WireToolCall`

`m3.observability.WireToolCall(*, jsonrpc_id: m3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]] = &lt;factory&gt;, server: m3.observability.Observation[str] = &lt;factory&gt;, tool: m3.observability.Observation[str] = &lt;factory&gt;, arguments: m3.observability.Observation[JsonValue] = &lt;factory&gt;, result: m3.observability.Observation[ToolResult] = &lt;factory&gt;, latency_ms: m3.observability.Observation[float] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `jsonrpc_id: &lt;class 'm3.observability.Observation[Union[Annotated[int, Strict(strict=True)], Annotated[str, Strict(strict=True)]]]'&gt;` (required).
- `server: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `tool: &lt;class 'm3.observability.Observation[str]'&gt;` (required).
- `arguments: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).
- `result: &lt;class 'm3.observability.Observation[ToolResult]'&gt;` (required).
- `latency_ms: &lt;class 'm3.observability.Observation[float]'&gt;` (required).

### `WorkspaceEntry`

`m3.observability.WorkspaceEntry(*, entry_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], kind: Literal['workspace'] = 'workspace', parent_id: str | None = None, execution_id: m3.types.ExecutionId, session_id: m3.types.SessionId | None = None, turn_id: m3.types.TurnId | None = None, server_binding: str | None = None, connection_id: m3.types.ConnectionId | None = None, sequence_start: Annotated[int, Ge(ge=0)], sequence_end: Annotated[int, Ge(ge=0)], timing: m3.observability.TraceTiming = &lt;factory&gt;, status: m3.observability.TraceStatus = &lt;TraceStatus.COMPLETED: 'completed'&gt;, provenance: tuple[m3.types.EventSource, ...] = (), limitations: tuple[str, ...] = (), change: m3.observability.Observation[JsonValue] = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `entry_id: &lt;class 'str'&gt;` (required).
- `kind: typing.Literal['workspace']` (default: `'workspace'`).
- `parent_id: str | None` (default: `None`).
- `execution_id: &lt;class 'm3.types.ExecutionId'&gt;` (required).
- `session_id: m3.types.SessionId | None` (default: `None`).
- `turn_id: m3.types.TurnId | None` (default: `None`).
- `server_binding: str | None` (default: `None`).
- `connection_id: m3.types.ConnectionId | None` (default: `None`).
- `sequence_start: &lt;class 'int'&gt;` (required).
- `sequence_end: &lt;class 'int'&gt;` (required).
- `timing: &lt;class 'm3.observability.TraceTiming'&gt;` (required).
- `status: &lt;enum 'TraceStatus'&gt;` (default: `&lt;TraceStatus.COMPLETED: 'completed'&gt;`).
- `provenance: tuple[m3.types.EventSource, ...]` (default: `()`).
- `limitations: tuple[str, ...]` (default: `()`).
- `change: &lt;class 'm3.observability.Observation[JsonValue]'&gt;` (required).

## `m3.aggregations`

### `EvaluationGroup`

`m3.aggregations.EvaluationGroup(*, key: collections.abc.Mapping[str, str | int | float | bool | None] = &lt;factory&gt;, values: m3.aggregations.EvaluationStats = &lt;factory&gt;) -&gt; None`

Public fields and methods:

- `key: collections.abc.Mapping[str, str | int | float | bool | None]` (required).
- `values: &lt;class 'm3.aggregations.EvaluationStats'&gt;` (required).

### `EvaluationQuery`

`m3.aggregations.EvaluationQuery(*, from_: datetime.datetime | None = None, to: datetime.datetime | None = None, group_by: tuple[str, ...] = (), filters: collections.abc.Mapping[str, tuple[str | int | float | bool | None, ...]] = &lt;factory&gt;, limit: Annotated[int, Ge(ge=1), Le(le=1000)] = 200, offset: Annotated[int, Ge(ge=0)] = 0) -&gt; None`

A read-only query over persisted evaluations.

Public fields and methods:

- `from_: datetime.datetime | None` (default: `None`).
- `to: datetime.datetime | None` (default: `None`).
- `group_by: tuple[str, ...]` (default: `()`).
- `filters: collections.abc.Mapping[str, tuple[str | int | float | bool | None, ...]]` (required).
- `limit: &lt;class 'int'&gt;` (default: `200`).
- `offset: &lt;class 'int'&gt;` (default: `0`).

### `EvaluationReport`

`m3.aggregations.EvaluationReport(*, from_: datetime.datetime | None = None, to: datetime.datetime | None = None, totals: m3.aggregations.EvaluationStats = &lt;factory&gt;, groups: tuple[m3.aggregations.EvaluationGroup, ...] = (), total_groups: int = 0, limit: int = 200, offset: int = 0) -&gt; None`

Public fields and methods:

- `from_: datetime.datetime | None` (default: `None`).
- `to: datetime.datetime | None` (default: `None`).
- `totals: &lt;class 'm3.aggregations.EvaluationStats'&gt;` (required).
- `groups: tuple[m3.aggregations.EvaluationGroup, ...]` (default: `()`).
- `total_groups: &lt;class 'int'&gt;` (default: `0`).
- `limit: &lt;class 'int'&gt;` (default: `200`).
- `offset: &lt;class 'int'&gt;` (default: `0`).

### `EvaluationStats`

`m3.aggregations.EvaluationStats(*, trial_count: Annotated[int, Ge(ge=0)] = 0, evaluation_count: Annotated[int, Ge(ge=0)] = 0, expected_count: Annotated[int, Ge(ge=0)] = 0, missing_required_count: Annotated[int, Ge(ge=0)] = 0, pending_required_count: Annotated[int, Ge(ge=0)] = 0, status_counts: collections.abc.Mapping[str, int] = &lt;factory&gt;, pass_rate: Annotated[float | None, Ge(ge=0), Le(le=1)] = None, average_score: Annotated[float | None, Ge(ge=0), Le(le=1)] = None, score_count: Annotated[int, Ge(ge=0)] = 0, health: m3.aggregations.HealthStats = &lt;factory&gt;) -&gt; None`

Counts and health metrics for one aggregate group.

Public fields and methods:

- `trial_count: &lt;class 'int'&gt;` (default: `0`).
- `evaluation_count: &lt;class 'int'&gt;` (default: `0`).
- `expected_count: &lt;class 'int'&gt;` (default: `0`).
- `missing_required_count: &lt;class 'int'&gt;` (default: `0`).
- `pending_required_count: &lt;class 'int'&gt;` (default: `0`).
- `status_counts: collections.abc.Mapping[str, int]` (required).
- `pass_rate: float | None` (default: `None`).
- `average_score: float | None` (default: `None`).
- `score_count: &lt;class 'int'&gt;` (default: `0`).
- `health: &lt;class 'm3.aggregations.HealthStats'&gt;` (required).

### `HealthStats`

`m3.aggregations.HealthStats(*, execution_count: Annotated[int, Ge(ge=0)] = 0, tool_calls: m3.aggregations.ToolCallStats = &lt;factory&gt;, protocol_error_count: Annotated[int, Ge(ge=0)] = 0, outcome_counts: collections.abc.Mapping[str, int] = &lt;factory&gt;, execution_duration_ms: m3.aggregations.LatencyStats = &lt;factory&gt;, server_latency_ms: m3.aggregations.LatencyStats = &lt;factory&gt;) -&gt; None`

Execution and tool health observed for a group.

Public fields and methods:

- `execution_count: &lt;class 'int'&gt;` (default: `0`).
- `tool_calls: &lt;class 'm3.aggregations.ToolCallStats'&gt;` (required).
- `protocol_error_count: &lt;class 'int'&gt;` (default: `0`).
- `outcome_counts: collections.abc.Mapping[str, int]` (required).
- `execution_duration_ms: &lt;class 'm3.aggregations.LatencyStats'&gt;` (required).
- `server_latency_ms: &lt;class 'm3.aggregations.LatencyStats'&gt;` (required).

### `LatencyStats`

`m3.aggregations.LatencyStats(*, count: Annotated[int, Ge(ge=0)] = 0, p50: Annotated[float | None, Ge(ge=0)] = None, p95: Annotated[float | None, Ge(ge=0)] = None) -&gt; None`

Public fields and methods:

- `count: &lt;class 'int'&gt;` (default: `0`).
- `p50: float | None` (default: `None`).
- `p95: float | None` (default: `None`).

### `ToolCallStats`

`m3.aggregations.ToolCallStats(*, total: Annotated[int, Ge(ge=0)] = 0, successful: Annotated[int, Ge(ge=0)] = 0, failed: Annotated[int, Ge(ge=0)] = 0) -&gt; None`

Public fields and methods:

- `total: &lt;class 'int'&gt;` (default: `0`).
- `successful: &lt;class 'int'&gt;` (default: `0`).
- `failed: &lt;class 'int'&gt;` (default: `0`).

### `aggregate_evaluations`

`m3.aggregations.aggregate_evaluations(query: 'EvaluationQuery', records: 'Iterable[EvaluationRecord]', *, snapshots: 'Mapping[str, Any] | None' = None, specifications: 'Mapping[str, Any] | None' = None, traces: 'Mapping[str, Any] | None' = None, attempt_states: 'Mapping[str, str] | None' = None, project_names: 'Mapping[str, str] | None' = None) -&gt; 'EvaluationReport'`

Aggregate persisted evaluations and unresolved specification requirements.

## `m3.elicitation`

### `ElicitationPlan`

`m3.elicitation.ElicitationPlan(*, node: Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of'] = 'leaf', request: Optional[Annotated[m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]] = None, response: m3.elicitation.ElicitationResponse | None = None, children: tuple[m3.elicitation.ElicitationPlan, ...] = (), optional_occurrence: bool = False) -&gt; None`

An immutable, serializable elicitation expectation tree.

Public fields and methods:

- `node: typing.Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of']` (default: `'leaf'`).
- `request: typing.Optional[typing.Annotated[m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]]` (default: `None`).
- `response: m3.elicitation.ElicitationResponse | None` (default: `None`).
- `children: tuple[m3.elicitation.ElicitationPlan, ...]` (default: `()`).
- `optional_occurrence: &lt;class 'bool'&gt;` (default: `False`).
- `accept(self, content: 'Mapping[str, object] | None' = None) -&gt; 'ElicitationPlan'`
- `decline(self) -&gt; 'ElicitationPlan'`
- `cancel(self) -&gt; 'ElicitationPlan'`
- `canonical_identity(self) -&gt; 'str'`
- `canonical_json(self) -&gt; 'str'`
- `model_dump(self, *args: 'Any', **kwargs: 'Any') -&gt; 'dict[str, Any]'`
- `model_dump_json(self, *args: 'Any', **kwargs: 'Any') -&gt; 'str'`
- `matcher(self) -&gt; 'PlanMatcher'`

### `ElicitationRequest`

`m3.elicitation.ElicitationRequest`

Represent a PEP 604 union type

### `ElicitationResponse`

`m3.elicitation.ElicitationResponse(*, action: Literal['accept', 'decline', 'cancel'], content: collections.abc.Mapping[str, object] | None = None, meta: collections.abc.Mapping[str, object] | None = None) -&gt; None`

The response that will be associated with one request key.

Public fields and methods:

- `action: typing.Literal['accept', 'decline', 'cancel']` (required).
- `content: collections.abc.Mapping[str, object] | None` (default: `None`).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).

### `FormElicitationRequest`

`m3.elicitation.FormElicitationRequest(*, request_key: Annotated[str, MinLen(min_length=1)], mode: Literal['form'] = 'form', message: str, requested_schema: collections.abc.Mapping[str, object], meta: collections.abc.Mapping[str, object] | None = None, task: collections.abc.Mapping[str, object] | None = None, server: str | None = None, operation_kind: Optional[Literal['tool', 'prompt', 'resource']] = None, operation_name: str | None = None) -&gt; None`

A normalized form-mode elicitation request.

Public fields and methods:

- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['form']` (default: `'form'`).
- `message: &lt;class 'str'&gt;` (required).
- `requested_schema: collections.abc.Mapping[str, object]` (required).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).
- `task: collections.abc.Mapping[str, object] | None` (default: `None`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Optional[typing.Literal['tool', 'prompt', 'resource']]` (default: `None`).
- `operation_name: str | None` (default: `None`).

### `PendingElicitationRound`

`m3.elicitation.PendingElicitationRound(*, round_id: Annotated[str, MinLen(min_length=1)], execution_id: Annotated[str, MinLen(min_length=1)], logical_operation_id: Annotated[str, MinLen(min_length=1)], server: Annotated[str, MinLen(min_length=1)], operation_kind: Literal['tool', 'prompt', 'resource'], operation_name: Annotated[str, MinLen(min_length=1)], request_state: str | None = None, requests: collections.abc.Mapping[str, typing.Annotated[m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]], created_at: datetime.datetime, deadline: datetime.datetime | None = None) -&gt; None`

A persisted, keyed set of elicitation requests awaiting responses.

Public fields and methods:

- `round_id: &lt;class 'str'&gt;` (required).
- `execution_id: &lt;class 'str'&gt;` (required).
- `logical_operation_id: &lt;class 'str'&gt;` (required).
- `server: &lt;class 'str'&gt;` (required).
- `operation_kind: typing.Literal['tool', 'prompt', 'resource']` (required).
- `operation_name: &lt;class 'str'&gt;` (required).
- `request_state: str | None` (default: `None`).
- `requests: collections.abc.Mapping[str, typing.Annotated[m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest, FieldInfo(annotation=NoneType, required=True, discriminator='mode')]]` (required).
- `created_at: &lt;class 'datetime.datetime'&gt;` (required).
- `deadline: datetime.datetime | None` (default: `None`).

### `UrlElicitationRequest`

`m3.elicitation.UrlElicitationRequest(*, request_key: Annotated[str, MinLen(min_length=1)], mode: Literal['url'] = 'url', message: str, url: str, elicitation_id: str | None = None, meta: collections.abc.Mapping[str, object] | None = None, task: collections.abc.Mapping[str, object] | None = None, server: str | None = None, operation_kind: Optional[Literal['tool', 'prompt', 'resource']] = None, operation_name: str | None = None) -&gt; None`

A normalized URL-mode elicitation request.

Public fields and methods:

- `request_key: &lt;class 'str'&gt;` (required).
- `mode: typing.Literal['url']` (default: `'url'`).
- `message: &lt;class 'str'&gt;` (required).
- `url: &lt;class 'str'&gt;` (required).
- `elicitation_id: str | None` (default: `None`).
- `meta: collections.abc.Mapping[str, object] | None` (default: `None`).
- `task: collections.abc.Mapping[str, object] | None` (default: `None`).
- `server: str | None` (default: `None`).
- `operation_kind: typing.Optional[typing.Literal['tool', 'prompt', 'resource']]` (default: `None`).
- `operation_name: str | None` (default: `None`).

### `expect_form`

`m3.elicitation.expect_form(request_key: 'str', *, message: 'str | None' = None, schema: 'Mapping[str, object] | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `expect_url`

`m3.elicitation.expect_url(request_key: 'str', *, message: 'str | None' = None, url: 'str | None' = None, elicitation_id: 'str | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `maybe_form`

`m3.elicitation.maybe_form(request_key: 'str', *, message: 'str | None' = None, schema: 'Mapping[str, object] | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `maybe_url`

`m3.elicitation.maybe_url(request_key: 'str', *, message: 'str | None' = None, url: 'str | None' = None, elicitation_id: 'str | None' = None, server: 'object | None' = None, operation_kind: 'OperationKind | None' = None, operation_name: 'str | None' = None) -&gt; 'ElicitationPlan'`

### `one_of`

`m3.elicitation.one_of(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `optional`

`m3.elicitation.optional(child: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `round_of`

`m3.elicitation.round_of(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

### `sequence`

`m3.elicitation.sequence(*children: 'ElicitationPlan') -&gt; 'ElicitationPlan'`

## `m3.judges`

### `PROMPT_VERSION`

`m3.judges.PROMPT_VERSION`

str(object='') -&gt; str str(bytes_or_buffer[, encoding[, errors]]) -&gt; str

### `LLMJudge`

`m3.judges.LLMJudge(model: 'str', base_url: 'str | None' = None, api_key_env: 'str | None' = None, auth: 'str' = 'env', response_mode: 'str | None' = None, threshold: 'float' = 0.8, rubric: 'str | None' = None, rubric_id: 'str | None' = None, rubric_version: 'str | None' = None, timeout_seconds: 'float' = 30.0, max_retries: 'Literal[0, 1]' = 1) -&gt; None`

LLMJudge(model: 'str', base_url: 'str | None' = None, api_key_env: 'str | None' = None, auth: 'str' = 'env', response_mode: 'str | None' = None, threshold: 'float' = 0.8, rubric: 'str | None' = None, rubric_id: 'str | None' = None, rubric_version: 'str | None' = None, timeout_seconds: 'float' = 30.0, max_retries: 'Literal[0, 1]' = 1)

Public fields and methods:

- `evaluate_async(self, context: 'EvaluationContext') -&gt; 'EvaluationDecision'`

## `m3.managed_input_api`

### `HumanInput`

`m3.managed_input_api.HumanInput(*args, **kwargs)`

### `apply_human_input`

`m3.managed_input_api.apply_human_input(spec: 'object', value: 'object' = 'fail') -&gt; 'object'`

### `command_human_input`

`m3.managed_input_api.command_human_input(payload: 'Mapping[str, object]') -&gt; 'HumanInput'`

Read the durable policy, preserving fail as the old-envelope default.

### `managed_input_provider`

`m3.managed_input_api.managed_input_provider(store: 'object') -&gt; 'ManagedInputProvider'`

### `managed_store`

`m3.managed_input_api.managed_store(store: 'object') -&gt; 'ManagedInputStore'`

### `pending_elicitation`

`m3.managed_input_api.pending_elicitation(store: 'object', execution_id: 'object') -&gt; 'PendingElicitationRound | None'`

### `respond_elicitation`

`m3.managed_input_api.respond_elicitation(store: 'object', execution_id: 'object', round_id: 'str', responses: 'Mapping[str, ElicitationResponse]', *, idempotency_key: 'str') -&gt; 'None'`

### `validate_human_input`

`m3.managed_input_api.validate_human_input(value: 'object') -&gt; 'HumanInput'`

## `m3.storage`

### `ACPProbeDimension`

`m3.storage.ACPProbeDimension(*, profile_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], revision_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], probe_type: m3.services.acp_probes.ACPProbeKind, transport: Annotated[str, MinLen(min_length=1), MaxLen(max_length=32)] = 'stdio', agent_mode_id: Annotated[str | None, MaxLen(max_length=256)] = None, session_config: collections.abc.Mapping[str, JsonValue] = &lt;factory&gt;) -&gt; None`

Exact profile/revision and ACP request dimensions.

Public fields and methods:

- `profile_id: &lt;class 'str'&gt;` (required).
- `revision_id: &lt;class 'str'&gt;` (required).
- `probe_type: &lt;enum 'ACPProbeKind'&gt;` (required).
- `transport: &lt;class 'str'&gt;` (default: `'stdio'`).
- `agent_mode_id: str | None` (default: `None`).
- `session_config: collections.abc.Mapping[str, JsonValue]` (required).

### `ACPProbeResult`

`m3.storage.ACPProbeResult(*, profile_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], revision_id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)], probe_type: m3.services.acp_probes.ACPProbeKind, transport: Annotated[str, MinLen(min_length=1), MaxLen(max_length=32)] = 'stdio', agent_mode_id: Annotated[str | None, MaxLen(max_length=256)] = None, session_config: collections.abc.Mapping[str, JsonValue] = &lt;factory&gt;, id: Annotated[str, MinLen(min_length=1), MaxLen(max_length=256)] = &lt;factory&gt;, status: m3.services.acp_probes.ACPProbeStatus, agent_capabilities: collections.abc.Mapping[str, JsonValue] = &lt;factory&gt;, config_options: tuple[collections.abc.Mapping[str, JsonValue], ...] = (), evidence: collections.abc.Mapping[str, JsonValue] = &lt;factory&gt;, diagnostics: Annotated[str | None, MaxLen(max_length=65536)] = None, error: Annotated[str | None, MaxLen(max_length=2048)] = None, created_at: datetime.datetime = &lt;factory&gt;, started_at: datetime.datetime | None = None, finished_at: datetime.datetime | None = None, duration_ms: Annotated[float | None, Ge(ge=0)] = None, agent_identity: m3.services.acp_probes.ACPAgentIdentity | None = None, agent_modes: tuple[m3.services.acp_probes.ACPAgentMode, ...] = (), current_agent_mode_id: Annotated[str | None, MaxLen(max_length=256)] = None) -&gt; None`

One terminal or in-flight probe observation.

Public fields and methods:

- `profile_id: &lt;class 'str'&gt;` (required).
- `revision_id: &lt;class 'str'&gt;` (required).
- `probe_type: &lt;enum 'ACPProbeKind'&gt;` (required).
- `transport: &lt;class 'str'&gt;` (default: `'stdio'`).
- `agent_mode_id: str | None` (default: `None`).
- `session_config: collections.abc.Mapping[str, JsonValue]` (required).
- `id: &lt;class 'str'&gt;` (required).
- `status: &lt;enum 'ACPProbeStatus'&gt;` (required).
- `agent_capabilities: collections.abc.Mapping[str, JsonValue]` (required).
- `config_options: tuple[collections.abc.Mapping[str, JsonValue], ...]` (default: `()`).
- `evidence: collections.abc.Mapping[str, JsonValue]` (required).
- `diagnostics: str | None` (default: `None`).
- `error: str | None` (default: `None`).
- `created_at: &lt;class 'datetime.datetime'&gt;` (required).
- `started_at: datetime.datetime | None` (default: `None`).
- `finished_at: datetime.datetime | None` (default: `None`).
- `duration_ms: float | None` (default: `None`).
- `agent_identity: m3.services.acp_probes.ACPAgentIdentity | None` (default: `None`).
- `agent_modes: tuple[m3.services.acp_probes.ACPAgentMode, ...]` (default: `()`).
- `current_agent_mode_id: str | None` (default: `None`).

### `ACPProbeStore`

`m3.storage.ACPProbeStore(*args, **kwargs)`

Public fields and methods:

- `save_acp_probe(self, result: 'ACPProbeResult') -&gt; 'ACPProbeResult'`
- `get_acp_probe(self, probe_id: 'str') -&gt; 'ACPProbeResult | None'`
- `list_acp_probes(self, dimension: 'ACPProbeDimension | None' = None, *, include_inflight: 'bool' = True) -&gt; 'tuple[ACPProbeResult, ...]'`
- `latest_acp_probe(self, dimension: 'ACPProbeDimension') -&gt; 'ACPProbeResult | None'`

### `ArtifactNotFound`

`m3.storage.ArtifactNotFound`

An artifact or content-addressed blob is not present.

### `ArtifactStore`

`m3.storage.ArtifactStore(*args, **kwargs)`

Content-addressed artifact/blob store contract.

Public fields and methods:

- `put(self, execution_id: 'ExecutionId | str', name: 'str', content: 'bytes', *, media_type: 'str | None' = None) -&gt; 'ArtifactRef'`
- `get(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'bytes'`
- `get_ref(self, artifact_id: 'ArtifactId | str') -&gt; 'ArtifactRef'`
- `iter_refs(self, execution_id: 'ExecutionId | str | None' = None) -&gt; 'Iterator[ArtifactRef]'`
- `delete(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'None'`
- `cleanup(self) -&gt; 'None'`

### `BlobIntegrityError`

`m3.storage.BlobIntegrityError`

A compressed blob does not match its recorded hash or length.

### `BlobRecord`

`m3.storage.BlobRecord(sha256: 'str', size_bytes: 'int', compressed_size_bytes: 'int', path: 'Path') -&gt; None`

Verified metadata for one compressed content-addressed blob.

### `Command`

`m3.storage.Command(id: 'str', execution_id: 'ExecutionId', kind: 'str', status: 'str', payload: 'Mapping[str, Any]', session_id: 'SessionId | None' = None, turn_id: 'TurnId | None' = None) -&gt; None`

Command(id: 'str', execution_id: 'ExecutionId', kind: 'str', status: 'str', payload: 'Mapping[str, Any]', session_id: 'SessionId | None' = None, turn_id: 'TurnId | None' = None)

### `DurableSerializationError`

`m3.storage.DurableSerializationError(reason: 'str' = 'durable value is invalid') -&gt; 'None'`

A value cannot safely cross a durable specification boundary.

### `EventCallback`

`m3.storage.EventCallback(*args, **kwargs)`

### `ExecutionStore`

`m3.storage.ExecutionStore(*args, **kwargs)`

Store contract for immutable execution snapshots and event streams.

Public fields and methods:

- `create(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, object] | None' = None, provenance: 'Mapping[str, object] | None' = None, server_bindings: 'Sequence[Mapping[str, object]]' = (), harness_binding: 'Mapping[str, object] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `get_snapshot(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionState | None'`
- `get_execution_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`
- `list_executions(self, *, limit: 'int' = 50, offset: 'int' = 0, lifecycle: 'ExecutionStatus | str | None' = None, outcome: 'ExecutionOutcome | str | None' = None, run_id: 'RunId | str | None' = None, suite_id: 'int | None' = None, project_id: 'str | None' = None) -&gt; 'ExecutionPage'`
- `get_report(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1, event_limit: 'int | None' = None, artifact_limit: 'int | None' = None) -&gt; 'ExecutionReport | None'`
- `get_trace(self, execution_id: 'ExecutionId | str') -&gt; 'TraceResult | None'`
- `get_trace_view(self, execution_id: 'ExecutionId | str') -&gt; 'TraceView | None'`
- `save_evaluation(self, execution_id: 'ExecutionId | str', result: 'EvaluationResult | Mapping[str, object]', *, evaluation_id: 'str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'str'`
- `evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `aggregate_evaluations(self, query: 'EvaluationQuery') -&gt; 'EvaluationReport'`
- `turns(self, execution_id: 'ExecutionId | str') -&gt; 'tuple[tuple[TurnState, TurnResult | None], ...]'`
- `save_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `append_events(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append_event(self, event: 'Event', content: 'bytes', *, media_type: 'str') -&gt; 'Event'`
- `iter_events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'Iterator[Event]'`
- `transaction(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionTransaction'`
- `release(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`
- `subscribe(self, execution_id: 'ExecutionId | str', callback: 'EventCallback') -&gt; 'Callable[[], None]'`
- `put_raw_evidence(self, event_id: 'EventId | str', content: 'bytes', *, media_type: 'str') -&gt; 'EvidenceCapture'`
- `read_raw_evidence(self, reference: 'EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`
- `delete_execution(self, execution_id: 'ExecutionId | str') -&gt; 'None'`

### `ExecutionTransaction`

`m3.storage.ExecutionTransaction(*args, **kwargs)`

Uncommitted event batch used by :class:`ExecutionStore`.

Public fields and methods:

- `append(self, events: 'Sequence[Event]') -&gt; 'None'`
- `commit(self) -&gt; 'None'`
- `rollback(self) -&gt; 'None'`

### `FilesystemBlobStore`

`m3.storage.FilesystemBlobStore(root: 'str | os.PathLike[str]', *, max_read_bytes: 'int' = 536870912) -&gt; 'None'`

Atomic, compressed, content-addressed filesystem storage.

Public fields and methods:

- `path_for(self, sha256: 'str') -&gt; 'Path'`
- `put(self, content: 'bytes', *, sha256: 'str | None' = None, size_bytes: 'int | None' = None) -&gt; 'BlobRecord'`: Atomically persist ``content`` and return verified metadata.
- `put_blob(self, content: 'bytes', *, sha256: 'str | None' = None, size_bytes: 'int | None' = None) -&gt; 'BlobRecord'`: Atomically persist ``content`` and return verified metadata.
- `read(self, sha256: 'str', *, size_bytes: 'int | None' = None) -&gt; 'bytes'`
- `get(self, sha256: 'str', *, size_bytes: 'int | None' = None) -&gt; 'bytes'`
- `read_blob(self, sha256: 'str', *, size_bytes: 'int | None' = None) -&gt; 'bytes'`
- `verify(self, sha256: 'str', size_bytes: 'int') -&gt; 'BlobRecord'`
- `iter_records(self) -&gt; 'tuple[BlobRecord, ...]'`
- `garbage_collect(self, references: 'Mapping[str, int] | Iterable[str]') -&gt; 'tuple[str, ...]'`: Delete only explicitly unreferenced, valid blobs.
- `collect_garbage(self, references: 'Mapping[str, int] | Iterable[str]') -&gt; 'tuple[str, ...]'`: Delete only explicitly unreferenced, valid blobs.
- `cleanup_temporary_files(self) -&gt; 'tuple[Path, ...]'`: Remove incomplete temp files left by interrupted writers.

### `InMemoryArtifactStore`

`m3.storage.InMemoryArtifactStore(*, config: 'RedactionConfig | None' = None) -&gt; 'None'`

In-memory compressed, content-addressed artifact store.

Public fields and methods:

- `put(self, execution_id: 'ExecutionId | str', name: 'str', content: 'bytes', *, media_type: 'str | None' = None) -&gt; 'ArtifactRef'`
- `get(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'bytes'`
- `get_ref(self, artifact_id: 'ArtifactId | str') -&gt; 'ArtifactRef'`
- `iter_refs(self, execution_id: 'ExecutionId | str | None' = None) -&gt; 'Iterator[ArtifactRef]'`
- `delete(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'None'`
- `cleanup(self) -&gt; 'None'`
- `close(self) -&gt; 'None'`

### `InMemoryExecutionStore`

`m3.storage.InMemoryExecutionStore(*, config: 'RedactionConfig | None' = None, capture_config: 'CaptureOptions | None' = None) -&gt; 'None'`

Thread-safe execution metadata store with commit-gated visibility.

Public fields and methods:

- `save_acp_probe(self, result: 'ACPProbeResult') -&gt; 'ACPProbeResult'`
- `get_acp_probe(self, probe_id: 'str') -&gt; 'ACPProbeResult | None'`
- `list_acp_probes(self, dimension: 'ACPProbeDimension | None' = None, *, include_inflight: 'bool' = True) -&gt; 'tuple[ACPProbeResult, ...]'`
- `latest_acp_probe(self, dimension: 'ACPProbeDimension') -&gt; 'ACPProbeResult | None'`
- `create(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, object] | None' = None, provenance: 'Mapping[str, object] | None' = None, server_bindings: 'Sequence[Mapping[str, object]]' = (), harness_binding: 'Mapping[str, object] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `ensure_project(self, project_id: 'str', project_name: 'str') -&gt; 'tuple[str, str]'`
- `get_project(self, project_id: 'str') -&gt; 'tuple[str, str] | None'`
- `ensure_suite(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite'`
- `get_suite(self, suite_id: 'SuiteId | str') -&gt; 'Suite | None'`
- `get_suite_by_name(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite | None'`
- `list_suites(self) -&gt; 'tuple[Suite, ...]'`
- `create_execution(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, object] | None' = None, provenance: 'Mapping[str, object] | None' = None, server_bindings: 'Sequence[Mapping[str, object]]' = (), harness_binding: 'Mapping[str, object] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `save_test_run(self, run_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `get_test_run(self, run_id: 'str') -&gt; 'Mapping[str, object] | None'`
- `list_test_runs(self) -&gt; 'tuple[Mapping[str, object], ...]'`
- `list_test_run_page(self, *, limit: 'int | None' = None, offset: 'int' = 0, suite_id: 'int | None' = None, project_id: 'str | None' = None, q: 'str | None' = None) -&gt; 'tuple[tuple[Mapping[str, object], ...], int]'`: Return newest-first run manifests with their suites, and the total.
- `save_test_result(self, run_id: 'str', attempt_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `list_test_results(self, run_id: 'str') -&gt; 'tuple[Mapping[str, object], ...]'`
- `get_snapshot(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionState | None'`
- `get_execution_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`
- `list_executions(self, *, limit: 'int' = 50, offset: 'int' = 0, lifecycle: 'ExecutionStatus | str | None' = None, outcome: 'ExecutionOutcome | str | None' = None, run_id: 'RunId | str | None' = None, suite_id: 'int | None' = None, project_id: 'str | None' = None) -&gt; 'ExecutionPage'`
- `get_report(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1, event_limit: 'int | None' = None, artifact_limit: 'int | None' = None) -&gt; 'ExecutionReport | None'`
- `save_evaluation(self, execution_id: 'ExecutionId | str', result: 'EvaluationResult | Mapping[str, object]', *, evaluation_id: 'str | None' = None, turn_id: 'object | None' = None) -&gt; 'str'`
- `save_turn(self, snapshot: 'TurnState', result: 'TurnResult | None' = None) -&gt; 'None'`
- `append_turn(self, snapshot: 'TurnState', result: 'TurnResult | None' = None) -&gt; 'None'`
- `turns(self, execution_id: 'ExecutionId | str') -&gt; 'tuple[tuple[TurnState, TurnResult | None], ...]'`
- `evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'object | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `aggregate_evaluations(self, query: 'EvaluationQuery') -&gt; 'EvaluationReport'`: Calculate summaries from the evaluations currently in memory.
- `get_trace(self, execution_id: 'ExecutionId | str') -&gt; 'TraceResult | None'`
- `get_trace_view(self, execution_id: 'ExecutionId | str') -&gt; 'TraceView | None'`
- `save_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `update_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `append_events(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append_event(self, event: 'Event', content: 'bytes', *, media_type: 'str') -&gt; 'Event'`: Commit an event and its raw blob as one in-memory operation.
- `iter_events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'Iterator[Event]'`
- `events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'tuple[Event, ...]'`
- `transaction(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionTransaction'`
- `subscribe(self, execution_id: 'ExecutionId | str', callback: 'EventCallback') -&gt; 'Callable[[], None]'`
- `allocate(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`: Reserve the next per-execution sequence numbers.
- `allocate_sequence(self, execution_id: 'ExecutionId | str') -&gt; 'int'`
- `allocate_sequences(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`: Reserve the next per-execution sequence numbers.
- `release(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`: Release uncommitted reservations after producer cancellation.
- `release_sequences(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`: Release uncommitted reservations after producer cancellation.
- `close(self) -&gt; 'None'`
- `put_raw_evidence(self, event_id: 'EventId | str', content: 'bytes', *, media_type: 'str') -&gt; 'EvidenceCapture'`
- `read_raw_evidence(self, reference: 'EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`
- `delete_execution(self, execution_id: 'ExecutionId | str') -&gt; 'None'`
- `delete(self, execution_id: 'ExecutionId | str') -&gt; 'None'`

### `Lease`

`m3.storage.Lease(execution_id: 'ExecutionId', owner_id: 'str', lease_token: 'str', expires_at: 'datetime') -&gt; None`

Lease(execution_id: 'ExecutionId', owner_id: 'str', lease_token: 'str', expires_at: 'datetime')

### `ManagedInputLease`

`m3.storage.ManagedInputLease(*, owner_id: Annotated[str, MinLen(min_length=1)], lease_token: Annotated[str, MinLen(min_length=1)], expires_at: datetime.datetime) -&gt; None`

The compare-and-set token currently allowed to mutate a round.

Public fields and methods:

- `owner_id: &lt;class 'str'&gt;` (required).
- `lease_token: &lt;class 'str'&gt;` (required).
- `expires_at: &lt;class 'datetime.datetime'&gt;` (required).

### `ManagedInputRecord`

`m3.storage.ManagedInputRecord(*, pending: m3.elicitation.PendingElicitationRound, round_index: Annotated[int, Ge(ge=0)], round_limit: Annotated[int, Gt(gt=0)], status: Literal['pending', 'response_validated', 'delivery_started', 'delivered', 'resolved', 'failed'], lease: m3.storage.managed_input.ManagedInputLease, responses: collections.abc.Mapping[str, m3.elicitation.ElicitationResponse] | None = None, response_idempotency_key: str | None = None, harness_session_id: str | None = None, native_resume_token: str | None = None, delivery_idempotency_key: str | None = None, session_id: str | None = None, turn_id: str | None = None, operation_parameters: collections.abc.Mapping[str, object] = &lt;factory&gt;, delivery_attempts: Annotated[int, Ge(ge=0)] = 0, created_at: datetime.datetime, updated_at: datetime.datetime, response_validated_at: datetime.datetime | None = None, delivery_started_at: datetime.datetime | None = None, delivered_at: datetime.datetime | None = None, resolved_at: datetime.datetime | None = None, failed_at: datetime.datetime | None = None, failure_code: str | None = None, failure_message: str | None = None) -&gt; None`

Immutable view of one durable managed-input round.

Public fields and methods:

- `pending: &lt;class 'm3.elicitation.PendingElicitationRound'&gt;` (required).
- `round_index: &lt;class 'int'&gt;` (required).
- `round_limit: &lt;class 'int'&gt;` (required).
- `status: typing.Literal['pending', 'response_validated', 'delivery_started', 'delivered', 'resolved', 'failed']` (required).
- `lease: &lt;class 'm3.storage.managed_input.ManagedInputLease'&gt;` (required).
- `responses: collections.abc.Mapping[str, m3.elicitation.ElicitationResponse] | None` (default: `None`).
- `response_idempotency_key: str | None` (default: `None`).
- `harness_session_id: str | None` (default: `None`).
- `native_resume_token: str | None` (default: `None`).
- `delivery_idempotency_key: str | None` (default: `None`).
- `session_id: str | None` (default: `None`).
- `turn_id: str | None` (default: `None`).
- `operation_parameters: collections.abc.Mapping[str, object]` (required).
- `delivery_attempts: &lt;class 'int'&gt;` (default: `0`).
- `created_at: &lt;class 'datetime.datetime'&gt;` (required).
- `updated_at: &lt;class 'datetime.datetime'&gt;` (required).
- `response_validated_at: datetime.datetime | None` (default: `None`).
- `delivery_started_at: datetime.datetime | None` (default: `None`).
- `delivered_at: datetime.datetime | None` (default: `None`).
- `resolved_at: datetime.datetime | None` (default: `None`).
- `failed_at: datetime.datetime | None` (default: `None`).
- `failure_code: str | None` (default: `None`).
- `failure_message: str | None` (default: `None`).

### `ManagedInputStatus`

`m3.storage.ManagedInputStatus(*args, **kwargs)`

### `ManagedInputStore`

`m3.storage.ManagedInputStore(*args, **kwargs)`

Storage contract consumed by future execution handles.

Public fields and methods:

- `create_round(self, pending: 'PendingElicitationRound', *, round_index: 'int', round_limit: 'int', owner_id: 'str', lease_seconds: 'float', lease_token: 'str | None' = None, harness_session_id: 'str | None' = None, native_resume_token: 'str | None' = None, delivery_idempotency_key: 'str | None' = None, session_id: 'str | None' = None, turn_id: 'str | None' = None, operation_parameters: 'Mapping[str, object] | None' = None) -&gt; 'ManagedInputRecord'`
- `get_round(self, execution_id: 'str', round_id: 'str') -&gt; 'ManagedInputRecord | None'`
- `list_rounds(self, execution_id: 'str') -&gt; 'tuple[ManagedInputRecord, ...]'`
- `claim_round(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_seconds: 'float', expected_lease_token: 'str | None' = None, delivery_state: "Literal['not_started', 'not_delivered', 'delivered'] | None" = None, idempotent_delivery: 'bool' = False) -&gt; 'ManagedInputRecord'`
- `renew(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str', lease_seconds: 'float') -&gt; 'ManagedInputRecord'`
- `submit_responses(self, execution_id: 'str', round_id: 'str', responses: 'Mapping[str, ElicitationResponse]', *, owner_id: 'str', lease_token: 'str', response_idempotency_key: 'str') -&gt; 'ManagedInputRecord'`
- `start_delivery(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str') -&gt; 'ManagedInputRecord'`
- `mark_delivered(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str') -&gt; 'ManagedInputRecord'`
- `resolve(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str') -&gt; 'ManagedInputRecord'`
- `fail(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str', code: 'str', message: 'str') -&gt; 'ManagedInputRecord'`
- `fail_recovery(self, execution_id: 'str', round_id: 'str', *, expected_lease_token: 'str', message: 'str') -&gt; 'ManagedInputRecord'`
- `redacted_responses(self, execution_id: 'str', round_id: 'str') -&gt; 'Mapping[str, object] | None'`

### `PersistentExecutionStore`

`m3.storage.PersistentExecutionStore(database: 'str | Path', *, blob_root: 'str | Path | None' = None, config: 'RedactionConfig | None' = None, capture_config: 'CaptureOptions | None' = None, payload_blob_threshold: 'int' = 65536, **kwargs: 'Any') -&gt; 'None'`

SQLite implementation of the public :class:`ExecutionStore` protocol.

Public fields and methods:

- `resolve_managed_input_store(self) -&gt; 'SQLiteManagedInputStore'`: Resolve managed-input storage lazily for opted-in executions.
- `ensure_project(self, project_id: 'str', project_name: 'str') -&gt; 'tuple[str, str]'`: Register a stable project identity and refresh its display name.
- `get_project(self, project_id: 'str') -&gt; 'tuple[str, str] | None'`
- `ensure_suite(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite'`
- `get_suite(self, suite_id: 'SuiteId | str') -&gt; 'Suite | None'`
- `get_suite_by_name(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite | None'`
- `list_suites(self) -&gt; 'tuple[Suite, ...]'`
- `create(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, Any] | None' = None, provenance: 'Mapping[str, Any] | None' = None, server_bindings: 'Sequence[Mapping[str, Any]]' = (), harness_binding: 'Mapping[str, Any] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `create_execution(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, Any] | None' = None, provenance: 'Mapping[str, Any] | None' = None, server_bindings: 'Sequence[Mapping[str, Any]]' = (), harness_binding: 'Mapping[str, Any] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `save_test_run(self, run_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `get_test_run(self, run_id: 'str') -&gt; 'Mapping[str, object] | None'`
- `list_test_runs(self) -&gt; 'tuple[Mapping[str, object], ...]'`
- `list_test_run_page(self, *, limit: 'int | None' = None, offset: 'int' = 0, suite_id: 'int | None' = None, project_id: 'str | None' = None, q: 'str | None' = None) -&gt; 'tuple[tuple[Mapping[str, object], ...], int]'`: Return newest-first run manifests with their suites, and the total.
- `save_test_result(self, run_id: 'str', attempt_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `list_test_results(self, run_id: 'str') -&gt; 'tuple[Mapping[str, object], ...]'`
- `get_snapshot(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionState | None'`
- `get_execution_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`: Return the immutable typed submission spec, if one was saved.
- `get_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`: Return the immutable typed submission spec, if one was saved.
- `list_executions(self, *, limit: 'int' = 50, offset: 'int' = 0, lifecycle: 'ExecutionStatus | str | None' = None, outcome: 'ExecutionOutcome | str | None' = None, run_id: 'str | None' = None, suite_id: 'int | None' = None, project_id: 'str | None' = None) -&gt; 'ExecutionPage'`
- `get_report(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1, event_limit: 'int | None' = None, artifact_limit: 'int | None' = None) -&gt; 'ExecutionReport | None'`
- `get_trace(self, execution_id: 'ExecutionId | str') -&gt; 'TraceResult | None'`
- `get_trace_view(self, execution_id: 'ExecutionId | str') -&gt; 'TraceView | None'`
- `save_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `update_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `append_events(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append_event(self, event: 'Event', content: 'bytes', *, media_type: 'str') -&gt; 'Event'`: Commit an event and its raw blob in one SQLite transaction.
- `iter_events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'Iterator[Event]'`
- `events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'tuple[Event, ...]'`
- `create_session(self, execution_id: 'ExecutionId | str', session_id: 'SessionId | str', *, state: 'str' = 'open') -&gt; 'SessionId'`
- `close_session(self, session_id: 'SessionId | str') -&gt; 'None'`
- `save_turn(self, snapshot: 'TurnState', result: 'TurnResult | Mapping[str, Any] | None' = None) -&gt; 'None'`
- `append_turn(self, snapshot: 'TurnState', result: 'TurnResult | Mapping[str, Any] | None' = None) -&gt; 'None'`
- `turns(self, execution_id: 'ExecutionId | str') -&gt; 'tuple[tuple[TurnState, TurnResult | None], ...]'`
- `save_evaluation(self, execution_id: 'ExecutionId | str', result: 'Mapping[str, Any] | Any', *, evaluation_id: 'str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'str'`
- `reserve_judge_request(self, run_id: 'str', limit: 'int') -&gt; 'bool'`: Atomically reserve one judge request for a run across workers.
- `save(self, result: 'EvaluationResult') -&gt; 'None'`
- `get(self, evaluation_id: 'str') -&gt; 'EvaluationResult | None'`
- `all(self) -&gt; 'tuple[EvaluationResult, ...]'`
- `evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `aggregate_evaluations(self, query: 'EvaluationQuery') -&gt; 'EvaluationReport'`: Calculate summaries from persisted evaluations and execution traces.
- `persisted_evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `evaluation_json(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[Mapping[str, Any], ...]'`
- `transaction(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionTransaction'`
- `allocate(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`
- `allocate_sequence(self, execution_id)`
- `allocate_sequences(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`
- `release(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`
- `release_sequences(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`
- `subscribe(self, execution_id: 'ExecutionId | str', callback: 'EventCallback') -&gt; 'Callable[[], None]'`
- `save_acp_probe(self, result: 'ACPProbeResult') -&gt; 'ACPProbeResult'`: Persist one redacted ACP probe result and return its safe copy.
- `get_acp_probe(self, probe_id: 'str') -&gt; 'ACPProbeResult | None'`
- `list_acp_probes(self, dimension: 'ACPProbeDimension | None' = None, *, include_inflight: 'bool' = True) -&gt; 'tuple[ACPProbeResult, ...]'`
- `latest_acp_probe(self, dimension: 'ACPProbeDimension') -&gt; 'ACPProbeResult | None'`
- `create_profile(self, kind: 'str', name: 'str', value: 'Mapping[str, Any]', *, description: 'str' = '', profile_id: 'str | None' = None, revision_id: 'str | None' = None) -&gt; 'ProfileRecord'`
- `create_server_profile(self, name: 'str', value: 'Mapping[str, Any]', **kwargs: 'Any') -&gt; 'ProfileRecord'`
- `create_harness_profile(self, name: 'str', value: 'Mapping[str, Any]', **kwargs: 'Any') -&gt; 'ProfileRecord'`
- `get_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord | None'`
- `resolve_profile(self, profile_id: 'str', selection: 'RevisionSelection', *, kind: "Literal['server', 'harness']") -&gt; 'tuple[ProfileRecord | None, ProfileRevisionRecord | None]'`: Read one profile and its selected immutable revision.
- `list_profiles(self, kind: 'str', *, include_archived: 'bool' = False) -&gt; 'tuple[ProfileRecord, ...]'`: List one profile family in stable name/id order.
- `list_profile_revisions(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'tuple[ProfileRevisionRecord, ...]'`: Return every revision ordered by revision number then id.
- `update_profile(self, profile_id: 'str', *, name: 'str | None' = None, description: 'str | None' = None, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`: Update mutable metadata without changing the immutable revision.
- `add_revision(self, profile_id: 'str', value: 'Mapping[str, Any]', *, revision_id: 'str | None' = None, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord'`
- `archive_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`
- `restore_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`
- `resolve_revision(self, profile_id: 'str', selection: 'RevisionSelection | str' = 'latest', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord'`
- `get_revision(self, revision_id: 'RevisionId | str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord | None'`
- `enqueue_command(self, execution_id: 'ExecutionId | str', kind: 'str' = 'execution', payload: 'Mapping[str, Any] | None' = None, *, command_id: 'str | None' = None, session_id: 'SessionId | str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'Command'`
- `enqueue(self, execution_id: 'ExecutionId | str', kind: 'str' = 'execution', payload: 'Mapping[str, Any] | None' = None, *, command_id: 'str | None' = None, session_id: 'SessionId | str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'Command'`
- `get_command(self, command_id: 'str') -&gt; 'Command | None'`
- `claim_next(self, owner_id: 'str', *, lease_seconds: 'float' = 30.0) -&gt; 'tuple[Command, Lease] | None'`
- `claim(self, owner_id: 'str', *, lease_seconds: 'float' = 30.0) -&gt; 'tuple[Command, Lease] | None'`
- `heartbeat(self, lease: 'Lease | str', *, owner_id: 'str | None' = None, lease_seconds: 'float' = 30.0) -&gt; 'bool'`
- `renew_lease(self, lease: 'Lease | str', *, owner_id: 'str | None' = None, lease_seconds: 'float' = 30.0) -&gt; 'bool'`
- `mark_interrupted_if_lease_lost(self, lease: 'Lease', *, reason: 'str' = 'worker lease lost') -&gt; 'bool'`: Atomically close work whose owner can no longer renew its lease.
- `mark_managed_recovery_unavailable_if_lease_lost(self, lease: 'Lease', *, reason: 'str' = 'managed interaction cannot be resumed safely after worker loss') -&gt; 'bool'`: Terminalize managed input when its owning worker is lost.
- `release_lease(self, lease: 'Lease | str', *, owner_id: 'str | None' = None) -&gt; 'bool'`
- `complete_command(self, command_id: 'str', *, owner_id: 'str', lease_token: 'str', status: 'str' = 'done') -&gt; 'bool'`: Mark one claimed command terminal under its current lease.
- `request_cancel(self, execution_id: 'ExecutionId | str', reason: 'str | None' = None) -&gt; 'bool'`
- `cancel(self, execution_id: 'ExecutionId | str', reason: 'str | None' = None) -&gt; 'bool'`
- `finalize_cancelled(self, execution_id: 'ExecutionId | str', *, reason: 'str' = 'cancelled') -&gt; 'bool'`: Persist a cancellation terminal event when a worker observed it.
- `cancellation_requested(self, execution_id: 'ExecutionId | str') -&gt; 'bool'`
- `mark_stale_interrupted(self, *, now: 'datetime | None' = None) -&gt; 'tuple[ExecutionId, ...]'`
- `delete_execution(self, execution_id: 'ExecutionId | str') -&gt; 'None'`
- `delete(self, execution_id: 'ExecutionId | str') -&gt; 'None'`
- `put_raw_evidence(self, event_id: 'EventId | str', content: 'bytes', *, media_type: 'str') -&gt; 'EvidenceCapture'`: Redact, bound, and durably associate evidence with one event.
- `read_raw_evidence(self, reference: 'EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`
- `clone_execution(self, execution_id: 'ExecutionId | str', *, use_latest: 'bool' = False) -&gt; 'ExecutionId'`
- `clone(self, execution_id: 'ExecutionId | str', *, use_latest: 'bool' = False) -&gt; 'ExecutionId'`
- `resolved_bindings(self, execution_id: 'ExecutionId | str') -&gt; 'Mapping[str, Any]'`: Return the immutable, submission-time profile binding snapshot.
- `close(self) -&gt; 'None'`

### `ProfileRecord`

`m3.storage.ProfileRecord(id: 'str', kind: 'str', name: 'str', description: 'str', archived: 'bool', current_revision_id: 'RevisionId | None', created_at: 'datetime', updated_at: 'datetime') -&gt; None`

ProfileRecord(id: 'str', kind: 'str', name: 'str', description: 'str', archived: 'bool', current_revision_id: 'RevisionId | None', created_at: 'datetime', updated_at: 'datetime')

### `ProfileResolver`

`m3.storage.ProfileResolver(*args, **kwargs)`

Read-only saved-profile lookup used by execution runtimes.

Public fields and methods:

- `resolve_profile(self, profile_id: 'str', selection: 'RevisionSelection', *, kind: "Literal['server', 'harness']") -&gt; 'tuple[Any, Any]'`

### `ProfileRevisionRecord`

`m3.storage.ProfileRevisionRecord(id: 'RevisionId', profile_id: 'str', revision_number: 'int', value: 'Mapping[str, Any]', created_at: 'datetime') -&gt; None`

ProfileRevisionRecord(id: 'RevisionId', profile_id: 'str', revision_number: 'int', value: 'Mapping[str, Any]', created_at: 'datetime')

### `SQLiteArtifactStore`

`m3.storage.SQLiteArtifactStore(database: 'str | Path', blob_root: 'str | Path | None' = None, **kwargs: 'Any') -&gt; 'None'`

Filesystem-backed content-addressed artifact store with SQLite refs.

Public fields and methods:

- `put(self, execution_id: 'ExecutionId | str', name: 'str', content: 'bytes', *, media_type: 'str | None' = None) -&gt; 'ArtifactRef'`
- `get(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'bytes'`
- `get_ref(self, artifact_id: 'ArtifactId | str') -&gt; 'ArtifactRef'`
- `iter_refs(self, execution_id: 'ExecutionId | str | None' = None) -&gt; 'Iterator[ArtifactRef]'`
- `delete(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'None'`
- `cleanup(self) -&gt; 'None'`

### `SQLiteExecutionStore`

`m3.storage.SQLiteExecutionStore(database: 'str | Path', *, blob_root: 'str | Path | None' = None, config: 'RedactionConfig | None' = None, capture_config: 'CaptureOptions | None' = None, payload_blob_threshold: 'int' = 65536, **kwargs: 'Any') -&gt; 'None'`

SQLite implementation of the public :class:`ExecutionStore` protocol.

Public fields and methods:

- `resolve_managed_input_store(self) -&gt; 'SQLiteManagedInputStore'`: Resolve managed-input storage lazily for opted-in executions.
- `ensure_project(self, project_id: 'str', project_name: 'str') -&gt; 'tuple[str, str]'`: Register a stable project identity and refresh its display name.
- `get_project(self, project_id: 'str') -&gt; 'tuple[str, str] | None'`
- `ensure_suite(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite'`
- `get_suite(self, suite_id: 'SuiteId | str') -&gt; 'Suite | None'`
- `get_suite_by_name(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite | None'`
- `list_suites(self) -&gt; 'tuple[Suite, ...]'`
- `create(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, Any] | None' = None, provenance: 'Mapping[str, Any] | None' = None, server_bindings: 'Sequence[Mapping[str, Any]]' = (), harness_binding: 'Mapping[str, Any] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `create_execution(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, Any] | None' = None, provenance: 'Mapping[str, Any] | None' = None, server_bindings: 'Sequence[Mapping[str, Any]]' = (), harness_binding: 'Mapping[str, Any] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `save_test_run(self, run_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `get_test_run(self, run_id: 'str') -&gt; 'Mapping[str, object] | None'`
- `list_test_runs(self) -&gt; 'tuple[Mapping[str, object], ...]'`
- `list_test_run_page(self, *, limit: 'int | None' = None, offset: 'int' = 0, suite_id: 'int | None' = None, project_id: 'str | None' = None, q: 'str | None' = None) -&gt; 'tuple[tuple[Mapping[str, object], ...], int]'`: Return newest-first run manifests with their suites, and the total.
- `save_test_result(self, run_id: 'str', attempt_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `list_test_results(self, run_id: 'str') -&gt; 'tuple[Mapping[str, object], ...]'`
- `get_snapshot(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionState | None'`
- `get_execution_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`: Return the immutable typed submission spec, if one was saved.
- `get_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`: Return the immutable typed submission spec, if one was saved.
- `list_executions(self, *, limit: 'int' = 50, offset: 'int' = 0, lifecycle: 'ExecutionStatus | str | None' = None, outcome: 'ExecutionOutcome | str | None' = None, run_id: 'str | None' = None, suite_id: 'int | None' = None, project_id: 'str | None' = None) -&gt; 'ExecutionPage'`
- `get_report(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1, event_limit: 'int | None' = None, artifact_limit: 'int | None' = None) -&gt; 'ExecutionReport | None'`
- `get_trace(self, execution_id: 'ExecutionId | str') -&gt; 'TraceResult | None'`
- `get_trace_view(self, execution_id: 'ExecutionId | str') -&gt; 'TraceView | None'`
- `save_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `update_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `append_events(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append_event(self, event: 'Event', content: 'bytes', *, media_type: 'str') -&gt; 'Event'`: Commit an event and its raw blob in one SQLite transaction.
- `iter_events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'Iterator[Event]'`
- `events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'tuple[Event, ...]'`
- `create_session(self, execution_id: 'ExecutionId | str', session_id: 'SessionId | str', *, state: 'str' = 'open') -&gt; 'SessionId'`
- `close_session(self, session_id: 'SessionId | str') -&gt; 'None'`
- `save_turn(self, snapshot: 'TurnState', result: 'TurnResult | Mapping[str, Any] | None' = None) -&gt; 'None'`
- `append_turn(self, snapshot: 'TurnState', result: 'TurnResult | Mapping[str, Any] | None' = None) -&gt; 'None'`
- `turns(self, execution_id: 'ExecutionId | str') -&gt; 'tuple[tuple[TurnState, TurnResult | None], ...]'`
- `save_evaluation(self, execution_id: 'ExecutionId | str', result: 'Mapping[str, Any] | Any', *, evaluation_id: 'str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'str'`
- `reserve_judge_request(self, run_id: 'str', limit: 'int') -&gt; 'bool'`: Atomically reserve one judge request for a run across workers.
- `save(self, result: 'EvaluationResult') -&gt; 'None'`
- `get(self, evaluation_id: 'str') -&gt; 'EvaluationResult | None'`
- `all(self) -&gt; 'tuple[EvaluationResult, ...]'`
- `evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `aggregate_evaluations(self, query: 'EvaluationQuery') -&gt; 'EvaluationReport'`: Calculate summaries from persisted evaluations and execution traces.
- `persisted_evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `evaluation_json(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[Mapping[str, Any], ...]'`
- `transaction(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionTransaction'`
- `allocate(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`
- `allocate_sequence(self, execution_id)`
- `allocate_sequences(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`
- `release(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`
- `release_sequences(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`
- `subscribe(self, execution_id: 'ExecutionId | str', callback: 'EventCallback') -&gt; 'Callable[[], None]'`
- `save_acp_probe(self, result: 'ACPProbeResult') -&gt; 'ACPProbeResult'`: Persist one redacted ACP probe result and return its safe copy.
- `get_acp_probe(self, probe_id: 'str') -&gt; 'ACPProbeResult | None'`
- `list_acp_probes(self, dimension: 'ACPProbeDimension | None' = None, *, include_inflight: 'bool' = True) -&gt; 'tuple[ACPProbeResult, ...]'`
- `latest_acp_probe(self, dimension: 'ACPProbeDimension') -&gt; 'ACPProbeResult | None'`
- `create_profile(self, kind: 'str', name: 'str', value: 'Mapping[str, Any]', *, description: 'str' = '', profile_id: 'str | None' = None, revision_id: 'str | None' = None) -&gt; 'ProfileRecord'`
- `create_server_profile(self, name: 'str', value: 'Mapping[str, Any]', **kwargs: 'Any') -&gt; 'ProfileRecord'`
- `create_harness_profile(self, name: 'str', value: 'Mapping[str, Any]', **kwargs: 'Any') -&gt; 'ProfileRecord'`
- `get_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord | None'`
- `resolve_profile(self, profile_id: 'str', selection: 'RevisionSelection', *, kind: "Literal['server', 'harness']") -&gt; 'tuple[ProfileRecord | None, ProfileRevisionRecord | None]'`: Read one profile and its selected immutable revision.
- `list_profiles(self, kind: 'str', *, include_archived: 'bool' = False) -&gt; 'tuple[ProfileRecord, ...]'`: List one profile family in stable name/id order.
- `list_profile_revisions(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'tuple[ProfileRevisionRecord, ...]'`: Return every revision ordered by revision number then id.
- `update_profile(self, profile_id: 'str', *, name: 'str | None' = None, description: 'str | None' = None, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`: Update mutable metadata without changing the immutable revision.
- `add_revision(self, profile_id: 'str', value: 'Mapping[str, Any]', *, revision_id: 'str | None' = None, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord'`
- `archive_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`
- `restore_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`
- `resolve_revision(self, profile_id: 'str', selection: 'RevisionSelection | str' = 'latest', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord'`
- `get_revision(self, revision_id: 'RevisionId | str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord | None'`
- `enqueue_command(self, execution_id: 'ExecutionId | str', kind: 'str' = 'execution', payload: 'Mapping[str, Any] | None' = None, *, command_id: 'str | None' = None, session_id: 'SessionId | str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'Command'`
- `enqueue(self, execution_id: 'ExecutionId | str', kind: 'str' = 'execution', payload: 'Mapping[str, Any] | None' = None, *, command_id: 'str | None' = None, session_id: 'SessionId | str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'Command'`
- `get_command(self, command_id: 'str') -&gt; 'Command | None'`
- `claim_next(self, owner_id: 'str', *, lease_seconds: 'float' = 30.0) -&gt; 'tuple[Command, Lease] | None'`
- `claim(self, owner_id: 'str', *, lease_seconds: 'float' = 30.0) -&gt; 'tuple[Command, Lease] | None'`
- `heartbeat(self, lease: 'Lease | str', *, owner_id: 'str | None' = None, lease_seconds: 'float' = 30.0) -&gt; 'bool'`
- `renew_lease(self, lease: 'Lease | str', *, owner_id: 'str | None' = None, lease_seconds: 'float' = 30.0) -&gt; 'bool'`
- `mark_interrupted_if_lease_lost(self, lease: 'Lease', *, reason: 'str' = 'worker lease lost') -&gt; 'bool'`: Atomically close work whose owner can no longer renew its lease.
- `mark_managed_recovery_unavailable_if_lease_lost(self, lease: 'Lease', *, reason: 'str' = 'managed interaction cannot be resumed safely after worker loss') -&gt; 'bool'`: Terminalize managed input when its owning worker is lost.
- `release_lease(self, lease: 'Lease | str', *, owner_id: 'str | None' = None) -&gt; 'bool'`
- `complete_command(self, command_id: 'str', *, owner_id: 'str', lease_token: 'str', status: 'str' = 'done') -&gt; 'bool'`: Mark one claimed command terminal under its current lease.
- `request_cancel(self, execution_id: 'ExecutionId | str', reason: 'str | None' = None) -&gt; 'bool'`
- `cancel(self, execution_id: 'ExecutionId | str', reason: 'str | None' = None) -&gt; 'bool'`
- `finalize_cancelled(self, execution_id: 'ExecutionId | str', *, reason: 'str' = 'cancelled') -&gt; 'bool'`: Persist a cancellation terminal event when a worker observed it.
- `cancellation_requested(self, execution_id: 'ExecutionId | str') -&gt; 'bool'`
- `mark_stale_interrupted(self, *, now: 'datetime | None' = None) -&gt; 'tuple[ExecutionId, ...]'`
- `delete_execution(self, execution_id: 'ExecutionId | str') -&gt; 'None'`
- `delete(self, execution_id: 'ExecutionId | str') -&gt; 'None'`
- `put_raw_evidence(self, event_id: 'EventId | str', content: 'bytes', *, media_type: 'str') -&gt; 'EvidenceCapture'`: Redact, bound, and durably associate evidence with one event.
- `read_raw_evidence(self, reference: 'EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`
- `clone_execution(self, execution_id: 'ExecutionId | str', *, use_latest: 'bool' = False) -&gt; 'ExecutionId'`
- `clone(self, execution_id: 'ExecutionId | str', *, use_latest: 'bool' = False) -&gt; 'ExecutionId'`
- `resolved_bindings(self, execution_id: 'ExecutionId | str') -&gt; 'Mapping[str, Any]'`: Return the immutable, submission-time profile binding snapshot.
- `close(self) -&gt; 'None'`

### `SQLiteManagedInputStore`

`m3.storage.SQLiteManagedInputStore(database: 'str | Path', *, busy_timeout_ms: 'int' = 5000, clock: 'Callable[[], datetime]' = &lt;function _now&gt;) -&gt; 'None'`

SQLite-backed managed-input storage sharing a database path safely.

Public fields and methods:

- `get_round(self, execution_id: 'str', round_id: 'str') -&gt; 'ManagedInputRecord | None'`
- `list_rounds(self, execution_id: 'str') -&gt; 'tuple[ManagedInputRecord, ...]'`
- `create_round(self, pending: 'PendingElicitationRound', *, round_index: 'int', round_limit: 'int', owner_id: 'str', lease_seconds: 'float', lease_token: 'str | None' = None, harness_session_id: 'str | None' = None, native_resume_token: 'str | None' = None, delivery_idempotency_key: 'str | None' = None, session_id: 'str | None' = None, turn_id: 'str | None' = None, operation_parameters: 'Mapping[str, object] | None' = None) -&gt; 'ManagedInputRecord'`
- `claim_round(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_seconds: 'float', expected_lease_token: 'str | None' = None, delivery_state: "Literal['not_started', 'not_delivered', 'delivered'] | None" = None, idempotent_delivery: 'bool' = False) -&gt; 'ManagedInputRecord'`
- `renew(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str', lease_seconds: 'float') -&gt; 'ManagedInputRecord'`: Renew an active local wait without changing its compare-and-set token.
- `submit_responses(self, execution_id: 'str', round_id: 'str', responses: 'Mapping[str, ElicitationResponse]', *, owner_id: 'str', lease_token: 'str', response_idempotency_key: 'str') -&gt; 'ManagedInputRecord'`
- `start_delivery(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str') -&gt; 'ManagedInputRecord'`
- `mark_delivered(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str') -&gt; 'ManagedInputRecord'`
- `resolve(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str') -&gt; 'ManagedInputRecord'`
- `fail(self, execution_id: 'str', round_id: 'str', *, owner_id: 'str', lease_token: 'str', code: 'str', message: 'str') -&gt; 'ManagedInputRecord'`
- `fail_recovery(self, execution_id: 'str', round_id: 'str', *, expected_lease_token: 'str', message: 'str') -&gt; 'ManagedInputRecord'`
- `redacted_responses(self, execution_id: 'str', round_id: 'str') -&gt; 'Mapping[str, object] | None'`

### `SQLiteStore`

`m3.storage.SQLiteStore(database: 'str | Path', *, blob_root: 'str | Path | None' = None, config: 'RedactionConfig | None' = None, capture_config: 'CaptureOptions | None' = None, payload_blob_threshold: 'int' = 65536, **kwargs: 'Any') -&gt; 'None'`

SQLite implementation of the public :class:`ExecutionStore` protocol.

Public fields and methods:

- `resolve_managed_input_store(self) -&gt; 'SQLiteManagedInputStore'`: Resolve managed-input storage lazily for opted-in executions.
- `ensure_project(self, project_id: 'str', project_name: 'str') -&gt; 'tuple[str, str]'`: Register a stable project identity and refresh its display name.
- `get_project(self, project_id: 'str') -&gt; 'tuple[str, str] | None'`
- `ensure_suite(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite'`
- `get_suite(self, suite_id: 'SuiteId | str') -&gt; 'Suite | None'`
- `get_suite_by_name(self, suite_name: 'str', project_id: 'str | None' = None) -&gt; 'Suite | None'`
- `list_suites(self) -&gt; 'tuple[Suite, ...]'`
- `create(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, Any] | None' = None, provenance: 'Mapping[str, Any] | None' = None, server_bindings: 'Sequence[Mapping[str, Any]]' = (), harness_binding: 'Mapping[str, Any] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `create_execution(self, snapshot: 'ExecutionState', *, specification: 'Mapping[str, Any] | None' = None, provenance: 'Mapping[str, Any] | None' = None, server_bindings: 'Sequence[Mapping[str, Any]]' = (), harness_binding: 'Mapping[str, Any] | None' = None, parent_execution_id: 'ExecutionId | str | None' = None, run_id: 'RunId | str | None' = None) -&gt; 'None'`
- `save_test_run(self, run_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `get_test_run(self, run_id: 'str') -&gt; 'Mapping[str, object] | None'`
- `list_test_runs(self) -&gt; 'tuple[Mapping[str, object], ...]'`
- `list_test_run_page(self, *, limit: 'int | None' = None, offset: 'int' = 0, suite_id: 'int | None' = None, project_id: 'str | None' = None, q: 'str | None' = None) -&gt; 'tuple[tuple[Mapping[str, object], ...], int]'`: Return newest-first run manifests with their suites, and the total.
- `save_test_result(self, run_id: 'str', attempt_id: 'str', value: 'Mapping[str, object]') -&gt; 'None'`
- `list_test_results(self, run_id: 'str') -&gt; 'tuple[Mapping[str, object], ...]'`
- `get_snapshot(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionState | None'`
- `get_execution_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`: Return the immutable typed submission spec, if one was saved.
- `get_spec(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionSpec | None'`: Return the immutable typed submission spec, if one was saved.
- `list_executions(self, *, limit: 'int' = 50, offset: 'int' = 0, lifecycle: 'ExecutionStatus | str | None' = None, outcome: 'ExecutionOutcome | str | None' = None, run_id: 'str | None' = None, suite_id: 'int | None' = None, project_id: 'str | None' = None) -&gt; 'ExecutionPage'`
- `get_report(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1, event_limit: 'int | None' = None, artifact_limit: 'int | None' = None) -&gt; 'ExecutionReport | None'`
- `get_trace(self, execution_id: 'ExecutionId | str') -&gt; 'TraceResult | None'`
- `get_trace_view(self, execution_id: 'ExecutionId | str') -&gt; 'TraceView | None'`
- `save_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `update_snapshot(self, snapshot: 'ExecutionState') -&gt; 'None'`
- `append_events(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append(self, events: 'Sequence[Event]') -&gt; 'None'`
- `append_event(self, event: 'Event', content: 'bytes', *, media_type: 'str') -&gt; 'Event'`: Commit an event and its raw blob in one SQLite transaction.
- `iter_events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'Iterator[Event]'`
- `events(self, execution_id: 'ExecutionId | str', *, after_sequence: 'int' = -1) -&gt; 'tuple[Event, ...]'`
- `create_session(self, execution_id: 'ExecutionId | str', session_id: 'SessionId | str', *, state: 'str' = 'open') -&gt; 'SessionId'`
- `close_session(self, session_id: 'SessionId | str') -&gt; 'None'`
- `save_turn(self, snapshot: 'TurnState', result: 'TurnResult | Mapping[str, Any] | None' = None) -&gt; 'None'`
- `append_turn(self, snapshot: 'TurnState', result: 'TurnResult | Mapping[str, Any] | None' = None) -&gt; 'None'`
- `turns(self, execution_id: 'ExecutionId | str') -&gt; 'tuple[tuple[TurnState, TurnResult | None], ...]'`
- `save_evaluation(self, execution_id: 'ExecutionId | str', result: 'Mapping[str, Any] | Any', *, evaluation_id: 'str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'str'`
- `reserve_judge_request(self, run_id: 'str', limit: 'int') -&gt; 'bool'`: Atomically reserve one judge request for a run across workers.
- `save(self, result: 'EvaluationResult') -&gt; 'None'`
- `get(self, evaluation_id: 'str') -&gt; 'EvaluationResult | None'`
- `all(self) -&gt; 'tuple[EvaluationResult, ...]'`
- `evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `aggregate_evaluations(self, query: 'EvaluationQuery') -&gt; 'EvaluationReport'`: Calculate summaries from persisted evaluations and execution traces.
- `persisted_evaluations(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[EvaluationRecord, ...]'`
- `evaluation_json(self, execution_id: 'ExecutionId | str', *, turn_id: 'TurnId | str | None' = None) -&gt; 'tuple[Mapping[str, Any], ...]'`
- `transaction(self, execution_id: 'ExecutionId | str') -&gt; 'ExecutionTransaction'`
- `allocate(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`
- `allocate_sequence(self, execution_id)`
- `allocate_sequences(self, execution_id: 'ExecutionId | str', *, count: 'int' = 1) -&gt; 'tuple[int, ...]'`
- `release(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`
- `release_sequences(self, execution_id: 'ExecutionId | str', sequences: 'Sequence[int]') -&gt; 'None'`
- `subscribe(self, execution_id: 'ExecutionId | str', callback: 'EventCallback') -&gt; 'Callable[[], None]'`
- `save_acp_probe(self, result: 'ACPProbeResult') -&gt; 'ACPProbeResult'`: Persist one redacted ACP probe result and return its safe copy.
- `get_acp_probe(self, probe_id: 'str') -&gt; 'ACPProbeResult | None'`
- `list_acp_probes(self, dimension: 'ACPProbeDimension | None' = None, *, include_inflight: 'bool' = True) -&gt; 'tuple[ACPProbeResult, ...]'`
- `latest_acp_probe(self, dimension: 'ACPProbeDimension') -&gt; 'ACPProbeResult | None'`
- `create_profile(self, kind: 'str', name: 'str', value: 'Mapping[str, Any]', *, description: 'str' = '', profile_id: 'str | None' = None, revision_id: 'str | None' = None) -&gt; 'ProfileRecord'`
- `create_server_profile(self, name: 'str', value: 'Mapping[str, Any]', **kwargs: 'Any') -&gt; 'ProfileRecord'`
- `create_harness_profile(self, name: 'str', value: 'Mapping[str, Any]', **kwargs: 'Any') -&gt; 'ProfileRecord'`
- `get_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord | None'`
- `resolve_profile(self, profile_id: 'str', selection: 'RevisionSelection', *, kind: "Literal['server', 'harness']") -&gt; 'tuple[ProfileRecord | None, ProfileRevisionRecord | None]'`: Read one profile and its selected immutable revision.
- `list_profiles(self, kind: 'str', *, include_archived: 'bool' = False) -&gt; 'tuple[ProfileRecord, ...]'`: List one profile family in stable name/id order.
- `list_profile_revisions(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'tuple[ProfileRevisionRecord, ...]'`: Return every revision ordered by revision number then id.
- `update_profile(self, profile_id: 'str', *, name: 'str | None' = None, description: 'str | None' = None, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`: Update mutable metadata without changing the immutable revision.
- `add_revision(self, profile_id: 'str', value: 'Mapping[str, Any]', *, revision_id: 'str | None' = None, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord'`
- `archive_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`
- `restore_profile(self, profile_id: 'str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRecord'`
- `resolve_revision(self, profile_id: 'str', selection: 'RevisionSelection | str' = 'latest', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord'`
- `get_revision(self, revision_id: 'RevisionId | str', *, kind: "Literal['server', 'harness'] | None" = None) -&gt; 'ProfileRevisionRecord | None'`
- `enqueue_command(self, execution_id: 'ExecutionId | str', kind: 'str' = 'execution', payload: 'Mapping[str, Any] | None' = None, *, command_id: 'str | None' = None, session_id: 'SessionId | str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'Command'`
- `enqueue(self, execution_id: 'ExecutionId | str', kind: 'str' = 'execution', payload: 'Mapping[str, Any] | None' = None, *, command_id: 'str | None' = None, session_id: 'SessionId | str | None' = None, turn_id: 'TurnId | str | None' = None) -&gt; 'Command'`
- `get_command(self, command_id: 'str') -&gt; 'Command | None'`
- `claim_next(self, owner_id: 'str', *, lease_seconds: 'float' = 30.0) -&gt; 'tuple[Command, Lease] | None'`
- `claim(self, owner_id: 'str', *, lease_seconds: 'float' = 30.0) -&gt; 'tuple[Command, Lease] | None'`
- `heartbeat(self, lease: 'Lease | str', *, owner_id: 'str | None' = None, lease_seconds: 'float' = 30.0) -&gt; 'bool'`
- `renew_lease(self, lease: 'Lease | str', *, owner_id: 'str | None' = None, lease_seconds: 'float' = 30.0) -&gt; 'bool'`
- `mark_interrupted_if_lease_lost(self, lease: 'Lease', *, reason: 'str' = 'worker lease lost') -&gt; 'bool'`: Atomically close work whose owner can no longer renew its lease.
- `mark_managed_recovery_unavailable_if_lease_lost(self, lease: 'Lease', *, reason: 'str' = 'managed interaction cannot be resumed safely after worker loss') -&gt; 'bool'`: Terminalize managed input when its owning worker is lost.
- `release_lease(self, lease: 'Lease | str', *, owner_id: 'str | None' = None) -&gt; 'bool'`
- `complete_command(self, command_id: 'str', *, owner_id: 'str', lease_token: 'str', status: 'str' = 'done') -&gt; 'bool'`: Mark one claimed command terminal under its current lease.
- `request_cancel(self, execution_id: 'ExecutionId | str', reason: 'str | None' = None) -&gt; 'bool'`
- `cancel(self, execution_id: 'ExecutionId | str', reason: 'str | None' = None) -&gt; 'bool'`
- `finalize_cancelled(self, execution_id: 'ExecutionId | str', *, reason: 'str' = 'cancelled') -&gt; 'bool'`: Persist a cancellation terminal event when a worker observed it.
- `cancellation_requested(self, execution_id: 'ExecutionId | str') -&gt; 'bool'`
- `mark_stale_interrupted(self, *, now: 'datetime | None' = None) -&gt; 'tuple[ExecutionId, ...]'`
- `delete_execution(self, execution_id: 'ExecutionId | str') -&gt; 'None'`
- `delete(self, execution_id: 'ExecutionId | str') -&gt; 'None'`
- `put_raw_evidence(self, event_id: 'EventId | str', content: 'bytes', *, media_type: 'str') -&gt; 'EvidenceCapture'`: Redact, bound, and durably associate evidence with one event.
- `read_raw_evidence(self, reference: 'EvidenceRef', *, max_bytes: 'int' = 1048576) -&gt; 'RawEvidence'`
- `clone_execution(self, execution_id: 'ExecutionId | str', *, use_latest: 'bool' = False) -&gt; 'ExecutionId'`
- `clone(self, execution_id: 'ExecutionId | str', *, use_latest: 'bool' = False) -&gt; 'ExecutionId'`
- `resolved_bindings(self, execution_id: 'ExecutionId | str') -&gt; 'Mapping[str, Any]'`: Return the immutable, submission-time profile binding snapshot.
- `close(self) -&gt; 'None'`

### `SQLiteStoreWorker`

`m3.storage.SQLiteStoreWorker(store: 'Any', runner: 'Callable[[Any, Any, Any], Any]', *, worker_id: 'str | None' = None, lease_seconds: 'float' = 30.0, cancel_runner: 'Callable[[Any], Any] | None' = None) -&gt; 'None'`

Embedded worker facade for :class:`SQLiteExecutionStore`.

Public fields and methods:

- `run_once(self) -&gt; 'bool'`
- `stop(self) -&gt; 'None'`

### `StorageConflict`

`m3.storage.StorageConflict`

The append or snapshot operation conflicts with committed state.

### `StorageError`

`m3.storage.StorageError`

Base class for expected ephemeral storage failures.

### `TemporaryArtifactStore`

`m3.storage.TemporaryArtifactStore(root: 'str | os.PathLike[str] | None' = None, *, config: 'RedactionConfig | None' = None) -&gt; 'None'`

Filesystem-backed temporary artifact store with atomic blob placement.

Public fields and methods:

- `put(self, execution_id: 'ExecutionId | str', name: 'str', content: 'bytes', *, media_type: 'str | None' = None) -&gt; 'ArtifactRef'`
- `get(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'bytes'`
- `delete(self, artifact: 'ArtifactRef | ArtifactId | str') -&gt; 'None'`
- `cleanup(self) -&gt; 'None'`

### `serialize_durable`

`m3.storage.serialize_durable(value: 'Any', *, config: 'RedactionConfig | None' = None, path: 'str' = '$') -&gt; 'Any'`

Return strict JSON-compatible durable data while preserving references.
