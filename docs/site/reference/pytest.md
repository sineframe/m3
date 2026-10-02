---
title: "pytest integration"
description: "Install sf-m3[pytest] or run m3 setup. The plugin adds M3 fixtures, selection, storage integration, and feedback generation while leaving ordinary pytest selection after -- intact."
---

# pytest integration

Install `sf-m3[pytest]` or run `m3 setup`. The plugin adds M3 fixtures,
selection, storage integration, and feedback generation while leaving ordinary
pytest selection after `--` intact.

Plain pytest can load the plugin with
`uv run pytest -p m3.pytest_plugin --results-db .m3/executions.sqlite …`.
Direct pytest needs `-p m3.pytest_plugin` to use the `agent` fixture because
there is no `pytest11` entry point.

## Marker

```python
import pytest

pytestmark = pytest.mark.m3(suite_name="shipping")
```

The nearest marker can set `suite_name`, server definitions, agent selections,
and `ci`. Persisted selected tests need a non-empty suite name. A module or
class marker supplies defaults; a closer marker overrides them.
The function docstring is saved as the test description shown in the viewer.
The `servers=[...]` marker accepts dictionaries in these forms:

- `{"type": "http", "url": URL, "trust": "public"}`
- `{"type": "stdio", "command": ..., "args": [...]}`

After pytest's `-k` and `-m` deselection, `m3 test` rejects selected tests
with missing or blank suite names. `--collect-only` and deselected tests need
no name. Only the plugin running with `--results-db` requires suite names.
Plain pytest, scripts, and notebooks do not, even with an explicit SQLite
store. Suite selection is intersected with paths, `-k`, `-m`, harnesses, and
trials before agent expansion.

## Fixtures

| Fixture | Value |
| --- | --- |
| `m3_kit` | The `MCPTestKit` bound to the current test and selected store. |
| `agent` | One selected agent case. Requesting it expands harness/model/trial selection. |
| `server` | One selected server binding. Requesting it expands configured server cases. |

CLI server groups replace marker server lists. CLI harness selections replace
marker defaults. Ordinary pytest parametrization composes with these M3 axes.
Each combination is a separate execution; `--trials` is not a retry count.

Request `server` and pass it to `agent.run(..., server=server)` or
`kit.direct(server)`. A test requesting `agent` needs `--harness KIND=MODEL`
or marker `agents=[...]`; a marker alone does not select an agent. `-k`
filters after collection and does not avoid an agent-fixture selection error
in the same file. Keep direct-only and agent tests in separate files.

`m3 test` always adds `-p m3.pytest_plugin --results-db PATH`. The plugin
installs a default `SQLiteExecutionStore` for kits without an explicit store;
an explicit kit store takes precedence.

`m3 ci test` excludes tests whose nearest marker has `ci=False`. `m3 test`
does not apply that exclusion.
