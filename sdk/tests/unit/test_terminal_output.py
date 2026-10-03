"""Interactive terminal rendering: live progress line and run summary panel."""

from __future__ import annotations

import re
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
        option=SimpleNamespace(**{"verbose": 0, "numprocesses": 0, **option}),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
    )
    progress = _Progress(config)
    progress.reporter = reporter
    progress.enabled = progress.enabled and progress._is_tty()
    progress.disable_native_progress()
    return progress


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


_CONTROL = re.compile(
    r"\x1b\[(\d*)([A-Za-z])|\x1b\][^\x1b]*\x1b\\|\x1b\[[0-9;?]*[A-Za-z]"
)


def _screen(chunks: list[str]) -> list[str]:
    """Replay what the plugin wrote: \\r, \\n, cursor up, erase line/below."""

    lines, row, col = [""], 0, 0
    text = "".join(chunks)
    index = 0
    while index < len(text):
        match = _CONTROL.match(text, index)
        if match:
            index = match.end()
            amount, command = match.group(1), match.group(2)
            if command == "A":
                row = max(0, row - int(amount or 1))
            elif command == "J":
                lines[row] = lines[row][:col]
                del lines[row + 1 :]
            elif command == "K":
                lines[row] = lines[row][:col]
            continue
        char = text[index]
        index += 1
        if char == "\r":
            col = 0
        elif char == "\n":
            row, col = row + 1, 0
            if row == len(lines):
                lines.append("")
        else:
            line = lines[row].ljust(col)
            lines[row] = line[:col] + char + line[col + 1 :]
            col += 1
    return lines


def _items(*nodeids: str) -> SimpleNamespace:
    return SimpleNamespace(items=[SimpleNamespace(nodeid=nodeid) for nodeid in nodeids])


def _report(nodeid: str, when: str, outcome: str) -> SimpleNamespace:
    return SimpleNamespace(nodeid=nodeid, when=when, outcome=outcome)


def _run(progress: Any, nodeid: str, outcome: str, **extra: Any) -> None:
    progress.pytest_runtest_logstart(nodeid, ("", 1, ""))
    progress.pytest_runtest_logreport(
        SimpleNamespace(nodeid=nodeid, when="call", outcome=outcome, **extra)
    )


def test_live_block_flows_file_lines_and_failures_above_it() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    secret = "tests/test_api.py::test_login[token=hunter2]"
    progress.pytest_collection_finish(
        _items("tests/test_api.py::test_ok", secret, "tests/test_b.py::test_skip")
    )
    _run(progress, "tests/test_api.py::test_ok", "passed")
    crash = SimpleNamespace(
        message="assert 'open' == 'escalated'\nmore detail",
        path="/repo/tests/test_api.py",
        lineno=88,
    )
    _run(progress, secret, "failed", longrepr=SimpleNamespace(reprcrash=crash))
    _run(progress, "tests/test_b.py::test_skip", "skipped")
    progress.finish()

    screen = _screen(reporter.written)
    assert "hunter2" not in "".join(reporter.written)
    assert screen[:5] == [
        "",  # one blank line above everything the live block prints
        "  x tests/test_api.py::test_login",
        "      assert 'open' == 'escalated'  - test_api.py:88",
        "  x tests/test_api.py  1 passed - 1 failed  0.0s",
        "  + tests/test_b.py  1 skipped  0.0s",
    ]
    # Without colour the matrix is left out: its cells would all look alike.
    assert screen[5] == ""
    assert screen[6].startswith("  x ") and "3/3" in screen[6]
    assert len(screen) == 7
    assert reporter.lines == [""]


def test_live_block_stays_within_the_terminal_width() -> None:
    reporter = _Reporter()
    reporter._tw = SimpleNamespace(fullwidth=60, hasmarkup=False, _file=None)
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items(*(f"t.py::t{i}" for i in range(500))))
    progress.pytest_runtest_logstart(
        "tests/test_x.py::test_" + "very_long_name_" * 10, ("", 1, "")
    )
    screen = _screen(reporter.written)
    assert all(len(line) < 60 for line in screen)
    # Without colour there is no matrix: a blank line and the progress line.
    assert len(screen) == 2 and screen[0] == ""


