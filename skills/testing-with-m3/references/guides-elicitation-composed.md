<!-- Generated from docs/site/guides/elicitation/composed.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Compose elicitation workflows

Some tools ask for input more than once, or ask for different things depending
on their arguments. Combine single-request plans with `sequence`, `one_of`,
`optional`, and `round_of` to describe those flows.

## Requirements

Use Python 3.10 or later with `sf-m3[pytest]` installed in the project
environment. The tests use an in-process server and need no agent harness,
network service, or credentials.

## Example

`book_verified_shipment` takes an `address_kind` of `home`, `business`, `both`,
or `none`. It first asks for the matching address forms, all in one round, and
skips that round for `none`. It then sends a URL request for verification and
books the shipment once every request has been accepted.

Save as `shipping_server.py`:

```python
from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}
# Which address forms the server asks for in round 1, by `address_kind`.
ADDRESS_FORMS = {
    "home": ["home_address"],
    "business": ["business_address"],
    "both": ["home_address", "business_address"],
    "none": [],
}
VERIFICATION = types.ElicitRequest(
    params=types.ElicitRequestURLParams(
        message="Complete shipment verification.",
        url="https://example.test/verify/123",
    )
)


def address_form(key: str) -> types.ElicitRequest:
    return types.ElicitRequest(
        params=types.ElicitRequestFormParams(
            message=f"Enter the {key.replace('_', ' ')}.",
            requested_schema=ADDRESS_SCHEMA,
        )
    )


def accepted(response: object) -> bool:
    return isinstance(response, types.ElicitResult) and response.action == "accept"


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_verified_shipment",
                description="Collect addresses, then ask for verification",
                input_schema={
                    "type": "object",
                    "properties": {"address_kind": {"enum": list(ADDRESS_FORMS)}},
                    "required": ["address_kind"],
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    kind = (params.arguments or {})["address_kind"]
    forms = ADDRESS_FORMS[kind]
    responses = params.input_responses or {}
    state = params.request_state

    # First round: ask for every address form at once.
    if state is None and forms:
        return types.InputRequiredResult(
            input_requests={key: address_form(key) for key in forms},
            request_state="addresses",
        )
    # Next round: ask for verification once each address form is accepted.
    if state is None or (
        state == "addresses" and all(accepted(responses.get(k)) for k in forms)
    ):
        return types.InputRequiredResult(
            input_requests={"verification": VERIFICATION},
            request_state="verification",
        )
    if state == "verification" and accepted(responses.get("verification")):
        return types.CallToolResult(
            content=[types.TextContent(text="Shipment verified.")],
            structured_content={"status": "booked", "address_kind": kind},
        )
    return types.CallToolResult(
        content=[types.TextContent(text="unexpected response")], is_error=True
    )


def build_server() -> Server:
    return Server("shipping-composed", on_list_tools=list_tools, on_call_tool=call_tool)
```

Save as `test_composed.py` beside it:

```python
from __future__ import annotations

from shipping_server import build_server

from m3 import (
    Config,
    ElicitationPlan,
    InProcessServer,
    MCPTestKit,
    expect_form,
    expect_url,
    one_of,
    optional,
    round_of,
    sequence,
)

HOME = expect_form("home_address").accept({"street": "1 Home St", "city": "Pune"})
BUSINESS = expect_form("business_address").accept(
    {"street": "2 Business St", "city": "Pune"}
)
VERIFY = expect_url("verification").accept()


def book(address_kind: str, plan: ElicitationPlan) -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    config = Config(protocol_revision="2026-07-28")
    with MCPTestKit(config=config, env={}) as kit, kit.direct(server) as client:
        result = client.call_tool(
            "book_verified_shipment", {"address_kind": address_kind}, elicitation=plan
        )
    assert result.is_error is False
    assert result.structured_content == {
        "status": "booked",
        "address_kind": address_kind,
    }


def test_one_of_answers_whichever_address_is_requested() -> None:
    book("business", sequence(one_of(HOME, BUSINESS), VERIFY))


def test_optional_step_can_be_skipped() -> None:
    book("none", sequence(optional(one_of(HOME, BUSINESS)), VERIFY))


def test_round_of_answers_two_requests_in_one_round() -> None:
    book("both", sequence(round_of(HOME, BUSINESS), VERIFY))
```

Run:

```sh
python -m pytest -q test_composed.py
```

Captured output:

```text
3 passed
```

All three tests build their plans from the same leaves, `HOME`, `BUSINESS`, and
`VERIFY`. Plans are immutable, so one leaf can appear in several plans.

- `sequence(a, b)` answers `a` in one round and `b` in a later round. Each test
  uses it to put verification after the addresses.
- `one_of(HOME, BUSINESS)` expects one of the two requests and answers
  whichever arrives. The server asks only for the business address here.
- `optional(...)` lets a step be skipped. With `none`, the server goes straight
  to verification and the plan still completes.
- `round_of(HOME, BUSINESS)` expects both requests in the same round and
  answers them together.

When the server's rounds don't fit the plan, the call raises
`ElicitationExpectationError`. For example, `sequence(HOME, BUSINESS, VERIFY)`
fails against `both`, because the server sends both address forms in the same
round.

To make a single request optional, use `maybe_form` or `maybe_url`. The
[elicitation reference](reference-python-m3-elicitation.md) describes
these and the errors raised when a round could match more than one path.

The complete project is in
[`sdk/examples/docs/elicitation-composed`](../examples/elicitation-composed).
To use plans in agent turns, see [Handle elicitation in agent tests](guides-elicitation-agents.md).
