"""Publish one explicit, finalized local pytest run."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from m3.feedback import Feedback
from m3.storage import SQLiteExecutionStore

from .ci_credentials import (
    ACCESS_TOKEN_ENV,
    DEFAULT_CONTROL_PLANE_URL,
    access_token,
    control_plane_url,
    parse_credential_mapping,
    resolved_environment,
)
from .control_plane import PublishResult, inspect_current_run, upload_current_run
from .errors import CLIError

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SCAN_FIELDS = ("upload_scan_digest", "upload_scan_clean", "upload_scan_sources")


def record_upload_inspection(
    database: Path,
    run_id: str,
    project_root: Path,
    credential_env: Sequence[str],
    environment: dict[str, str],
) -> None:
    """Run the upload checks on the finalized run and record the attested digest.

    Any earlier scan result is cleared first, so a failed inspection can never
    leave a stale digest that a later ``m3 upload`` would accept.
    """
    if not _SAFE_ID.fullmatch(run_id) or ".." in run_id:
        raise CLIError("invalid run ID")
    sources = _credential_source_names(credential_env)
    store = SQLiteExecutionStore(database)
    try:
        manifest = store.get_test_run(run_id)
        if manifest is None or manifest.get("status") != "finished":
            raise CLIError(f"run {run_id} did not finish")
        if any(field in manifest for field in _SCAN_FIELDS):
            for field in _SCAN_FIELDS:
                manifest.pop(field, None)
            store.save_test_run(run_id, manifest)
        directory = project_root / ".m3" / "reports" / run_id
        feedback = _load_feedback(directory, run_id, manifest)
        digest = inspect_current_run(
            feedback,
            store,
            directory,
            token=environment.get(ACCESS_TOKEN_ENV, ""),
            sensitive_values=_sensitive_values(environment, source_names=sources),
        )
        manifest["upload_scan_digest"] = digest
        manifest["upload_scan_clean"] = True
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
) -> PublishResult:
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
            raise CLIError(f"run {run_id} did not save all results")
        if not manifest.get("project_id"):
            raise CLIError("published runs require a valid m3.toml project identity")
        feedback = _load_feedback(directory, run_id, manifest)
        digest = manifest.get("upload_scan_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CLIError(
                f"run {run_id} cannot be uploaded: it was not started with --upload, "
                "pytest did not exit 0 or 1, or its credential scan failed; "
                "rerun the tests with --upload"
            )
        # Runs scanned before inspection raised on a match recorded ``False``.
        if manifest.get("upload_scan_clean") is not True:
            raise CLIError(
                f"run {run_id} output contains a credential from the test environment"
            )
        sources = manifest.get("upload_scan_sources")
        if not isinstance(sources, list) or any(
            not isinstance(name, str) for name in sources
        ):
            raise CLIError(f"run {run_id} has unreadable credential scan data")
        sensitive_values = _sensitive_values(env, source_names=sources)
        published = upload_current_run(
            feedback,
            store,
            directory,
            base_url=url,
            token=token,
            sensitive_values=sensitive_values,
            expected_digest=digest,
        )
        label = manifest.get("run_label")
        return PublishResult(
            published.run_label
            or (label if isinstance(label, str) and label else None),
            published.run_url,
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
            parse_credential_mapping(mapping)[2]
            for mapping in credential_env
            if "=" in mapping
        )
    )


def write_github_summary(
    environment: Mapping[str, str],
    label: str,
    run_url: str | None,
    command: str,
) -> None:
    """Append the published run to the GitHub job summary; failures only warn."""
    path = environment.get("GITHUB_STEP_SUMMARY")
    if environment.get("GITHUB_ACTIONS") != "true" or not path:
        return
    line = (
        f"M3 published [{label}]({run_url})\n" if run_url else f"M3 published {label}\n"
    )
    try:
        with open(path, "a", encoding="utf-8") as summary:
            summary.write(line)
    except OSError:
        print(f"m3 {command}: could not write the GitHub job summary", file=sys.stderr)


__all__ = [
    "DEFAULT_CONTROL_PLANE_URL",
    "PublishResult",
    "control_plane_url",
    "publish_run",
    "record_upload_inspection",
    "write_github_summary",
]
