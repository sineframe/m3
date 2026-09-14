"""Real-socket gate for both supported local ASGI construction paths."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

_ARCHIVE_PATH = "/api/v1/profiles/00000000-0000-4000-8000-000000000001/archive"
_OPENER = build_opener(ProxyHandler({}))


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _status(
    base: str,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> int:
    request = Request(
        base + path,
        method=method,
        data=b"" if method == "POST" else None,
        headers=headers or {},
    )
    try:
        with _OPENER.open(request, timeout=5) as response:
            return response.status
    except HTTPError as error:
        return error.code


@contextmanager
def _server(command: list[str], env: dict[str, str], port: int) -> Iterator[str]:
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _out, error = process.communicate()
                raise AssertionError(f"security gate server exited early: {error}")
            try:
                if _status(base, "/api/v1/health") == 200:
                    break
            except OSError:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("security gate server did not become ready")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _assert_local_boundary(base: str) -> None:
    port = base.rsplit(":", 1)[1]
    assert (
        _status(
            base,
            "/api/v2/executions",
            headers={
                "Host": f"attacker.example:{port}",
                "Origin": f"http://attacker.example:{port}",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        == 400
    )
    assert (
        _status(
            base,
            _ARCHIVE_PATH,
            method="POST",
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        == 403
    )
    assert (
        _status(
            base,
            _ARCHIVE_PATH,
            method="POST",
            headers={
                "Origin": base,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        == 200
    )


def _non_loopback_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        address = str(probe.getsockname()[0])
    if address.startswith("127."):
        raise AssertionError("live security gate could not find a non-loopback address")
    return address


def test_raw_asgi_and_cli_apps_enforce_local_security_over_real_http(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[2]
    base_env = os.environ.copy()
    base_env["ANTHROPIC_API_KEY"] = ""

    raw_port = _port()
    raw_env = {**base_env, "DATABASE_PATH": str(tmp_path / "raw.sqlite")}
    raw_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "mcp_pal_app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(raw_port),
        "--log-level",
        "error",
    ]
    with _server(raw_command, raw_env, raw_port) as base:
        _assert_local_boundary(base)
        remote_base = f"http://{_non_loopback_address()}:{raw_port}"
        assert (
            _status(
                remote_base,
                "/api/v2/executions",
                headers={"Host": f"localhost:{raw_port}"},
            )
            == 403
        )

    cli_port = _port()
    cli_env = {
        **base_env,
        "TEST_WEB_SECURITY_DATABASE": str(tmp_path / "cli.sqlite"),
        "TEST_WEB_SECURITY_UI": str(root / "cli/tests/fixtures/ui"),
        "TEST_WEB_SECURITY_PORT": str(cli_port),
    }
    cli_program = (
        "import os, uvicorn; "
        "from mcp_pal_cli.web import create_web_app; "
        "uvicorn.run(create_web_app("
        "os.environ['TEST_WEB_SECURITY_DATABASE'], "
        "ui_dir=os.environ['TEST_WEB_SECURITY_UI']), "
        "host='127.0.0.1', port=int(os.environ['TEST_WEB_SECURITY_PORT']), "
        "log_level='error')"
    )
    with _server([sys.executable, "-c", cli_program], cli_env, cli_port) as base:
        _assert_local_boundary(base)
