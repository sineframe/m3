"""Publish one explicit, finalized local pytest run."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from m3.feedback import Feedback
from m3.storage import SQLiteExecutionStore

from .ci_credentials import (
    ACCESS_TOKEN_ENV,
    DEFAULT_CONTROL_PLANE_URL,
    access_token,
    control_plane_url,
    resolved_environment,
)
from .control_plane import inspect_current_run, upload_current_run
from .errors import CLIError

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def record_upload_inspection(
    database: Path,
    run_id: str,
    project_root: Path,
    credential_env: Sequence[str],
    environment: dict[str, str],
) -> None:
    """Inspect the finalized upload bytes against test-time credentials."""
    sources = _credential_source_names(credential_env)
    store = SQLiteExecutionStore(database)
    try:
        manifest = store.get_test_run(run_id)
        if manifest is None or manifest.get("status") != "finished":
            raise CLIError("the selected run has no finalized manifest")
        directory = project_root / ".m3" / "reports" / run_id
        feedback = _load_feedback(directory, run_id, manifest)
        digest, contains_secret = inspect_current_run(
            feedback,
            store,
            directory,
            sensitive_values=_sensitive_values(environment, source_names=sources),
        )
        manifest["upload_scan_digest"] = digest
        manifest["upload_scan_clean"] = not contains_secret
        manifest["upload_scan_sources"] = list(sources)
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
) -> None:
    """Read the exported run and send it with the existing uploader."""
    env = resolved_environment(env_file) if environment is None else environment
    if not _SAFE_ID.fullmatch(run_id) or ".." in run_id:
        raise CLIError("invalid run ID")
    url = control_plane_url(env)
    token = access_token(env, base_url=url)
    env = dict(env)
    env.setdefault(ACCESS_TOKEN_ENV, token)
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
        feedback = _load_feedback(directory, run_id, manifest)
        digest = manifest.get("upload_scan_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CLIError("this run has no valid CI credential inspection")
        if manifest.get("upload_scan_clean") is not True:
            raise CLIError("run output contains test-time credential material")
        sources = manifest.get("upload_scan_sources")
        if not isinstance(sources, list) or any(
            not isinstance(name, str) for name in sources
        ):
            raise CLIError("the selected run has invalid credential metadata")
        sensitive_values = _sensitive_values(env, source_names=sources)
        upload_current_run(
            feedback,
            store,
            directory,
            base_url=url,
            token=token,
            sensitive_values=sensitive_values,
            expected_digest=digest,
        )
    finally:
        store.close()


def _load_feedback(directory: Path, run_id: str, manifest: dict[str, Any]) -> Feedback:
    try:
        exported = json.loads((directory / "feedback.json").read_bytes())
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
    if feedback.run_id != run_id or feedback.project_id != manifest.get("project_id"):
        raise CLIError("the selected feedback does not match the run")
    return feedback


def _sensitive_values(
    environment: dict[str, str],
    *,
    source_names: Sequence[str] = (),
) -> tuple[str, ...]:
    mapped_sources = set(source_names)
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


__all__ = [
    "DEFAULT_CONTROL_PLANE_URL",
    "control_plane_url",
    "publish_run",
    "record_upload_inspection",
]
