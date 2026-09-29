#!/usr/bin/env python3
"""Synchronize mapped Python file blocks into their owning guides."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
EXAMPLES = ROOT / "sdk" / "examples" / "docs"
NAVIGATION = SITE / "navigation.json"
PYTHON_FENCE = re.compile(r"(```(?:python|py)\s*\n)(.*?)(\n```)", re.DOTALL)


def load_mappings() -> dict[str, list[tuple[Path, dict[str, object]]]]:
    navigation = json.loads(NAVIGATION.read_text(encoding="utf-8"))
    sources = {page["id"]: SITE / page["source"] for page in navigation["pages"]}
    mappings: dict[str, list[tuple[Path, dict[str, object]]]] = {}
    for manifest_path in sorted(EXAMPLES.glob("*/example.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for block in manifest["displayed_blocks"]:
            page_id = block["page"]
            if page_id not in sources:
                raise ValueError(f"{manifest_path}: unknown page {page_id!r}")
            mappings.setdefault(page_id, []).append((manifest_path.parent, block))
    return {str(sources[page_id]): blocks for page_id, blocks in mappings.items()}


def render_page(path: Path, mappings: list[tuple[Path, dict[str, object]]]) -> str:
    original = path.read_text(encoding="utf-8")
    index = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal index
        if index >= len(mappings):
            raise ValueError(f"{path}: more Python blocks than manifest mappings")
        example_root, block = mappings[index]
        index += 1
        if block["kind"] == "snippet":
            return match.group(0)
        source = example_root / str(block["source"])
        body = source.read_text(encoding="utf-8").rstrip()
        return f"{match.group(1)}{body}{match.group(3)}"

    rendered = PYTHON_FENCE.sub(replace, original)
    if index != len(mappings):
        raise ValueError(
            f"{path}: {len(mappings)} manifest mappings but {index} Python blocks"
        )
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale: list[Path] = []
    for path_text, mappings in load_mappings().items():
        path = Path(path_text)
        rendered = render_page(path, mappings)
        if rendered == path.read_text(encoding="utf-8"):
            continue
        if args.check:
            stale.append(path)
        else:
            path.write_text(rendered, encoding="utf-8")
    if stale:
        for path in stale:
            print(f"displayed example is stale: {path.relative_to(ROOT)}")
        return 1
    print("Documentation example blocks are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
