from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import types

from m3.elicitation import expect_form, expect_url, round_of, sequence
from m3.harness.pi_extension.bridge import (
    ActionContextChannel,
    BridgeActionStatus,
    BridgeProtocolError,
    MCPBridge,
    _handle_request,
    serve,
)


def _form(
    key: str = "address",
    property_name: str = "city",
    request_state: str = "opaque-state",
) -> types.InputRequiredResult:
    return types.InputRequiredResult(
        inputRequests={
            key: types.ElicitRequest(
                params=types.ElicitRequestFormParams(
                    message="Address",
                    requestedSchema={
                        "type": "object",
                        "properties": {property_name: {"type": "string"}},
                        "required": [property_name],
                    },
                )
            )
        },
        requestState=request_state,
    )


def _url(request_state: str = "url-state") -> types.InputRequiredResult:
    return types.InputRequiredResult(
        inputRequests={
            "checkout": types.ElicitRequest(
                params=types.ElicitRequestURLParams(
                    message="Continue checkout.",
                    url="https://example.test/checkout/123",
                    elicitation_id="checkout-123",
                )
            )
        },
        requestState=request_state,
    )


class _Session:
    server_info = type("Info", (), {"name": "example-mcp"})()

    def __init__(self, values: list[Any]) -> None:
        self.values = list(values)
        self.calls: list[dict[str, Any]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append({"name": name, "arguments": arguments, **kwargs})
        return self.values.pop(0)


class _RetryFailureSession(_Session):
    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append({"name": name, "arguments": arguments, **kwargs})
        if len(self.calls) > 1:
            raise RuntimeError("retry failed")
        return self.values.pop(0)


class _BlockingSession(_Session):
    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append({"name": name, "arguments": arguments, **kwargs})
        await asyncio.sleep(60)
        raise AssertionError("unreachable")


class _InteractiveSession(_Session):
    def __init__(self, values: list[Any], response: Any) -> None:
        super().__init__(values)
        self.response = response

    async def dispatch_input_request(self, _context: Any, _request: Any) -> Any:
        return self.response


class _SlowCallSession(_Session):
    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append({"name": name, "arguments": arguments, **kwargs})
        await asyncio.sleep(0.1)
        return types.CallToolResult(content=[])


class _ConcurrentElicitingSession(_Session):
    def __init__(self) -> None:
        super().__init__([])
        self.first_dispatch_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.first_dispatch_cancelled = asyncio.Event()

    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append({"name": name, "arguments": arguments, **kwargs})
        if len(self.calls) == 1:
            sampling = types.CreateMessageRequest(
                params=types.CreateMessageRequestParams(messages=[], maxTokens=16)
            )
            return types.InputRequiredResult(
                inputRequests={
                    "address": _form().input_requests["address"],
                    "sample": sampling,
                },
                requestState="first",
            )
        if len(self.calls) == 2:
            return _form("second")
        return types.CallToolResult(content=[])

    async def dispatch_input_request(self, _context: Any, request: Any) -> Any:
        if isinstance(request, types.CreateMessageRequest):
            self.first_dispatch_started.set()
            try:
                await self.release_first.wait()
            except asyncio.CancelledError:
                self.first_dispatch_cancelled.set()
                raise
            return types.CreateMessageResult(
                role="assistant",
                content=types.TextContent(text="sampled"),
                model="fixture",
            )
        raise AssertionError("unexpected input request")


def _channel(tmp_path: Path) -> ActionContextChannel:
    return ActionContextChannel(tmp_path / "context.json", tmp_path / "status.json")


def test_channel_round_trips_generation_and_plan(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    plan = expect_form("address").accept({"city": "Pune"})
    channel.write_context(generation="g1", turn_sequence=2, plan=plan, round_limit=10)
    context = channel.read_context()
    assert context is not None
    assert context.generation == "g1"
    assert context.turn_sequence == 2
    assert context.plan is not None and context.plan.is_complete
    channel.write_status(BridgeActionStatus("g1", 2, "idle"))
    assert channel.read_status() is not None


def test_bridge_retries_form_with_current_state_and_unchanged_args(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=expect_form("address").accept({"city": "Pune"}),
        round_limit=10,
    )
    session = _Session(
        [_form(), types.CallToolResult(content=[], structuredContent={"ok": True})]
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )
    result = asyncio.run(
        bridge.call_tool("example-mcp", "book", {"weight": 2}, generation="g1")
    )
    assert result["structuredContent"] == {"ok": True}
    assert len(session.calls) == 2
    assert session.calls[1]["arguments"] == {"weight": 2}
    assert session.calls[1]["request_state"] == "opaque-state"
    assert set(session.calls[1]["input_responses"]) == {"address"}
    assert channel.read_status().state == "in_progress"  # type: ignore[union-attr]
    bridge.finalize_action("g1")
    assert channel.read_status().state == "completed"  # type: ignore[union-attr]


def test_managed_bridge_pauses_and_continues_with_parent_keyed_response(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=3,
        plan=None,
        round_limit=10,
        execution_id="execution-1",
        session_id="harness-1",
        turn_id="turn-1",
    )
    session = _Session(
        [_form(request_state="opaque-state"), types.CallToolResult(content=[])]
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )
    first = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-1",
                "method": "call_tool",
                "server": "example-mcp",
                "tool": "book",
                "arguments": {"weight": 2},
                "generation": "g1",
            },
        )
    )
    assert first["ok"] is True
    marker = first["result"]
    pending = marker["__m3_pending__"]
    assert pending["execution_id"] == "execution-1"
    assert pending["round_index"] == 0
    assert pending["operation_parameters"] == {
        "weight": 2,
        "session_id": "harness-1",
        "turn_id": "turn-1",
    }
    second = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-2",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": marker["continuation_id"],
                "responses": {
                    "address": {"action": "accept", "content": {"city": "Pune"}}
                },
            },
        )
    )
    assert second["ok"] is True
    assert len(session.calls) == 2
    assert session.calls[1]["arguments"] == {"weight": 2}
    assert session.calls[1]["request_state"] == "opaque-state"
    assert set(session.calls[1]["input_responses"]) == {"address"}


