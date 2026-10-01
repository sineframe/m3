---
title: "Bring your own agent"
description: "Connect an existing ACP-compatible agent or expose a custom agent through ACP."
---

# Bring your own agent

Run your existing agent with M3 while keeping its model, tools, and vendor configuration. M3 launches it as a subprocess through the Agent Client Protocol (ACP). You install and manage the executable.

Choose the task that matches your setup:

- [Connect an ACP-compatible agent](acp-connect.md) when an existing agent already speaks ACP.
- [Expose your custom agent through ACP](acp-wrapper.md) when your agent uses another interface and you will provide a small ACP process.

Both paths pass MCP servers to the agent for a session and record its messages, tool calls, results, and lifecycle frames. Check the captured MCP result to confirm what the server returned. An agent's tool-call report alone cannot confirm that response.

The [ACP reference](../../reference/acp.md) covers manifest fields, session selection, environment isolation, tool-policy evidence, cancellation, and failures. Compare ACP with native harnesses in [agent harness compatibility](harnesses.md).
