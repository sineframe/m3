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

pytestmark = pytest.mark.m3(suite_name="elicitation")

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
