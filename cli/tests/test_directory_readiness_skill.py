"""Static checks for the preparing-directory-submissions skill and its examples."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "preparing-directory-submissions"
EXAMPLES = ROOT / "benchmarks" / "directory-readiness"
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    block = text[4 : text.index("\n---\n", 4)]
    return {
        key.strip(): value.strip()
        for key, _, value in (line.partition(":") for line in block.splitlines())
    }


def test_frontmatter_names_the_skill_and_its_trigger() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    meta = _frontmatter(text)
    assert meta["name"] == SKILL.name
    assert 1 <= len(meta["description"]) <= 1024
    assert "testing-with-m3" in meta["description"]
    assert len(text.splitlines()) <= 500


@pytest.mark.parametrize(
    "page", sorted(p.relative_to(SKILL).as_posix() for p in SKILL.rglob("*.md"))
)
def test_relative_links_resolve(page: str) -> None:
    path = SKILL / page
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#", "<")):
            continue
        file = target.split("#", 1)[0]
        assert (path.parent / file).resolve().is_file(), f"{page}: {target}"


def test_quoted_policy_ids_exist() -> None:
    ids = {
        line[4:].split()[0]
        for page in SKILL.glob("references/policy-*.md")
        for line in page.read_text(encoding="utf-8").splitlines()
        if line.startswith("### ")
    }
    cited = set()
    for page in ("checks.md", "rejection-reasons.md", "test-cases.md"):
        text = (SKILL / "references" / page).read_text(encoding="utf-8")
        cited |= set(re.findall(r"`((?:claude|openai|muse)-[a-z0-9.-]+)`", text))
    assert cited - ids == set()


def _listing(manifest: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "check_listing.py"), str(manifest)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_listing_check_flags_the_problem_manifest() -> None:
    lines = _listing(EXAMPLES / "problem-server" / ".codex-plugin" / "plugin.json")
    failures = {
        line.split()[1].rstrip(":") for line in lines if line.startswith("FAIL")
    }
    assert failures == {
        "displayName",
        "shortDescription",
        "privacyPolicyURL",
        "termsOfServiceURL",
        "logo",
        "composerIcon",
    }


def test_listing_check_passes_the_clean_manifest() -> None:
    lines = _listing(EXAMPLES / "clean-server" / ".codex-plugin" / "plugin.json")
    assert not [line for line in lines if line.startswith("FAIL")]


def test_probe_refuses_plain_http() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL / "scripts" / "probe_connectivity.py"),
            "http://example.com/mcp",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout.startswith("FAIL     public https:")


def _lint(server: str, module: str) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "m3_cli",
            "lint",
            "--directory",
            "all",
            "--json",
            "--",
            sys.executable,
            "-m",
            module,
        ],
        cwd=EXAMPLES / server,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(result.stdout)


def test_problem_server_lint_findings() -> None:
    report = _lint("problem-server", "ledgerly_mcp.server")
    found = {(f["rule"], f["location"]) for f in report["findings"]}
    assert found == {
        ("directory-tool-title-missing", "tool 'manage_customers' title"),
        ("directory-claude-safety-hints", "tool 'summarize_conversation' annotations"),
        ("directory-hints-not-explicit", "tool 'manage_customers' annotations"),
        ("directory-hints-not-explicit", "tool 'summarize_conversation' annotations"),
        ("directory-claude-safety-hints", "tool 'start_checkout' annotations"),
    }
    assert report["directories"]["not_checked"] == ["muse"]


def test_clean_server_has_no_lint_errors() -> None:
    report = _lint("clean-server", "fieldnotes_mcp.server")
    assert report["counts"]["error"] == 0
    assert [f["location"] for f in report["findings"]] == [
        "tool 'create_note' annotations"
    ]
