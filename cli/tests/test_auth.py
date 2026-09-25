from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib import error, parse, request

import pytest

from m3_cli import auth


class MemoryKeyring:
    priority = 1

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


@pytest.fixture
def keyring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MemoryKeyring:
    value = MemoryKeyring()
    monkeypatch.setattr(auth, "_keyring", lambda: value)
    monkeypatch.setattr(
        auth, "_metadata_path", lambda: tmp_path / "config" / "auth.json"
    )
    return value


def test_saved_token_uses_origin_scoped_credential_and_metadata(
    keyring: MemoryKeyring,
) -> None:
    base = "https://control-plane.example"
    auth._save_token(
        base,
        "m3pat_secret",
        {"token_id": "id", "expires_at": "2030-01-01T00:00:00+00:00"},
    )

    assert auth.load_saved_token(base) == "m3pat_secret"
    assert auth.load_saved_token("https://other.example") is None
    path = auth._metadata_path()
    assert json.loads(path.read_text())[base]["token_id"] == "id"
    assert "m3pat_secret" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert auth._remove_saved_token(base)
    assert auth.load_saved_token(base) is None


def test_status_identifies_saved_developer_token_for_rotation(
    keyring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = "https://control-plane.example"
    auth._save_token(base, "m3pat_secret", {"token_id": "current-token-id"})
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)

    assert auth.status() == 0
    output = capsys.readouterr().out
    assert "ID current-token-id" in output
    assert "m3pat_secret" not in output


def test_control_plane_origin_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", "http://control-plane.example")
    with pytest.raises(ValueError, match="HTTPS origin"):
        auth.control_plane_url()


def test_only_os_keyring_backends_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    class FileKeyring:
        priority = 100

    fake = SimpleNamespace(get_keyring=lambda: FileKeyring())
    monkeypatch.setitem(sys.modules, "keyring", fake)
    with pytest.raises(RuntimeError, match="supported OS credential store"):
        auth._keyring()

    for module, name in (
        ("keyring.backends.macOS", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.libsecret", "Keyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    ):
        backend_type = type(name, (), {"__module__": module})
        fake_backend = SimpleNamespace(
            get_keyring=lambda backend_type=backend_type: backend_type()
        )
        monkeypatch.setitem(sys.modules, "keyring", fake_backend)
        assert auth._keyring() is fake_backend


def _start_login(monkeypatch: pytest.MonkeyPatch, exchange_result: dict[str, object]):
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", "https://control-plane.example")
    opened: list[str] = []
    calls: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        auth.webbrowser, "open", lambda url, **_kwargs: opened.append(url) or True
    )

    def exchange(base: str, code: str, verifier: str, redirect_uri: str):
        calls.append((base, code, verifier, redirect_uri))
        return exchange_result

    monkeypatch.setattr(auth, "_exchange_code", exchange)
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(auth.login()), daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not opened and time.monotonic() < deadline:
        time.sleep(0.01)
    assert opened
    return opened[0], calls, result, thread


def _callback_url(login_url: str) -> tuple[dict[str, list[str]], str]:
    query = parse.parse_qs(parse.urlparse(login_url).query)
    redirect_uri = query["redirect_uri"][0]
    return query, redirect_uri


def _send_callback(url: str, state: str, code: str = "opaque.one-time-code") -> int:
    target = url + "?" + parse.urlencode({"code": code, "state": state})
    try:
        with request.urlopen(target, timeout=3) as response:
            response.read()
            return response.status
    except error.HTTPError as exc:
        return exc.code


def test_login_uses_hosted_pkce_flow_and_leaves_saved_token_when_no_new_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    keyring: MemoryKeyring,
) -> None:
    base = "https://control-plane.example"
    auth._save_token(base, "m3pat_previous", {"token_id": "previous-id"})
    login_url, calls, result, thread = _start_login(monkeypatch, {"created": False})
    query, redirect_uri = _callback_url(login_url)
    assert parse.urlparse(login_url).path == "/cli/login"
    assert set(query) == {
        "redirect_uri",
        "state",
        "code_challenge",
        "can_store_developer",
    }
    assert query["can_store_developer"] == ["1"]
    assert redirect_uri.startswith("http://127.0.0.1:")
    assert redirect_uri.endswith("/callback")

    callback_query = parse.urlencode(
        {"code": "opaque.one-time-code", "state": "incorrect-state"}
    )
    with pytest.raises(error.HTTPError) as invalid:
        request.urlopen(redirect_uri + "?" + callback_query, timeout=3)
    assert invalid.value.code == 400

    valid_query = parse.urlencode(
        {"code": "opaque.one-time-code", "state": query["state"][0]}
    )
    wrong_host = request.Request(
        redirect_uri + "?" + valid_query,
        headers={"Host": "localhost:" + str(parse.urlparse(redirect_uri).port)},
    )
    with pytest.raises(error.HTTPError) as host_error:
        request.urlopen(wrong_host, timeout=3)
    assert host_error.value.code == 400
    with pytest.raises(error.HTTPError) as path_error:
        request.urlopen(
            redirect_uri.replace("/callback", "/other") + "?" + valid_query, timeout=3
        )
    assert path_error.value.code == 404

    assert _send_callback(redirect_uri, query["state"][0]) == 200
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result == [0]
    assert len(calls) == 1
    exchange_base, code, verifier, exchange_redirect = calls[0]
    assert exchange_base == base
    assert code == "opaque.one-time-code"
    assert exchange_redirect == redirect_uri
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    )
    assert query["code_challenge"][0] == expected_challenge.rstrip(b"=").decode()
    assert auth.load_saved_token(base) == "m3pat_previous"
    assert "No developer token was created or changed" in capsys.readouterr().out


