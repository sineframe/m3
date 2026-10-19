"""`m3 lint --directory` against reference servers.

``fixtures/directory_corpus`` holds servers that are not single-rule cases: a
clean example that every directory check should pass.
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
CORPUS = FIXTURES / "directory_corpus"
CASES = FIXTURES / "lint_cases"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(spec: dict[str, Any], *args: str) -> tuple[int, dict[str, Any]]:
    """Lint ``spec`` through the fixture server, as test_lint_rules does."""

    with tempfile.TemporaryDirectory() as directory:
        spec_path = Path(directory) / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        previous = os.environ.get("LINT_SPEC")
        os.environ["LINT_SPEC"] = str(spec_path)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = main(
                    ["lint", "--json", *args, "--", sys.executable, str(LINT_SERVER)]
                )
        finally:
            if previous is None:
                os.environ.pop("LINT_SPEC", None)
            else:
                os.environ["LINT_SPEC"] = previous
    return code, json.loads(out.getvalue())


def _findings(report: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    return [f for f in report["findings"] if f["rule"] == rule]


def test_clean_server_passes_every_directory() -> None:
    code, report = _run(_load(CORPUS / "clean.json"), "--directory", "all")
    assert report["findings"] == []
    assert code == 0
    assert report["exit_code"] == 0
    # No rule checks Muse from metadata, so it is reported as not checked.
    assert report["directories"]["not_checked"] == ["muse"]


def test_tag_chars_detail_reads_hidden_text() -> None:
    case = next(
        c
        for c in _load(CASES / "directory-hidden-content.json")["cases"]
        if c["name"] == "tag-chars"
    )
    description = case["spec"]["tools"][0]["description"]
    tags = [c for c in description if 0xE0000 <= ord(c) <= 0xE007F]
    hidden = "".join(chr(ord(c) - 0xE0000) for c in tags)
    _, report = _run(case["spec"], *case["args"])
    (finding,) = _findings(report, "directory-hidden-content")
    detail = finding["detail"]
    assert f"{len(tags)} invisible Unicode tag characters" in detail
    assert "U+E0000-U+E007F" in detail
    assert hidden in detail
