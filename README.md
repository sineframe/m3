# M3

Test MCP servers and the agents that use them.

M3 turns MCP interactions into ordinary, repeatable Python tests. Discover
tools and schemas, exercise real calls, capture typed traces and evidence, and
compare a new run with a saved baseline. The standalone CLI is the recommended
starting point: it runs your existing pytest suite, records managed runs, and
opens a local browser viewer when you need one.

M3 is currently an alpha release.

[![CI](https://github.com/sineframe/m3/actions/workflows/ci.yml/badge.svg)](https://github.com/sineframe/m3/actions/workflows/ci.yml)

## What you can do

- Verify an MCP server's tools, schemas, responses, and error handling.
- Test direct MCP clients as well as agent sessions and harnesses.
- Exercise native Claude Code, OpenCode, Codex, and Pi harnesses, or connect
  another agent through an ACP-compatible adapter.
- Capture lifecycle events, tool calls, traces, artifacts, and evaluations.
- Repeat trials across harnesses and inspect aggregate results.
- Save executions to SQLite, produce deterministic feedback bundles, and
  compare later runs with `--baseline`.

## Test the behavior that matters

Mark one ordinary pytest test and let the CLI supply each harness and model:

```python
import pytest
from m3 import expect

@pytest.mark.m3
def test_shipping(agent, shipping_server):
    result = agent.run("Get a local shipping quote", server=shipping_server)
    expect(result).to_have_tool_call("shipping_quote", server=shipping_server.name,
                                     status="success")
```

Set credentials with exported `OPENCODE_API_KEY` and `OPENAI_API_KEY`, or use
an explicitly requested `.env` file. Then run two selections for two trials:

```bash
m3 test --env-file .env --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol --trials 2 -- tests/test_shipping.py
```

This collects four agent items and performs four executions. The fixture
supplies the selected agent; the test supplies the MCP server and assertion.

## Bring your own harness

Built-in harnesses are convenient, but you can bring any agent implementing
Agent Client Protocol (ACP) v1. Provide an agent dictionary with a manifest
describing the executable, arguments, protocol version, and environment-variable references:

```json
{
  "schema_version": "m3.harness.v1",
  "protocol": "acp",
  "protocol_version": 1,
  "command": "your-agent",
  "args": ["--acp"],
  "env": {"MY_AGENT_API_KEY": "${MY_AGENT_API_KEY}"}
}
```

Bind the MCP server using a transport the agent advertises and supports;
Streamable HTTP, stdio, and SSE are available where applicable. M3
validates the manifest, can check local readiness and probe the configured
process, then records the agent turn and captured MCP tool evidence using the
same assertions as native harnesses.

See the [BYO ACP guide](sdk/docs/quick-start.md#bring-your-own-harness-with-acp)
and [complete ACP example](sdk/docs/examples.md#4-bring-your-own-harness-with-acp)
for the manifest shape, probes, and executable test.

## Ask your coding agent to get started

If you use a coding agent, M3 includes a reusable
[`testing-with-m3`](skills/testing-with-m3/SKILL.md) skill. Ask your
preferred agent to install the skill from this repository and use it to create
tests for your server or agent workflow. This repository is private, so the
agent needs authenticated Git access or `GITHUB_TOKEN`/`GH_TOKEN`. If it cannot
access GitHub, use a local checkout and point it to
`skills/testing-with-m3/SKILL.md` and that directory's `references/` files.

The skill helps an agent inspect the real MCP contract, choose direct server
tests or agent-behavior tests, assert captured tool evidence, and iterate using
feedback reports and baselines. The recommended workflow is to use the CLI to
run the tests and inspect persistent history or the local UI.

```text
Install and use the M3 skill from
https://github.com/sineframe/m3/tree/main/skills/testing-with-m3
(authenticated GitHub access or GITHUB_TOKEN/GH_TOKEN may be required).
If this repository is available only as a local checkout, read
skills/testing-with-m3/SKILL.md and its references/ directory instead.
Read its testing patterns, then add and run the smallest tests that verify
<the behavior I care about> against <my MCP server or agent workflow>.
If this project has no M3 test yet, run m3 init first and replace
its skipped starter test after inspecting the real server contract.
Keep direct server checks separate from agent tool-selection checks, and use
the M3 CLI to run the tests and inspect the resulting report or UI.
```

This is one onboarding workflow; M3 works with any agent and any ordinary
Python and pytest workflow.

## Start with the CLI

The standalone `m3` command runs your existing pytest suite in its project
environment and includes the local browser viewer. No Node.js or frontend
checkout is needed in the project under test. The CLI guide covers installation,
project setup, environment checks, test selection, persistent results, UI use,
and troubleshooting.

Start with the [CLI installation guide](cli/README.md#install), then follow the
[project setup and testing guide](cli/README.md#set-up-a-project).

From the project root, run `m3 init` to answer the project and suite name
questions and create a skipped starter test and `.env.example` key template.
Then run `m3 setup`, fill in the test, and use `m3 test --env-file .env` when
the test needs provider keys.

CLI-managed runs use `.m3/executions.sqlite` by default and write an
agent-readable report to `.m3/reports/<run-id>/feedback.json`. The CLI
guide explains how to select pytest arguments, compare a run with a baseline,
and open the bundled viewer.

## Use the SDK with direct pytest

The SDK is the library layer for Python tests. When managed history and the
viewer are not needed, users may run SDK tests directly with pytest. The
[SDK README](sdk/README.md) and [quick start](sdk/docs/quick-start.md) cover
installation, direct clients, agent sessions, typed assertions, async tests,
and explicit persistence. The CLI itself also runs pytest in the project's
environment; these are two ways to run the same test style.

## Choose your next step

| I want to… | Read |
| --- | --- |
| Install and run the standalone command | [CLI guide](cli/README.md) |
| Write direct Python or pytest tests | [SDK quick start](sdk/docs/quick-start.md) |
| Understand traces, storage, and evaluations | [SDK concepts](sdk/docs/concepts.md) |
| Score repeated agent trials | [Evaluation guide](sdk/docs/evaluations.md) |
| Test a deployed Streamable HTTP server | [HTTP guide](sdk/docs/http.md) |
| Give a coding agent M3 instructions | [Testing skill](skills/testing-with-m3/SKILL.md) |
| Contribute to the implementation | [Architecture](docs/architecture.md) |

## Development

Contributors should start with the [architecture guide](docs/architecture.md),
then use the package-specific guides for SDK, app, and CLI workflows. See the
[release guide](docs/releasing.md) when preparing a release.
