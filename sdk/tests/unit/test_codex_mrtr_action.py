from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from m3.elicitation import (
    ElicitationResponse,
    expect_form,
    expect_url,
    maybe_form,
    round_of,
)
from m3.errors import ElicitationExpectationError
from m3.harness._codex_mrtr import CodexMRTRAction, _ObservedCall
from m3.harness.codex import (
    CodexHarnessAdapter,
    HarnessStartupError,
    codex_configuration,
    render_codex_config,
)
from m3.harness.contracts import HarnessInteractionCapabilities, HarnessTurnResult
from m3.server_group import HarnessServerConfig
from m3.types import TransportKind, TurnOutcome


class _Subscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False
        self.resume = asyncio.Event()
        self.resume.set()

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> Any:
        await self.resume.wait()
        value = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def acknowledge(self, event: Any) -> None:
        del event
        self.queue.task_done()

    async def barrier(self) -> int:
        self.resume.set()
        await self.queue.join()
        return self.queue.qsize()

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            self.queue.put_nowait(None)


class _Capture:
    def __init__(self) -> None:
        self.subscription = _Subscription()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def subscribe(
        self, connection_ids: Any = None, *, maxsize: int = 256
    ) -> _Subscription:
        del connection_ids, maxsize
        return self.subscription

    def publish(self, direction: str, payload: dict[str, Any]) -> None:
        self.events.append((direction, payload))
        self.subscription.queue.put_nowait(
            SimpleNamespace(
                connection_id="connection-1",
                direction=direction,
                payload=payload,
            )
        )

    def pause_observer(self) -> None:
        self.subscription.resume.clear()


def _launch(capture: _Capture) -> SimpleNamespace:
    return SimpleNamespace(
        capture=capture,
        configurations=(
            SimpleNamespace(
                connection_id="connection-1", key="fixture", available=True
            ),
        ),
    )


def _call(request_id: int | str, *, state: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"name": "collect", "arguments": {"batch": 2}}
    if state is not None:
        params["requestState"] = state
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": params,
    }


def _input_required(
    request_id: int | str, requests: dict[str, Any], state: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "resultType": "input_required",
            "inputRequests": requests,
            "requestState": state,
        },
    }


def _elicitation(key: str) -> dict[str, Any]:
    return {
        "method": "elicitation/create",
        "params": {
            "mode": "form",
            "message": "Enter the delivery city.",
            "requestedSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "_meta": {"m3/request_key": key},
        },
    }


def _native_prompt(request_id: int | str, key: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "mcpServer/elicitation/request",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "serverName": "fixture",
            "mode": "form",
            "message": "Enter the delivery city.",
            "requestedSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "_meta": {"m3/request_key": key},
        },
    }


