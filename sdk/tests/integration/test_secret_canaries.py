from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

import pytest

from m3.server_group import HarnessServerConfig
from m3.trace.capture import CaptureWriter
from m3.transport.capture_proxy import McpCaptureManager
from m3.types import SecretReference, TransportKind

pytestmark = pytest.mark.process_lifecycle


def _configuration(
    *,
    connection_id: str,
    transport: TransportKind,
    endpoint: str | None = None,
    command: str | None = None,
    args: tuple[str, ...] = (),
    environment: dict[str, object] | None = None,
    headers: dict[str, object] | None = None,
) -> HarnessServerConfig:
    return HarnessServerConfig(
        key=connection_id,
        transport=transport,
        required=True,
        available=True,
        connection_id=connection_id,
        endpoint=endpoint,
        command=command,
        args=args,
        environment=environment or {},
        headers=headers or {},
    )


def _echo_server(path: Path) -> None:
    path.write_text(
        """import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    values = [os.environ.get(name, "") for name in ("X_API_KEY", "ANTHROPIC_API_KEY", "REF_TOKEN")]
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": {"code": -32000, "message": "|".join(values)},
    }), flush=True)
""",
        encoding="utf-8",
    )


def _capture_bytes(manager: McpCaptureManager, connection_id: str) -> bytes:
    return manager.writer_for(connection_id).path.read_bytes()


def _run_proxy_with_handoff(
    handoff: Path, capture: Path, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).parents[2] / "src")
    environment["PYTHONPATH"] = (
        source_root + os.pathsep + environment.get("PYTHONPATH", "")
    )
    return subprocess.run(
        (
            sys.executable,
            "-m",
            "m3.transport.stdio_proxy",
            "--capture",
            str(capture),
            "--baseline",
            "0",
            "--env-file",
            str(handoff),
            "--",
            sys.executable,
            "-c",
            "pass",
        ),
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_api_key_literals_are_scanned_on_http_and_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    literal_http = "http-x-api-canary"
    literal_vendor = "anthropic-api-canary"
    resolved = "resolved-reference-canary"
    monkeypatch.setenv("HTTP_REFERENCE", resolved)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32000,
                    "message": "|".join(
                        (
                            self.headers.get("X-API-Key", ""),
                            self.headers.get("ANTHROPIC_API_KEY", ""),
                            self.headers.get("Authorization", ""),
                        )
                    ),
                },
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    script = tmp_path / "echo_server.py"
    _echo_server(script)
    manager = McpCaptureManager(tmp_path / "capture", trusted_private_keys=("http",))
    try:
        configurations = await manager.instrument(
            (
                _configuration(
                    connection_id="http",
                    transport=TransportKind.STREAMABLE_HTTP,
                    endpoint=f"http://127.0.0.1:{upstream.server_port}/mcp",
                    headers={
                        "X-API-Key": literal_http,
                        "ANTHROPIC_API_KEY": literal_vendor,
                        "Authorization": SecretReference(
                            source="environment", name="HTTP_REFERENCE"
                        ),
                    },
                ),
                _configuration(
                    connection_id="stdio",
                    transport=TransportKind.STDIO,
                    command=sys.executable,
                    args=(str(script),),
                    environment={
                        "X_API_KEY": literal_http,
                        "ANTHROPIC_API_KEY": literal_vendor,
                        "REF_TOKEN": SecretReference(
                            source="environment", name="HTTP_REFERENCE"
                        ),
                    },
                ),
            )
        )
        http_config, stdio_config = configurations
        request = Request(
            str(http_config.endpoint),
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}',
            headers={"Content-Type": "application/json"},
        )
        with await asyncio.to_thread(urlopen, request) as response:
            http_body = response.read().decode()
        assert literal_http in http_body
        assert literal_vendor in http_body
        assert resolved in http_body

        env_index = stdio_config.args.index("--env-file") + 1
        env_file = Path(stdio_config.args[env_index])
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        child_environment = os.environ.copy()
        source_root = str(Path(__file__).parents[2] / "src")
        child_environment["PYTHONPATH"] = (
            source_root + os.pathsep + child_environment.get("PYTHONPATH", "")
        )
        process = subprocess.Popen(
            (stdio_config.command, *stdio_config.args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=child_environment,
        )
        assert process.stdin is not None and process.stdout is not None
        stdout, _ = process.communicate(
            '{"jsonrpc":"2.0","id":2,"method":"tools/call"}\n', timeout=10
        )
        assert process.returncode == 0
        assert literal_http in stdout
        assert literal_vendor in stdout
        assert resolved in stdout
        assert not env_file.exists()
    finally:
        await manager.close()
        upstream.shutdown()
        upstream.server_close()

    capture = _capture_bytes(manager, "http") + _capture_bytes(manager, "stdio")
    for canary in (literal_http, literal_vendor, resolved):
        assert canary.encode() not in capture
        assert all(
            canary not in repr(event)
            for snapshot in manager.snapshots()
            for event in snapshot.events
        )


@pytest.mark.asyncio
async def test_proxy_writer_receives_resolved_canaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    literal = "proxy-literal-api-key"
    resolved = "proxy-resolved-reference"
    ambient = "ambient-api-key"
    monkeypatch.setenv("PROXY_REFERENCE", resolved)
    monkeypatch.setenv("OPENAI_API_KEY", ambient)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            data = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"value": f"{literal}|{resolved}|{ambient}"},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=upstream.serve_forever, daemon=True).start()
    manager = McpCaptureManager(tmp_path / "capture", trusted_private_keys=("proxy",))
    try:
        config = _configuration(
            connection_id="proxy",
            transport=TransportKind.STREAMABLE_HTTP,
            endpoint=f"http://127.0.0.1:{upstream.server_port}/mcp",
            headers={
                "X-API-Key": literal,
                "Authorization": SecretReference(
                    source="environment", name="PROXY_REFERENCE"
                ),
            },
        )
        instrumented = (await manager.instrument((config,)))[0]
        request = Request(
            str(instrumented.endpoint),
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}',
            headers={"Content-Type": "application/json"},
        )
        with await asyncio.to_thread(urlopen, request) as response:
            assert literal in response.read().decode()
    finally:
        await manager.close()
        upstream.shutdown()
        upstream.server_close()
    raw = _capture_bytes(manager, "proxy")
    assert literal.encode() not in raw
    assert resolved.encode() not in raw
    assert ambient.encode() not in raw
    assert all(
        canary not in repr(event)
        for canary in (literal, resolved, ambient)
        for event in manager.snapshot("proxy").events
    )