def test_matrix_shows_each_test_state_in_colour() -> None:
    reporter = _Reporter()
    reporter._tw = SimpleNamespace(
        fullwidth=80, hasmarkup=True, _file=SimpleNamespace(encoding="utf-8")
    )
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items("a", "b", "c", "d", "e"))
    _run(progress, "a", "passed")
    _run(progress, "b", "failed")
    _run(progress, "c", "skipped")
    progress.pytest_runtest_logstart("d", ("", 1, ""))
    screen = _screen(reporter.written)
    assert screen[-3:-1] == ["  ■ ■ ■ ■ ·", ""]
    assert "\x1b[38;" in "".join(reporter.written)  # states are coloured


def test_finished_line_marks_interrupted_runs() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items("a", "b"))
    _run(progress, "a", "passed")
    progress.finish()
    assert _screen(reporter.written)[-1].startswith("  ! ")


def test_window_title_is_saved_shown_and_restored() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items("a"))
    _run(progress, "a", "failed")
    progress.finish()
    output = "".join(reporter.written)
    assert output.startswith("\x1b[?25l\x1b[22;0t")
    assert "\x1b]2;m3 - 1/1 - x 1\x1b\\" in output
    assert output.endswith("\x1b[23;0t\x1b[?25h")


def test_slowest_tests_group_cases_and_skip_fast_tests() -> None:
    progress = _progress(_Reporter(isatty=False))
    for nodeid, when, seconds in (
        ("tests/a.py::slow[secret]", "setup", 0.5),
        ("tests/a.py::slow[secret]", "call", 1.0),
        ("tests/a.py::slow[other]", "call", 0.7),
        ("tests/a.py::fast", "call", 0.01),
        ("tests/b.py::medium", "call", 0.6),
        ("tests/b.py::quick", "call", 0.4),
    ):
        progress.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=nodeid, when=when, outcome="passed", duration=seconds
            )
        )
    assert progress.slowest() == [
        ("slow (2 cases)", "a.py", 1.5),
        ("medium", "b.py", 0.6),
    ]


@pytest.mark.parametrize(
    ("terminal", "seconds", "expected"),
    [("iTerm.app", 31, True), ("iTerm.app", 5, False), ("Apple_Terminal", 31, False)],
)
def test_long_runs_notify_only_on_supporting_terminals(
    monkeypatch: pytest.MonkeyPatch, terminal: str, seconds: float, expected: bool
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", terminal)
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items("a"))
    _run(progress, "a", "passed")
    progress._started -= seconds
    progress.finish()
    assert ("\x1b]9;m3: 1 passed\x1b\\" in "".join(reporter.written)) is expected


def test_live_line_yields_to_uncaptured_output_and_non_terminals() -> None:
    assert _progress(_Reporter(), capture="no").enabled is False
    assert _progress(_Reporter(isatty=False)).enabled is False


def test_finish_prints_nothing_when_no_tests_were_collected() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items())
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


