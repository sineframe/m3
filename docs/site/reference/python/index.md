---
title: "Python SDK reference"
description: "Start with the small package-root surface for ordinary synchronous tests:"
---

# Python SDK reference

Start with the small package-root surface for ordinary synchronous tests:

```python
from m3 import MCPTestKit, StdioServer, expect
```

Use focused modules for the rest of the supported contract:

| Need | Reference |
| --- | --- |
| Core kit, direct operations, and agent sessions | [Core execution](m3/core.md) |
| Server, result, policy, and operation values | [Types](m3/types.md) |
| Execution assertions | [Matchers](m3/matchers.md) |
| Typed traces | [Observability](m3/observability.md) |
| Evaluators and aggregation | [Evaluations](m3/evaluations.md) |
| Elicitation and managed input | [Elicitation](m3/elicitation.md) |
| Matrix cases | [Matrices](m3/matrix.md) |
| Test doubles and snapshots | [Testing utilities](m3/testing.md) |
| Persistent and in-memory stores | [Storage](m3/storage.md) |
| Exceptions | [Errors](m3/errors.md) |

Each page explains ownership, inputs, results, and failure behavior. Exact
signatures are checked against the release's public export contract.

Use the [Python API inventory](api.md) for exact signatures, model fields,
default factories, enum values, properties, and public methods across every
declared module. Compatibility import modules appear under their own headings.
