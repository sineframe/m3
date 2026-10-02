from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from m3_cli import agent_skill

_DEVELOPMENT_CHECK = agent_skill._is_development_install

VERSION = "1.2.3"
SKILL_PATH = Path(".agents/skills/testing-with-m3/SKILL.md")


@pytest.fixture(autouse=True)
def _not_development_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_skill, "_is_development_install", lambda: False)


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    returncode: int = 0,
    stdout: str | bytes = "",
    stderr: str | bytes = "",
    timeout: bool = False,
) -> list[dict[str, Any]]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for marker in agent_skill._CI_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.delenv("DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_skill.shutil, "which", lambda _name: "/x/npx")

    class FakeProcess:
        pid = 123

        def __init__(self) -> None:
            self.returncode = returncode
            self.communications = 0

        def communicate(
            self, *, timeout: int | None = None
        ) -> tuple[str | bytes, str | bytes]:
            self.communications += 1
            if timeout and self.communications == 1 and timeout_mode:
                raise subprocess.TimeoutExpired(
                    "npx", timeout, output=output, stderr=error
                )
            return output, error

    timeout_mode = timeout
    output = stdout
    error = stderr

    def popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess()

    monkeypatch.setattr(agent_skill.subprocess, "Popen", popen)
    return calls


def _lock(root: Path, ref: str = "v1.2.3", source: str = "sineframe/m3") -> None:
    (root / "skills-lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "testing-with-m3": {
                        "source": source,
                        "ref": ref,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_install_command_is_release_pinned() -> None:
    assert agent_skill.install_command(VERSION) == [
        "npx",
        "--yes",
        "skills@1.7.0",
        "add",
        "sineframe/m3#v1.2.3",
        "--skill",
        "testing-with-m3",
        "--agent",
        "universal",
        "claude-code",
        "-y",
    ]


def test_nothing_installed_runs_pinned_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert len(calls) == 1
    assert calls[0]["command"] == [
        "/x/npx",
        "--yes",
        "skills@1.7.0",
        "add",
        "sineframe/m3#v1.2.3",
        "--skill",
        "testing-with-m3",
        "--agent",
        "universal",
        "claude-code",
        "-y",
    ]
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["env"]["DISABLE_TELEMETRY"] == "1"
    assert calls[0]["env"]["DO_NOT_TRACK"] == "1"
    assert calls[0]["stdin"] == subprocess.DEVNULL
    assert calls[0]["stdout"] == subprocess.PIPE
    assert calls[0]["stderr"] == subprocess.PIPE
    assert calls[0]["text"] is True
    assert calls[0]["start_new_session"] is True
    assert (
        "Agent skill: installed testing-with-m3 for M3 1.2.3" in capsys.readouterr().out
    )


def test_matching_managed_install_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    _lock(tmp_path)
    skill = tmp_path / SKILL_PATH
    skill.parent.mkdir(parents=True)
    skill.write_text("skill", encoding="utf-8")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert calls == []
    assert "matches M3 1.2.3" in capsys.readouterr().out


def test_different_managed_release_is_updated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    _lock(tmp_path, "v1.2.2")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert len(calls) == 1
    assert (
        "Agent skill: updated testing-with-m3 for M3 1.2.3" in capsys.readouterr().out
    )


def test_managed_install_with_deleted_project_files_is_installed_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    _lock(tmp_path)

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert len(calls) == 1


def test_unmanaged_project_copy_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    skill = tmp_path / ".claude/skills/testing-with-m3/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("skill", encoding="utf-8")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert calls == []
    assert f"using existing testing-with-m3 at {skill}" in output
    assert "To use the copy for this M3 release" in output


def test_fork_lock_does_not_manage_existing_project_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    _lock(tmp_path, source="sineframe/m3-fork")
    skill = tmp_path / SKILL_PATH
    skill.parent.mkdir(parents=True)
    skill.write_text("skill", encoding="utf-8")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert calls == []
    assert f"using existing testing-with-m3 at {skill}" in capsys.readouterr().out


def test_home_copy_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    home_skill = tmp_path / "home" / SKILL_PATH
    home_skill.parent.mkdir(parents=True)
    home_skill.write_text("skill", encoding="utf-8")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert calls == []


@pytest.mark.parametrize("marker", ["JENKINS_URL", "TF_BUILD"])
def test_additional_ci_markers_skip_install(
    marker: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv(marker, "1")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert calls == []
    assert "Agent skill: skipped in CI" in capsys.readouterr().out


def test_missing_npx_prints_manual_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_skill.shutil, "which", lambda _name: None)

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert "Agent skill: npx not found. Install Node.js, then run:" in output
    assert (
        "  npx --yes skills@1.7.0 add sineframe/m3#v1.2.3 --skill "
        "testing-with-m3 --agent universal claude-code -y" in output
    )


def test_ci_skips_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("CI", "1")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert calls == []
    assert "Agent skill: skipped in CI" in capsys.readouterr().out


def test_development_install_skips_with_manual_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    distribution = type(
        "Distribution",
        (),
        {
            "read_text": lambda self, filename: (
                '{"dir_info": {"editable": true}}'
                if filename == "direct_url.json"
                else None
            )
        },
    )()
    monkeypatch.setattr(
        agent_skill.importlib.metadata,
        "distribution",
        lambda name: distribution if name == "sf-m3-cli" else None,
    )
    monkeypatch.setattr(agent_skill, "_is_development_install", _DEVELOPMENT_CHECK)

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert calls == []
    assert (
        "Agent skill: skipped for a development install of M3. To install it, run:"
        in output
    )
    assert "  npx --yes skills@1.7.0 add sineframe/m3#v1.2.3" in output


def test_disabled_skill_install_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=False)

    assert calls == []
    assert "Agent skill: skipped (--no-skill)" in capsys.readouterr().out


def test_corrupt_lock_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    (tmp_path / "skills-lock.json").write_text("{", encoding="utf-8")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert len(calls) == 1


def test_failed_install_prints_last_stderr_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(
        monkeypatch,
        tmp_path,
        returncode=1,
        stderr="fatal: Remote branch v1.2.3 not found\n",
    )

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert "Agent skill: could not install testing-with-m3. Run:" in output
    assert "  npx: fatal: Remote branch v1.2.3 not found" in output


def test_failure_hint_uses_stdout_when_stderr_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(monkeypatch, tmp_path, returncode=1, stdout="first\nstdout failure\n")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert "  npx: stdout failure" in capsys.readouterr().out


def test_failure_hint_prefers_decorated_error_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    decorated = (
        "\x1b[1G│  fatal: Remote branch v1.2.3 not found in upstream origin\n"
        "│\n"
        "└  Installation failed\n"
        "\x1b[1G\x1b[J■  Canceled\n"
    )
    _prepare(monkeypatch, tmp_path, returncode=1, stdout=decorated)

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert (
        "  npx: fatal: Remote branch v1.2.3 not found in upstream origin"
        in capsys.readouterr().out
    )


def test_install_failure_never_raises_on_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(
        agent_skill.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("npx unavailable")),
    )

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert (
        "Agent skill: could not install testing-with-m3. Run:"
        in capsys.readouterr().out
    )


