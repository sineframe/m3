---
name: testing-with-m3
description: Use when setting up M3 or when writing, running, debugging, or evaluating tests for an MCP server or for agents that use one. Covers m3 init, setup, doctor, and test; direct MCP tests; agent tests with Codex, Claude Code, OpenCode, Pi, or ACP agents; evaluations and LLM judges; saved runs, feedback files, and traces. Use it whenever a project has an m3.toml file, imports m3, or the user mentions M3, MCP server tests, or testing an MCP tool with an agent.
license: Apache-2.0
---

# Testing with M3

M3 tests MCP servers with ordinary Python and pytest. It has two parts that
are installed separately:

- The `m3` command (PyPI package `sf-m3-cli`) is installed once per machine.
  It runs pytest in the project environment and saves every run.
- The `m3` Python package (PyPI package `sf-m3`) is what tests import.
  `m3 setup` installs the version that matches the command into the project.

M3 has two kinds of test:

- A **direct test** connects to the server as an MCP client and calls it. It
  shows what the server does and needs no model provider.
- An **agent test** gives a coding agent access to the server and checks what
  the agent observably did, such as which tools it called with which
  arguments. It needs a harness such as Codex or Claude Code and its
  credentials.

Start with direct tests. Add agent tests only when the claim is about tool
choice or about the agent's answer.

This skill was installed for one M3 release, and `references/` holds that
release's documentation. M3 changes between releases, so read the relevant
reference before writing code instead of relying on memory, and use
`m3 COMMAND --help` for exact options.

## Rules

- Take expected values from the project's documentation, its specification,
  or the user. Never copy the server's current output into an assertion just
  to make a test pass.
- A passing pytest case is not M3 coverage on its own. A test counts only
  when it called the server through M3 and the run recorded an execution for
  it.
- A collected test, a completed execution, and a passing evaluation are
  different facts. Report the one you observed.
- An agent saying that it used a tool is not evidence. Use the recorded tool
  calls.
- Keep secrets out of tests, commands, and reports. Refer to keys by variable
  name, and keep `.env` out of Git. `m3 test` loads the project root `.env`
  automatically; pass `--env-file PATH` only for a custom file.
- Upload results only when the user asks.
- If `m3 doctor` reports that the command and the project SDK do not match,
  run `m3 setup`. Do not work around the mismatch.
- Use the M3 APIs the references teach first: `MCPTestKit`, `kit.direct(...)`
  and its client methods, the pytest `agent` fixture, `expect(...)`, and
  evaluators. Lower-level types such as `DirectSpec` exist for advanced use.
  Use them only when the project's existing tests already do, and read
  their reference before writing code.
- Results are read-only: nested lists come back as tuples and nested objects
  as read-only mappings. Write expected values with tuples, or convert with
  `list(...)` and `dict(...)` at each level you compare.

## Set up the project

Run these from the project root:

```sh
m3 --version
m3 init --project-name PROJECT_NAME --suite SUITE_NAME
m3 setup
m3 doctor
```

- If `m3` is not installed, follow [Install M3](references/start-install.md).
- Pass both names to `m3 init`; without a terminal it cannot ask for them.
- `m3 init` creates `m3.toml`, a skipped starter test at
  `tests/test_m3_starter.py`, and `.env.example`. `m3.toml` holds the
  project's `project_id`, which links saved runs, baselines, and uploads, so
  keep it in Git. A project counts as initialized when `m3.toml` is valid;
  you can move, rename, or delete the starter. Running `m3 init` again keeps
  existing files.
- `m3 setup` installs the matching SDK into the project's Python environment.
  It does not edit `pyproject.toml` or the lockfile.
- Continue only when `m3 doctor` prints `m3 doctor: ready`.

## If the project already has M3 tests

Look for `m3.toml` and for test files that import `m3`. If they exist:

- Run `m3 setup` and `m3 doctor`. Running `m3 init` is also safe: when
  `m3.toml` is valid it changes nothing except adding a missing `.env.example`.
- Before writing anything, read the existing M3 tests, their fixtures, and
  the project's pytest configuration (`testpaths`, markers, `conftest.py`).
  Reuse their server fixtures and suite names.
- Add tests for behavior the existing suites do not cover. Do not add a copy
  of the starter; delete the generated skipped starter if the project does
  not use it.
- Pass the path you changed to `m3 test`, for example
  `m3 test -- m3_tests/test_new.py`. Without a path, pytest selects tests
  from its `testpaths` setting.

## Write a first direct test

First find out how the project starts its MCP server (a command for a local
process, or a URL for a running service) and what its tools are documented to
return.

This is the complete first test from the M3 getting-started guide. It starts
a local stdio server, checks the advertised tools, and calls one tool:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping")


