---
title: "Respond to elicitation requests"
description: "Test accepted, declined, and cancelled form and URL responses on direct MCP operations."
---

# Respond to elicitation requests

The local server records the response M3 sends when it retries a form or URL
request. The tests cover accepted, declined, and cancelled responses, along
with response metadata.

## Requirements

Works with Python 3.10 or newer and MCP protocol revision `2026-07-28` through
direct SDK operations. The tests use an in-process MCP server and need no
credentials or network service. Here, `cancel` is an MCP elicitation response;
`handle.cancel()` cancels an execution or session.

From the repository root, install the candidate package and pytest with
`python -m pip install -e 'sdk[pytest]'`. Installing `sf-m3[pytest]` from the
package index selects a published release.

## Complete server and tests

Save as `shipping_server.py`:

```python
from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}


def build_server(calls: list[dict[str, object]]) -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="request_input",
                    description="Request one form or URL response",
                    input_schema={
                        "type": "object",
                        "properties": {"mode": {"type": "string"}},
                        "required": ["mode"],
                    },
                )
            ]
        )

    async def call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        state = params.request_state
        responses = params.input_responses or {}
        mode = (params.arguments or {}).get("mode")
        calls.append(
            {
                "state": state,
                "responses": {
                    key: item.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for key, item in responses.items()
                },
            }
        )
        if state is None:
            if mode == "form":
                request = types.ElicitRequest(
                    params=types.ElicitRequestFormParams(
                        message="Enter your city.", requested_schema=ADDRESS_SCHEMA
                    )
                )
            elif mode == "url":
                request = types.ElicitRequest(
                    params=types.ElicitRequestURLParams(
                        message="Continue checkout.",
                        url="https://example.test/checkout/123",
                    )
                )
            else:
                return types.CallToolResult(content=[], is_error=True)
            return types.InputRequiredResult(
                input_requests={"input": request}, request_state=f"{mode}-state"
            )
        response = responses.get("input")
        return types.CallToolResult(
            content=[types.TextContent(text="Response received.")],
            structured_content={
                "action": response.action,
                "content": response.content,
                "meta": response.meta,
            },
        )

    return Server(
        "elicitation-responses", on_list_tools=list_tools, on_call_tool=call_tool
    )
```

Save as `test_responses.py` beside it:

```python
from __future__ import annotations

from shipping_server import ADDRESS_SCHEMA, build_server

from m3 import (
    Config,
    ElicitationPlan,
    ElicitationResponse,
    InProcessServer,
    MCPTestKit,
    expect_form,
    expect_url,
)


def test_form_acceptance_and_decline_have_distinct_content() -> None:
    for action in ("accept", "decline"):
        calls: list[dict[str, object]] = []
        server = InProcessServer(
            name="shipping", factory=lambda calls=calls: build_server(calls)
        )
        leaf = expect_form(
            "input",
            message="Enter your city.",
            schema=ADDRESS_SCHEMA,
            server=server,
            operation_kind="tool",
            operation_name="request_input",
        )
        plan = leaf.accept({"city": "Pune"}) if action == "accept" else leaf.decline()
        with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
            with kit.direct(server) as client:
                result = client.call_tool(
                    "request_input", {"mode": "form"}, elicitation=plan
                )
        wire_response = calls[1]["responses"]["input"]
        assert wire_response["action"] == action
        assert ("content" in wire_response) is (action == "accept")
        assert result.structured_content["action"] == action


def test_form_cancellation_is_an_elicitation_response() -> None:
    calls: list[dict[str, object]] = []
    server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
    plan = expect_form(
        "input", server=server, operation_kind="tool", operation_name="request_input"
    ).cancel()
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "request_input", {"mode": "form"}, elicitation=plan
            )
    assert calls[1]["responses"]["input"] == {"action": "cancel"}
    assert result.structured_content["action"] == "cancel"


def test_url_decline_and_cancel_never_send_form_content() -> None:
    for action in ("decline", "cancel"):
        calls: list[dict[str, object]] = []
        server = InProcessServer(
            name="shipping", factory=lambda calls=calls: build_server(calls)
        )
        leaf = expect_url(
            "input",
            message="Continue checkout.",
            url="https://example.test/checkout/123",
            server=server,
            operation_kind="tool",
            operation_name="request_input",
        )
        plan = leaf.decline() if action == "decline" else leaf.cancel()
        with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
            with kit.direct(server) as client:
                result = client.call_tool(
                    "request_input", {"mode": "url"}, elicitation=plan
                )
        assert calls[1]["responses"]["input"] == {"action": action}
        assert result.structured_content["action"] == action


def test_response_meta_is_preserved_without_changing_action_content() -> None:
    calls: list[dict[str, object]] = []
    server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
    response = ElicitationResponse(action="accept", meta={"source": "test"})
    plan = ElicitationPlan.model_validate(
        {
            "node": "leaf",
            "request": {
                "mode": "url",
                "request_key": "input",
                "server": "shipping",
                "operation_kind": "tool",
                "operation_name": "request_input",
            },
            "response": response,
        }
    )
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            client.call_tool("request_input", {"mode": "url"}, elicitation=plan)
    assert calls[1]["responses"]["input"] == {
        "action": "accept",
        "_meta": {"source": "test"},
    }
```

The complete runnable source is in
[`sdk/examples/docs/elicitation-responses`](../../../../sdk/examples/docs/elicitation-responses).
From the directory containing both files, run:

```sh
python -m pytest -q test_responses.py
```

Captured output:

```text
4 passed
```

Form acceptance includes content; form decline and cancellation do not. URL
decline and cancellation also omit form content, while response metadata is
preserved under `_meta`. The URL tests send an MCP action without visiting the
URL. After receiving `cancel`, the server still returns its normal tool result,
so the enclosing execution continues.

For schema mismatch behavior and plan composition, see
[Compose elicitation workflows](composed.md) and the
[elicitation reference](../../reference/python/m3/elicitation.md). For a
server that returns an `InputRequiredResult` to application code, continue to
[Handle elicitation manually](manual.md).
