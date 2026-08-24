#!/usr/bin/env python3
"""Regenerate the canonical formatting of the sync/async parity manifest.

The JSON manifest is the reviewable source of truth.  This small generator
keeps formatting deterministic so additions are easy to audit; the companion
checker validates that every declared symbol still exists on the intended
side.  Use ``--check`` in CI and ``--write`` after an intentional manifest
change.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from check_sync_async_parity import MANIFEST, load_manifest


def canonical_manifest(value: dict[str, object]) -> str:
    return json.dumps(value, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--check", action="store_true", help="fail when the file is not canonical")
    parser.add_argument("--write", action="store_true", help="rewrite the file in canonical form")
    args = parser.parse_args()
    if args.check and args.write:
        parser.error("--check and --write are mutually exclusive")
    value = load_manifest(args.manifest)
    rendered = canonical_manifest(value)
    current = args.manifest.read_text(encoding="utf-8")
    if args.write:
        args.manifest.write_text(rendered, encoding="utf-8")
        return 0
    if args.check and current != rendered:
        print(f"{args.manifest} is not canonical; run generate_sync_async_parity.py --write")
        return 1
    if not args.check:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
