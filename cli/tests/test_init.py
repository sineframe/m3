"""Project initialization through the public CLI command."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import tomllib

from m3_cli import init as init_command
from m3_cli import main


def test_init_creates_project_identity_and_one_collectable_starter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "init",
                "--project-root",
                str(tmp_path),
                "--project-name",
                "Catalog",
                "--suite",
                "catalog behavior",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert "Initialized M3 project" in output.out
    assert "Project name [" not in output.out
    assert "Suite name [" not in output.out

    identity = tomllib.loads((tmp_path / "m3.toml").read_text(encoding="utf-8"))
    assert identity["schema_version"] == 1
    assert identity["project_name"] == "Catalog"
    assert str(uuid.UUID(identity["project_id"])) == identity["project_id"]

    template = (tmp_path / ".env.example").read_text(encoding="utf-8")
    for name in (
        "OPENCODE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "M3_JUDGE_API_KEY",
    ):
        assert f"{name}=\n" in template
    assert "--env-file .env" in output.out
    assert not (tmp_path / ".env").exists()

    starter = tmp_path / "tests" / "test_m3_starter.py"
    source = starter.read_text(encoding="utf-8")
    assert '@pytest.mark.m3(suite_name="catalog behavior")' in source
    assert "def test_mcp_behavior() -> None:" in source
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            str(starter),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout


def test_init_rerun_preserves_files_and_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    initial = [
        "init",
        "--project-root",
        str(tmp_path),
        "--project-name",
        "Catalog",
        "--suite",
        "core",
    ]
    assert main(initial) == 0
    capsys.readouterr()
    config = tmp_path / "m3.toml"
    starter = tmp_path / "tests" / "test_m3_starter.py"
    env_example = tmp_path / ".env.example"
    env_example.write_text("MY_EXISTING_KEY=\n", encoding="utf-8")
    before = (config.read_bytes(), starter.read_bytes(), env_example.read_bytes())

    assert (
        main(
            [
                "init",
                "--project-root",
                str(tmp_path),
                "--project-name",
                "Different",
                "--suite",
                "other",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "already initialized" in output
    assert "supplied names were not applied" in output
    assert (
        config.read_bytes(),
        starter.read_bytes(),
        env_example.read_bytes(),
    ) == before


def test_init_rerun_adds_template_to_older_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command = [
        "init",
        "--project-root",
        str(tmp_path),
        "--project-name",
        "Catalog",
        "--suite",
        "core",
    ]
    assert main(command) == 0
    capsys.readouterr()
    config = tmp_path / "m3.toml"
    starter = tmp_path / "tests" / "test_m3_starter.py"
    before = (config.read_bytes(), starter.read_bytes())
    (tmp_path / ".env.example").unlink()

    assert main(command) == 0
    output = capsys.readouterr().out
    assert "already initialized" in output
    assert "Created" in output and ".env.example" in output
    assert "M3_JUDGE_API_KEY=\n" in (tmp_path / ".env.example").read_text()
    assert (config.read_bytes(), starter.read_bytes()) == before


def test_init_preserves_existing_env_example(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_example = tmp_path / ".env.example"
    env_example.write_text("PROJECT_KEY=keep-me\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("PROJECT_KEY=private-value\n", encoding="utf-8")
    assert (
        main(
            [
                "init",
                "--project-root",
                str(tmp_path),
                "--project-name",
                "Catalog",
                "--suite",
                "core",
            ]
        )
        == 0
    )
    assert env_example.read_text(encoding="utf-8") == "PROJECT_KEY=keep-me\n"
    assert env_file.read_text(encoding="utf-8") == "PROJECT_KEY=private-value\n"
    assert "Created" in capsys.readouterr().out
    assert (tmp_path / "m3.toml").is_file()


def test_init_prompts_for_missing_fields_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(init_command.sys.stdin, "isatty", lambda: True)

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", answer)
    assert main(["init", "--project-root", str(tmp_path)]) == 0
    assert prompts == [
        f"Project name [{tmp_path.name}]: ",
        "Suite name [mcp-behavior]: ",
    ]
    source = (tmp_path / "tests" / "test_m3_starter.py").read_text()
    assert 'suite_name="mcp-behavior"' in source


def test_init_rejects_partial_state_without_overwriting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "tests" / "test_m3_starter.py"
    existing.parent.mkdir()
    existing.write_text("existing test\n", encoding="utf-8")
    assert (
        main(
            [
                "init",
                "--project-root",
                str(tmp_path),
                "--project-name",
                "Catalog",
                "--suite",
                "core",
            ]
        )
        == 2
    )
    assert "partial initialization" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "existing test\n"
    assert not (tmp_path / "m3.toml").exists()
    assert not (tmp_path / ".env.example").exists()


def test_init_noninteractive_missing_name_is_usage_error(tmp_path: Path) -> None:
    assert main(["init", "--project-root", str(tmp_path), "--suite", "core"]) == 2
    assert not (tmp_path / "m3.toml").exists()
