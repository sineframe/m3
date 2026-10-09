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

## Trials

`--trials N` (or marker `trials=N`) expands the `agent` fixture, so only tests
that request `agent` run N times. Each trial is its own test case with its own
pass or fail:

```python
@pytest.mark.m3
def test_lookup(agent):
    result = agent.run("Find order 42.", server=server)
    expect(result).to_have_tool_calls(["get_order"])
```

```sh
m3 test --harness codex=gpt-5.6-sol --trials 5 -- tests
```

A test that builds agents in its body with `m3_kit.agents([...])` is collected
once and is not repeated by `--trials`; pass `trials=N` to `agents()` instead,
which returns N trial selections for the test to run. When `--trials` is above
1 but no selected test requests `agent`, the option has no effect and the
plugin emits one pytest warning. `m3(trials=N)` written directly on a test that
does not request `agent` is a usage error; on a module or class it only applies
to the tests there that do.

## Step timings

`M3_TIMINGS=1 pytest -p m3.pytest_plugin tests/` records step timings and prints the summary at the
end of the run. It works with plain pytest, with or without `--results-db`, and
with pytest-xdist: the controller and each worker write their own file, and the
report merges them. Plain pytest has no `cli.*` steps. See
[Find slow steps in a test run](../guides/results/timings.md).

## Parallel runs

`m3 test -n 4` runs tests in four pytest-xdist worker processes; `m3 test -n
auto` uses one per CPU. `m3 setup` installs pytest-xdist. Without it, the
command fails with `--num-processes requires pytest-xdist in the project
Python; run m3 setup in the project`. `-n` cannot be combined with `-n` or
`--numprocesses` after `--`.

All workers write one run: the same run ID, verdicts, feedback, and judge
budget. `--judge-max-requests` is shared across workers. When `-n` is used,
pytest's normal output replaces the M3 progress line.

Inside one test, up to 4 executions submitted with `agent.submit(...)` or
`kit.submit(...)` run at once; further submissions wait. A kit created by the
plugin only runs executions submitted in its own process. A kit given an
explicit `store=` uses the shared queue and runs one execution at a time.

Mark tests that share a resource so they run on the same worker, and select
`loadgroup` distribution:

```python
import pytest


@pytest.mark.xdist_group(name="orders-db")
def test_creates_order(agent): ...


@pytest.mark.xdist_group(name="orders-db")
def test_cancels_order(agent): ...
```

```sh
m3 test -n 4 -- --dist loadgroup
```

Tests without the mark are distributed normally.

pytest-xdist sets `PYTEST_XDIST_WORKER` (`gw0`, `gw1`, ...) in each worker.
Use it to give each worker its own ports, directories, or accounts.

N workers running up to 4 executions each can reach model rate limits. Lower
`-n` if executions fail with provider rate-limit errors.

`m3 ci test` excludes tests whose nearest marker has `ci=False`. `m3 test`
does not apply that exclusion.
