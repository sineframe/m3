<!-- Generated from docs/site/guides/elicitation/responses.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Respond to elicitation requests

A user can accept, decline, or cancel an elicitation request. A plan can give
any of these answers, so you can test how your server handles each one.

## Requirements

Use Python 3.10 or later with `sf-m3[pytest]` installed in the project
environment. The test uses an in-process server and needs no agent harness,
network service, or credentials.

## Example

The `request_input` tool asks for a city through a form or sends the user to a
checkout URL, depending on its `mode` argument. On the retry it returns the
action and content it received, so the test can check exactly what M3 sent.

Save as `shipping_server.py`:

```python
from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

CITY_FORM = types.ElicitRequestFormParams(
    message="Enter your city.",
    requested_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)
CHECKOUT_URL = types.ElicitRequestURLParams(
    message="Continue checkout.",
    url="https://example.test/checkout/123",
)


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="request_input",
                description="Ask for a form or URL response and report the answer",
                input_schema={
                    "type": "object",
                    "properties": {"mode": {"enum": ["form", "url"]}},
                    "required": ["mode"],
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    if params.request_state is None:
        mode = (params.arguments or {})["mode"]
        request = CITY_FORM if mode == "form" else CHECKOUT_URL
        return types.InputRequiredResult(
            input_requests={"input": types.ElicitRequest(params=request)},
            request_state="awaiting-input",
        )

    # Report what M3 sent so the test can check it.
    response = (params.input_responses or {})["input"]
    return types.CallToolResult(
        content=[types.TextContent(text="Response received.")],
        structured_content={"action": response.action, "content": response.content},
    )


def build_server() -> Server:
    return Server(
        "elicitation-responses", on_list_tools=list_tools, on_call_tool=call_tool
    )
```

Save as `test_responses.py` beside it:

```python
from __future__ import annotations

import pytest
from shipping_server import build_server

from m3 import (
    Config,
    ElicitationPlan,
    InProcessServer,
    MCPTestKit,
    expect_form,
    expect_url,
)

CITY = expect_form("input")
CHECKOUT = expect_url("input")


@pytest.mark.parametrize(
    ("mode", "plan", "action", "content"),
    [
        ("form", CITY.accept({"city": "Pune"}), "accept", {"city": "Pune"}),
        ("form", CITY.decline(), "decline", None),
        ("form", CITY.cancel(), "cancel", None),
        ("url", CHECKOUT.accept(), "accept", None),
        ("url", CHECKOUT.decline(), "decline", None),
        ("url", CHECKOUT.cancel(), "cancel", None),
    ],
)
def test_server_receives_the_planned_response(
    mode: str, plan: ElicitationPlan, action: str, content: object
) -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    config = Config(protocol_revision="2026-07-28")
    with MCPTestKit(config=config, env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("request_input", {"mode": mode}, elicitation=plan)

    assert result.is_error is False
    assert result.structured_content == {"action": action, "content": content}
```

Run:

```sh
python -m pytest -q test_responses.py
```

Captured output:

```text
6 passed
```

Only an accepted form carries content. Declining or cancelling sends the action
alone. Accepting a URL request also sends no content, because the user completes
that step on the web page; M3 does not visit the URL.

`cancel` is the MCP elicitation action for a user who dismissed the request.
The server decides what happens next. This one still returns a normal result,
so the tool call succeeds. It has nothing to do with `handle.cancel()`, which
stops an agent execution or session.

To attach metadata to a response, build the plan with
`ElicitationPlan.model_validate(...)` and an `ElicitationResponse` that sets
`meta`. M3 sends it to the server as `_meta`. The
[elicitation reference](reference-python-m3-elicitation.md) covers this
and the rest of the plan API.

The complete project is in
[`sdk/examples/docs/elicitation-responses`](../examples/elicitation-responses).
When application code needs to see the request and answer it, continue to
[Handle elicitation directly with the SDK](guides-elicitation-manual.md).
