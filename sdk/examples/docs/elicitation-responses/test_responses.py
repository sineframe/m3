from __future__ import annotations

from m3 import (
    Config,
    ElicitationPlan,
    ElicitationResponse,
    InProcessServer,
    MCPTestKit,
    expect_form,
    expect_url,
)

from shipping_server import ADDRESS_SCHEMA, build_server


def test_form_acceptance_and_decline_have_distinct_content() -> None:
    for action in ("accept", "decline"):
        calls: list[dict[str, object]] = []
        server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
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
        server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
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
