from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from m3_cli import auth


def token() -> str:
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    return f"m3pat_{enc(b'i' * 16)}.{enc(b's' * 32)}"


class Keyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Keyring:
    value = Keyring()
    monkeypatch.setattr(auth, "_keyring", lambda: value)
    monkeypatch.setattr(auth, "_metadata_path", lambda: tmp_path / "m3" / "auth.json")
    return value


def test_metadata_and_installation_are_preserved(store: Keyring) -> None:
    base = "https://control.example"
    installation = auth._installation_id()
    auth._save_token(base, token(), {"id": "id", "kind": "cli"})
    data = json.loads(auth._metadata_path().read_text())
    assert data["_installation_id"] == installation
    assert auth.load_saved_token(base) == token()
    assert auth._remove_saved_token(base)
    assert (
        json.loads(auth._metadata_path().read_text())["_installation_id"]
        == installation
    )


def test_installation_id_does_not_replace_corrupt_metadata(store: Keyring) -> None:
    path = auth._metadata_path()
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        auth._installation_id()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_windows_metadata_lock_prepares_byte_and_acquires_releases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[int, int]] = []
    import types

    fake_msvcrt = types.SimpleNamespace(
        LK_NBLCK=1,
        LK_UNLCK=2,
        locking=lambda _fd, mode, length: calls.append((mode, length)),
    )
    monkeypatch.setitem(__import__("sys").modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(auth.os, "name", "nt")
    lock = tmp_path / "auth.lock"
    with auth._metadata_file_lock(lock):
        assert lock.with_name(".auth.lock.lock").stat().st_size == 1
    assert calls == [(fake_msvcrt.LK_NBLCK, 1), (fake_msvcrt.LK_UNLCK, 1)]


def test_concurrent_installation_id_calls_persist_one_winner(store: Keyring) -> None:
    barrier = threading.Barrier(8)
    values: list[str] = []

    def read_id() -> None:
        barrier.wait()
        values.append(auth._installation_id())

    threads = [threading.Thread(target=read_id) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(values)) == 1
    assert (
        json.loads(auth._metadata_path().read_text())["_installation_id"] == values[0]
    )


def test_verification_urls_require_exact_next() -> None:
    origin = "https://auth.example"
    code = "ABCD-EFGH"
    complete = origin + "/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH"
    assert auth._validate_verification_url(origin + "/sign-in", origin, complete=False)
    assert auth._validate_verification_url(
        complete, origin, complete=True, user_code=code
    )
    for value in (
        origin + "/sign-in?cli=1&user_code=ABCD-EFGH",
        origin + "/sign-in?next=x&next=y",
        origin + "/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DWRONG",
        origin + "/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH#fragment",
    ):
        with pytest.raises(RuntimeError):
            auth._validate_verification_url(
                value, origin, complete=True, user_code=code
            )


def test_login_posts_device_body_and_saves_token(
    monkeypatch: pytest.MonkeyPatch, store: Keyring
) -> None:
    base = "https://control.example"
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)
    monkeypatch.setenv("M3_AUTH_URL", "https://auth.example/")
    monkeypatch.setattr(auth, "_probe_keyring", lambda: None)
    monkeypatch.setattr(auth, "_cli_version", lambda: "9.8.7")
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a, **k: True)
    responses = iter(
        [
            {
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://auth.example/sign-in",
                "verification_uri_complete": "https://auth.example/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH",
                "expires_in": 1,
                "interval": 1,
            },
            {
                "access_token": token(),
                "token_type": "Bearer",
                "metadata": {
                    "id": "id",
                    "kind": "cli",
                    "org_id": "org",
                    "name": "M3 CLI",
                    "created_at": "a",
                    "expires_at": "b",
                },
            },
        ]
    )
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake(url: str, method: str, body=None, token=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(auth, "_json_request", fake)
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    monkeypatch.setattr(auth.time, "monotonic", lambda: 0.0)
    assert auth.login() == 0
    assert calls[0][0] == base + "/v1/cli/device/authorization"
    assert calls[0][2]["installation_id"]
    assert calls[0][2]["cli_version"] == "9.8.7"
    assert calls[1][2] == {"device_code": "device-secret"}
    assert auth.load_saved_token(base) == token()


def test_login_reports_poll_error_without_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", "https://control.example")
    monkeypatch.setattr(auth, "_probe_keyring", lambda: None)
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a, **k: False)
    start = {
        "device_code": "device-secret",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://auth.sineframe.com/sign-in",
        "verification_uri_complete": "https://auth.sineframe.com/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH",
        "expires_in": 1,
        "interval": 1,
    }
    monkeypatch.setattr(
        auth,
        "_json_request",
        lambda url, method, body=None, token=None: (
            start
            if url.endswith("authorization")
            else (_ for _ in ()).throw(RuntimeError("access_denied"))
        ),
    )
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(auth.time, "monotonic", lambda: next(clock, 2.0))
    assert auth.login() == 2
    output = capsys.readouterr()
    assert "access_denied" in output.err
    assert "device-secret" not in output.err


