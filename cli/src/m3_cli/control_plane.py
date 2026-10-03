"""Report uploader for ``m3 test --upload``, ``m3 ci test --upload``, and
``m3 upload``.

Commands without ``--upload`` do not import this module.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import quote, urlparse, urlsplit

from m3 import _timing
from m3.feedback import Feedback, load_run_entries, project_test_attempts
from m3.hosted_comparison import comparison_input as project_comparison_input
from m3.storage import SQLiteExecutionStore
from m3_app.api.report_payloads import (
    build_execution_envelope,
    build_report_envelope,
)
from m3_app.api.wire import neutralize_response
from m3_app.services.execution_service import project_test_results

from .errors import UploadError

_RETRIES = 3
_MAX_EXECUTION_BYTES = 16 << 20
_MAX_SUMMARY_BYTES = 6 << 20
_MAX_ERROR_BODY_BYTES = 4 << 10
_MAX_RESPONSE_BYTES = 64 << 10
_MAX_RUN_LABEL = 256
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ERROR_CODE = re.compile(r"[a-z_]{1,64}")


@dataclass(frozen=True)
class PublishResult:
    """What the server reported when publishing: its label and report link."""

    run_label: str | None
    run_url: str | None


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
        raise UploadError(
            "the M3 server redirected the upload; redirects are not followed",
            retryable=False,
        )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_json_object(response: Any) -> dict[str, Any] | None:
    """Decode at most 64 KiB of ``response`` as a JSON object, else ``None``."""
    try:
        decoded = json.loads(response.read(_MAX_RESPONSE_BYTES))
    except (OSError, http.client.HTTPException, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _post(url: str, token: str, body: bytes, subject: str) -> dict[str, Any] | None:
    """POST ``body``, retrying rate limits, server errors, and network failures.

    ``subject`` names what is sent. It must contain only validated identifiers
    because it becomes part of the user-facing failure message.
    """
    status: int | None = None
    code: str | None = None
    for attempt in range(_RETRIES):
        if attempt:
            time.sleep(0.25 * (2 ** (attempt - 1)))
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
        try:
            # The opener raises HTTPError for every non-2xx response.
            with _OPENER.open(req, timeout=30) as response:
                return _read_json_object(response)
        except error.HTTPError as exc:
            status, code = exc.code, _error_code(exc)
            if status < 500 and status != 429:
                raise UploadError(
                    f"the M3 server rejected {subject} ({_http_detail(status, code)})",
                    retryable=False,
                    status=status,
                    code=code,
                ) from None
        except (OSError, http.client.HTTPException):
            status = code = None
    if status is None:
        raise UploadError(
            f"could not reach the M3 server to send {subject} "
            f"after {_RETRIES} attempts",
            retryable=True,
        )
    raise UploadError(
        f"the M3 server did not accept {subject} after {_RETRIES} attempts "
        f"({_http_detail(status, code)})",
        retryable=True,
        status=status,
        code=code,
    )


def _error_code(response: error.HTTPError) -> str | None:
    """Return the response's ``error.code`` when it is a plain identifier."""
    try:
        envelope = json.loads(response.read(_MAX_ERROR_BODY_BYTES))
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        return None
    failure = envelope.get("error") if isinstance(envelope, dict) else None
    code = failure.get("code") if isinstance(failure, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else None


def _http_detail(status: int, code: str | None) -> str:
    return f"HTTP {status} {code}" if code else f"HTTP {status}"


def _body_subject(run_id: str, execution_id: str) -> str:
    """Name an upload body; the empty execution ID denotes the run summary."""
    if execution_id:
        return f"execution {execution_id} report"
    return f"run {run_id} summary"


def _check_body(
    run_id: str,
    execution_id: str,
    body: bytes,
    token: str,
    sensitive_values: Sequence[str],
) -> None:
    """Reject a body that is too large or contains a known credential.

    Inspection and upload share these checks, so a run that passes inspection
    is not refused later for a reason inspection could have reported.
    """
    subject = _body_subject(run_id, execution_id)
    limit = _MAX_EXECUTION_BYTES if execution_id else _MAX_SUMMARY_BYTES
    if len(body) > limit:
        raise UploadError(
            f"{subject} is {len(body)} bytes; limit is {limit}", retryable=False
        )
    if token and token.encode("utf-8") in body:
        raise UploadError(f"{subject} contains the M3 access token", retryable=False)
    if any(value and value.encode("utf-8") in body for value in sensitive_values):
        raise UploadError(
            f"{subject} contains a credential from the test environment",
            retryable=False,
        )


def _run_projection(
    store: SQLiteExecutionStore, run_id: str
) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]:
    """Load the run once; return entries by execution ID and projected attempts."""
    entries = load_run_entries(store, run_id)
    by_id = {entry.report.snapshot.execution_id.root: entry for entry in entries}
    return by_id, project_test_attempts(store, run_id, entries=entries)


