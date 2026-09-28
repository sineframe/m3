# Python SDK reference

Start with the small package-root surface for ordinary synchronous tests:

```python
from m3 import MCPTestKit, StdioServer, expect
```

Use focused modules for the rest of the supported contract:

| Need | Reference |
| --- | --- |
| Core kit, direct operations, and agent sessions | [Core execution](/reference/python/m3/core) |
| Server, result, policy, and operation values | [Types](/reference/python/m3/types) |
| Execution assertions | [Matchers](/reference/python/m3/matchers) |
| Typed traces | [Observability](/reference/python/m3/observability) |
| Evaluators and aggregation | [Evaluations](/reference/python/m3/evaluations) |
| Elicitation and managed input | [Elicitation](/reference/python/m3/elicitation) |
| Matrix cases | [Matrices](/reference/python/m3/matrix) |
| Test doubles and snapshots | [Testing utilities](/reference/python/m3/testing) |
| Persistent and in-memory stores | [Storage](/reference/python/m3/storage) |
| Exceptions | [Errors](/reference/python/m3/errors) |

Each page explains ownership, inputs, results, and failure behavior. Exact
signatures are checked against the release's public export contract.

Use the [Python API inventory](/reference/python/api) for exact signatures,
model fields, and public methods across every declared module.
