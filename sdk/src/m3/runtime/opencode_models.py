"""One immutable OpenCode model catalog per run, outside session caches."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .core import RuntimeManager, RuntimeValidationError, _entry_lock

MODELS_URL = "https://models.opencode.ai/api.json"
MAX_MODELS_BYTES = 8 * 1024 * 1024


def models_directory(database: Path, run_id: str) -> Path:
    """Keep run evidence beside its database, including arbitrary run IDs."""
    key = hashlib.sha256(run_id.encode()).hexdigest()
    return database.resolve().with_name(database.name + ".runs") / key


def prepare_models(directory: Path, source: str | None = None) -> tuple[Path, str]:
    """Fetch or copy once; serialize competing processes and never refresh a run."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = directory.resolve() / "opencode-models.json"
    with _entry_lock(snapshot, timeout=45):
        if snapshot.is_symlink():
            raise RuntimeValidationError("OpenCode model catalog must not be a symlink")
        if snapshot.exists():
            raw = _read_models(snapshot)
        elif source:
            raw = _read_models(Path(source).expanduser())
        else:
            try:
                raw = RuntimeManager._download(MODELS_URL, "manifest")
            except RuntimeValidationError:
                raise RuntimeValidationError(
                    "OpenCode model catalog download failed"
                ) from None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeValidationError(
                "OpenCode model catalog is invalid JSON"
            ) from None
        if not isinstance(value, dict) or not value:
            raise RuntimeValidationError(
                "OpenCode model catalog must be a nonempty object"
            )
        if not snapshot.exists():
            descriptor, name = tempfile.mkstemp(
                prefix=".opencode-models-", dir=directory
            )
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, snapshot)
            finally:
                temporary.unlink(missing_ok=True)
        return snapshot, hashlib.sha256(raw).hexdigest()


def _read_models(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MODELS_BYTES + 1)
    except OSError:
        raise RuntimeValidationError(
            "OpenCode model catalog could not be read"
        ) from None
    if len(raw) > MAX_MODELS_BYTES:
        raise RuntimeValidationError("OpenCode model catalog exceeds size cap")
    return raw
