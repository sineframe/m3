from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = (
    "import pytest\n"
    "pytestmark = pytest.mark.m3(suite_name='timing')\n"
    "@pytest.fixture\n"
    "def user_fixture():\n"
    "    return 1\n"
    "def test_one(user_fixture):\n"
    "    assert user_fixture == 1\n"
)


def _run(
    tmp_path: Path,
    *extra: str,
    timings: bool = True,
    results_db: bool = True,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_case.py").write_text(SOURCE, encoding="utf-8")
    env = os.environ.copy()
    env.pop("M3_TIMINGS", None)
    env.pop("GITHUB_STEP_SUMMARY", None)
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    if timings:
        env["M3_TIMINGS"] = "1"
    env.update(env_extra or {})
    args = [sys.executable, "-m", "pytest", "-q", "-p", "m3.pytest_plugin"]
    if results_db:
        args += ["--results-db", str(tmp_path / "results.sqlite")]
    return subprocess.run(
        [*args, *extra, "test_case.py"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _timing_dirs(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / ".m3" / "reports").glob("*/timings"))


def _records(directory: Path) -> list[dict]:
    records = []
    for path in directory.glob("*.jsonl"):
        records += [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return records


@pytest.mark.parametrize("results_db", [False, True])
def test_timings_files_and_summary(tmp_path: Path, results_db: bool) -> None:
    result = _run(tmp_path, results_db=results_db)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "M3 timings:" in result.stdout
    (directory,) = _timing_dirs(tmp_path)
    assert (directory / "trace.json").is_file()
    assert (directory / "summary.json").is_file()
    assert list(directory.glob("controller-*.jsonl"))
    names = {r.get("name") for r in _records(directory)}
    assert {"pytest.configure", "pytest.collection", "test", "test.call"} <= names
    assert "fixture.setup" in names
    if results_db:
        feedback = directory.parent / "feedback.json"
        assert feedback.is_file()
        assert json.loads(feedback.read_text())["run_id"] == directory.parent.name
        assert {"pytest.finish", "feedback.export"} <= names
    fixtures = [r for r in _records(directory) if r.get("name") == "fixture.setup"]
    assert any(r["key"] == "user_fixture" for r in fixtures)
    tests = [r for r in _records(directory) if r.get("name") == "test"]
    assert tests and all(r["key"] == "test_case.py::test_one" for r in tests)
    calls = [r for r in _records(directory) if r.get("name") == "test.call"]
    assert all(r["test"] == "test_case.py::test_one" for r in calls)


def test_disabled_without_env_var(tmp_path: Path) -> None:
    result = _run(tmp_path, timings=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "M3 timings" not in result.stdout
    assert _timing_dirs(tmp_path) == []


def test_github_step_summary_gets_markdown(tmp_path: Path) -> None:
    summary = tmp_path / "step_summary.md"
    result = _run(tmp_path, env_extra={"GITHUB_STEP_SUMMARY": str(summary)})
    assert result.returncode == 0, result.stdout + result.stderr
    text = summary.read_text(encoding="utf-8")
    assert "|" in text and "---" in text


def test_cli_owner_writes_files_without_summary(tmp_path: Path) -> None:
    result = _run(tmp_path, "--m3-timings-owner=cli")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "M3 timings" not in result.stdout
    (directory,) = _timing_dirs(tmp_path)
    assert list(directory.glob("controller-*.jsonl"))
    assert not (directory / "trace.json").exists()
