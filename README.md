# M3

[![CI](https://github.com/sineframe/m3/actions/workflows/ci.yml/badge.svg)](https://github.com/sineframe/m3/actions/workflows/ci.yml)

M3 tests MCP servers and the agents that use them. It runs Python and pytest
tests, captures MCP evidence, and can save runs for inspection and comparison.

## Install and start

Install the CLI with uv:

```sh
uv tool install sf-m3-cli
```

On macOS or Linux, the shell installer is an alternative:

```sh
curl -LsSf https://m3.sineframe.com/install.sh | sh
```

Then initialize your project:

```sh
m3 init
m3 setup
m3 doctor
```

Then follow the [first MCP test](https://m3.sineframe.com/docs/getting-started).
It uses a local server and needs no model credentials.

## Agent skill

Install the `testing-with-m3` skill in the project where you want an agent to
write or debug M3 tests:

```sh
npx skills add sineframe/m3@testing-with-m3
```

Use `-g` to make the skill available across projects. The published skill and
its supporting references are explained in the
[agent skill guide](https://m3.sineframe.com/docs/guides/agents/skill).

## Documentation

- [Documentation](https://m3.sineframe.com/docs/)
- [Test a local server](https://m3.sineframe.com/docs/guides/servers/stdio)
- [Test agent behavior](https://m3.sineframe.com/docs/guides/agents/first-test)
- [CLI reference](https://m3.sineframe.com/docs/reference/cli/)
- [Python reference](https://m3.sineframe.com/docs/reference/python/)
- [Contributing](https://github.com/sineframe/m3/blob/main/docs/contributing.md)

The SDK and CLI use separate environments. The CLI runs pytest in the project
environment and saves history; tests import the SDK.

## Development

Read [Architecture](https://github.com/sineframe/m3/blob/main/docs/architecture.md), then use the package README for the
area you are changing. Documentation changes follow the
[documentation writing guide](https://github.com/sineframe/m3/blob/main/docs/documentation-writing-guide.md).

```sh
just setup
just test-all
just lint
just format-check
```

M3 is licensed under Apache-2.0.