def test_stdio_handoff_failure_removes_non_private_file(tmp_path: Path) -> None:
    handoff = tmp_path / "handoff.json"
    handoff.write_text('{"environment": {}, "canaries": []}', encoding="utf-8")
    handoff.chmod(0o644)
    result = _run_proxy_with_handoff(handoff, tmp_path / "capture.jsonl", tmp_path)
    assert result.returncode == 78
    assert not handoff.exists()


def test_stdio_handoff_rejects_legacy_shape_and_symlink(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"TOKEN": "legacy-secret"}', encoding="utf-8")
    legacy.chmod(0o600)
    result = _run_proxy_with_handoff(
        legacy, tmp_path / "legacy-capture.jsonl", tmp_path
    )
    assert result.returncode == 78
    assert not legacy.exists()

    target = tmp_path / "target.json"
    target_contents = '{"environment": {}, "canaries": []}'
    target.write_text(target_contents, encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")
    result = _run_proxy_with_handoff(link, tmp_path / "link-capture.jsonl", tmp_path)
    assert result.returncode == 78
    assert not link.exists()
    assert target.exists()
    assert target.read_text(encoding="utf-8") == target_contents


def test_capture_writer_without_explicit_canaries_keeps_ambient_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ambient = "ambient-default-api-key"
    monkeypatch.setenv("VENDOR_API_KEY", ambient)
    writer = CaptureWriter(str(tmp_path / "capture.jsonl"), 0, secrets=set())
    writer.write(
        transport="stdio", direction="server_to_client", payload={"result": ambient}
    )
    assert ambient.encode() not in (tmp_path / "capture.jsonl").read_bytes()
