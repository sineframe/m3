---
title: "Install the M3 agent skill"
description: "How m3 init and m3 setup install the testing-with-m3 agent skill, and how to install it yourself."
---

# Install the M3 agent skill

The `testing-with-m3` skill tells a coding agent what M3 is, how to write and
run a first direct test, and which M3 documentation page to read next. Its
`references/` directory is a copy of the M3 documentation for one release, so
the agent reads documentation that matches the M3 you installed.

## Automatic installation

`m3 init` and `m3 setup` install the skill into the project when it is
missing, and update it when it was installed for a different M3 release. They
run this command from the project root, with `VERSION` replaced by the output
of `m3 --version`:

```sh
npx --yes skills add sineframe/m3#vVERSION --skill testing-with-m3 -y
```

The command needs Node.js and Git. The skills CLI copies the skill to
`.agents/skills/testing-with-m3/`, links it for detected agents such as
Claude Code (`.claude/skills/`), and records the source and release in
`skills-lock.json`. Commit those files if every contributor should get the
skill.

M3 does not run the command when:

- you pass `--no-skill` to `m3 init` or `m3 setup`;
- `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` is set;
- you run `m3 setup` in a directory without `m3.toml`;
- a `testing-with-m3` skill that M3 did not install already exists in the
  project or in your home directory. M3 prints the command to replace it.

If `npx` is missing or the command fails, M3 prints the command and finishes
setup normally.

## Install it yourself

Run the command above with your M3 version. Add `-g` to install it for all
projects. M3 does not update a global installation, so rerun the command
after upgrading M3.

[Read the skill source.](../../../../skills/testing-with-m3/SKILL.md)