def test_run_panel_adds_baseline_and_slowest_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3.feedback import Comparison
    from m3.pytest_plugin import _write_run_panel

    monkeypatch.chdir(tmp_path)
    reporter = _Reporter()
    reporter._tw = SimpleNamespace(fullwidth=120)

    def attempt(outcome: str) -> list[dict[str, str]]:
        return [{"outcome": outcome}]

    comparison = Comparison(
        baseline_run_id="run-base",
        current_run_id="run-1",
        baseline_run_label="Run 7f3e1a0",
        test_changes=(
            {
                "node_id": "fixed",
                "baseline": attempt("failed"),
                "current": attempt("passed"),
            },
            {
                "node_id": "broke",
                "baseline": attempt("passed"),
                "current": attempt("failed"),
            },
            {"node_id": "added", "baseline": [], "current": attempt("passed")},
            {"node_id": "gone", "baseline": attempt("passed"), "current": []},
        ),
        coverage={"current_tests": 10},
    )
    _write_run_panel(
        reporter,
        Style(False),
        "run-1",
        tmp_path / "feedback.json",
        [(10, "passed", "passed")],
        0,
        0,
        comparison=comparison,
        slowest=[("test_slow", "a.py", 2.0), ("test_fast", "b.py", 0.5)],
    )
    text = "\n".join(reporter.lines)
    assert (
        "vs baseline   1 fixed · 1 regressed · 1 new · 1 removed · 7 unchanged  vs Run 7f3e1a0"
        in text
    )
    # The test name comes first; the file is last, so it is cut first.
    assert "slowest       test_slow    ━━━━━━━━━━━━   2.0s  a.py" in text
    assert "              test_fast    ━━━            0.5s  b.py" in text
    assert len({visible_len(line) for line in reporter.lines[1:]}) == 1


def test_run_panel_without_baseline_or_timings_keeps_its_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from m3.pytest_plugin import _write_run_panel

    monkeypatch.chdir(tmp_path)
    reporter = _Reporter()
    _write_run_panel(
        reporter,
        Style(False),
        "run-1",
        tmp_path / "f.json",
        [(1, "passed", "passed")],
        0,
        0,
    )
    keys = [line.split()[1] for line in reporter.lines[2:-1]]
    assert keys == ["verdicts", "observations", "feedback"]


def test_open_box_rows_are_cut_instead_of_wrapping() -> None:
    lines = Style(False).box("Title", [("feedback", "x" * 80)], width=40)
    assert all(visible_len(line) <= 40 for line in lines)
    assert lines[1].endswith("…")


def test_fit_closes_a_hyperlink_it_cuts() -> None:
    from m3._terminal import fit, hyperlink

    cut = fit("see " + hyperlink("file:///a", "a" * 30), 10)
    assert visible_len(cut) == 10
    assert cut.endswith("\x1b]8;;\x1b\\…")


def test_live_block_follows_xdist_workers_from_the_main_process() -> None:
    reporter = _Reporter()
    progress = _progress(reporter, numprocesses=2)
    assert progress.enabled is True
    # The xdist main process collects nothing itself.
    progress.pytest_collection_finish(_items())
    progress.pytest_xdist_node_collection_finished(
        node=None, ids=["tests/a.py::one", "tests/b.py::two", "tests/a.py::three"]
    )
    progress.pytest_runtest_logstart("tests/a.py::one", ("", 1, ""))
    progress.pytest_runtest_logstart("tests/b.py::two", ("", 1, ""))
    assert "2 running" in _screen(reporter.written)[-1]
    for nodeid in ("tests/a.py::one", "tests/b.py::two"):
        for when in ("call", "teardown"):
            progress.pytest_runtest_logreport(
                SimpleNamespace(nodeid=nodeid, when=when, outcome="passed")
            )
    progress.pytest_runtest_logstart("tests/a.py::three", ("", 1, ""))
    _run(progress, "tests/a.py::three", "passed")
    progress.finish()
    screen = _screen(reporter.written)
    # Files interleave across workers, so there are no per-file lines.
    assert not any(".py  " in line for line in screen)
    assert "3/3" in screen[-1]


def test_xdist_workers_never_draw() -> None:
    from m3.pytest_plugin import _Progress

    reporter = _Reporter()
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=0),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
        workerinput={"workerid": "gw0"},
    )
    assert _Progress(config).enabled is False


def test_native_reporter_settings_survive_repeated_disable() -> None:
    reporter = _Reporter()
    progress = _progress(reporter)
    progress.disable_native_progress()
    progress._attach()
    progress.restore_native_progress()
    assert reporter._show_progress_info == "count"
    assert reporter._showfspath is None


