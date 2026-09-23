from __future__ import annotations

import json
from pathlib import Path

import pytest

from m3_cli.main import _parser, main
from m3_cli.server_options import normalize_server_groups
from m3_cli.supervisor import pytest_command


def test_grouped_server_arguments_keep_group_and_argument_order() -> None:
    args = _parser().parse_args(
        [
            "test",
            "--server",
            "http",
            "--url",
            "https://one.example/mcp",
            "--trust",
            "public",
            "--server",
            "stdio",
            "--command",
            "python",
            "--arg=-m",
            "--arg",
            "module",
        ]
    )

    assert normalize_server_groups(args._server_groups) == [
        {
            "type": "http",
            "url": "https://one.example/mcp",
            "name": "server",
            "trust": "public",
        },
        {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "module"],
            "name": "server",
        },
    ]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["--server", "http"],
            "--server http requires --url with an http:// or https:// MCP endpoint",
        ),
        (
            ["--server", "http", "--url", "file:///tmp/mcp"],
            "--server http requires --url with an http:// or https:// MCP endpoint",
        ),
        (["--server", "stdio"], "--server stdio requires a non-empty --command"),
        (
            ["--server", "stdio", "--command", "python", "--trust", "public"],
            "--server stdio accepts --command, --arg, and --name only",
        ),
        (
            ["--server", "http", "--url", "http://example.test", "--command", "python"],
            "--server http accepts --url, --name, and --trust only",
        ),
        (
            [
                "--server",
                "http",
                "--url",
                "http://one.example/mcp",
                "--url",
                "http://two.example/mcp",
            ],
            "--url may appear only once per --server group",
        ),
        (
            [
                "--server",
                "http",
                "--url",
                "http://example.test",
                "--trust",
                "sdk_loopback",
            ],
            "--trust must be untrusted, public, or trusted_private",
        ),
        (
            [
                "--server",
                "stdio",
                "--command",
                "python",
                "--arg=-m",
                "--server",
                "stdio",
                "--command",
                "python",
                "--arg=-m",
            ],
            "duplicate --server group",
        ),
    ],
)
def test_group_validation_errors(arguments: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("--", r"\-\-")):
        args = _parser().parse_args(["test", *arguments])
        normalize_server_groups(args._server_groups)


def test_server_field_before_group_is_rejected() -> None:
    with pytest.raises(ValueError, match="preceding --server group"):
        _parser().parse_args(["test", "--url", "http://example.test"])


def test_invalid_http_server_group_has_specific_cli_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["test", "--server", "http"]) == 2
    assert capsys.readouterr().err.strip() == (
        "m3 test: --server http requires --url with an http:// or https:// MCP endpoint"
    )


def test_pytest_command_forwards_server_selection_as_one_json_value(
    tmp_path: Path,
) -> None:
    entries = [
        {"type": "http", "url": "http://127.0.0.1:8000/mcp", "name": "local"},
        {"type": "stdio", "command": "python", "args": ["-m", "srv"], "name": "pipe"},
    ]
    command = pytest_command(
        Path("/project/.venv/bin/python"),
        tmp_path / "results.sqlite",
        [],
        server_selections=entries,
    )

    index = command.index("--m3-server-selections")
    assert json.loads(command[index + 1]) == entries
    assert command.count("--m3-server-selections") == 1
