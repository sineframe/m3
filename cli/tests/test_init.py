"""Project initialization through the public CLI command."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import tomllib

from mcp_pal_cli import init as init_command
from mcp_pal_cli import main


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
    assert "Initialized MCP Pal project" in output.out
    assert "Project name [" not in output.out
    assert "Suite name [" not in output.out

    identity = tomllib.loads((tmp_path / "mcp-pal.toml").read_text(encoding="utf-8"))
    assert identity["schema_version"] == 1
    assert identity["project_name"] == "Catalog"
    assert str(uuid.UUID(identity["project_id"])) == identity["project_id"]

    starter = tmp_path / "tests" / "test_mcp_pal_starter.py"
    source = starter.read_text(encoding="utf-8")
    assert '@pytest.mark.mcp_pal(suite_name="catalog behavior")' in source
    assert "def test_mcp_behavior() -> None:" in source
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "mcp_pal.pytest_plugin",
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
    config = tmp_path / "mcp-pal.toml"
    starter = tmp_path / "tests" / "test_mcp_pal_starter.py"
    before = (config.read_bytes(), starter.read_bytes())

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
    assert (config.read_bytes(), starter.read_bytes()) == before


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
    source = (tmp_path / "tests" / "test_mcp_pal_starter.py").read_text()
    assert 'suite_name="mcp-behavior"' in source


def test_init_rejects_partial_state_without_overwriting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "tests" / "test_mcp_pal_starter.py"
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
    assert not (tmp_path / "mcp-pal.toml").exists()


def test_init_noninteractive_missing_name_is_usage_error(tmp_path: Path) -> None:
    assert main(["init", "--project-root", str(tmp_path), "--suite", "core"]) == 2
    assert not (tmp_path / "mcp-pal.toml").exists()