@pytest.mark.parametrize(
    "error_code",
    ["authorization_pending", "slow_down", "expired_token", "invalid_grant"],
)
def test_login_poll_error_codes_are_handled(
    monkeypatch: pytest.MonkeyPatch, error_code: str
) -> None:
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", "https://control.example")
    monkeypatch.setattr(auth, "_probe_keyring", lambda: None)
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a, **k: False)
    start = {
        "device_code": "device-secret",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://auth.sineframe.com/sign-in",
        "verification_uri_complete": "https://auth.sineframe.com/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH",
        "expires_in": 1,
        "interval": 1,
    }
    monkeypatch.setattr(
        auth,
        "_json_request",
        lambda url, method, body=None, token=None: (
            start
            if url.endswith("authorization")
            else (_ for _ in ()).throw(RuntimeError(error_code))
        ),
    )
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(auth.time, "monotonic", lambda: next(clock, 2.0))
    assert auth.login() == 2


def test_login_preflight_failure_and_browser_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", "https://control.example")
    monkeypatch.setattr(
        auth, "_probe_keyring", lambda: (_ for _ in ()).throw(RuntimeError("no store"))
    )
    assert auth.login() == 2
    assert "no store" in capsys.readouterr().err


def test_save_failure_restores_old_credential_and_reports_revoke_failure(
    monkeypatch: pytest.MonkeyPatch, store: Keyring, capsys: pytest.CaptureFixture[str]
) -> None:
    base = "https://control.example"
    old = token()
    auth._save_token(base, old, {"id": "old"})
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)
    monkeypatch.setattr(auth, "_probe_keyring", lambda: None)
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a, **k: True)
    start = {
        "device_code": "d",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://auth.sineframe.com/sign-in",
        "verification_uri_complete": "https://auth.sineframe.com/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH",
        "expires_in": 1,
        "interval": 1,
    }
    new = {
        "access_token": token(),
        "token_type": "Bearer",
        "metadata": {
            "id": "new",
            "kind": "cli",
            "org_id": "o",
            "name": "M3 CLI",
            "created_at": "a",
            "expires_at": "b",
        },
    }
    monkeypatch.setattr(
        auth, "_save_token", lambda *a: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    responses = iter((start, new))
    monkeypatch.setattr(
        auth,
        "_json_request",
        lambda url, method, body=None, token=None: (
            next(responses)
            if url.endswith("authorization")
            else new
            if url.endswith("/token")
            else (_ for _ in ()).throw(RuntimeError("offline"))
        ),
    )
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    monkeypatch.setattr(auth.time, "monotonic", lambda: 0.0)
    assert auth.login() == 2
    assert auth.load_saved_token(base) == old
    assert "revocation also failed" in capsys.readouterr().err


def test_logout_remote_failure_retains_local_and_no_local_does_not_delete(
    monkeypatch: pytest.MonkeyPatch, store: Keyring, capsys: pytest.CaptureFixture[str]
) -> None:
    base = "https://control.example"
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)
    monkeypatch.setattr(
        auth,
        "_json_request",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert auth.logout() == 0
    assert "No local" in capsys.readouterr().out
    auth._save_token(base, token(), {"id": "id"})
    assert auth.logout() == 2
    assert auth.load_saved_token(base) == token()


def test_json_request_caps_body_and_rejects_redirect() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/ok")
                self.end_headers()
                return
            if self.path == "/empty":
                self.send_response(204)
                self.end_headers()
                return
            if self.path == "/error":
                self.send_response(500)
                body = b"x" * (auth._MAX_RESPONSE + 1)
            elif self.path == "/large":
                self.send_response(200)
                body = b"{" + b"x" * (auth._MAX_RESPONSE + 1)
            elif self.path == "/json204":
                self.send_response(204)
                body = b""
            else:
                self.send_response(200)
                body = b'{"ok":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:
            self.do_GET()

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert (
            auth._json_request(base + "/empty", "DELETE", expect_no_content=True) == {}
        )
        with pytest.raises(RuntimeError, match="invalid response"):
            auth._json_request(base + "/json204", "GET")
        with pytest.raises(RuntimeError, match="too large"):
            auth._json_request(base + "/error", "GET")
        assert auth._json_request(base + "/ok", "GET") == {"ok": True}
        with pytest.raises(RuntimeError, match="redirect"):
            auth._json_request(base + "/redirect", "GET")
        with pytest.raises(RuntimeError, match="too large"):
            auth._json_request(base + "/large", "GET")
    finally:
        server.shutdown()
        thread.join()


def test_logout_ignores_environment_token(
    monkeypatch: pytest.MonkeyPatch, store: Keyring
) -> None:
    base = "https://control.example"
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)
    monkeypatch.setenv("M3_ACCESS_TOKEN", token())
    auth._save_token(base, token(), {"id": "id", "kind": "cli"})
    used: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        auth,
        "_json_request",
        lambda url, method, body=None, token=None, **kwargs: (
            used.append((token or "", kwargs.get("expect_no_content", False))) or {}
        ),
    )
    assert auth.logout() == 0
    assert used == [(token(), True)]
    assert auth.load_saved_token(base) is None


