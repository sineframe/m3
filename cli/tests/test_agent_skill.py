from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3_cli import agent_skill

VERSION = "1.2.3"
SKILL_PATH = Path(".agents/skills/testing-with-m3/SKILL.md")


def _prepare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[dict[str, object]]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for marker in agent_skill._CI_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(agent_skill.shutil, "which", lambda _name: "/x/npx")

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(agent_skill.subprocess, "run", run)
    return calls


def _lock(root: Path, ref: str = "v1.2.3") -> None:
    (root / "skills-lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "testing-with-m3": {
                        "source": "sineframe/m3",
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
        "skills",
        "add",
        "sineframe/m3#v1.2.3",
        "--skill",
        "testing-with-m3",
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
        "skills",
        "add",
        "sineframe/m3#v1.2.3",
        "--skill",
        "testing-with-m3",
        "-y",
    ]
    assert calls[0]["cwd"] == tmp_path
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
    assert (
        "To use the copy for this M3 release, run: npx --yes skills add sineframe/m3#v1.2.3 --skill testing-with-m3 -y"
        in output
    )


def test_home_copy_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    home_skill = tmp_path / "home" / SKILL_PATH
    home_skill.parent.mkdir(parents=True)
    home_skill.write_text("skill", encoding="utf-8")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert calls == []


def test_missing_npx_prints_manual_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_skill.shutil, "which", lambda _name: None)

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert "Agent skill: npx not found. Install Node.js, then run:" in output
    assert (
        "  npx --yes skills add sineframe/m3#v1.2.3 --skill testing-with-m3 -y"
        in output
    )


def test_failed_install_prints_last_stderr_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(monkeypatch, tmp_path)

    def fail(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=1, stderr="fatal: Remote branch v1.2.3 not found\n"
        )

    monkeypatch.setattr(agent_skill.subprocess, "run", fail)
    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert "Agent skill: could not install testing-with-m3. Run:" in output
    assert "  npx: fatal: Remote branch v1.2.3 not found" in output


def test_ci_skips_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("CI", "1")

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert calls == []
    assert "Agent skill: skipped in CI" in capsys.readouterr().out


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


def test_install_failure_never_raises_on_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(
        agent_skill.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("npx unavailable")),
    )

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    assert (
        "Agent skill: could not install testing-with-m3. Run:"
        in capsys.readouterr().out
    )


def test_install_timeout_never_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(
        agent_skill.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("npx", 180, stderr="network timeout\n")
        ),
    )

    agent_skill.ensure_agent_skill(tmp_path, VERSION, enabled=True)

    output = capsys.readouterr().out
    assert "Agent skill: could not install testing-with-m3. Run:" in output
    assert "  npx: network timeout" in output


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
