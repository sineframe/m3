#!/usr/bin/env python3
"""Check the mechanically maintained synchronous/asynchronous API manifest.

The manifest is intentionally explicit.  A new public method or result model
must either be added to both twins (with a mapping) or be listed as an
explicitly pending async capability.  This makes API drift fail in CI instead
of silently creating an async-only contract.
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


def _public_members(owner: type[Any]) -> set[str]:
    """Return public methods/properties, excluding lifecycle dunder names."""

    names: set[str] = set()
    for name in dir(owner):
        if name.startswith("_"):
            continue
        value = inspect.getattr_static(owner, name)
        if callable(value) or isinstance(value, property):
            names.add(name)
    return names


def _module_exports(module_name: str) -> set[str]:
    module = _module(module_name)
    exports = getattr(module, "__all__", None)
    if not isinstance(exports, (tuple, list)) or not all(
        isinstance(item, str) for item in exports
    ):
        raise AssertionError(f"{module_name} must expose an explicit string __all__")
    return set(exports)


def _add_expected(
    expected: dict[str, set[str]],
    module_name: str,
    name: str,
    errors: list[str],
    label: str,
) -> None:
    if name in expected.setdefault(module_name, set()):
        errors.append(f"duplicate {label}: {module_name}.{name}")
    expected[module_name].add(name)


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("version") != 1:
        errors.append("unsupported parity manifest version")

    module_exports = manifest.get("module_exports")
    if not isinstance(module_exports, dict):
        return ["module_exports must be an object"]
    expected_exports: dict[str, set[str]] = {}

    for entry in module_exports.get("paired", ()):
        if not isinstance(entry, list) or len(entry) != 4:
            errors.append(f"invalid paired export entry: {entry!r}")
            continue
        sync_module, sync_name, async_module, async_name = entry
        if not all(isinstance(item, str) for item in entry):
            errors.append(f"invalid paired export entry: {entry!r}")
            continue
        _add_expected(expected_exports, sync_module, sync_name, errors, "paired export")
        _add_expected(
            expected_exports, async_module, async_name, errors, "paired export"
        )
        if sync_name not in _module_exports(sync_module):
            errors.append(f"missing sync export: {sync_module}.{sync_name}")
        if async_name not in _module_exports(async_module):
            errors.append(f"missing async export: {async_module}.{async_name}")

    for entry in module_exports.get("aliases", ()):
        if (
            not isinstance(entry, list)
            or len(entry) != 4
            or not all(isinstance(item, str) for item in entry)
        ):
            errors.append(f"invalid export alias entry: {entry!r}")
            continue
        sync_module, sync_name, async_module, async_name = entry
        expected_exports.setdefault(sync_module, set()).add(sync_name)
        expected_exports.setdefault(async_module, set()).add(async_name)
        if sync_name not in _module_exports(sync_module):
            errors.append(f"missing sync alias target: {sync_module}.{sync_name}")
        if async_name not in _module_exports(async_module):
            errors.append(f"missing async alias: {async_module}.{async_name}")

    owner_pairs = module_exports.get("owner_pairs", ())
    if not isinstance(owner_pairs, list):
        errors.append("module_exports.owner_pairs must be a list")
        owner_pairs = []
    for entry in owner_pairs:
        if (
            not isinstance(entry, list)
            or len(entry) != 4
            or not all(isinstance(item, str) for item in entry)
        ):
            errors.append(f"invalid owner pair: {entry!r}")
            continue
        sync_module, sync_name, async_module, async_name = entry
        _add_expected(expected_exports, sync_module, sync_name, errors, "owner export")
        _add_expected(
            expected_exports, async_module, async_name, errors, "owner export"
        )
        if sync_name not in _module_exports(sync_module):
            errors.append(f"missing sync owner export: {sync_module}.{sync_name}")
        if async_name not in _module_exports(async_module):
            errors.append(f"missing async owner export: {async_module}.{async_name}")

    async_module = "mcp_pal.async_api"
    sync_module = "mcp_pal.sync_api"
    for name in module_exports.get("async_pending", ()):
        if not isinstance(name, str):
            errors.append(f"invalid pending async export: {name!r}")
            continue
        _add_expected(
            expected_exports, async_module, name, errors, "pending async export"
        )
        if name not in _module_exports(async_module):
            errors.append(f"missing pending async export: {async_module}.{name}")
        if name in _module_exports(sync_module):
            errors.append(
                f"pending async export unexpectedly exists on sync side: {sync_module}.{name}"
            )

    for name in module_exports.get("sync_only", ()):
        if not isinstance(name, str):
            errors.append(f"invalid sync-only export: {name!r}")
            continue
        _add_expected(expected_exports, sync_module, name, errors, "sync-only export")
        if name not in _module_exports(sync_module):
            errors.append(f"missing sync-only export: {sync_module}.{name}")
        if name in _module_exports(async_module):
            errors.append(
                f"sync-only export unexpectedly exists on async side: {async_module}.{name}"
            )

    for module_name in (sync_module, async_module):
        actual = _module_exports(module_name)
        expected = expected_exports.get(module_name, set())
        for name in sorted(actual - expected):
            errors.append(
                f"unclassified public export (parity drift): {module_name}.{name}"
            )
        for name in sorted(expected - actual):
            errors.append(f"manifest export is absent: {module_name}.{name}")

    owner_members = manifest.get("owner_members")
    if not isinstance(owner_members, dict):
        return [*errors, "owner_members must be an object"]
    for key, details in owner_members.items():
        if not isinstance(key, str) or ":" not in key or not isinstance(details, dict):
            errors.append(f"invalid owner member entry: {key!r}")
            continue
        sync_spec, async_spec = key.split(":", 1)
        if "." not in sync_spec or "." not in async_spec:
            errors.append(f"invalid owner member key: {key!r}")
            continue
        sync_mod, sync_owner = sync_spec.rsplit(".", 1)
        async_mod, async_owner = async_spec.rsplit(".", 1)
        sync_class = _owner(sync_mod, sync_owner)
        async_class = _owner(async_mod, async_owner)
        sync_expected: set[str] = set()
        async_expected: set[str] = set()
        for pair in details.get("paired", ()):
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not all(isinstance(item, str) for item in pair)
            ):
                errors.append(f"invalid member pair for {key}: {pair!r}")
                continue
            async_name, sync_name = pair
            async_expected.add(async_name)
            sync_expected.add(sync_name)
            if not hasattr(async_class, async_name):
                errors.append(f"missing async member: {async_spec}.{async_name}")
            if not hasattr(sync_class, sync_name):
                errors.append(f"missing sync member: {sync_spec}.{sync_name}")
        pending = details.get("async_pending", ())
        if not isinstance(pending, list):
            errors.append(f"async_pending must be a list for {key}")
            pending = []
        for name in pending:
            if not isinstance(name, str):
                errors.append(f"invalid pending member for {key}: {name!r}")
                continue
            async_expected.add(name)
            if not hasattr(async_class, name):
                errors.append(f"missing pending async member: {async_spec}.{name}")
            if hasattr(sync_class, name):
                errors.append(
                    f"pending async member unexpectedly exists on sync side: {sync_spec}.{name}"
                )

        actual_sync = _public_members(sync_class)
        actual_async = _public_members(async_class)
        visible_sync_expected = {
            name for name in sync_expected if not name.startswith("_")
        }
        visible_async_expected = {
            name for name in async_expected if not name.startswith("_")
        }
        if actual_sync != visible_sync_expected:
            errors.append(
                f"sync member drift for {sync_spec}: expected {sorted(visible_sync_expected)!r}, "
                f"found {sorted(actual_sync)!r}"
            )
        if actual_async != visible_async_expected:
            errors.append(
                f"async member drift for {async_spec}: expected {sorted(visible_async_expected)!r}, "
                f"found {sorted(actual_async)!r}"
            )

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