async def _flush() -> None:
    # Yield to the observer and response coordinator tasks after test inputs.
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_action_batches_all_native_prompts_before_sending_any_answer() -> None:
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    plan = round_of(
        expect_form("home", message="Enter the delivery city.").accept(
            {"city": "Pune"}
        ),
        expect_form("business", message="Enter the delivery city.").accept(
            {"city": "Mumbai"}
        ),
    )
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=plan,
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            writes, request_id, result
        ),
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(
                1,
                {"home": _elicitation("home"), "business": _elicitation("business")},
                "state-1",
            ),
        )
        await _flush()
        action.submit_native_prompt(_native_prompt(8, "business"))
        await _flush()
        assert writes == []

        action.submit_native_prompt(_native_prompt(9, "home"))
        await _flush()
        assert writes == [
            (8, {"action": "accept", "content": {"city": "Mumbai"}}),
            (9, {"action": "accept", "content": {"city": "Pune"}}),
        ]

        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-1",
                    "inputResponses": {
                        "home": {"action": "accept", "content": {"city": "Pune"}},
                        "business": {
                            "action": "accept",
                            "content": {"city": "Mumbai"},
                        },
                    },
                },
            },
        )
        capture.publish(
            "server_to_client",
            {"jsonrpc": "2.0", "id": 2, "result": {"resultType": "complete"}},
        )
        await _flush()
        await action.finish()
        assert action.failure is None
    finally:
        await action.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "action_name"),
    [("form", "decline"), ("form", "cancel"), ("url", "decline"), ("url", "cancel")],
)
async def test_planned_decline_and_cancel_retry_without_content_and_preserve_meta(
    mode: str,
    action_name: str,
) -> None:
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    response_meta = {"fixture/response": action_name}
    if mode == "form":
        message = "Enter the delivery city."
        elicitation_params = {
            "mode": "form",
            "message": message,
            "requestedSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
            "_meta": {"m3/request_key": "answer"},
        }
        native_params = {
            "mode": "form",
            "message": message,
            "requestedSchema": elicitation_params["requestedSchema"],
            "_meta": elicitation_params["_meta"],
        }
        expectation = expect_form(
            "answer",
            message=message,
            schema=elicitation_params["requestedSchema"],
        )
    else:
        message = "Continue checkout."
        url = "https://example.test/checkout/123"
        elicitation_params = {
            "mode": "url",
            "message": message,
            "url": url,
            "elicitationId": "checkout-123",
        }
        native_params = dict(elicitation_params)
        native_params["_meta"] = None
        expectation = expect_url(
            "answer",
            message=message,
            url=url,
            elicitation_id="checkout-123",
        )
    plan = expectation.cancel() if action_name == "cancel" else expectation.decline()
    plan = plan.model_copy(
        update={
            "response": ElicitationResponse(
                action=action_name,
                meta=response_meta,
            )
        }
    )

    async def write_native_response(
        request_id: int | str, result: dict[str, Any]
    ) -> None:
        writes.append((request_id, result))

    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=plan,
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=write_native_response,
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(
                1,
                {
                    "answer": {
                        "method": "elicitation/create",
                        "params": elicitation_params,
                    }
                },
                "state-1",
            ),
        )
        await _flush()
        action.submit_native_prompt(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "mcpServer/elicitation/request",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "serverName": "fixture",
                    **native_params,
                },
            }
        )
        await _flush()

        assert writes == [
            (8, {"action": action_name, "content": None, "_meta": response_meta})
        ]
        input_responses = {"answer": {"action": action_name, "_meta": response_meta}}
        assert "content" not in input_responses["answer"]
        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-1",
                    "inputResponses": input_responses,
                },
            },
        )
        capture.publish(
            "server_to_client",
            {"jsonrpc": "2.0", "id": 2, "result": {"resultType": "complete"}},
        )
        await _flush()
        await action.finish()
        assert action.failure is None
    finally:
        await action.close()


async def _record_write(
    writes: list[tuple[int | str, dict[str, Any]]],
    request_id: int | str,
    result: dict[str, Any],
) -> None:
    writes.append((request_id, result))


@pytest.mark.asyncio
async def test_ordinary_codex_turn_does_not_start_mrtr_observation() -> None:
    class Capture:
        def subscribe(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("a normal Codex turn must not subscribe for MRTR")

    class Process:
        owner = None

        async def next(self, _timeout: float | None = None) -> dict[str, Any]:
            return {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "mcpToolCall",
                        "id": "tool-1",
                        "server": "fixture",
                        "tool": "shipping_quote",
                        "arguments": {},
                        "status": "completed",
                    }
                },
            }

    process = Process()
    adapter = CodexHarnessAdapter(executable="fixture")

    class Session:
        async def send(self, _request: Any) -> HarnessTurnResult:
            frame = await adapter.next_frame(process, None)
            assert frame["method"] == "item/completed"
            return HarnessTurnResult(sequence=1, status="completed")

    adapter._session = Session()  # type: ignore[assignment]
    adapter._launch = SimpleNamespace(capture=Capture(), configurations=())

    result = await adapter.send("Call the shipping quote tool.")

    assert result.outcome is TurnOutcome.COMPLETED
    assert adapter._active_mrtr_action is None


