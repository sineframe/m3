"""`m3 lint` output without --directory, pinned byte for byte.

``fixtures/lint_golden`` holds the JSON report, the human report and the exit
code of every case that existed in ``fixtures/lint_cases`` before directory
checks did; cases added since are not pinned here. Regenerate with
``M3_LINT_GOLDEN_UPDATE=1`` only when a harness rule change is meant to alter
the output.
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

FIXTURES = Path(__file__).parent / "fixtures"
LINT_SERVER = FIXTURES / "lint_server.py"
GOLDEN = FIXTURES / "lint_golden"
CASES = FIXTURES / "lint_cases"
GOLDEN_FILES = sorted(p for p in GOLDEN.glob("*.json") if p.name != "list-rules.json")
UPDATE = os.environ.get("M3_LINT_GOLDEN_UPDATE") == "1"


def _lint(spec: Any, args: list[str], *, as_json: bool) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as directory:
        spec_path = Path(directory) / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        previous = os.environ.get("LINT_SPEC")
        os.environ["LINT_SPEC"] = str(spec_path)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "lint",
                        *(["--json"] if as_json else []),
                        *args,
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
    return code, out.getvalue()


def _normalized(text: str) -> Any:
    report = json.loads(text)
    if report.get("target", {}).get("kind") == "stdio":
        report["target"]["command"] = ["<python>", "<lint_server>"]
    return report


def _record(path: Path, names: set[str]) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases: dict[str, Any] = {}
    for case in data["cases"]:
        if case["name"] not in names:
            continue
        code, text = _lint(case["spec"], case["args"], as_json=True)
        human_code, human = _lint(case["spec"], case["args"], as_json=False)
        cases[case["name"]] = {
            "exit_code": code,
            "human_exit_code": human_code,
            "json": _normalized(text),
            "human": human,
        }
    return cases


@pytest.mark.parametrize("golden", GOLDEN_FILES, ids=[p.stem for p in GOLDEN_FILES])
def test_output_unchanged(golden: Path) -> None:
    expected = json.loads(golden.read_text(encoding="utf-8"))
    current = _record(CASES / golden.name, set(expected))
    if UPDATE:
        golden.write_text(
            json.dumps(current, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    assert current == expected


def test_rule_catalogue_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    """Existing rules keep their catalogue entries; new rules may be added."""

    golden = GOLDEN / "list-rules.json"
    assert main(["lint", "--list-rules", "--json"]) == 0
    catalogue = json.loads(capsys.readouterr().out)
    if UPDATE:
        golden.write_text(
            json.dumps(catalogue, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    expected = json.loads(golden.read_text(encoding="utf-8"))
    # Existing rules may gain directory effects; everything else is pinned.
    current = {
        rule["id"]: {k: v for k, v in rule.items() if k != "directories"}
        for rule in catalogue["rules"]
    }
    assert [current.get(rule["id"]) for rule in expected["rules"]] == expected["rules"]
    assert catalogue["known_latest"] == expected["known_latest"]
    assert catalogue["schema_version"] == expected["schema_version"]
