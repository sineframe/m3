from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3.runtime.core import ProgressEmitter
from m3_cli import main, runtime, supervisor


def test_runtime_cache_help_and_invalid_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["runtime", "cache", "list", "--help"]) == 0
    assert "--cache-dir" in capsys.readouterr().out
    assert main(["runtime", "cache", "list", "--bad"]) == 2


def test_runtime_cache_list_uses_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        runtime, "list_cache", lambda root: [{"path": str(root), "kind": "codex"}]
    )
    assert main(["runtime", "cache", "list", "--cache-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["path"] == str(tmp_path)


def test_runtime_cache_prune_and_full_remove(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(runtime, "prune_cache", lambda root: calls.append(root) or [])
    assert main(["runtime", "cache", "prune", "--cache-dir", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    assert "pruned runtime cache" in capsys.readouterr().out


def test_managed_runtime_options_reach_supervisor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    def fake(**kwargs: object) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(supervisor, "run_test", fake)
    assert (
        main(
            [
                "test",
                "--runtime",
                "managed",
                "--harness",
                "codex@2.0.0=model",
                "--harness-cache-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert seen["runtime"] == "managed"
    assert seen["harness_cache_dir"] == tmp_path
    assert seen["harnesses"] == ["codex@2.0.0=model"]


@pytest.mark.parametrize(
    "selector",
    [
        "codex@../x=model",
        "codex@a/b=model",
        "codex@v\uff11=model",
        "codex@1\x00=model",
        "codex@01.2.3=model",
        "codex@1.2.3+build=model",
        "codex@1.2=model",
        "codex@1.2.3-ALPHA=model",
        "codex@" + ("1" * 65) + "=model",
    ],
)
def test_harness_version_rejects_noncanonical_values(selector: str) -> None:
    assert (
        supervisor._validate_selection_options((selector,), None, (), runtime="managed")
        == "--harness has an invalid version"
    )


def test_harness_version_requires_managed_runtime() -> None:
    assert (
        supervisor._validate_selection_options(
            ("codex@1=model",), None, (), runtime="system"
        )
        == "versioned harness selectors require --runtime managed"
    )


def test_harness_version_accepts_lowercase_prerelease() -> None:
    assert (
        supervisor._validate_selection_options(
            ("opencode@1.2.3-beta.1=model",), None, (), runtime="managed"
        )
        is None
    )


def test_managed_runtime_rejects_acp() -> None:
    assert (
        supervisor._validate_selection_options(
            ("acp=model",), None, (), runtime="managed"
        )
        == "ACP does not support --runtime managed"
    )


def test_cache_environment_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("M3_HARNESS_CACHE_DIR", str(tmp_path))
    assert runtime.resolve_cache_root(project_root=Path.cwd()) == tmp_path


def test_cache_list_preserves_corrupt_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        runtime,
        "_sdk_list_cache",
        lambda _root: [
            {"kind": "codex", "version": "1.2.3", "target": "x", "status": "corrupt"}
        ],
    )
    assert runtime.list_cache(tmp_path)[0]["status"] == "corrupt"


def test_cache_invalid_root_returns_operational_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        runtime,
        "_sdk_resolve_cache_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid root")),
    )
    assert main(["runtime", "cache", "list"]) == 2
    assert "invalid root" in capsys.readouterr().err


def test_cache_prune_rejects_active_lease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = tmp_path / "a" / "b" / "c" / "sha256-live"
    entry.mkdir(parents=True)
    (entry / "lease-active").touch()
    monkeypatch.setattr(
        runtime, "_sdk_list_cache", lambda _root: [{"path": str(entry)}]
    )
    monkeypatch.setattr(runtime, "_sdk_prune_cache", lambda _root: [])
    ticks = iter((0.0, 11.0))
    monkeypatch.setattr(
        runtime,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _delay: None),
    )
    assert main(["runtime", "cache", "prune", "--cache-dir", str(tmp_path)]) == 2
    assert "still in use" in capsys.readouterr().err


def test_progress_reader_accepts_runtime_core_event_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_text(
        json.dumps(
            {"event": "acquire_start", "phase": "download", "resolved_version": None}
        )
        + "\n",
        encoding="utf-8",
    )
    supervisor._ProgressStream(path).poll()
    assert capsys.readouterr().out.splitlines() == ["m3: downloading"]


def test_progress_reader_consumes_actual_runtime_core_jsonl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "progress.jsonl"
    emitter = ProgressEmitter(path)
    emitter.emit(
        "acquire_start",
        phase="download",
        kind="opencode",
        resolved_version="1.18.30",
        bytes=42 * 1024 * 1024,
        total_bytes=44 * 1024 * 1024,
    )
    emitter.emit(
        "acquire_complete",
        phase="ready",
        kind="opencode",
        resolved_version="1.18.30",
    )
    supervisor._ProgressStream(path).poll()
    assert capsys.readouterr().out.splitlines() == [
        "m3: OpenCode 1.18.30: downloading 42/44 MB",
        "m3: OpenCode 1.18.30: ready; running test",
    ]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {
                "event": "acquire_start",
                "phase": "download",
                "kind": "opencode",
                "resolved_version": "1.18.30",
            },
            "m3: OpenCode 1.18.30: downloading",
        ),
        (
            {
                "event": "acquire_start",
                "phase": "download",
                "kind": "opencode",
                "resolved_version": "1.18.30",
                "bytes": 42 * 1024 * 1024,
                "total_bytes": 44 * 1024 * 1024,
            },
            "m3: OpenCode 1.18.30: downloading 42/44 MB",
        ),
        (
            {
                "event": "acquire_complete",
                "phase": "ready",
                "kind": "opencode",
                "resolved_version": "1.18.30",
            },
            "m3: OpenCode 1.18.30: ready; running test",
        ),
        (
            {
                "event": "cache_hit",
                "phase": "ready",
                "kind": "opencode",
                "resolved_version": "1.18.30",
            },
            "m3: OpenCode 1.18.30: loaded from cache",
        ),
    ],
)
def test_progress_reader_formats_runtime_events(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    event: dict[str, object],
    expected: str,
) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    supervisor._ProgressStream(path).poll()
    assert capsys.readouterr().out.splitlines() == [expected]


