"""CLI access to the managed runtime cache service."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from m3.runtime import list_cache as _sdk_list_cache
from m3.runtime import prune_cache as _sdk_prune_cache
from m3.runtime import resolve_cache_root as _sdk_resolve_cache_root


def resolve_cache_root(
    override: str | os.PathLike[str] | None = None, *, project_root: Path | None = None
) -> Path:
    return (
        _sdk_resolve_cache_root(
            project_root or Path.cwd(),
            cli_override=Path(override).expanduser() if override is not None else None,
        )
        .expanduser()
        .absolute()
    )


def list_cache(root: Path) -> list[dict[str, Any]]:
    entries = list(_sdk_list_cache(root))

    def project(item: Mapping[str, Any]) -> dict[str, str]:
        provenance = item.get("provenance")
        values = provenance if isinstance(provenance, dict) else item
        path = Path(str(item.get("path", "")))
        return {
            "kind": str(values.get("kind", "")),
            "version": str(values.get("version", "")),
            "target": str(values.get("target", "")),
            "digest": str(values.get("sha256", "")),
            "status": "in_use"
            if any(path.glob("lease-*"))
            else str(item.get("status", "ready")),
        }

    projected = [project(item) for item in entries]
    return sorted(
        projected,
        key=lambda item: tuple(
            item[field] for field in ("kind", "version", "target", "digest")
        ),
    )


def _entry_paths(root: Path) -> list[Path]:
    return [
        Path(str(item["path"]))
        for item in _sdk_list_cache(root)
        if isinstance(item.get("path"), str)
    ]


def _entry_size(path: Path) -> int:
    """Return bytes occupied by regular files in a cache entry.

    Cache entries are trusted only after the SDK has validated them.  Still,
    avoid following links here so reporting a prune cannot traverse outside
    the selected cache if an entry is concurrently modified.
    """
    total = 0
    try:
        for current, directories, files in os.walk(path, followlinks=False):
            directories[:] = [
                name for name in directories if not (Path(current) / name).is_symlink()
            ]
            for name in files:
                candidate = Path(current) / name
                try:
                    if not candidate.is_symlink():
                        total += candidate.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def prune_cache(root: Path) -> list[Path]:
    deadline = time.monotonic() + 10.0
    removed: list[Path] = []
    while True:
        removed.extend(_sdk_prune_cache(root))
        if not _entry_paths(root):
            return removed
        if time.monotonic() >= deadline:
            raise RuntimeError("runtime cache entries are still in use")
        time.sleep(0.1)


def cache_command(
    action: str,
    override: str | os.PathLike[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    try:
        root = resolve_cache_root(override, project_root=project_root)
        if action == "list":
            print(json.dumps(list_cache(root), sort_keys=True))
        else:
            sizes = {path: _entry_size(path) for path in _entry_paths(root)}
            removed = prune_cache(root)
            bytes_removed = sum(sizes.get(path, 0) for path in removed)
            print(
                f"pruned runtime cache: {root} "
                f"({len(removed)} entries, {bytes_removed} bytes removed)"
            )
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"m3 runtime: {exc}", file=sys.stderr)
        return 2
