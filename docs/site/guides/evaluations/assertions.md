---
title: "Assert the evidence that matters"
description: "Use ordinary Python assertions for direct operation results. Use expect when you need to ask a question about a complete M3 execution or agent turn."
---

# Assert the evidence that matters

Use ordinary Python assertions for direct operation results. Use `expect` when
you need to ask a question about a complete M3 execution or agent turn.

## Direct result

```python
with MCPTestKit(env={}) as kit, kit.direct(server) as client:
    result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"}
    )

assert result.is_error is False
assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

This checks the server response directly.

## Tool metadata and response size

```python
from m3 import expect

with MCPTestKit(env={}) as kit, kit.direct(server) as client:
    tools = client.list_all_tools()
    result = client.call_tool("search_orders", {"query": "late"})

expect(tools).to_have_explicit_tool_annotations()
expect(tools).to_have_tool_annotations("delete_*", destructive_hint=True)
expect(result).to_have_response_size_at_most(max_bytes=20_000, max_tokens=5_000)
```

`to_have_explicit_tool_annotations` fails for every tool that has no title
(from `title` or `annotations.title`) or leaves a hint undeclared. Hints are
checked as the server declared them, so MCP spec defaults never count, and
removing a hint fails the test. `to_have_tool_annotations` also fails when no
tool name matches the pattern. The size check measures `content` and
`structuredContent` as compact JSON in UTF-8 bytes, spelled as MCP sends them
(unset content-block fields are left out). It also accepts a trace
`ToolResult`. `max_tokens` compares an estimate of characters divided by four.

## Agent evidence

```python
from m3 import expect

result = agent.run("Quote a 2 kg parcel in the local zone.", server=server)

expect(result).to_have_tool_call("shipping_quote", arguments={"weight_kg": 2, "zone": "local"},
    status="success",
)
```

This checks the captured call, its arguments, and its result status. It does
not accept the agent's prose as proof that the call occurred.

Codex needs no permission setting for this call; M3 approves it from the tool
selection. An ACP agent that asks permission before calling a tool is denied
by default, so pass `permission_policy="allow"` to `agent.run(...)` for it.
Grant that only to a trusted agent with the scoped test server and workspace,
because it also approves the agent's requests to run commands or edit files.

See the [matcher reference](../../reference/python/m3/matchers.md) for count, choice,
and result predicates.
