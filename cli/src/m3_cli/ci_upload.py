"""Publish one explicit, finalized local pytest run."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from m3.feedback import Feedback
from m3.storage import SQLiteExecutionStore

from .ci_credentials import CONTROL_PLANE_URL_ENV, access_token, resolved_environment
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
        sensitive_values = tuple(
            value
            for name, value in env.items()
            if len(value) >= 8
            and any(
                part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")
            )
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


__all__ = ["DEFAULT_CONTROL_PLANE_URL", "control_plane_url", "publish_run"]