def test_status_reports_online_session_and_rejects_malformed_without_secret(
    monkeypatch: pytest.MonkeyPatch, store: Keyring, capsys: pytest.CaptureFixture[str]
) -> None:
    base = "https://control.example"
    secret = token()
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)
    monkeypatch.setenv("M3_ACCESS_TOKEN", secret)
    session = {
        "metadata": {
            "id": "id",
            "kind": "cli",
            "org_id": "org",
            "name": "M3 CLI",
            "created_at": "a",
            "expires_at": "b",
        }
    }
    monkeypatch.setattr(auth, "_json_request", lambda *a, **k: session)
    assert auth.status() == 0
    assert "valid" in capsys.readouterr().out
    monkeypatch.setattr(auth, "_json_request", lambda *a, **k: {"metadata": {}})
    assert auth.status() == 2
    output = capsys.readouterr()
    assert secret not in output.out + output.err


def test_successful_logout_removes_only_credential_metadata(
    monkeypatch: pytest.MonkeyPatch, store: Keyring
) -> None:
    base = "https://control.example"
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)
    installation = auth._installation_id()
    auth._save_token(base, token(), {"id": "id", "kind": "cli"})
    monkeypatch.setattr(auth, "_json_request", lambda *a, **k: {})
    assert auth.logout() == 0
    data = json.loads(auth._metadata_path().read_text())
    assert data == {"_installation_id": installation}
    assert auth.load_saved_token(base) is None