@pytest.mark.asyncio
async def test_native_elicitation_without_plan_fails_without_taking_over_codex() -> (
    None
):
    class Capture:
        def subscribe(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("unplanned elicitation must not start an observer")

    class Process:
        owner = None

        def __init__(self) -> None:
            self.writes: list[dict[str, Any]] = []

        async def next(self, _timeout: float | None = None) -> dict[str, Any]:
            return {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "mcpServer/elicitation/request",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "serverName": "fixture",
                    "mode": "form",
                    "message": "Enter the city.",
                    "requestedSchema": {"type": "object", "properties": {}},
                },
            }

        async def write(self, frame: dict[str, Any]) -> None:
            self.writes.append(frame)

    process = Process()
    adapter = CodexHarnessAdapter(executable="fixture")
    adapter._thread_id = "thread-1"
    adapter._turn_id = "turn-1"

    class Session:
        async def send(self, _request: Any) -> HarnessTurnResult:
            frame = await adapter.next_frame(process, None)
            assert frame["method"] == "mcpServer/elicitation/request"
            return HarnessTurnResult(sequence=1, status="interrupted")

    adapter._session = Session()  # type: ignore[assignment]
    adapter._launch = SimpleNamespace(capture=Capture(), configurations=())

    result = await adapter.send("Continue without granting new input.")

    assert result.outcome is TurnOutcome.FAILED
    assert result.error is not None
    assert result.error.details["reason"] == "unexpected_elicitation"
    assert process.writes == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "turn/interrupt",
            "params": {"threadId": "thread-1", "turnId": "turn-1"},
        }
    ]


@pytest.mark.asyncio
async def test_confirmed_turn_interrupt_keeps_codex_process_reusable() -> None:
    class Process:
        owner = None

        def __init__(self) -> None:
            self.writes: list[dict[str, Any]] = []
            self.closed = False

        async def write(self, frame: dict[str, Any]) -> None:
            if self.closed:
                raise RuntimeError("process was closed")
            self.writes.append(frame)

        async def close(self) -> None:
            self.closed = True

    process = Process()
    adapter = CodexHarnessAdapter(executable="fixture")
    adapter._thread_id = "thread-1"
    adapter._turn_id = "turn-1"

    await adapter._cancel(process)  # type: ignore[arg-type]
    await adapter._write_frame(process, {"method": "turn/start"})  # type: ignore[arg-type]

    assert process.closed is False
    assert process.writes == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "turn/interrupt",
            "params": {"threadId": "thread-1", "turnId": "turn-1"},
        },
        {"method": "turn/start"},
    ]


@pytest.mark.asyncio
async def test_native_failed_tool_result_preserves_mapping_error_message() -> None:
    adapter = CodexHarnessAdapter(executable="fixture")
    observations: list[Any] = []
    native_message = "input_required did not complete within 10 MRTR rounds"

    adapter.consume_frame(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "mcpToolCall",
                    "id": "native-call-1",
                    "server": "fixture",
                    "tool": "ten_rounds",
                    "arguments": {"rounds": 10},
                    "status": "failed",
                    "error": {"message": native_message},
                }
            },
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,
    )

    results = [
        item for item in observations if getattr(item, "status", None) == "tool_error"
    ]
    assert len(results) == 1
    assert results[0].error_message == native_message