def test_install_timeout_decodes_output_kills_process_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _prepare(
        monkeypatch,
        tmp_path,
        returncode=-9,
        stdout=b"old\n",
        stderr=b"failure: timed out\xff\n",
        timeout=True,
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        agent_skill.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert len(calls) == 1
    assert killed == [(123, agent_skill.signal.SIGKILL)]
    assert "Agent skill: could not install testing-with-m3. Run:" in output
    assert "  npx: failure: timed out�" in output


def test_keyboard_interrupt_kills_and_waits_for_install_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch, tmp_path)
    killed: list[Any] = []
    waited: list[bool] = []

    class InterruptedProcess:
        pid = 123

        def communicate(self, *, timeout: int | None = None) -> tuple[str, str]:
            raise KeyboardInterrupt

        def wait(self) -> None:
            waited.append(True)

    process = InterruptedProcess()
    monkeypatch.setattr(
        agent_skill.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(
        agent_skill, "_kill_process", lambda child: killed.append(child)
    )

    with pytest.raises(KeyboardInterrupt):
        agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert killed == [process]
    assert waited == [True]


def test_managed_lock_with_non_string_source_is_treated_as_unmanaged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    (tmp_path / "skills-lock.json").write_text(
        json.dumps({"skills": {"testing-with-m3": {"source": None}}})
    )
    (tmp_path / SKILL_PATH).parent.mkdir(parents=True)
    (tmp_path / SKILL_PATH).write_text("skill")
    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)
    assert calls == []