def test_progress_reader_ignores_malformed_and_oversized_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_bytes(
        (b"x" * (supervisor._PROGRESS_LINE_LIMIT + 1)) + b"\n" + b"not-json\n"
    )
    path.open("ab").write(
        json.dumps({"message": "ready", "resolved_version": None}).encode() + b"\n"
    )
    reader = supervisor._ProgressStream(path)
    reader.poll()
    assert capsys.readouterr().out.splitlines() == ["m3: ready"]


def test_progress_reader_bounds_total_input_and_numeric_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_bytes(b"x" * (supervisor._PROGRESS_TOTAL_LIMIT + 1024))
    reader = supervisor._ProgressStream(path)
    for _ in range(20):
        reader.poll()
    assert reader.total == supervisor._PROGRESS_TOTAL_LIMIT
    assert capsys.readouterr().out == ""
    assert (
        supervisor._format_progress_event(
            {
                "event": "download_progress",
                "phase": "download",
                "bytes": 1e309,
                "total_bytes": 1e309,
            }
        )
        == "downloading"
    )


def test_progress_reader_rejects_symlink_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text('{"message":"unexpected"}\n', encoding="utf-8")
    link = tmp_path / "progress.jsonl"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    supervisor._ProgressStream(link).poll()
    assert capsys.readouterr().out == ""


def test_progress_reader_handles_replacement_and_truncation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_text(json.dumps({"message": "first"}) + "\n", encoding="utf-8")
    reader = supervisor._ProgressStream(path)
    reader.poll()
    replacement = tmp_path / "replacement"
    replacement.write_text(json.dumps({"message": "second"}) + "\n", encoding="utf-8")
    os.replace(replacement, path)
    reader.poll()
    path.write_text(json.dumps({"message": "third"}) + "\n", encoding="utf-8")
    reader.poll()
    assert capsys.readouterr().out.splitlines() == [
        "m3: first",
        "m3: second",
        "m3: third",
    ]


def test_managed_subprocess_preserves_stdout_exit_and_progress(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    observed: dict[str, str] = {}

    def command(*_args: object, **_kwargs: object) -> list[str]:
        script = (
            "import os, pathlib, time; "
            "print('pytest stdout', flush=True); "
            "p=pathlib.Path(os.environ['M3_RUNTIME_PROGRESS_FILE']); "
            "p.write_text('{\\\"message\\\":\\\"running\\\"}\\n', encoding='utf-8'); "
            "time.sleep(.3); raise SystemExit(7)"
        )
        return [sys.executable, "-c", script]

    monkey = pytest.MonkeyPatch()
    monkey.setattr(supervisor, "pytest_command", command)
    original_popen = supervisor.subprocess.Popen

    def popen(*args: object, **kwargs: object):
        observed["progress"] = kwargs["env"]["M3_RUNTIME_PROGRESS_FILE"]
        observed["pin"] = kwargs["env"]["M3_INVOCATION_PIN_DIR"]
        return original_popen(*args, **kwargs)

    monkey.setattr(supervisor.subprocess, "Popen", popen)
    try:
        code = supervisor._run_pytest_process(
            Path(sys.executable), tmp_path / "results.sqlite", (), runtime="managed"
        )
    finally:
        monkey.undo()
    assert code == 7
    output = capfd.readouterr().out
    assert "pytest stdout" in output
    assert "m3: running" in output
    assert not Path(observed["progress"]).exists()
    assert not Path(observed["pin"]).exists()