def test_piped_xdist_run_keeps_pytest_letters() -> None:
    """Under xdist the main process skips collection_finish; letters must
    still be left alone when output is not a terminal."""

    reporter = _Reporter(isatty=False)
    from m3.pytest_plugin import _Progress

    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=2),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
    )
    progress = _Progress(config)  # enabled before the terminal is checked
    hook = progress.pytest_report_teststatus(report=None, config=None)
    next(hook)
    with pytest.raises(StopIteration) as stop:
        hook.send(("passed", ".", "PASSED"))
    assert stop.value.value == ("passed", ".", "PASSED")
    assert progress.enabled is False
    assert reporter._show_progress_info == "count"


def _colour_reporter(width: int) -> _Reporter:
    reporter = _Reporter()
    reporter._tw = SimpleNamespace(
        fullwidth=width, hasmarkup=True, _file=SimpleNamespace(encoding="utf-8")
    )
    return reporter


@pytest.mark.parametrize(
    ("count", "max_columns", "shape"),
    [
        (3, 40, (1, 3)),
        (12, 40, (1, 12)),  # up to a dozen tests: a single row
        (13, 40, (2, 7)),
        (30, 40, (3, 10)),
        (95, 40, (5, 19)),  # the reported suite: a 5 x 19 grid, none empty
        (240, 40, (6, 40)),
        (70, 12, (6, 12)),  # narrow terminal: fewer columns, more rows
    ],
)
def test_matrix_shape_looks_like_a_matrix(
    count: int, max_columns: int, shape: tuple[int, int]
) -> None:
    from m3.pytest_plugin import _matrix_shape

    assert _matrix_shape(count, max_columns) == shape


@pytest.mark.parametrize(("tests", "width"), [(95, 90), (95, 30), (5000, 90)])
def test_matrix_rows_are_a_spaced_grid_within_the_width(tests: int, width: int) -> None:
    reporter = _colour_reporter(width)
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items(*(f"t.py::t{i}" for i in range(tests))))
    progress.pytest_runtest_logstart("t.py::t0", ("", 1, ""))
    screen = _screen(reporter.written)
    matrix = screen[1:-2]  # blank line, grid, blank line, progress line
    assert screen[0] == "" and screen[-2] == ""
    assert 1 <= len(matrix) <= 6
    assert all(line[2::2].strip("■·") == "" for line in matrix)  # cells
    assert all(set(line[3::2]) <= {" "} for line in matrix)  # spacing
    assert all(len(line) < width for line in screen)


def test_live_block_hides_the_cursor_and_restores_it() -> None:
    reporter = _colour_reporter(80)
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items("a"))
    _run(progress, "a", "passed")
    progress.finish()
    output = "".join(reporter.written)
    assert output.count("\x1b[?25l") == 1
    assert output.rindex("\x1b[?25h") > output.rindex("\x1b[?25l")


def test_timeouts_are_compact_lines_under_the_panel() -> None:
    from m3.pytest_plugin import _write_timeouts

    reporter = _Reporter()
    reporter._tw = SimpleNamespace(fullwidth=80)
    timeouts = [
        ("execution-0bb6b55effb0494a9736a16e0f4bddcf", "unknown", None),
        ("execution-2", "turn", 12.34),
        ("execution-3", "turn", 1.0),
        ("execution-4", "turn", 1.0),
    ]
    _write_timeouts(reporter, Style(False), timeouts)
    assert reporter.lines == [
        "  ⚠ execution timed out  execution-0bb6b55effb04…",
        "  ⚠ execution timed out  execution-2  stage turn  after 12.3s",
        "  ⚠ execution timed out  execution-3  stage turn  after 1.0s",
        "    1 more · details in feedback.json",
    ]
    assert all(visible_len(line) < 80 for line in reporter.lines)


class _Writer:
    fullwidth = 41
    hasmarkup = True

    def __init__(self) -> None:
        self._file = SimpleNamespace(encoding="utf-8")
        self.lines: list[str] = []
        self.native: list[tuple[str, str | None]] = []

    def sep(self, sepchar: str, title: str | None = None, **_markup: bool) -> None:
        self.native.append((sepchar, title))

    def markup(self, text: str, **_markup: bool) -> str:
        return f"<{text}>"

    def line(self, text: str) -> None:
        self.lines.append(text)


