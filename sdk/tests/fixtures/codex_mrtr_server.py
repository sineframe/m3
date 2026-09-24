"""Raw stdio MCP peer used to characterize Codex's installed app-server.

It records the negotiated protocol and every tool request exactly as received,
then returns deterministic MCP 2026-07-28 input-required rounds. This fixture is
deliberately independent of m3's transport code so the real Codex client path is
what the tests exercise.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "street": {"type": "string"},
        "city": {"type": "string"},
        "postal_code": {"type": "string"},
    },
    "required": ["street", "city", "postal_code"],
}
EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _write(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _record(value: dict[str, Any]) -> None:
    marker = os.environ.get("M3_CODEX_MRTR_WIRE_MARKER")
    if marker:
        with Path(marker).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, separators=(",", ":")) + "\n")


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "form_round",
            "description": "Collect a shipping address form.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "url_round",
            "description": "Confirm a checkout URL.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "same_round",
            "description": "Collect two distinct address forms together.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "identical_round",
            "description": "Collect two identical forms together.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "empty_form",
            "description": "Collect an empty form.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "state_only",
            "description": "Collect an input-required round with no elicitation requests.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "multi_round",
            "description": "Collect two separate form rounds.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "many_rounds",
            "description": "Collect a controlled number of form rounds.",
            "inputSchema": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
        },
        {
            "name": "approval_probe",
            "description": "A destructive action used to inspect approval routing.",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
    ]


def _form(
    message: str,
    schema: dict[str, Any] = EMPTY_SCHEMA,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = {
        "mode": "form",
        "message": message,
        "requestedSchema": schema,
    }
    if meta is not None:
        params["_meta"] = meta
    return {"method": "elicitation/create", "params": params}


def _url(message: str) -> dict[str, Any]:
    return {
        "method": "elicitation/create",
        "params": {
            "mode": "url",
            "message": message,
            "url": "https://example.test/checkout/123",
            "elicitationId": "checkout-123",
        },
    }


def _input_required(
    request_id: object,
    input_requests: dict[str, dict[str, Any]],
    request_state: str,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "resultType": "input_required",
            "inputRequests": input_requests,
            "requestState": request_state,
            "_meta": {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "m3-codex-mrtr",
                    "version": "1.0.0",
                }
            },
        },
    }


def _complete(request_id: object, structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "resultType": "complete",
            "content": [{"type": "text", "text": "complete"}],
            "structuredContent": structured,
            "isError": False,
        },
    }


def _server_error(request_id: object, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32602, "message": message},
    }


def _call_tool(request: dict[str, Any]) -> dict[str, Any]:
    request_id = request.get("id")
    params = request.get("params")
    params = params if isinstance(params, dict) else {}
    name = params.get("name")
    arguments = params.get("arguments")
    arguments = arguments if isinstance(arguments, dict) else {}
    state = params.get("requestState")
    responses = params.get("inputResponses")
    responses = responses if isinstance(responses, dict) else {}
    record = {
        "method": "tools/call",
        "id": request_id,
        "params": params,
        "observedAt": time.monotonic_ns(),
    }
    _record(record)

    if name == "approval_probe":
        return _complete(request_id, {"executed": True})
    if name == "state_only" and state is None:
        return _input_required(request_id, {}, "state-only-state")
    if name == "form_round" and state is None:
        return _input_required(
            request_id,
            {"shipping_address": _form("Enter the delivery address.", ADDRESS_SCHEMA)},
            "form-state",
        )
    if name == "url_round" and state is None:
        return _input_required(
            request_id, {"checkout": _url("Continue checkout.")}, "url-state"
        )
    if name == "same_round" and state is None:
        return _input_required(
            request_id,
            {
                "home_address": _form(
                    "Enter the home delivery address.", ADDRESS_SCHEMA
                ),
                "business_address": _form(
                    "Enter the business delivery address.", ADDRESS_SCHEMA
                ),
            },
            "same-round-state",
        )
    if name == "identical_round" and state is None:
        return _input_required(
            request_id,
            {
                "first_address": _form(
                    "Enter an address.",
                    ADDRESS_SCHEMA,
                    {"fixture/request_id": "first_address"},
                ),
                "second_address": _form(
                    "Enter an address.",
                    ADDRESS_SCHEMA,
                    {"fixture/request_id": "second_address"},
                ),
            },
            "identical-round-state",
        )
    if name == "empty_form" and state is None:
        return _input_required(
            request_id,
            {"confirm": _form("Continue?", EMPTY_SCHEMA)},
            "empty-form-state",
        )
    if name == "multi_round":
        if state is None:
            return _input_required(
                request_id,
                {"address": _form("Enter an address.", ADDRESS_SCHEMA)},
                "multi-address-state",
            )
        if state == "multi-address-state":
            if set(responses) != {"address"}:
                return _server_error(
                    request_id, "expected only the current address response"
                )
            return _input_required(
                request_id,
                {"contact": _form("Enter a contact name.", EMPTY_SCHEMA)},
                "multi-contact-state",
            )
        if state == "multi-contact-state":
            if set(responses) != {"contact"}:
                return _server_error(request_id, "previous-round response leaked")
            return _complete(request_id, {"status": "complete"})
    if name == "many_rounds":
        count = arguments.get("count", 10)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return _server_error(request_id, "count must be a positive integer")
        round_index = 1 if state is None else int(str(state)) + 1
        if round_index <= count:
            expected_previous = (
                set() if round_index == 1 else {f"round-{round_index - 1}"}
            )
            if set(responses) != expected_previous:
                return _server_error(request_id, "previous-round response leaked")
            key = f"round-{round_index}"
            return _input_required(
                request_id, {key: _form(key, EMPTY_SCHEMA)}, str(round_index)
            )
        return _complete(request_id, {"rounds": count})

    if state is not None and name in {
        "form_round",
        "url_round",
        "same_round",
        "identical_round",
        "empty_form",
    }:
        return _complete(request_id, {"responses": responses})
    return _complete(request_id, {"status": "complete"})


def main() -> None:
    _record(
        {
            "method": "server/startup",
            "protocolMarker": os.environ.get("CODEX_MCP_PROTOCOL_VERSION"),
        }
    )
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        method = message.get("method")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "initialize":
            _record({"method": "initialize", "params": params})
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "m3-codex-mrtr", "version": "1.0.0"},
                    },
                }
            )
        elif method == "server/discover":
            _record({"method": method, "params": params})
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "resultType": "complete",
                        "supportedVersions": ["2026-07-28"],
                        "capabilities": {"tools": {}, "resources": {}},
                        "_meta": {
                            "io.modelcontextprotocol/serverInfo": {
                                "name": "m3-codex-mrtr",
                                "version": "1.0.0",
                            }
                        },
                        "ttlMs": 0,
                        "cacheScope": "private",
                    },
                }
            )
        elif method == "notifications/initialized":
            _record({"method": method, "params": params})
        elif method == "ping":
            _write({"jsonrpc": "2.0", "id": message.get("id"), "result": {}})
        elif method == "tools/list":
            _record({"method": method, "params": params})
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "resultType": "complete",
                        "tools": _tools(),
                        "ttlMs": 0,
                        "cacheScope": "private",
                    },
                }
            )
        elif method == "tools/call":
            _write(_call_tool(message))
        elif isinstance(message.get("id"), (str, int)) and not isinstance(
            message.get("id"), bool
        ):
            _write(_server_error(message.get("id"), f"unsupported method: {method}"))
        else:
            _record({"method": str(method), "params": params})


if __name__ == "__main__":
    main()