def _execution_payload(
    store: SQLiteExecutionStore,
    snapshot: Any,
    entry: Any | None,
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one execution body, reusing the run's preloaded ``entry`` where it loaded successfully."""
    execution_id = snapshot.execution_id.root
    report = (
        entry.report
        if entry is not None
        else store.get_report(execution_id, event_limit=None, artifact_limit=None)
    )
    if report is None:
        raise UploadError(
            f"execution {execution_id} has no saved report", retryable=False
        )
    if snapshot.lifecycle.value != "finished" or snapshot.outcome is None:
        raise UploadError(f"execution {execution_id} did not finish", retryable=False)
    if (
        report.snapshot.execution_id != snapshot.execution_id
        or report.snapshot.lifecycle.value != "finished"
        or report.snapshot.outcome != snapshot.outcome
    ):
        raise UploadError(
            f"execution {execution_id} saved report does not match the execution",
            retryable=False,
        )
    if report.events_truncated or report.artifacts_truncated:
        raise UploadError(
            f"execution {execution_id} saved report is truncated", retryable=False
        )
    if report.event_count != len(report.events) or report.artifact_count != len(
        report.artifacts
    ):
        raise UploadError(
            f"execution {execution_id} saved report is incomplete", retryable=False
        )
    trace = (
        entry.trace
        if entry is not None and entry.trace is not None
        else store.get_trace_view(execution_id)
    )
    if trace is None:
        raise UploadError(
            f"execution {execution_id} has no saved trace", retryable=False
        )
    spec = entry.spec if entry is not None else store.get_execution_spec(execution_id)
    project_name = None
    project_id = snapshot.project_id.root if snapshot.project_id is not None else None
    get_project = getattr(store, "get_project", None)
    if project_id is not None and callable(get_project):
        project = get_project(project_id)
        project_name = project[1] if project is not None else None
    test_results = tuple(
        asdict(item) for item in project_test_results(attempts, execution_id)
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


def _current_run_summary(
    feedback: Feedback,
    store: SQLiteExecutionStore,
    directory: str | os.PathLike[str],
) -> tuple[str, list[Any], bytes]:
    run_id = _id_value(feedback.run_id)
    if not _SAFE_ID.fullmatch(run_id) or ".." in run_id:
        raise UploadError("invalid run ID", retryable=False)
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
        raise UploadError(f"run {run_id} has an invalid execution ID", retryable=False)
    feedback_text = (Path(directory) / "feedback.json").read_text(encoding="utf-8")
    try:
        exported = json.loads(feedback_text)
    except ValueError as exc:
        raise UploadError(
            f"run {run_id} feedback.json is invalid", retryable=False
        ) from exc
    if not isinstance(exported, dict):
        raise UploadError(f"run {run_id} feedback.json is invalid", retryable=False)
    manifest = store.get_test_run(run_id)
    comparison_input = None
    if manifest is not None:
        comparison_input = project_comparison_input(
            manifest, store.list_test_results(run_id)
        )
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
    if comparison_input is not None:
        summary["comparison_input"] = comparison_input
    return run_id, snapshots, _json_bytes(summary)


def _checked_run_bodies(
    feedback: Feedback,
    store: SQLiteExecutionStore,
    directory: str | os.PathLike[str],
    token: str,
    sensitive_values: Sequence[str],
) -> Iterator[tuple[str, bytes]]:
    """Render the run's upload bodies, applying the upload checks to each."""
    run_id, snapshots, summary = _current_run_summary(feedback, store, directory)
    _check_body(run_id, "", summary, token, sensitive_values)
    yield "", summary
    entries_by_id, attempts = _run_projection(store, run_id)
    for snapshot in snapshots:
        execution_id = snapshot.execution_id.root
        body = _json_bytes(
            _execution_payload(
                store, snapshot, entries_by_id.get(execution_id), attempts
            )
        )
        _check_body(run_id, execution_id, body, token, sensitive_values)
        yield execution_id, body


def _payload_attestation(bodies: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for execution_id, body in bodies:
        identifier = execution_id.encode("utf-8")
        digest.update(len(identifier).to_bytes(4, "big"))
        digest.update(identifier)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def inspect_current_run(
    feedback: Feedback,
    store: SQLiteExecutionStore,
    directory: str | os.PathLike[str],
    *,
    token: str,
    sensitive_values: Sequence[str],
) -> str:
    """Run the upload checks locally and attest to the exact outgoing bytes."""
    return _payload_attestation(
        _checked_run_bodies(feedback, store, directory, token, sensitive_values)
    )


def upload_current_run(
    feedback: Feedback,
    store: SQLiteExecutionStore,
    directory: str | os.PathLike[str],
    *,
    base_url: str,
    token: str,
    sensitive_values: Sequence[str] = (),
    expected_digest: str | None = None,
) -> PublishResult:
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
        raise UploadError(
            "M3_CONTROL_PLANE_URL must be an HTTPS origin", retryable=False
        )
    if parsed.username is not None or parsed.password is not None:
        raise UploadError(
            "M3_CONTROL_PLANE_URL must not contain credentials", retryable=False
        )
    if not token.startswith("m3pat_"):
        raise UploadError(
            "M3_ACCESS_TOKEN must be an M3 personal access token", retryable=False
        )
    run_id, snapshots, rendered_summary = _current_run_summary(
        feedback, store, directory
    )
    root = Path(directory) / "control-plane"
    if root.exists() and root.is_symlink():
        raise UploadError(f"run {run_id} upload cache is a symlink", retryable=False)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    execution_cache = root / "executions"
    if execution_cache.is_symlink():
        raise UploadError(f"run {run_id} upload cache is a symlink", retryable=False)
    execution_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(execution_cache, 0o700)
    needs_projection = any(
        not (execution_cache / (snapshot.execution_id.root + ".json")).is_file()
        or (execution_cache / (snapshot.execution_id.root + ".json")).is_symlink()
        for snapshot in snapshots
    )
    projection = _run_projection(store, run_id) if needs_projection else None
    summary_path = root / "summary.json"
    destination_path = root / "destination.json"
    if destination_path.is_symlink():
        raise UploadError(f"run {run_id} upload cache is a symlink", retryable=False)
    destination = _json_bytes({"base_url": base_url.rstrip("/"), "run_id": run_id})
    if destination_path.is_file():
        if destination_path.read_bytes() != destination:
            raise UploadError(
                f"run {run_id} upload was started for a different M3 server",
                retryable=False,
            )
    else:
        _cache(destination_path, destination)
    if summary_path.is_symlink():
        raise UploadError(f"run {run_id} upload cache is a symlink", retryable=False)
    pending_cache: list[tuple[Path, bytes]] = []
    summary_bytes = (
        summary_path.read_bytes() if summary_path.is_file() else rendered_summary
    )
    try:
        cached_summary = json.loads(summary_bytes)
        cached_run_id = cached_summary["feedback"]["feedback"]["run_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UploadError(
            f"run {run_id} upload cache is corrupt", retryable=False
        ) from exc
    if cached_run_id != run_id:
        raise UploadError(
            f"run {run_id} upload cache belongs to another run", retryable=False
        )
    _check_body(run_id, "", summary_bytes, token, sensitive_values)
    if not summary_path.is_file():
        pending_cache.append((summary_path, summary_bytes))
    base = base_url.rstrip("/") + "/v1/runs/" + quote(run_id, safe="")
    pending: list[tuple[str, bytes, str]] = [
        (base + "/report", summary_bytes, _body_subject(run_id, ""))
    ]
    actual_bodies: list[tuple[str, bytes]] = [("", summary_bytes)]
    for snapshot in snapshots:
        execution_id = snapshot.execution_id.root
        path = execution_cache / (execution_id + ".json")
        if path.is_symlink():
            raise UploadError(
                f"execution {execution_id} upload cache is a symlink",
                retryable=False,
            )
        if path.is_file():
            body = path.read_bytes()
        else:
            assert projection is not None
            entries_by_id, attempts = projection
            body = _json_bytes(
                _execution_payload(
                    store, snapshot, entries_by_id.get(execution_id), attempts
                )
            )
        _check_body(run_id, execution_id, body, token, sensitive_values)
        if path.is_file():
            try:
                cached_snapshot = json.loads(body)["snapshot"]
                cached_identity = (
                    cached_snapshot["execution_id"],
                    cached_snapshot["run_id"],
                )
            except (ValueError, KeyError, TypeError) as exc:
                raise UploadError(
                    f"execution {execution_id} upload cache is corrupt",
                    retryable=False,
                ) from exc
            if cached_identity != (execution_id, run_id):
                raise UploadError(
                    f"execution {execution_id} upload cache belongs to another run",
                    retryable=False,
                )
        else:
            pending_cache.append((path, body))
        actual_bodies.append((execution_id, body))
        pending.append(
            (
                base + "/executions/" + quote(execution_id, safe="") + "/report",
                body,
                _body_subject(run_id, execution_id),
            )
        )
    if (
        expected_digest is not None
        and _payload_attestation(actual_bodies) != expected_digest
    ):
        raise UploadError(
            f"run {run_id} results changed after the credential scan",
            retryable=False,
        )
    for path, body in pending_cache:
        _cache(path, body)
    for url, body, subject in pending:
        with _timing.count("upload.post"):
            _post(url, token, body, subject)
    with _timing.count("upload.post"):
        published = _post(
            base + "/publish",
            token,
            _json_bytes({"transport_version": 1}),
            f"run {run_id} publication",
        )
    run_url = published.get("run_url") if published else None
    run_label = published.get("run_label") if published else None
    return PublishResult(
        run_label
        if isinstance(run_label, str) and 0 < len(run_label) <= _MAX_RUN_LABEL
        else None,
        run_url
        if isinstance(run_url, str) and urlsplit(run_url).scheme == "https"
        else None,
    )


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
