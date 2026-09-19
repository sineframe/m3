#!/usr/bin/env python3
"""Check the synchronous and asynchronous public API pairs.

Same-name exports and methods are checked automatically.  The small JSON file
only records renamed names, identity aliases, one-sided names, and paired
owner classes.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "parity" / "sync_async_parity.json"


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("sync/async parity manifest must be a JSON object")
    return value


def _module(name: str) -> ModuleType:
    source = str(ROOT / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    return importlib.import_module(name)


def _owner(module_name: str, owner_name: str) -> type[Any]:
    module = _module(module_name)
    value = getattr(module, owner_name, None)
    if not inspect.isclass(value):
        raise AssertionError(f"{module_name}.{owner_name} is not a class")
    return value


_LIFECYCLE_MEMBERS = {"__enter__", "__exit__", "__aenter__", "__aexit__"}


def _public_members(owner: type[Any], *, include_lifecycle: bool = False) -> set[str]:
    """Return public methods and selected lifecycle methods."""

    names: set[str] = set()
    for name in dir(owner):
        if name.startswith("_") and not (
            include_lifecycle and name in _LIFECYCLE_MEMBERS
        ):
            continue
        value = inspect.getattr_static(owner, name)
        if callable(value) or isinstance(value, property):
            names.add(name)
    return names


def _module_export_names(module_name: str) -> tuple[str, ...]:
    module = _module(module_name)
    exports = getattr(module, "__all__", None)
    if not isinstance(exports, (tuple, list)) or not all(
        isinstance(item, str) for item in exports
    ):
        raise AssertionError(f"{module_name} must expose an explicit string __all__")
    return tuple(exports)


def _module_exports(module_name: str) -> set[str]:
    return set(_module_export_names(module_name))


def _pairs(value: Any, label: str, errors: list[str]) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in value:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not all(isinstance(item, str) for item in entry)
        ):
            errors.append(f"invalid {label} entry: {entry!r}")
            continue
        pair = (entry[0], entry[1])
        if pair in seen:
            errors.append(f"duplicate {label}: {pair!r}")
            continue
        seen.add(pair)
        pairs.append(pair)
    return pairs


def _names(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    names: list[str] = []
    seen: set[str] = set()
    for name in value:
        if not isinstance(name, str):
            errors.append(f"invalid {label} name: {name!r}")
            continue
        if name in seen:
            errors.append(f"duplicate {label} name: {name}")
            continue
        seen.add(name)
        names.append(name)
    return names


def _check_side_names(
    actual: set[str], expected: set[str], module_name: str, errors: list[str]
) -> None:
    for name in sorted(actual - expected):
        errors.append(
            f"unclassified public export (parity drift): {module_name}.{name}"
        )
    for name in sorted(expected - actual):
        errors.append(f"manifest export is absent: {module_name}.{name}")


def _validate_exports(
    manifest: dict[str, Any], errors: list[str]
) -> tuple[str, str, set[str], set[str], set[str], set[str]]:
    modules = manifest.get("modules")
    if not isinstance(modules, dict):
        errors.append("modules must be an object")
        return (
            "m3.sync_api",
            "m3.async_api",
            set(),
            set(),
            set(),
            set(),
        )
    sync_module = modules.get("sync")
    async_module = modules.get("async")
    if not isinstance(sync_module, str) or not isinstance(async_module, str):
        errors.append("modules.sync and modules.async must be strings")
        return (
            "m3.sync_api",
            "m3.async_api",
            set(),
            set(),
            set(),
            set(),
        )

    sync_actual = _module_exports(sync_module)
    async_actual = _module_exports(async_module)
    expected_sync = sync_actual & async_actual
    expected_async = set(expected_sync)

    exports = manifest.get("exports")
    if not isinstance(exports, dict):
        errors.append("exports must be an object")
        exports = {}

    aliases = _pairs(exports.get("aliases", []), "export alias", errors)
    renamed = _pairs(exports.get("renamed", []), "renamed export", errors)
    async_only = _names(exports.get("async_only", []), "async-only export", errors)
    sync_only = _names(exports.get("sync_only", []), "sync-only export", errors)

    classified_sync: set[str] = set()
    classified_async: set[str] = set()
    alias_sources: dict[str, str] = {}
    for sync_name, async_name in aliases:
        if sync_name == async_name:
            errors.append(f"alias repeats a same-name export: {sync_name}")
        previous = alias_sources.get(async_name)
        if previous is not None and previous != sync_name:
            errors.append(
                f"multiple sync aliases target {async_module}.{async_name}: "
                f"{previous}, {sync_name}"
            )
        alias_sources[async_name] = sync_name
        if sync_name in async_actual and async_name in sync_actual:
            errors.append(
                f"alias names are public on both sides: {sync_name}, {async_name}"
            )
        if sync_name not in sync_actual:
            errors.append(f"missing sync alias: {sync_module}.{sync_name}")
        if async_name not in async_actual:
            errors.append(f"missing async alias: {async_module}.{async_name}")
        if sync_name in sync_actual and async_name in async_actual:
            if getattr(_module(sync_module), sync_name) is not getattr(
                _module(async_module), async_name
            ):
                errors.append(
                    f"export alias is not the same object: {sync_module}.{sync_name} "
                    f"and {async_module}.{async_name}"
                )
        classified_sync.add(sync_name)
        classified_async.add(async_name)

    for sync_name, async_name in renamed:
        if sync_name in classified_sync or async_name in classified_async:
            errors.append(f"duplicate export classification: {sync_name}, {async_name}")
        classified_sync.add(sync_name)
        classified_async.add(async_name)
        if sync_name not in sync_actual:
            errors.append(f"missing sync renamed export: {sync_module}.{sync_name}")
        if async_name not in async_actual:
            errors.append(f"missing async renamed export: {async_module}.{async_name}")
        if sync_name in async_actual or async_name in sync_actual:
            errors.append(
                "renamed export is also present under the other name: "
                f"{sync_name}, {async_name}"
            )

    for name in async_only:
        if name in classified_async:
            errors.append(f"duplicate async export classification: {name}")
        classified_async.add(name)
        if name not in async_actual:
            errors.append(f"missing async-only export: {async_module}.{name}")
        if name in sync_actual:
            errors.append(
                f"async-only export exists on sync side: {sync_module}.{name}"
            )

    for name in sync_only:
        if name in classified_sync:
            errors.append(f"duplicate sync export classification: {name}")
        classified_sync.add(name)
        if name not in sync_actual:
            errors.append(f"missing sync-only export: {sync_module}.{name}")
        if name in async_actual:
            errors.append(
                f"sync-only export exists on async side: {async_module}.{name}"
            )

    expected_sync.update(classified_sync)
    expected_async.update(classified_async)
    return (
        sync_module,
        async_module,
        sync_actual,
        async_actual,
        expected_sync,
        expected_async,
    )


def _validate_owner(
    sync_module: str,
    async_module: str,
    sync_exports: set[str],
    async_exports: set[str],
    details: dict[str, Any],
    expected_sync_exports: set[str],
    expected_async_exports: set[str],
    errors: list[str],
) -> None:
    sync_name = details.get("sync")
    async_name = details.get("async")
    if not isinstance(sync_name, str) or not isinstance(async_name, str):
        errors.append(f"invalid owner pair: {details!r}")
        return
    expected_sync_exports.add(sync_name)
    expected_async_exports.add(async_name)
    if sync_name not in sync_exports:
        errors.append(f"missing sync owner export: {sync_module}.{sync_name}")
    if async_name not in async_exports:
        errors.append(f"missing async owner export: {async_module}.{async_name}")

    sync_class = _owner(sync_module, sync_name)
    async_class = _owner(async_module, async_name)
    include_lifecycle = sync_name == "MCPTestKit"
    sync_actual = _public_members(sync_class, include_lifecycle=include_lifecycle)
    async_actual = _public_members(async_class, include_lifecycle=include_lifecycle)
    expected_sync = sync_actual & async_actual
    expected_async = set(expected_sync)

    renamed = _pairs(
        details.get("renamed", []), f"renamed member for {sync_name}", errors
    )
    async_only = _names(
        details.get("async_only", []),
        f"async-only member for {async_name}",
        errors,
    )
    sync_only = _names(
        details.get("sync_only", []),
        f"sync-only member for {sync_name}",
        errors,
    )
    classified_sync: set[str] = set()
    classified_async: set[str] = set()
    for sync_member, async_member in renamed:
        if sync_member in classified_sync or async_member in classified_async:
            errors.append(
                f"duplicate member classification: {sync_member}, {async_member}"
            )
        classified_sync.add(sync_member)
        classified_async.add(async_member)
        if sync_member not in sync_actual:
            errors.append(
                f"missing sync member: {sync_module}.{sync_name}.{sync_member}"
            )
        if async_member not in async_actual:
            errors.append(
                f"missing async member: {async_module}.{async_name}.{async_member}"
            )
        if sync_member in async_actual or async_member in sync_actual:
            errors.append(
                "renamed member is also present under the other name: "
                f"{sync_member}, {async_member}"
            )
    for member in async_only:
        if member in classified_async:
            errors.append(f"duplicate async member classification: {member}")
        classified_async.add(member)
        if member not in async_actual:
            errors.append(
                f"missing async-only member: {async_module}.{async_name}.{member}"
            )
        if member in sync_actual:
            errors.append(
                "async-only member exists on sync side: "
                f"{sync_module}.{sync_name}.{member}"
            )
    for member in sync_only:
        if member in classified_sync:
            errors.append(f"duplicate sync member classification: {member}")
        classified_sync.add(member)
        if member not in sync_actual:
            errors.append(
                f"missing sync-only member: {sync_module}.{sync_name}.{member}"
            )
        if member in async_actual:
            errors.append(
                "sync-only member exists on async side: "
                f"{async_module}.{async_name}.{member}"
            )
    expected_sync.update(classified_sync)
    expected_async.update(classified_async)
    if sync_actual != expected_sync:
        errors.append(f"sync member drift for {sync_module}.{sync_name}")
    if async_actual != expected_async:
        errors.append(f"async member drift for {async_module}.{async_name}")


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("version") != 2:
        errors.append("unsupported parity manifest version")
    (
        sync_module,
        async_module,
        sync_actual,
        async_actual,
        expected_sync_exports,
        expected_async_exports,
    ) = _validate_exports(manifest, errors)

    owners = manifest.get("owners")
    if not isinstance(owners, list):
        errors.append("owners must be a list")
        owners = []
    seen_owners: set[tuple[str, str]] = set()
    for details in owners:
        if not isinstance(details, dict):
            errors.append(f"invalid owner pair: {details!r}")
            continue
        pair = (details.get("sync"), details.get("async"))
        if pair in seen_owners:
            errors.append(f"duplicate owner pair: {pair!r}")
            continue
        seen_owners.add(pair)
        _validate_owner(
            sync_module,
            async_module,
            sync_actual,
            async_actual,
            details,
            expected_sync_exports,
            expected_async_exports,
            errors,
        )
    _check_side_names(sync_actual, expected_sync_exports, sync_module, errors)
    _check_side_names(async_actual, expected_async_exports, async_module, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    try:
        errors = validate_manifest(load_manifest(args.manifest))
    except Exception as error:
        print(
            f"sync/async parity check failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    if errors:
        print("sync/async parity check failed:", file=sys.stderr)
        for message in errors:
            print(f"- {message}", file=sys.stderr)
        return 1
    print("sync/async parity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
