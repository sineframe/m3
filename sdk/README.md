# M3 Python SDK

The `sf-m3` package provides direct MCP clients, agent sessions, pytest
integration, assertions, traces, evaluations, and optional persistence.

```sh
uv add "sf-m3[pytest]"
```

The SDK requires Python 3.10 or newer. Installing it does not install the
standalone `m3` command or viewer.

Use the [first test](https://m3.sineframe.com/docs/getting-started) for a
runnable project and the [Python reference](https://m3.sineframe.com/docs/reference/python/)
for public contracts.

## Develop the SDK

```sh
just setup
uv run --project sdk --extra pytest --group typecheck pytest sdk/tests
```

Runnable guide projects live under `sdk/examples/docs`. Follow the
[documentation writing guide](https://github.com/sineframe/m3/blob/main/docs/documentation-writing-guide.md)
when changing them.
