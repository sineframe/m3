<!-- Generated from docs/site/guides/agents/acp.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Bring your own agent

Run your existing agent with M3 while keeping its model, tools, and vendor configuration. M3 launches it as a subprocess through the Agent Client Protocol (ACP). You install and manage the executable.

Choose the task that matches your setup:

- [Connect an ACP-compatible agent](guides-agents-acp-connect.md) when an existing agent already speaks ACP.
- [Expose your custom agent through ACP](guides-agents-acp-wrapper.md) when your agent uses another interface and you will provide a small ACP process.

M3 passes your MCP servers to the agent for a session and records its messages, tool calls, results, and lifecycle frames. Assertions against captured MCP responses check what the server returned. Agent-reported tool updates describe the agent's actions and may lack a matching server response.

The [ACP reference](reference-acp.md) covers manifest fields, session selection, environment isolation, tool-policy evidence, cancellation, and failures. Compare ACP with native harnesses in [agent harness compatibility](guides-agents-harnesses.md).
