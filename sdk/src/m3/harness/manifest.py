"""Validation and safe handling for ``m3.harness.v1`` manifests.

This module is deliberately dependency-light so the bridge kit can be used
from a terminal without constructing the web application. Values in ``env``
are references, never resolved credentials.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator


class HarnessManifest(BaseModel):
    """Validated descriptor for an external ACP harness executable."""

    schema_version: Literal["m3.harness.v1"] = "m3.harness.v1"
    protocol: Literal["acp"] = "acp"
    protocol_version: Literal[1] = 1
    command: str = Field(min_length=1, max_length=1000)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

    @field_validator("env")
    @classmethod
    def references_only(cls, value: dict[str, str]) -> dict[str, str]:
        for name, ref in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid environment variable name: {name}")
            if not re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", ref):
                raise ValueError(f"environment value for {name} must be a reference")
        return value


class ManifestValidationError(ValueError):
    """A manifest is malformed or contains a literal secret."""


def _model(manifest: Any) -> HarnessManifest:
    try:
        return HarnessManifest.model_validate(manifest)
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ManifestValidationError(messages) from exc


def _local_readiness(value: dict[str, Any]) -> dict[str, Any]:
    command = value["command"]
    executable = command if os.path.isabs(command) else shutil.which(command)
    env_refs = sorted({ref[2:-1] for ref in value["env"].values()})
    missing = [name for name in env_refs if name not in os.environ]
    return {
        "executable": executable,
        "local_ready": bool(
            executable and os.access(executable, os.X_OK) and not missing
        ),
        "missing_environment": missing,
    }


def validate_manifest(manifest: Any, *, check_local: bool = False) -> dict[str, Any]:
    """Validate a manifest, optionally checking local readiness.

    Ordinary shape validation is deterministic and does not inspect the host
    executable search path or environment.  ``check_local`` explicitly checks
    the executable and referenced host variables; it never launches a process.
    A manifest may be valid but unavailable, which is useful when importing
    configurations on another machine.
    """

    if not isinstance(manifest, dict):
        raise ManifestValidationError("manifest must be an object")
    unknown = sorted(
        set(manifest)
        - {"schema_version", "protocol", "protocol_version", "command", "args", "env"}
    )
    if unknown:
        raise ManifestValidationError("unknown manifest fields: " + ", ".join(unknown))
    model = _model(manifest)
    value: dict[str, Any] = model.model_dump(mode="json")
    for name, reference in value["env"].items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ManifestValidationError(f"env: invalid child variable name: {name}")
        if not re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", reference):
            raise ManifestValidationError(
                f"env.{name}: value must be an environment reference"
            )
    result: dict[str, Any] = {
        "valid": True,
        "manifest": value,
        "executable": None,
        "local_ready": None,
        "missing_environment": [],
    }
    if check_local:
        result.update(_local_readiness(value))
        command = value["command"]
        executable = result["executable"]
        missing = result["missing_environment"]
        if not executable:
            result["warning"] = f"Executable is unavailable: {command}"
        elif missing:
            result["warning"] = "Missing environment variables: " + ", ".join(missing)
    return result


def load_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load one JSON manifest from a path or ``-`` (stdin)."""

    import sys

    source = (
        sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8")
    )
    try:
        raw = json.loads(source)
    except (json.JSONDecodeError, OSError) as exc:
        raise ManifestValidationError(f"invalid JSON: {exc}") from exc
    return cast(dict[str, Any], validate_manifest(raw)["manifest"])


def export_manifest(manifest: Any) -> str:
    """Return stable, secret-safe JSON suitable for sharing."""

    return (
        json.dumps(validate_manifest(manifest)["manifest"], indent=2, sort_keys=True)
        + "\n"
    )


__all__ = [
    "ManifestValidationError",
    "export_manifest",
    "load_manifest",
    "validate_manifest",
]
