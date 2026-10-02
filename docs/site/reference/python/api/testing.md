---
title: "m3.testing"
description: "Public Python API reference for m3.testing."
---

# `m3.testing`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `ArtifactIntegrityError`

```python
m3.testing.ArtifactIntegrityError
```

A recorded artifact does not match its content-addressed metadata.

## `ExpectedCall`

```python
m3.testing.ExpectedCall(
    method: str,
    matcher: _Mapping[str, _Any] = ...,
    response: _Any = None,
    optional_call: bool = False,
    minimum: int = 1,
    maximum: int | None = 1,
    subset_match: bool = False,
    unordered_group: str | None = None,
    fallback_call: bool = False,
    calls: int = 0,
) -> None
```


```python
optional(
    self,
) -> ExpectedCall
```

```python
repeated(
    self,
    minimum: int = 0,
    maximum: int | None = None,
) -> ExpectedCall
```

```python
subset(
    self,
) -> ExpectedCall
```

```python
unordered(
    self,
    group: str = 'default',
) -> ExpectedCall
```

```python
fallback(
    self,
) -> ExpectedCall
```

```python
returns(
    self,
    value: _Any,
) -> ExpectedCall
```

## `FaultInjector`

```python
m3.testing.FaultInjector(
    delays: dict[str, float] = ...,
    gates: dict[str, Gate] = ...,
    disconnect_methods: set[str] = ...,
    malformed_methods: set[str] = ...,
    partial_methods: set[str] = ...,
    invalid_schema_methods: set[str] = ...,
    invalid_result_methods: set[str] = ...,
    protocol_errors: dict[str, tuple[int, str]] = ...,
    oversized_methods: dict[str, int] = ...,
    reordered_methods: set[str] = ...,
    duplicate_id_methods: set[str] = ...,
    cancel_before_methods: set[str] = ...,
    process_crash_methods: set[str] = ...,
    race_gates: dict[str, Gate] = ...,
    reorder_window: float = 0.01,
) -> None
```

Deterministic faults for decoded in-process or literal stdio fixtures.


```python
delay(
    self,
    method: str,
    seconds: float,
) -> FaultInjector
```

```python
hang(
    self,
    method: str,
    gate: Gate | None = None,
) -> Gate
```

```python
disconnect(
    self,
    method: str,
) -> FaultInjector
```

```python
malformed(
    self,
    method: str,
) -> FaultInjector
```

```python
partial_frame(
    self,
    method: str,
) -> FaultInjector
```

```python
invalid_schema(
    self,
    method: str,
) -> FaultInjector
```

```python
invalid_result(
    self,
    method: str,
) -> FaultInjector
```
Advertise an output schema and return a violating structured result.

```python
duplicate_id(
    self,
    method: str,
) -> FaultInjector
```

```python
reorder(
    self,
    method: str,
) -> FaultInjector
```

```python
oversized(
    self,
    method: str,
    size: int,
) -> FaultInjector
```

```python
cancel_before_dispatch(
    self,
    method: str,
) -> FaultInjector
```

```python
process_crash(
    self,
    method: str,
) -> FaultInjector
```
Exit the dedicated stdio fixture with a non-zero status.

```python
cancellation_race(
    self,
    method: str,
) -> FaultInjector
```

```python
race_gate(
    self,
    method: str,
) -> Gate
```
Return the gate deciding whether a raced response is released.

```python
protocol_error(
    self,
    method: str,
    code: int = -32000,
    message: str = 'mock protocol error',
) -> FaultInjector
```

```python
stdio_server(
    self,
    *,
    name: str = 'wire-fault',
) -> _StdioServer
```
Return a safe stdio fixture that applies literal wire faults.

```python
close_fixture(
    self,
) -> None
```

```python
before(
    self,
    method: str,
) -> None
```

```python
response(
    self,
    method: str,
    value: _Any,
) -> _Any
```
Apply deterministic response-shape faults after handler output.

## `Gate`

```python
m3.testing.Gate(
    *,
    open: bool = False,
) -> None
```

Deterministic async gate for delay and hang tests.

- `is_open` (property)

```python
open(
    self,
) -> None
```

```python
release(
    self,
) -> None
```

```python
close(
    self,
) -> None
```

```python
wait(
    self,
    *,
    poll_seconds: float = 0.001,
) -> None
```

```python
wait_async(
    self,
    *,
    poll_seconds: float = 0.001,
) -> None
```
Alias with an explicit name for composing deterministic races.

## `MockExpectationError`

```python
m3.testing.MockExpectationError
```

A scripted mock interaction did not match.

## `MockMCPServer`

```python
m3.testing.MockMCPServer(
    name: str = 'mock-mcp',
    *,
    version: str = '1',
    strict: bool = True,
    redaction_config: _RedactionConfig | None = None,
    faults: FaultInjector | None = None,
    state: _Mapping[str, _Any] | None = None,
) -> None
```

Decorator-defined and stateful official in-process MCP server.


```python
tool(
    self,
    name: str | _Callable[..., _Any] | None = None,
    *,
    description: str | None = None,
    input_schema: _Mapping[str, _Any] | None = None,
    output_schema: _Mapping[str, _Any] | None = None,
) -> _Any
```

