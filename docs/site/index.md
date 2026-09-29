---
title: "Test MCP servers and the agents that use them"
description: "M3 turns MCP behavior into Python tests. Test a server directly when you need a deterministic protocol contract; put an agent in the loop when you need to check tool selection, arguments, or multi-turn behavior."
---

# Test MCP servers and the agents that use them

M3 turns MCP behavior into Python tests. Test a server directly when you need a
deterministic protocol contract; put an agent in the loop when you need to
check tool selection, arguments, or multi-turn behavior.

[Write your first MCP test](getting-started.md) with a local server and no model
credentials. The walkthrough includes a passing test, an intentional failure,
and the saved evidence behind both results.

## Choose a task

| I need to… | Start here |
| --- | --- |
| Check a local MCP server | [Test a stdio server](guides/servers/stdio.md) |
| Check a deployed endpoint | [Test Streamable HTTP](guides/servers/http.md) |
| Verify how an agent uses tools | [Write an agent test](guides/agents/first-test.md) |
| Inspect a failed run | [Read traces](guides/results/traces.md) |
| Compare a change with an earlier run | [Compare with a baseline](guides/results/baselines.md) |
| Run tests in automation | [Run M3 in CI](guides/ci/run.md) |
| Look up a command or object | [Reference](reference/index.md) |

## Two ways to test

A direct test calls the MCP protocol without a model. It is the right starting
point for tool schemas, structured results, protocol errors, resources, and
prompts. An agent test adds a harness and model so you can inspect the tool
calls the agent actually made. [The testing model](concepts/testing-model.md)
explains the boundary.

The standalone `m3` command runs tests in the project environment, saves run
history, and serves the local viewer. The `m3` Python package is the SDK used by
the tests. They are installed separately; see [Install and update M3](start/install.md).

## Documentation version

The site header identifies the M3 release these pages describe. Commands and
examples are checked against that release before publication. Use the
[compatibility reference](reference/compatibility.md) for version-specific
harness support and known boundaries.
