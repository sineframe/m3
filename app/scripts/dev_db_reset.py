#!/usr/bin/env python3
"""Safely remove the disposable development SQLite database.

This is intentionally a recipe helper, not an installed ``mcp-pal`` command.
It accepts only the exact confirmation used by the documented ``just`` recipe
and only a regular database file below the repository working directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _configured_path(root: Path) -> Path:
    raw = os.environ.get("DATABASE_PATH", "./mcp_pal.db").strip()
    if raw.startswith("sqlite:///"):
        raw = raw.removeprefix("sqlite:///")
    if not raw or raw in {".", "..", "/"}:
        raise ValueError("database path must name an app-owned development file")
    path = Path(raw).expanduser()
    # Resolve ``..`` lexically, but never follow symlinks.  Following a
    # symlink here would make a path that looks app-owned delete its target.
    candidate = Path(
        os.path.abspath(os.fspath(path if path.is_absolute() else root / path))
    )
    # Development reset must never be pointed at a user home, root, or an
    # arbitrary outside path through an environment variable.
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "database path is outside the repository development location"
        ) from exc
    # Reject symlinks in the configured path, including a dangling final
    # symlink.  This is both clearer for callers and closes the reset-time
    # symlink-to-another-app attack.
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ValueError("database path must not contain symlinks")
    if candidate == root or candidate.name in {"", ".", ".."}:
        raise ValueError("database path must name a file")
    if candidate.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("database path must use a SQLite database suffix")
    if candidate.exists() and not candidate.is_file():
        raise ValueError("database path must not be a directory")
    return candidate


def main(argv: list[str] | None = None) -> int:
    # ``CONFIRM`` is read from the environment so the just recipe never
    # interpolates an untrusted value into shell source.
    args = list(sys.argv[1:] if argv is None else argv)
    # The environment form is the documented recipe interface.  Extra
    # positional arguments must not be silently ignored when confirmation is
    # read from the environment.
    confirmation = (
        (os.environ.get("CONFIRM") if not args else None)
        if argv is None
        else (args[0] if len(args) == 1 else None)
    )
    if confirmation != "reset":
        print("refusing database reset: use CONFIRM=reset", file=sys.stderr)
        return 2
    root = Path.cwd().resolve()
    try:
        database = _configured_path(root)
    except ValueError as exc:
        print(f"refusing database reset: {exc}", file=sys.stderr)
        return 2
    removed: list[str] = []
    for candidate in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        # Sidecars are exact siblings, never a glob or recursive target.
        if os.path.lexists(candidate):
            if candidate.is_symlink() or not candidate.is_file():
                print(
                    "refusing database reset: database or sidecar is not a regular file",
                    file=sys.stderr,
                )
                return 2
            try:
                candidate.unlink()
            except OSError:
                # Keep the helper's failure surface value-free and avoid a
                # traceback containing a user-controlled path.
                print(
                    "refusing database reset: database file could not be removed",
                    file=sys.stderr,
                )
                return 2
            removed.append(str(candidate))
    print(f"removed {len(removed)} development database file(s): {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
