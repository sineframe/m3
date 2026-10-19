"""`m3 lint --directory all` against a real server's tool definitions.

``fixtures/directory_corpus/github-mcp-server.json`` holds the tool snapshots of
github/github-mcp-server (MIT) at a pinned commit. The ``.expected.json`` file
next to it lists every directory finding, each reviewed by hand and marked
``true`` or ``false-positive``. A rule change that adds or removes a hit, or
changes a directory's severity, fails here; update the expected file on purpose.
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

from m3_cli import main

FIXTURES = Path(__file__).parent / "fixtures"
LINT_SERVER = FIXTURES / "lint_server.py"
CORPUS = FIXTURES / "directory_corpus" / "github-mcp-server.json"
EXPECTED = FIXTURES / "directory_corpus" / "github-mcp-server.expected.json"
DIRECTORIES = ("claude", "openai", "muse")
RANK = {"info": 0, "warning": 1, "error": 2}

Key = tuple[str, str, tuple[tuple[str, str | None], ...]]


def _lint(spec: Any) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        spec_path = Path(directory) / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        previous = os.environ.get("LINT_SPEC")
        os.environ["LINT_SPEC"] = str(spec_path)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                main(
                    [
                        "lint",
                        "--json",
                        "--directory",
                        "all",
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
    return json.loads(out.getvalue())


def _key(rule: str, location: str, severities: dict[str, str | None]) -> Key:
    return rule, location, tuple((d, severities.get(d)) for d in DIRECTORIES)


def _actual(report: dict[str, Any]) -> list[Key]:
    keys = []
    for finding in report["findings"]:
        if not finding.get("directories"):
            continue
        worst: dict[str, str | None] = {}
        for effect in finding["directories"]:
            current = worst.get(effect["directory"])
            if current is None or RANK[effect["severity"]] > RANK[current]:
                worst[effect["directory"]] = effect["severity"]
        keys.append(_key(finding["rule"], finding["location"], worst))
    return keys


def _format(keys: set[Key]) -> str:
    return "\n".join(
        f"  {rule} @ {location} {dict(severities)}"
        for rule, location, severities in sorted(keys)
    )


def test_github_corpus_directory_findings_match_reviewed_list() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    expected_rows = json.loads(EXPECTED.read_text(encoding="utf-8"))["findings"]
    for row in expected_rows:
        assert row["verdict"] in {"true", "false-positive"}, row
        assert set(row["directories"]) == set(DIRECTORIES), row
    expected = [_key(r["rule"], r["location"], r["directories"]) for r in expected_rows]
    actual = _actual(_lint(corpus["spec"]))
    assert len(set(actual)) == len(actual), "duplicate findings in the report"
    assert len(set(expected)) == len(expected), "duplicate rows in the expected file"

    unexpected = set(actual) - set(expected)
    missing = set(expected) - set(actual)
    message = []
    if unexpected:
        message.append(
            f"unexpected findings ({len(unexpected)}):\n{_format(unexpected)}"
        )
    if missing:
        message.append(f"missing findings ({len(missing)}):\n{_format(missing)}")
    assert not message, "\n".join(message)
