# Elicitation

An MCP operation can pause for input, resume with your response, and pause again. In M3, an **elicitation plan** says which requests may appear and what to answer. Attach the completed plan to the action that can elicit; assert the action's final result afterward. The [API reference](elicitation-api.md) covers every helper, parameter, result model, and manual path.

## One prompt, two elicitation rounds, one tool call

This is the test shape to start from. The server asks for either a home or a business address, then asks for URL verification on a later retry. The prompt, both responses, and the final tool result belong to one `agent.run` call.

```text
agent.run(prompt, elicitation=plan)
  book_verified_shipment attempt 1 -> home_address OR business_address
  M3 answers the selected form
  book_verified_shipment attempt 2 -> verification URL
  M3 answers the URL request
  book_verified_shipment attempt 3 -> booked
expect(result).to_have_tool_call(..., count=1)
```

The following is a complete pytest test. It uses the maintained [MCP server fixture](../examples/servers/modern_mrtr_server.py) and [local Pi provider](../examples/tests/pi_provider.py), so it needs Pi 0.85.1 but no external model credentials. Save it under `sdk/examples/tests/` if running in this repository.

```python
import sys
from pathlib import Path

import pytest

from examples.servers.modern_mrtr_server import ADDRESS_SCHEMA
from m3 import expect, expect_form, expect_url, one_of, sequence
from m3.types import ExecutionOutcome, StdioServer

pytest_plugins = ("examples.tests.pi_conftest",)
EXAMPLES_ROOT = Path(__file__).parents[1]
HOME = {"street": "1 Home Street", "city": "Pune", "postal_code": "411001"}
BUSINESS = {"street": "2 Business Street", "city": "Pune", "postal_code": "411002"}


@pytest.fixture
def example_server(pi_fixture):
    return StdioServer(
        name="modern-mrtr-example",
        command=sys.executable,
        args=(str(EXAMPLES_ROOT / "servers" / "modern_mrtr_server.py"),),
        cwd=str(EXAMPLES_ROOT),
        environment={"M3_MRTR_WIRE_MARKER": str(pi_fixture)},
    )


def address(server, kind):
    return expect_form(
        f"{kind}_address",
        message=f"Enter the {kind} delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept(HOME if kind == "home" else BUSINESS)


def verification(server):
    return expect_url(
        "verification",
        message="Complete shipment verification.",
        url="https://example.test/verify/123",
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept()


@pytest.mark.parametrize("kind", ["home", "business"])
@pytest.mark.m3(agents=[{"harness": "pi", "models": ["fixture-model"]}])
def test_address_choice_then_url(agent, example_server, kind):
    plan = sequence(
        one_of(address(example_server, "home"), address(example_server, "business")),
        verification(example_server),
    )

    result = agent.run(
        f"Use m3-gate:book_verified_shipment for a {kind} shipment, "
        "then report whether it was booked.",
        server=example_server,
        elicitation=plan,
        timeout=120,
    )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "book_verified_shipment",
        server="modern-mrtr-example",
        status="success",
        count=1,
    )
    assert [entry.request_key for entry in result.trace_view.elicitations] == [
        f"{kind}_address", "verification"
    ]
```

The maintained [composed test](../examples/tests/test_modern_mrtr_pi_composed.py) runs both branches and also checks retry state and keyed responses. Run it from the repository root:

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests/test_modern_mrtr_pi_composed.py
```

`one_of` selects the request that actually arrives in the first round. `sequence` means the URL must arrive in the **next** round. `count=1` checks one logical tool call; the protocol makes three wire attempts as that call resumes. The tool assertion is written after `agent.run` because the call completes there. The maintained test's attempt and elicitation assertions check the order within that call. There is no tool-call node inside the elicitation plan.

## Change the composition, keep the action and assertion

The same [real test module](../examples/tests/test_modern_mrtr_pi_composed.py) runs these neighboring cases against the same server and Pi provider:

| Server behavior | Plan for the same `agent.run` action | Cases run |
|---|---|---|
| One of two address forms, then URL | `sequence(one_of(home, business), url)` | Home; business |
| Address may be absent, then URL | `sequence(optional(one_of(home, business)), url)` | No address; home; business |
| Both address forms in one result, then URL | `sequence(round_of(home, business), url)` | Both addresses |

For the optional case, replace only the plan in the test above and prompt for a shipment "without an address" to exercise the skipped branch:

```python
from m3 import optional

plan = sequence(
    optional(one_of(address(example_server, "home"), address(example_server, "business"))),
    verification(example_server),
)
```

For two keyed requests in **one** `InputRequiredResult`, use `round_of` instead of `one_of`. Prompt for "both addresses":

```python
from m3 import round_of

plan = sequence(
    round_of(address(example_server, "home"), address(example_server, "business")),
    verification(example_server),
)
```

In each variant, pass `elicitation=plan` to the same action and assert `expect(result).to_have_tool_call(..., count=1)`. The real tests also inspect `result.trace_view.elicitations` and the server's wire marker: the optional case has two or three attempts, while the `round_of` case sends both address responses on the same retry.

## Pick the action that owns the plan

- **Direct operation:** Attach `elicitation=plan` to `client.call_tool`, `client.get_prompt`, or `client.read_resource`. Start with the runnable [direct booking test](../examples/tests/test_modern_mrtr_direct.py); [prompt and resource tests](../tests/integration/test_direct_client_mrtr.py) cover those operation kinds in sync and async clients.
- **One agent prompt:** Attach it to `agent.run` as above. The [qualified](../examples/tests/test_modern_mrtr_pi_qualified.py) and [unqualified](../examples/tests/test_modern_mrtr_pi_unqualified.py) tests show known-operation and model-choice cases.
- **A later conversation turn:** Attach it only to the `session.send` that can elicit. The [session test](../examples/tests/test_modern_mrtr_pi_session.py) sends a normal quote request first, then books on the next turn.

Build each leaf with an expected request key and bind its response with `.accept(...)`, `.decline()`, or `.cancel()` before composing it. Match a known server and operation when possible. For the full signatures, manual input, managed submission, URL details, and trace fields, use the [MRTR API reference](elicitation-api.md).
