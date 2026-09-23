# M3

[![CI](https://github.com/sineframe/m3/actions/workflows/ci.yml/badge.svg)](https://github.com/sineframe/m3/actions/workflows/ci.yml)

**Test MCP servers and the agents that use them.**

M3 turns MCP interactions into repeatable Python tests. Run your existing
pytest suite, capture tool calls and traces, compare runs, and explore results
in a local browser viewer.

## Install the CLI

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install sf-m3-cli
```

Or use the shell installer on macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/sineframe/m3/main/scripts/install-latest.sh | sh
```

## Get started

From the project you want to test:

```sh
cd your-project
m3 init
m3 setup
m3 doctor
```

`m3 init` creates a starter test. Replace its placeholder with a real
assertion, then run `m3 test -- tests/test_m3_starter.py`. `m3 setup` installs
the matching Python SDK into the project's environment.

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
from m3.types import HTTPServer, PermissionPolicy, TrustLevel

@pytest.fixture
def shipping_server():
    return HTTPServer(
        name="shipping",
        url="https://shipping.example.com/mcp",
        trust=TrustLevel.PUBLIC,
    )

@pytest.mark.m3
def test_shipping(agent, shipping_server):
    result = agent.run(
        "Get a local shipping quote",
        server=shipping_server,
        permission_policy=PermissionPolicy(mode="allow"),
    )
    expect(result).to_have_tool_call("shipping_quote")
```

Replace the URL with your MCP endpoint. Your fixture supplies `shipping_server`;
M3 supplies `agent`.

Set credentials with exported `OPENCODE_API_KEY` and `OPENAI_API_KEY`, or use
an explicitly requested `.env` file. Then run two selections for two trials:

```bash
m3 test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol \
  --trials 2 \
  -- tests/test_shipping.py
```

This collects four agent items and performs four executions.

To test specific harness releases, select a managed runtime and put each
version after the harness name:

```bash
m3 test --runtime=managed \
  --harness opencode@1.18.30=opencode/big-pickle \
  --harness opencode@1.18.31=opencode/big-pickle \
  -- tests/test_shipping.py
```

M3 downloads each release into a per-user cache, runs each selection with its
own executable, and records the resolved harness and model in results and
traces. See the [CLI guide](cli/README.md#managed-harness-runtimes).

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
Streamable HTTP and stdio are available where applicable. M3
validates the manifest, can check local readiness and probe the configured
process, then records the agent turn and captured MCP tool evidence using the
same assertions as native harnesses.

See the [BYO ACP guide](sdk/docs/quick-start.md#bring-your-own-harness-with-acp)
and [complete ACP example](sdk/docs/examples.md#4-bring-your-own-harness-with-acp)
for the manifest shape, probes, and executable test.

## Ask your coding agent to get started

If you use a coding agent, M3 includes a reusable
[`testing-with-m3`](skills/testing-with-m3/SKILL.md) skill. Ask your
preferred agent to install the skill from this repository and use it to
create tests for your server or agent workflow.

The skill helps an agent inspect the real MCP contract, choose direct server
tests or agent-behavior tests, assert captured tool evidence, and iterate using
feedback reports and baselines. The recommended workflow is to use the CLI to
run the tests and inspect persistent history or the local UI.

```text
Install and use the M3 skill from
https://github.com/sineframe/m3/tree/main/skills/testing-with-m3
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
