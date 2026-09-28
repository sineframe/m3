# Direct tests and agent tests

M3 supports two different questions.

A **direct test** controls the MCP operation itself: it chooses a server, method, arguments, and expected result. Use it to check a server’s contract.

An **agent test** gives an agent a goal and checks the interaction M3 observed: which tool it called, with what arguments, and what result the server returned. Use it to check agent behavior around your server.

```text
direct test: test code -> MCP operation -> server result -> assertion
agent test:  test code -> agent turn -> MCP operation(s) -> evidence -> assertion
```

An agent’s final answer is not evidence that it called the expected tool. Assert recorded tool evidence, and assert the final response separately only when that response is part of the contract. Agent runs also depend on harness, model, provider configuration, and approval behavior; record those inputs when comparing results.

Start with [a direct server test](/guides/servers/tools), then [test an agent’s tool use](/guides/agents/first-test).
