# Matchers

`expect(subject)` creates an `Expectation` whose methods raise
`AssertionError` when captured evidence does not satisfy the predicate.
`check(subject)` records failures through a `CheckGroup`, allowing several
related checks to be reported together when the plugin has a recording
binding.

Use matchers for execution and trace evidence: tool names, qualified tool
choices, arguments, results, status, counts, messages, and terminal outcomes.
Pass a `TurnResult` when a check should apply to one turn rather than the whole
session.

```python
expect(result).to_have_tool_call(
    "shipping_quote",
    arguments={"weight_kg": 2, "zone": "local"},
    status="success",
)
```

Unavailable evidence fails a predicate that requires it; it is not treated as
an empty value. Matcher failures are ordinary assertion failures even when the
plugin also records them as evaluation evidence.
