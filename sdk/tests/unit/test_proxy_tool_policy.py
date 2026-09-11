from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from mcp_pal.server_group import HarnessServerConfig
from mcp_pal.transport.capture_proxy import McpCaptureManager
from mcp_pal.transport.http_proxy import McpHttpProxy
from mcp_pal.transport.tool_policy import ProxyToolPolicy
from mcp_pal.types import RestrictiveToolPolicy, TransportKind


def test_proxy_gate_qualifies_and_denies_without_arguments() -> None:
    gate = ProxyToolPolicy(
        RestrictiveToolPolicy(allowed_tools=("one:read",)),
        server="one",
        known_servers=("one", "two"),
        known_tools=("read",),
    )
    assert gate.decide("read")[0] is True
    denied, reason = ProxyToolPolicy(
        RestrictiveToolPolicy(allowed_tools=("one:read",)),
        server="one",
    ).decide("delete")
    assert denied is False
    assert reason == "tool denied by restrictive policy"


def test_empty_restrictive_policy_is_deny_all() -> None:
    denied, reason = ProxyToolPolicy(RestrictiveToolPolicy(), server="one").decide(
        "anything"
    )
    assert denied is False
    assert reason == "tool denied by restrictive policy"


def test_full_and_native_policies_are_separate() -> None:
    from mcp_pal.types import FullToolPolicy, NativeToolPolicy

    allowed, _ = ProxyToolPolicy(
        FullToolPolicy(acknowledge_risk=True), server="one"
    ).decide("anything")
    assert allowed is True
    native = ProxyToolPolicy(
        NativeToolPolicy(
            harness="acp", policy={"allow": "all"}, nonportable_reason="provider"
        ),
        server="one",
    )
    assert native.decide("anything") == (False, "policy_unavailable")


def test_proxy_gate_rejects_ambiguous_unqualified_name() -> None:
    gate = ProxyToolPolicy(
        RestrictiveToolPolicy(allowed_tools=("read",)),
        server="one",
        known_servers=("one", "two"),
        known_tools=("read",),
    )
    assert gate.decide("read") == (False, "ambiguous_tool")


def test_independent_dynamic_inventories_fail_closed_and_ignore_unmatched_tools_list() -> (
    None
):
    policy = RestrictiveToolPolicy(allowed_tools=("read",))
    one = ProxyToolPolicy(policy, server="one", known_servers=("one", "two"))
    two = ProxyToolPolicy(policy, server="two", known_servers=("one", "two"))
    one.observe_request({"id": 1, "method": "tools/list"})
    two.observe_request({"id": 1, "method": "tools/list"})
    one.observe({"id": 99, "result": {"tools": [{"name": "read"}]}})
    two.observe({"id": 99, "result": {"tools": [{"name": "read"}]}})
    assert one.decide("read")[0] is False
    assert two.decide("read")[0] is False
    one.observe({"id": 1, "result": {"tools": [{"name": "read"}]}})
    assert one.decide("read")[0] is False


def test_learned_inventory_rejects_unknown_tool() -> None:
    gate = ProxyToolPolicy(
        RestrictiveToolPolicy(allowed_tools=("one:read",)),
        server="one",
        known_servers=("one",),
        known_tools_by_server={"one": ("read",)},
    )
    assert gate.decide("missing") == (False, "tool_unavailable")


def test_qualified_allow_survives_paginated_tools_list_and_denies_other_tool() -> None:
    gate = ProxyToolPolicy(
        RestrictiveToolPolicy(allowed_tools=("e2e-mcp:echo",)),
        server="e2e-mcp",
        known_servers=("e2e-mcp",),
    )
    gate.observe_request({"id": 1, "method": "tools/list"})
    gate.observe(
        {"id": 1, "result": {"tools": [{"name": "echo"}], "nextCursor": "page-2"}}
    )
    gate.observe_request(
        {"id": 2, "method": "tools/list", "params": {"cursor": "page-2"}}
    )
    gate.observe({"id": 2, "result": {"tools": [{"name": "failure"}]}})
    assert gate.decide("echo") == (True, "allowed")
    assert gate.decide("failure") == (False, "tool denied by restrictive policy")


def test_first_tools_list_page_refreshes_stale_inventory() -> None:
    gate = ProxyToolPolicy(
        RestrictiveToolPolicy(allowed_tools=("e2e-mcp:echo",)),
        server="e2e-mcp",
        known_servers=("e2e-mcp",),
    )
    gate.observe_request({"id": 1, "method": "tools/list"})
    gate.observe({"id": 1, "result": {"tools": [{"name": "echo"}]}})
    assert gate.decide("echo") == (True, "allowed")
    gate.observe_request({"id": 2, "method": "tools/list"})
    gate.observe({"id": 2, "result": {"tools": [{"name": "failure"}]}})
    assert gate.decide("echo") == (False, "tool_unavailable")


