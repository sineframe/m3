"""Resolve CI secrets without changing the parent process environment."""

from __future__ import annotations

import os
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import dotenv_values

from .errors import CLIError

ACCESS_TOKEN_ENV = "M3_ACCESS_TOKEN"
CONTROL_PLANE_URL_ENV = "M3_CONTROL_PLANE_URL"
_PAT = re.compile(r"m3pat_[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}")


def resolved_environment(
    env_file: str | os.PathLike[str] | None,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Ambient values win; requested dotenv values fill only absent names."""
    result = dict(os.environ if source is None else source)
    if env_file is None:
        return result
    path = Path(env_file).expanduser()
    if not path.is_file():
        raise CLIError("requested environment file was not found")
    try:
        values = dotenv_values(str(path), interpolate=False)
    except Exception as exc:
        raise CLIError("requested environment file could not be read") from exc
    for key, value in values.items():
        # Blank dotenv placeholders are absent; explicit ambient empties remain
        # in ``result`` and are rejected by access_token.
        if (
            key
            and value is not None
            and not (key == ACCESS_TOKEN_ENV and not value)
            and key not in result
        ):
            result[key] = value
    return result


def validate_credential_mappings(mappings: Sequence[str]) -> None:
    """The upload credential may never become a harness or judge credential."""
    for mapping in mappings:
        if "=" not in mapping:
            continue  # Existing option validation reports the malformed mapping.
        target, source = mapping.split("=", 1)
        if target.split(":", 1)[-1] == ACCESS_TOKEN_ENV or source == ACCESS_TOKEN_ENV:
            raise CLIError("M3_ACCESS_TOKEN cannot be mapped to a test credential")


def test_environment(environment: Mapping[str, str]) -> dict[str, str]:
    child = dict(environment)
    child.pop(ACCESS_TOKEN_ENV, None)
    return child


def access_token(environment: Mapping[str, str], *, base_url: str) -> str:
    if ACCESS_TOKEN_ENV in environment:
        value = environment[ACCESS_TOKEN_ENV]
        if not value:
            raise CLIError("M3_ACCESS_TOKEN is empty")
        return validate_access_token(value)
    if any(environment.get(name) for name in ("CI", "GITHUB_ACTIONS", "GITLAB_CI")):
        raise CLIError("M3_ACCESS_TOKEN is required in CI")
    from .auth import load_saved_token

    saved = load_saved_token(base_url)
    if not saved:
        raise CLIError(
            "M3 access is required; run m3 auth login or set M3_ACCESS_TOKEN"
        )
    return validate_access_token(saved)


def validate_access_token(value: str) -> str:
    if not _PAT.fullmatch(value):
        raise CLIError("M3_ACCESS_TOKEN must be an M3 personal access token")
    identifier, secret = value.removeprefix("m3pat_").split(".", 1)
    for segment, size in ((identifier, 16), (secret, 32)):
        try:
            decoded = urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        except ValueError as exc:
            raise CLIError(
                "M3_ACCESS_TOKEN must be an M3 personal access token"
            ) from exc
        if (
            len(decoded) != size
            or urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != segment
        ):
            raise CLIError("M3_ACCESS_TOKEN must be an M3 personal access token")
    return value


__all__ = [
    "ACCESS_TOKEN_ENV",
    "CONTROL_PLANE_URL_ENV",
    "access_token",
    "resolved_environment",
    "test_environment",
    "validate_credential_mappings",
]
