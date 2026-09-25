"""Version reporting for the standalone CLI."""

from importlib import metadata

from m3_cli import main


def test_version_prints_installed_cli_version(capsys) -> None:
    assert main(["--version"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"m3 {metadata.version('sf-m3-cli')}\n"
    assert captured.err == ""


def test_version_is_listed_in_help(capsys) -> None:
    assert main(["--help"]) == 0
    assert "--version" in capsys.readouterr().out