def test_retry_identity_ignores_only_the_volatile_progress_token() -> None:
    initial = _ObservedCall(
        "connection-1",
        "fixture",
        "collect",
        {},
        {
            "name": "collect",
            "arguments": {},
            "_meta": {"progressToken": 1, "tenant": "north"},
        },
    )
    retry = _ObservedCall(
        "connection-1",
        "fixture",
        "collect",
        {},
        {
            "name": "collect",
            "arguments": {},
            "requestState": "state-1",
            "inputResponses": {"address": {"action": "accept", "content": {}}},
            "_meta": {"progressToken": 2, "tenant": "north"},
        },
    )
    changed_metadata = _ObservedCall(
        "connection-1",
        "fixture",
        "collect",
        {},
        {
            "name": "collect",
            "arguments": {},
            "requestState": "state-1",
            "inputResponses": {"address": {"action": "accept", "content": {}}},
            "_meta": {"progressToken": 2, "tenant": "south"},
        },
    )

    assert CodexMRTRAction._same_operation(initial, retry)
    assert not CodexMRTRAction._same_operation(initial, changed_metadata)


@pytest.mark.asyncio
async def test_malformed_second_prompt_fails_without_partial_answer() -> None:
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    plan = round_of(
        expect_form("home", message="Enter the delivery city.").accept(
            {"city": "Pune"}
        ),
        expect_form("business", message="Enter the delivery city.").accept(
            {"city": "Mumbai"}
        ),
    )
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=plan,
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            writes, request_id, result
        ),
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(
                1,
                {"home": _elicitation("home"), "business": _elicitation("business")},
                "state-1",
            ),
        )
        await _flush()
        action.submit_native_prompt(_native_prompt(8, "home"))
        wrong = _native_prompt(9, "business")
        wrong["params"]["requestedSchema"] = {"type": "object", "properties": {}}
        action.submit_native_prompt(wrong)
        await _flush()
        assert writes == []
        assert action.failure is not None
        assert getattr(action.failure, "details", {}).get("reason") == (
            "unmatched_native_prompt"
        )
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_concurrent_eliciting_operation_fails_before_late_native_answers() -> (
    None
):
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=expect_form("address", message="Enter the delivery city.").accept(
            {"city": "Pune"}
        ),
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            writes, request_id, result
        ),
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(1, {"address": _elicitation("address")}, "state-1"),
        )
        await _flush()
        assert action.failure is None

        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "ship", "arguments": {"batch": 3}},
            },
        )
        capture.publish(
            "server_to_client",
            _input_required(2, {"other": _elicitation("other")}, "state-2"),
        )
        await asyncio.wait_for(action.failure_event.wait(), timeout=1.0)

        assert isinstance(action.failure, ElicitationExpectationError)
        assert action.failure.details["reason"] == "concurrent_elicitation"
        assert action.requires_turn_interrupt is True
        assert writes == []
        assert action._awaiting_retry is None
        assert [
            event[1]["params"]["name"]
            for event in capture.events
            if event[0] == "client_to_server"
        ] == ["collect", "ship"]

        # A prompt already queued by Codex can arrive after the second wire
        # result made the action terminal. The failed action must not answer it.
        action.submit_native_prompt(_native_prompt(8, "address"))
        action.submit_native_prompt(_native_prompt(9, "other"))
        await _flush()
        assert writes == []
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_second_native_response_write_failure_stops_batch_without_replay() -> (
    None
):
    capture = _Capture()
    attempts: list[tuple[int | str, dict[str, Any]]] = []
    successful: list[tuple[int | str, dict[str, Any]]] = []
    aborted: list[BaseException] = []
    responses_ready = asyncio.Event()

    async def write_response(request_id: int | str, result: dict[str, Any]) -> None:
        attempts.append((request_id, result))
        if request_id == 9:
            raise OSError("Codex input pipe closed during the second response")
        successful.append((request_id, result))

    async def handle_round(
        _call: Any,
        requests: Any,
        _request_state: str | None,
        _request_state_present: bool,
    ) -> dict[str, ElicitationResponse]:
        assert set(requests) == {"home", "business"}
        responses_ready.set()
        return {
            "home": ElicitationResponse(action="accept", content={"city": "Pune"}),
            "business": ElicitationResponse(
                action="accept", content={"city": "Mumbai"}
            ),
        }

    async def abort_round(error: BaseException) -> None:
        aborted.append(error)

    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=None,
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=write_response,
        managed_round_handler=handle_round,
        managed_round_abort=abort_round,
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(
                1,
                {"home": _elicitation("home"), "business": _elicitation("business")},
                "state-1",
            ),
        )
        await _flush()
        await asyncio.wait_for(responses_ready.wait(), timeout=1.0)
        action.submit_native_prompt(_native_prompt(8, "business"))
        action.submit_native_prompt(_native_prompt(9, "home"))
        await asyncio.wait_for(action.failure_event.wait(), timeout=1.0)

        assert isinstance(action.failure, OSError)
        assert action.requires_turn_interrupt is True
        assert attempts == [
            (8, {"action": "accept", "content": {"city": "Mumbai"}}),
            (9, {"action": "accept", "content": {"city": "Pune"}}),
        ]
        assert successful == [attempts[0]]

        # Any further observer or native events after the writer failure must
        # not make the action replay the successful first response.
        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-1",
                    "inputResponses": {
                        "business": {"action": "accept", "content": {"city": "Mumbai"}},
                        "home": {"action": "accept", "content": {"city": "Pune"}},
                    },
                },
            },
        )
        action.submit_native_prompt(_native_prompt(10, "business"))
        await _flush()
        assert len(attempts) == 2
        assert successful == [attempts[0]]
        assert [
            event[1]["params"]["name"]
            for event in capture.events
            if event[0] == "client_to_server"
        ] == ["collect", "collect"]
    finally:
        await action.close()
    assert aborted == [action.failure]


