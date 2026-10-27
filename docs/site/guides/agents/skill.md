---
title: "Install the M3 agent skill"
description: "How m3 init and m3 setup install the testing-with-m3 agent skill, how to install it yourself, and how to add the preparing-directory-submissions skill for directory listings."
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
npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill testing-with-m3 --agent universal claude-code -y
```

The command needs Node.js and Git. It installs the skill only at
`.agents/skills/testing-with-m3/`, creates a `.claude/skills/testing-with-m3`
link, and records the source and release in `skills-lock.json`. The skills
CLI version is pinned to 1.7.0, and telemetry is disabled for this command.
Commit those files if every contributor should get the skill.

M3 does not run the command when:

- you pass `--no-skill` to `m3 init` or `m3 setup`;
- any of `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `BUILDKITE`, `CIRCLECI`,
  `JENKINS_URL`, `TF_BUILD`, `TEAMCITY_VERSION`, `BITBUCKET_BUILD_NUMBER`, or
  `CODEBUILD_BUILD_ID` is set to a non-empty value;
- you run `m3 setup` in a directory without `m3.toml`;
- M3 is installed for development (editable). Installation is skipped, and M3
  prints `Agent skill: skipped for a development install of M3. To install it, run:`
  followed by:

  ```sh
  npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill testing-with-m3 --agent universal claude-code -y
  ```
- a `testing-with-m3` skill that M3 did not install already exists in the
  project or in your home directory. M3 prints the command to replace it.

If `npx` is missing or the command fails, M3 prints the command and finishes
setup normally.

## Install it yourself

Run the command above with your M3 version. To install it globally, use:

```sh
npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill testing-with-m3 --agent universal claude-code -g
```

M3 does not update a global installation, so rerun the command after upgrading
M3.

[Read the skill source.](../../../../skills/testing-with-m3/SKILL.md)

## Prepare a directory submission

The `preparing-directory-submissions` skill is for builders submitting an MCP
server to the Claude connector directory, the OpenAI plugin directory
(ChatGPT and Codex) or the Muse connector directory. Ask your agent to get the
server ready for a directory, or paste the rejection feedback you received.
The agent then:

- runs [`m3 lint --directory`](../../reference/directory-readiness.md) and
  carries its findings over unchanged;
- does the checks that need judgment, such as steering language in
  descriptions, hints that don't match what the code does, catch-all tools,
  data requests, unsupported use cases, privacy, listing text, connectivity
  and Muse tool classes, and backs each one with a quote, a `file:line`
  reference or command output;
- drafts the review test cases each directory asks for and writes an M3
  direct test for each positive case. It runs only the tests for read-only
  tools unless you confirm a test account;
- writes `directory-readiness-report.md`, with a table per directory that
  quotes the policy line behind every failure and proposes fixes as diffs.

The agent never edits your server without asking, never submits anything,
and never asks for credentials. The report says what was checked and what
was found. It can't tell you whether a directory will approve the server.

`m3 init` and `m3 setup` don't install this skill. Install it once per
project with the same command as `testing-with-m3`, with `VERSION` replaced
by the output of `m3 --version`:

```sh
npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill preparing-directory-submissions --agent universal claude-code -y
```

It installs at `.agents/skills/preparing-directory-submissions/`, next to
`testing-with-m3`, and adds its own entry to `skills-lock.json`. It doesn't
change `testing-with-m3`, and `m3 setup` keeps managing `testing-with-m3` as
before. Use `-g` in place of `-y` to install it globally. Rerun the
command after upgrading M3; the skill's policy excerpts are dated, and newer
releases carry newer ones.

The skill works with any M3 release. Without `m3 lint --directory`, the agent
still runs the judgment checks, marks the lint rows "not checked" and tells
you how to upgrade.

[Read the skill source.](../../../../skills/preparing-directory-submissions/SKILL.md)