```python
resource(
    self,
    uri: str,
    *,
    name: str | None = None,
    mime_type: str | None = None,
) -> _Callable[[_Callable[_P, _R]], _Callable[_P, _R]]
```

```python
resource_template(
    self,
    uri_template: str,
    *,
    name: str | None = None,
    description: str | None = None,
    mime_type: str | None = None,
) -> _types.ResourceTemplate
```
Register a resource template exposed by ``resources/templates/list``.

```python
prompt(
    self,
    name: str | _Callable[..., _Any] | None = None,
    *,
    description: str | None = None,
) -> _Any
```

```python
expect(
    self,
    method: str,
    *,
    optional: bool = False,
    repeat: int | tuple[int, int | None] | None = None,
    subset: bool = False,
    unordered: str | None = None,
    fallback: bool = False,
    **matcher: _Any,
) -> ExpectedCall
```

```python
expect_call(
    self,
    method: str,
    *,
    optional: bool = False,
    repeat: int | tuple[int, int | None] | None = None,
    subset: bool = False,
    unordered: str | None = None,
    fallback: bool = False,
    **matcher: _Any,
) -> ExpectedCall
```

```python
expect_tool_call(
    self,
    name: str,
    arguments: _Mapping[str, _Any] | None = None,
    **options: _Any,
) -> ExpectedCall
```

```python
server(
    self,
) -> _Server
```

```python
in_process(
    self,
    *,
    name: str | None = None,
) -> _InProcessServer
```
Return a decoded ``SessionMessage`` in-process fixture.

```python
verify(
    self,
) -> None
```

```python
recording(
    self,
) -> Recording
```

```python
close(
    self,
) -> None
```

## `MockProtocolError`

```python
m3.testing.MockProtocolError(
    code: int,
    message: str,
) -> None
```

## `RecordedArtifact`

```python
m3.testing.RecordedArtifact(
    artifact_id: str,
    sha256: str,
    size_bytes: int,
    media_type: str | None = None,
) -> None
```

Content-addressed artifact metadata carried by a recording.


```python
from_bytes(
    cls,
    artifact_id: str,
    content: bytes,
    *,
    media_type: str | None = None,
) -> RecordedArtifact
```

```python
validate(
    self,
    content: bytes,
) -> None
```

```python
model(
    self,
) -> dict[str, _JsonValue]
```

## `RecordedInteraction`

```python
m3.testing.RecordedInteraction(
    method: str,
    params: _Mapping[str, _JsonValue],
    response: _JsonValue | None = None,
    error: _Mapping[str, _JsonValue] | None = None,
    sequence: int = 0,
    provenance: tuple[str, ...] = (),
) -> None
```


```python
model(
    self,
) -> dict[str, _JsonValue]
```

## `Recording`

```python
m3.testing.Recording(
    interactions: tuple[RecordedInteraction, ...],
    redaction_bound: bool,
    provenance: tuple[str, ...] = (),
    server_name: str = 'mock-mcp',
    server_version: str = '1',
    initialization: _Mapping[str, _JsonValue] = ...,
    artifacts: tuple[RecordedArtifact, ...] = (),
    redaction_bindings: tuple[RedactionBinding, ...] = ...,
    _runtime: _RedactionRuntime = ...,
) -> None
```


```python
with_redaction_config(
    self,
    config: _RedactionConfig,
) -> Recording
```
Bind an explicit in-process redaction config without serializing secrets.

```python
validate_artifacts(
    self,
    contents: _Mapping[str, bytes],
) -> None
```
Validate every supplied artifact against the recorded digest/length.

```python
model_dump(
    self,
    *,
    config: _RedactionConfig | None = None,
) -> dict[str, _JsonValue]
```

```python
to_json(
    self,
    *,
    config: _RedactionConfig | None = None,
) -> str
```

```python
redacted(
    self,
    *,
    config: _RedactionConfig | None = None,
) -> Recording
```
Return a detached recording whose persisted values are redacted.

```python
from_json(
    cls,
    value: str,
) -> Recording
```

## `RedactionBinding`

```python
m3.testing.RedactionBinding(
    source: str = 'explicit',
    version: str = '1',
    wildcard_paths: tuple[str, ...] = (),
    secret_references: tuple[str, ...] = (),
) -> None
```

Serializable redaction provenance without secret values.


```python
model(
    self,
) -> dict[str, _JsonValue]
```

## `ReplayMismatch`

```python
m3.testing.ReplayMismatch
```

A strict replay request differs from the recorded interaction.

## `ReplayServer`

```python
m3.testing.ReplayServer(
    recording: Recording,
    *,
    strict: bool = True,
    redaction_config: _RedactionConfig | None = None,
    artifacts: _Mapping[str, bytes] | None = None,
    expected_server: tuple[str, str] | None = None,
) -> None
```

Strict JSON-only replay matcher.


```python
match(
    self,
    method: str,
    params: _Mapping[str, _Any],
) -> RecordedInteraction
```

```python
verify_replay(
    self,
) -> None
```
- `provenance` (property)

## `VirtualClock`

```python
m3.testing.VirtualClock(
    start: float = 0.0,
) -> None
```

A process-local virtual clock with awaitable deterministic sleeps.

- `now` (property)

```python
sleep(
    self,
    duration: float,
) -> None
```

```python
advance(
    self,
    duration: float,
) -> float
```
