"""Device authorization and local M3 access-token storage."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import secrets
import sys
import threading
import time
import webbrowser
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast
from urllib import error, request
from urllib.parse import parse_qsl, urlparse

from . import _http
from .ci_credentials import (
    control_plane_url as _validated_control_plane_url,
)
from .ci_credentials import (
    normalize_https_origin,
    validate_access_token,
)
from .errors import CLIError

_SERVICE = "sf-m3"
_PENDING_REVOKE_SERVICE = "sf-m3-pending-revoke"
_MAX_RESPONSE = 64 * 1024
_AUTH_DEFAULT = "https://auth.sineframe.com"
_REQUEST_TIMEOUT = 20
_METADATA_LOCK = threading.Lock()


class _RateLimited(RuntimeError):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__("rate_limited")


def _retry_after_seconds(value: str | None) -> float:
    if value is None:
        return 0.0
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value):
        try:
            return float(value)
        except (ValueError, OverflowError):
            return 0.0
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, retry_at.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 0.0


def control_plane_url() -> str:
    try:
        return _validated_control_plane_url(os.environ)
    except CLIError as exc:
        raise ValueError("M3 control-plane URL must be an HTTPS origin") from exc


def _origin(raw: str, setting: str) -> str:
    return normalize_https_origin(raw, setting)


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
    temp = path.with_name("." + path.name + "." + secrets.token_hex(8))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("local credential metadata is unreadable") from exc
    if not isinstance(value, dict):
        raise RuntimeError("local credential metadata is invalid")
    return value


def _update_metadata(update: Any) -> None:
    """Serialize read-modify-write operations in this process atomically."""
    path = _metadata_path()
    with _METADATA_LOCK, _metadata_file_lock(path):
        current = _read_metadata(path) if path.exists() else {}
        update(current)
        _write_metadata(path, current)


@contextmanager
def _metadata_file_lock(path: Path) -> Any:
    """A crash-safe advisory lock around metadata read-modify-write."""
    lock = path.with_name("." + path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(lock, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            for attempt in range(10):
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                    break
                except OSError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _installation_id() -> str:
    path = _metadata_path()
    with _METADATA_LOCK, _metadata_file_lock(path):
        data = _read_metadata(path)
        value = data.get("_installation_id")
        if isinstance(value, str):
            if len(value) == 32 and all(c in "0123456789abcdef" for c in value):
                return value
        value = secrets.token_hex(16)
        # Installation metadata is shared with credential metadata.  Never replace
        # that document merely because this is the first login on this machine.
        data["_installation_id"] = value
        _write_metadata(path, data)
        return value


def _device_name() -> str:
    raw = os.environ.get("COMPUTERNAME" if sys.platform == "win32" else "HOSTNAME", "")
    safe = "".join(c for c in raw if c.isascii() and (c.isalnum() or c in "-_"))[:48]
    return "M3 CLI" + (f" ({safe})" if safe else "")


def _keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("OS credential storage is unavailable") from exc
    backend = keyring.get_keyring()
    trusted = {
        ("keyring.backends.macOS", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.libsecret", "Keyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    }
    if (type(backend).__module__, type(backend).__name__) not in trusted:
        raise RuntimeError("a supported OS credential store is unavailable")
    return keyring


def _account(base: str) -> str:
    return "m3_" + hashlib.sha256(base.encode()).hexdigest()[:32]


def _legacy_account(base: str) -> str:
    parsed = urlparse(base)
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", parsed.netloc.lower())
    digest = hashlib.sha256(origin.encode()).hexdigest()[:32]
    return f"m3_{slug[:20]}_{digest}"


def load_saved_token(base_url: str) -> str | None:
    try:
        keyring = _keyring()
        account = _account(base_url)
        token = keyring.get_password(_SERVICE, account)
        legacy = _legacy_account(base_url)
        if token is None and legacy != account:
            token = keyring.get_password(_SERVICE, legacy)
            if isinstance(token, str):
                keyring.set_password(_SERVICE, account, token)
                keyring.delete_password(_SERVICE, legacy)
        if token is not None and not isinstance(token, str):
            raise RuntimeError("OS credential store returned invalid data")
        return token
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("could not read OS credential store") from None


def _probe_keyring() -> None:
    keyring = _keyring()
    account = secrets.token_hex(16)
    try:
        keyring.set_password(_SERVICE + "-probe", account, secrets.token_urlsafe(32))
        keyring.delete_password(_SERVICE + "-probe", account)
    except Exception:
        try:
            keyring.delete_password(_SERVICE + "-probe", account)
        except Exception:
            pass
        raise RuntimeError("OS credential store cannot save credentials") from None


def _save_token(base: str, token: str, metadata: dict[str, Any]) -> None:
    keyring = _keyring()
    account = _account(base)
    old = keyring.get_password(_SERVICE, account)
    keyring.set_password(_SERVICE, account, token)
    try:
        _update_metadata(lambda all_metadata: all_metadata.__setitem__(base, metadata))
    except Exception:
        try:
            if old is None:
                keyring.delete_password(_SERVICE, account)
            else:
                keyring.set_password(_SERVICE, account, old)
        except Exception:
            pass
        raise RuntimeError("could not save credential metadata securely") from None


def _remove_saved_token(base: str) -> bool:
    keyring = _keyring()
    try:
        for account in {_account(base), _legacy_account(base)}:
            if keyring.get_password(_SERVICE, account) is not None:
                keyring.delete_password(_SERVICE, account)
    except Exception:
        return False
    try:
        _update_metadata(lambda metadata: metadata.pop(base, None))
    except Exception:
        pass
    return True


def _pending_revoke_token(base: str) -> str | None:
    try:
        token = _keyring().get_password(_PENDING_REVOKE_SERVICE, _account(base))
    except Exception:
        raise RuntimeError("could not read pending credential cleanup") from None
    if token is not None and not isinstance(token, str):
        raise RuntimeError("OS credential store returned invalid cleanup data")
    return token


def _remember_pending_revoke(base: str, token: str) -> None:
    try:
        _keyring().set_password(_PENDING_REVOKE_SERVICE, _account(base), token)
    except Exception:
        raise RuntimeError(
            "could not preserve the previous credential for revocation"
        ) from None


def _clear_pending_revoke(base: str) -> None:
    keyring = _keyring()
    account = _account(base)
    try:
        if keyring.get_password(_PENDING_REVOKE_SERVICE, account) is not None:
            keyring.delete_password(_PENDING_REVOKE_SERVICE, account)
    except Exception:
        raise RuntimeError("could not clear completed credential cleanup") from None


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("redirect rejected")


def _read_limited(stream: Any) -> bytes:
    raw = stream.read(_MAX_RESPONSE + 1)
    if len(raw) > _MAX_RESPONSE:
        raise RuntimeError("server response is too large")
    return cast(bytes, raw)


def _json_request(
    url: str,
    method: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    *,
    expect_no_content: bool = False,
) -> dict[str, Any]:
    request_id = _http.new_request_id()
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-store",
        **_http.base_headers(request_id),
    }
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, data=data, method=method, headers=headers)
    try:
        with request.build_opener(_NoRedirect()).open(
            req, timeout=_REQUEST_TIMEOUT
        ) as response:
            if response.status == 204:
                if not expect_no_content:
                    raise RuntimeError("server returned an invalid response")
                return {}
            raw = _read_limited(response)
        result = json.loads(raw)
    except error.HTTPError as exc:
        try:
            envelope = json.loads(_read_limited(exc))
            err = envelope.get("error")
            code = err.get("code") if isinstance(err, dict) else None
            if code not in {
                "authorization_pending",
                "slow_down",
                "access_denied",
                "expired_token",
                "invalid_grant",
            } and not (exc.code == 429 and code == "rate_limited"):
                code = None
        except RuntimeError as inner:
            raise RuntimeError(f"{inner} (ref: {request_id})") from None
        except Exception:
            code = None
        if code == "rate_limited" and exc.code == 429:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raise _RateLimited(_retry_after_seconds(retry_after)) from None
        if exc.code >= 500:
            raise RuntimeError(
                f"server rejected the request (ref: {request_id})"
            ) from None
        if code:
            raise RuntimeError(code) from None
        raise RuntimeError(f"server rejected the request (ref: {request_id})") from None
    except RuntimeError as exc:
        raise RuntimeError(f"{exc} (ref: {request_id})") from None
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise RuntimeError(
            f"could not contact the M3 control-plane (ref: {request_id})"
        ) from None
    if not isinstance(result, dict):
        raise RuntimeError(f"server returned an invalid response (ref: {request_id})")
    return result


def _revoke_token(base: str, token: str) -> None:
    _json_request(
        base + "/v1/cli/session",
        "DELETE",
        token=token,
        expect_no_content=True,
    )


def status() -> int:
    try:
        base = control_plane_url()
        env_token = os.environ.get("M3_ACCESS_TOKEN")
        if env_token is not None:
            validate_access_token(env_token)
            print(
                "M3_ACCESS_TOKEN is set in the current environment "
                "(CI token validity is not checked by auth status)."
            )
        pending = _pending_revoke_token(base)
        if pending is not None:
            try:
                _revoke_token(base, pending)
                _clear_pending_revoke(base)
            except RuntimeError:
                print(
                    "Warning: the previous CLI credential still needs server revocation.",
                    file=sys.stderr,
                )
        token = load_saved_token(base)
        if not token:
            print("No local M3 CLI credential is configured.")
            return 0
        result = _json_request(base + "/v1/cli/session", "GET", token=token)
        metadata = result.get("metadata")
        fields = ("id", "kind", "org_id", "name", "created_at", "expires_at")
        if (
            not isinstance(metadata, dict)
            or metadata.get("kind") != "cli"
            or any(not isinstance(metadata.get(key), str) for key in fields)
        ):
            raise RuntimeError("server returned invalid session metadata")
        print("M3 CLI credential is valid.")
        for key, label in (
            ("name", "Token"),
            ("org_id", "Organization"),
            ("id", "Token ID"),
            ("expires_at", "Expires"),
        ):
            if isinstance(metadata.get(key), str):
                print(f"{label}: {metadata[key]}")
        return 0
    except (RuntimeError, ValueError, CLIError) as exc:
        print(f"m3 auth status: {exc}", file=sys.stderr)
        return 2


def logout() -> int:
    try:
        base = control_plane_url()
        # Logout is deliberately local-only: an explicit environment token is
        # not the CLI credential that this command owns.
        token = load_saved_token(base)
        pending = _pending_revoke_token(base)
        if not token and not pending:
            print("No local M3 CLI credential is configured.")
            return 0
        if token:
            _revoke_token(base, token)
            if not _remove_saved_token(base):
                raise RuntimeError(
                    "remote credential revoked, but local credential could not be removed"
                )
        if pending is not None:
            _revoke_token(base, pending)
            _clear_pending_revoke(base)
        print("M3 CLI credential revoked and removed.")
        return 0
    except (RuntimeError, ValueError, CLIError) as exc:
        print(f"m3 auth logout: {exc}", file=sys.stderr)
        return 2


def _validate_verification_url(
    value: Any, auth_origin: str, *, complete: bool, user_code: str | None = None
) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise RuntimeError("server returned an invalid verification URL")
    parsed = urlparse(value)
    expected = urlparse(auth_origin)
    try:
        same_origin = (
            parsed.scheme == "https"
            and parsed.hostname == expected.hostname
            and (parsed.port or 443) == (expected.port or 443)
        )
    except ValueError:
        same_origin = False
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        pairs = []
    next_value = (
        f"/cli/authorize?code={user_code}" if isinstance(user_code, str) else None
    )
    valid_query = (
        len(pairs) == 1 and pairs[0][0] == "next" and pairs[0][1] == next_value
    )
    if (
        not same_origin
        or parsed.path != "/sign-in"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.netloc.endswith(".")
        or (not complete and parsed.query)
        or (complete and not valid_query)
    ):
        raise RuntimeError("server returned an invalid verification URL")
    return value


def _cli_version() -> str:
    return _http.cli_version()


def login() -> int:
    try:
        base = control_plane_url()
        auth_origin = _origin(
            os.environ.get("M3_AUTH_URL", _AUTH_DEFAULT), "M3_AUTH_URL"
        )
        _probe_keyring()
        pending = _pending_revoke_token(base)
        if pending is not None:
            _revoke_token(base, pending)
            _clear_pending_revoke(base)
        previous_token = load_saved_token(base)
        authorization = _json_request(
            base + "/v1/cli/device/authorization",
            "POST",
            {
                "installation_id": _installation_id(),
                "device_name": _device_name(),
                "cli_version": _cli_version(),
            },
        )
        required = (
            "device_code",
            "user_code",
            "verification_uri",
            "verification_uri_complete",
            "expires_in",
            "interval",
        )
        if any(key not in authorization for key in required):
            raise RuntimeError("server returned incomplete device authorization")
        code, user_code = authorization["device_code"], authorization["user_code"]
        if (
            not isinstance(code, str)
            or not 1 <= len(code) <= 4096
            or not isinstance(user_code, str)
            or not 4 <= len(user_code) <= 64
            or any(
                not (char.isascii() and (char.isalnum() or char == "-"))
                for char in user_code
            )
            or user_code.startswith("-")
            or user_code.endswith("-")
        ):
            raise RuntimeError("server returned invalid device authorization")
        _validate_verification_url(
            authorization["verification_uri"], auth_origin, complete=False
        )
        complete = _validate_verification_url(
            authorization["verification_uri_complete"],
            auth_origin,
            complete=True,
            user_code=user_code,
        )
        expires, interval = authorization["expires_in"], authorization["interval"]
        if (
            type(expires) is not int
            or not 1 <= expires <= 3600
            or type(interval) is not int
            or not 1 <= interval <= 60
        ):
            raise RuntimeError("server returned invalid device authorization")
        deadline = time.monotonic() + expires
        poll_delay: float = interval
        try:
            opened = bool(webbrowser.open(complete, new=2))
        except Exception:
            opened = False
        if not opened:
            print(f"Open this sign-in page: {complete}", flush=True)
            print(f"Confirm the code shown in the browser: {user_code}", flush=True)
        else:
            print(f"Complete sign-in with code: {user_code}", flush=True)
        while time.monotonic() < deadline:
            time.sleep(min(poll_delay, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            poll_delay = interval
            try:
                result = _json_request(
                    base + "/v1/cli/device/token", "POST", {"device_code": code}
                )
            except _RateLimited as exc:
                poll_delay = max(interval, exc.retry_after) + random.uniform(0.1, 0.5)
                continue
            except RuntimeError as exc:
                if str(exc) == "authorization_pending":
                    continue
                if str(exc) == "slow_down":
                    interval += 5
                    poll_delay = interval
                    continue
                raise
            token, metadata = result.get("access_token"), result.get("metadata")
            fields = ("id", "kind", "org_id", "name", "created_at", "expires_at")
            if (
                not isinstance(token, str)
                or result.get("token_type") != "Bearer"
                or not isinstance(metadata, dict)
                or any(
                    not isinstance(metadata.get(k), str) or not metadata[k]
                    for k in fields
                )
                or metadata.get("kind") != "cli"
            ):
                raise RuntimeError("server returned incomplete CLI token metadata")
            try:
                validate_access_token(token)
            except CLIError:
                raise RuntimeError("server returned an invalid CLI token") from None
            try:
                if previous_token is not None and previous_token != token:
                    _remember_pending_revoke(base, previous_token)
                _save_token(base, token, {key: metadata[key] for key in fields})
            except Exception:
                try:
                    _clear_pending_revoke(base)
                except Exception:
                    pass
                try:
                    _revoke_token(base, token)
                except Exception:
                    raise RuntimeError(
                        "token was issued but could not be saved; remote revocation also failed"
                    ) from None
                raise RuntimeError(
                    "token was issued but could not be saved; remote token revoked"
                ) from None
            if previous_token is not None and previous_token != token:
                try:
                    _revoke_token(base, previous_token)
                    _clear_pending_revoke(base)
                except RuntimeError:
                    print(
                        "m3 auth login: new credential saved, but the previous "
                        "credential still needs server revocation",
                        file=sys.stderr,
                    )
                    return 2
            print("M3 CLI credential saved in OS credential store.")
            return 0
        raise RuntimeError("device authorization expired")
    except KeyboardInterrupt:
        print(
            "m3 auth login: cancelled; stopped polling (server authorization may remain active until expiry)",
            file=sys.stderr,
        )
        return 130
    except (RuntimeError, ValueError, CLIError) as exc:
        print(f"m3 auth login: {exc}", file=sys.stderr)
        return 2