def test_managed_round_indices_track_elicitation_rounds_per_operation(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=3,
        plan=None,
        round_limit=10,
        execution_id="execution-1",
    )
    sampling = types.CreateMessageRequest(
        params=types.CreateMessageRequestParams(messages=[], maxTokens=16)
    )
    session = _InteractiveSession(
        [
            types.InputRequiredResult(
                inputRequests={"sample": sampling}, requestState="sampling-state"
            ),
            _form(request_state="managed-state"),
            types.InputRequiredResult(
                inputRequests={"sample": sampling}, requestState="sampling-state-2"
            ),
            _form("contact", property_name="phone", request_state="managed-state-2"),
            types.CallToolResult(content=[], structuredContent={"ok": True}),
            types.InputRequiredResult(
                inputRequests={"sample": sampling}, requestState="later-sampling-state"
            ),
            _form("later", request_state="later-managed-state"),
            types.CallToolResult(content=[], structuredContent={"later": True}),
        ],
        types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(text="sampled"),
            model="fixture",
        ),
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )

    result = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-1",
                "method": "call_tool",
                "server": "example-mcp",
                "tool": "book",
                "arguments": {},
                "generation": "g1",
            },
        )
    )

    assert result["ok"] is True
    first = result["result"]
    assert first["__m3_pending__"]["round_index"] == 0

    second = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-2",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": first["continuation_id"],
                "responses": {
                    "address": {"action": "accept", "content": {"city": "Pune"}}
                },
            },
        )
    )
    second_pending = second["result"]
    assert second_pending["__m3_pending__"]["round_index"] == 1

    completed = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-3",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": second_pending["continuation_id"],
                "responses": {
                    "contact": {
                        "action": "accept",
                        "content": {"phone": "+91-555-0100"},
                    }
                },
            },
        )
    )
    assert completed["result"]["structuredContent"] == {"ok": True}
    assert channel.read_status().rounds == 4  # type: ignore[union-attr]

    later = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-4",
                "method": "call_tool",
                "server": "example-mcp",
                "tool": "book",
                "arguments": {},
                "generation": "g1",
            },
        )
    )
    later_pending = later["result"]
    assert later_pending["__m3_pending__"]["round_index"] == 0

    later_completed = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-5",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": later_pending["continuation_id"],
                "responses": {
                    "later": {
                        "action": "accept",
                        "content": {"city": "Pune"},
                    }
                },
            },
        )
    )
    assert later_completed["result"]["structuredContent"] == {"later": True}
    assert channel.read_status().rounds == 6  # type: ignore[union-attr]
    assert len(session.calls) == 8


