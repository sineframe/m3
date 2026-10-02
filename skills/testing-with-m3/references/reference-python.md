<!-- Generated from docs/site/reference/python/index.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Python SDK reference

Start with the small package-root surface for ordinary synchronous tests:

```python
from m3 import MCPTestKit, StdioServer, expect
```

Use focused modules for the rest of the supported contract:

| Need | Reference |
| --- | --- |
| Core kit, direct operations, and agent sessions | [Core execution](reference-python-m3-core.md) |
| Server, result, policy, and operation values | [Types](reference-python-m3-types.md) |
| Execution assertions | [Matchers](reference-python-m3-matchers.md) |
| Typed traces | [Observability](reference-python-m3-observability.md) |
| Evaluators and aggregation | [Evaluations](reference-python-m3-evaluations.md) |
| Elicitation and managed input | [Elicitation](reference-python-m3-elicitation.md) |
| Matrix cases | [Matrices](reference-python-m3-matrix.md) |
| Test doubles and snapshots | [Testing utilities](reference-python-m3-testing.md) |
| Persistent and in-memory stores | [Storage](reference-python-m3-storage.md) |
| Exceptions | [Errors](reference-python-m3-errors.md) |

Each page explains ownership, inputs, results, and failure behavior. Exact
signatures are checked against the release's public export contract.

Use the [Python API inventory](https://m3.sineframe.com/docs/reference/python/api) for exact signatures, model fields,
default factories, enum values, properties, and public methods across every
declared module. Compatibility import modules appear under their own headings.
