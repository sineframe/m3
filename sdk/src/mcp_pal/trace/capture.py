"""Shared JSONL capture helpers for MCP transport observers."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact


class CaptureWriter:
    def __init__(self, path: str, baseline_ns: int, *, secrets: set[str] | None = None):
        self.path = Path(path)
        self.baseline_ns = baseline_ns
        self.lock = threading.Lock()
        self._secrets: set[str] | None = None if secrets is None else set(secrets)

    def add_secrets(self, secrets: set[str]) -> None:
        """Register process-local values for redaction of later wire events."""

        with self.lock:
            if self._secrets is None:
                self._secrets = set()
            self._secrets.update(value for value in secrets if value)

    def write(
        self,
        *,
        transport: str,
        direction: str,
        payload: Any,
        kind: str = "jsonrpc",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # A harness may resolve an explicit SecretReference immediately before
        # launching a child.  Keep that value only in this process-local set;
        # every persisted wire record still receives the normal structural and
        # exact-value redaction.
        with self.lock:
            secrets = None if self._secrets is None else set(self._secrets)
        if secrets is None:
            safe_payload, payload_paths = redact(payload, path="$.payload")
            safe_metadata, metadata_paths = redact(metadata or {}, path="$.metadata")
        else:
            safe_payload, payload_paths = redact(payload, secrets=secrets, path="$.payload")
            safe_metadata, metadata_paths = redact(metadata or {}, secrets=secrets, path="$.metadata")
        now_ns = time.perf_counter_ns()
        record = {
            "source": "mcp_transport",
            "transport": transport,
            "direction": direction,
            "kind": kind,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "offset_ms": max(0.0, (now_ns - self.baseline_ns) / 1_000_000),
            "payload": safe_payload,
            "metadata": safe_metadata,
            "redacted_paths": payload_paths + metadata_paths,
        }
        with self.lock:
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_json_payload(data: bytes | str) -> Any:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return text


def read_capture(path: str) -> list[dict[str, Any]]:
    capture = Path(path)
    if not capture.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in capture.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return sorted(records, key=lambda item: float(item.get("offset_ms", 0)))