def test_managed_round_limit_includes_rounds_before_continuation(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=3,
        plan=None,
        round_limit=2,
        execution_id="execution-1",
    )
    sampling = types.CreateMessageRequest(
        params=types.CreateMessageRequestParams(messages=[], maxTokens=16)
    )
    session = _InteractiveSession(
        [
            types.InputRequiredResult(
                inputRequests={"sample": sampling}, requestState="sampling-state"
            ),
            _form(request_state="managed-state"),
            _form("contact", property_name="phone", request_state="managed-state-2"),
        ],
        types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(text="sampled"),
            model="fixture",
        ),
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )

    first = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-1",
                "method": "call_tool",
                "server": "example-mcp",
                "tool": "book",
                "arguments": {},
                "generation": "g1",
            },
        )
    )
    assert first["ok"] is True
    assert first["result"]["__m3_pending__"]["round_index"] == 0

    second = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-2",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": first["result"]["continuation_id"],
                "responses": {
                    "address": {"action": "accept", "content": {"city": "Pune"}}
                },
            },
        )
    )

    assert second["ok"] is False
    assert second["error_code"] == "round_limit"
    assert channel.read_status().state == "failed"  # type: ignore[union-attr]
    assert channel.read_status().rounds == 3  # type: ignore[union-attr]


def test_managed_bridge_preserves_url_request_identity(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=None,
        round_limit=10,
        execution_id="execution-1",
    )
    bridge = MCPBridge(
        {"example-mcp": {"checkout": {}}},
        {"example-mcp": _Session([_url()])},
        channel=channel,
    )
    response = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-1",
                "method": "call_tool",
                "server": "example-mcp",
                "tool": "checkout",
                "arguments": {},
                "generation": "g1",
            },
        )
    )
    request = response["result"]["__m3_pending__"]["requests"]["checkout"]
    assert request["mode"] == "url"
    assert request["url"] == "https://example.test/checkout/123"
    assert request["elicitation_id"] == "checkout-123"