@pytest.mark.asyncio
async def test_action_correlates_integer_and_string_mcp_request_ids_separately() -> (
    None
):
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=expect_form("address", message="Enter the delivery city.").accept(
            {"city": "Pune"}
        ),
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            writes, request_id, result
        ),
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tools/call",
                "params": {"name": "collect", "arguments": {"batch": 3}},
            },
        )
        await _flush()
        assert set(action._calls) == {
            ("connection-1", int, 1),
            ("connection-1", str, "1"),
        }

        capture.publish(
            "server_to_client",
            {"jsonrpc": "2.0", "id": "1", "result": {"resultType": "complete"}},
        )
        await _flush()
        assert set(action._calls) == {("connection-1", int, 1)}

        capture.publish(
            "server_to_client",
            _input_required(1, {"address": _elicitation("address")}, "state-1"),
        )
        await _flush()
        action.submit_native_prompt(_native_prompt("1", "address"))
        await _flush()
        assert writes == [("1", {"action": "accept", "content": {"city": "Pune"}})]

        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-1",
                    "inputResponses": {
                        "address": {"action": "accept", "content": {"city": "Pune"}}
                    },
                },
            },
        )
        capture.publish(
            "server_to_client",
            {"jsonrpc": "2.0", "id": 2, "result": {"resultType": "complete"}},
        )
        await _flush()
        await action.finish()
        assert action.failure is None
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_state_only_input_required_uses_codex_auto_retry_without_prompt() -> None:
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    plan = maybe_form("optional").accept({})
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=plan,
        round_limit=1,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            writes, request_id, result
        ),
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish("server_to_client", _input_required(1, {}, "state-only"))
        await _flush()
        assert action.failure is None
        assert writes == []
        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-only",
                },
            },
        )
        capture.publish(
            "server_to_client",
            {"jsonrpc": "2.0", "id": 2, "result": {"resultType": "complete"}},
        )
        await _flush()
        await action.finish()
        assert action.failure is None
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_lower_m3_round_limit_fails_immediately_before_native_cap() -> None:
    capture = _Capture()
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=maybe_form("optional").accept({}),
        round_limit=1,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            [], request_id, result
        ),
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish("server_to_client", _input_required(1, {}, "state-1"))
        await _flush()
        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-1",
                },
            },
        )
        capture.publish("server_to_client", _input_required(2, {}, "state-2"))
        await _flush()

        assert isinstance(action.failure, ElicitationExpectationError)
        assert action.failure.details["reason"] == "round_limit_exceeded"
        assert action.requires_turn_interrupt is True
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_codex_tenth_round_failure_uses_native_item_without_interrupt() -> None:
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=maybe_form("optional").accept({}),
        round_limit=10,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            writes, request_id, result
        ),
    )
    action._rounds = 9
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(1, {"address": _elicitation("address")}, "state-10"),
        )
        await _flush()

        assert action.failure is None
        assert action._pending_native_rejection is not None
        assert writes == []

        action.observe_native_tool_item(
            {
                "server": "fixture",
                "tool": "collect",
                "arguments": {"batch": 2},
                "status": "failed",
                "error": "input_required did not complete within 10 MRTR rounds",
            }
        )

        assert isinstance(action.failure, ElicitationExpectationError)
        assert action.failure.details["reason"] == "round_limit_exceeded"
        assert action.requires_turn_interrupt is False
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_terminal_barrier_drains_published_keyed_retry_before_plan_completion() -> (
    None
):
    capture = _Capture()
    capture.pause_observer()
    writes: list[tuple[int | str, dict[str, Any]]] = []

    async def write_response(request_id: int | str, result: dict[str, Any]) -> None:
        writes.append((request_id, result))
        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-1",
                    "inputResponses": {
                        "address": {"action": "accept", "content": {"city": "Pune"}}
                    },
                },
            },
        )
        capture.publish(
            "server_to_client",
            {"jsonrpc": "2.0", "id": 2, "result": {"resultType": "complete"}},
        )

    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=expect_form("address", message="Enter the delivery city.").accept(
            {"city": "Pune"}
        ),
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=write_response,
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(1, {"address": _elicitation("address")}, "state-1"),
        )
        action.submit_native_prompt(_native_prompt(8, "address"))
        # The app-server can deliver turn/completed while the subscriber still
        # has already-published request/response events awaiting consumption.
        # finish() must drain and acknowledge those events before deciding the
        # plan is complete.
        await action.finish()
        assert writes == [(8, {"action": "accept", "content": {"city": "Pune"}})]
        assert action.failure is None
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_optional_plan_waits_for_each_native_tool_terminal_observation() -> None:
    capture = _Capture()
    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=maybe_form("optional").accept({}),
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            [], request_id, result
        ),
    )
    await action.start()
    item = {"server": "fixture", "tool": "collect", "arguments": {"batch": 2}}
    completed = asyncio.create_task(action.finish(native_tool_items=(item, item)))
    try:
        await _flush()
        assert not completed.done()

        for request_id in (1, 2):
            capture.publish("client_to_server", _call(request_id))
            capture.publish(
                "server_to_client",
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"resultType": "complete"},
                },
            )
            await _wait_for_terminal_operations(action, request_id)
            if request_id == 1:
                # One identical wire operation cannot satisfy two native
                # mcpToolCall items; terminal evidence is reconciled as a
                # multiset rather than by the first matching item.
                assert not completed.done()

        await completed
        assert action.failure is None
    finally:
        if not completed.done():
            completed.cancel()
            await asyncio.gather(completed, return_exceptions=True)
        await action.close()


