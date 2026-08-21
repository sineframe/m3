from mcp_pal.domain.events import normalize_events
import asyncio
import time

from mcp_pal.trace.claude import build_claude_trace, transport_for_server
from mcp_pal.trace.redaction import redact
from mcp_pal.transport.http_proxy import McpHttpProxy


def test_transport_detection_and_partial_stream_events():
    assert transport_for_server({"command": "server"}) == "stdio"
    assert transport_for_server({"type": "http", "url": "https://example.test/mcp"}) == "http"
    assert transport_for_server({"type": "sse", "url": "https://example.test/sse"}) == "sse"
    raw = {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}}
    assert normalize_events(raw)[0][0] == "assistant_text"


def test_trace_has_transport_usage_and_protocol_correlation():
    events = [
        {"type": "assistant", "offset_ms": 2, "raw_event": {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "u1", "name": "mcp__draw__create", "input": {"x": 1}}], "usage": {"input_tokens": 4, "output_tokens": 2}}}},
        {"type": "result", "offset_ms": 8, "raw_event": {"type": "result", "result": "ok", "duration_ms": 8, "total_cost_usd": 0.01}},
    ]
    protocol = [
        {"offset_ms": 3, "transport": "http", "direction": "client_to_server", "payload": {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "create", "arguments": {}}}},
        {"offset_ms": 6, "transport": "http", "direction": "server_to_client", "payload": {"jsonrpc": "2.0", "id": 7, "result": {"content": []}}},
    ]
    trace = build_claude_trace(events=events, protocol_events=protocol, transport="http")
    assert trace["summary"]["transport"] == "http"
    assert trace["summary"]["total_tokens"] == 6
    assert any(span.get("metadata", {}).get("correlation") == "heuristic" for span in trace["spans"])
    assert trace["protocol_events"][0]["sequence"] == 1
    session = next(span for span in trace["spans"] if span["kind"] == "mcp_session")
    assert (session["start_ms"], session["end_ms"], session["duration_ms"]) == (3, 6, 3)


def test_protocol_notification_uses_its_method_as_the_label():
    trace = build_claude_trace(
        events=[],
        protocol_events=[
            {
                "offset_ms": 12,
                "transport": "stdio",
                "direction": "server_to_client",
                "payload": {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
            }
        ],
        transport="stdio",
    )

    event = next(span for span in trace["spans"] if span["kind"] == "mcp_event")
    assert event["name"] == "notifications/tools/list_changed"


def test_redaction_covers_nested_credentials_and_urls():
    value, paths = redact({"headers": {"Authorization": "Bearer abc"}, "url": "https://example.test/mcp?token=abc"}, secrets=set())
    assert value["headers"]["Authorization"] == "[REDACTED]"
    assert "token=%5BREDACTED%5D" in value["url"]
    assert paths


def test_partial_and_complete_message_are_one_turn_with_one_usage_total():
    raws = [
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 5}}}},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}}},
        {"type": "stream_event", "event": {"type": "message_delta", "usage": {"output_tokens": 3}}},
        {"type": "stream_event", "event": {"type": "message_stop"}},
        {"type": "assistant", "message": {"id": "msg_1", "content": [{"type": "text", "text": "hello"}], "usage": {"input_tokens": 5, "output_tokens": 3}}},
        {"type": "result", "result": "hello"},
    ]
    trace = build_claude_trace(events=[{"type": x["type"], "offset_ms": i, "raw_event": x} for i, x in enumerate(raws)], transport="sse")
    assert trace["summary"]["turns"] == 1
    assert trace["summary"]["input_tokens"] == 5
    assert trace["summary"]["output_tokens"] == 3
    assert trace["summary"]["total_tokens"] == 8
    assert len([x for x in trace["spans"] if x["kind"] == "model_turn"]) == 1
    assert [x["output"] for x in trace["spans"] if x["kind"] == "text"] == ["hello"]
    turn = next(x for x in trace["spans"] if x["kind"] == "model_turn")
    assert turn["start_ms"] == 0 and turn["end_ms"] == 4


def test_content_block_timing_is_not_stretched_by_complete_message_replay():
    raws = [
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg_1"}}},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}}},
        {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
        {"type": "stream_event", "event": {"type": "message_stop"}},
        {"type": "assistant", "message": {"id": "msg_1", "content": [{"type": "text", "text": "hello"}]}},
    ]
    offsets = [0, 10, 20, 30, 40, 50]

    trace = build_claude_trace(
        events=[{"type": raw["type"], "offset_ms": offset, "raw_event": raw} for raw, offset in zip(raws, offsets)],
        transport="stdio",
    )

    text = next(span for span in trace["spans"] if span["kind"] == "text")
    assert (text["start_ms"], text["end_ms"], text["duration_ms"]) == (10, 30, 20)
    assert text["output"] == "hello"


def test_tool_call_span_covers_round_trip_duration():
    raws = [
        {"type": "assistant", "message": {"id": "msg_1", "content": [{"type": "tool_use", "id": "tool_1", "name": "mcp__draw__create", "input": {"x": 1}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": [{"type": "text", "text": "ok"}]}]}},
    ]
    trace = build_claude_trace(events=[{"type": x["type"], "offset_ms": i * 10, "raw_event": x} for i, x in enumerate(raws)], transport="stdio")
    calls = [x for x in trace["spans"] if x["kind"] == "tool_call"]
    assert len(calls) == 1 and calls[0]["duration_ms"] == 10
    assert calls[0]["metadata"]["correlation"] == "exact"


def test_sse_crlf_and_split_utf8_are_preserved(tmp_path):
    class Response:
        async def aiter_bytes(self):
            payload = 'data: {"message":"café"}\r\n\r\n'.encode("utf-8")
            for chunk in (payload[:12], payload[12:13], payload[13:]):
                yield chunk
        async def aclose(self):
            return None

    proxy = McpHttpProxy(upstream_url="https://example.test/mcp", configured_headers=None, transport="sse", capture_path=str(tmp_path / "capture.jsonl"), baseline_ns=time.perf_counter_ns(), allow_private=True)
    async def collect():
        return b"".join([chunk async for chunk in proxy._stream_sse(Response())])
    output = asyncio.run(collect())
    assert "café" in output.decode("utf-8")


def test_thinking_signature_is_classified_but_not_rendered():
    raws = [
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg_1"}}},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "private-signature"}}},
        {"type": "stream_event", "event": {"type": "message_stop"}},
    ]
    trace = build_claude_trace(events=[{"type": x["type"], "offset_ms": i, "raw_event": x} for i, x in enumerate(raws)])
    thinking = [x for x in trace["spans"] if x["kind"] == "thinking"]
    assert len(thinking) == 1 and thinking[0]["output"] in (None, "")
    assert trace["summary"]["thinking"] == {"state": "encrypted", "count": 1}