def test_login_saves_only_token_returned_from_exchange(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    keyring: MemoryKeyring,
) -> None:
    login_url, calls, result, thread = _start_login(
        monkeypatch,
        {
            "created": True,
            "token": "m3pat_" + "A" * 22 + "." + "A" * 43,
            "metadata": {"id": "token-id", "expires_at": "2030-01-01T00:00:00Z"},
        },
    )
    query, redirect_uri = _callback_url(login_url)
    assert _send_callback(redirect_uri, query["state"][0]) == 200
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result == [0]
    assert calls[0][1] == "opaque.one-time-code"
    expected_token = "m3pat_" + "A" * 22 + "." + "A" * 43
    assert auth.load_saved_token("https://control-plane.example") == expected_token
    saved = json.loads(auth._metadata_path().read_text())
    assert saved["https://control-plane.example"] == {
        "token_id": "token-id",
        "expires_at": "2030-01-01T00:00:00Z",
    }
    output = capsys.readouterr().out
    assert "saved in the OS credential store" in output
    assert expected_token not in output


def test_login_keeps_available_without_secure_store_and_disables_token_creation(
    monkeypatch: pytest.MonkeyPatch,
    keyring: MemoryKeyring,
) -> None:
    monkeypatch.setattr(
        auth,
        "_probe_keyring",
        lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    login_url, calls, result, thread = _start_login(monkeypatch, {"created": False})
    query, redirect_uri = _callback_url(login_url)
    assert query["can_store_developer"] == ["0"]
    assert _send_callback(redirect_uri, query["state"][0]) == 200
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result == [0]
    assert len(calls) == 1
    assert auth.load_saved_token("https://control-plane.example") is None


@pytest.mark.parametrize(
    "exchange_result",
    [
        {"created": True},
        {"created": False, "token": "m3pat_" + "A" * 22 + "." + "A" * 43},
    ],
)
def test_login_rejects_inconsistent_or_invalid_token_results(
    monkeypatch: pytest.MonkeyPatch,
    keyring: MemoryKeyring,
    exchange_result: dict[str, object],
) -> None:
    base = "https://control-plane.example"
    auth._save_token(base, "m3pat_previous", {"token_id": "previous-id"})
    login_url, _calls, result, thread = _start_login(monkeypatch, exchange_result)
    query, redirect_uri = _callback_url(login_url)
    assert _send_callback(redirect_uri, query["state"][0]) == 200
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result == [2]
    assert auth.load_saved_token(base) == "m3pat_previous"


def test_exchange_rejects_noncanonical_personal_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int) -> bytes:
            return json.dumps({"created": True, "token": "m3pat_invalid"}).encode()

    class Opener:
        def open(self, req, *, timeout: int):
            assert req.full_url == "https://control-plane.example/v1/cli/exchange"
            assert req.get_method() == "POST"
            assert timeout == 20
            return Response()

    monkeypatch.setattr(auth.request, "build_opener", lambda *_handlers: Opener())
    with pytest.raises(RuntimeError, match="invalid developer token"):
        auth._exchange_code(
            "https://control-plane.example",
            "opaque-code",
            "verifier",
            "http://127.0.0.1:1234/callback",
        )


def test_login_times_out_without_exchange_or_replacing_saved_token(
    monkeypatch: pytest.MonkeyPatch,
    keyring: MemoryKeyring,
) -> None:
    base = "https://control-plane.example"
    auth._save_token(base, "m3pat_previous", {"token_id": "previous-id"})
    monkeypatch.setattr(auth, "_LOGIN_TTL_SECONDS", 0.05)
    login_url, calls, result, thread = _start_login(monkeypatch, {"created": False})
    assert parse.urlparse(login_url).path == "/cli/login"
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result == [2]
    assert calls == []
    assert auth.load_saved_token(base) == "m3pat_previous"
