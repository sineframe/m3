"""`m3 lint` command behaviour over stdio and streamable HTTP."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from m3_cli import main
from m3_cli.lint.rules import RULES

LINT_SERVER = Path(__file__).parent / "fixtures" / "lint_server.py"
SERVER = [sys.executable, str(LINT_SERVER)]
CTL = {
    "name": "ctl",
    "description": "Control tool",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "annotations": {"readOnlyHint": True},
}
NO_ROOT_TYPE = {
    "name": "probe",
    "description": "Probe tool",
    "inputSchema": {"properties": {"q": {"type": "string"}}},
    "annotations": {"readOnlyHint": True},
}


@pytest.fixture
def lint_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def write(spec: dict[str, Any]) -> Path:
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        monkeypatch.setenv("LINT_SPEC", str(path))
        return path

    return write


def _rules(report: dict[str, Any]) -> list[str]:
    return [finding["rule"] for finding in report["findings"]]


# --- usage errors --------------------------------------------------------------


def test_unknown_harness_is_rejected_without_echo(capsys, lint_spec) -> None:
    lint_spec({"tools": [CTL]})
    assert main(["lint", "--harness", "opencode@9.9.9", "--", *SERVER]) == 2
    captured = capsys.readouterr()
    assert captured.err == "m3 lint: m3 lint supports claude_code and codex\n"
    assert "opencode" not in captured.out + captured.err
    assert "9.9.9" not in captured.out + captured.err


@pytest.mark.parametrize(
    "argv",
    [["lint", "http://127.0.0.1:1/mcp", "--", *SERVER], ["lint"]],
    ids=["both", "neither"],
)
def test_target_must_be_url_or_command(argv: list[str], capsys) -> None:
    assert main(argv) == 2
    assert (
        capsys.readouterr().err == "m3 lint: provide a URL or -- COMMAND (not both)\n"
    )


def test_header_needs_url_target(capsys) -> None:
    assert main(["lint", "--header", "X:1", "--", *SERVER]) == 2
    assert capsys.readouterr().err == "m3 lint: --header applies only to URL targets\n"


@pytest.mark.parametrize(
    "header", ["no-colon-secret", "Bad Name: secret", ": secret", "X: a\x00secret"]
)
def test_invalid_header_is_rejected(header: str, capsys) -> None:
    assert main(["lint", "--header", header, "http://127.0.0.1:1/mcp"]) == 2
    captured = capsys.readouterr()
    assert captured.err == "m3 lint: invalid --header (expected NAME:VALUE)\n"
    assert "secret" not in captured.out + captured.err


def test_unknown_ignore_rule(capsys) -> None:
    assert main(["lint", "--ignore", "no-such-rule", "--", *SERVER]) == 2
    assert capsys.readouterr().err == "m3 lint: unknown rule id in --ignore\n"


def test_timeout_must_be_positive(capsys) -> None:
    assert main(["lint", "--timeout", "0", "--", *SERVER]) == 2
    assert capsys.readouterr().err == "m3 lint: --timeout must be positive\n"
    assert main(["lint", "--timeout", "inf", "--", *SERVER]) == 2
    assert (
        capsys.readouterr().err == "m3 lint: --timeout must be at most 86400 seconds\n"
    )


def test_server_exiting_before_initialize(capsys) -> None:
    command = ["--", sys.executable, "-c", "pass"]
    assert main(["lint", *command]) == 2
    assert (
        capsys.readouterr().err == "m3 lint: server process exited before initialize\n"
    )
    assert main(["lint", "--json", *command]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "server process exited before initialize",
        "exit_code": 2,
    }


def test_missing_server_binary(capsys) -> None:
    assert main(["lint", "--", "/nonexistent/binary"]) == 2
    assert capsys.readouterr().err == "m3 lint: could not start the server command\n"


# --- stdio behaviour ---------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_wrapper_children_are_stopped_with_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, lint_spec
) -> None:
    # The shell stays the parent and its server child inherits stdout; a hung
    # probe makes lint stop the wrapper, which must not leave lint waiting.
    log = tmp_path / "server.log"
    monkeypatch.setenv("LINT_LOG", str(log))
    lint_spec({"tools": [CTL], "discover": "hang"})
    script = f"'{sys.executable}' '{LINT_SERVER}'; true"
    assert main(["lint", "--json", "--timeout", "5", "--", "sh", "-c", script]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["collection"]["discover"] == "timeout"
    assert report["collection"]["tool_count"] == 1


HUGE_INTEGER_SERVER = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    if message["method"] == "initialize":
        reply["result"] = {"protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "huge", "version": "1"}}
    elif message["method"] == "tools/list":
        sys.stdout.write('{"jsonrpc":"2.0","id":%d,"result":{"tools":[{"name":"n",'
            '"description":"d","annotations":{"readOnlyHint":true},'
            '"inputSchema":{"type":"object","properties":{"n":{"type":"integer",'
            '"maximum":1%s}}}}]}}\n' % (message["id"], "0" * 5000))
        sys.stdout.flush()
        continue
    else:
        reply["error"] = {"code": -32601, "message": "Method not found"}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
"""


