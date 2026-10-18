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
run this command from the project root. Replace `VERSION` with the version
number that `m3 --version` prints after `m3 `: for `m3 0.2.22`, use `0.2.22`.

```sh
npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill testing-with-m3 --agent universal claude-code -y
```

The command needs Node.js and Git. It installs the skill only at
`.agents/skills/testing-with-m3/`, creates a `.claude/skills/testing-with-m3`
link, and records the source and release in `skills-lock.json`. The skills
CLI version is pinned to 1.7.0, and telemetry is disabled for this command.
[Installed files](#installed-files) explains whether to commit them.

M3 does not run the command when:

- you pass `--no-skill` to `m3 init` or `m3 setup`;
- any of `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `BUILDKITE`, `CIRCLECI`,
  `JENKINS_URL`, `TF_BUILD`, `TEAMCITY_VERSION`, `BITBUCKET_BUILD_NUMBER`, or
  `CODEBUILD_BUILD_ID` is set to a non-empty value. M3 prints
  `Agent skill: skipped in CI. To install it, run:` followed by the command.
  Run that command yourself when `CI` is set in a shell that is not a CI run;
- you run `m3 setup` in a directory without `m3.toml`;
- M3 is installed from a source checkout: an editable install, a local
  directory, or a Git URL. These builds carry a placeholder version with no
  release tag, so there is no release to install. Installation is skipped, and
  M3 prints
  `Agent skill: skipped for a development install of M3. To install it, run:`
  followed by:

  ```sh
  npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill testing-with-m3 --agent universal claude-code -y
  ```

- a `testing-with-m3` skill that M3 did not install already exists in the
  project or in your home directory. For a project copy, M3 prints the command
  above to replace it. For a home directory copy, M3 prints the command with
  `-g` added, because the command without `-g` leaves the home directory copy
  in place and adds a second copy to the project.

If `npx` is missing or the command fails, M3 prints the command and finishes
setup normally.

## Installed files

The installation writes three paths in the project root:

- `.agents/skills/testing-with-m3/`, the skill and its `references/` copy of
  the documentation;
- `.claude/skills/testing-with-m3`, a symlink to the directory above;
- `skills-lock.json`, which records the source (`sineframe/m3`) and the release
  (`ref`) the skill was installed from.

Commit all three if every contributor should get the skill without running
`m3 init` or `m3 setup`. To keep the skill out of version control, add all three
paths to `.gitignore`:

```gitignore
.agents/skills/testing-with-m3/
.claude/skills/testing-with-m3
skills-lock.json
```

Keep `skills-lock.json` and the skill directory together. M3 treats a skill
without a matching lock entry as one it did not install, and never updates it.

`m3 init` and `m3 setup` compare the release in `skills-lock.json` with the
installed M3 release. They run the command again when the releases differ, so a
copy installed for an older or a newer M3 release is replaced with the one for
the release you run. They also run it again when the lock entry exists but the
skill files were deleted.

## Agents that read the skill

The command installs the skill for two targets:

- `--agent universal` writes `.agents/skills/`. The skills 1.7.0 README lists
  `.agents/skills/` as the project path for Amp, Cline, Codex, Cursor, Gemini
  CLI, GitHub Copilot, OpenCode, and other agents. Check that README for the
  current list, because it is the skills CLI that decides which agents read
  which directory.
- `--agent claude-code` writes the `.claude/skills/` link for Claude Code.

To add an agent that uses another directory, run the install command yourself
and append its name to `--agent`. For Windsurf:

```sh
npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill testing-with-m3 --agent universal claude-code windsurf -y
```

M3's automatic command passes only `universal` and `claude-code`. Agent names
are in the skills 1.7.0 README under "Supported Agents".

## Install it yourself

Run the command above with your M3 version. To install it globally, use:

```sh
npx --yes skills@1.7.0 add sineframe/m3#vVERSION --skill testing-with-m3 --agent universal claude-code -g
```

M3 does not update a global installation, so rerun the command after upgrading
M3.

[Read the skill source.](../../../../skills/testing-with-m3/SKILL.md)
