<!-- Generated from docs/site/getting-started.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Write your first MCP test

This walkthrough starts a small MCP server as a local process, discovers its
`shipping_quote` tool, calls it, and checks the structured response. You will
also make the assertion fail, restore it, and open the saved run.

## Requirements

- Python 3.10 or newer.
- [uv](https://docs.astral.sh/uv/) and the standalone M3 CLI.
- No agent provider key, M3 account, or external server.

This walkthrough is a direct test: M3 calls the server itself, so it needs no
API key and no model provider is billed. Agent tests are a separate step. They
run your own agent CLI with your own key or token, and your model provider
bills that usage; see [test an agent's tool use](guides-agents-first-test.md).

If `uv --version` fails, install uv first. On macOS or Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal afterwards so `uv` is on your `PATH`. The
[uv installation page](https://docs.astral.sh/uv/getting-started/installation/)
lists Windows and package-manager installs.

Install the CLI once with uv:

```sh
uv tool install sf-m3-cli
```

On macOS or Linux, you can use the shell installer instead:

```sh
curl -LsSf https://m3.sineframe.com/install.sh | sh
```

The shell installer uses uv when it finds it. Without uv it runs
`python3 -m venv`, which a stock Ubuntu or Debian system does not include: the
install stops with `m3 install failed: could not create the dedicated virtual
environment`. Install uv as shown above and rerun, or install your
distribution's `python3-venv` package, which needs root.

Create a project, install the matching Python SDK, and check the environment:

```sh
mkdir shipping-test
cd shipping-test
m3 init --project-name shipping-test --suite shipping
m3 setup
m3 doctor
```

`m3 init` without options asks for the two names instead. Pass them as shown
when a script, CI job, or coding agent runs the command, because a
non-interactive `m3 init` stops with `--project-name is required when stdin is
not interactive`. The suite option is `--suite`.

`m3 init` creates `m3.toml`, `tests/test_m3_starter.py`, and `.env.example`.
When Node.js is available it also installs the `testing-with-m3` agent skill, which adds
`.agents/skills/testing-with-m3/`, a `.claude/skills/testing-with-m3` link, and
`skills-lock.json`. Add `--no-skill` to `m3 init` and `m3 setup` to leave those
out, for example in a repository you do not own. See
[install the M3 agent skill](https://m3.sineframe.com/docs/guides/agents/skill).

`m3 setup` installs the SDK and pytest support into the project's Python
environment. It does not install the CLI or edit your dependency manifest or
lockfile. The CLI and SDK are separate installations; keep them on matching
releases. See [install and update M3](start-install.md).

## Add the server and test

Create `shipping_server.py` beside the `tests` directory:

```python
"""A deterministic MCP shipping quote server used by the first-test guide."""

from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="shipping_quote",
                description="Calculate a deterministic shipping quote",
                input_schema={
                    "type": "object",
                    "properties": {
                        "weight_kg": {"type": "number", "exclusiveMinimum": 0},
                        "zone": {
                            "type": "string",
                            "enum": ["local", "regional", "international"],
                        },
                    },
                    "required": ["weight_kg", "zone"],
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    rates = {"local": 2.0, "regional": 3.5, "international": 7.0}
    amount = round(5 + float(arguments["weight_kg"]) * rates[str(arguments["zone"])], 2)
    quote = {"amount": amount, "currency": "USD"}
    return types.CallToolResult(
        content=[types.TextContent(text=f"{amount:.2f} USD")],
        structured_content=quote,
    )


async def main() -> None:
    server = Server(
        "shipping-server",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

Replace the generated skipped test at `tests/test_m3_starter.py` with this
complete file:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping")


def test_shipping_quote() -> None:
    """shipping_quote prices a 2 kg local parcel at 9.00 USD."""
    project_root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(project_root / "shipping_server.py"),),
        cwd=str(project_root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        tools = client.list_all_tools()
        assert [tool.name for tool in tools] == ["shipping_quote"]
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

The `StdioServer` command uses the same Python environment as pytest and points
at the server file relative to the test. The context managers close the MCP
connection and the subprocess after the assertion has its result.

## Run the test

From the project root, run:

```sh
m3 test -- tests/test_m3_starter.py
```

Pytest should report one passing test. The assertion checks both that discovery
returned the intended tool and that a 2 kg local quote has the structured value
`{"amount": 9.0, "currency": "USD"}`. A successful protocol call alone would
not prove that the server returned the right quote.

## See a real failure

Change the expected amount from `9.0` to `10.0` and rerun the same command. The
test should fail at the structured-content assertion and show the actual
`9.0` result. Restore `9.0` and rerun; the test should pass again.

## Open the saved run

From the project root, open the history created by the test:

```sh
m3 ui
```

M3 opens the saved-run list in a local browser. Select the run from the test
you completed. The CLI stores history in `.m3/executions.sqlite` by
default. See [inspect a saved run](guides-results-viewer.md) for navigating the
report.

## Test your own server

The demo server is a single file started with the project's Python. To test a
server you already have, the command in `StdioServer` must also start from the
environment `m3 setup` prepared, because `m3 test` runs pytest there.

- A Python server must be installed in that environment. `m3 setup` installs
  only the M3 SDK; it does not read your `pyproject.toml` or lockfile. From
  your server's project root, with the project `.venv` that `m3 setup` created:

  ```sh
  uv pip install --python .venv/bin/python -e .
  ```

- A server started with `npx` or as a compiled binary needs nothing installed
  in the Python environment. The command must be on `PATH`.

If the server process exits before it answers, the test fails with:

```text
m3.errors.TransportError: MCP transport operation failed: initialize. The stdio server process exited or never answered the MCP handshake. Run the server command by hand in the same environment to see its error. If the server is a Python package, install it into the environment that runs the tests, for example: uv pip install --python .venv/bin/python -e .
```

M3 does not show or store the server's own error output. Run the command
yourself to read it, for example `.venv/bin/python -m inventory_mcp` for a
module named `inventory_mcp`. `No module named ...` means the install step
above is missing.

Continue to [test your own server](start-your-server.md) for the complete
stdio and HTTP tests.