def test_managed_bridge_delivers_two_rounds_with_current_keyed_responses(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=3,
        plan=None,
        round_limit=10,
        execution_id="execution-1",
        session_id="harness-1",
        turn_id="turn-1",
    )
    session = _Session(
        [
            _form("address", request_state="state-1"),
            _form("contact", property_name="phone", request_state="state-2"),
            types.CallToolResult(content=[], structuredContent={"ok": True}),
        ]
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )
    first = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-1",
                "method": "call_tool",
                "server": "example-mcp",
                "tool": "book",
                "arguments": {"weight": 2},
                "generation": "g1",
            },
        )
    )
    first_marker = first["result"]["__m3_pending__"]
    second = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-2",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": first["result"]["continuation_id"],
                "responses": {
                    "address": {"action": "accept", "content": {"city": "Pune"}}
                },
            },
        )
    )
    second_marker = second["result"]["__m3_pending__"]
    assert second_marker["round_index"] == 1
    assert set(second_marker["requests"]) == {"contact"}
    assert second_marker["request_state"] == "state-2"
    third = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-3",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": second["result"]["continuation_id"],
                "responses": {
                    "contact": {
                        "action": "accept",
                        "content": {"phone": "+91-555-0100"},
                    }
                },
            },
        )
    )
    assert third["result"]["structuredContent"] == {"ok": True}
    assert len(session.calls) == 3
    assert (
        session.calls[0]["arguments"]
        == session.calls[1]["arguments"]
        == session.calls[2]["arguments"]
        == {"weight": 2}
    )
    assert session.calls[0].get("request_state") is None
    assert session.calls[1]["request_state"] == "state-1"
    assert session.calls[2]["request_state"] == "state-2"
    assert set(session.calls[1]["input_responses"]) == {"address"}
    assert set(session.calls[2]["input_responses"]) == {"contact"}
    assert first_marker["round_index"] == 0


def test_managed_bridge_releases_claim_for_sequential_operation(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=None,
        round_limit=10,
        execution_id="execution-1",
    )
    session = _Session(
        [
            _form("address"),
            types.CallToolResult(content=[], structuredContent={"operation": 1}),
            _form("contact"),
            types.CallToolResult(content=[], structuredContent={"operation": 2}),
        ]
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )

    def call(request_id: str) -> dict[str, Any]:
        return asyncio.run(
            _handle_request(
                bridge,
                {
                    "id": request_id,
                    "method": "call_tool",
                    "server": "example-mcp",
                    "tool": "book",
                    "arguments": {},
                    "generation": "g1",
                },
            )
        )

    first = call("request-1")
    first_result = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-2",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": first["result"]["continuation_id"],
                "responses": {
                    "address": {"action": "accept", "content": {"city": "Pune"}}
                },
            },
        )
    )
    assert first_result["result"]["structuredContent"] == {"operation": 1}
    second = call("request-3")
    second_result = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-4",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": second["result"]["continuation_id"],
                "responses": {
                    "contact": {"action": "accept", "content": {"city": "Pune"}}
                },
            },
        )
    )
    assert second_result["result"]["structuredContent"] == {"operation": 2}
    assert len(session.calls) == 4


def test_managed_continue_failure_cleans_claim_and_is_terminal(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=None,
        round_limit=10,
        execution_id="execution-1",
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": _RetryFailureSession([_form()])},
        channel=channel,
    )
    first = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-1",
                "method": "call_tool",
                "server": "example-mcp",
                "tool": "book",
                "arguments": {},
                "generation": "g1",
            },
        )
    )
    marker = first["result"]
    second = asyncio.run(
        _handle_request(
            bridge,
            {
                "id": "request-2",
                "method": "continue_tool",
                "generation": "g1",
                "continuation_id": marker["continuation_id"],
                "responses": {
                    "address": {"action": "accept", "content": {"city": "Pune"}}
                },
            },
        )
    )
    assert second["ok"] is False
    assert second["error_code"] == "mcp_protocol"
    assert second["error_code"] != "cancelled"
    assert channel.read_status().state == "failed"  # type: ignore[union-attr]
    with pytest.raises(BridgeProtocolError, match="terminal"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))


def test_bridge_rejects_unexpected_input_without_plan(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(generation="g1", turn_sequence=1, plan=None, round_limit=10)
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": _Session([_form()])},
        channel=channel,
    )
    with pytest.raises(BridgeProtocolError, match="without a plan"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert channel.read_status().error_code == "unexpected_elicitation"  # type: ignore[union-attr]


def test_bridge_rejects_stale_generation(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(generation="new", turn_sequence=2, plan=None, round_limit=10)
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": _Session([])}, channel=channel
    )
    with pytest.raises(BridgeProtocolError, match="stale"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="old"))


