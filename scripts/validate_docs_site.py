#!/usr/bin/env python3
"""Validate M3's dependency-free documentation publishing contract."""

from __future__ import annotations

import ast
import json
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
MANIFEST = SITE / "navigation.json"
EXAMPLES = ROOT / "sdk" / "examples" / "docs"
PLUGIN_SOURCE = ROOT / "sdk" / "src" / "m3" / "pytest_plugin.py"
LIMITATIONS_SOURCE = ROOT / "sdk" / "src" / "m3" / "harness" / "observations.py"
OUTPUT_PAGE = SITE / "reference" / "output.md"
REQUIRED = {
    "index.md",
    "getting-started.md",
    "start/install.md",
    "start/your-server.md",
    "reference/index.md",
    "reference/cli/index.md",
    "reference/python/index.md",
    "reference/python/api.md",
    "reference/pytest.md",
    "reference/configuration.md",
    "reference/compatibility.md",
}
LINK = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
VITEPRESS_SPECIAL = re.compile(
    r"[\s~`!@#$%^&*()_+\-=\[\]{}|\\;:\"'\u201c\u201d\u2018\u2019<>,.?/]+"
)
RESERVED_OUTPUTS = {"assets", "404", "sitemap", "llms", "llms-full"}
EXCLUDED_NAVIGATION_KINDS = {"migration", "redirect", "maintainer"}
OBSOLETE_DOC_LINK = re.compile(
    r"(?:m3\.sineframe\.com/docs|github\.com/sineframe/m3/"
    r"(?:blob|tree)/(?:main|master)/(?:README\.md|sdk/README\.md|"
    r"cli/README\.md|sdk/docs/))",
    re.IGNORECASE,
)
ESCAPED_ANGLE_ENTITY = re.compile(
    r"&(?:amp;)*(?:lt|gt);|&#0*(?:60|62);|&#x0*(?:3c|3e);",
    re.IGNORECASE,
)
UNFORMATTED_SIGNATURE = re.compile(
    r"^\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(.*"
    r"&(?:amp;)*(?:lt|gt);",
    re.IGNORECASE,
)


def expected_route(source: str) -> str:
    path = PurePosixPath(source)
    if path.name == "index.md":
        parent = "/" + ("" if str(path.parent) == "." else str(path.parent) + "/")
        return parent
    return "/" + str(path.with_suffix(""))