def test_unsafe_tool_identity_fails_closed_without_validation_error() -> None:
    gate = ProxyToolPolicy(
        RestrictiveToolPolicy(allowed_tools=("one:read",)), server="one"
    )
    assert gate.decide("bad\x01name") == (False, "tool_identity_invalid")


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_policy_is_a_one_shot_handoff(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated.policy.json"
    unrelated.write_text("keep", encoding="utf-8")
    manager = McpCaptureManager(
        tmp_path,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:read",)),
        server_aliases=("echo", "other"),
    )
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection-echo",
        command=sys.executable,
    )
    instrumented = (await manager.instrument((config,)))[0]
    policy_path = Path(instrumented.args[instrumented.args.index("--policy-file") + 1])
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    assert payload["server"] == "echo"
    assert payload["policy"]["allowed_tools"] == ["echo:read"]
    await manager.close()
    assert not policy_path.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_capture_reports_policy_proof_only_for_enforcing_transports(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(
        tmp_path,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:read",)),
        server_aliases=("echo",),
    )
    in_process = HarnessServerConfig(
        key="echo",
        transport=TransportKind.IN_PROCESS,
        required=True,
        available=True,
        connection_id="not-proxied",
        endpoint="http://127.0.0.1/loopback",
    )
    await manager.instrument((in_process,))
    assert manager.enforces_portable_policy(("not-proxied",)) is False
    assert manager.enforces_portable_policy(()) is False
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_policy_handoff_refuses_a_preexisting_symlink(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text("unchanged", encoding="utf-8")
    root = tmp_path / "capture"
    root.mkdir()
    policy_path = root / "connection-echo.policy.json"
    try:
        policy_path.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    manager = McpCaptureManager(
        root,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:read",)),
        server_aliases=("echo",),
    )
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection-echo",
        command=sys.executable,
    )
    with pytest.raises(FileExistsError):
        await manager.instrument((config,))
    assert target.read_text(encoding="utf-8") == "unchanged"
    await manager.close()
    assert policy_path.is_symlink()


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_policy_blocks_child_before_side_effect_and_returns_jsonrpc_error(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "called"
    child = tmp_path / "child.py"
    child.write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " p=json.loads(line)\n"
        " if p.get('method') == 'tools/call': open("
        + repr(str(marker))
        + ", 'w').write('called')\n"
        " print(json.dumps({'jsonrpc':'2.0','id':p.get('id'),'result':{}}), flush=True)\n",
        encoding="utf-8",
    )
    manager = McpCaptureManager(
        tmp_path / "capture",
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:allowed",)),
        server_aliases=("echo",),
    )
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection-echo",
        command=sys.executable,
        args=(str(child),),
    )
    instrumented = (await manager.instrument((config,)))[0]
    env = os.environ.copy()
    process = subprocess.Popen(
        (instrumented.command, *instrumented.args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "blocked",
                    "arguments": {"secret": "must-not-capture"},
                },
            }
        )
        + "\n"
    )
    process.stdin.close()
    output = process.stdout.readline()
    process.wait(timeout=5)
    assert json.loads(output)["error"]["code"] == -32001
    assert not marker.exists()
    capture = (tmp_path / "capture" / "connection-echo.jsonl").read_text(
        encoding="utf-8"
    )
    assert "must-not-capture" not in capture
    await manager.close()


@pytest.mark.asyncio
async def test_http_policy_denies_before_upstream_and_preserves_id(
    tmp_path: Path,
) -> None:
    proxy = McpHttpProxy(
        upstream_url="http://127.0.0.1:9/mcp",
        configured_headers={},
        transport="streamable_http",
        capture_path=str(tmp_path / "capture.jsonl"),
        baseline_ns=0,
        allow_private=True,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:allowed",)),
        server_alias="echo",
    )

    class NeverClient:
        def build_request(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("denied request reached upstream")

    proxy.client = NeverClient()  # type: ignore[assignment]
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "request-7",
            "method": "tools/call",
            "params": {"name": "blocked", "arguments": {"secret": "not-captured"}},
        }
    ).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
            "scheme": "http",
            "server": ("127.0.0.1", 1),
            "client": ("127.0.0.1", 1),
        },
        receive=receive,
    )
    response = await proxy._forward(request)
    assert response.status_code == 200
    response_payload = json.loads(bytes(response.body))
    assert response_payload["id"] == "request-7"
    assert response_payload["error"]["code"] == -32001
    assert "not-captured" not in (tmp_path / "capture.jsonl").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_http_policy_batch_and_notification_fail_closed(tmp_path: Path) -> None:
    proxy = McpHttpProxy(
        upstream_url="http://127.0.0.1:9/mcp",
        configured_headers={},
        transport="streamable_http",
        capture_path=str(tmp_path / "batch.jsonl"),
        baseline_ns=0,
        allow_private=True,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:allowed",)),
        server_alias="echo",
    )

    class NeverClient:
        def build_request(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("denied request reached upstream")

    proxy.client = NeverClient()  # type: ignore[assignment]

    async def run(value: object) -> Any:
        body = json.dumps(value).encode()

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("127.0.0.1", 1),
                "client": ("127.0.0.1", 1),
            },
            receive=receive,
        )
        return await proxy._forward(request)

    batch = await run(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "blocked", "arguments": {"secret": "batch-secret"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "allowed", "arguments": {}},
            },
        ]
    )
    assert [item["id"] for item in json.loads(bytes(batch.body))] == [1, 2]
    notification = await run(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "blocked",
                "arguments": {"secret": "notification-secret"},
            },
        }
    )
    assert bytes(notification.body) == b""
    assert notification.status_code == 202
    batch_notification = await run(
        [
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "blocked",
                    "arguments": {"secret": "batch-notification-secret"},
                },
            }
        ]
    )
    assert bytes(batch_notification.body) == b""
    assert batch_notification.status_code == 202
    text = (tmp_path / "batch.jsonl").read_text(encoding="utf-8")
    assert (
        "batch-secret" not in text
        and "notification-secret" not in text
        and "batch-notification-secret" not in text
    )


