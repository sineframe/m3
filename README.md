# MCP Pal

MCP Pal helps developers test MCP servers and verify how coding agents use
their tools. Tests are ordinary Python tests: run them directly with pytest or
through the standalone CLI to record executions and inspect them in a local UI.

## Choose your entry point

| If you want to… | Start here |
|---|---|
| Install the `mcp-pal` command, run tests, or open the local UI | [CLI guide](cli/README.md) |
| Write Python tests against MCP servers or agent harnesses | [SDK guide](sdk/README.md) and [SDK quick start](sdk/docs/quick-start.md) |
| Work on the internal FastAPI viewer runtime or legacy UI | [App guide](app/README.md) |
| Teach a coding agent how to set up and use MCP Pal | [Agent skill](skills/testing-with-mcp-pal/SKILL.md) |

Most users should start with the CLI guide. SDK users who only need a library
and pytest can use the SDK directly without installing the CLI or UI.

Runs through the MCP Pal pytest plugin produce a deterministic JSON feedback
bundle under `.mcp-pal/reports/<run-id>/`. A later `mcp-pal test
--baseline RUN_ID` run compares the observed interface and saved results for a
coding agent. Test `print()` and logging output remain diagnostics rather than
an inferred score.

## The three packages

This repository is a `uv` workspace with three Python distributions. They have
different audiences and installation scopes:

| Directory | Distribution | Role |
|---|---|---|
| [`sdk/`](sdk/README.md) | `mcp-pal` | Public Python SDK, pytest integration, harnesses, tracing, assertions, and storage interfaces. Installed in the project being tested. |
| [`cli/`](cli/README.md) | `mcp-pal-cli` | Standalone machine-level tool that provides the `mcp-pal` command, launches pytest in the project environment, and serves the bundled production UI. |
| [`app/`](app/README.md) | `mcp-pal-app` | Internal FastAPI viewer, application services, and legacy Streamlit surface used by the standalone product. Consumers do not install it directly. |

The installation boundary matters. Adding `mcp-pal[pytest]` to a project
installs the SDK, but it does not provide the `mcp-pal` command or UI. The
standalone CLI is installed separately and keeps its own CLI, app runtime, and
bundled UI outside the tested project. `mcp-pal setup` then prepares that
project with the matching SDK, pytest plugin, and SQLite support.

Detailed installation, version matching, command behavior, and troubleshooting
belong in the [CLI guide](cli/README.md). SDK APIs and test patterns belong in
the [SDK documentation](sdk/docs/README.md).

## Give your coding agent the MCP Pal skill

The repository ships the
[`testing-with-mcp-pal`](skills/testing-with-mcp-pal/SKILL.md) skill. It teaches
agents how to install and configure the separate CLI, choose between CLI and
direct pytest execution, define SDK tests, and assert captured tool evidence.

For Codex, ask the agent to install it from this repository:

> Use `$skill-installer` to install the skill from GitHub repository
> `mcppal/mcp-pal`, path `skills/testing-with-mcp-pal`.

The repository is private, so the agent needs access through existing Git
credentials or `GITHUB_TOKEN`/`GH_TOKEN`. The installed skill becomes available
on the next agent turn. Then point the agent at the skill explicitly:

> Use `$testing-with-mcp-pal` to write and run tests for this MCP server.

For another agent system that supports skills, install the
[`skills/testing-with-mcp-pal/`](skills/testing-with-mcp-pal/) directory using
that system's skill installation mechanism. If it does not support installed
skills, point the agent directly to
[`SKILL.md`](skills/testing-with-mcp-pal/SKILL.md) and its `references/`
directory.

## Repository development

Install [`uv`](https://docs.astral.sh/uv/) and
[`just`](https://just.systems/), then use the workspace recipes:

```bash
just setup
just test
just check
```

Run `just --list` for focused tests, local API/UI commands, harness probes, and
packaging checks. Live provider tests and the browser gate are opt-in because
they require external credentials and may incur provider costs. Application
configuration is documented in the [App guide](app/README.md).

`just setup` also enables the repository's pre-push hook. The hook runs the
complete non-live SDK, example, application, and CLI test suites before each
push. Run `just install-hooks` to enable it in an existing checkout. In an
urgent situation, `git push --no-verify` bypasses the hook, but doing so is not
recommended.

Maintainers preparing a tagged build should follow the
[release guide](docs/releasing.md).
