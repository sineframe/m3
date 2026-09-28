#!/usr/bin/env python3
"""Validate M3's dependency-free documentation publishing contract."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
MANIFEST = SITE / "navigation.json"
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
LEGACY_PREFIXES = ("sdk/", "cli/")
LEGACY_FILES = {"ci.md"}
LINK = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")


def expected_route(source: str) -> str:
    path = PurePosixPath(source)
    if path.name == "index.md":
        parent = "/" + ("" if str(path.parent) == "." else str(path.parent) + "/")
        return parent
    return "/" + str(path.with_suffix(""))


def heading_anchors(text: str) -> set[str]:
    """Return common VitePress/GitHub heading slugs for fragment validation."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"!?(?:\[([^\]]*)\])\([^)]*\)", r"\1", match.group(1))
        title = re.sub(r"<[^>]+>|[`*_~]", "", title)
        title = (
            unicodedata.normalize("NFKD", title)
            .encode("ascii", "ignore")
            .decode()
            .lower()
        )
        slug = re.sub(r"[^a-z0-9 -]", "", title).strip().replace(" ", "-")
        slug = re.sub(r"-+", "-", slug)
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


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
    schema_version = data.get("schemaVersion", 1)
    if schema_version not in {1, 2}:
        errors.append(f"unsupported docs schemaVersion: {schema_version}")
    routes: dict[str, str] = {}
    normalized_routes: dict[str, str] = {}
    sources: set[str] = set()
    page_ids: set[str] = set()
    for number, page in enumerate(pages, 1):
        where = f"pages[{number}]"
        if not isinstance(page, dict) or not all(
            isinstance(page.get(k), str) and page[k].strip()
            for k in ("title", "description", "source", "route")
        ):
            errors.append(
                f"{where} requires non-empty title, description, source, and route"
            )
            continue
        source, route = page["source"], page["route"]
        if schema_version == 2:
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
            or ".." in PurePosixPath(source).parts
            or not source.endswith(".md")
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
        sources.add(source)
        if not route.startswith("/") or "?" in route or "#" in route:
            errors.append(f"{where} route must be an absolute clean path: {route}")
        if route != "/" and route.endswith("/") != (
            PurePosixPath(source).name == "index.md"
        ):
            errors.append(f"{where} route has wrong trailing-slash form: {route}")
        if route != expected_route(source):
            errors.append(
                f"{where} route/source mismatch: {route} != {expected_route(source)}"
            )
        if route in routes:
            errors.append(f"route collision: {route} ({routes[route]} and {source})")
        routes[route] = source
        normalized = route.rstrip("/") or "/"
        if normalized in normalized_routes:
            errors.append(
                f"normalized route/output collision: {route} and {normalized_routes[normalized]}"
            )
        normalized_routes[normalized] = route
    missing = sorted(REQUIRED - sources)
    if missing:
        errors.append("required pages missing from manifest: " + ", ".join(missing))
    markdown_files = {path.relative_to(SITE).as_posix() for path in SITE.rglob("*.md")}
    legacy = {
        source
        for source in markdown_files
        if source in LEGACY_FILES or source.startswith(LEGACY_PREFIXES)
    }
    unlisted = sorted(markdown_files - sources - legacy)
    if unlisted:
        errors.append("Markdown files missing from manifest: " + ", ".join(unlisted))
    redirect_routes: dict[str, str] = {}
    if schema_version == 2:
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
                    elif isinstance(item, dict) and isinstance(item.get("title"), str):
                        check_items(item.get("items"), item_where)
                    else:
                        errors.append(
                            f"{item_where} must be a page id or navigation group"
                        )

            for index, group in enumerate(navigation):
                if not isinstance(group, dict) or not isinstance(
                    group.get("title"), str
                ):
                    errors.append(f"navigation[{index}] requires a title")
                    continue
                check_items(group.get("items"), f"navigation[{index}]")
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
        for raw in LINK.findall(text):
            target = raw.split()[0].strip("<>")
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
                if path.startswith("/docs/"):
                    errors.append(
                        f"{source}: site-root link must omit the configured /docs base: {target}"
                    )
                elif path not in routes and path not in redirect_routes:
                    errors.append(
                        f"{source}: unresolved site link or wrong trailing slash {target}"
                    )
                elif parsed.fragment and path in routes:
                    target_source = routes[path]
                    target_text = (
                        (SITE / target_source).resolve().read_text(encoding="utf-8")
                    )
                    anchors = heading_anchors(target_text)
                    if unquote(parsed.fragment).lower() not in anchors:
                        errors.append(
                            f"{source}: unresolved heading anchor in {target}"
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
            elif parsed.fragment and resolved.suffix.lower() == ".md":
                target_text = resolved.read_text(encoding="utf-8")
                if unquote(parsed.fragment).lower() not in heading_anchors(target_text):
                    errors.append(
                        f"{source}: unresolved local heading anchor in {target}"
                    )
    if errors:
        print("Documentation validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(sources)} documentation pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
