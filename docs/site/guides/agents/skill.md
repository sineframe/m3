---
title: "Install the M3 agent skill"
description: "Install testing-with-m3 from the M3 GitHub repository so a coding agent can set up M3, write tests, run them, and inspect saved evidence."
---

# Install the M3 agent skill

Install `testing-with-m3` when you want a coding agent to set up M3, write or
debug tests, run them, and inspect the saved evidence. The installable files
live under `skills/testing-with-m3` in the M3 repository. This page documents
the skill without moving its source into the documentation tree.

## Requirements

- Install Node.js so the `npx` command is available.
- Run the command from the project where the agent will work.
- Allow Git access to the public `sineframe/m3` repository.

## Install in one project

```sh
npx skills add sineframe/m3@testing-with-m3
```

The `skills` package after `npx` is the installer. The
`sineframe/m3@testing-with-m3` argument identifies the GitHub repository and
the skill within it. The installer clones the repository and discovers
`skills/testing-with-m3/SKILL.md`; the M3 skill does not need a separate
registry publication.

Review the selected agent and destination shown by the installer before you
confirm. Project installation keeps the skill with that project.

## Install across projects

Use a global installation when the same agent should find the skill in every
project:

```sh
npx skills add sineframe/m3@testing-with-m3 -g
```

Run `npx skills update testing-with-m3 -g` to update the global copy. For a
project installation, omit `-g` from the update command.

## Inspect the installed instructions

Read the [skill entrypoint](../../../../skills/testing-with-m3/SKILL.md) before
using it. The installer also copies the references linked from that entrypoint:

- [CLI runner](../../../../skills/testing-with-m3/references/cli-runner.md)
- [Test patterns](../../../../skills/testing-with-m3/references/test-patterns.md)
- [Feedback and iteration](../../../../skills/testing-with-m3/references/feedback-iteration.md)

The entrypoint tells the agent which reference to read for the current task.
It does not load every reference for every request.

Next: [write your first MCP test](../../getting-started.md) or
[test an agent's tool use](first-test.md).