def test_shipping_quote() -> None:
    project_root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(project_root / "shipping_server.py"),),
        cwd=str(project_root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        tools = client.list_all_tools()
        assert [tool.name for tool in tools] == ["shipping_quote"]
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

What each part does:

- `pytestmark = pytest.mark.m3(suite_name=...)` names the suite. `m3 test`
  rejects selected tests that have no suite name.
- `StdioServer(...)` tells M3 how to start the server process. Replace the
  command and arguments with the project's own. For a server that is already
  running at a URL, use `HTTPServer`; see
  [HTTP servers](references/guides-servers-http.md).
- `MCPTestKit(...)` and `kit.direct(server)` open one MCP connection. Leaving
  the `with` block closes it and stops the server process.
- The assertions check the advertised tool list and the structured result. A
  call that returns without an error proves very little.

The server this test uses, and the full walkthrough, are in
[Getting started](references/getting-started.md). For the project's own
server, read [Test your own server](references/start-your-server.md).

Replace `tests/test_m3_starter.py` with a test like this for the project's
server. Give each test function a short docstring that states the behavior;
M3 shows it in the results viewer. Then run:

```sh
m3 test -- tests/test_m3_starter.py
```

## Confirm the run recorded evidence

`m3 test` prints the run ID and the path of a feedback file,
`.m3/reports/RUN_ID/feedback.json`. Use the printed path; do not guess the
run ID. Check that the test passed and is linked to at least one execution,
replacing the node ID with your test's:

```sh
report=.m3/reports/RUN_ID/feedback.json
node=tests/test_m3_starter.py::test_shipping_quote
jq -e --arg node "$node" '
  (.summary.executions > 0) and
  any(.tests[];
    .node_id == $node and .outcome == "passed" and
    ((.execution_ids // []) | length > 0))
' "$report"
```

If this prints `false`, the test did not call the server through M3. Fix the
test and run it again before reporting coverage. Read selected fields like
this rather than printing whole feedback or trace files, which can contain
application data. The file format is described in
[Saved results](references/guides-results-persistence.md). To browse saved
runs in a browser, run `m3 ui` from the project root. If `jq` is not
installed, read the same fields with Python's `json` module.

`m3 test` also prints lines that start with `M3`, and traces list
limitations such as `capture_incomplete`. Some of these are routine in a
passing run. Before reporting one as a problem, look it up in
[CLI output and trace limitations](references/reference-output.md).

## Choose the next test

Choose the test from the claim being made, and read the reference before
writing it.

| Claim | Test | Read |
|---|---|---|
| The server advertises the right tools, schemas, resources, or prompts | Direct | [Tools](references/guides-servers-tools.md), [Resources and prompts](references/guides-servers-resources-prompts.md) |
| A tool handles valid, boundary, and invalid input | Direct, one case per test or a `ToolMatrix` | [Errors and schemas](references/guides-servers-errors-schemas.md), [ToolMatrix](references/reference-python-m3-matrix.md) |
| Calls share state | Direct, one client for the whole sequence | [Stateful tests](references/guides-servers-stateful-tests.md) |
| An agent picks the right tool | Agent | [First agent test](references/guides-agents-first-test.md), [Assertions](references/guides-evaluations-assertions.md) |
| An agent handles a conversation | Agent session | [Sessions](references/guides-agents-sessions.md) |
| An answer meets a quality rule | Evaluator or LLM judge | [Custom evaluators](references/guides-evaluations-custom.md), [LLM judges](references/guides-evaluations-judges.md) |
| A change helps across agents or models | Matrix with trials, compared with a baseline | [Matrices](references/guides-agents-matrices.md), [Baselines](references/guides-results-baselines.md) |
| The server asks the user for input | Direct or agent test with an input plan | [Elicitation](references/guides-elicitation-plans.md) |

For server behavior, cover what can actually fail: the advertised catalog and
schemas, representative valid and boundary inputs, expected errors, and any
state, resource, or prompt behavior the project exposes. Keep each expected
failure in its own test so its reason stays visible.

For agent tool-choice tests:

- Keep realistic alternative tools available. A policy that exposes only one
  tool shows that the agent used it, not that it chose it.
- Do not name the target tool in the prompt when testing whether the agent
  finds it.
- Assert the recorded call, its arguments, and the calls that must not
  happen.
- Use `--trials N` for repeated attempts and report every attempt. Never
  rerun only the failures and report the best result. A few trials are an
  observation, not a reliable rate.

## Find details

| Task | Reference |
|---|---|
| Install or upgrade M3 | [Install M3](references/start-install.md) |
| Connect to a local or remote server | [Test your own server](references/start-your-server.md), [Stdio](references/guides-servers-stdio.md), [HTTP](references/guides-servers-http.md) |
| pytest marker, fixtures, and suite names | [pytest reference](references/reference-pytest.md) |
| `m3` commands and options | [CLI reference](references/reference-cli.md) |
| Credentials and `.env` | [Credentials](references/guides-credentials.md), [Credential reference](references/reference-credentials.md) |
| Harnesses, models, and versions | [Harnesses](references/guides-agents-harnesses.md), [Versions](references/guides-agents-versions.md), [Managed runtimes](references/guides-agents-managed-runtimes.md) |
| ACP agents | [Connect an ACP agent](references/guides-agents-acp-connect.md) |
| Traces | [Traces](references/guides-results-traces.md), [Observability API](references/reference-python-m3-observability.md) |
| Saved runs and the viewer | [Saved results](references/guides-results-persistence.md), [Viewer](references/guides-results-viewer.md) |
| Async tests | [Async](references/guides-advanced-async.md) |
| Running in CI | [Run in CI](references/guides-ci-run.md), [GitHub Actions](references/guides-ci-github-actions.md) |
| Python API | [Python reference](references/reference-python.md) |
| Supported harnesses and limits | [Compatibility](references/reference-compatibility.md) |
| What M3 counts as evidence | [Testing model](references/concepts-testing-model.md), [Evidence](references/concepts-evidence.md), [Outcomes](references/concepts-outcomes.md) |
| What a CLI output line or a trace limitation means | [CLI output and trace limitations](references/reference-output.md) |
| Anything else | [Documentation index](references/index.md) |

## When a run fails

Check in this order and read the matching page:

1. Collection and environment: [Install problems](references/troubleshooting-install.md)
2. Server startup or connection: [Server problems](references/troubleshooting-servers.md)
3. Harness and provider setup: [Agent problems](references/troubleshooting-agents.md)
4. The test's own assertions
5. Evaluator status: [Evaluations API](references/reference-python-m3-evaluations.md)
6. Saved results and traces: [Result problems](references/troubleshooting-results.md)

A tool that reports an error returns a result with `is_error=True`. Transport,
protocol, and timeout failures raise exceptions instead.
