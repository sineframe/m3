# M3

[![CI](https://github.com/sineframe/m3/actions/workflows/ci.yml/badge.svg)](https://github.com/sineframe/m3/actions/workflows/ci.yml)

M3 tests MCP servers and the agents that use them. It runs Python and pytest
tests, captures MCP evidence, and can save runs for inspection and comparison.

## Install and start

```sh
uv tool install sf-m3-cli
m3 init
m3 setup
m3 doctor
```

Then follow the [first MCP test](https://m3.sineframe.com/docs/getting-started).
It uses a local server and needs no model credentials.

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
