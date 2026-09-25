"""Publish one explicit, finalized local pytest run."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

from m3.feedback import Feedback
from m3.storage import SQLiteExecutionStore

from .ci_credentials import (
    ACCESS_TOKEN_ENV,
    CONTROL_PLANE_URL_ENV,
    access_token,
    resolved_environment,
)
from .control_plane import upload_current_run
from .errors import CLIError

DEFAULT_CONTROL_PLANE_URL = "https://control-plane-ulwh0w.fly.dev"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def control_plane_url(environment: dict[str, str]) -> str:
    url = environment.get(CONTROL_PLANE_URL_ENV, DEFAULT_CONTROL_PLANE_URL).rstrip("/")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise CLIError("M3_CONTROL_PLANE_URL must be an HTTPS origin")
    return url


def record_credential_sources(
    database: Path,
    run_id: str,
    credential_env: Sequence[str],
    environment: dict[str, str],
) -> None:
    """Persist names and keyed fingerprints for safe upload retries."""
    sources = {
        name
        for name in _credential_source_names(credential_env)
        if environment.get(name)
    }
    sources.update(_scan_source_names(environment))
    store = SQLiteExecutionStore(database)
    try:
        manifest = store.get_test_run(run_id)
        if manifest is None:
            raise CLIError("the selected run has no saved manifest")
        fingerprints = {}
        if sources:
            key = _load_or_create_scan_key(database)
            fingerprints = {
                name: _value_fingerprint(key, name, environment[name])
                for name in sorted(sources)
                if environment.get(name)
            }
        manifest["upload_scan_sources"] = sorted(sources)
        manifest["upload_scan_fingerprints"] = fingerprints
        store.save_test_run(run_id, manifest)
    finally:
        store.close()


def publish_run(
    run_id: str,
    *,
    project_root: Path,
    database: Path,
    environment: dict[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
    credential_env: Sequence[str] = (),
) -> None:
    """Read the exported run and send it with the existing uploader."""
    env = resolved_environment(env_file) if environment is None else environment
    if not _SAFE_ID.fullmatch(run_id) or ".." in run_id:
        raise CLIError("invalid run ID")
    url = control_plane_url(env)
    token = access_token(env, base_url=url)
    env = dict(env)
    env.setdefault("M3_ACCESS_TOKEN", token)
    directory = project_root / ".m3" / "reports" / run_id
    feedback_path = directory / "feedback.json"
    if not feedback_path.is_file() or feedback_path.is_symlink():
        raise CLIError("the selected run has no exported feedback")
    store = SQLiteExecutionStore(database)
    try:
        manifest = store.get_test_run(run_id)
        if manifest is None or manifest.get("status") != "finished":
            raise CLIError("the selected run is missing or incomplete")
        if manifest.get("persistence_error") or manifest.get("worker_errors"):
            raise CLIError("the selected run has incomplete persisted data")
        if not manifest.get("project_id"):
            raise CLIError("published runs require a valid m3.toml project identity")
        try:
            exported = json.loads(feedback_path.read_bytes())
            if not isinstance(exported, dict):
                raise ValueError("invalid feedback bundle")
            feedback = Feedback.model_validate(
                {
                    key: value
                    for key, value in exported.items()
                    if key in Feedback.model_fields
                }
            )
        except Exception as exc:
            raise CLIError("the selected run has invalid exported feedback") from exc
        if feedback.run_id != run_id or feedback.project_id != manifest.get(
            "project_id"
        ):
            raise CLIError("the selected feedback does not match the run")
        stored_sources = manifest.get("upload_scan_sources", [])
        fingerprints = manifest.get("upload_scan_fingerprints", {})
        if (
            "upload_scan_sources" not in manifest
            or "upload_scan_fingerprints" not in manifest
        ):
            raise CLIError(
                "this run predates credential fingerprints and cannot be safely uploaded"
            )
        if (
            not isinstance(stored_sources, list)
            or any(not isinstance(value, str) for value in stored_sources)
            or not isinstance(fingerprints, dict)
            or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in fingerprints.items()
            )
        ):
            raise CLIError("the selected run has invalid credential metadata")
        explicit_sources = _credential_source_names(credential_env)
        source_names = tuple(
            dict.fromkeys(
                (*stored_sources, *explicit_sources, *_scan_source_names(env))
            )
        )
        if not set(fingerprints).issubset(stored_sources):
            raise CLIError("the saved run has incomplete credential fingerprints")
        if any(env.get(name) and name not in fingerprints for name in stored_sources):
            raise CLIError("the saved run has incomplete credential fingerprints")
        if any(env.get(name) and name not in fingerprints for name in explicit_sources):
            raise CLIError("the saved run has incomplete credential fingerprints")
        if fingerprints:
            key = _load_scan_key(database)
            for name, expected in fingerprints.items():
                value = env.get(name)
                if not value or not hmac.compare_digest(
                    _value_fingerprint(key, name, value), expected
                ):
                    raise CLIError(
                        "credential values for this run are unavailable or changed; "
                        "supply the original environment with --env-file"
                    )
        elif stored_sources:
            raise CLIError("the saved run has no credential fingerprints")
        sensitive_values = _sensitive_values(
            env, credential_env, source_names=source_names
        )
        upload_current_run(
            feedback,
            store,
            directory,
            base_url=url,
            token=token,
            sensitive_values=sensitive_values,
        )
    finally:
        store.close()


def _sensitive_values(
    environment: dict[str, str],
    credential_env: Sequence[str],
    *,
    source_names: Sequence[str] = (),
) -> tuple[str, ...]:
    mapped_sources = set(source_names) | set(_credential_source_names(credential_env))
    return tuple(
        value
        for name, value in environment.items()
        if value
        and (
            name in mapped_sources
            or (
                len(value) >= 8
                and any(
                    part in name.upper()
                    for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")
                )
            )
        )
    )


def _credential_source_names(credential_env: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            mapping.split("=", 1)[1] for mapping in credential_env if "=" in mapping
        )
    )


def _scan_source_names(environment: dict[str, str]) -> set[str]:
    return {
        name
        for name, value in environment.items()
        if value
        and (
            name != ACCESS_TOKEN_ENV
            and len(value) >= 8
            and any(
                part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")
            )
        )
    }


def _scan_key_path(database: Path) -> Path:
    from .auth import _metadata_path

    identity = str(Path(database).expanduser().resolve()).encode("utf-8")
    filename = "upload-scan-" + hashlib.sha256(identity).hexdigest() + ".key"
    return _metadata_path().parent / "upload-scan" / filename


def _load_scan_key(database: Path) -> bytes:
    path = _scan_key_path(database)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise CLIError("upload scan key is not a regular file")
        if os.name != "nt" and info.st_mode & 0o077:
            raise CLIError("upload scan key permissions are too broad")
        if os.name != "nt" and info.st_uid != os.getuid():
            raise CLIError("upload scan key has an unexpected owner")
        key = path.read_bytes()
    except FileNotFoundError as exc:
        raise CLIError(
            "local upload scan key is unavailable; retry on the machine that ran the tests"
        ) from exc
    if len(key) != 32:
        raise CLIError("local upload scan key is invalid")
    return key


def _load_or_create_scan_key(database: Path) -> bytes:
    path = _scan_key_path(database)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = path.parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
        raise CLIError("upload scan key directory is not secure")
    if os.name != "nt" and parent_info.st_uid != os.getuid():
        raise CLIError("upload scan key directory has an unexpected owner")
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".upload-scan-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        key = secrets.token_bytes(32)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            return _load_scan_key(database)
        return key
    except Exception:
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _value_fingerprint(key: bytes, name: str, value: str) -> str:
    message = name.encode("utf-8") + b"\0" + value.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


__all__ = [
    "DEFAULT_CONTROL_PLANE_URL",
    "control_plane_url",
    "publish_run",
    "record_credential_sources",
]
