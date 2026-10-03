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
    source: str = SOURCE,
    quiet: bool = True,
    timings: bool = True,
    results_db: bool = True,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_case.py").write_text(source, encoding="utf-8")
    env = os.environ.copy()
    env.pop("M3_TIMINGS", None)
    env.pop("GITHUB_STEP_SUMMARY", None)
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    if timings:
        env["M3_TIMINGS"] = "1"
    env.update(env_extra or {})
    args = [sys.executable, "-m", "pytest", "-p", "m3.pytest_plugin"]
    if quiet:
        args.append("-q")
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


PARAM_SOURCE = (
    "import pytest\n"
    "pytestmark = pytest.mark.m3(suite_name='timing')\n"
    "@pytest.mark.parametrize('token', ['sk-test-SECRET123', 'other'])\n"
    "def test_param(token):\n"
    "    assert token\n"
)


def test_parameter_values_never_reach_timing_outputs(tmp_path: Path) -> None:
    summary = tmp_path / "step_summary.md"
    result = _run(
        tmp_path,
        source=PARAM_SOURCE,
        env_extra={"GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (directory,) = _timing_dirs(tmp_path)
    texts = [result.stdout, summary.read_text(encoding="utf-8")]
    texts += [p.read_text(encoding="utf-8") for p in directory.glob("*.jsonl")]
    texts += [
        (directory / n).read_text(encoding="utf-8")
        for n in ("summary.json", "trace.json")
    ]
    assert all("SECRET123" not in text for text in texts)
    keys = {r["key"] for r in _records(directory) if r.get("name") == "test"}
    assert keys == {"test_case.py::test_param[0]", "test_case.py::test_param[1]"}


def test_reports_built_without_terminal_plugin(tmp_path: Path) -> None:
    result = _run(tmp_path, "-p", "no:terminal", quiet=False)
    assert result.returncode == 0, result.stdout + result.stderr
    (directory,) = _timing_dirs(tmp_path)
    assert (directory / "summary.json").is_file()
    assert (directory / "trace.json").is_file()
    assert result.stdout.count("M3 timings:") == 1


def test_normal_run_prints_one_summary(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.stdout.count("M3 timings:") == 1


FAIL_SOURCE = (
    "import pytest\n"
    "pytestmark = pytest.mark.m3(suite_name='timing')\n"
    "@pytest.fixture\n"
    "def bad():\n"
    "    raise RuntimeError('boom')\n"
    "def test_assert():\n"
    "    assert 1 == 2\n"
    "def test_fixture(bad):\n"
    "    pass\n"
)


def test_failures_are_recorded_as_errors(tmp_path: Path) -> None:
    result = _run(tmp_path, source=FAIL_SOURCE)
    assert result.returncode == 1, result.stdout + result.stderr
    (directory,) = _timing_dirs(tmp_path)
    records = _records(directory)

    def statuses(name: str, key: str | None = None) -> set[str]:
        return {
            r.get("status")
            for r in records
            if r.get("name") == name and (key is None or r.get("key") == key)
        }

    assert statuses("test.call") >= {"error"}
    assert statuses("test", "test_case.py::test_assert") == {"error"}
    assert statuses("fixture.setup", "bad") == {"error"}


def test_unwritable_directory_warns_and_run_passes(tmp_path: Path) -> None:
    (tmp_path / ".m3").write_text("not a directory", encoding="utf-8")
    result = _run(tmp_path, results_db=False, env_extra={"PYTHONWARNINGS": "always"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "m3 timings disabled" in result.stdout + result.stderr