def heading_anchors(text: str) -> set[str]:
    """Return VitePress heading slugs and explicit HTML IDs."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    anchors.update(
        match.group(1) for match in re.finditer(r"\bid\s*=\s*['\"]([^'\"]+)['\"]", text)
    )
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"!?(?:\[([^\]]*)\])\([^)]*\)", r"\1", match.group(1))
        title = re.sub(r"<[^>]+>|`", "", title)
        title = unicodedata.normalize("NFKD", title)
        title = re.sub(r"[\u0300-\u036f]", "", title)
        title = re.sub(r"[\x00-\x1f]", "", title)
        slug = VITEPRESS_SPECIAL.sub("-", title)
        slug = re.sub(r"-+", "-", slug)
        slug = re.sub(r"^-+|-+$", "", slug)
        slug = re.sub(r"^(\d)", r"_\1", slug).lower()
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def markdown_code_regions(text: str) -> list[str]:
    """Return fenced and inline code contents without treating prose as code."""
    lines = text.splitlines(keepends=True)
    inline_source: list[str] = []
    regions: list[str] = []
    fence_char: str | None = None
    fence_size = 0
    fence_content: list[str] = []

    for line in lines:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence_char is None:
            if marker:
                fence_char = marker.group(1)[0]
                fence_size = len(marker.group(1))
                fence_content = []
            else:
                inline_source.append(line)
            continue

        if (
            marker
            and marker.group(1)[0] == fence_char
            and len(marker.group(1)) >= fence_size
            and re.fullmatch(
                rf" {{0,3}}{re.escape(marker.group(1))}[ \t]*(?:\r?\n)?",
                line,
            )
        ):
            regions.append("".join(fence_content))
            fence_char = None
            fence_size = 0
            fence_content = []
        else:
            fence_content.append(line)

    if fence_char is not None:
        regions.append("".join(fence_content))

    # Inline code spans use matching runs of backticks. Entities inside them
    # remain literal in Markdown renderers, which is the source of this bug.
    inline = "".join(inline_source)
    cursor = 0
    while cursor < len(inline):
        if inline[cursor] != "`":
            cursor += 1
            continue
        end = cursor
        while end < len(inline) and inline[end] == "`":
            end += 1
        marker = inline[cursor:end]
        search = end
        while True:
            close = inline.find(marker, search)
            if close < 0:
                cursor = end
                break
            if (close == 0 or inline[close - 1] != "`") and (
                close + len(marker) == len(inline) or inline[close + len(marker)] != "`"
            ):
                regions.append(inline[end:close])
                cursor = close + len(marker)
                break
            search = close + 1

    return regions


def unformatted_signature_entity_count(text: str) -> int:
    """Catch generated signatures that contain entities but lack code markup."""
    count = 0
    fence_char: str | None = None
    fence_size = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence_char is None:
            if marker:
                fence_char = marker.group(1)[0]
                fence_size = len(marker.group(1))
                continue
            if "`" not in line and UNFORMATTED_SIGNATURE.search(line):
                count += len(ESCAPED_ANGLE_ENTITY.findall(line))
            continue
        if (
            marker
            and marker.group(1)[0] == fence_char
            and len(marker.group(1)) >= fence_size
            and re.fullmatch(
                rf" {{0,3}}{re.escape(marker.group(1))}[ \t]*(?:\r?\n)?",
                line,
            )
        ):
            fence_char = None
            fence_size = 0
    return count


def escaped_angle_count(text: str) -> int:
    """Count escaped angle entities in code and unformatted signatures."""
    return sum(
        len(ESCAPED_ANGLE_ENTITY.findall(region))
        for region in markdown_code_regions(text)
    ) + unformatted_signature_entity_count(text)


def output_terms(
    plugin_source: str, observations_source: str
) -> tuple[list[str], list[str]]:
    """Return CLI output prefixes and trace limitation codes from the SDK."""

    def leading_text(node: ast.expr) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    break
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
            return "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return leading_text(node.left)
        return ""

    plugin_tree = ast.parse(plugin_source)
    lines: set[str] = set()
    for node in ast.walk(plugin_tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_line"
            and node.args
        ):
            continue
        value = leading_text(node.args[0])
        if not value.startswith("M3"):
            continue
        value = value.split("{", 1)[0]
        separator = value.find(": ")
        if separator > len("M3"):
            value = value[:separator]
        lines.add(value.rstrip(" :"))

    observations_tree = ast.parse(observations_source)
    limitations: set[str] = set()
    for node in observations_tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "_ALLOWED_LIMITATIONS"
            for target in node.targets
        ):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and value.args
            and isinstance(value.args[0], ast.Set)
        ):
            continue
        limitations.update(
            element.value
            for element in value.args[0].elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
        break
    return sorted(lines), sorted(limitations)


def main() -> int:
    errors: list[str] = []
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"docs manifest: {exc}", file=sys.stderr)
        return 1
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list):
        print("docs manifest must contain a pages array", file=sys.stderr)
        return 1
    schema_version = data.get("schemaVersion")
    if schema_version != 2:
        print(
            f"unsupported docs schemaVersion: {schema_version!r}; expected 2",
            file=sys.stderr,
        )
        return 1
    routes: dict[str, str] = {}
    folded_routes: dict[str, str] = {}
    normalized_routes: dict[str, str] = {}
    sources: set[str] = set()
    normalized_sources: dict[str, str] = {}
    page_ids: set[str] = set()
    for number, page in enumerate(pages, 1):
        where = f"pages[{number}]"
        if not isinstance(page, dict) or not all(
            isinstance(page.get(k), str) and page[k].strip()
            for k in ("title", "description", "source")
        ):
            errors.append(f"{where} requires non-empty title, description, and source")
            continue
        source = page["source"]
        if "route" in page:
            errors.append(f"{where} must not store a route; routes derive from source")
        identifier = page.get("id")
        kind = page.get("kind")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{where} requires a non-empty id")
        elif identifier in page_ids:
            errors.append(f"duplicate page id: {identifier}")
        else:
            page_ids.add(identifier)
        if not isinstance(kind, str) or not kind.strip():
            errors.append(f"{where} requires a non-empty kind")
        aliases = page.get("searchAliases", [])
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in aliases
        ):
            errors.append(f"{where} searchAliases must be non-empty strings")
        if (
            "\\" in source
            or PurePosixPath(source).is_absolute()
            or ".." in PurePosixPath(source).parts
            or not source.endswith(".md")
            or "?" in source
            or "#" in source
        ):
            errors.append(
                f"{where} source must be a relative .md path without '..' or backslashes: {source}"
            )
            continue
        source_entry = SITE / source
        source_path = source_entry.resolve()
        if ROOT.resolve() not in source_path.parents:
            errors.append(f"{where} source escapes the M3 repository: {source}")
            continue
        if not source_path.is_file():
            errors.append(f"{where} source does not exist: {source}")
        if source_entry.is_symlink() and not source_path.is_file():
            errors.append(f"{where} symlink target is not a file: {source}")
        if source in sources:
            errors.append(f"duplicate source: {source}")
        source_key = source.casefold()
        if source_key in normalized_sources:
            errors.append(
                f"case-insensitive source collision: {source} and {normalized_sources[source_key]}"
            )
        else:
            normalized_sources[source_key] = source
        sources.add(source)
        if PurePosixPath(source).parts[0].casefold() in RESERVED_OUTPUTS:
            errors.append(f"{where} source collides with reserved output: {source}")
        route = expected_route(source)
        route_key = route.casefold()
        if route_key in folded_routes:
            errors.append(
                f"route collision: {route} ({folded_routes[route_key]} and {source})"
            )
        else:
            folded_routes[route_key] = source
        routes[route] = source
        normalized = (route.rstrip("/") or "/").casefold()
        if normalized in normalized_routes:
            errors.append(
                f"normalized route/output collision: {route} and {normalized_routes[normalized]}"
            )
        normalized_routes[normalized] = route
    missing = sorted(REQUIRED - sources)
    if missing:
        errors.append("required pages missing from manifest: " + ", ".join(missing))
    for path in SITE.rglob("*"):
        if path.is_symlink():
            errors.append(
                f"symlink is not allowed under docs/site: {path.relative_to(SITE)}"
            )
    markdown_files = {
        path.relative_to(SITE).as_posix()
        for path in SITE.rglob("*.md")
        if not path.is_symlink() and path.is_file()
    }
    unlisted = sorted(markdown_files - sources)
    if unlisted:
        errors.append("Markdown files missing from manifest: " + ", ".join(unlisted))
    redirect_routes: dict[str, str] = {}
    navigation_ids: list[str] = []
    navigation = data.get("navigation")
    if not isinstance(navigation, list):
        errors.append("schemaVersion 2 requires a navigation array")
    else:

        def check_items(items: object, where: str) -> None:
            if not isinstance(items, list):
                errors.append(f"{where} items must be an array")
                return
            for index, item in enumerate(items):
                item_where = f"{where}.items[{index}]"
                if isinstance(item, str):
                    if item not in page_ids:
                        errors.append(
                            f"{item_where} references unknown page id: {item}"
                        )
                    navigation_ids.append(item)
                elif isinstance(item, dict) and isinstance(item.get("title"), str):
                    check_items(item.get("items"), item_where)
                else:
                    errors.append(f"{item_where} must be a page id or navigation group")

        for index, group in enumerate(navigation):
            if not isinstance(group, dict) or not isinstance(group.get("title"), str):
                errors.append(f"navigation[{index}] requires a title")
                continue
            check_items(group.get("items"), f"navigation[{index}]")
    navigation_counts: dict[str, int] = {}
    for identifier in navigation_ids:
        navigation_counts[identifier] = navigation_counts.get(identifier, 0) + 1
    for identifier, count in navigation_counts.items():
        if count > 1:
            errors.append(f"navigation includes page id {identifier!r} more than once")
    for page in pages:
        if not isinstance(page, dict):
            continue
        identifier, kind, source = page.get("id"), page.get("kind"), page.get("source")
        if not isinstance(identifier, str) or not isinstance(source, str):
            continue
        count = navigation_counts.get(identifier, 0)
        if isinstance(kind, str) and kind in EXCLUDED_NAVIGATION_KINDS:
            if count:
                errors.append(
                    f"navigation must not include excluded {kind} page id {identifier!r}"
                )
        elif expected_route(source) != "/" and kind != "index" and count != 1:
            errors.append(
                f"navigation must include product page id {identifier!r} exactly once; found {count}"
            )
    redirects = data.get("redirects", [])
    if not isinstance(redirects, list):
        errors.append("redirects must be an array")
    else:
        for index, redirect in enumerate(redirects):
            where = f"redirects[{index}]"
            if not isinstance(redirect, dict):
                errors.append(f"{where} must be an object")
                continue
            old, new = redirect.get("from"), redirect.get("to")
            if not isinstance(old, str) or not old.startswith("/"):
                errors.append(f"{where} requires an absolute from route")
                continue
            if not isinstance(new, str) or new not in routes:
                errors.append(f"{where} targets an unknown canonical route: {new}")
                continue
            if old in routes or old in redirect_routes:
                errors.append(f"{where} duplicates a page or redirect route: {old}")
                continue
            redirect_routes[old] = new

    for source in sorted(sources):
        page = SITE / source
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        physical_page = page.resolve()
        if OBSOLETE_DOC_LINK.search(text):
            errors.append(
                f"{source}: contains an obsolete hosted or legacy documentation link"
            )
        escaped_angles = escaped_angle_count(text)
        if escaped_angles:
            errors.append(
                f"{source}: contains {escaped_angles} HTML-escaped angle bracket(s) "
                "inside Markdown code; write literal < and > characters instead"
            )
        frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
        if not frontmatter:
            errors.append(f"{source}: missing YAML frontmatter")
        else:
            fields: dict[str, str] = {}
            for key in ("title", "description"):
                match = re.search(rf"^{key}:\s*(.*?)\s*$", frontmatter.group(1), re.M)
                if match:
                    value = match.group(1)
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        value = value.strip("'\"")
                    if isinstance(value, str) and value.strip():
                        fields[key] = value
            for key in ("title", "description"):
                if key not in fields:
                    errors.append(f"{source}: frontmatter requires non-empty {key}")
                elif fields[key] != next(
                    (item[key] for item in pages if item.get("source") == source), ""
                ):
                    errors.append(
                        f"{source}: frontmatter {key} does not match manifest"
                    )
        for raw in LINK.findall(text):
            target = raw.split()[0].strip("<>")
            if OBSOLETE_DOC_LINK.search(target):
                errors.append(
                    f"{source}: obsolete hosted or legacy documentation link: {target}"
                )
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                if not parsed.path and parsed.fragment:
                    if unquote(parsed.fragment).lower() not in heading_anchors(text):
                        errors.append(
                            f"{source}: unresolved local heading anchor {target}"
                        )
                continue
            path = unquote(parsed.path)
            if path.startswith("/"):
                errors.append(
                    f"{source}: public-page links must be relative .md links: {target}"
                )
                continue
            resolved = (physical_page.parent / path).resolve()
            if ROOT.resolve() not in resolved.parents and resolved != ROOT.resolve():
                errors.append(
                    f"{source}: local link escapes the M3 repository: {target}"
                )
            elif not resolved.exists():
                errors.append(
                    f"{source}: unresolved local link in M3 repository: {target}"
                )
            elif EXAMPLES.resolve() in {resolved, *resolved.parents}:
                errors.append(
                    f"{source}: links to example project path {target}; the "
                    "agent skill bundle cannot ship repository files, so a "
                    "reader of the bundle would see only a path. Show the "
                    "file as a displayed block in its page (example.json "
                    "displayed_blocks, then render_docs_examples.py) and link "
                    "to that page section instead"
                )
            elif (
                SITE.resolve() in resolved.parents and resolved.suffix.lower() != ".md"
            ):
                errors.append(
                    f"{source}: public-page links must target relative .md files: {target}"
                )
            elif parsed.fragment and resolved.suffix.lower() == ".md":
                target_text = resolved.read_text(encoding="utf-8")
                if unquote(parsed.fragment).lower() not in heading_anchors(target_text):
                    errors.append(
                        f"{source}: unresolved local heading anchor in {target}"
                    )
    lines, limitations = output_terms(
        PLUGIN_SOURCE.read_text(encoding="utf-8"),
        LIMITATIONS_SOURCE.read_text(encoding="utf-8"),
    )
    output_page = OUTPUT_PAGE.read_text(encoding="utf-8")
    for term in [*lines, *limitations]:
        if f"`{term}`" not in output_page:
            errors.append(f"reference/output.md does not document {term!r}")
    if errors:
        print("Documentation validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(sources)} documentation pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
