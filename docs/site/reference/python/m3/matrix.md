---
title: "Matrix API"
description: "ToolCase names one tool invocation and its arguments. ServerCase groups tool cases under the server that owns them. ToolMatrix validates and expands those cases without starting processes or network work."
---

# Matrix API

`ToolCase` names one tool invocation and its arguments. `ServerCase` groups
tool cases under the server that owns them. `ToolMatrix` validates and expands
those cases without starting processes or network work.

`ToolMatrix.cases()` returns `ToolMatrixCase` values for scripts.
`ToolMatrix.parametrize()` produces normal pytest parametrization with stable
case IDs and marks. Work starts only when the selected case is executed.

Matrices directly invoke known tools. They do not test which tool an agent
chooses. `HarnessMatrix` combines harnesses, servers, tools, and trials for
agent cases; its `HarnessMatrixCase.run()` method executes one expanded case.
Keep deterministic tool contracts in `ToolMatrix`.

For selection through CLI options and an unchanged pytest action, see [Run the
same test across agent harnesses](../../../guides/agents/multiple-harnesses.md).
