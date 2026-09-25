"""Shared report uploader for explicit ``m3 ci test --upload`` and ``m3 upload``.

Ordinary ``m3 test`` does not import or invoke this module.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import quote, urlparse

from m3.feedback import Feedback, project_test_attempts
from m3.storage import SQLiteExecutionStore
from m3_app.api.report_payloads import (
    build_execution_envelope,
    build_report_envelope,
)
from m3_app.api.wire import neutralize_response
from m3_app.services.execution_service import project_test_results

_RETRIES = 3
_MAX_UPLOAD_BYTES = 16 << 20
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _id_value(value: Any) -> str:
    return str(getattr(value, "root", value))


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise RuntimeError("control-plane redirect rejected")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _post(url: str, token: str, body: bytes) -> None:
    _validate_body(body, token)
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            req = request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            with _OPENER.open(req, timeout=30) as response:
                if 200 <= response.status < 300:
                    return
                if response.status < 500 and response.status != 429:
                    raise RuntimeError("control-plane upload rejected")
        except (
            error.HTTPError,
            error.URLError,
            TimeoutError,
            OSError,
            RuntimeError,
        ) as exc:
            last = exc
            if isinstance(exc, error.HTTPError) and exc.code < 500 and exc.code != 429:
                break
            if attempt + 1 < _RETRIES:
                time.sleep(0.25 * (2**attempt))
    raise RuntimeError("control-plane upload failed") from last


def _validate_body(body: bytes, token: str) -> None:
    if len(body) > _MAX_UPLOAD_BYTES:
        raise RuntimeError("control-plane upload is too large")
    if token and token.encode("utf-8") in body:
        raise RuntimeError("control-plane upload contains credential material")


def _execution_payload(store: SQLiteExecutionStore, snapshot: Any) -> dict[str, Any]:
    execution_id = snapshot.execution_id.root
    report = store.get_report(execution_id, event_limit=None, artifact_limit=None)
    if report is None:
        raise RuntimeError("execution data unavailable")
    if snapshot.lifecycle.value != "finished" or snapshot.outcome is None:
        raise RuntimeError("execution is not terminal")
    if (
        report.snapshot.execution_id != snapshot.execution_id
        or report.snapshot.lifecycle.value != "finished"
        or report.snapshot.outcome != snapshot.outcome
    ):
        raise RuntimeError("execution report identity is invalid")
    if report.events_truncated or report.artifacts_truncated:
        raise RuntimeError("execution report is truncated")
    if report.event_count != len(report.events) or report.artifact_count != len(
        report.artifacts
    ):
        raise RuntimeError("execution report is incomplete")
    trace = store.get_trace_view(execution_id)
    if trace is None:
        raise RuntimeError("trace data unavailable")
    spec = store.get_execution_spec(execution_id)
    project_name = None
    project_id = snapshot.project_id.root if snapshot.project_id is not None else None
    get_project = getattr(store, "get_project", None)
    if project_id is not None and callable(get_project):
        project = get_project(project_id)
        project_name = project[1] if project is not None else None
    test_results: tuple[dict[str, Any], ...] = ()
    if snapshot.run_id is not None:
        test_results = tuple(
            asdict(item)
            for item in project_test_results(
                project_test_attempts(store, snapshot.run_id.root), execution_id
            )
        )
    public = build_report_envelope(execution_id, spec, report, trace, test_results)
    public_execution = build_execution_envelope(snapshot, spec, project_name)
    public_snapshot = public_execution["snapshot"]
    return {
        "transport_version": 1,
        "snapshot": public_snapshot,
        "execution": public_execution,
        "report": public,
        "sort_at": snapshot.created_at.isoformat(),
        "lifecycle": snapshot.lifecycle.value,
        "outcome": snapshot.outcome.value if snapshot.outcome is not None else None,
        "project_id": project_id,
        "project_name": project_name,
    }


def upload_current_run(
    feedback: Feedback,
    store: SQLiteExecutionStore,
    directory: str | os.PathLike[str],
    *,
    base_url: str,
    token: str,
    sensitive_values: Sequence[str] = (),
) -> None:
    """Upload summary, complete current-run executions, then publish.

    Every body is cached before its first request, making retries byte-identical.
    """
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("control-plane URL must be HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError("control-plane URL must not contain credentials")
    if not token.startswith("m3pat_"):
        raise RuntimeError("control-plane token must be a personal access token")
    root = Path(directory) / "control-plane"
    if root.exists() and root.is_symlink():
        raise RuntimeError("upload cache path is a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    execution_cache = root / "executions"
    if execution_cache.is_symlink():
        raise RuntimeError("upload cache path is a symlink")
    execution_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(execution_cache, 0o700)
    run_id = _id_value(feedback.run_id)
    if not _SAFE_ID.fullmatch(run_id) or ".." in run_id:
        raise RuntimeError("invalid run ID")
    snapshots: list[Any] = []
    offset = 0
    while True:
        page = store.list_executions(run_id=run_id, limit=100, offset=offset)
        snapshots.extend(page.items)
        offset += len(page.items)
        if not page.items or offset >= page.total:
            break
    snapshots = [
        item
        for item in snapshots
        if item.run_id is not None and item.run_id.root == run_id
    ]
    execution_ids = [item.execution_id.root for item in snapshots]
    if any(not _SAFE_ID.fullmatch(item) or ".." in item for item in execution_ids):
        raise RuntimeError("invalid execution ID")
    exported = json.loads(
        (Path(directory) / "feedback.json").read_text(encoding="utf-8")
    )
    if not isinstance(exported, dict):
        raise RuntimeError("feedback export is invalid")
    summary = {
        "transport_version": 1,
        "execution_ids": execution_ids,
        "baseline_run_id": _id_value(feedback.comparison.baseline_run_id)
        if feedback.comparison is not None
        else None,
        "feedback": {
            "version": "v2",
            "feedback": neutralize_response("/api/v2/feedback/{run_id}", exported),
        },
    }
    summary_path = root / "summary.json"
    destination_path = root / "destination.json"
    if destination_path.is_symlink():
        raise RuntimeError("upload cache file is a symlink")
    destination = _json_bytes({"base_url": base_url.rstrip("/"), "run_id": run_id})
    if destination_path.is_file():
        if destination_path.read_bytes() != destination:
            raise RuntimeError("upload cache belongs to another destination")
    else:
        _cache(destination_path, destination)
    if summary_path.is_symlink():
        raise RuntimeError("upload cache file is a symlink")
    summary_bytes = (
        summary_path.read_bytes() if summary_path.is_file() else _json_bytes(summary)
    )
    try:
        cached_summary = json.loads(summary_bytes)
        cached_run_id = cached_summary["feedback"]["feedback"]["run_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("cached run summary is invalid") from exc
    if cached_run_id != run_id:
        raise RuntimeError("cached run summary does not match this run")
    _validate_body(summary_bytes, token)
    _reject_known_secrets(summary_bytes, sensitive_values)
    if len(summary_bytes) > 1 << 20:
        raise RuntimeError("control-plane summary is too large")
    if not summary_path.is_file():
        _cache(summary_path, summary_bytes)
    base = base_url.rstrip("/") + "/v1/runs/" + quote(run_id, safe="")
    pending: list[tuple[str, bytes]] = [(base + "/report", summary_bytes)]
    for snapshot in snapshots:
        execution_id = snapshot.execution_id.root
        path = execution_cache / (execution_id + ".json")
        if path.is_symlink():
            raise RuntimeError("upload cache file is a symlink")
        body = (
            path.read_bytes()
            if path.is_file()
            else _json_bytes(_execution_payload(store, snapshot))
        )
        _validate_body(body, token)
        _reject_known_secrets(body, sensitive_values)
        if path.is_file():
            cached_snapshot = json.loads(body).get("snapshot", {})
            if (
                cached_snapshot.get("execution_id") != execution_id
                or cached_snapshot.get("run_id") != run_id
            ):
                raise RuntimeError("cached execution identity does not match this run")
        if not path.is_file():
            _cache(path, body)
        pending.append(
            (
                base + "/executions/" + quote(execution_id, safe="") + "/report",
                body,
            )
        )
    for url, body in pending:
        _post(url, token, body)
    _post(base + "/publish", token, _json_bytes({"transport_version": 1}))


def _reject_known_secrets(body: bytes, values: Sequence[str]) -> None:
    if any(value and value.encode("utf-8") in body for value in values):
        raise RuntimeError("control-plane upload contains credential material")


def _cache(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


__all__ = ["upload_current_run"]

_OPENER = request.build_opener(_NoRedirect())
