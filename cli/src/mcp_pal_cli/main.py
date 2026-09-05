"""Argument parsing and command dispatch for the standalone CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from . import doctor
from . import setup
from .errors import CLIError


class _RedactingArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise CLIError("invalid command or configuration")


def _parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(prog="mcp-pal")
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_RedactingArgumentParser
    )
    doctor_parser = subparsers.add_parser(
        "doctor", help="check explicitly requested SDK capabilities"
    )
    doctor_parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="KIND:TARGET",
        help=(
            "require config, binary:<exe>, harness:<exe>, protocol:<exe>, "
            "transport:<stdio|streamable_http|sse>, or storage:<memory|sqlite>"
        ),
    )
    doctor_parser.add_argument(
        "--project-root", type=Path, help="project root used for pyproject discovery"
    )
    doctor_parser.add_argument("--python", type=Path, metavar="PATH", help="Python used for project checks")
    doctor_parser.add_argument(
        "--env-file", type=Path, help="explicit dotenv file; cwd .env is never searched"
    )
    doctor_parser.add_argument("--json", action="store_true", help="emit a machine-readable report")

    setup_parser = subparsers.add_parser("setup", help="install the SDK into a project environment")
    setup_parser.add_argument("--project-root", type=Path, help="project root used for environment setup")
    setup_parser.add_argument("--python", type=Path, metavar="PATH", help="isolated Python environment to update")

    test = subparsers.add_parser("test", help="run pytest")
    test.add_argument("--python", type=Path, metavar="PATH", help="Python used to run pytest")
    test.add_argument("--results-db", type=Path, metavar="PATH", help="SQLite history database")
    test.add_argument("--ui", action="store_true", help="serve the bundled UI after pytest")
    test.add_argument("--port", type=int, default=8000, metavar="PORT", help="UI port")
    return parser


def _command_error_message(command: str) -> str:
    return f"mcp-pal {command}: invalid command or configuration"


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    command_name = (
        effective_argv[0]
        if effective_argv and effective_argv[0] in {"doctor", "setup", "test"}
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
                python=args.python,
                pytest_args=pytest_args,
                database=args.results_db,
                ui=args.ui,
                port=args.port,
            )
        if args.command == "setup":
            try:
                return setup.run(args)
            except setup.SetupError as exc:
                print(f"mcp-pal setup: {exc}", file=sys.stderr)
                return 2
        code, report = doctor.run(args)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            doctor.print_human(report)
        return code
    except doctor.DoctorConfigurationError as exc:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": doctor._configuration_error_payload(exc.configuration_error)}))
        else:
            doctor.print_configuration_error(exc.configuration_error)
        return 2
    except doctor.DoctorArgumentError as exc:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": str(exc)}))
        else:
            print(f"mcp-pal doctor: {exc}", file=sys.stderr)
        return 2
    except doctor.DoctorProjectPythonError as exc:
        if "--json" in effective_argv:
            print(json.dumps({"ready": False, "error": {"code": "project_python_unavailable", "reason": str(exc)}}))
        else:
            print(f"mcp-pal doctor: {exc}", file=sys.stderr)
        return 2
    except doctor.DoctorCLIError as exc:
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
