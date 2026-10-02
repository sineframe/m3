from __future__ import annotations

from datetime import datetime, timezone

import pytest

from m3_cli.errors import CLIError, UploadError
from m3_cli.main import main
from m3_cli.supervisor import StoredRun
from m3_cli.supervisor import TestRunResult as RunResult

TOKEN = "m3pat_" + "A" * 22 + "." + "A" * 43


@pytest.mark.parametrize(
    "passthrough",
    [
        ["--m3-run-id", "forced"],
        ["--m3-run-id=forced"],
        ["--m3-ci-metadata", "{}"],
        ["--results-db=other.sqlite"],
        ["--project-root", "/tmp/other"],
        ["--credential-env", "VENDOR_KEY=UNSCANNED_CRED"],
        ["--m3-ci"],
    ],
)
def test_cli_rejects_owned_pytest_options_before_running(
    monkeypatch, capsys, passthrough
):
    import m3_cli.supervisor as supervisor

    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("tests started")),
    )
    assert main(["ci", "test", "--", "-q", *passthrough]) == 2
    assert "pytest passthrough" in capsys.readouterr().err


def test_cli_rejects_pytest_response_file_before_running(monkeypatch, tmp_path, capsys):
    import m3_cli.supervisor as supervisor

    response_file = tmp_path / "pytest-options.txt"
    response_file.write_text("--m3-run-id=forced-response-id\n", encoding="utf-8")
    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("tests started")),
    )
    assert main(["ci", "test", "--", f"@{response_file}"]) == 2
    assert "response files are not supported" in capsys.readouterr().err