def test_unparseable_reply_fails_fast_as_invalid(capsys, lint_spec) -> None:
    # NaN is not JSON (Claude Code and Codex both lose the server). The reply
    # carries this request's id, so it fails at once instead of timing out.
    nan_tool = {
        "name": "nan",
        "description": "NaN maximum",
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "number", "maximum": float("nan")}},
        },
        "annotations": {"readOnlyHint": True},
    }
    lint_spec({"tools": [CTL, nan_tool]})
    started = time.monotonic()
    assert main(["lint", "--json", "--timeout", "30", "--", *SERVER]) == 1
    assert time.monotonic() - started < 10
    report = json.loads(capsys.readouterr().out)
    failed = [f for f in report["findings"] if f["rule"] == "tools-list-failed"]
    assert [f["detail"] for f in failed] == ["invalid"]


STALLED_STDIN_SERVER = r"""
import json, sys, time
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    if message["method"] == "initialize":
        reply["result"] = {"protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stall", "version": "1"}}
    elif message["method"] == "tools/list":
        reply["result"] = {"tools": [], "nextCursor": "c" * 1_000_000}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
        time.sleep(60)  # never reads the 1 MB cursor echoed back
    else:
        reply["error"] = {"code": -32601, "message": "Method not found"}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
"""


def test_stalled_server_stdin_respects_the_deadline(capsys) -> None:
    command = ["--", sys.executable, "-c", STALLED_STDIN_SERVER]
    started = time.monotonic()
    assert main(["lint", "--json", "--timeout", "1", *command]) == 1
    assert time.monotonic() - started < 15
    report = json.loads(capsys.readouterr().out)
    failed = [f for f in report["findings"] if f["rule"] == "tools-list-failed"]
    assert [f["detail"] for f in failed] == ["timeout"]


def test_integer_literals_beyond_python_digit_limit(capsys) -> None:
    command = ["--", sys.executable, "-c", HUGE_INTEGER_SERVER]
    assert main(["lint", "--json", "--timeout", "5", *command]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["collection"]["tool_count"] == 1
    assert "tools-list-failed" not in _rules(report)
    assert "integer-literal-unsafe" in _rules(report)


def test_unanswered_discover_respawns_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, lint_spec
) -> None:
    log = tmp_path / "server.log"
    monkeypatch.setenv("LINT_LOG", str(log))
    lint_spec({"tools": [CTL], "discover": "hang"})
    assert main(["lint", "--json", "--timeout", "5", "--", *SERVER]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "discover-probe-unanswered" in _rules(report)
    assert report["collection"]["discover"] == "timeout"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("start ") for line in lines) == 2


def test_initialize_retries_on_a_fresh_connection_after_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, lint_spec
) -> None:
    log = tmp_path / "server.log"
    monkeypatch.setenv("LINT_LOG", str(log))
    lint_spec({"tools": [CTL], "discover": "bind"})
    assert main(["lint", "--json", "--", *SERVER]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["collection"]["discover"] == "result"
    assert report["collection"]["tool_count"] == 1
    lines = log.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("start ") for line in lines) == 2


def test_only_metadata_methods_are_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, lint_spec
) -> None:
    log = tmp_path / "server.log"
    monkeypatch.setenv("LINT_LOG", str(log))
    lint_spec(
        {
            "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
            "tools": [CTL],
            "prompts": [{"name": "p"}],
            "resources": [{"uri": "file:///a", "name": "a"}],
            "resourceTemplates": [{"uriTemplate": "file:///{x}", "name": "x"}],
        }
    )
    assert main(["lint", "--json", "--", *SERVER]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["collection"]["prompt_count"] == 1
    assert report["collection"]["resource_count"] == 1
    assert report["collection"]["template_count"] == 1
    methods = [
        line.split(" ", 1)[1]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("method ")
    ]
    assert methods == [
        "server/discover",
        "initialize",
        "notifications/initialized",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
    ]


def test_noise_on_stdout_is_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, lint_spec
) -> None:
    spec = {"tools": [CTL, NO_ROOT_TYPE]}
    lint_spec(spec)
    assert main(["lint", "--json", "--", *SERVER]) == 1
    quiet = json.loads(capsys.readouterr().out)

    log = tmp_path / "server.log"
    monkeypatch.setenv("LINT_LOG", str(log))
    lint_spec({**spec, "noise": True})
    assert main(["lint", "--json", "--", *SERVER]) == 1
    noisy = json.loads(capsys.readouterr().out)
    assert noisy["findings"] == quiet["findings"]
    assert "reply s1 -32601" in log.read_text(encoding="utf-8").splitlines()


