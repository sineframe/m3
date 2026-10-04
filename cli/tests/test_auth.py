from __future__ import annotations

import base64
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from m3_cli import auth


def token(fill: bytes = b"s") -> str:
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    return f"m3pat_{enc(fill * 16)}.{enc(fill * 32)}"


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


def fake_clock(monkeypatch: pytest.MonkeyPatch) -> tuple[list[float], list[float]]:
    current = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current[0] += seconds

    monkeypatch.setattr(auth.time, "monotonic", lambda: current[0])
    monkeypatch.setattr(auth.time, "sleep", sleep)
    return current, sleeps


def device_authorization(*, expires: int = 30, interval: int = 1) -> dict[str, object]:
    return {
        "device_code": "device-secret",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://auth.sineframe.com/sign-in",
        "verification_uri_complete": "https://auth.sineframe.com/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH",
        "expires_in": expires,
        "interval": interval,
    }


def issued_token() -> dict[str, object]:
    return {
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
    }


def prepare_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", "https://control.example")
    monkeypatch.setattr(auth, "_probe_keyring", lambda: None)
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a, **k: True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 0.0),
        ("", 0.0),
        ("   ", 0.0),
        ("0", 0.0),
        ("12", 12.0),
        (" 7 ", 7.0),
        ("-1", 0.0),
        ("1.5", 0.0),
        ("NaN", 0.0),
        ("Infinity", 0.0),
        ("-Infinity", 0.0),
        ("not a date", 0.0),
    ],
)
def test_retry_after_seconds_integer_and_malformed_values(
    value: str | None, expected: float
) -> None:
    assert auth._retry_after_seconds(value) == expected


def test_retry_after_seconds_http_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(auth.time, "time", lambda: now.timestamp())
    future = format_datetime(now + timedelta(seconds=90), usegmt=True)
    past = format_datetime(now - timedelta(seconds=90), usegmt=True)
    assert auth._retry_after_seconds(future) == 90.0
    assert auth._retry_after_seconds(past) == 0.0


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


def test_legacy_keyring_account_migrates_on_read(store: Keyring) -> None:
    base = "https://control.example"
    legacy = auth._legacy_account(base)
    current = auth._account(base)
    store.set_password(auth._SERVICE, legacy, token())
    assert legacy != current
    assert auth.load_saved_token(base) == token()
    assert store.get_password(auth._SERVICE, legacy) is None
    assert store.get_password(auth._SERVICE, current) == token()


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


@pytest.mark.parametrize(
    ("interval", "retry_after", "expected_wait"),
    [
        (1, None, 1.25),
        (1, "malformed", 1.25),
        (1, "0", 1.25),
        (1, "4", 4.25),
        (5, "1", 5.25),
    ],
)
def test_login_retries_rate_limit_with_same_authorization_and_advised_delay(
    monkeypatch: pytest.MonkeyPatch,
    store: Keyring,
    interval: int,
    retry_after: str | None,
    expected_wait: float,
) -> None:
    prepare_login(monkeypatch)
    current, sleeps = fake_clock(monkeypatch)
    jitter_bounds: list[tuple[float, float]] = []

    def jitter(low: float, high: float) -> float:
        jitter_bounds.append((low, high))
        return 0.25

    monkeypatch.setattr(auth.random, "uniform", jitter)
    start = device_authorization(interval=interval)
    requests: list[tuple[str, dict[str, object] | None]] = []
    poll_results = iter(
        (auth._RateLimited(auth._retry_after_seconds(retry_after)), issued_token())
    )

    def fake_request(url: str, method: str, body=None, token=None):
        requests.append((url, body))
        if url.endswith("/authorization"):
            return start
        outcome = next(poll_results)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(auth, "_json_request", fake_request)
    assert auth.login() == 0
    assert len([url for url, _ in requests if url.endswith("/authorization")]) == 1
    polls = [(url, body) for url, body in requests if url.endswith("/token")]
    assert polls == [
        (
            "https://control.example/v1/cli/device/token",
            {"device_code": "device-secret"},
        ),
        (
            "https://control.example/v1/cli/device/token",
            {"device_code": "device-secret"},
        ),
    ]
    assert sleeps == pytest.approx([float(interval), expected_wait])
    assert jitter_bounds == [(0.1, 0.5)]
    assert current[0] == pytest.approx(float(interval) + expected_wait)
    assert auth.load_saved_token("https://control.example") == token()


