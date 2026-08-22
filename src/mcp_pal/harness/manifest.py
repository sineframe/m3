"""Validation and safe handling for ``mcp-pal.harness.v1`` manifests.

The API uses the pydantic model in :mod:`mcp_pal.api.schemas`; this module is
deliberately dependency-light so the bridge kit can be used from a terminal
without constructing the web application.  Values in ``env`` are references,
never resolved credentials.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import ValidationError


class ManifestValidationError(ValueError):
    """A manifest is malformed or contains a literal secret."""


def _model(manifest: Any):
    # Import lazily: importing the CLI must not import FastAPI or create an
    # application object.
    from ..api.schemas import HarnessManifest

    try:
        return HarnessManifest.model_validate(manifest)
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ManifestValidationError(messages) from exc


def validate_manifest(manifest: Any, *, check_local: bool = False) -> dict[str, Any]:
    """Validate and return non-secret readiness metadata.

    ``check_local`` only checks the executable and referenced host variables;
    it never launches a process.  A manifest may be valid but unavailable,
    which is useful when importing configurations on another machine.
    """

    if not isinstance(manifest, dict):
        raise ManifestValidationError("manifest must be an object")
    unknown = sorted(set(manifest) - {"schema_version", "protocol", "protocol_version", "command", "args", "env"})
    if unknown:
        raise ManifestValidationError("unknown manifest fields: " + ", ".join(unknown))
    model = _model(manifest)
    value = model.model_dump(mode="json")
    for name, reference in value["env"].items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ManifestValidationError(f"env: invalid child variable name: {name}")
        if not re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", reference):
            raise ManifestValidationError(f"env.{name}: value must be an environment reference")
    command = value["command"]
    executable = command if os.path.isabs(command) else shutil.which(command)
    env_refs = sorted({ref[2:-1] for ref in value["env"].values()})
    missing = [name for name in env_refs if name not in os.environ]
    result: dict[str, Any] = {
        "valid": True,
        "manifest": value,
        "executable": executable,
        "local_ready": bool(executable and os.access(executable, os.X_OK) and not missing),
        "missing_environment": missing,
    }
    if check_local and not executable:
        result["warning"] = f"Executable is unavailable: {command}"
    elif check_local and missing:
        result["warning"] = "Missing environment variables: " + ", ".join(missing)
    return result


def load_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load one JSON manifest from a path or ``-`` (stdin)."""

    import sys

    source = sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(source)
    except (json.JSONDecodeError, OSError) as exc:
        raise ManifestValidationError(f"invalid JSON: {exc}") from exc
    return validate_manifest(raw)["manifest"]


def export_manifest(manifest: Any) -> str:
    """Return canonical, secret-safe JSON suitable for sharing."""

    return json.dumps(validate_manifest(manifest)["manifest"], indent=2, sort_keys=True) + "\n"


__all__ = ["ManifestValidationError", "export_manifest", "load_manifest", "validate_manifest"]