async def _wait_for_terminal_operations(action: CodexMRTRAction, expected: int) -> None:
    while sum(action._terminal_operations.values()) < expected:
        await action._observation_changed.wait()
        action._observation_changed.clear()


@pytest.mark.asyncio
async def test_native_tool_item_without_server_binding_fails_terminal_validation() -> (
    None
):
    action = CodexMRTRAction(
        launch=_launch(_Capture()),
        plan=maybe_form("optional").accept({}),
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=lambda request_id, result: _record_write(
            [], request_id, result
        ),
    )
    await action.start()
    try:
        await action.finish(native_tool_items=({"tool": "collect"},))
        assert isinstance(action.failure, ElicitationExpectationError)
        assert action.failure.details["reason"] == "native_tool_item_unbound"
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_managed_response_stays_live_until_exact_retry_result() -> None:
    capture = _Capture()
    writes: list[tuple[int | str, dict[str, Any]]] = []
    native_response_written = asyncio.Event()
    handler_started = asyncio.Event()
    deliver_managed_response = asyncio.Event()
    completed_rounds: list[bool] = []
    aborted: list[BaseException] = []

    async def handle_round(
        call: Any,
        requests: Any,
        request_state: str | None,
        request_state_present: bool,
    ) -> dict[str, ElicitationResponse]:
        assert call.name == "collect"
        assert set(requests) == {"address"}
        assert request_state == "state-1"
        assert request_state_present is True
        handler_started.set()
        await deliver_managed_response.wait()
        return {
            "address": ElicitationResponse(action="accept", content={"city": "Pune"})
        }

    async def complete_round(*, operation_complete: bool) -> None:
        completed_rounds.append(operation_complete)

    async def abort_round(error: BaseException) -> None:
        aborted.append(error)

    async def write_response(request_id: int | str, result: dict[str, Any]) -> None:
        await _record_write(writes, request_id, result)
        native_response_written.set()

    action = CodexMRTRAction(
        launch=_launch(capture),
        plan=None,
        round_limit=3,
        thread_id=lambda: "thread-1",
        turn_id=lambda: "turn-1",
        write_native_response=write_response,
        managed_round_handler=handle_round,
        managed_round_completed=complete_round,
        managed_round_abort=abort_round,
    )
    await action.start()
    try:
        capture.publish("client_to_server", _call(1))
        capture.publish(
            "server_to_client",
            _input_required(1, {"address": _elicitation("address")}, "state-1"),
        )
        action.submit_native_prompt(_native_prompt(8, "address"))
        await handler_started.wait()

        # The action-level observer remains responsive while its managed
        # callback waits for user input.
        capture.publish("client_to_server", {"jsonrpc": "2.0", "method": "ping"})
        await _flush()
        assert writes == []
        assert completed_rounds == []

        deliver_managed_response.set()
        await asyncio.wait_for(native_response_written.wait(), timeout=1.0)
        assert writes == [(8, {"action": "accept", "content": {"city": "Pune"}})]

        capture.publish(
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "collect",
                    "arguments": {"batch": 2},
                    "requestState": "state-1",
                    "inputResponses": {
                        "address": {
                            "action": "accept",
                            "content": {"city": "Pune"},
                        }
                    },
                },
            },
        )
        await _flush()
        assert completed_rounds == []
        capture.publish(
            "server_to_client",
            {"jsonrpc": "2.0", "id": 2, "result": {"resultType": "complete"}},
        )
        await _flush()
        assert completed_rounds == [True]
        assert aborted == []
        await action.finish()
        assert action.failure is None
    finally:
        await action.close()


