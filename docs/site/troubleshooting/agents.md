---
title: "Agent readiness, approvals, and timeouts"
description: "Use m3 doctor --require harness:NAME for a system runtime, or select a managed runtime/version supported on the current OS and CPU."
---

# Agent readiness, approvals, and timeouts

## Harness executable is unavailable

Use `m3 doctor --require harness:NAME` for a system runtime, or select a managed
runtime/version supported on the current OS and CPU.

## Tool call was denied

Approval is separate from MCP elicitation. Set the permission policy required
by the harness only for a trusted server and scoped workspace.

## Execution timed out

Use `--execution-timeout SECONDS` to set an intentional deadline. Inspect the
trace and harness diagnostics before increasing it; a server waiting for input
or approval will not be fixed by an arbitrary larger value.
