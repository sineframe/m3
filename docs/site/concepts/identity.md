---
title: "Runs, suites, cases, and executions"
description: "A **run** is one invocation of pytest or one explicit SDK run ID. The CLI prints its run ID and writes the corresponding feedback under .m3/reports/<run-id>/feedback.json."
---

# Runs, suites, cases, and executions

A **run** is one invocation of pytest or one explicit SDK run ID. The CLI
prints its run ID and writes the corresponding feedback under
`.m3/reports/<run-id>/feedback.json`.

A **suite** groups related tests for selection and comparison. Tests persisted
by the CLI need a non-empty `suite_name`; `--suite` selects existing names and
does not assign one.
The results database assigns one suite ID per exact trimmed name. Standalone
SDK runs pass `suite_name` to `MCPTestKit` or the execution spec.

A **case** is the stable logical scenario, such as “local shipping quote.” An
**execution** is one concrete attempt of that case. Two trials therefore create
two executions while retaining the same case identity. An agent session can
contain several **turns**, each with its own response and evidence.

Pytest records whether the test passed. M3 separately records execution and
evaluation outcomes. See [Outcomes](outcomes.md) before using evaluation
pass rates as test pass rates.
