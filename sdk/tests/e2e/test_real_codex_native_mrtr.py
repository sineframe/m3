"""Real installed Codex characterization using only local deterministic services."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fixtures.codex_app_server_harness import (
    CodexAppServer,
    CodexUnavailable,
    require_codex,
)
from fixtures.codex_responses_provider import (
    ModelOutput,
    ResponsesRun,
    function_tools,
    open_codex_responses_provider,
    wait_for_requests,
)

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_MCP_SERVER = _SDK_ROOT / "tests" / "fixtures" / "codex_mrtr_server.py"


def _codex_or_skip() -> tuple[str, str]:
    try:
        return require_codex()
    except CodexUnavailable as exc:
        if os.environ.get("M3_REQUIRE_CODEX_MRTR") == "1":
            pytest.fail(str(exc), pytrace=False)
        pytest.skip(str(exc))


def _tool_records(marker: Path) -> list[dict[str, Any]]:
    if not marker.exists():
        return []
    return [
        value
        for line in marker.read_text(encoding="utf-8").splitlines()
        if isinstance((value := json.loads(line)), dict)
        and value.get("method") == "tools/call"
    ]


async def _run_turn(
    tmp_path: Path,
    *,
    tool: str,
    arguments: dict[str, Any] | None = None,
    answers: dict[str, dict[str, Any] | None] | None = None,
    response_actions: dict[str, str] | None = None,
    interrupt_first: bool = False,
    response_meta: dict[str, Any] | None = None,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], ResponsesRun]:
    executable, _version = _codex_or_skip()
    marker = tmp_path / "mcp-wire.jsonl"
    provider = ResponsesRun()
    provider.enqueue(ModelOutput(function_name=tool, arguments=arguments or {}))
    if not interrupt_first:
        provider.enqueue(ModelOutput(text="completed by deterministic provider"))
    with open_codex_responses_provider(provider) as provider_url:
        app = CodexAppServer(
            executable=executable,
            codex_home=tmp_path / "codex-home",
            workspace=tmp_path / "workspace",
            provider_url=provider_url,
            mcp_server=_MCP_SERVER,
            wire_marker=marker,
        )
        try:
            await app.start()
            await app.start_turn(f"Call the fixture {tool} tool exactly once.")

            async def answer(frame: dict[str, Any]) -> None:
                params = frame.get("params")
                params = params if isinstance(params, dict) else {}
                elicitation = params.get("mode")
                message = params.get("message")
                metadata = params.get("_meta")
                metadata = metadata if isinstance(metadata, dict) else {}
                is_tool_approval = (
                    metadata.get("codex_approval_kind") == "mcp_tool_call"
                )
                if is_tool_approval:
                    await app._write(
                        {
                            "jsonrpc": "2.0",
                            "id": frame.get("id"),
                            "result": {"action": "accept", "content": {}},
                        }
                    )
                    return
                payload = (answers or {}).get(str(metadata.get("fixture/request_id")))
                if payload is None:
                    payload = (answers or {}).get(str(message))
                action = (response_actions or {}).get(
                    str(metadata.get("fixture/request_id"))
                )
                if action is None:
                    action = (response_actions or {}).get(str(message), "accept")
                result: dict[str, Any] = {
                    "action": action,
                }
                if is_tool_approval or (action == "accept" and elicitation == "form"):
                    result["content"] = {} if is_tool_approval else payload
                if response_meta is not None and elicitation in {"form", "url"}:
                    result["_meta"] = response_meta
                if interrupt_first:
                    turn_id = params.get("turnId")
                    assert isinstance(turn_id, str), frame
                    await app.cancel_turn(turn_id)
                    return
                await app._write(
                    {"jsonrpc": "2.0", "id": frame.get("id"), "result": result}
                )

            frames = await app.read_until_turn_completed(on_elicitation=answer)
        finally:
            await app.close()
    records = tuple(_tool_records(marker))
    wait_for_requests(provider, 1)
    return tuple(dict(frame) for frame in frames), records, provider


@pytest.mark.asyncio
async def test_real_codex_negotiates_modern_mcp_and_surfaces_a_form(
    tmp_path: Path,
) -> None:
    native, calls, provider = await _run_turn(
        tmp_path,
        tool="form_round",
        answers={
            "Enter the delivery address.": {
                "street": "1 Main",
                "city": "Pune",
                "postal_code": "411001",
            }
        },
        response_meta={"fixture/response": "accepted"},
    )
    prompts = _mrtr_prompts(native)
    assert prompts, (
        "Codex did not ask its app-server client for elicitation input; "
        f"wire_methods={[request['method'] for request in _server_records(tmp_path / 'mcp-wire.jsonl')]!r}; "
        f"protocols={[request['params'].get('protocolVersion') for request in _server_records(tmp_path / 'mcp-wire.jsonl') if request['method'] == 'initialize']!r}; "
        f"provider_requests={len(provider.requests)}"
    )
    request = prompts[0]
    assert request.get("method") == "mcpServer/elicitation/request"
    params = request.get("params")
    assert isinstance(params, dict)
    assert params.get("mode") == "form"
    assert params.get("message") == "Enter the delivery address."
    assert params.get("requestedSchema") == {
        "type": "object",
        "properties": {
            "street": {"type": "string"},
            "city": {"type": "string"},
            "postal_code": {"type": "string"},
        },
        "required": ["street", "city", "postal_code"],
    }
    assert params.get("_meta") is None
    assert "requestKey" not in params and "roundId" not in params
    assert len(calls) == 2
    retry = calls[-1]["params"]
    assert retry["requestState"] == "form-state"
    assert retry["inputResponses"] == {
        "shipping_address": {
            "action": "accept",
            "content": {
                "street": "1 Main",
                "city": "Pune",
                "postal_code": "411001",
            },
            "_meta": {"fixture/response": "accepted"},
        }
    }
    records = _server_records(tmp_path / "mcp-wire.jsonl")
    assert records[0] == {
        "method": "server/startup",
        "protocolMarker": None,
    }
    discovery = next(
        record for record in records if record["method"] == "server/discover"
    )
    discovery_meta = discovery["params"]["_meta"]
    assert discovery_meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert discovery_meta["io.modelcontextprotocol/clientInfo"] == {
        "name": "codex-mcp-client",
        "title": "Codex",
        "version": "0.156.1",
    }
    assert discovery_meta["io.modelcontextprotocol/clientCapabilities"] == {
        "experimental": {"codex/auth-change": {}},
        "elicitation": {"form": {}, "url": {}},
    }
    assert any(record["method"] == "tools/list" for record in records)
    assert not any(record["method"] == "initialize" for record in records)
    assert provider.requests
    assert "authorization" not in provider.requests[0].headers
    assert "mcp__fixture::form_round" in function_tools(provider.requests[0])
    resolutions = [
        frame for frame in native if frame.get("method") == "serverRequest/resolved"
    ]
    assert len(resolutions) == 2  # tool approval, then elicitation answer
    assert resolutions[-1]["params"]["requestId"] == request["id"]


def _mrtr_prompts(native: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        frame
        for frame in native
        if frame.get("method") == "mcpServer/elicitation/request"
        and not _is_tool_approval(frame)
    )


def _is_tool_approval(frame: dict[str, Any]) -> bool:
    params = frame.get("params")
    metadata = params.get("_meta") if isinstance(params, dict) else None
    return (
        isinstance(metadata, dict)
        and metadata.get("codex_approval_kind") == "mcp_tool_call"
    )


def _server_records(marker: Path) -> list[dict[str, Any]]:
    if not marker.exists():
        return []
    return [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.asyncio
async def test_real_codex_sends_each_native_request_for_a_multi_request_round(
    tmp_path: Path,
) -> None:
    native, calls, _provider = await _run_turn(
        tmp_path,
        tool="same_round",
        answers={
            "Enter the home delivery address.": {"city": "Pune"},
            "Enter the business delivery address.": {"city": "Mumbai"},
        },
    )
    params = [item.get("params", {}) for item in _mrtr_prompts(native)]
    assert {item.get("message") for item in params} == {
        "Enter the home delivery address.",
        "Enter the business delivery address.",
    }
    assert all(
        "requestKey" not in item and "inputRequests" not in item for item in params
    )
    prompt_ids = {frame.get("id") for frame in _mrtr_prompts(native)}
    request_events = [
        index
        for index, frame in enumerate(native)
        if frame.get("method") == "mcpServer/elicitation/request"
        and not _is_tool_approval(frame)
    ]
    resolution_events = [
        index
        for index, frame in enumerate(native)
        if frame.get("method") == "serverRequest/resolved"
        and frame.get("params", {}).get("requestId") in prompt_ids
    ]
    assert len(request_events) == len(resolution_events) == 2
    assert max(request_events) < min(resolution_events), (
        "Codex should emit every native request in this input-required round before any answer resolves"
    )
    assert len(calls) == 2
    responses = calls[-1]["params"]["inputResponses"]
    assert set(responses) == {"home_address", "business_address"}
    assert responses["home_address"]["content"] == {"city": "Pune"}
    assert responses["business_address"]["content"] == {"city": "Mumbai"}


@pytest.mark.asyncio
async def test_real_codex_surfaces_url_and_empty_form_requests(
    tmp_path: Path,
) -> None:
    url_native, url_calls, _provider = await _run_turn(
        tmp_path / "url",
        tool="url_round",
        response_meta={"fixture/response": "url"},
    )
    url_prompts = _mrtr_prompts(url_native)
    assert len(url_prompts) == 1
    url_params = url_prompts[0]["params"]
    assert url_params["mode"] == "url"
    assert url_params["message"] == "Continue checkout."
    assert url_params["url"] == "https://example.test/checkout/123"
    assert url_params["elicitationId"] == "checkout-123"
    assert url_calls[-1]["params"]["inputResponses"] == {
        "checkout": {
            "action": "accept",
            "content": {},
            "_meta": {"fixture/response": "url"},
        }
    }

    empty_native, empty_calls, _provider = await _run_turn(
        tmp_path / "empty", tool="empty_form", answers={"Continue?": {}}
    )
    empty_prompts = _mrtr_prompts(empty_native)
    assert len(empty_prompts) == 1
    assert empty_prompts[0]["params"]["requestedSchema"] == {
        "type": "object",
        "properties": {},
    }
    assert empty_calls[-1]["params"]["inputResponses"] == {
        "confirm": {"action": "accept", "content": {}}
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,mode,message,key",
    [
        ("form_round", "form", "Enter the delivery address.", "shipping_address"),
        ("url_round", "url", "Continue checkout.", "checkout"),
    ],
)
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_real_codex_forwards_non_accept_form_and_url_responses(
    tmp_path: Path,
    tool: str,
    mode: str,
    message: str,
    key: str,
    action: str,
) -> None:
    response_meta = {"fixture/response": action}
    native, calls, _provider = await _run_turn(
        tmp_path,
        tool=tool,
        response_actions={message: action},
        response_meta=response_meta,
    )

    prompts = _mrtr_prompts(native)
    assert len(prompts) == 1
    assert prompts[0]["params"]["mode"] == mode
    assert len(calls) == 2
    retry = calls[-1]["params"]
    assert retry["inputResponses"] == {key: {"action": action, "_meta": response_meta}}
    assert retry["requestState"] == ("form-state" if mode == "form" else "url-state")


@pytest.mark.asyncio
async def test_real_codex_preserves_server_metadata_for_identical_requests(
    tmp_path: Path,
) -> None:
    native, calls, _provider = await _run_turn(
        tmp_path,
        tool="identical_round",
        answers={
            "first_address": {"city": "Pune"},
            "second_address": {"city": "Mumbai"},
        },
    )
    prompts = _mrtr_prompts(native)
    assert len(prompts) == 2
    assert [frame["params"]["message"] for frame in prompts] == [
        "Enter an address.",
        "Enter an address.",
    ]
    assert {frame["params"]["_meta"]["fixture/request_id"] for frame in prompts} == {
        "first_address",
        "second_address",
    }
    assert len(calls) == 2
    responses = calls[-1]["params"]["inputResponses"]
    assert responses["first_address"]["content"] == {"city": "Pune"}
    assert responses["second_address"]["content"] == {"city": "Mumbai"}


@pytest.mark.asyncio
async def test_real_codex_keeps_separate_input_required_rounds_separate(
    tmp_path: Path,
) -> None:
    native, calls, provider = await _run_turn(
        tmp_path,
        tool="multi_round",
        answers={
            "Enter an address.": {"city": "Pune"},
            "Enter a contact name.": {"name": "Ada"},
        },
    )
    prompts = _mrtr_prompts(native)
    assert [frame["params"]["message"] for frame in prompts] == [
        "Enter an address.",
        "Enter a contact name.",
    ]
    assert len(calls) == 3
    assert calls[1]["params"]["requestState"] == "multi-address-state"
    assert calls[1]["params"]["inputResponses"] == {
        "address": {"action": "accept", "content": {"city": "Pune"}}
    }
    assert calls[2]["params"]["requestState"] == "multi-contact-state"
    assert calls[2]["params"]["inputResponses"] == {
        "contact": {"action": "accept", "content": {"name": "Ada"}}
    }
    wait_for_requests(provider, 2)


@pytest.mark.asyncio
async def test_real_codex_handles_nine_rounds_and_rejects_a_tenth(
    tmp_path: Path,
) -> None:
    supported_count = 9
    native, calls, provider = await _run_turn(
        tmp_path / "nine-rounds",
        tool="many_rounds",
        arguments={"count": supported_count},
        answers={f"round-{index}": {} for index in range(1, supported_count + 1)},
    )
    prompts = _mrtr_prompts(native)
    assert [frame["params"]["message"] for frame in prompts] == [
        f"round-{index}" for index in range(1, supported_count + 1)
    ]
    assert len(calls) == supported_count + 1
    for index in range(1, supported_count + 1):
        retry = calls[index]["params"]
        assert retry["requestState"] == str(index)
        assert retry["inputResponses"] == {
            f"round-{index}": {"action": "accept", "content": {}}
        }
    wait_for_requests(provider, 2)
    completed_tool = next(
        frame["params"]["item"]
        for frame in native
        if frame.get("method") == "item/completed"
        and frame.get("params", {}).get("item", {}).get("type") == "mcpToolCall"
    )
    assert completed_tool["status"] == "completed"

    rejected_count = 10
    native, calls, provider = await _run_turn(
        tmp_path / "ten-rounds",
        tool="many_rounds",
        arguments={"count": rejected_count},
        answers={f"round-{index}": {} for index in range(1, rejected_count + 1)},
    )
    prompts = _mrtr_prompts(native)
    assert [frame["params"]["message"] for frame in prompts] == [
        f"round-{index}" for index in range(1, rejected_count)
    ]
    assert len(calls) == rejected_count
    assert calls[-1]["params"]["requestState"] == "9"
    assert calls[-1]["params"]["inputResponses"] == {
        "round-9": {"action": "accept", "content": {}}
    }
    failed_tool = next(
        frame["params"]["item"]
        for frame in native
        if frame.get("method") == "item/completed"
        and frame.get("params", {}).get("item", {}).get("type") == "mcpToolCall"
    )
    assert failed_tool["status"] == "failed"
    assert failed_tool["error"]["message"].endswith(
        "input_required did not complete within 10 MRTR rounds"
    )
    wait_for_requests(provider, 2)


@pytest.mark.asyncio
async def test_real_codex_retries_state_only_input_required_without_native_prompt(
    tmp_path: Path,
) -> None:
    native, calls, _provider = await _run_turn(tmp_path, tool="state_only")
    assert not _mrtr_prompts(native)
    assert len(calls) == 2
    assert calls[1]["params"]["requestState"] == "state-only-state"
    assert "inputResponses" not in calls[1]["params"]


@pytest.mark.asyncio
async def test_real_codex_emits_distinct_native_approval_metadata(
    tmp_path: Path,
) -> None:
    native, calls, _provider = await _run_turn(tmp_path, tool="approval_probe")
    assert calls
    assert calls[-1]["params"]["name"] == "approval_probe"
    assert any(
        isinstance(frame.get("params"), dict)
        and frame["params"].get("_meta", {}).get("codex_approval_kind")
        == "mcp_tool_call"
        for frame in native
    )


@pytest.mark.asyncio
async def test_real_codex_interrupt_before_answer_prevents_mcp_retry(
    tmp_path: Path,
) -> None:
    native, calls, _provider = await _run_turn(
        tmp_path,
        tool="form_round",
        interrupt_first=True,
    )
    assert _mrtr_prompts(native)
    assert len(calls) == 1
    completed = next(
        frame for frame in native if frame.get("method") == "turn/completed"
    )
    assert completed["params"]["turn"]["status"] == "interrupted"
    assert not any(
        frame.get("method") == "serverRequest/resolved"
        and frame.get("params", {}).get("requestId") == 1
        for frame in native
    )
