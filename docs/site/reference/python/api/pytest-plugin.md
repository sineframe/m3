---
title: "m3.pytest_plugin"
description: "Public Python API reference for m3.pytest_plugin."
---

# `m3.pytest_plugin`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `pytest_addoption`

```python
m3.pytest_plugin.pytest_addoption(
    parser: _Any,
) -> None
```

## `pytest_configure`

```python
m3.pytest_plugin.pytest_configure(
    config: _Any,
) -> None
```

## `pytest_unconfigure`

```python
m3.pytest_plugin.pytest_unconfigure(
    config: _Any,
) -> None
```

## `pytest_generate_tests`

```python
m3.pytest_plugin.pytest_generate_tests(
    metafunc: _Any,
) -> None
```

## `m3_kit`

```python
m3.pytest_plugin.m3_kit(
    request: _Any,
) -> _Any
```

## `agent`

```python
m3.pytest_plugin.agent(
    request: _Any,
    m3_kit: _Any,
) -> _Any
```

## `server`

```python
m3.pytest_plugin.server(
    request: _Any,
) -> _Any
```

## `pytest_collection_modifyitems`

```python
m3.pytest_plugin.pytest_collection_modifyitems(
    config: _Any,
    items: list[_Any],
) -> None
```
