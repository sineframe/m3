"""Temporary loopback sign-in and local M3 access-token storage."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import parse_qs, urlencode, urlparse

from .ci_credentials import validate_access_token
from .errors import CLIError

_DEFAULT_CONTROL_PLANE = "https://control-plane-ulwh0w.fly.dev"
_SERVICE = "sf-m3"
_LOGIN_TTL_SECONDS = 600
_MAX_EXCHANGE_RESPONSE = 64 * 1024


def control_plane_url() -> str:
    raw = os.environ.get("M3_CONTROL_PLANE_URL", _DEFAULT_CONTROL_PLANE)
    parsed = urlparse(raw)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("M3 control-plane URL must be an HTTPS origin")
    return raw.rstrip("/")


def _metadata_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "m3" / "auth.json"


def _write_metadata(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name("." + path.name + "." + secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - packaging supplies keyring
        raise RuntimeError(
            "secure credential storage is unavailable; set M3_ACCESS_TOKEN instead"
        ) from exc
    backend = keyring.get_keyring()
    backend_type = type(backend)
    trusted_backends = {
        ("keyring.backends.macOS", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.libsecret", "Keyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    }
    if (backend_type.__module__, backend_type.__name__) not in trusted_backends:
        raise RuntimeError(
            "a supported OS credential store is unavailable; set M3_ACCESS_TOKEN instead"
        )
    return keyring


def _probe_keyring() -> Any:
    keyring = _keyring()
    service = _SERVICE + "-probe"
    account = secrets.token_hex(16)
    try:
        keyring.set_password(service, account, secrets.token_urlsafe(32))
        keyring.delete_password(service, account)
    except Exception:
        try:
            keyring.delete_password(service, account)
        except Exception:
            pass
        raise RuntimeError(
            "OS credential storage cannot save a token; set M3_ACCESS_TOKEN instead"
        ) from None
    return keyring


def _account(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.netloc.lower()
    # Credential-manager account names are bounded and cannot contain secrets.
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", host)
    return "m3_" + slug[:58]


def load_saved_token(base_url: str) -> str | None:
    """Return the saved PAT for this exact API origin, if the OS store has one."""
    try:
        token = _keyring().get_password(_SERVICE, _account(base_url))
        return token if isinstance(token, str) else None
    except RuntimeError:
        raise
    except Exception:
        return None


def _save_token(base_url: str, token: str, metadata: dict[str, Any]) -> None:
    keyring = _keyring()
    account = _account(base_url)
    previous = keyring.get_password(_SERVICE, account)
    keyring.set_password(_SERVICE, account, token)
    try:
        path = _metadata_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        current: dict[str, Any] = {}
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                current = decoded
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        current[base_url] = metadata
        _write_metadata(path, current)
    except Exception:
        try:
            if previous is None:
                keyring.delete_password(_SERVICE, account)
            else:
                keyring.set_password(_SERVICE, account, previous)
        except Exception:
            pass
        raise RuntimeError("could not save credential metadata securely") from None


def _remove_saved_token(base_url: str) -> bool:
    try:
        keyring = _keyring()
        account = _account(base_url)
        if keyring.get_password(_SERVICE, account) is not None:
            keyring.delete_password(_SERVICE, account)
    except Exception:
        return False
    path = _metadata_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.pop(base_url, None)
            _write_metadata(path, value)
    except (OSError, json.JSONDecodeError):
        pass
    return True


def status() -> int:
    try:
        base = control_plane_url()
    except ValueError as exc:
        print(f"m3 auth status: {exc}", file=sys.stderr)
        return 2
    try:
        token = load_saved_token(base)
    except RuntimeError as exc:
        print(
            f"OS credential store unavailable ({exc}); server validity was not checked."
        )
        token = None
    env_token = os.environ.get("M3_ACCESS_TOKEN")
    if env_token:
        print(
            "M3_ACCESS_TOKEN is set in the current environment (validity not checked)."
        )
    if token is None:
        if not env_token:
            print("No developer token is saved in the OS credential store.")
        return 0
    metadata: dict[str, Any] = {}
    try:
        all_metadata = json.loads(_metadata_path().read_text(encoding="utf-8"))
        if isinstance(all_metadata, dict) and isinstance(all_metadata.get(base), dict):
            metadata = all_metadata[base]
    except (OSError, json.JSONDecodeError):
        pass
    expires = metadata.get("expires_at")
    description = "saved developer token found in the OS credential store"
    if isinstance(expires, str):
        description += f" (expires {expires})"
    print(description + "; server validity has not been checked.")
    return 0


def logout() -> int:
    try:
        base = control_plane_url()
        if not _remove_saved_token(base):
            print(
                "m3 auth logout: could not access the OS credential store",
                file=sys.stderr,
            )
            return 2
    except ValueError as exc:
        print(f"m3 auth logout: {exc}", file=sys.stderr)
        return 2
    print(
        "Removed the locally saved M3 credential. Copies held by other systems remain active."
    )
    return 0


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("control-plane redirect rejected")


def login() -> int:
    try:
        base = control_plane_url()
    except ValueError as exc:
        print(f"m3 auth login: {exc}", file=sys.stderr)
        return 2
    try:
        _probe_keyring()
        can_store_developer = True
    except Exception:
        can_store_developer = False
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(32)
    challenge_bytes = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    )
    challenge = challenge_bytes.rstrip(b"=").decode("ascii")
    callback: dict[str, str] = {}
    callback_lock = threading.Lock()
    completed = threading.Event()
    started = time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        server_version = "M3Auth/2"
        sys_version = ""

        def log_message(self, _format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            expected_host = f"127.0.0.1:{server.server_address[1]}"
            if self.headers.get("Host") != expected_host:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if time.monotonic() - started > _LOGIN_TTL_SECONDS:
                self._reply(
                    HTTPStatus.GONE,
                    "This sign-in has expired. Run m3 auth login again.",
                )
                return
            parsed = urlparse(self.path)
            if parsed.path != "/callback" or parsed.fragment:
                self._reply(
                    HTTPStatus.NOT_FOUND, "This local address is only for M3 sign-in."
                )
                return
            try:
                values = parse_qs(
                    parsed.query, strict_parsing=True, keep_blank_values=True
                )
            except ValueError:
                self._reply(HTTPStatus.BAD_REQUEST, "The sign-in response is invalid.")
                return
            if any(len(items) != 1 for items in values.values()) or not set(
                values
            ).issubset({"state", "code", "error"}):
                self._reply(HTTPStatus.BAD_REQUEST, "The sign-in response is invalid.")
                return
            returned_state = values.get("state", [""])[0]
            code = values.get("code", [""])[0]
            callback_error = values.get("error", [""])[0]
            if not secrets.compare_digest(returned_state, state):
                self._reply(
                    HTTPStatus.BAD_REQUEST,
                    "The sign-in response could not be verified.",
                )
                return
            if callback_error:
                if code or callback_error not in {
                    "access_denied",
                    "login_required",
                    "server_error",
                }:
                    self._reply(
                        HTTPStatus.BAD_REQUEST, "The sign-in response is invalid."
                    )
                    return
                with callback_lock:
                    if completed.is_set():
                        self._reply(
                            HTTPStatus.CONFLICT,
                            "This sign-in response was already received.",
                        )
                        return
                    callback["error"] = callback_error
                    completed.set()
                self._reply(
                    HTTPStatus.OK,
                    "M3 sign-in was cancelled. You can close this browser tab.",
                )
                return
            if (
                not code
                or len(code) > 4096
                or not re.fullmatch(r"[A-Za-z0-9._~-]+", code)
            ):
                self._reply(HTTPStatus.BAD_REQUEST, "The sign-in response is invalid.")
                return
            with callback_lock:
                if completed.is_set():
                    self._reply(
                        HTTPStatus.CONFLICT,
                        "This sign-in response was already received.",
                    )
                    return
                callback["code"] = code
                completed.set()
            self._reply(
                HTTPStatus.OK, "M3 sign-in complete. You can close this browser tab."
            )

        def _reply(self, status: int, message: str) -> None:
            body = (
                "<!doctype html><meta charset=utf-8><title>M3 sign-in</title>"
                "<p>" + message + "</p>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

        def do_HEAD(self) -> None:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

        def do_OPTIONS(self) -> None:
            self.send_error(HTTPStatus.FORBIDDEN)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError:
        print("m3 auth login: could not bind the local sign-in page", file=sys.stderr)
        return 2
    server.daemon_threads = True
    server.block_on_close = False
    server.timeout = 0.25
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
    login_url = (
        base
        + "/cli/login?"
        + urlencode(
            {
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "can_store_developer": "1" if can_store_developer else "0",
            }
        )
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
    )
    thread.start()
    try:
        print("Opening M3 sign-in in your browser…", flush=True)
        if not webbrowser.open(login_url, new=2):
            print(f"Open this sign-in page: {login_url}", flush=True)
        while not completed.wait(0.25):
            if time.monotonic() - started > _LOGIN_TTL_SECONDS:
                raise RuntimeError("sign-in timed out; run m3 auth login again")
    except KeyboardInterrupt:
        print("m3 auth login: sign-in cancelled", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"m3 auth login: {exc}", file=sys.stderr)
        return 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    if callback.get("error"):
        print("M3 sign-in cancelled. Your saved credential was not changed.")
        return 0
    try:
        result = _exchange_code(base, callback["code"], verifier, redirect_uri)
    except RuntimeError as exc:
        print(f"m3 auth login: {exc}", file=sys.stderr)
        return 2
    token = result.get("token")
    if result["created"] != (token is not None):
        print(
            "m3 auth login: control-plane returned an incomplete token result",
            file=sys.stderr,
        )
        return 2
    if token is None:
        print("M3 sign-in complete. No developer token was created or changed.")
        return 0
    if not can_store_developer:
        print(
            "m3 auth login: control-plane returned a token when secure storage was unavailable",
            file=sys.stderr,
        )
        return 2
    metadata = result.get("metadata")
    saved_metadata = {
        "token_id": metadata.get("id") if isinstance(metadata, dict) else None,
        "expires_at": metadata.get("expires_at")
        if isinstance(metadata, dict)
        else None,
    }
    try:
        _save_token(base, token, saved_metadata)
    except Exception:
        print(
            "m3 auth login: developer token was created but could not be saved in the OS credential store",
            file=sys.stderr,
        )
        return 2
    print("M3 sign-in complete. Developer token saved in the OS credential store.")
    return 0


def _exchange_code(
    base: str, code: str, verifier: str, redirect_uri: str
) -> dict[str, Any]:
    body = json.dumps(
        {"code": code, "code_verifier": verifier, "redirect_uri": redirect_uri},
        separators=(",", ":"),
    ).encode("utf-8")
    req = request.Request(
        base + "/v1/cli/exchange",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
    )
    opener = request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=20) as response:
            raw = response.read(_MAX_EXCHANGE_RESPONSE + 1)
            if len(raw) > _MAX_EXCHANGE_RESPONSE:
                raise RuntimeError("control-plane response is too large")
            result = json.loads(raw)
    except error.HTTPError:
        raise RuntimeError("control-plane rejected the sign-in code") from None
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise RuntimeError(
            "could not complete sign-in with the M3 control-plane"
        ) from None
    if not isinstance(result, dict) or not isinstance(result.get("created"), bool):
        raise RuntimeError("control-plane returned an invalid sign-in response")
    token = result.get("token")
    if token is not None:
        if not isinstance(token, str) or not result["created"]:
            raise RuntimeError("control-plane returned an invalid developer token")
        try:
            validate_access_token(token)
        except CLIError:
            raise RuntimeError(
                "control-plane returned an invalid developer token"
            ) from None
    if "metadata" in result and not isinstance(result["metadata"], dict):
        raise RuntimeError("control-plane returned invalid token metadata")
    return result


__all__ = ["control_plane_url", "load_saved_token", "login", "logout", "status"]
