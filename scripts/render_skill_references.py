#!/usr/bin/env python3
"""Generate the testing-with-m3 skill references from the documentation site."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
MANIFEST = SITE / "navigation.json"
SKILL_DIR = ROOT / "skills" / "testing-with-m3"
SKILL_FILE = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"
STARTER_TEST = (
    ROOT / "sdk" / "examples" / "docs" / "first-test" / "tests" / "test_m3_starter.py"
)
CLI_REFERENCE = SITE / "reference" / "cli" / "index.md"
HOSTED_DOCS = "https://m3.sineframe.com/docs"
SKILL_NAME = "testing-with-m3"
EXCLUDED_IDS = {"home", "examples", "guides-agents-skill"}
EXCLUDED_SOURCE_PREFIX = "reference/python/api"  # generated API pages (~18k lines)
EXCLUDED_KINDS = {"migration", "redirect", "maintainer"}
KIND_ORDER = (
    "getting-started",
    "start",
    "concepts",
    "guides",
    "reference",
    "troubleshooting",
    "cli",
)
GENERATED_NOTE = "<!-- Generated from docs/site/{source} by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->"
MARKDOWN_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")

_VALIDATOR_PATH = ROOT / "scripts" / "validate_docs_site.py"
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "m3_docs_site_validator", _VALIDATOR_PATH
)
if _VALIDATOR_SPEC is None or _VALIDATOR_SPEC.loader is None:
    raise RuntimeError("could not load the documentation site validator")
_VALIDATOR = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(_VALIDATOR)
expected_route = _VALIDATOR.expected_route


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _non_fence_blocks(
    text: str,
) -> list[tuple[str, bool, list[re.Match[str]]]]:
    """Split text into maximal non-fence blocks and pass-through fence lines."""
    blocks: list[tuple[str, bool, list[re.Match[str]]]] = []
    in_fence = False
    pending: list[str] = []

    def flush() -> None:
        if pending:
            block = "".join(pending)
            blocks.append((block, False, list(INLINE_CODE.finditer(block))))
            pending.clear()

    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            flush()
            blocks.append((line, True, []))
            in_fence = not in_fence
        elif in_fence:
            blocks.append((line, True, []))
        else:
            pending.append(line)
    flush()
    return blocks


def _links_outside_code(
    text: str,
) -> list[tuple[re.Match[str], list[re.Match[str]]]]:
    """Find markdown links outside fences and inline-code spans."""
    matches = []
    for block, in_fence, spans in _non_fence_blocks(text):
        if in_fence:
            continue
        matches.extend(
            (match, spans)
            for match in MARKDOWN_LINK.finditer(block)
            if not any(
                span.start() <= match.start() and match.end() <= span.end()
                for span in spans
            )
        )
    return matches


def rewrite_links(text: str, source: str, selected: dict[str, str]) -> str:
    """Rewrite links in one documentation page for the bundled references."""
    source_dir = (SITE / source).parent

    def replace(match: re.Match[str], code_spans: list[re.Match[str]]) -> str:
        if any(
            span.start() <= match.start() and match.end() <= span.end()
            for span in code_spans
        ):
            return match.group(0)
        image, label, target = match.groups()
        if image:
            return label
        split = urlsplit(target)
        if target.startswith("#") or split.scheme:
            return match.group(0)
        path_text, separator, fragment = target.partition("#")
        resolved = Path(os.path.normpath(source_dir / path_text))
        if _inside(resolved, SITE):
            relative = resolved.relative_to(SITE).as_posix()
            page_id = selected.get(relative)
            suffix = f"#{fragment}" if separator else ""
            if page_id is not None:
                return f"[{label}]({page_id}.md{suffix})"
            if resolved.suffix == ".md":
                return f"[{label}]({HOSTED_DOCS}{expected_route(relative)}{suffix})"
            return label
        if _inside(resolved, ROOT):
            relative = resolved.relative_to(ROOT).as_posix()
            return f"{label} (M3 repository: `{relative}`)"
        return label

    rendered: list[str] = []
    for block, in_fence, spans in _non_fence_blocks(text):
        if in_fence:
            rendered.append(block)
        else:
            rendered.append(
                MARKDOWN_LINK.sub(
                    lambda match, spans=spans: replace(match, spans), block
                )
            )
    return "".join(rendered)


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    body = text[end + 5 :]
    if body.startswith("\n"):
        body = body[1:]
    return body


def _selected_pages() -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [
        page
        for page in manifest["pages"]
        if page["id"] not in EXCLUDED_IDS
        and not page["source"].startswith(EXCLUDED_SOURCE_PREFIX)
        and page["kind"] not in EXCLUDED_KINDS
    ]


def _render_references(pages: list[dict[str, str]]) -> dict[Path, str]:
    selected = {page["source"]: page["id"] for page in pages}
    rendered: dict[Path, str] = {}
    for page in pages:
        body = _strip_frontmatter((SITE / page["source"]).read_text(encoding="utf-8"))
        body = rewrite_links(body, page["source"], selected)
        rendered[REFERENCES / f"{page['id']}.md"] = (
            GENERATED_NOTE.format(source=page["source"]) + "\n\n" + body
        )

    headings = {
        "getting-started": "Getting started",
        "start": "Start",
        "concepts": "Concepts",
        "guides": "Guides",
        "reference": "Reference",
        "troubleshooting": "Troubleshooting",
        "cli": "CLI",
    }
    lines = [
        "# M3 documentation index",
        "",
        GENERATED_NOTE.format(source="navigation.json"),
    ]
    for kind in KIND_ORDER:
        kind_pages = sorted(
            (page for page in pages if page["kind"] == kind),
            key=lambda page: page["id"],
        )
        if not kind_pages:
            continue
        lines.extend(("", f"## {headings[kind]}", ""))
        lines.extend(
            f"- [{page['title']}]({page['id']}.md): {page['description'].replace('**', '')}"
            for page in kind_pages
        )
    rendered[REFERENCES / "index.md"] = "\n".join(lines) + "\n"
    return rendered


def _sync_starter(skill_text: str) -> tuple[str, list[str]]:
    pattern = re.compile(r"(^```python\s*\n)(.*?)(^```\s*$)", re.MULTILINE | re.DOTALL)
    matches = list(pattern.finditer(skill_text))
    if len(matches) != 1:
        return skill_text, ["SKILL.md must contain exactly one ```python fence"]
    starter = STARTER_TEST.read_text(encoding="utf-8").rstrip("\n")
    match = matches[0]
    synced = (
        skill_text[: match.start()]
        + match.group(1)
        + starter
        + "\n"
        + match.group(3)
        + skill_text[match.end() :]
    )
    return synced, []


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _lint(
    skill_text: str, expected_paths: set[Path], fence_errors: list[str]
) -> list[str]:
    errors = list(fence_errors)
    metadata = _frontmatter(skill_text)
    if metadata.get("name") != SKILL_NAME:
        errors.append(f"SKILL.md frontmatter name must equal {SKILL_NAME!r}")
    description = metadata.get("description", "")
    if not 1 <= len(description) <= 1024:
        errors.append("SKILL.md frontmatter description must be 1-1024 characters")
    if len(skill_text.splitlines()) > 500:
        errors.append("SKILL.md must contain at most 500 lines")

    available = {path.resolve() for path in expected_paths}
    for match in MARKDOWN_LINK.finditer(skill_text):
        target = match.group(3)
        if "/blob/main/" in target or "/tree/main/" in target:
            errors.append(f"SKILL.md link must not target main: {target}")
        split = urlsplit(target)
        if target.startswith("#") or split.scheme:
            continue
        path_text = target.partition("#")[0]
        resolved = Path(os.path.normpath(SKILL_DIR / path_text)).resolve()
        if resolved not in available:
            errors.append(f"SKILL.md relative link does not resolve: {target}")

    cli_text = CLI_REFERENCE.read_text(encoding="utf-8")
    for line in skill_text.splitlines():
        if "m3 " not in line:
            continue
        for flag in FLAG.findall(line):
            if flag not in cli_text:
                errors.append(
                    f"SKILL.md CLI flag is absent from the CLI reference: {flag}"
                )
    return errors


def _reference_link_errors(expected: dict[Path, str]) -> list[str]:
    """Find relative links in generated references that do not resolve."""
    available = {path.resolve() for path in expected if path.parent == REFERENCES}
    errors: list[str] = []
    for path, content in expected.items():
        if path.parent != REFERENCES:
            continue
        for match, _ in _links_outside_code(content):
            target = match.group(3)
            if target.startswith("#") or urlsplit(target).scheme:
                continue
            path_text = target.partition("#")[0]
            resolved = Path(os.path.normpath(path.parent / path_text)).resolve()
            if resolved not in available:
                errors.append(
                    f"{path.relative_to(SKILL_DIR).as_posix()} link does not resolve: {target}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    pages = _selected_pages()
    expected = _render_references(pages)
    skill_text, fence_errors = _sync_starter(SKILL_FILE.read_text(encoding="utf-8"))
    expected[SKILL_FILE] = skill_text
    expected_paths = set(expected)
    expected_reference_paths = {path for path in expected if path.parent == REFERENCES}
    existing_reference_paths = (
        set(REFERENCES.glob("*.md")) if REFERENCES.exists() else set()
    )

    stale: list[Path] = []
    if args.check:
        for path, content in expected.items():
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(path)
        stale.extend(existing_reference_paths - expected_reference_paths)
    else:
        REFERENCES.mkdir(parents=True, exist_ok=True)
        for path, content in expected.items():
            path.write_text(content, encoding="utf-8")
        for path in existing_reference_paths - expected_reference_paths:
            path.unlink()

    errors = _lint(skill_text, expected_paths, fence_errors)
    errors.extend(_reference_link_errors(expected))
    for path in sorted(set(stale)):
        print(f"agent skill is stale: {path.relative_to(ROOT)}")
    for error in errors:
        print(f"agent skill error: {error}")
    if stale or errors:
        return 1
    print("Agent skill references are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
