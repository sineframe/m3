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
    "ci.md",
    "cli/index.md",
    "cli/commands.md",
    "cli/harnesses.md",
    "cli/results-and-ui.md",
    "sdk/index.md",
    "sdk/quick-start.md",
    "sdk/http.md",
    "sdk/concepts.md",
    "sdk/examples.md",
    "sdk/evaluations.md",
    "sdk/elicitation.md",
    "sdk/elicitation-api.md",
}
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
    routes: dict[str, str] = {}
    normalized_routes: dict[str, str] = {}
    sources: set[str] = set()
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
        if (
            "\\" in source
            or ".." in PurePosixPath(source).parts
            or not source.endswith(".md")
        ):
            errors.append(
                f"{where} source must be a relative .md path without '..' or backslashes: {source}"
            )
            continue
        source_path = (SITE / source).resolve()
        if (
            source_path.parent != SITE.resolve()
            and SITE.resolve() not in source_path.parents
        ):
            errors.append(f"{where} source escapes docs/site: {source}")
            continue
        if not source_path.is_file():
            errors.append(f"{where} source does not exist: {source}")
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
    unlisted = sorted(markdown_files - sources)
    if unlisted:
        errors.append("Markdown files missing from manifest: " + ", ".join(unlisted))
    for source in sorted(sources):
        page = SITE / source
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        for raw in LINK.findall(text):
            target = raw.split()[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = unquote(parsed.path)
            if path.startswith("/"):
                if path.startswith("/docs/"):
                    errors.append(
                        f"{source}: site-root link must omit the configured /docs base: {target}"
                    )
                elif path not in routes:
                    errors.append(
                        f"{source}: unresolved site link or wrong trailing slash {target}"
                    )
                elif parsed.fragment:
                    target_source = routes[path]
                    target_text = (SITE / target_source).read_text(encoding="utf-8")
                    anchors = heading_anchors(target_text)
                    if unquote(parsed.fragment).lower() not in anchors:
                        errors.append(
                            f"{source}: unresolved heading anchor in {target}"
                        )
                continue
            resolved = (page.parent / path).resolve()
            if SITE.resolve() not in resolved.parents and resolved != SITE.resolve():
                errors.append(f"{source}: link escapes docs/site: {target}")
            elif not resolved.is_file():
                errors.append(f"{source}: unresolved local link {target}")
    if errors:
        print("Documentation validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(sources)} documentation pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
