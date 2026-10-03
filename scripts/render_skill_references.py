#!/usr/bin/env python3
"""Generate the testing-with-m3 skill references from the documentation site."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
MANIFEST = SITE / "navigation.json"
SKILL_DIR = ROOT / "skills" / "testing-with-m3"
SKILL_FILE = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"
EXAMPLES = SKILL_DIR / "examples"
EXAMPLE_SOURCES = ROOT / "sdk" / "examples" / "docs"
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
MARKDOWN_LINK = re.compile(
    r'(!?)\[([^\]]*)\]\(\s*(<[^>\n]*>|(?:\\.|[^)\s])+)(?:[ \t]+("[^"\n]*"|\'[^\'\n]*\'|\([^)\n]*\)))?[ \t]*\)'
)
REFERENCE_DEFINITION = re.compile(
    r'^[ \t]{0,3}(\[(?!\^)[^\]\n]+\]:[ \t]*)(<[^>\n]*>|(?:\\.|[^\s])+)([ \t]+(?:"[^"\n]*"|\'[^\'\n]*\'|\([^)\n]*\)))?[ \t]*$',
    re.MULTILINE,
)
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
NON_M3_FLAGS = frozenset({"--arg"})


def cli_flags(text: str) -> frozenset[str]:
    """Return the exact CLI flag tokens in text."""
    return frozenset(FLAG.findall(text))


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


def _plausible_definition_target(target: str) -> bool:
    raw_target, angle = _bare_target(target)
    return (
        angle
        or any(character in raw_target for character in "/.#")
        or bool(urlsplit(raw_target).scheme)
    )


def _reference_definitions(
    block: str, spans: list[re.Match[str]]
) -> list[re.Match[str]]:
    """Find block-start reference definitions with meaningful link targets."""
    matches: list[re.Match[str]] = []
    previous_was_definition = False
    offset = 0
    for line in block.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        candidate = REFERENCE_DEFINITION.fullmatch(content)
        at_block_start = offset == 0
        after_blank = offset > 0 and block[:offset].endswith(("\n\n", "\r\n\r\n"))
        match = (
            candidate
            if candidate is not None
            and (at_block_start or after_blank or previous_was_definition)
            and _plausible_definition_target(candidate.group(2))
            else None
        )
        if match is not None:
            line_match = REFERENCE_DEFINITION.match(block, offset)
            if line_match is not None and not any(
                span.start() <= line_match.start() and line_match.end() <= span.end()
                for span in spans
            ):
                matches.append(line_match)
        previous_was_definition = match is not None
        offset += len(line)
    return matches


def _links_outside_code(
    text: str,
) -> list[tuple[re.Match[str], list[re.Match[str]], bool]]:
    """Find inline links and block-start reference definitions outside code."""
    matches: list[tuple[re.Match[str], list[re.Match[str]], bool]] = []
    for block, in_fence, spans in _non_fence_blocks(text):
        if in_fence:
            continue
        matches.extend(
            (match, spans, False)
            for match in MARKDOWN_LINK.finditer(block)
            if not any(
                span.start() <= match.start() and match.end() <= span.end()
                for span in spans
            )
        )
        matches.extend(
            (match, spans, True) for match in _reference_definitions(block, spans)
        )
    return matches


def _bare_target(target: str) -> tuple[str, bool]:
    angle = target.startswith("<") and target.endswith(">")
    return (target[1:-1] if angle else target), angle


def _rewrite_target(
    target: str,
    source: str,
    selected: dict[str, str],
    examples: set[str] | None = None,
) -> tuple[str, str | None]:
    """Return target, or a repository path for the caller to explain.

    Links into an example project under ``sdk/examples/docs`` point at its
    bundled copy; ``examples`` collects the project ids to copy.
    """
    raw_target, angle = _bare_target(target)
    split = urlsplit(raw_target)
    if raw_target.startswith("#") or split.scheme:
        return target, None
    resolved = Path(os.path.normpath((SITE / source).parent / split.path))
    suffix = f"#{split.fragment}" if split.fragment else ""
    if _inside(resolved, SITE):
        relative = resolved.relative_to(SITE).as_posix()
        page_id = selected.get(relative)
        if page_id is not None:
            # Bundled copies are release-pinned; query parameters do not apply.
            rewritten = f"{page_id}.md{suffix}"
        elif resolved.suffix == ".md":
            query = f"?{split.query}" if split.query else ""
            rewritten = f"{HOSTED_DOCS}{expected_route(relative)}{query}{suffix}"
        else:
            return "", None
    elif _inside(resolved, EXAMPLE_SOURCES) and resolved != EXAMPLE_SOURCES:
        relative = resolved.relative_to(EXAMPLE_SOURCES)
        if examples is not None:
            examples.add(relative.parts[0])
        rewritten = f"../examples/{relative.as_posix()}{suffix}"
    elif _inside(resolved, ROOT):
        return "", resolved.relative_to(ROOT).as_posix()
    else:
        return "", None
    return (f"<{rewritten}>" if angle else rewritten), None


def rewrite_links(
    text: str,
    source: str,
    selected: dict[str, str],
    examples: set[str] | None = None,
) -> str:
    """Rewrite links in one documentation page for the bundled references."""
    rendered: list[str] = []
    for block, in_fence, _ in _non_fence_blocks(text):
        if in_fence:
            rendered.append(block)
            continue
        matches = sorted(
            (
                (match, definition)
                for match, _, definition in _links_outside_code(block)
            ),
            key=lambda item: item[0].start(),
        )
        pieces: list[str] = []
        cursor = 0
        for match, definition in matches:
            pieces.append(block[cursor : match.start()])
            group = 2 if definition else 3
            target = match.group(group)
            rewritten, repository_path = _rewrite_target(
                target, source, selected, examples
            )
            if definition and (repository_path is not None or not rewritten):
                replacement = match.group(0)
            elif repository_path is not None:
                replacement = f"{match.group(2)} (M3 repository: `{repository_path}`)"
            elif definition:
                replacement = match.group(1) + rewritten + (match.group(3) or "")
            elif not rewritten:
                replacement = match.group(2)
            elif match.group(1):
                replacement = match.group(2)
            else:
                title = match.group(4)
                title_suffix = f" {title}" if title else ""
                replacement = f"[{match.group(2)}]({rewritten}{title_suffix})"
            pieces.append(replacement)
            cursor = match.end()
        pieces.append(block[cursor:])
        rendered.append("".join(pieces))
    return "".join(rendered)


def reference_definition_errors(
    text: str, source: str, selected: dict[str, str], filename: str
) -> list[str]:
    """Report definitions whose targets cannot be represented in the bundle."""
    errors: list[str] = []
    for match, _, definition in _links_outside_code(text):
        if not definition:
            continue
        target = match.group(2)
        rewritten, repository_path = _rewrite_target(target, source, selected)
        if repository_path is not None or not rewritten:
            errors.append(
                f"{filename} reference definition cannot be bundled: {target}; "
                "use an inline link"
            )
    return errors


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
    examples: set[str] = set()
    rendered: dict[Path, str] = {}
    for page in pages:
        body = _strip_frontmatter((SITE / page["source"]).read_text(encoding="utf-8"))
        body = rewrite_links(body, page["source"], selected, examples)
        rendered[REFERENCES / f"{page['id']}.md"] = (
            GENERATED_NOTE.format(source=page["source"]) + "\n\n" + body
        )
    # Copy each linked example project's runnable files verbatim, so the
    # installed skill carries the sources of its own release.
    for example_id in sorted(examples):
        project = EXAMPLE_SOURCES / example_id
        manifest = json.loads((project / "example.json").read_text(encoding="utf-8"))
        for name in manifest["files"]:
            rendered[EXAMPLES / example_id / name] = (project / name).read_text(
                encoding="utf-8"
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


def _frontmatter_shape_errors(text: str) -> list[str]:
    """Reject frontmatter this line parser would silently misread.

    Only single-line ``key: value`` pairs are supported. A block scalar such as
    ``description: >`` or an indented continuation line would otherwise be read
    as a one-character value that passes the length check.
    """
    if not text.startswith("---\n"):
        return ["SKILL.md must start with YAML frontmatter"]
    end = text.find("\n---\n", 4)
    if end < 0:
        return ["SKILL.md frontmatter is not closed with ---"]
    errors: list[str] = []
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if line[0].isspace() or not separator:
            errors.append(
                "SKILL.md frontmatter must use single-line 'key: value' pairs; "
                f"unsupported line: {line.strip()!r}"
            )
        elif value.strip()[:1] in {">", "|"}:
            errors.append(
                f"SKILL.md frontmatter {key.strip()!r} must be a single-line "
                "value, not a block scalar"
            )
    return errors


def _dead_anchor(split: SplitResult, current: Path, contents: dict[Path, str]) -> bool:
    """Return whether a link fragment names no heading in a bundled markdown file."""
    if not split.fragment:
        return False
    target = (
        Path(os.path.normpath(current.parent / split.path)).resolve()
        if split.path
        else current
    )
    text = contents.get(target)
    if target.suffix != ".md" or text is None:
        return False
    return unquote(split.fragment).lower() not in _VALIDATOR.heading_anchors(text)


def _moving_branch_errors(content: str, filename: str) -> list[str]:
    errors = []
    for match in re.finditer(r"https?://[^\s]+", content):
        url = match.group(0).rstrip(".,;:!?)]}>\"'")
        if "/blob/main/" in url or "/tree/main/" in url:
            errors.append(f"{filename} links to a moving branch: {url}")
    return errors


def _lint(
    skill_text: str, expected: dict[Path, str], fence_errors: list[str]
) -> list[str]:
    errors = list(fence_errors)
    errors.extend(_frontmatter_shape_errors(skill_text))
    metadata = _frontmatter(skill_text)
    if metadata.get("name") != SKILL_NAME:
        errors.append(f"SKILL.md frontmatter name must equal {SKILL_NAME!r}")
    description = metadata.get("description", "")
    if not 1 <= len(description) <= 1024:
        errors.append("SKILL.md frontmatter description must be 1-1024 characters")
    if len(skill_text.splitlines()) > 500:
        errors.append("SKILL.md must contain at most 500 lines")

    contents = {path.resolve(): content for path, content in expected.items()}
    skill_path = SKILL_FILE.resolve()
    for match, _, definition in _links_outside_code(skill_text):
        if definition:
            # Reported by _skill_reference_definition_errors.
            continue
        target = match.group(3)
        raw_target, _ = _bare_target(target)
        split = urlsplit(raw_target)
        if split.scheme:
            continue
        if raw_target.startswith("#"):
            resolved = skill_path
        else:
            resolved = Path(os.path.normpath(SKILL_DIR / split.path)).resolve()
        if resolved not in contents:
            errors.append(f"SKILL.md relative link does not resolve: {target}")
        elif _dead_anchor(split, skill_path, contents):
            errors.append(f"SKILL.md link anchor does not resolve: {target}")

    cli_flags_available = cli_flags(CLI_REFERENCE.read_text(encoding="utf-8"))
    for flag in cli_flags(skill_text) - NON_M3_FLAGS:
        if flag not in cli_flags_available:
            errors.append(f"SKILL.md CLI flag is absent from the CLI reference: {flag}")
    errors.extend(_moving_branch_errors(skill_text, "SKILL.md"))
    errors.extend(_skill_reference_definition_errors(skill_text, set(expected)))
    return errors


def _skill_reference_definition_errors(
    skill_text: str, expected_paths: set[Path]
) -> list[str]:
    available = {path.resolve() for path in expected_paths}
    errors: list[str] = []
    for match, _, definition in _links_outside_code(skill_text):
        if not definition:
            continue
        target = match.group(2)
        raw_target, _ = _bare_target(target)
        split = urlsplit(raw_target)
        if raw_target.startswith("#") or split.scheme:
            continue
        resolved = Path(os.path.normpath(SKILL_DIR / split.path)).resolve()
        if resolved not in available:
            errors.append(
                f"SKILL.md reference definition cannot be bundled: {target}; "
                "use an inline link"
            )
    return errors


def _reference_link_errors(
    expected: dict[Path, str], pages: list[dict[str, str]]
) -> list[str]:
    """Find broken links and definitions that cannot be bundled."""
    available = {path.resolve() for path in expected if path.parent == REFERENCES}
    for path in expected:
        if _inside(path, EXAMPLES):
            # Links may target a bundled file or its project directory.
            parent = path
            while parent != EXAMPLES:
                available.add(parent.resolve())
                parent = parent.parent
    contents = {path.resolve(): content for path, content in expected.items()}
    selected = {page["source"]: page["id"] for page in pages}
    sources = {page["id"]: page["source"] for page in pages}
    errors: list[str] = []
    for path, content in expected.items():
        if path.parent != REFERENCES:
            continue
        filename = path.relative_to(SKILL_DIR).as_posix()
        for match, _, definition in _links_outside_code(content):
            if definition:
                # Definitions are checked against their source page below.
                continue
            target = match.group(3)
            raw_target, _ = _bare_target(target)
            split = urlsplit(raw_target)
            if split.scheme:
                continue
            if not raw_target.startswith("#"):
                resolved = Path(os.path.normpath(path.parent / split.path)).resolve()
                if resolved not in available:
                    errors.append(f"{filename} link does not resolve: {target}")
                    continue
            if _dead_anchor(split, path.resolve(), contents):
                errors.append(f"{filename} link anchor does not resolve: {target}")
        source = sources.get(path.stem)
        if source is not None:
            original = _strip_frontmatter((SITE / source).read_text(encoding="utf-8"))
            errors.extend(
                reference_definition_errors(original, source, selected, filename)
            )
        errors.extend(_moving_branch_errors(content, filename))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    pages = _selected_pages()
    expected = _render_references(pages)
    skill_text, fence_errors = _sync_starter(SKILL_FILE.read_text(encoding="utf-8"))
    expected[SKILL_FILE] = skill_text
    expected_reference_paths = {
        path
        for path in expected
        if path.parent == REFERENCES or _inside(path, EXAMPLES)
    }
    existing_reference_paths = (
        set(REFERENCES.glob("*.md")) if REFERENCES.exists() else set()
    ) | (
        {path for path in EXAMPLES.rglob("*") if path.is_file()}
        if EXAMPLES.exists()
        else set()
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
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        for path in existing_reference_paths - expected_reference_paths:
            path.unlink()
        if EXAMPLES.exists():
            for directory in sorted(EXAMPLES.rglob("*"), reverse=True):
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()

    errors = _lint(skill_text, expected, fence_errors)
    errors.extend(_reference_link_errors(expected, pages))
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
