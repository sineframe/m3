---
title: "m3.matchers"
description: "Public Python API reference for m3.matchers."
---

# `m3.matchers`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `CheckGroup`

```python
m3.matchers.CheckGroup(
    redaction_config: _RedactionConfig | None = None,
) -> None
```

Collect assertion failures and report them together on context exit.


```python
expect(
    self,
    subject: _Any,
) -> Expectation[_Any]
```

## `Expectation`

```python
m3.matchers.Expectation(
    subject: _SubjectT,
    sink: _FailureSink | None = None,
    redaction_config: _RedactionConfig | None = None,
) -> None
```

One-shot assertion facade; optional pytest recording persists checks.


```python
to_have_text(
    self,
    expected: str,
) -> None
```

```python
to_have_text_containing(
    self,
    expected: str,
) -> None
```

```python
to_have_text_matching(
    self,
    pattern: str,
    *,
    flags: int = 0,
) -> None
```

```python
to_have_text_matching_regex(
    self,
    pattern: str,
    *,
    flags: int = 0,
) -> None
```

```python
to_not_have_text(
    self,
    unexpected: str,
    *,
    current_snapshot: bool = False,
) -> None
```

```python
to_not_have_text_containing(
    self,
    unexpected: str,
    *,
    current_snapshot: bool = False,
) -> None
```

```python
to_have_structured_content(
    self,
    expected: object,
) -> None
```

```python
to_have_content(
    self,
    expected: _Any,
    *,
    ordered: bool = True,
) -> None
```

```python
to_have_ordered_content(
    self,
    expected: _Any,
) -> None
```

```python
to_have_unordered_content(
    self,
    expected: _Any,
) -> None
```

```python
to_have_tool_calls(
    self,
    expected: _Sequence[str],
    *,
    ordered: bool = True,
    server: str | None = None,
    turn: _TurnSelector | None = None,
    evidence: _Literal['wire', 'reported', 'any'] = 'wire',
    current_snapshot: bool = False,
) -> None
```
Match the complete list of tool names, including repeated calls.

```python
to_have_tool_call(
    self,
    tool: str | None = None,
    *,
    name: str | None = None,
    server: str | None = None,
    arguments: _Any = None,
    arguments_partial: bool = False,
    arguments_unordered: bool = False,
    arguments_regex: bool = False,
    arguments_tolerance: float | None = None,
    argument_predicate: _Callable[[_Any], bool] | None = None,
    result: _Any = None,
    result_partial: bool = False,
    arguments_observed_null: bool = False,
    result_observed_null: bool = False,
    status: str | None = None,
    count: int | None = None,
    min_count: int | None = None,
    max_count: int | None = None,
    turn: _TurnSelector | None = None,
    predicate: _Callable[[_Mapping[str, _Any]], bool] | None = None,
    server_name: str | None = None,
    evidence: _Literal['wire', 'reported', 'any'] = 'wire',
    min_latency_ms: float | None = None,
    max_latency_ms: float | None = None,
    current_snapshot: bool = False,
) -> None
```

```python
to_not_have_tool_call(
    self,
    tool: str | None = None,
    *,
    current_snapshot: bool = False,
    **kwargs: _Any,
) -> None
```

```python
to_have_no_tool_call(
    self,
    tool: str | None = None,
    *,
    current_snapshot: bool = False,
    **kwargs: _Any,
) -> None
```

```python
to_have_reported_tool_call(
    self,
    tool: str | None = None,
    **kwargs: _Any,
) -> None
```
Explicitly match provider/harness-reported calls without wire evidence.

```python
to_have_duration(
    self,
    expected_ms: float | None = None,
    *,
    min_ms: float | None = None,
    max_ms: float | None = None,
    tolerance_ms: float = 0.0,
) -> None
```

```python
to_have_duration_between(
    self,
    min_ms: float,
    max_ms: float,
) -> None
```

```python
to_have_lifecycle(
    self,
    lifecycle: _ExecutionStatus | _TurnStatus | str,
) -> None
```

```python
to_have_outcome(
    self,
    outcome: _ExecutionOutcome | str,
) -> None
```

```python
to_be_completed(
    self,
) -> None
```

```python
to_have_terminal_outcome(
    self,
    outcome: _ExecutionOutcome | str,
) -> None
```

```python
to_have_error(
    self,
    expected: _Any = None,
) -> None
```

```python
to_have_protocol_version(
    self,
    version: str,
) -> None
```

```python
to_have_transport(
    self,
    transport: str,
) -> None
```

```python
to_have_capability(
    self,
    name: str,
    *,
    status: _CapabilityStatus | str | None = None,
) -> None
```

```python
to_have_artifact(
    self,
    artifact: _ArtifactRef | str,
    *,
    name: str | None = None,
) -> None
```

```python
to_have_workspace_diff(
    self,
    expected: _Any = None,
    *,
    added: _Any = None,
    modified: _Any = None,
    deleted: _Any = None,
) -> None
```
Assert the generic workspace projection exposed by a result/handle.

```python
to_have_trace(
    self,
    *,
    completeness: str | None = None,
    limitation: str | None = None,
) -> None
```

```python
to_have_event(
    self,
    kind: _EventKind | str,
    *,
    count: int | None = None,
) -> None
```

```python
to_eventually(
    self,
    matcher: _Callable[[_Any], None],
    *,
    timeout: float = 5.0,
) -> None
```

## `check`

```python
m3.matchers.check(
    *,
    redaction_config: _RedactionConfig | None = None,
) -> CheckGroup
```

## `expect`

```python
m3.matchers.expect(
    subject: _SubjectT,
    *,
    redaction_config: _RedactionConfig | None = None,
) -> Expectation[_SubjectT]
```