def _rules_progress(*, no_header: bool, isatty: bool = True) -> tuple[Any, _Writer]:
    from m3.pytest_plugin import _Progress

    reporter = _Reporter(isatty=isatty)
    reporter._tw = _Writer()
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0, numprocesses=0, no_header=no_header),
        pluginmanager=SimpleNamespace(getplugin=lambda _: reporter),
        getoption=lambda _name, default=None: False,
    )
    progress = _Progress(config)
    progress.reporter = reporter
    progress.restyle_separators()
    return progress, reporter._tw


def test_section_rules_are_thin_and_keep_their_title_markup() -> None:
    _, writer = _rules_progress(no_header=False)
    writer.sep("=", "FAILURES", red=True)
    writer.sep("_", "test_breaks")
    writer.sep("-")
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in writer.lines]
    assert plain == [
        "── <FAILURES> " + "─" * 26,  # every rule fills the 40 columns
        "── test_breaks " + "─" * 25,
        "─" * 40,
    ]
    assert writer.native == []


def test_session_header_is_dropped_after_the_banner_and_rules_restored() -> None:
    progress, writer = _rules_progress(no_header=True)
    writer.sep("=", "test session starts", bold=True)
    assert writer.lines == []
    progress.restore_native_progress()
    writer.sep("=", "after")
    assert writer.native == [("=", "after")]


def test_piped_output_keeps_pytest_rules() -> None:
    _, writer = _rules_progress(no_header=True, isatty=False)
    writer.sep("=", "test session starts")
    assert writer.native == [("=", "test session starts")]


def test_redraws_overwrite_in_place_without_an_empty_frame() -> None:
    reporter = _colour_reporter(80)
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items("t.py::a", "t.py::b", "u.py::c"))
    _run(progress, "t.py::a", "passed")
    _run(progress, "t.py::b", "failed")  # logs a failure line above the block
    _run(progress, "u.py::c", "passed")  # logs the file line, then redraws
    progress.finish()
    frames = [chunk for chunk in reporter.written if "\x1b[?2026h" in chunk]
    assert len(frames) >= 5
    for chunk in frames:
        start = chunk.index("\x1b[?2026h") + len("\x1b[?2026h")
        frame = chunk[start : chunk.index("\x1b[?2026l")]
        # The only erase-below comes last, after the new content is drawn.
        assert frame.count("\x1b[J") == 1
        assert frame.endswith("\x1b[J")
    # And the final screen is still right.
    screen = _screen(reporter.written)
    assert screen[-1].startswith("  ✗ ") and "3/3" in screen[-1]


def test_xfails_are_yellow_cells_file_counts_and_not_regressions() -> None:
    from m3.feedback import Comparison
    from m3.pytest_plugin import _baseline_delta

    reporter = _colour_reporter(80)
    progress = _progress(reporter)
    progress.pytest_collection_finish(_items("t.py::a", "t.py::b"))
    progress.pytest_runtest_logstart("t.py::a", ("", 1, ""))
    xfail = _report("t.py::a", "call", "skipped")
    xfail.wasxfail = ""
    progress.pytest_runtest_logreport(xfail)
    _run(progress, "t.py::b", "passed")
    progress.finish()
    output = "".join(reporter.written)
    assert "1 xfailed" in _screen(reporter.written)[1]  # the file line
    assert Style(True).yellow("■") in output

    comparison = Comparison(
        baseline_run_id="run-base",
        current_run_id="run-1",
        test_changes=(
            {
                "node_id": "a",
                "baseline": [{"outcome": "passed"}],
                "current": [{"outcome": "xfailed"}],
            },
        ),
        coverage={"current_tests": 2},
    )
    assert "regressed" not in _baseline_delta(Style(False), comparison)