def test_bridge_supports_multi_request_round(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    plan = round_of(
        expect_form("address").accept({"city": "Pune"}),
        expect_form("contact").accept({"phone": "555"}),
    )
    channel.write_context(generation="g1", turn_sequence=1, plan=plan, round_limit=10)
    first = types.InputRequiredResult(
        inputRequests={
            "address": _form().input_requests["address"],
            "contact": _form("contact", "phone").input_requests["contact"],
        },
        requestState="state",
    )
    session = _Session([first, types.CallToolResult(content=[])])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert set(session.calls[1]["input_responses"]) == {"address", "contact"}


def test_bridge_url_is_asserted_and_not_visited(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    plan = expect_url("payment", url="https://example.test/pay").accept()
    channel.write_context(generation="g1", turn_sequence=1, plan=plan, round_limit=10)
    request = types.InputRequiredResult(
        inputRequests={
            "payment": types.ElicitRequest(
                params=types.ElicitRequestURLParams(
                    message="Pay", url="https://example.test/pay"
                )
            )
        },
        requestState="state",
    )
    session = _Session([request, types.CallToolResult(content=[])])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert len(session.calls) == 2


def test_bridge_cancellation_marks_generation_failed_and_cannot_be_reused(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        channel = _channel(tmp_path)
        channel.write_context(
            generation="g1", turn_sequence=1, plan=None, round_limit=10
        )
        bridge = MCPBridge(
            {"example-mcp": {"book": {}}},
            {"example-mcp": _BlockingSession([])},
            channel=channel,
        )
        task = asyncio.create_task(
            bridge.call_tool("example-mcp", "book", {}, generation="g1")
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert channel.read_status().error_code == "cancelled"  # type: ignore[union-attr]
        with pytest.raises(BridgeProtocolError, match="stale"):
            await bridge.call_tool("example-mcp", "book", {}, generation="old")

    asyncio.run(scenario())


def test_bridge_requires_plan_completion_at_action_finalize(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=expect_form("address").accept({"city": "Pune"}),
        round_limit=10,
    )
    session = _Session([types.CallToolResult(content=[])])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    with pytest.raises(BridgeProtocolError, match="action did not"):
        bridge.finalize_action("g1")
    assert channel.read_status().error_code == "elicitation_mismatch"  # type: ignore[union-attr]


def test_bridge_rejects_second_same_target_eliciting_invocation(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=sequence(
            expect_form("address").accept({"city": "Pune"}),
            expect_form("contact").accept({"phone": "555"}),
        ),
        round_limit=10,
    )
    first = _Session(
        [
            _form(),
            types.CallToolResult(content=[]),
            _form("contact", "phone"),
            types.CallToolResult(content=[]),
        ]
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": first}, channel=channel
    )
    # The first call consumes one operation's plan; a later call to the same
    # qualified target is a new logical invocation and cannot claim it again.
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    with pytest.raises(BridgeProtocolError, match="more than one"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))


def test_concurrent_eliciting_calls_cancel_first_and_fail_generation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        channel = _channel(tmp_path)
        channel.write_context(
            generation="g1",
            turn_sequence=1,
            plan=expect_form("address").accept({"city": "Pune"}),
            round_limit=10,
        )
        session = _ConcurrentElicitingSession()
        bridge = MCPBridge(
            {"example-mcp": {"book": {}}},
            {"example-mcp": session},
            channel=channel,
        )
        first = asyncio.create_task(
            bridge.call_tool("example-mcp", "book", {}, generation="g1")
        )
        await session.first_dispatch_started.wait()
        with pytest.raises(BridgeProtocolError, match="more than one"):
            await bridge.call_tool("example-mcp", "book", {}, generation="g1")
        with pytest.raises(asyncio.CancelledError):
            await first
        assert session.first_dispatch_cancelled.is_set()
        assert channel.read_status().state == "failed"  # type: ignore[union-attr]
        assert channel.read_status().error_code == "concurrent_elicitation"  # type: ignore[union-attr]
        with pytest.raises(BridgeProtocolError, match="terminal"):
            await bridge.call_tool("example-mcp", "book", {}, generation="g1")
        assert len(session.calls) == 2

    asyncio.run(scenario())


def test_bridge_retry_uses_only_current_round_responses_and_exact_state(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    plan = sequence(
        expect_form("address").accept({"city": "Pune"}),
        expect_form("address").accept({"city": "Mumbai"}),
    )
    channel.write_context(generation="g1", turn_sequence=1, plan=plan, round_limit=10)
    session = _Session(
        [
            _form(request_state="state-1"),
            _form(request_state="state-2"),
            types.CallToolResult(content=[]),
        ]
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {"weight": 2}, generation="g1"))
    assert session.calls[1]["request_state"] == "state-1"
    assert session.calls[2]["request_state"] == "state-2"
    assert set(session.calls[1]["input_responses"]) == {"address"}
    assert set(session.calls[2]["input_responses"]) == {"address"}


def test_bridge_limit_allows_ten_rounds_and_final_retry(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    plan = sequence(*[expect_form(f"r{i}").accept({"city": "Pune"}) for i in range(10)])
    channel.write_context(generation="g1", turn_sequence=1, plan=plan, round_limit=10)
    responses: list[Any] = []
    for index in range(10):
        responses.append(_form(f"r{index}", request_state=f"state-{index}"))
    responses.append(types.CallToolResult(content=[]))
    session = _Session(responses)
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert len(session.calls) == 11


def test_bridge_eleventh_round_fails_without_an_extra_retry(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    plan = sequence(*[expect_form(f"r{i}").accept({"city": "Pune"}) for i in range(11)])
    channel.write_context(generation="g1", turn_sequence=1, plan=plan, round_limit=10)
    session = _Session([_form(f"r{i}", request_state=f"state-{i}") for i in range(11)])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    with pytest.raises(BridgeProtocolError, match="limit"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert len(session.calls) == 11


def test_bridge_decline_is_forwarded_without_form_content(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=expect_form("address").decline(),
        round_limit=10,
    )
    session = _Session([_form(), types.CallToolResult(content=[])])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    response = session.calls[1]["input_responses"]["address"]
    assert response.action == "decline" and response.content is None


def test_bridge_url_mismatch_and_schema_mismatch_fail_before_retry(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=expect_url("payment", url="https://expected.test").accept(),
        round_limit=10,
    )
    request = types.InputRequiredResult(
        inputRequests={
            "payment": types.ElicitRequest(
                params=types.ElicitRequestURLParams(
                    message="Pay", url="https://other.test"
                )
            )
        },
        requestState="state",
    )
    session = _Session([request])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    with pytest.raises(BridgeProtocolError, match="elicitation"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))


def test_bridge_form_schema_mismatch_fails_before_retry(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=expect_form("address").accept({"city": 42}),
        round_limit=10,
    )
    session = _Session([_form()])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )
    with pytest.raises(BridgeProtocolError, match="elicitation"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert len(session.calls) == 1


def test_channel_rejects_unknown_status_and_invalid_permissions(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_status(BridgeActionStatus("g1", 1, "idle"))
    channel.status_path.write_text(
        '{"generation":"g1","turn_sequence":1,"state":"unknown"}'
    )
    channel.status_path.chmod(0o600)
    with pytest.raises(BridgeProtocolError, match="invalid"):
        channel.read_status()
    channel.status_path.chmod(0o644)
    with pytest.raises(BridgeProtocolError, match="permissions"):
        channel.read_status()


def test_failed_generation_is_sticky_and_finalize_cannot_overwrite_it(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=expect_form("address").accept({"city": "Pune"}),
        round_limit=10,
    )
    session = _Session([_form("wrong")])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    with pytest.raises(BridgeProtocolError):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert channel.read_status().state == "failed"  # type: ignore[union-attr]
    with pytest.raises(BridgeProtocolError, match="terminal"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    with pytest.raises(BridgeProtocolError):
        bridge.finalize_action("g1")
    assert channel.read_status().state == "failed"  # type: ignore[union-attr]


def test_state_only_input_required_retries_without_a_plan(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(generation="g1", turn_sequence=1, plan=None, round_limit=1)
    empty = types.InputRequiredResult(inputRequests={}, requestState="opaque")
    session = _Session([empty, types.CallToolResult(content=[])])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    result = asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert result["content"] == []
    assert len(session.calls) == 2
    assert session.calls[1]["request_state"] == "opaque"
    assert session.calls[1]["input_responses"] == {}
    assert channel.read_status().rounds == 1  # type: ignore[union-attr]


def test_state_only_round_does_not_consume_pi_elicitation_plan(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=sequence(
            expect_form("address").accept({"city": "Pune"}),
            expect_form("contact").accept({"email": "a@example.test"}),
        ),
        round_limit=4,
    )
    session = _Session(
        [
            types.InputRequiredResult(requestState="state-only-1"),
            _form("address", request_state="address-state"),
            types.InputRequiredResult(requestState="state-only-2"),
            _form("contact", "email", request_state="contact-state"),
            types.CallToolResult(content=[]),
        ]
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )

    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))

    assert [call.get("request_state") for call in session.calls] == [
        None,
        "state-only-1",
        "address-state",
        "state-only-2",
        "contact-state",
    ]
    assert session.calls[1]["input_responses"] == {}
    assert set(session.calls[2]["input_responses"]) == {"address"}
    assert session.calls[3]["input_responses"] == {}
    assert set(session.calls[4]["input_responses"]) == {"contact"}


def test_malformed_empty_input_required_fails_without_retry(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(generation="g1", turn_sequence=1, plan=None, round_limit=10)
    empty = types.InputRequiredResult.model_construct(
        input_requests=None,
        request_state=None,
    )
    session = _Session([empty])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    with pytest.raises(BridgeProtocolError, match="no requests"):
        asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert len(session.calls) == 1


def test_non_mapping_arguments_are_rejected(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(generation="g1", turn_sequence=1, plan=None, round_limit=10)
    session = _Session([types.CallToolResult(content=[])])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}}, {"example-mcp": session}, channel=channel
    )
    with pytest.raises(BridgeProtocolError, match="mapping"):
        asyncio.run(bridge.call_tool("example-mcp", "book", ["bad"], generation="g1"))
    assert not session.calls
    assert channel.read_status().state == "failed"  # type: ignore[union-attr]


def test_jsonl_dispatch_can_overlap_non_eliciting_calls() -> None:
    async def scenario() -> None:
        slow = _SlowCallSession([])
        bridge = MCPBridge(
            {"example-mcp": {"slow": {}, "fast": {"call": lambda _args: {"ok": True}}}},
            {"example-mcp": slow},
        )
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(
            _handle_request(
                bridge,
                {
                    "id": 1,
                    "method": "call_tool",
                    "server": "example-mcp",
                    "tool": "slow",
                    "arguments": {},
                },
            ),
            _handle_request(
                bridge,
                {
                    "id": 2,
                    "method": "call_tool",
                    "server": "example-mcp",
                    "tool": "fast",
                    "arguments": {},
                },
            ),
        )
        assert asyncio.get_running_loop().time() - started < 0.2
        assert results[1] == {"ok": True, "result": {"ok": True}}

    asyncio.run(scenario())


def test_jsonl_many_completed_requests_are_reaped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Input:
        def __init__(self, lines: list[bytes]) -> None:
            self.buffer = self
            self._lines = lines

        def readline(self, _limit: int) -> bytes:
            return self._lines.pop(0) if self._lines else b""

    lines = [
        (
            json.dumps(
                {
                    "id": index,
                    "method": "call_tool",
                    "server": "example-mcp",
                    "tool": "fast",
                    "arguments": {},
                }
            ).encode()
            + b"\n"
        )
        for index in range(100)
    ]
    monkeypatch.setattr(sys, "stdin", _Input(lines))
    bridge = MCPBridge(
        {
            "example-mcp": {
                "fast": {"call": lambda _args: {"ok": True}},
            }
        }
    )
    asyncio.run(serve(bridge))
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(output) == 100
    assert {item["id"] for item in output} == set(range(100))


def test_tool_catalog_uses_pi_json_schema_aliases() -> None:
    bridge = MCPBridge(
        {
            "example-mcp": {
                "book": {
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                }
            }
        }
    )
    descriptor = bridge.list_tools()[0]
    assert descriptor["inputSchema"] == {"type": "object"}
    assert descriptor["outputSchema"] == {"type": "object"}


def test_bridge_sampling_only_and_roots_only_rounds_are_retried(tmp_path: Path) -> None:
    async def scenario() -> None:
        sampling = types.CreateMessageRequest(
            params=types.CreateMessageRequestParams(messages=[], maxTokens=16)
        )
        roots = types.ListRootsRequest()
        for request, response in (
            (
                sampling,
                types.CreateMessageResult(
                    role="assistant",
                    content=types.TextContent(text="sampled"),
                    model="fixture",
                ),
            ),
            (roots, types.ListRootsResult(roots=[])),
        ):
            channel = _channel(tmp_path / str(len(request.method)))
            channel.write_context(
                generation="g1", turn_sequence=1, plan=None, round_limit=10
            )
            session = _InteractiveSession(
                [
                    types.InputRequiredResult(
                        inputRequests={"input": request}, requestState="opaque"
                    ),
                    types.CallToolResult(content=[]),
                ],
                response,
            )
            bridge = MCPBridge(
                {"example-mcp": {"book": {}}},
                {"example-mcp": session},
                channel=channel,
            )
            await bridge.call_tool("example-mcp", "book", {}, generation="g1")
            assert len(session.calls) == 2

    asyncio.run(scenario())


def test_bridge_mixed_round_merges_form_and_sampling_responses(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=expect_form("address").accept({"city": "Pune"}),
        round_limit=10,
    )
    sampling = types.CreateMessageRequest(
        params=types.CreateMessageRequestParams(messages=[], maxTokens=16)
    )
    first = types.InputRequiredResult(
        inputRequests={
            "address": _form().input_requests["address"],
            "sample": sampling,
        },
        requestState="opaque",
    )
    session = _InteractiveSession(
        [first, types.CallToolResult(content=[])],
        types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(text="sampled"),
            model="fixture",
        ),
    )
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert set(session.calls[1]["input_responses"]) == {"address", "sample"}


def test_bridge_round_of_accepts_form_and_url_together(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel.write_context(
        generation="g1",
        turn_sequence=1,
        plan=round_of(
            expect_form("address").accept({"city": "Pune"}),
            expect_url("payment", url="https://expected.test").accept(),
        ),
        round_limit=10,
    )
    first = types.InputRequiredResult(
        inputRequests={
            "address": _form().input_requests["address"],
            "payment": types.ElicitRequest(
                params=types.ElicitRequestURLParams(
                    message="Pay", url="https://expected.test"
                )
            ),
        },
        requestState="opaque",
    )
    session = _Session([first, types.CallToolResult(content=[])])
    bridge = MCPBridge(
        {"example-mcp": {"book": {}}},
        {"example-mcp": session},
        channel=channel,
    )
    asyncio.run(bridge.call_tool("example-mcp", "book", {}, generation="g1"))
    assert set(session.calls[1]["input_responses"]) == {"address", "payment"}
