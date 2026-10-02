---
title: "m3.aggregations"
description: "Public Python API reference for m3.aggregations."
---

# `m3.aggregations`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `EvaluationGroup`

```python
m3.aggregations.EvaluationGroup(
    *,
    key: collections.abc.Mapping[str, str | int | float | bool | None] = ...,
    values: m3.aggregations.EvaluationStats = ...,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `key` | `collections.abc.Mapping[str, str \| int \| float \| bool \| None]` | No | `factory builtins.dict()` | — | — |
| `values` | `m3.aggregations.EvaluationStats` | No | `factory m3.aggregations.EvaluationStats()` | — | — |

## `EvaluationQuery`

```python
m3.aggregations.EvaluationQuery(
    *,
    from_: datetime.datetime | None = None,
    to: datetime.datetime | None = None,
    group_by: tuple[str, ...] = (),
    filters: collections.abc.Mapping[str, tuple[str | int | float | bool | None, ...]] = ...,
    limit: int = 200,
    offset: int = 0,
) -> None
```

A read-only query over persisted evaluations.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `from_` | `datetime.datetime \| None` | No | `None` | — | — |
| `to` | `datetime.datetime \| None` | No | `None` | — | — |
| `group_by` | `tuple[str, ...]` | No | `()` | — | — |
| `filters` | `collections.abc.Mapping[str, tuple[str \| int \| float \| bool \| None, ...]]` | No | `factory builtins.dict()` | — | — |
| `limit` | `int` | No | `200` | `ge=1, le=1000` | — |
| `offset` | `int` | No | `0` | `ge=0` | — |
- `start` (property)

## `EvaluationReport`

```python
m3.aggregations.EvaluationReport(
    *,
    from_: datetime.datetime | None = None,
    to: datetime.datetime | None = None,
    totals: m3.aggregations.EvaluationStats = ...,
    groups: tuple[m3.aggregations.EvaluationGroup, ...] = (),
    total_groups: int = 0,
    limit: int = 200,
    offset: int = 0,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `from_` | `datetime.datetime \| None` | No | `None` | — | — |
| `to` | `datetime.datetime \| None` | No | `None` | — | — |
| `totals` | `m3.aggregations.EvaluationStats` | No | `factory m3.aggregations.EvaluationStats()` | — | — |
| `groups` | `tuple[m3.aggregations.EvaluationGroup, ...]` | No | `()` | — | — |
| `total_groups` | `int` | No | `0` | — | — |
| `limit` | `int` | No | `200` | — | — |
| `offset` | `int` | No | `0` | — | — |

## `EvaluationStats`

```python
m3.aggregations.EvaluationStats(
    *,
    trial_count: int = 0,
    evaluation_count: int = 0,
    expected_count: int = 0,
    missing_required_count: int = 0,
    pending_required_count: int = 0,
    status_counts: collections.abc.Mapping[str, int] = ...,
    pass_rate: float | None = None,
    average_score: float | None = None,
    score_count: int = 0,
    health: m3.aggregations.HealthStats = ...,
) -> None
```

Counts and health metrics for one aggregate group.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `trial_count` | `int` | No | `0` | `ge=0` | — |
| `evaluation_count` | `int` | No | `0` | `ge=0` | — |
| `expected_count` | `int` | No | `0` | `ge=0` | — |
| `missing_required_count` | `int` | No | `0` | `ge=0` | — |
| `pending_required_count` | `int` | No | `0` | `ge=0` | — |
| `status_counts` | `collections.abc.Mapping[str, int]` | No | `factory builtins.dict()` | — | — |
| `pass_rate` | `float \| None` | No | `None` | `ge=0, le=1` | — |
| `average_score` | `float \| None` | No | `None` | `ge=0, le=1` | — |
| `score_count` | `int` | No | `0` | `ge=0` | — |
| `health` | `m3.aggregations.HealthStats` | No | `factory m3.aggregations.HealthStats()` | — | — |

## `HealthStats`

```python
m3.aggregations.HealthStats(
    *,
    execution_count: int = 0,
    tool_calls: m3.aggregations.ToolCallStats = ...,
    protocol_error_count: int = 0,
    outcome_counts: collections.abc.Mapping[str, int] = ...,
    execution_duration_ms: m3.aggregations.LatencyStats = ...,
    server_latency_ms: m3.aggregations.LatencyStats = ...,
) -> None
```

Execution and tool health observed for a group.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `execution_count` | `int` | No | `0` | `ge=0` | — |
| `tool_calls` | `m3.aggregations.ToolCallStats` | No | `factory m3.aggregations.ToolCallStats()` | — | — |
| `protocol_error_count` | `int` | No | `0` | `ge=0` | — |
| `outcome_counts` | `collections.abc.Mapping[str, int]` | No | `factory builtins.dict()` | — | — |
| `execution_duration_ms` | `m3.aggregations.LatencyStats` | No | `factory m3.aggregations.LatencyStats()` | — | — |
| `server_latency_ms` | `m3.aggregations.LatencyStats` | No | `factory m3.aggregations.LatencyStats()` | — | — |

## `LatencyStats`

```python
m3.aggregations.LatencyStats(
    *,
    count: int = 0,
    p50: float | None = None,
    p95: float | None = None,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `count` | `int` | No | `0` | `ge=0` | — |
| `p50` | `float \| None` | No | `None` | `ge=0` | — |
| `p95` | `float \| None` | No | `None` | `ge=0` | — |

## `ToolCallStats`

```python
m3.aggregations.ToolCallStats(
    *,
    total: int = 0,
    successful: int = 0,
    failed: int = 0,
) -> None
```

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `total` | `int` | No | `0` | `ge=0` | — |
| `successful` | `int` | No | `0` | `ge=0` | — |
| `failed` | `int` | No | `0` | `ge=0` | — |

## `aggregate_evaluations`

```python
m3.aggregations.aggregate_evaluations(
    query: EvaluationQuery,
    records: Iterable[EvaluationRecord],
    *,
    snapshots: Mapping[str, Any] | None = None,
    specifications: Mapping[str, Any] | None = None,
    traces: Mapping[str, Any] | None = None,
    attempt_states: Mapping[str, str] | None = None,
    project_names: Mapping[str, str] | None = None,
) -> EvaluationReport
```

Aggregate persisted evaluations and unresolved specification requirements.
