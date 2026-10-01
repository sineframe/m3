#!/usr/bin/env python3
"""Render the docs manifest from canonical Markdown pages and nav groups."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
OUTPUT = SITE / "navigation.json"
NAVIGATION = [
    {
        "title": "Getting started",
        "items": [
            "home",
            "getting-started",
            "cli",
            "start-install",
            "guides-agents-skill",
            "start-your-server",
        ],
    },
    {
        "title": "Guides",
        "items": [
            {
                "title": "Test servers",
                "items": [
                    "guides-servers-stdio",
                    "guides-servers-http",
                    "guides-servers-tools",
                    "guides-servers-resources-prompts",
                    "guides-servers-errors-schemas",
                    "guides-servers-stateful-tests",
                ],
            },
            {
                "title": "Test agents",
                "items": [
                    "guides-agents-first-test",
                    "guides-agents-harnesses",
                    "guides-agents-managed-runtimes",
                    "guides-agents-acp",
                    "guides-agents-sessions",
                    "guides-agents-matrices",
                ],
            },
            {
                "title": "Inspect results",
                "items": [
                    "guides-results-viewer",
                    "guides-results-traces",
                    "guides-results-persistence",
                    "guides-results-baselines",
                ],
            },
            {
                "title": "Evaluate behavior",
                "items": [
                    "guides-evaluations-assertions",
                    "guides-evaluations-custom",
                    "guides-evaluations-judges",
                    "guides-evaluations-aggregate",
                ],
            },
            {
                "title": "Handle interaction",
                "items": [
                    "guides-elicitation-plans",
                    "guides-elicitation-managed-input",
                ],
            },
            {
                "title": "Run in CI",
                "items": [
                    "guides-ci-run",
                    "guides-ci-github-actions",
                    "guides-ci-publish",
                    "guides-ci-access",
                ],
            },
            {
                "title": "Advanced",
                "items": [
                    "guides-advanced-async",
                    "guides-advanced-mocks-replay",
                    "guides-advanced-snapshots",
                    "guides-advanced-scripts",
                ],
            },
            "examples",
        ],
    },
    {
        "title": "Concepts",
        "items": [
            "concepts-testing-model",
            "concepts-lifecycle",
            "concepts-identity",
            "concepts-evidence",
            "concepts-outcomes",
            "concepts-isolation",
        ],
    },
    {
        "title": "Reference",
        "items": [
            "reference",
            "reference-cli",
            {
                "title": "Python SDK",
                "items": [
                    "reference-python",
                    "reference-python-m3-core",
                    "reference-python-m3-types",
                    "reference-python-m3-matchers",
                    "reference-python-m3-observability",
                    "reference-python-m3-evaluations",
                    "reference-python-m3-elicitation",
                    "reference-python-m3-matrix",
                    "reference-python-m3-testing",
                    "reference-python-m3-storage",
                    "reference-python-m3-errors",
                    "reference-python-api",
                ],
            },
            "reference-pytest",
            "reference-configuration",
            "reference-compatibility",
        ],
    },
    {
        "title": "Troubleshooting",
        "items": [
            "troubleshooting",
            "troubleshooting-install",
            "troubleshooting-servers",
            "troubleshooting-agents",
            "troubleshooting-results",
            "troubleshooting-ci",
        ],
    },
]

REDIRECTS = [
    {"from": "/cli/commands", "to": "/reference/cli/"},
    {"from": "/sdk/", "to": "/reference/python/"},
    {"from": "/sdk/quick-start", "to": "/getting-started"},
    {"from": "/sdk/http", "to": "/guides/servers/http"},
    {"from": "/sdk/concepts", "to": "/concepts/testing-model"},
    {"from": "/sdk/examples", "to": "/examples"},
    {"from": "/sdk/evaluations", "to": "/guides/evaluations/assertions"},
    {"from": "/sdk/elicitation", "to": "/guides/elicitation/plans"},
    {"from": "/sdk/elicitation-api", "to": "/reference/python/m3/elicitation"},
    {"from": "/ci", "to": "/guides/ci/run"},
    {"from": "/evaluations", "to": "/guides/evaluations/assertions"},
]

ALIASES = {
    "getting-started": ["quick start", "first MCP test"],
    "guides-agents-skill": ["npx skills", "testing-with-m3"],
    "reference-pytest": ["suite_name", "pytest marker", "fixtures"],
    "guides-results-baselines": ["compare runs", "baseline run ID"],
    "guides-results-persistence": ["results database", "saved runs"],
    "guides-evaluations-judges": ["judge key", "M3_JUDGE_API_KEY"],
    "guides-ci-publish": ["upload failed", "retry upload"],
    "reference-configuration": ["env file", "credentials"],
}


def page_id(source: str) -> str:
    if source == "index.md":
        return "home"
    path = PurePosixPath(source)
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "index":
        parts.pop()
    return "-".join(parts)


def metadata(text: str, source: str) -> tuple[str, str, str]:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if match is None:
        raise ValueError(f"{source} needs YAML frontmatter")
    fields: dict[str, str] = {}
    for key in ("title", "description"):
        value = re.search(rf"^{key}:\s*(.*?)\s*$", match.group(1), re.MULTILINE)
        if value is None:
            raise ValueError(f"{source} frontmatter needs {key}")
        raw = value.group(1)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw.strip("'\"")
        if not isinstance(parsed, str) or not parsed.strip():
            raise ValueError(f"{source} frontmatter needs non-empty {key}")
        fields[key] = parsed
    return fields["title"], fields["description"], text[match.end() :]


def page_kind(source: str) -> str:
    if source == "index.md":
        return "home"
    return source.split("/", 1)[0].removesuffix(".md")


def render() -> str:
    pages = []
    for path in sorted(SITE.rglob("*.md")):
        source = path.relative_to(SITE).as_posix()
        text = path.read_text(encoding="utf-8")
        title, page_description, content = metadata(text, source)
        heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if heading is None:
            raise ValueError(f"{source} needs one H1")
        identifier = page_id(source)
        page = {
            "id": identifier,
            "title": title,
            "source": source,
            "description": page_description,
            "kind": page_kind(source),
        }
        if identifier in ALIASES:
            page["searchAliases"] = ALIASES[identifier]
        pages.append(page)
    data = {
        "schemaVersion": 2,
        "pages": pages,
        "navigation": NAVIGATION,
        "redirects": REDIRECTS,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print("docs/site/navigation.json is stale")
            return 1
        print("Documentation navigation is current")
        return 0
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Rendered {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
