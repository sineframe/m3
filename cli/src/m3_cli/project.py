"""Project root and identity lookups that need only the standard library."""

from __future__ import annotations

import sys
import typing
from pathlib import Path

if typing.TYPE_CHECKING:
    import tomli as _tomllib
elif sys.version_info >= (3, 11):
    import tomllib as _tomllib
else:  # pragma: no cover
    import tomli as _tomllib


def resolve_project_root(explicit: Path | None = None) -> Path:
    """Return the explicit root, else the nearest ``m3.toml`` directory above cwd.

    The walk stops after checking a directory that contains ``.git``; without an
    ``m3.toml`` the current directory is the root.
    """
    if explicit is not None:
        return explicit.expanduser().resolve()
    start = Path.cwd().resolve()
    for directory in (start, *start.parents):
        if (directory / "m3.toml").is_file():
            return directory
        if (directory / ".git").exists():
            break
    return start


def project_name(root: Path) -> str | None:
    try:
        value = _tomllib.loads((root / "m3.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    name = value.get("project_name")
    return name.strip() if isinstance(name, str) and name.strip() else None