PROBE = {
    "name": "probe",
    "description": "Probe tool",
    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
    "annotations": {"readOnlyHint": True},
}


def _collection(capsys) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)["collection"]


def test_complete_collection_is_reported(capsys, lint_spec) -> None:
    lint_spec({"tools": [CTL, PROBE], "page_size": 1})
    main(["lint", "--json", "--", *SERVER])
    collection = _collection(capsys)
    assert collection["complete"] is True
    assert collection["listings"] == {
        "tools/list": {"items": 2, "pages": 2, "error": None, "truncated": False}
    }


@pytest.mark.parametrize(
    ("mode", "error"), [("error", "error"), ("exit", "error"), ("hang", "timeout")]
)
def test_failure_on_a_later_page_is_reported(
    mode: str, error: str, capsys, lint_spec
) -> None:
    lint_spec(
        {"tools": [CTL, PROBE], "page_size": 1, "fail": {"page": 2, "mode": mode}}
    )
    assert main(["lint", "--json", "--timeout", "2", "--", *SERVER]) == 1
    report = json.loads(capsys.readouterr().out)
    collection = report["collection"]
    assert collection["complete"] is False
    assert collection["listings"]["tools/list"] == {
        "items": 1,
        "pages": 1,
        "error": error,
        "truncated": False,
    }
    assert "tools-list-failed" in _rules(report)


def test_failed_prompt_listing_is_reported_without_a_tools_finding(
    capsys, lint_spec
) -> None:
    lint_spec(
        {
            "capabilities": {"tools": {}, "prompts": {}},
            "tools": [CTL],
            "prompts": [{"name": "p"}],
            "fail": {"listing": "prompts", "mode": "error"},
        }
    )
    assert main(["lint", "--", *SERVER]) == 0
    out = capsys.readouterr().out
    assert "incomplete: prompts/list failed (error) after 0 pages, 0 items read" in out
    assert "tools-list-failed" not in out


def test_page_cap_marks_the_listing_truncated(capsys, lint_spec) -> None:
    tools = {
        "$gen": "tools",
        "n": 1000,
        "template": {**PROBE, "name": "probe{i}"},
    }
    lint_spec({"tools": [CTL, tools], "page_size": 1})
    main(["lint", "--json", "--", *SERVER])
    status = _collection(capsys)["listings"]["tools/list"]
    assert status == {"items": 1000, "pages": 1000, "error": None, "truncated": True}


def test_repeated_cursor_marks_the_listing_truncated(capsys, lint_spec) -> None:
    lint_spec({"tools": [CTL, PROBE], "page_size": 1, "cursor_mode": "repeat"})
    main(["lint", "--json", "--", *SERVER])
    collection = _collection(capsys)
    assert collection["complete"] is False
    assert collection["listings"]["tools/list"]["truncated"] is True


def test_list_rules(capsys) -> None:
    assert main(["lint", "--list-rules"]) == 0
    text = capsys.readouterr().out
    blocks = [block for block in text.split("\n\n") if block.strip()]
    assert [block.split()[0] for block in blocks] == [rule.id for rule in RULES]
    assert main(["lint", "--list-rules", "--json"]) == 0
    catalogue = json.loads(capsys.readouterr().out)
    assert [rule["id"] for rule in catalogue["rules"]] == [rule.id for rule in RULES]
    assert catalogue["known_latest"] == {"claude_code": "2.1.302", "codex": "0.163.0"}


# Codex shows an array without items as Array<string>: a warning, nothing else.
UNTYPED_ARRAY = {
    "name": "flag",
    "description": "Flag tool",
    "inputSchema": {"type": "object", "properties": {"x": {"type": "array"}}},
    "annotations": {"readOnlyHint": True},
}


def test_human_report(capsys, lint_spec) -> None:
    lint_spec({"tools": [CTL, NO_ROOT_TYPE, UNTYPED_ARRAY]})
    assert main(["lint", "--", *SERVER]) == 1
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert out.index("ERROR   root-type-missing  tool 'probe'") < out.index(
        "WARNING array-items-degraded  tool 'flag'"
    )
    assert out.rstrip("\n").endswith("1 errors, 1 warnings, 0 notes")


