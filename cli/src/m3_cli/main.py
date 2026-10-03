"""Argument parsing and command dispatch for the standalone CLI."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from . import doctor, init, runtime, setup
from .branding import M3_ASCII_ART
from .errors import CLIError, UploadError
from .server_options import add_server_arguments, normalize_server_groups

if TYPE_CHECKING:
    from .supervisor import TestRunResult


class _RedactingArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise CLIError("invalid command or configuration")


class _VersionAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> NoReturn:
        try:
            version = importlib.metadata.version("sf-m3-cli")
        except importlib.metadata.PackageNotFoundError:
            raise CLIError("the CLI installation is incomplete") from None
        print(f"m3 {version}")
        parser.exit(0)


def _parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(
        prog="m3",
        description=M3_ASCII_ART,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action=_VersionAction,
        nargs=0,
        help="show the installed CLI version",
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
        "--env-file",
        type=Path,
        help="dotenv file; defaults to PROJECT_ROOT/.env when present",
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
    setup_parser.add_argument(
        "--no-skill",
        action="store_true",
        help="do not install or update the testing-with-m3 agent skill",
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
    init_parser.add_argument(
        "--no-skill",
        action="store_true",
        help="do not install or update the testing-with-m3 agent skill",
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

    upload = subparsers.add_parser(
        "upload", help="publish a run started with --upload that was not published"
    )
    upload.add_argument("run_id", metavar="RUN_ID")
    upload.add_argument("--project-root", type=Path, default=None)
    upload.add_argument("--results-db", type=Path, default=None)
    upload.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="dotenv file; defaults to PROJECT_ROOT/.env when present",
    )

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
    ui_parser.add_argument(
        "--project-root", type=Path, metavar="PATH", help="project root"
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
    test.add_argument(
        "-n",
        "--num-processes",
        dest="num_processes",
        default=None,
        metavar="N|auto",
        help="run tests in N pytest-xdist worker processes, or auto for one per CPU",
    )
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
    test.add_argument(
        "--env-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="dotenv file; defaults to PROJECT_ROOT/.env when present",
    )
    if include_ui:
        test.add_argument(
            "--upload", action="store_true", help="publish this completed run"
        )
        test.add_argument(
            "--ui", action="store_true", help="serve the bundled UI after pytest"
        )
        test.add_argument(
            "--port", type=int, default=8000, metavar="PORT", help="UI port"
        )


def _finish_upload_run(
    result: TestRunResult,
    *,
    resolved: dict[str, str],
    credential_env: list[str],
    upload: bool,
    command_name: str,
) -> int:
    run_id = result.run_id
    database = result.database_path
    root = result.project_root
    if run_id and database and root:
        report_path = root / ".m3" / "reports" / run_id / "feedback.json"
        if (
            any(run.run_id == run_id for run in result.new_runs)
            and report_path.is_file()
            and not report_path.is_symlink()
        ):
            print(f"Run ID: {run_id}")
            print(f"Local report: {report_path}")
    if not upload or result.exit_code not in (0, 1):
        return result.exit_code
    if not run_id or not database or not root:
        raise CLIError("the selected run has no saved results to publish")
    from .ci_upload import publish_run, record_upload_inspection

    try:
        record_upload_inspection(database, run_id, root, credential_env, resolved)
    except Exception as exc:
        print(_inspection_failure(command_name, exc), file=sys.stderr)
        return result.exit_code or 2
    try:
        publish_run(run_id, project_root=root, database=database, environment=resolved)
    except Exception as exc:
        print(
            _publish_failure(
                command_name,
                run_id,
                exc,
                f"m3 {command_name}: publishing failed; retry with m3 upload {run_id}",
            ),
            file=sys.stderr,
        )
        return result.exit_code or 2
    print("Published: yes")
    return result.exit_code


def _command_error_message(command: str) -> str:
    return f"m3 {command}: invalid command or configuration"


def _failure_reason(exc: Exception) -> str | None:
    """Return a printable failure reason, or ``None`` if none is safe to print.

    CLIError and UploadError messages are built only from trusted parts. For
    any other OSError only the OS description is used; its filename may not be
    trusted. Every other message may carry untrusted values.
    """
    if isinstance(exc, (CLIError, UploadError)):
        return str(exc)
    if isinstance(exc, OSError) and isinstance(exc.strerror, str) and exc.strerror:
        return exc.strerror
    return None


def _publish_failure(command: str, run_id: str, exc: Exception, fallback: str) -> str:
    reason = _failure_reason(exc)
    if reason is None:
        return fallback
    if isinstance(exc, UploadError) and exc.retryable:
        return (
            f"m3 {command}: publishing failed: {reason}; retry with m3 upload {run_id}"
        )
    return f"m3 {command}: publishing failed: {reason}; fix the cause and rerun tests"


def _inspection_failure(command: str, exc: Exception) -> str:
    """Describe a failed upload inspection; it never succeeds on a plain retry."""
    reason = _failure_reason(exc)
    if reason is None:
        return (
            f"m3 {command}: upload inspection unavailable; the run was not "
            "published. Rerun the tests with --upload"
        )
    return f"m3 {command}: publishing failed: {reason}; fix the cause and rerun tests"


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
            from .ci_credentials import validate_credential_mappings
            from .supervisor import _passthrough_option_error, run_test

            validate_credential_mappings(args.credential_env)

            passthrough_error = _passthrough_option_error(pytest_args)
            if passthrough_error is not None:
                print(f"m3 {args.command}: {passthrough_error}", file=sys.stderr)
                return 2

            is_ci = args.command == "ci"

            if args.command == "test" and args.upload and args.ui:
                print("m3 test: --upload cannot be combined with --ui", file=sys.stderr)
                return 2

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
                num_processes=args.num_processes,
                suite=args.suite,
                credential_env=args.credential_env,
                env_file=args.env_file,
                execution_timeout=args.execution_timeout,
                judge_max_requests=args.judge_max_requests,
                runtime=args.runtime,
                harness_cache_dir=args.harness_cache_dir,
            )
            if is_ci or args.upload:
                from .ci_credentials import (
                    ACCESS_TOKEN_ENV,
                    access_token,
                    resolved_environment,
                    test_environment,
                )
                from .ci_upload import control_plane_url
                from .supervisor import (
                    discover_env_file,
                    resolve_project_root,
                    run_test_with_runs,
                )

                resolved = resolved_environment(
                    discover_env_file(
                        args.env_file, resolve_project_root(args.project_root)
                    )
                )
                if args.upload:
                    resolved[ACCESS_TOKEN_ENV] = access_token(
                        resolved, base_url=control_plane_url(resolved)
                    )
                test_kwargs["env_file"] = None
                test_kwargs["environment"] = test_environment(resolved)
                if is_ci:
                    from .ci_metadata import resolve_ci_metadata
                    from .supervisor import run_ci_test

                    test_kwargs["ci_metadata"] = resolve_ci_metadata(
                        resolved, args.ci_metadata
                    )
                    result = run_ci_test(**test_kwargs)
                else:
                    result = run_test_with_runs(**test_kwargs)
                return _finish_upload_run(
                    result,
                    resolved=resolved,
                    credential_env=args.credential_env,
                    upload=args.upload,
                    command_name="ci" if is_ci else "test",
                )
            return run_test(**test_kwargs)
        if args.command == "upload":
            from .ci_upload import publish_run
            from .supervisor import (
                _absolute_database,
                discover_env_file,
                resolve_project_root,
            )

            root = resolve_project_root(args.project_root)
            database = _absolute_database(args.results_db, project_root=root)
            try:
                publish_run(
                    args.run_id,
                    project_root=root,
                    database=database,
                    env_file=discover_env_file(args.env_file, root),
                )
            except (RuntimeError, OSError) as exc:
                print(
                    _publish_failure(
                        "upload",
                        args.run_id,
                        exc,
                        "m3 upload: publication failed; local results are unchanged",
                    ),
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

            return run_ui(port=args.port, project_root=args.project_root)
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
