"""CLI configuration is validated before probing the project interpreter."""

import sys
from pathlib import Path

import pytest

from m3_cli import supervisor
from m3_cli.main import main


@pytest.mark.parametrize("command", [["test"], ["ci", "test"], ["doctor"]])
def test_explicit_invalid_config_fails_before_project_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('[tool.m3.harness_timeouts]\nstartup_seconds="PRIVATE-CANARY"\n')

    def unexpected_probe(*args, **kwargs):
        pytest.fail("invalid configuration reached project Python resolution")

    monkeypatch.setattr(supervisor, "resolve_project_python", unexpected_probe)
    assert (
        main([*command, "--project-root", str(tmp_path), "--m3-config", str(path)]) == 2
    )
    output = capsys.readouterr()
    assert "PRIVATE-CANARY" not in output.out + output.err
    assert "configuration" in output.out + output.err


def test_cli_passes_resolved_settings_after_source_is_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        '[tool.m3]\nartifact_policy="always"\n'
        "[tool.m3.cli]\nproject_python_timeout_seconds=61\n"
    )
    monkeypatch.setattr(
        supervisor,
        "resolve_project_python",
        lambda *args, **kwargs: Path(sys.executable),
    )

    def probe(python, *, project_root, require_xdist, timeout_seconds):
        assert timeout_seconds == 61
        path.unlink()
        return "0.3.1"

    def run_pytest(*args, **kwargs):
        assert not path.exists()
        assert kwargs["configuration"].artifact_policy == "always"
        assert kwargs["configuration"].cli.project_python_timeout_seconds == 61
        assert kwargs["configuration_file"] == path
        return 0

    monkeypatch.setattr(supervisor, "validate_project_python", probe)
    monkeypatch.setattr(supervisor, "_run_pytest_process", run_pytest)
    assert (
        main(["test", "--project-root", str(tmp_path), "--m3-config", str(path)]) == 0
    )


def test_configuration_option_is_reserved_from_pytest_passthrough(capsys) -> None:
    assert main(["test", "--", "--m3-config", "another.toml"]) == 2
    assert "must be set through m3" in capsys.readouterr().err
