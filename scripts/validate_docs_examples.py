#!/usr/bin/env python3
"""Validate guide-owned example manifests and displayed Python source."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
EXAMPLES = ROOT / "sdk" / "examples" / "docs"
NAVIGATION = SITE / "navigation.json"
PYTHON_FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)\n```", re.DOTALL)
VERIFICATION_MODES = {"deterministic", "live-provider", "manual-ui"}


def load_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: {exc}")
        return None


def nonempty_strings(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def validate_manifest(
    path: Path, pages: dict[str, dict[str, object]], errors: list[str]
) -> None:
    raw = load_json(path, errors)
    if not isinstance(raw, dict):
        return
    label = str(path.relative_to(ROOT))
    example_root = path.parent
    if raw.get("schema_version") != 1:
        errors.append(f"{label}: schema_version must be 1")
    if raw.get("id") != example_root.name:
        errors.append(f"{label}: id must match directory name {example_root.name!r}")
    for key in (
        "target_release",
        "python",
        "working_directory",
        "network",
        "credentials",
        "cleanup",
    ):
        if not isinstance(raw.get(key), str) or not str(raw[key]).strip():
            errors.append(f"{label}: {key} must be a non-empty string")
    for key in ("owning_pages", "dependencies", "files"):
        if not nonempty_strings(raw.get(key)):
            errors.append(f"{label}: {key} must be a non-empty string array")

    owning_pages = raw.get("owning_pages", [])
    if isinstance(owning_pages, list):
        for page_id in owning_pages:
            if page_id not in pages:
                errors.append(f"{label}: unknown owning page {page_id!r}")

    files = raw.get("files", [])
    if isinstance(files, list):
        for relative in files:
            if not isinstance(relative, str):
                continue
            source = example_root / relative
            if not source.is_file():
                errors.append(f"{label}: missing example file {relative!r}")
                continue
            if source.suffix == ".py":
                try:
                    compile(source.read_text(encoding="utf-8"), str(source), "exec")
                except SyntaxError as exc:
                    errors.append(
                        f"{label}: {relative}: {exc.msg} at line {exc.lineno}"
                    )

    blocks = raw.get("displayed_blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append(f"{label}: displayed_blocks must be a non-empty array")
        blocks = []
    blocks_by_page: dict[str, list[dict[str, object]]] = {}
    for index, block in enumerate(blocks):
        block_label = f"{label}: displayed_blocks[{index}]"
        if not isinstance(block, dict):
            errors.append(f"{block_label} must be an object")
            continue
        page_id = block.get("page")
        source_name = block.get("source")
        kind = block.get("kind")
        if page_id not in owning_pages:
            errors.append(f"{block_label}: page must be listed in owning_pages")
        if source_name not in files:
            errors.append(f"{block_label}: source must be listed in files")
        if kind not in {"file", "snippet"}:
            errors.append(f"{block_label}: kind must be 'file' or 'snippet'")
        if not isinstance(block.get("label"), str) or not str(block["label"]).strip():
            errors.append(f"{block_label}: label must be a non-empty string")
        if kind == "snippet" and (
            not isinstance(block.get("context"), str)
            or not str(block["context"]).strip()
        ):
            errors.append(
                f"{block_label}: snippets need insertion or replacement context"
            )
        if isinstance(page_id, str):
            blocks_by_page.setdefault(page_id, []).append(block)

    for page_id in owning_pages if isinstance(owning_pages, list) else []:
        page = pages.get(page_id)
        if page is None:
            continue
        page_path = SITE / str(page["source"])
        markdown = page_path.read_text(encoding="utf-8")
        fences = [match.strip() for match in PYTHON_FENCE.findall(markdown)]
        mappings = blocks_by_page.get(page_id, [])
        matched: set[int] = set()
        for block in mappings:
            if block.get("kind") != "file":
                continue
            source_path = example_root / str(block.get("source"))
            if not source_path.is_file():
                continue
            source_text = source_path.read_text(encoding="utf-8").strip()
            match_index = next(
                (
                    index
                    for index, fence in enumerate(fences)
                    if index not in matched and fence == source_text
                ),
                None,
            )
            if match_index is None:
                errors.append(
                    f"{label}: {page_id} does not contain exact displayed source "
                    f"{block.get('source')!r}"
                )
            else:
                matched.add(match_index)
        unmatched = [
            fence for index, fence in enumerate(fences) if index not in matched
        ]
        snippet_count = sum(block.get("kind") == "snippet" for block in mappings)
        if len(unmatched) < snippet_count:
            errors.append(
                f"{label}: {page_id} has only {len(unmatched)} possible snippet block(s), "
                f"but {snippet_count} snippet mapping(s)"
            )

    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        errors.append(f"{label}: scenarios must be a non-empty array")
        return
    scenario_ids: set[str] = set()
    for index, scenario in enumerate(scenarios):
        scenario_label = f"{label}: scenarios[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{scenario_label} must be an object")
            continue
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            errors.append(f"{scenario_label}: id must be a non-empty string")
        elif scenario_id in scenario_ids:
            errors.append(f"{scenario_label}: duplicate id {scenario_id!r}")
        else:
            scenario_ids.add(scenario_id)
        if (
            not isinstance(scenario.get("command"), str)
            or not scenario["command"].strip()
        ):
            errors.append(f"{scenario_label}: command must be a non-empty string")
        if scenario.get("expected_exit_code") not in {0, 1}:
            errors.append(f"{scenario_label}: expected_exit_code must be 0 or 1")
        if not nonempty_strings(scenario.get("assertions")):
            errors.append(
                f"{scenario_label}: assertions must be a non-empty string array"
            )
        verification = scenario.get("verification")
        if verification not in VERIFICATION_MODES:
            errors.append(
                f"{scenario_label}: verification must be one of "
                f"{sorted(VERIFICATION_MODES)}"
            )
        if verification == "live-provider":
            compatibility = scenario.get("compatibility")
            status = (
                compatibility.get("status") if isinstance(compatibility, dict) else None
            )
            if (
                not isinstance(status, str)
                or "requires live verification" not in status
            ):
                errors.append(
                    f"{scenario_label}: live scenarios must state that live verification is required"
                )


def validate_display_coverage(
    manifests: list[Path], pages: dict[str, dict[str, object]], errors: list[str]
) -> None:
    mappings: dict[str, list[tuple[Path, dict[str, object]]]] = {}
    owning_pages: set[str] = set()
    for manifest in manifests:
        raw = load_json(manifest, errors)
        if not isinstance(raw, dict):
            continue
        owning_pages.update(
            page_id
            for page_id in raw.get("owning_pages", [])
            if isinstance(page_id, str)
        )
        for block in raw.get("displayed_blocks", []):
            if isinstance(block, dict) and isinstance(block.get("page"), str):
                mappings.setdefault(str(block["page"]), []).append(
                    (manifest.parent, block)
                )

    for page_id in sorted(owning_pages):
        page = pages.get(page_id)
        if page is None:
            continue
        page_path = SITE / str(page["source"])
        fences = [
            match.strip()
            for match in PYTHON_FENCE.findall(page_path.read_text(encoding="utf-8"))
        ]
        matched: set[int] = set()
        snippets: list[dict[str, object]] = []
        for example_root, block in mappings.get(page_id, []):
            if block.get("kind") == "snippet":
                snippets.append(block)
                continue
            source = example_root / str(block.get("source"))
            if not source.is_file():
                continue
            source_text = source.read_text(encoding="utf-8").strip()
            index = next(
                (
                    candidate
                    for candidate, fence in enumerate(fences)
                    if candidate not in matched and fence == source_text
                ),
                None,
            )
            if index is not None:
                matched.add(index)
        remaining = [index for index in range(len(fences)) if index not in matched]
        if len(remaining) != len(snippets):
            errors.append(
                f"{page_path.relative_to(ROOT)}: {len(remaining)} Python block(s) are not "
                f"source-backed, but {len(snippets)} snippet mapping(s) exist"
            )
            continue
        for index in remaining:
            try:
                compile(fences[index], f"{page_path}:block-{index + 1}", "exec")
            except SyntaxError as exc:
                errors.append(
                    f"{page_path.relative_to(ROOT)}: snippet block {index + 1} is not "
                    f"valid Python: {exc.msg}"
                )


def main() -> int:
    errors: list[str] = []
    raw_navigation = load_json(NAVIGATION, errors)
    if not isinstance(raw_navigation, dict) or not isinstance(
        raw_navigation.get("pages"), list
    ):
        errors.append("docs/site/navigation.json: pages must be an array")
        pages: dict[str, dict[str, object]] = {}
    else:
        pages = {
            str(page["id"]): page
            for page in raw_navigation["pages"]
            if isinstance(page, dict) and isinstance(page.get("id"), str)
        }
    manifests = sorted(EXAMPLES.glob("*/example.json"))
    directories = sorted(path for path in EXAMPLES.iterdir() if path.is_dir())
    if len(manifests) != len(directories):
        errors.append("sdk/examples/docs: every example directory needs example.json")
    for manifest in manifests:
        validate_manifest(manifest, pages, errors)
    validate_display_coverage(manifests, pages, errors)
    if errors:
        print("Documentation example validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Validated {len(manifests)} documentation example manifests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
