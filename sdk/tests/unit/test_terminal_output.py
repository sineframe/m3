"""Interactive terminal rendering: live progress line and run summary panel."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from m3._terminal import Style, truncate, visible_len


class _Reporter:
    def __init__(self, *, isatty: bool = True) -> None:
        self.isatty = isatty
        self.written: list[str] = []
        self.lines: list[str] = []
        self._show_progress_info = "count"
        self._showfspath = None

    def rewrite(self, text: str, **_markup: Any) -> None:
        self.written.append(text)

    def write_line(self, text: str, **_markup: Any) -> None:
        self.lines.append(text)


def _progress(reporter: _Reporter, **option: Any) -> Any:
    from m3.pytest_plugin import _Progress

    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=0, **option),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
    )
    progress = _Progress(config)
    progress.reporter = reporter
    progress.enabled = progress.enabled and progress._is_tty()
    progress.disable_native_progress()
    return progress


def _report(nodeid: str, when: str, outcome: str) -> SimpleNamespace:
    return SimpleNamespace(nodeid=nodeid, when=when, outcome=outcome)


def test_live_line_hides_pytest_letters_and_paths_then_restores_them() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    assert reporter._show_progress_info is False
    assert reporter._showfspath is False

    hook = progress.pytest_report_teststatus(report=None, config=None)
    next(hook)
    with pytest.raises(StopIteration) as stop:
        hook.send(("passed", ".", "PASSED"))
    assert stop.value.value == ("passed", "", "PASSED")

    progress.restore_native_progress()
    assert reporter._show_progress_info == "count"
    assert reporter._showfspath is None


def test_live_line_keeps_failures_above_it_without_parameter_values() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(SimpleNamespace(items=[1, 2]))
    nodeid = "tests/test_api.py::test_login[token=hunter2]"
    progress.pytest_runtest_logstart(nodeid, ("tests/test_api.py", 1, "test_login"))
    progress.pytest_runtest_logreport(_report(nodeid, "call", "failed"))
    progress.pytest_runtest_logreport(
        _report("tests/test_api.py::ok", "call", "passed")
    )
    progress.finish()

    output = "".join(reporter.written)
    assert "hunter2" not in output
    failure = next(text for text in reporter.written if text.endswith("\n"))
    assert "tests/test_api.py::test_login" in failure
    assert "2/2" in reporter.written[-1]
    assert reporter.lines == [""]


def test_live_line_stays_within_the_terminal_width() -> None:
    reporter = _Reporter()
    reporter._tw = SimpleNamespace(fullwidth=60, hasmarkup=False, _file=None)
    progress = _progress(reporter)
    progress.pytest_collection_finish(SimpleNamespace(items=list(range(120))))
    progress.pytest_runtest_logstart(
        "tests/test_x.py::test_" + "very_long_name_" * 10, ("", 1, "")
    )
    assert all(visible_len(text) <= 60 for text in reporter.written)


def test_finished_line_marks_interrupted_runs() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(SimpleNamespace(items=[1, 2]))
    progress.pytest_runtest_logreport(_report("a", "call", "passed"))
    progress.finish()
    assert reporter.written[-1].lstrip().startswith("!")


def test_live_line_yields_to_uncaptured_output_and_non_terminals() -> None:
    assert _progress(_Reporter(), capture="no").enabled is False
    assert _progress(_Reporter(isatty=False)).enabled is False


def test_finish_prints_nothing_when_no_tests_were_collected() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(SimpleNamespace(items=[]))
    progress.finish()
    assert reporter.lines == []


def test_run_panel_summarises_the_run(tmp_path: Path, monkeypatch: Any) -> None:
    from m3.pytest_plugin import _write_run_panel

    monkeypatch.chdir(tmp_path)
    reporter = _Reporter()
    reporter._tw = SimpleNamespace(fullwidth=120)
    output = tmp_path / ".m3" / "reports" / "run-1" / "feedback.json"
    _write_run_panel(
        reporter,
        Style(False),
        "run-1",
        output,
        [(2, "passed", "passed"), (0, "skipped", "skipped"), (1, "setup_error", "x")],
        0,
        1,
    )
    lines = reporter.lines
    assert lines[0] == ""
    assert lines[1].startswith("  ╭─ M3 run-1 ")
    assert "2 passed · 1 x" in lines[2]
    assert "0 tool errors · 1 execution" in lines[3]
    assert ".m3/reports/run-1/feedback.json" in lines[4]
    assert len({visible_len(line) for line in lines[1:]}) == 1


def test_run_panel_reports_xfailed_after_skipped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from m3.pytest_plugin import _write_run_panel

    monkeypatch.chdir(tmp_path)
    reporter = _Reporter()
    reporter._tw = SimpleNamespace(fullwidth=120)
    _write_run_panel(
        reporter,
        Style(False),
        "run-1",
        tmp_path / "feedback.json",
        [(1, "passed", "passed"), (1, "skipped", "skipped"), (2, "xfailed", "xfailed")],
        0,
        1,
    )
    assert "1 passed · 1 skipped · 2 xfailed" in reporter.lines[2]


def test_live_line_counts_xfail_separately_from_skips() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(SimpleNamespace(items=[1, 2, 3]))
    xfail = _report("x", "call", "skipped")
    xfail.wasxfail = ""
    progress.pytest_runtest_logreport(xfail)
    progress.pytest_runtest_logreport(_report("s", "setup", "skipped"))
    progress.pytest_runtest_logreport(_report("p", "call", "passed"))

    assert (progress.xfailed, progress.skipped, progress.passed) == (1, 1, 1)
    assert "xfail 1" in reporter.written[-1]

    progress.pytest_runtest_logreport(_report("x", "teardown", "failed"))
    assert (progress.xfailed, progress.failed) == (0, 1)


def test_box_drops_its_right_edge_when_it_would_wrap() -> None:
    lines = Style(False).box("T", [("key", "x" * 50)], width=40)
    assert not lines[1].endswith("│")
    assert lines[-1] == "  ╰──"


def test_style_without_colour_emits_no_escape_codes() -> None:
    style = Style(False)
    text = style.red("a") + style.bold("b") + style.bar(3, 10, 10)
    assert "\x1b" not in text


def test_truncate_keeps_the_requested_end() -> None:
    assert truncate("abcdefgh", 5) == "…efgh"
    assert truncate("abcdefgh", 5, keep="start") == "abcd…"
    assert truncate("abc", 5) == "abc"