def test_http_proxy_rewrites_same_origin_redirect_and_drops_cross_origin(
    tmp_path: Path,
) -> None:
    from httpx import Response

    proxy = McpHttpProxy(
        upstream_url="https://mcp.example.test/mcp",
        configured_headers={},
        transport="streamable_http",
        capture_path=str(tmp_path / "redirect.jsonl"),
        baseline_ns=0,
        allow_private=True,
    )
    proxy.proxy_origin = "http://127.0.0.1:43210"
    same = proxy._response_headers(
        Response(307, headers={"location": "https://mcp.example.test/mcp/"})
    )
    assert same["location"] == "http://127.0.0.1:43210/mcp/"
    cross = proxy._response_headers(
        Response(307, headers={"location": "https://evil.example/mcp"})
    )
    assert "location" not in cross
    malformed = proxy._response_headers(
        Response(307, headers={"location": "https://[bad"})
    )
    assert "location" not in malformed


@pytest.mark.asyncio
async def test_redirect_followup_stays_captured_and_policy_gated(
    tmp_path: Path,
) -> None:
    import httpx

    from mcp_pal.trace.capture import read_capture

    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/mcp":
            return httpx.Response(307, headers={"location": "/mcp/"})
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 2, "result": {"content": []}}
        )

    path = tmp_path / "redirect.jsonl"
    proxy = McpHttpProxy(
        upstream_url="https://mcp.example.test/mcp",
        configured_headers={},
        transport="streamable_http",
        capture_path=str(path),
        baseline_ns=0,
        allow_private=True,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:allowed",)),
        server_alias="echo",
    )
    endpoint = await proxy.start()
    await proxy.client.aclose()  # type: ignore[union-attr]
    proxy.client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream), follow_redirects=False
    )
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            first = await client.post(
                endpoint,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert first.status_code == 307
            assert first.headers["location"].startswith(endpoint.rsplit("/mcp", 1)[0])
            allowed = await client.post(
                first.headers["location"],
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "allowed", "arguments": {}},
                },
            )
            assert allowed.status_code == 200
        assert seen == ["/mcp", "/mcp/"]
        records = read_capture(str(path))
        assert any(
            record["payload"].get("method") == "tools/call" for record in records
        )
    finally:
        await proxy.stop()

    blocked_seen: list[str] = []

    def blocked_upstream(request: httpx.Request) -> httpx.Response:
        blocked_seen.append(request.url.path)
        if request.url.path == "/mcp":
            return httpx.Response(307, headers={"location": "/mcp/"})
        return httpx.Response(500)

    blocked_path = tmp_path / "blocked-redirect.jsonl"
    blocked_proxy = McpHttpProxy(
        upstream_url="https://mcp.example.test/mcp",
        configured_headers={},
        transport="streamable_http",
        capture_path=str(blocked_path),
        baseline_ns=0,
        allow_private=True,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("echo:allowed",)),
        server_alias="echo",
    )
    blocked_endpoint = await blocked_proxy.start()
    await blocked_proxy.client.aclose()  # type: ignore[union-attr]
    blocked_proxy.client = httpx.AsyncClient(
        transport=httpx.MockTransport(blocked_upstream), follow_redirects=False
    )
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            first = await client.post(
                blocked_endpoint,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            denied = await client.post(
                first.headers["location"],
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "blocked", "arguments": {}},
                },
            )
            assert denied.status_code == 200
            assert json.loads(denied.text)["error"]["code"] == -32001
        assert blocked_seen == ["/mcp"]
        records = read_capture(str(blocked_path))
        assert any(
            record.get("kind") == "policy_denied"
            and record["payload"].get("method") == "tools/call"
            for record in records
        )
    finally:
        await blocked_proxy.stop()