def test_ci_upload_publishes_exact_run_and_strips_access_token(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("OPENAI_API_KEY", "agent-value")
    monkeypatch.setenv("DEPLOY_CRED", "opaque-deployment-credential")
    captured = {}
    monkeypatch.setattr(
        ci_upload,
        "record_upload_inspection",
        lambda *_args: None,
    )

    def run_ci_test(**kwargs):
        captured["test"] = kwargs
        return RunResult(
            0,
            run_id="run-exact",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        )

    def publish(run_id, **kwargs):
        captured["upload"] = (run_id, kwargs)

    monkeypatch.setattr(supervisor, "run_ci_test", run_ci_test)
    monkeypatch.setattr(ci_upload, "publish_run", publish)
    assert (
        main(
            [
                "ci",
                "test",
                "--upload",
                "--credential-env",
                "codex:VENDOR_API_KEY=DEPLOY_CRED",
                "--project-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert captured["upload"][0] == "run-exact"
    assert captured["test"]["environment"]["OPENAI_API_KEY"] == "agent-value"
    assert "M3_ACCESS_TOKEN" not in captured["test"]["environment"]
    assert captured["upload"][1]["environment"]["M3_ACCESS_TOKEN"] == TOKEN
    assert "credential_env" not in captured["upload"][1]


def test_ci_without_upload_never_calls_publisher(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.delenv("M3_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(0, run_id="run-local", project_root=tmp_path),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uploaded")),
    )
    assert main(["ci", "test", "--project-root", str(tmp_path)]) == 0


def test_ci_early_failure_does_not_attempt_upload_inspection(
    monkeypatch, tmp_path, capsys
):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(
            2,
            run_id="run-unprepared",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "record_upload_inspection",
        lambda *_args: (_ for _ in ()).throw(AssertionError("inspected")),
    )
    assert main(["ci", "test", "--project-root", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "no saved manifest" not in output.err
    assert "Run ID:" not in output.out
    assert "Local report:" not in output.out


def test_ci_only_advertises_a_saved_report(monkeypatch, tmp_path, capsys):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    run_id = "run-saved"
    saved = StoredRun(run_id, datetime(2026, 9, 25, tzinfo=timezone.utc))
    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(
            1,
            new_runs=(saved,),
            run_id=run_id,
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(ci_upload, "record_upload_inspection", lambda *_args: None)
    assert main(["ci", "test", "--project-root", str(tmp_path)]) == 1
    assert "Run ID:" not in capsys.readouterr().out

    report = tmp_path / ".m3" / "reports" / run_id / "feedback.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    assert main(["ci", "test", "--project-root", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert f"Run ID: {run_id}" in output
    assert f"Local report: {report}" in output


def test_ci_missing_project_python_does_not_advertise_report(tmp_path, capsys):
    assert (
        main(
            [
                "ci",
                "test",
                "--project-root",
                str(tmp_path),
                "--python",
                str(tmp_path / "missing-python"),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert "Run ID:" not in output.out
    assert "Local report:" not in output.out


def _inspection_fails(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(
            0,
            run_id="run-local",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "record_upload_inspection",
        lambda *_args: (_ for _ in ()).throw(
            UploadError(
                "execution exec-1 report is 20 bytes; limit is 10", retryable=False
            )
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uploaded")),
    )


def test_ci_inspection_failure_keeps_local_test_outcome(monkeypatch, tmp_path, capsys):
    _inspection_fails(monkeypatch, tmp_path)
    assert main(["ci", "test", "--project-root", str(tmp_path)]) == 0
    assert capsys.readouterr().err == (
        "m3 ci: upload inspection failed: execution exec-1 report is 20 bytes; "
        "limit is 10; local test result is unchanged\n"
    )


def test_ci_requested_upload_stops_when_inspection_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    _inspection_fails(monkeypatch, tmp_path)
    assert main(["ci", "test", "--upload", "--project-root", str(tmp_path)]) == 2
    assert capsys.readouterr().err == (
        "m3 ci: publishing failed: execution exec-1 report is 20 bytes; "
        "limit is 10; fix the cause and rerun tests\n"
    )


def test_ci_without_upload_persists_mapped_source_names(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    captured = []
    monkeypatch.setenv("DEPLOY_CRED", "opaque-deployment-credential")
    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(
            0,
            run_id="run-local",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "record_upload_inspection",
        lambda *args: captured.append(args),
    )
    assert (
        main(
            [
                "ci",
                "test",
                "--credential-env",
                "codex:VENDOR_API_KEY=DEPLOY_CRED",
                "--project-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert len(captured) == 1
    assert captured[0][:4] == (
        tmp_path / "results.sqlite",
        "run-local",
        tmp_path,
        ["codex:VENDOR_API_KEY=DEPLOY_CRED"],
    )
    assert captured[0][4]["DEPLOY_CRED"] == "opaque-deployment-credential"


def _publish_fails(monkeypatch, tmp_path, exit_code, error):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(ci_upload, "record_upload_inspection", lambda *_args: None)
    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(
            exit_code,
            run_id="run-1",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            UploadError("could not reach the M3 server", retryable=True),
            "publishing failed: could not reach the M3 server; "
            "retry with m3 upload run-1",
        ),
        (
            UploadError("the M3 server rejected it (HTTP 413)", retryable=False),
            "publishing failed: the M3 server rejected it (HTTP 413); "
            "fix the cause and rerun tests",
        ),
        (
            CLIError("run run-1 was not scanned for credentials"),
            "publishing failed: run run-1 was not scanned for credentials; "
            "fix the cause and rerun tests",
        ),
        (
            PermissionError(13, "Permission denied", "/secret-path/cache.json"),
            "publishing failed: Permission denied; fix the cause and rerun tests",
        ),
        (
            RuntimeError("untrusted-value"),
            "publishing failed; retry with m3 upload run-1",
        ),
        (
            ValueError("untrusted-value"),
            "publishing failed; retry with m3 upload run-1",
        ),
    ],
)
def test_ci_publish_failure_reports_trusted_reason_once(
    monkeypatch, tmp_path, capsys, error, message
):
    _publish_fails(monkeypatch, tmp_path, 0, error)
    assert main(["ci", "test", "--upload", "--project-root", str(tmp_path)]) == 2
    assert capsys.readouterr().err == f"m3 ci: {message}\n"


@pytest.mark.parametrize(
    "error",
    [UploadError("rejected", retryable=False), ValueError("corrupt cache")],
)
def test_failed_test_code_takes_priority_over_upload_failure(
    monkeypatch, tmp_path, error
):
    _publish_fails(monkeypatch, tmp_path, 1, error)
    assert main(["ci", "test", "--upload", "--project-root", str(tmp_path)]) == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            UploadError("could not reach the M3 server", retryable=True),
            "m3 upload: publishing failed: could not reach the M3 server; "
            "retry with m3 upload run-1",
        ),
        (
            UploadError("run run-1 results changed", retryable=False),
            "m3 upload: publishing failed: run run-1 results changed; "
            "fix the cause and rerun tests",
        ),
        (
            RuntimeError("untrusted-value"),
            "m3 upload: publication failed; local results are unchanged",
        ),
    ],
)
def test_upload_command_reports_trusted_reason_once(
    monkeypatch, tmp_path, capsys, error, message
):
    import m3_cli.ci_upload as ci_upload

    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    assert main(["upload", "run-1", "--project-root", str(tmp_path)]) == 2
    assert capsys.readouterr().err == message + "\n"
