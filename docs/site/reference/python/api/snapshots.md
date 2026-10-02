---
title: "m3.snapshots"
description: "Public Python API reference for m3.snapshots."
---

# `m3.snapshots`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `SnapshotOptions`

```python
m3.snapshots.SnapshotOptions(
    include_fields: frozenset[str] = frozenset(),
    omitted_fields: frozenset[str] = frozenset({'cost', 'cost_usd', 'created_at', 'duration', 'duration_ms', 'elapsed_ms', 'end_time', 'ended_at', 'execution_id', 'finished_at', 'input_cost', 'latency', 'latency_ms', 'output_cost', 'path', 'paths', 'port', 'ports', 'provider_metadata', 'run_id', 'run_ids', 'session_id', 'start_time', 'started_at', 'timestamp', 'timestamps', 'total_cost', 'trace_id', 'turn_id', 'updated_at', 'vendor_metadata'}),
) -> None
```

Controls stable snapshot omission and explicit field opt-ins.

## `snapshot`

```python
m3.snapshots.snapshot(
    value: _Any,
    *,
    include_fields: _Iterable[str] = (),
    omitted_fields: _Iterable[str] | None = None,
    options: SnapshotOptions | None = None,
    config: _RedactionConfig | None = None,
) -> _SnapshotValue
```

Return a deterministic, redacted, JSON-compatible snapshot value.
