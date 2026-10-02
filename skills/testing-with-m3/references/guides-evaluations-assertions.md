<!-- Generated from docs/site/guides/evaluations/assertions.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

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

## Agent evidence

```python
from m3 import expect

result = agent.run("Quote a 2 kg parcel in the local zone.",
    server=server,
    permission_policy="allow",
)

expect(result).to_have_tool_call("shipping_quote", arguments={"weight_kg": 2, "zone": "local"},
    status="success",
)
```

This checks the captured call, its arguments, and its result status. It does
not accept the agent's prose as proof that the call occurred. Grant tool
approval only to the scoped test server and workspace.

See the [matcher reference](reference-python-m3-matchers.md) for count, choice,
and result predicates.