def test_login_rate_limit_retries_stop_at_original_expiry(
    monkeypatch: pytest.MonkeyPatch, store: Keyring, capsys: pytest.CaptureFixture[str]
) -> None:
    old = token(b"o")
    auth._save_token("https://control.example", old, {"id": "old", "kind": "cli"})
    prepare_login(monkeypatch)
    _, sleeps = fake_clock(monkeypatch)
    monkeypatch.setattr(auth.random, "uniform", lambda _low, _high: 0.25)
    requests: list[str] = []
    outcomes = iter((auth._RateLimited(1.0), auth._RateLimited(100.0)))

    def fake_request(url: str, method: str, body=None, token=None):
        requests.append(url)
        if url.endswith("/authorization"):
            return device_authorization(expires=4)
        raise next(outcomes)

    monkeypatch.setattr(auth, "_json_request", fake_request)
    assert auth.login() == 2
    polls = [url for url in requests if url.endswith("/token")]
    assert len(polls) == 2
    assert sleeps == pytest.approx([1.0, 1.25, 1.75])
    assert sum(sleeps) == pytest.approx(4.0)
    assert "device authorization expired" in capsys.readouterr().err
    assert auth.load_saved_token("https://control.example") == old


@pytest.mark.parametrize("retry_header", ["10000000", "9" * 400])
def test_login_large_retry_after_is_clipped_to_deadline(
    monkeypatch: pytest.MonkeyPatch,
    store: Keyring,
    capsys: pytest.CaptureFixture[str],
    retry_header: str,
) -> None:
    prepare_login(monkeypatch)
    _, sleeps = fake_clock(monkeypatch)
    monkeypatch.setattr(auth.random, "uniform", lambda _low, _high: 0.25)
    polls = 0

    def fake_request(url: str, method: str, body=None, token=None):
        nonlocal polls
        if url.endswith("/authorization"):
            return device_authorization(expires=3)
        polls += 1
        raise auth._RateLimited(auth._retry_after_seconds(retry_header))

    monkeypatch.setattr(auth, "_json_request", fake_request)
    assert auth.login() == 2
    assert polls == 1
    assert sleeps == pytest.approx([1.0, 2.0])
    assert "device authorization expired" in capsys.readouterr().err


def test_login_rate_limited_authorization_creation_fails_without_retry(
    monkeypatch: pytest.MonkeyPatch, store: Keyring, capsys: pytest.CaptureFixture[str]
) -> None:
    prepare_login(monkeypatch)
    _, sleeps = fake_clock(monkeypatch)
    jitter_calls: list[tuple[float, float]] = []
    monkeypatch.setattr(
        auth.random,
        "uniform",
        lambda low, high: jitter_calls.append((low, high)) or 0.25,
    )
    calls: list[str] = []

    def fake_request(url: str, method: str, body=None, token=None):
        calls.append(url)
        raise auth._RateLimited(3.0)

    monkeypatch.setattr(auth, "_json_request", fake_request)
    assert auth.login() == 2
    assert calls == ["https://control.example/v1/cli/device/authorization"]
    assert sleeps == []
    assert jitter_calls == []
    assert "rate_limited" in capsys.readouterr().err


def test_login_slow_down_and_rate_limit_delays_are_scoped_correctly(
    monkeypatch: pytest.MonkeyPatch, store: Keyring
) -> None:
    prepare_login(monkeypatch)
    _, sleeps = fake_clock(monkeypatch)
    jitter_bounds: list[tuple[float, float]] = []

    def jitter(low: float, high: float) -> float:
        jitter_bounds.append((low, high))
        return 0.25

    monkeypatch.setattr(auth.random, "uniform", jitter)
    outcomes = iter(
        (
            RuntimeError("slow_down"),
            auth._RateLimited(0.5),
            RuntimeError("authorization_pending"),
            issued_token(),
        )
    )
    requests: list[dict[str, object] | None] = []

    def fake_request(url: str, method: str, body=None, token=None):
        if url.endswith("/authorization"):
            return device_authorization(expires=30)
        requests.append(body)
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(auth, "_json_request", fake_request)
    assert auth.login() == 0
    assert sleeps == pytest.approx([1.0, 6.0, 6.25, 6.0])
    assert jitter_bounds == [(0.1, 0.5)]
    assert requests == [{"device_code": "device-secret"}] * 4


def test_login_browser_delay_counts_against_authorization_deadline(
    monkeypatch: pytest.MonkeyPatch, store: Keyring, capsys: pytest.CaptureFixture[str]
) -> None:
    prepare_login(monkeypatch)
    current, sleeps = fake_clock(monkeypatch)
    requests: list[str] = []

    def open_browser(*_args, **_kwargs) -> bool:
        current[0] += 2.0
        return True

    monkeypatch.setattr(auth.webbrowser, "open", open_browser)

    def fake_request(url: str, method: str, body=None, token=None):
        requests.append(url)
        return (
            device_authorization(expires=2)
            if url.endswith("/authorization")
            else issued_token()
        )

    monkeypatch.setattr(auth, "_json_request", fake_request)
    assert auth.login() == 2
    assert requests == ["https://control.example/v1/cli/device/authorization"]
    assert sleeps == []
    assert current[0] == 2.0
    assert "device authorization expired" in capsys.readouterr().err


