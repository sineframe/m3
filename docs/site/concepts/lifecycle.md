---
title: "Runtime and connection lifecycle"
description: "MCPTestKit owns the surrounding runtime. A direct client owns one initialized MCP connection. Entering the client starts its transport; leaving it closes the connection and any subprocess that client started."
---

# Runtime and connection lifecycle

`MCPTestKit` owns the surrounding runtime. A direct client owns one initialized
MCP connection. Entering the client starts its transport; leaving it closes
the connection and any subprocess that client started.

Keep calls that depend on server session state inside the same client context.
Inspect operation results while the client is open. Inspect the finalized trace
after the client has closed:

```python
with MCPTestKit(env={}) as kit, kit.direct(server) as client:
    result = client.call_tool("shipping_quote", arguments)

trace = client.final_trace
assert trace is not None
view = trace.view()
```

A deployed HTTP service is not owned by the client and remains running. A
stdio subprocess started from `StdioServer` is owned and stopped by the client.
Agent sessions likewise finalize their result when their context exits.

See [Read finalized traces](../guides/results/traces.md) for an executable test.
