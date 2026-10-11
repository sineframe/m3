"""One immutable OpenCode model catalog per run, outside session caches."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, FiniteFloat, TypeAdapter, ValidationError

from .core import RuntimeManager, RuntimeValidationError, _entry_lock

MODELS_URL = "https://models.opencode.ai/api.json"
MAX_MODELS_BYTES = 8 * 1024 * 1024


class _ModelLimits(BaseModel):
    model_config = ConfigDict(strict=True)

    context: FiniteFloat
    output: FiniteFloat
    input: FiniteFloat | None = None


class _CatalogModel(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    name: str
    release_date: str
    attachment: bool
    reasoning: bool
    temperature: bool | None = None
    tool_call: bool
    limit: _ModelLimits


class _CatalogProvider(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    name: str
    env: list[str]
    models: dict[str, _CatalogModel]


_CATALOG = TypeAdapter(dict[str, _CatalogProvider])


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
            try:
                _validate_models(raw)
            except RuntimeValidationError:
                if source is None:
                    raise
            else:
                return snapshot, hashlib.sha256(raw).hexdigest()
        if source:
            raw = _read_models(Path(source).expanduser())
        else:
            try:
                raw = RuntimeManager._download(MODELS_URL, "manifest")
            except RuntimeValidationError:
                raise RuntimeValidationError(
                    "OpenCode model catalog download failed"
                ) from None
        _validate_models(raw)
        descriptor, name = tempfile.mkstemp(prefix=".opencode-models-", dir=directory)
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


def _validate_models(raw: bytes) -> None:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeValidationError("OpenCode model catalog is invalid JSON") from None
    if not isinstance(value, dict) or not value:
        raise RuntimeValidationError("OpenCode model catalog must be a nonempty object")
    try:
        _CATALOG.validate_python(value)
    except ValidationError:
        raise RuntimeValidationError(
            "OpenCode model catalog has invalid provider or model structure"
        ) from None


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