@pytest.mark.parametrize(
    ("fail_on", "expected"), [("error", 0), ("warning", 1), ("never", 0)]
)
def test_fail_on_threshold(fail_on: str, expected: int, capsys, lint_spec) -> None:
    warning_only = UNTYPED_ARRAY
    lint_spec({"tools": [CTL, warning_only]})
    assert main(["lint", "--json", "--fail-on", fail_on, "--", *SERVER]) == expected
    report = json.loads(capsys.readouterr().out)
    assert report["counts"] == {"error": 0, "warning": 1, "info": 0}


def test_fail_on_never_with_errors(capsys, lint_spec) -> None:
    lint_spec({"tools": [CTL, NO_ROOT_TYPE]})
    assert main(["lint", "--json", "--fail-on", "never", "--", *SERVER]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["error"] == 1


def test_ignore_drops_the_rule(capsys, lint_spec) -> None:
    lint_spec({"tools": [CTL, NO_ROOT_TYPE]})
    assert main(["lint", "--json", "--ignore", "root-type-missing", "--", *SERVER]) == 0
    assert "root-type-missing" not in _rules(json.loads(capsys.readouterr().out))


# --- streamable HTTP -----------------------------------------------------------------


class _Mcp(BaseHTTPRequestHandler):
    mode = "json"
    seen: list[dict[str, Any]]

    def log_message(self, *args: Any) -> None:
        pass

    def _record(self, body: Any) -> None:
        self.seen.append(
            {
                "method": self.command,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )

    def _send(
        self, status: int, payload: bytes, content_type: str, **headers: str
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_DELETE(self) -> None:
        self._record(None)
        self._send(200, b"", "text/plain")

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._record(body)
        method, id = body.get("method"), body.get("id")
        if id is None:
            self._send(202, b"", "text/plain")
            return
        if method == "initialize":
            if self.mode == "redirect":
                self._send(
                    302,
                    b"",
                    "text/plain",
                    Location="http://evil.example/s3cr3t-location",
                )
                return
            if self.mode == "unauthorized":
                self._send(401, b"", "text/plain")
                return
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-quirk", "version": "1.0.0"},
            }
            self._json(
                {"jsonrpc": "2.0", "id": id, "result": result},
                **{"Mcp-Session-Id": "sid-1"},
            )
            return
        if method != "tools/list":
            error = {"code": -32601, "message": "Method not found"}
            self._json({"jsonrpc": "2.0", "id": id, "error": error})
            return
        response = {
            "jsonrpc": "2.0",
            "id": id,
            "result": {"tools": [CTL, NO_ROOT_TYPE]},
        }
        if self.mode == "sse":
            note = {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "info", "data": "x"},
            }
            text = json.dumps(response)
            # Data lines join with "\n", so split between JSON tokens.
            half = text.index('"result"')
            stream = (
                f"event: message\ndata: {json.dumps(note)}\n\n"
                ": keep-alive\n\n"
                f"data: {text[:half]}\ndata: {text[half:]}\n\n"
            )
            self._send(200, stream.encode(), "text/event-stream")
        elif self.mode == "batch":
            self._json([response])
        elif self.mode == "bom":
            payload = b"\xef\xbb\xbf" + json.dumps(response).encode()
            self._send(200, payload, "application/json")
        elif self.mode == "short":
            # Promise more bytes than are sent, then close the connection.
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload) + 100))
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
        elif self.mode == "trickle":
            # An SSE comment that never ends, one byte at a time.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b":")
            for _ in range(40):
                time.sleep(0.25)
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except OSError:
                    return
        elif self.mode == "slow-headers":
            # The status line and headers arrive one byte at a time, each
            # well inside the socket timeout.
            head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" * 3
            for byte in head:
                time.sleep(0.25)
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except OSError:
                    return
        elif self.mode == "utf16":
            payload = json.dumps(response).encode("utf-16")
            self._send(200, payload, "application/json")
        else:
            self._json(response)

    def _json(self, payload: Any, **headers: str) -> None:
        self._send(200, json.dumps(payload).encode(), "application/json", **headers)


@pytest.fixture
def http_server() -> Iterator[Any]:
    servers: list[ThreadingHTTPServer] = []

    def start(mode: str) -> tuple[str, list[dict[str, Any]]]:
        seen: list[dict[str, Any]] = []
        handler = type("Handler", (_Mcp,), {"mode": mode, "seen": seen})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return (
            f"http://127.0.0.1:{server.server_address[1]}/mcp?token=s3cr3t-query",
            seen,
        )

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _header(entry: dict[str, Any], name: str) -> str | None:
    lowered = {key.lower(): value for key, value in entry["headers"].items()}
    return lowered.get(name.lower())