@pytest.mark.asyncio
async def test_turn_timeout_aborts_pending_managed_mrtr_round() -> None:
    capture = _Capture()
    runtime_started = asyncio.Event()
    release_runtime = asyncio.Event()
    aborted: list[BaseException] = []

    class Runtime:
        bound_identity = ("harness-session", "m3-session", "m3-turn")
        execution_id = "execution-1"

        async def await_round(self, _pending: Any, _parameters: Any) -> Any:
            runtime_started.set()
            await release_runtime.wait()
            return {
                "address": ElicitationResponse(
                    action="accept", content={"city": "Pune"}
                )
            }

        async def resolve_round(
            self, _round_id: str, *, operation_complete: bool
        ) -> None:
            raise AssertionError("timed-out round must not be resolved")

        async def fail_round(self, error: BaseException) -> None:
            aborted.append(error)
            release_runtime.set()

    runtime = Runtime()

    class Process:
        owner = None

        def __init__(self) -> None:
            self.frames: list[dict[str, Any]] = []
            self.writes: list[dict[str, Any]] = []
            self.closed = False
            self.never = asyncio.Event()

        async def write(self, frame: Any) -> None:
            payload = dict(frame)
            self.writes.append(payload)
            if payload.get("method") == "turn/start":
                self.frames.extend(
                    [
                        {
                            "jsonrpc": "2.0",
                            "id": payload["id"],
                            "result": {"turn": {"id": "turn-1"}},
                        },
                        _native_prompt(8, "address"),
                    ]
                )
                capture.publish("client_to_server", _call(1))
                capture.publish(
                    "server_to_client",
                    _input_required(1, {"address": _elicitation("address")}, "state-1"),
                )

        async def next(self, _timeout: float | None = None) -> Any:
            if self.frames:
                return self.frames.pop(0)
            # Model the turn deadline expiring only after the action has
            # entered its durable wait for a managed answer.
            await runtime_started.wait()
            await asyncio.wait_for(self.never.wait(), timeout=0.01)
            raise AssertionError("the pending turn should time out")

        async def close(self) -> None:
            self.closed = True

    process = Process()
    adapter = CodexHarnessAdapter(executable="fixture")
    adapter._mrtr_capability_checked = True
    adapter._capabilities = replace(
        adapter._capabilities,
        interaction=HarnessInteractionCapabilities(
            supports_elicitation=True,
            preserves_request_keys=True,
            preserves_multi_request_rounds=True,
            supports_interaction_cancellation=True,
        ),
    )
    adapter._launch = _launch(capture)  # type: ignore[assignment]
    adapter._process = process  # type: ignore[assignment]
    adapter._thread_id = "thread-1"
    adapter._set_managed_input_runtime(runtime)

    class Session:
        session_id = "session-1"

        async def send(self, request: Any) -> Any:
            return await adapter._send(request, 1, process)  # type: ignore[arg-type]

    adapter._session = Session()  # type: ignore[assignment]

    async def start_turn(current_process: Any, request: Any, sequence: int) -> None:
        del request, sequence
        adapter._request_id += 1
        adapter._pending_turn_request = adapter._request_id
        adapter._turn_id = None
        await adapter._write_frame(
            current_process,
            {
                "jsonrpc": "2.0",
                "id": adapter._request_id,
                "method": "turn/start",
            },
        )

    adapter.send_turn = start_turn  # type: ignore[assignment]
    result = await adapter.send("wait for managed input", timeout=1.0)

    assert runtime_started.is_set()
    assert result.outcome is TurnOutcome.TIMED_OUT
    assert process.closed is True
    assert len(aborted) == 1
    assert isinstance(aborted[0], ElicitationExpectationError)
    assert getattr(aborted[0], "details", {}).get("reason") == (
        "managed_delivery_unconfirmed"
    )
    assert any(frame.get("method") == "turn/interrupt" for frame in process.writes)
    assert not any(frame.get("id") == 8 for frame in process.writes)


def test_codex_modern_mcp_configuration_and_marker_conflict() -> None:
    server = HarnessServerConfig(
        "fixture",
        TransportKind.STDIO,
        True,
        True,
        "connection-1",
        command="mcp-server",
    )
    launch = SimpleNamespace(configurations=(server,))

    config = codex_configuration(launch)
    assert config["features"] == {"mcp_2026_07_28": True}
    assert (
        config["mcp_servers"]["fixture"]["env"]["CODEX_MCP_PROTOCOL_VERSION"]
        == "2026-07-28"
    )
    rendered = render_codex_config(launch)
    assert "[features]" in rendered
    assert "mcp_2026_07_28 = true" in rendered
    assert '"CODEX_MCP_PROTOCOL_VERSION" = "2026-07-28"' in rendered

    conflicting = replace(server, environment={"CODEX_MCP_PROTOCOL_VERSION": "legacy"})
    with pytest.raises(HarnessStartupError, match="protocol marker conflicts"):
        codex_configuration(SimpleNamespace(configurations=(conflicting,)))
