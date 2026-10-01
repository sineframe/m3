---
title: "Bring your own agent"
description: "Connect an existing ACP-compatible agent or expose a custom agent through ACP."
---

# Bring your own agent

Use an agent you already run when you want to keep its model, tools, and vendor configuration while M3 captures the test execution. M3 launches the agent as a subprocess through the Agent Client Protocol (ACP); it does not download or manage that executable.

Choose the task that matches your setup:

- [Connect an ACP-compatible agent](acp-connect.md) when an existing agent already speaks ACP.
- [Expose your custom agent through ACP](acp-wrapper.md) when your agent uses another interface and you will provide a small ACP process.

Both paths pass MCP servers to the agent for a session and record emitted messages, tool calls, results, and lifecycle frames. An agent saying it used a tool is not proof that the server returned a result; check the recorded tool evidence in the test.

ACP has different option and evidence boundaries from native harnesses. Read the [ACP reference](../../reference/acp.md) for manifest fields, session selection, environment isolation, tool-policy evidence, cancellation, and failure behavior. For the tested feature comparison, see [agent harness compatibility](harnesses.md).
