"""Every `m3 lint` rule against its case corpus in ``fixtures/lint_cases``.

The same cases drive ``benchmarks/lint-oracle``, which checks the rule data
against the real Claude Code and Codex binaries.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from m3_cli import main
from m3_cli.lint.catalogue import RULES
from m3_cli.lint.codex import codex_normalized_size
from m3_cli.lint.directories import DIRECTORIES
from m3_cli.lint.versions import parse_version

FIXTURES = Path(__file__).parent / "fixtures"
LINT_SERVER = FIXTURES / "lint_server.py"
CASE_FILES = sorted((FIXTURES / "lint_cases").glob("*.json"))
IMPACT_RANK = {"advisory": 0, "degraded": 1, "tool": 2, "server": 3, "session": 4}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


CORPUS = {path.stem: _load(path) for path in CASE_FILES}
CASE_IDS = [
    (rule_id, case["name"])
    for rule_id, data in CORPUS.items()
    for case in data["cases"]
]
_results: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}


def _case(rule_id: str, name: str) -> dict[str, Any]:
    return next(c for c in CORPUS[rule_id]["cases"] if c["name"] == name)


def _run(rule_id: str, name: str) -> tuple[int, dict[str, Any]]:
    """Lint the case's spec through the fixture server; cached per case."""

    key = (rule_id, name)
    if key in _results:
        return _results[key]
    case = _case(rule_id, name)
    with tempfile.TemporaryDirectory() as directory:
        spec_path = Path(directory) / "spec.json"
        spec_path.write_text(json.dumps(case["spec"]), encoding="utf-8")
        previous = os.environ.get("LINT_SPEC")
        os.environ["LINT_SPEC"] = str(spec_path)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "lint",
                        "--json",
                        *case["args"],
                        "--",
                        sys.executable,
                        str(LINT_SERVER),
                    ]
                )
        finally:
            if previous is None:
                os.environ.pop("LINT_SPEC", None)
            else:
                os.environ["LINT_SPEC"] = previous
    report = json.loads(out.getvalue())
    _results[key] = (code, report)
    return code, report


@pytest.mark.parametrize(
    ("rule_id", "name"), CASE_IDS, ids=[f"{r}/{n}" for r, n in CASE_IDS]
)
def test_case(rule_id: str, name: str) -> None:
    case = _case(rule_id, name)
    code, report = _run(rule_id, name)
    matching = [f for f in report["findings"] if f["rule"] == rule_id]
    assert bool(matching) == (case["expect"] == "present"), report["findings"]
    if rule_id != "tools-list-failed":
        # Every other case must be fully readable, or its verdict means nothing.
        assert "tools-list-failed" not in {f["rule"] for f in report["findings"]}
    for harness, impact in case.get("impacts", {}).items():
        levels = [
            effect["impact"]
            for finding in matching
            for effect in finding["effects"]
            if effect["harness"] == harness
        ]
        assert levels, f"no {harness} effect"
        assert max(levels, key=IMPACT_RANK.__getitem__) == impact
    if "directories" in case:
        # The exact severity this rule reports per directory; null means none.
        assert set(case["directories"]) == set(DIRECTORIES), "name every directory"
        for directory, expected in case["directories"].items():
            levels = {
                effect["severity"]
                for finding in matching
                for effect in finding.get("directories", [])
                if effect["directory"] == directory
            }
            worst = "error" if "error" in levels else ("warning" if levels else None)
            assert worst == expected, (directory, matching)
    assert code == (1 if report["counts"]["error"] > 0 else 0)
    assert report["exit_code"] == code


def _effect_key(effect: Any) -> tuple[str, str | None, str | None, str]:
    """Identifies one catalogue effect; the summary separates effects that
    share a harness and range (codex-unresolvable-ref, for example)."""
    if isinstance(effect, dict):
        return (
            effect["harness"],
            effect["range"]["introduced"],
            effect["range"]["fixed"],
            effect["summary"],
        )
    return effect.harness, effect.range.introduced, effect.range.fixed, effect.summary


def test_corpus_complete() -> None:
    assert set(CORPUS) == {rule.id for rule in RULES}
    for rule_id, data in CORPUS.items():
        assert data["rule"] == rule_id
        expects = {case["expect"] for case in data["cases"]}
        assert expects == {"present", "absent"}, rule_id
    for rule in RULES:
        seen: set[tuple[str, str | None, str | None]] = set()
        for case in CORPUS[rule.id]["cases"]:
            if case["expect"] != "present":
                continue
            _, report = _run(rule.id, case["name"])
            for finding in report["findings"]:
                if finding["rule"] == rule.id:
                    seen.update(_effect_key(effect) for effect in finding["effects"])
        missing = {_effect_key(e) for e in rule.effects} - seen
        assert not missing, (
            f"{rule.id}: effects never exercised: {sorted(map(str, missing))}"
        )
        exercised: set[tuple[str, str, str]] = set()
        for case in CORPUS[rule.id]["cases"]:
            if case["expect"] == "present" and "directories" in case:
                _, report = _run(rule.id, case["name"])
                for finding in report["findings"]:
                    if finding["rule"] == rule.id:
                        exercised.update(
                            (e["directory"], e["severity"], e["summary"])
                            for e in finding.get("directories", [])
                        )
        unexercised = {
            (e.directory, e.severity, e.summary) for e in rule.directory_effects
        } - exercised
        assert not unexercised, (
            f"{rule.id}: directory effects never exercised: {sorted(unexercised)}"
        )


def _minus_one(version: str) -> str | None:
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch - 1}" if patch > 0 else None


def test_version_edges() -> None:
    for rule in RULES:
        for effect in rule.effects:
            bounds = effect.range
            if bounds.introduced is not None:
                assert bounds.contains(bounds.introduced), rule.id
                before = _minus_one(bounds.introduced)
                if before is not None:
                    assert not bounds.contains(before), rule.id
            if bounds.fixed is not None:
                assert not bounds.contains(bounds.fixed), rule.id
                before = _minus_one(bounds.fixed)
                if before is not None:
                    assert bounds.contains(before), rule.id


def _sized(count: int) -> dict[str, Any]:
    return {
        "properties": {
            "a": {"description": "x" * count, "type": "string"},
            "b": {"enum": ["p", "q"], "type": "string"},
        },
        "type": "object",
    }


def test_codex_size_boundary() -> None:
    assert codex_normalized_size(_sized(4890)) == 5000
    assert codex_normalized_size(_sized(4891)) == 5001