@pytest.mark.parametrize("mode", ["json", "sse", "batch"])
def test_http_modes(mode: str, http_server, capsys) -> None:
    url, seen = http_server(mode)
    argv = ["lint", "--json", "--header", "Authorization: Bearer s3cr3t-value", url]
    assert main(argv) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert "root-type-missing" in _rules(report)
    assert report["target"] == {"kind": "http", "url": url.split("?")[0]}
    assert report["server"]["name"] == "http-quirk"
    assert "s3cr3t" not in captured.out + captured.err
    posts = [entry for entry in seen if entry["method"] == "POST"]
    assert all(_header(e, "Authorization") == "Bearer s3cr3t-value" for e in posts)
    tools_list = next(e for e in posts if e["body"].get("method") == "tools/list")
    assert _header(tools_list, "Mcp-Session-Id") == "sid-1"
    assert _header(tools_list, "MCP-Protocol-Version") == "2025-11-25"
    discover, initialize = posts[0], posts[1]
    assert discover["body"]["method"] == "server/discover"
    assert _header(discover, "MCP-Protocol-Version") == "2026-07-28"
    assert _header(discover, "Mcp-Method") == "server/discover"
    assert _header(initialize, "MCP-Protocol-Version") is None
    # Mcp-Method belongs to the 2026-07-28 transport; 2025 requests omit it.
    assert _header(initialize, "Mcp-Method") is None
    assert _header(tools_list, "Mcp-Method") is None
    deletes = [entry for entry in seen if entry["method"] == "DELETE"]
    assert len(deletes) == 1 and _header(deletes[0], "Mcp-Session-Id") == "sid-1"


def test_http_redirect_is_not_followed(http_server, capsys) -> None:
    url, _ = http_server("redirect")
    assert main(["lint", url]) == 2
    captured = capsys.readouterr()
    assert captured.err == "m3 lint: initialize failed: server returned an error\n"
    assert "s3cr3t" not in captured.out + captured.err
    assert "evil.example" not in captured.out + captured.err


def test_http_unauthorized(http_server, capsys) -> None:
    url, _ = http_server("unauthorized")
    assert main(["lint", "--json", url]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_http_url_with_credentials_is_rejected(capsys) -> None:
    assert main(["lint", "http://u:p4ssw0rd@127.0.0.1:1/mcp"]) == 2
    captured = capsys.readouterr()
    assert captured.err == "m3 lint: URL must be an http(s) URL without credentials\n"
    assert "p4ssw0rd" not in captured.out + captured.err


def test_http_connection_refused(capsys) -> None:
    assert main(["lint", "http://127.0.0.1:1/mcp"]) == 2
    assert capsys.readouterr().err == "m3 lint: could not connect to the server\n"


def test_http_bom_prefixed_json(http_server, capsys) -> None:
    url, _ = http_server("bom")
    assert main(["lint", "--json", url]) == 1
    assert "root-type-missing" in _rules(json.loads(capsys.readouterr().out))


def test_http_truncated_body_is_a_failed_listing(http_server, capsys) -> None:
    url, _ = http_server("short")
    assert main(["lint", "--json", url]) == 1
    report = json.loads(capsys.readouterr().out)
    assert _rules(report) == ["tools-list-failed"]


def test_http_trickling_stream_respects_the_deadline(http_server, capsys) -> None:
    url, _ = http_server("trickle")
    started = time.monotonic()
    assert main(["lint", "--json", "--timeout", "2", url]) == 1
    assert time.monotonic() - started < 6
    report = json.loads(capsys.readouterr().out)
    failed = [f for f in report["findings"] if f["rule"] == "tools-list-failed"]
    assert [f["detail"] for f in failed] == ["timeout"]


def test_http_trickling_headers_respect_the_deadline(http_server, capsys) -> None:
    url, _ = http_server("slow-headers")
    started = time.monotonic()
    assert main(["lint", "--json", "--timeout", "1", url]) == 1
    assert time.monotonic() - started < 5
    report = json.loads(capsys.readouterr().out)
    assert report["collection"]["listings"]["tools/list"]["error"] == "timeout"


def test_http_utf16_reply_is_not_json(http_server, capsys) -> None:
    # Codex parses the raw bytes as UTF-8 JSON and rejects UTF-16.
    url, _ = http_server("utf16")
    assert main(["lint", "--json", url]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["collection"]["complete"] is False
    assert report["collection"]["listings"]["tools/list"]["error"] == "invalid"
    assert "tools-list-failed" in _rules(report)