def test_login_reports_poll_error_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    store: Keyring,
    capsys: pytest.CaptureFixture[str],
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
    clock = iter((0.0, 0.0, 0.5))
    monkeypatch.setattr(auth.time, "monotonic", lambda: next(clock, 0.5))
    assert auth.login() == 2
    output = capsys.readouterr()
    assert "access_denied" in output.err
    assert start["verification_uri_complete"] in output.out
    assert "device-secret" not in output.err


@pytest.mark.parametrize(
    "error_code",
    ["authorization_pending", "slow_down", "expired_token", "invalid_grant"],
)
def test_login_poll_error_codes_are_handled(
    monkeypatch: pytest.MonkeyPatch, store: Keyring, error_code: str
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
    old = token(b"o")
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
        "access_token": token(b"n"),
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


def test_relogin_revokes_previous_cli_credential(
    monkeypatch: pytest.MonkeyPatch, store: Keyring
) -> None:
    base = "https://control.example"
    old = token(b"o")
    new = token(b"n")
    auth._save_token(base, old, {"id": "old", "kind": "cli"})
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", base)
    monkeypatch.setattr(auth, "_probe_keyring", lambda: None)
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a, **k: True)
    start = {
        "device_code": "device-secret",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://auth.sineframe.com/sign-in",
        "verification_uri_complete": "https://auth.sineframe.com/sign-in?next=%2Fcli%2Fauthorize%3Fcode%3DABCD-EFGH",
        "expires_in": 30,
        "interval": 1,
    }
    issued = {
        "access_token": new,
        "token_type": "Bearer",
        "metadata": {
            "id": "new",
            "kind": "cli",
            "org_id": "org",
            "name": "M3 CLI",
            "created_at": "a",
            "expires_at": "b",
        },
    }
    revoked: list[tuple[str, bool]] = []

    def fake(url: str, method: str, body=None, token=None, **kwargs):
        if url.endswith("/authorization"):
            return start
        if url.endswith("/token"):
            return issued
        revoked.append((token, kwargs.get("expect_no_content", False)))
        return {}

    monkeypatch.setattr(auth, "_json_request", fake)
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    monkeypatch.setattr(auth.time, "monotonic", lambda: 0.0)
    assert auth.login() == 0
    assert auth.load_saved_token(base) == new
    assert auth._pending_revoke_token(base) is None
    assert revoked == [(old, True)]


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
    seen: list[tuple[str, str | None, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(
                (
                    self.path,
                    self.headers.get("X-Request-ID"),
                    self.headers.get("User-Agent"),
                )
            )
            extra_headers: dict[str, str] = {}
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
            elif self.path == "/unavailable":
                self.send_response(503)
                body = b'{"error":{"code":"unavailable"}}'
            elif self.path == "/unauthorized":
                self.send_response(401)
                body = b'{"error":{"code":"unauthorized"}}'
            elif self.path == "/pending":
                self.send_response(400)
                body = b'{"error":{"code":"authorization_pending"}}'
            elif self.path == "/json204":
                self.send_response(204)
                body = b""
            elif self.path == "/rate-limited":
                self.send_response(429)
                extra_headers["Retry-After"] = "7"
                body = b'{"error":{"code":"rate_limited","message":"try later"}}'
            elif self.path == "/rate-limited-wrong-status":
                self.send_response(400)
                extra_headers["Retry-After"] = "7"
                body = b'{"error":{"code":"rate_limited","message":"try later"}}'
            elif self.path == "/wrong-rate-limit-code":
                self.send_response(429)
                extra_headers["Retry-After"] = "7"
                body = b'{"error":{"code":"unexpected_error","message":"failure"}}'
            elif self.path == "/rate-limit-not-json":
                self.send_response(429)
                extra_headers["Retry-After"] = "7"
                body = b"not json"
            else:
                self.send_response(200)
                body = b'{"ok":true}'
            self.send_header("Content-Length", str(len(body)))
            for name, value in extra_headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:
            self.do_GET()

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert (
            auth._json_request(base + "/empty", "DELETE", expect_no_content=True) == {}
        )
        with pytest.raises(RuntimeError, match="invalid response") as invalid:
            auth._json_request(base + "/json204", "GET")
        invalid_id = next(item[1] for item in seen if item[0] == "/json204")
        assert str(invalid.value).endswith(f" (ref: {invalid_id})")
        with pytest.raises(RuntimeError, match="too large") as too_large_error:
            auth._json_request(base + "/error", "GET")
        error_id = next(item[1] for item in seen if item[0] == "/error")
        assert str(too_large_error.value).endswith(f" (ref: {error_id})")
        assert auth._json_request(base + "/ok", "GET") == {"ok": True}
        assert auth._json_request(base + "/ok", "GET") == {"ok": True}
        ok_headers = [item for item in seen if item[0] == "/ok"]
        assert len(ok_headers) == 2
        first, second = ok_headers[0][1], ok_headers[1][1]
        assert first and second and first != second
        assert str(uuid.UUID(first)) == first
        assert re.fullmatch(r"m3-cli/\S+ python/\S+", ok_headers[0][2] or "")
        with pytest.raises(RuntimeError) as unavailable:
            auth._json_request(base + "/unavailable", "GET")
        sent_id = next(item[1] for item in seen if item[0] == "/unavailable")
        assert str(unavailable.value) == f"server rejected the request (ref: {sent_id})"
        with pytest.raises(RuntimeError) as pending:
            auth._json_request(base + "/pending", "GET")
        assert str(pending.value) == "authorization_pending"
        with pytest.raises(RuntimeError) as unauthorized:
            auth._json_request(base + "/unauthorized", "GET")
        unauthorized_id = next(item[1] for item in seen if item[0] == "/unauthorized")
        assert (
            str(unauthorized.value)
            == f"server rejected the request (ref: {unauthorized_id})"
        )
        with pytest.raises(auth._RateLimited) as limited:
            auth._json_request(base + "/rate-limited", "GET")
        assert limited.value.retry_after == 7.0
        for path in (
            "/rate-limited-wrong-status",
            "/wrong-rate-limit-code",
            "/rate-limit-not-json",
        ):
            with pytest.raises(RuntimeError, match="server rejected") as rejected:
                auth._json_request(base + path, "GET")
            assert not isinstance(rejected.value, auth._RateLimited)
        with pytest.raises(RuntimeError, match="redirect") as redirected:
            auth._json_request(base + "/redirect", "GET")
        redirect_id = next(item[1] for item in seen if item[0] == "/redirect")
        assert str(redirected.value) == f"redirect rejected (ref: {redirect_id})"
        with pytest.raises(RuntimeError, match="too large") as too_large:
            auth._json_request(base + "/large", "GET")
        large_id = next(item[1] for item in seen if item[0] == "/large")
        assert str(too_large.value) == (
            f"server response is too large (ref: {large_id})"
        )
    finally:
        server.shutdown()
        thread.join()
    with pytest.raises(RuntimeError) as unreachable:
        auth._json_request(base + "/ok", "GET")
    assert re.fullmatch(
        r"could not contact the M3 control-plane \(ref: [0-9a-f-]{36}\)",
        str(unreachable.value),
    )


def test_login_retries_through_real_json_request_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch, store: Keyring
) -> None:
    clock, sleeps = fake_clock(monkeypatch)
    prepare_login(monkeypatch)
    jitter_bounds: list[tuple[float, float]] = []

    def jitter(low: float, high: float) -> float:
        jitter_bounds.append((low, high))
        return 0.25

    monkeypatch.setattr(auth.random, "uniform", jitter)
    observed: list[tuple[str, dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        poll_count = 0

        def _reply(self, status: int, value: dict[str, object], headers=None) -> None:
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for name, header_value in (headers or {}).items():
                self.send_header(name, header_value)
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append((self.path, body))
            if self.path.endswith("/authorization"):
                self._reply(200, device_authorization(expires=10))
                return
            if self.path.endswith("/token"):
                type(self).poll_count += 1
                if type(self).poll_count == 1:
                    self._reply(
                        429,
                        {"error": {"code": "rate_limited", "message": "try later"}},
                        {"Retry-After": "2"},
                    )
                else:
                    self._reply(200, issued_token())
                return
            self._reply(404, {"error": {"code": "not_found"}})

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(auth, "control_plane_url", lambda: base)
        assert auth.login() == 0
    finally:
        server.shutdown()
        thread.join()

    assert observed == [
        (
            "/v1/cli/device/authorization",
            {
                "installation_id": auth._installation_id(),
                "device_name": auth._device_name(),
                "cli_version": auth._cli_version(),
            },
        ),
        ("/v1/cli/device/token", {"device_code": "device-secret"}),
        ("/v1/cli/device/token", {"device_code": "device-secret"}),
    ]
    assert sleeps == pytest.approx([1.0, 2.25])
    assert clock[0] == pytest.approx(3.25)
    assert jitter_bounds == [(0.1, 0.5)]
    assert auth.load_saved_token(base) == token()


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
    auth._save_token(base, token(b"l"), {"id": "local", "kind": "cli"})
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
    first = capsys.readouterr()
    assert "M3_ACCESS_TOKEN is set" in first.out
    assert "credential is valid" in first.out
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
