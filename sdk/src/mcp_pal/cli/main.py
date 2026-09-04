"""Top-level CLI parser and safe command dispatch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from . import doctor
from .errors import CLIError


class _RedactingArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise CLIError("invalid command or configuration")


def _parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(prog="mcp-pal")
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_RedactingArgumentParser
    )
    doctor = subparsers.add_parser(
        "doctor", help="check explicitly requested SDK capabilities"
    )
    doctor.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="KIND:TARGET",
        help=(
            "require config, binary:<exe>, harness:<exe>, protocol:<exe>, "
            "transport:<stdio|streamable_http|sse>, or storage:<memory|sqlite>"
        ),
    )
    doctor.add_argument(
        "--project-root", type=Path, help="project root used for pyproject discovery"
    )
    doctor.add_argument(
        "--env-file", type=Path, help="explicit dotenv file; cwd .env is never searched"
    )
    doctor.add_argument("--json", action="store_true", help="emit a machine-readable report")
    test = subparsers.add_parser("test", help="run pytest, optionally with the local history UI")
    test.add_argument(
        "--ui",
        action="store_true",
        help="launch the repository-local history viewer after pytest",
    )
    test.add_argument(
        "--results-db",
        "--database-path",
        dest="results_db",
        metavar="PATH",
        help="SQLite history database",
    )
    test.add_argument("--api-port", type=int, default=8000, metavar="PORT")
    test.add_argument("--ui-port", type=int, default=4173, metavar="PORT")
    test.add_argument("--ui-dir", type=Path, metavar="DIR")
    return parser


def _command_error_message(command: str) -> str:
    return f"mcp-pal {command}: invalid command or configuration"


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    command_name = (
        effective_argv[0]
        if effective_argv and effective_argv[0] in {"doctor", "test"}
        else "doctor"
    )
    pytest_args: list[str] = []
    try:
        if effective_argv and effective_argv[0] == "test" and "--" in effective_argv:
            separator = effective_argv.index("--")
            pytest_args = effective_argv[separator + 1 :]
            effective_argv = effective_argv[:separator]
        try:
            args = _parser().parse_args(effective_argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2
        if args.command == "test":
            from .supervisor import run_test

            return run_test(
                pytest_args=pytest_args,
                database=str(args.results_db) if args.results_db is not None else None,
                ui=args.ui,
                api_port=args.api_port,
                ui_port=args.ui_port,
                ui_dir=str(args.ui_dir) if args.ui_dir is not None else None,
            )
        code, report = doctor.run(args)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            doctor.print_human(report)
        return code
    except doctor.DoctorConfigurationError as exc:
        if "--json" in effective_argv:
            print(
                json.dumps(
                    {
                        "ready": False,
                        "error": doctor._configuration_error_payload(exc.configuration_error),
                    }
                )
            )
        else:
            doctor.print_configuration_error(exc.configuration_error)
        return 2
    except doctor.DoctorArgumentError as exc:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": str(exc)}))
        else:
            print(f"mcp-pal doctor: {exc}", file=sys.stderr)
        return 2
    except CLIError:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": "invalid command or configuration"}))
        else:
            print(_command_error_message(command_name), file=sys.stderr)
        return 2
    except Exception:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": "invalid command or configuration"}))
        else:
            print(_command_error_message(command_name), file=sys.stderr)
        return 2


__all__ = ["main"]
