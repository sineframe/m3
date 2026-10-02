---
title: "m3.matrix"
description: "Public Python API reference for m3.matrix."
---

# `m3.matrix`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `ToolCase`

```python
m3.matrix.ToolCase(
    *,
    name: str,
    id: str | None = None,
    arguments: collections.abc.Mapping[str, Any] = ...,
    prompt: str | m3.types.UserMessage | None = None,
) -> None
```

One deterministic call definition owned by a server case.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `id` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `arguments` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |
| `prompt` | `str \| m3.types.UserMessage \| None` | No | `None` | — | — |

## `ServerCase`

```python
m3.matrix.ServerCase(
    *,
    name: str,
    server: m3.types.StdioServer | m3.types.HTTPServer | m3.types.InProcessServer,
    tools: tuple[m3.matrix.ToolCase, ...],
) -> None
```

A serializable MCP server and its owned logical tool cases.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `server` | `m3.types.StdioServer \| m3.types.HTTPServer \| m3.types.InProcessServer` | Yes | — | `discriminator='kind'` | — |
| `tools` | `tuple[m3.matrix.ToolCase, ...]` | Yes | — | — | — |

```python
tool(
    self,
    logical_id: str,
) -> ToolCase
```
Return the tool with ``logical_id`` or raise a clear lookup error.

## `ToolMatrix`

```python
m3.matrix.ToolMatrix(
    *,
    servers: tuple[m3.matrix.ServerCase, ...],
    id: str | None = None,
    matrix_id: str | None = None,
    trials: int = 1,
) -> None
```

Expand server-owned deterministic tool cases in declared order.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `servers` | `tuple[m3.matrix.ServerCase, ...]` | Yes | — | — | — |
| `id` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `matrix_id` | `str \| None` | No | `None` | `min_length=1, max_length=256` | — |
| `trials` | `int` | No | `1` | `strict=True, ge=1` | — |

```python
cases(
    self,
) -> tuple[ToolMatrixCase, ...]
```

```python
parametrize(
    self,
    argname: str = 'case',
) -> _pytest.MarkDecorator
```
Return pytest's ordinary parametrization decorator for this matrix.

## `ToolMatrixCase`

```python
m3.matrix.ToolMatrixCase(
    *,
    id: str,
    server: m3.matrix.ServerCase,
    tool: m3.matrix.ToolCase,
    matrix_id: str | None = None,
    cell_id: str | None = None,
    trial: int = 1,
    trial_count: int = 1,
) -> None
```

One server-owned deterministic tool cell.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `id` | `str` | Yes | — | `min_length=1, max_length=1024` | — |
| `server` | `m3.matrix.ServerCase` | Yes | — | — | — |
| `tool` | `m3.matrix.ToolCase` | Yes | — | — | — |
| `matrix_id` | `str \| None` | No | `None` | — | — |
| `cell_id` | `str \| None` | No | `None` | — | — |
| `trial` | `int` | No | `1` | `strict=True, ge=1` | — |
| `trial_count` | `int` | No | `1` | `strict=True, ge=1` | — |

```python
run(
    self,
    *,
    kit: _MCPTestKit | None = None,
    timeout: float | None = None,
    validate_schemas: bool = False,
    metadata: _Mapping[str, _Scalar] | None = None,
) -> _ExecutionResult
```
Run this cell through the normal synchronous execution boundary.

```python
run_async(
    self,
    *,
    kit: _AsyncMCPTestKit | None = None,
    timeout: float | None = None,
    validate_schemas: bool = False,
    metadata: _Mapping[str, _Scalar] | None = None,
) -> _ExecutionResult
```
Run this cell through the normal asynchronous execution boundary.
