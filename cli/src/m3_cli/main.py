"""Argument parsing and command dispatch for the standalone CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from . import doctor, init, runtime, setup
from .branding import M3_ASCII_ART
from .errors import CLIError
from .server_options import add_server_arguments, normalize_server_groups


class _RedactingArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise CLIError("invalid command or configuration")


def _parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(
        prog="m3",
        description=M3_ASCII_ART,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
            "transport:<stdio|streamable_http>, or storage:<memory|sqlite>"
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

    ci = subparsers.add_parser("ci", help="run tests using CI selection rules")
    ci_subparsers = ci.add_subparsers(
        dest="ci_command", required=True, parser_class=_RedactingArgumentParser
    )
    ci_test = ci_subparsers.add_parser("test", help="run the CI test selection")
    ci_test.add_argument(
        "--upload", action="store_true", help="publish this completed run"
    )
    ci_test.add_argument(
        "--ci-metadata", type=Path, metavar="PATH", help="JSON metadata overrides"
    )

    test = subparsers.add_parser("test", help="run pytest")
    for command in (test, ci_test):
        _add_test_arguments(command, include_ui=command is test)

    upload = subparsers.add_parser("upload", help="publish one saved run")
    upload.add_argument("run_id", metavar="RUN_ID")
    upload.add_argument("--project-root", type=Path, default=None)
    upload.add_argument("--results-db", type=Path, default=None)
    upload.add_argument("--env-file", type=Path, default=None)

    auth = subparsers.add_parser("auth", help="manage M3 access")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_sub.add_parser("login")
    auth_sub.add_parser("status")
    auth_sub.add_parser("logout")

    ui_parser = subparsers.add_parser(
        "ui", help="view saved runs without running tests"
    )
    ui_parser.add_argument(
        "--port", type=int, default=8000, metavar="PORT", help="UI port"
    )

    runtime_parser = subparsers.add_parser(
        "runtime", help="manage managed runtime caches"
    )
    runtime_sub = runtime_parser.add_subparsers(
        dest="runtime_command", required=True, parser_class=_RedactingArgumentParser
    )
    cache_parser = runtime_sub.add_parser("cache", help="manage managed harness cache")
    cache_sub = cache_parser.add_subparsers(
        dest="cache_command", required=True, parser_class=_RedactingArgumentParser
    )
    for action in ("list", "prune"):
        command = cache_sub.add_parser(action, help=f"{action} managed harness cache")
        command.add_argument(
            "--cache-dir",
            "--harness-cache-dir",
            type=Path,
            default=None,
            metavar="PATH",
        )
        command.add_argument("--project-root", type=Path, default=None, metavar="PATH")
    return parser


def _add_test_arguments(test: argparse.ArgumentParser, *, include_ui: bool) -> None:
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
    test.add_argument("--runtime", choices=("system", "managed"), default="system")
    test.add_argument(
        "--harness",
        action="append",
        default=[],
        metavar="KIND[@VERSION]=MODEL[,MODEL...]",
    )
    add_server_arguments(test)
    test.add_argument("--harness-cache-dir", type=Path, default=None, metavar="PATH")
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
    if include_ui:
        test.add_argument(
            "--ui", action="store_true", help="serve the bundled UI after pytest"
        )
        test.add_argument(
            "--port", type=int, default=8000, metavar="PORT", help="UI port"
        )


def _command_error_message(command: str) -> str:
    return f"m3 {command}: invalid command or configuration"


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    command_name = (
        effective_argv[0]
        if effective_argv
        and effective_argv[0]
        in {"doctor", "setup", "test", "ci", "upload", "auth", "ui", "init", "runtime"}
        else "doctor"
    )
    pytest_args: list[str] = []
    try:
        if (
            effective_argv
            and effective_argv[0] in {"test", "ci"}
            and "--" in effective_argv
        ):
            separator = effective_argv.index("--")
            pytest_args = effective_argv[separator + 1 :]
            effective_argv = effective_argv[:separator]
        try:
            args = _parser().parse_args(effective_argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2
        if args.command == "test" or args.command == "ci":
            from .supervisor import run_test

            is_ci = args.command == "ci"

            server_selections = normalize_server_groups(
                getattr(args, "_server_groups", None)
            )
            test_kwargs = dict(
                python=args.python,
                project_root=args.project_root,
                pytest_args=pytest_args,
                database=args.results_db,
                ui=getattr(args, "ui", False),
                port=getattr(args, "port", 8000),
                baseline=args.baseline,
                harnesses=args.harness,
                server_selections=server_selections,
                trials=args.trials,
                suite=args.suite,
                credential_env=args.credential_env,
                env_file=args.env_file,
                execution_timeout=args.execution_timeout,
                judge_max_requests=args.judge_max_requests,
                runtime=args.runtime,
                harness_cache_dir=args.harness_cache_dir,
            )
            if is_ci:
                from .ci_credentials import (
                    access_token,
                    resolved_environment,
                    test_environment,
                    validate_credential_mappings,
                )
                from .ci_metadata import resolve_ci_metadata
                from .ci_upload import control_plane_url, publish_run
                from .supervisor import run_ci_test

                validate_credential_mappings(args.credential_env)
                resolved = resolved_environment(args.env_file)
                if args.upload:
                    access_token(resolved, base_url=control_plane_url(resolved))
                test_kwargs["env_file"] = None
                test_kwargs["environment"] = test_environment(resolved)
                test_kwargs["ci_metadata"] = resolve_ci_metadata(
                    resolved, args.ci_metadata
                )
                result = run_ci_test(**test_kwargs)
                if result.run_id:
                    print(f"Run ID: {result.run_id}")
                    if result.project_root:
                        print(
                            f"Local report: {result.project_root / '.m3' / 'reports' / result.run_id / 'feedback.json'}"
                        )
                if not args.upload or result.exit_code not in (0, 1):
                    return result.exit_code
                if (
                    not result.run_id
                    or not result.database_path
                    or not result.project_root
                ):
                    raise CLIError("the selected run has no saved results to publish")
                try:
                    publish_run(
                        result.run_id,
                        project_root=result.project_root,
                        database=result.database_path,
                        environment=resolved,
                    )
                except (CLIError, RuntimeError, OSError):
                    print(
                        f"m3 ci: publishing failed; retry with m3 upload {result.run_id}",
                        file=sys.stderr,
                    )
                    return result.exit_code or 2
                print("Published: yes")
                return result.exit_code
            return run_test(**test_kwargs)
        if args.command == "upload":
            from .ci_upload import publish_run
            from .supervisor import _absolute_database

            root = (args.project_root or Path.cwd()).resolve()
            database = _absolute_database(args.results_db, project_root=root)
            try:
                publish_run(
                    args.run_id,
                    project_root=root,
                    database=database,
                    env_file=args.env_file,
                )
            except (RuntimeError, OSError):
                print(
                    "m3 upload: publication failed; local results are unchanged",
                    file=sys.stderr,
                )
                return 2
            print(f"Published: {args.run_id}")
            return 0
        if args.command == "auth":
            from . import auth

            if args.auth_command == "login":
                return auth.login()
            if args.auth_command == "status":
                return auth.status()
            return auth.logout()
        if args.command == "ui":
            from .supervisor import run_ui

            return run_ui(port=args.port)
        if args.command == "runtime":
            return runtime.cache_command(
                args.cache_command,
                args.cache_dir,
                project_root=args.project_root,
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
    except CLIError as exc:
        if "--json" in effective_argv:
            print(
                json.dumps(
                    {"ready": False, "error": "invalid command or configuration"}
                )
            )
        else:
            if (
                command_name in {"test", "ci", "upload"}
                and str(exc) != "invalid command or configuration"
            ):
                print(f"m3 {command_name}: {exc}", file=sys.stderr)
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
