"""Argument parsing and command dispatch for the standalone CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from . import doctor, init, setup
from .errors import CLIError


class _RedactingArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise CLIError("invalid command or configuration")


def _parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(prog="m3")
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
    doctor_parser.add_argument(
        "--python", type=Path, metavar="PATH", help="Python used for project checks"
    )
    doctor_parser.add_argument(
        "--env-file", type=Path, help="explicit dotenv file; cwd .env is never searched"
    )
    doctor_parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable report"
    )

    setup_parser = subparsers.add_parser(
        "setup", help="install the SDK into a project environment"
    )
    setup_parser.add_argument(
        "--project-root", type=Path, help="project root used for environment setup"
    )

    init_parser = subparsers.add_parser(
        "init", help="create a project identity and pytest starter test"
    )
    init_parser.add_argument("--project-root", type=Path, help="project root")
    init_parser.add_argument(
        "--project-name", help="project name (prompts when omitted)"
    )
    init_parser.add_argument(
        "--suite", help="starter suite name (prompts when omitted)"
    )
    setup_parser.add_argument(
        "--python",
        type=Path,
        metavar="PATH",
        help="isolated Python environment to update",
    )

    test = subparsers.add_parser("test", help="run pytest")
    test.add_argument(
        "--python", type=Path, metavar="PATH", help="Python used to run pytest"
    )
    test.add_argument(
        "--project-root", type=Path, help="project root used for pytest and M3 state"
    )
    test.add_argument(
        "--results-db", type=Path, metavar="PATH", help="SQLite history database"
    )
    test.add_argument(
        "--baseline", metavar="RUN_ID", help="compare feedback with a previous run"
    )
    test.add_argument(
        "--harness", action="append", default=[], metavar="KIND=MODEL[,MODEL...]"
    )
    test.add_argument("--trials", type=int, default=None, metavar="N")
    test.add_argument("--suite", type=str, default=None, metavar="NAME")
    test.add_argument(
        "--execution-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="deadline for each selected agent execution",
    )
    test.add_argument("--judge-max-requests", type=int, default=None, metavar="N")
    test.add_argument(
        "--credential-env",
        action="append",
        default=[],
        metavar="[KIND:]TARGET=SOURCE",
    )
    test.add_argument("--env-file", type=Path, default=None, metavar="PATH")
    test.add_argument(
        "--ui", action="store_true", help="serve the bundled UI after pytest"
    )
    test.add_argument("--port", type=int, default=8000, metavar="PORT", help="UI port")
    return parser


def _command_error_message(command: str) -> str:
    return f"m3 {command}: invalid command or configuration"


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    command_name = (
        effective_argv[0]
        if effective_argv and effective_argv[0] in {"doctor", "setup", "test", "init"}
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
                project_root=args.project_root,
                pytest_args=pytest_args,
                database=args.results_db,
                ui=args.ui,
                port=args.port,
                baseline=args.baseline,
                harnesses=args.harness,
                trials=args.trials,
                suite=args.suite,
                credential_env=args.credential_env,
                env_file=args.env_file,
                execution_timeout=args.execution_timeout,
                judge_max_requests=args.judge_max_requests,
            )
        if args.command == "setup":
            try:
                return setup.run(args)
            except setup.SetupError as exc:
                print(f"m3 setup: {exc}", file=sys.stderr)
                return 2
        if args.command == "init":
            return init.run(args)
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
                        "error": doctor._configuration_error_payload(
                            exc.configuration_error
                        ),
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
            print(f"m3 doctor: {exc}", file=sys.stderr)
        return 2
    except doctor.DoctorProjectPythonError as exc:
        if "--json" in effective_argv:
            print(
                json.dumps(
                    {
                        "ready": False,
                        "error": {
                            "code": "project_python_unavailable",
                            "reason": str(exc),
                        },
                    }
                )
            )
        else:
            print(f"m3 doctor: {exc}", file=sys.stderr)
        return 2
    except doctor.DoctorCLIError as exc:
        print(f"m3 doctor: {exc}", file=sys.stderr)
        return 2
    except CLIError:
        if "--json" in effective_argv:
            print(
                json.dumps(
                    {"ready": False, "error": "invalid command or configuration"}
                )
            )
        else:
            print(_command_error_message(command_name), file=sys.stderr)
        return 2
    except Exception:
        if "--json" in effective_argv:
            print(
                json.dumps(
                    {"ready": False, "error": "invalid command or configuration"}
                )
            )
        else:
            print(_command_error_message(command_name), file=sys.stderr)
        return 2


__all__ = ["main"]
