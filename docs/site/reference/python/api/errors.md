---
title: "m3.errors"
description: "Public Python API reference for m3.errors."
---

# `m3.errors`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `CleanupError`

```python
m3.errors.CleanupError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `ElicitationExpectationError`

```python
m3.errors.ElicitationExpectationError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

An elicitation round did not match the declared response plan.

## `ElicitationRoundLimitError`

```python
m3.errors.ElicitationRoundLimitError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

An elicitation operation exceeded its configured round limit.

## `ExecutionNotFound`

```python
m3.errors.ExecutionNotFound(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

The requested execution does not exist in the trace store.

## `InvalidTransitionError`

```python
m3.errors.InvalidTransitionError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `KitClosed`

```python
m3.errors.KitClosed(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

An operation was attempted after its owning test kit was closed.

## `MCPError`

```python
m3.errors.MCPError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

Base class for expected M3 failures.

## `ModelValidationError`

```python
m3.errors.ModelValidationError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `OperationCancelled`

```python
m3.errors.OperationCancelled(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `OperationTimeout`

```python
m3.errors.OperationTimeout(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `RawEvidenceIntegrityError`

```python
m3.errors.RawEvidenceIntegrityError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

Referenced raw evidence failed its digest or size integrity check.

## `RawEvidenceUnavailable`

```python
m3.errors.RawEvidenceUnavailable(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

Referenced redacted raw evidence cannot be read.

## `ProtocolError`

```python
m3.errors.ProtocolError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `SessionBusy`

```python
m3.errors.SessionBusy(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `SessionStillOpen`

```python
m3.errors.SessionStillOpen(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `TransportError`

```python
m3.errors.TransportError(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

## `TraceNotFinalized`

```python
m3.errors.TraceNotFinalized(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

A typed view was requested before terminal execution evidence arrived.

## `TraceUnavailable`

```python
m3.errors.TraceUnavailable(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```

An execution exists but has no usable trace evidence.

## `UnsupportedFeature`

```python
m3.errors.UnsupportedFeature(
    message: str,
    *,
    details: _Mapping[str, _Any] | None = None,
) -> None
```
