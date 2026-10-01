---
title: "pytest integration"
description: "Install sf-m3[pytest] or run m3 setup. The plugin adds M3 fixtures, selection, storage integration, and feedback generation while leaving ordinary pytest selection after -- intact."
---

# pytest integration

Install `sf-m3[pytest]` or run `m3 setup`. The plugin adds M3 fixtures,
selection, storage integration, and feedback generation while leaving ordinary
pytest selection after `--` intact.

## Marker

```python
import pytest

pytestmark = pytest.mark.m3(suite_name="shipping")
```

The nearest marker can set `suite_name`, server definitions, agent selections,
and `ci`. Persisted selected tests need a non-empty suite name. A module or
class marker supplies defaults; a closer marker overrides them.

## Fixtures

| Fixture | Value |
| --- | --- |
| `m3_kit` | The `MCPTestKit` bound to the current test and selected store. |
| `agent` | One selected agent case. Requesting it expands harness/model/trial selection. |
| `server` | One selected server binding. Requesting it expands configured server cases. |

CLI server groups replace marker server lists. CLI harness selections replace
marker defaults. Ordinary pytest parametrization composes with these M3 axes.
Each combination is a separate execution; `--trials` is not a retry count.

See [the multi-harness guide](../guides/agents/multiple-harnesses.md) for one
pytest test selected across native harnesses, and [the matrix guide](../guides/agents/matrices.md)
for SDK case expansion.

`m3 ci test` excludes tests whose nearest marker has `ci=False`. `m3 test`
does not apply that exclusion.
