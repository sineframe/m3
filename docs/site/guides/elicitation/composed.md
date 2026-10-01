---
title: "Compose elicitation workflows"
description: "Combine address alternatives, optional requests, same-round forms, and a later URL request in direct SDK tests."
---

# Compose elicitation workflows

Use plan combinators when one operation can ask for one of several forms,
skip an optional form, request multiple forms together, and then ask a URL
question in a later round. The test below checks the server's observed retry
attempts and keyed responses.

## Requirements

Use Python 3.10 or newer. From the repository root, install the candidate
package and pytest with `python -m pip install -e 'sdk[pytest]'`. An index
install of `sf-m3[pytest]` selects a published release. These tests use
protocol revision `2026-07-28` and a local in-process server. They need no
agent harness, network service, or credentials.

## Complete server and tests

Save as `shipping_server.py`:

```python
from __future__ import annotations

from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}
ADDRESSES = {
    "home_address": {"street": "1 Home Street", "city": "Pune"},
    "business_address": {"street": "2 Business Street", "city": "Pune"},
}


def build_server(calls: list[dict[str, object]]) -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="book_verified_shipment",
                    description="Collect addresses and complete a verification request",
                    input_schema={
                        "type": "object",
                        "properties": {"address_kind": {"type": "string"}},
                        "required": ["address_kind"],
                    },
                )
            ]
        )

    async def call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        state = params.request_state
        responses = params.input_responses or {}
        kind = (params.arguments or {}).get("address_kind")
        calls.append(
            {
                "request_state": state,
                "response_keys": tuple(sorted(responses)),
                "responses": {
                    key: response.model_dump(mode="json", by_alias=True)
                    for key, response in responses.items()
                },
            }
        )
        if state is None:
            if kind == "both":
                keys = ("home_address", "business_address")
            elif kind in {"home", "business"}:
                keys = (f"{kind}_address",)
            elif kind == "none":
                return types.InputRequiredResult(
                    input_requests={
                        "verification": types.ElicitRequest(
                            params=types.ElicitRequestURLParams(
                                message="Complete shipment verification.",
                                url="https://example.test/verify/123",
                            )
                        )
                    },
                    request_state="verification",
                )
            else:
                return types.CallToolResult(content=[], is_error=True)
            if keys:
                return types.InputRequiredResult(
                    input_requests={
                        key: types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message=f"Enter the {key.removesuffix('_address')} address.",
                                requested_schema=ADDRESS_SCHEMA,
                            )
                        )
                        for key in keys
                    },
                    request_state="addresses",
                )
        if state == "addresses":
            expected = (
                ("home_address", "business_address")
                if kind == "both"
                else (f"{kind}_address",)
            )
            if set(responses) != set(expected) or any(
                not isinstance(responses[key], types.ElicitResult)
                or responses[key].action != "accept"
                or responses[key].content != ADDRESSES[key]
                for key in expected
            ):
                return types.CallToolResult(content=[], is_error=True)
            return types.InputRequiredResult(
                input_requests={
                    "verification": types.ElicitRequest(
                        params=types.ElicitRequestURLParams(
                            message="Complete shipment verification.",
                            url="https://example.test/verify/123",
                        )
                    )
                },
                request_state="verification",
            )
        if state == "verification" and set(responses) == {"verification"}:
            response = responses["verification"]
            if isinstance(response, types.ElicitResult) and response.action == "accept":
                return types.CallToolResult(
                    content=[types.TextContent(text="Shipment verified.")],
                    structured_content={"status": "booked", "address_kind": kind},
                )
        return types.CallToolResult(content=[], is_error=True)

    return Server("shipping-composed", on_list_tools=list_tools, on_call_tool=call_tool)
```

Save as `test_composed.py` beside it:

```python
from __future__ import annotations

from typing import Any

from shipping_server import ADDRESS_SCHEMA, ADDRESSES, build_server

from m3 import (
    Config,
    InProcessServer,
    MCPTestKit,
    expect_form,
    expect_url,
    one_of,
    optional,
    round_of,
    sequence,
)


def form(server: InProcessServer, key: str):
    return expect_form(
        key,
        message=f"Enter the {key.removesuffix('_address')} address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept(ADDRESSES[key])


def verification(server: InProcessServer):
    return expect_url(
        "verification",
        message="Complete shipment verification.",
        url="https://example.test/verify/123",
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept()


def run(kind: str, plan_factory: Any):
    calls: list[dict[str, object]] = []
    server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "book_verified_shipment",
                {"address_kind": kind},
                elicitation=plan_factory(server),
            )
    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "address_kind": kind}
    return calls


def test_address_alternative_then_url_uses_later_round() -> None:
    calls = run(
        "business",
        lambda server: sequence(
            one_of(form(server, "home_address"), form(server, "business_address")),
            verification(server),
        ),
    )
    assert [call["response_keys"] for call in calls] == [
        (),
        ("business_address",),
        ("verification",),
    ]
    assert [call["request_state"] for call in calls] == [
        None,
        "addresses",
        "verification",
    ]
    assert (
        calls[1]["responses"]["business_address"]["content"]
        == ADDRESSES["business_address"]
    )


def test_optional_address_can_be_skipped_before_url() -> None:
    calls = run(
        "none",
        lambda server: sequence(
            optional(
                one_of(form(server, "home_address"), form(server, "business_address"))
            ),
            verification(server),
        ),
    )
    assert [call["response_keys"] for call in calls] == [(), ("verification",)]
    assert [call["request_state"] for call in calls] == [None, "verification"]


def test_two_forms_are_expected_in_the_same_round() -> None:
    calls = run(
        "both",
        lambda server: sequence(
            round_of(form(server, "home_address"), form(server, "business_address")),
            verification(server),
        ),
    )
    assert [set(call["response_keys"]) for call in calls] == [
        set(),
        {"home_address", "business_address"},
        {"verification"},
    ]
    assert calls[1]["responses"]["home_address"]["content"] == ADDRESSES["home_address"]
    assert (
        calls[1]["responses"]["business_address"]["content"]
        == ADDRESSES["business_address"]
    )
```

From the directory containing those files, run:

```sh
python -m pytest -q test_composed.py
```

Captured output:

```text
3 passed
```

The first case sends the business address in its own round, then sends the
URL acceptance in the next request. Its three recorded attempts are initial
request, address retry, and verification retry; each retry includes only its
current response key and carries the server's current `request_state`. The
optional case skips both address branches and sends only verification. The
same-round case sends both address keys together, followed by verification.
An accepted URL response does not visit or complete the URL.

The source project is available at
[`sdk/examples/docs/elicitation-composed`](../../../../sdk/examples/docs/elicitation-composed).
For an individual form plan, start with [Plan answers to elicitation
requests](plans.md). The [API reference](../../reference/python/m3/elicitation.md)
documents matching and ambiguity errors. Agent-driven versions are in
[Handle elicitation in agent tests](agents.md).
