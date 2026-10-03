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
        return ci_upload.PublishResult(None, None)

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


def test_ci_without_upload_never_inspects_or_publishes(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.delenv("M3_ACCESS_TOKEN", raising=False)
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
        lambda *_args: (_ for _ in ()).throw(AssertionError("inspected")),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uploaded")),
    )
    assert main(["ci", "test", "--project-root", str(tmp_path)]) == 0


def test_ci_test_forwards_num_processes(monkeypatch, tmp_path):
    import m3_cli.supervisor as supervisor

    monkeypatch.delenv("M3_ACCESS_TOKEN", raising=False)
    captured = {}

    def run_ci_test(**kwargs):
        captured.update(kwargs)
        return RunResult(
            0,
            run_id="run-local",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        )

    monkeypatch.setattr(supervisor, "run_ci_test", run_ci_test)
    assert main(["ci", "test", "-n", "auto", "--project-root", str(tmp_path)]) == 0
    assert captured["num_processes"] == "auto"


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


def test_ci_requested_upload_stops_when_inspection_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    _inspection_fails(monkeypatch, tmp_path)
    assert main(["ci", "test", "--upload", "--project-root", str(tmp_path)]) == 2
    assert capsys.readouterr().err == (
        "m3 ci: publishing failed: execution exec-1 report is 20 bytes; "
        "limit is 10; fix the cause and rerun tests\n"
    )


def test_ci_upload_inspects_with_mapped_source_names(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    captured = []
    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
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
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: ci_upload.PublishResult(None, None),
    )
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


def test_test_upload_requires_login_before_starting_pytest(
    monkeypatch, tmp_path, capsys
):
    import m3_cli.auth as auth
    import m3_cli.supervisor as supervisor

    for name in ("M3_ACCESS_TOKEN", "CI", "GITHUB_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "load_saved_token", lambda _url: None)
    monkeypatch.setattr(
        supervisor,
        "run_test_with_runs",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pytest started")),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 2
    assert "m3 test: M3 access is required" in capsys.readouterr().err


def test_test_upload_records_inspection_and_publishes_run(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor
    from m3.feedback import build_feedback, export_feedback
    from m3.storage import SQLiteExecutionStore

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    database = tmp_path / "results.sqlite"

    def run_test(**kwargs):
        assert "M3_ACCESS_TOKEN" not in kwargs["environment"]
        store = SQLiteExecutionStore(database)
        try:
            store.ensure_project("2a75f9d8-7dfa-4a30-b594-7526448d19bb", "Example")
            store.save_test_run(
                "run-test",
                {
                    "run_id": "run-test",
                    "status": "finished",
                    "project_id": "2a75f9d8-7dfa-4a30-b594-7526448d19bb",
                },
            )
            feedback = build_feedback(store, "run-test")
            export_feedback(feedback, store, tmp_path / ".m3" / "reports" / "run-test")
        finally:
            store.close()
        return RunResult(
            0,
            run_id="run-test",
            database_path=database,
            project_root=tmp_path,
        )

    uploaded = []
    monkeypatch.setattr(supervisor, "run_test_with_runs", run_test)
    monkeypatch.setattr(
        ci_upload,
        "upload_current_run",
        lambda *args, **kwargs: (
            uploaded.append((args, kwargs)) or ci_upload.PublishResult(None, None)
        ),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 0
    store = SQLiteExecutionStore(database)
    try:
        manifest = store.get_test_run("run-test")
        assert manifest["upload_scan_clean"] is True
        assert len(manifest["upload_scan_digest"]) == 64
    finally:
        store.close()
    assert len(uploaded) == 1
    assert uploaded[0][0][0].run_id == "run-test"


def _stub_test_upload(monkeypatch, tmp_path, captured):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)

    def run_test(**kwargs):
        captured["test"] = kwargs
        return RunResult(
            0,
            run_id="run-test",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        )

    monkeypatch.setattr(supervisor, "run_test_with_runs", run_test)
    monkeypatch.setattr(ci_upload, "record_upload_inspection", lambda *_args: None)


def test_test_upload_in_github_actions_passes_ci_metadata(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload

    captured = {}
    _stub_test_upload(monkeypatch, tmp_path, captured)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: ci_upload.PublishResult(None, None),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 0
    assert captured["test"]["ci_metadata"]["provider"] == "github"
    assert captured["test"]["ci_metadata"]["repository"] == "org/repo"


def test_test_upload_outside_ci_has_no_ci_metadata(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload

    captured = {}
    _stub_test_upload(monkeypatch, tmp_path, captured)
    for name in ("GITHUB_ACTIONS", "CI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: ci_upload.PublishResult(None, None),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 0
    assert captured["test"]["ci_metadata"] is None


def test_upload_prints_hosted_link_and_writes_github_summary(
    monkeypatch, tmp_path, capsys
):
    import m3_cli.ci_upload as ci_upload

    summary = tmp_path / "summary.md"
    _stub_test_upload(monkeypatch, tmp_path, {})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: ci_upload.PublishResult(
            "Run 1b74a51", "https://app.example/reports/runs/x"
        ),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Published: Run 1b74a51\n" in output
    assert "Hosted report: https://app.example/reports/runs/x\n" in output
    assert summary.read_text(encoding="utf-8") == (
        "M3 published [Run 1b74a51](https://app.example/reports/runs/x)\n"
    )


def test_unwritable_github_summary_warns_and_keeps_exit_code(
    monkeypatch, tmp_path, capsys
):
    import m3_cli.ci_upload as ci_upload

    _stub_test_upload(monkeypatch, tmp_path, {})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: ci_upload.PublishResult("Run 1b74a51", None),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 0
    assert "m3 test: could not write the GitHub job summary" in capsys.readouterr().err


def test_test_upload_inspection_failure_keeps_pytest_status_and_skips_upload(
    monkeypatch, tmp_path, capsys
):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(
        supervisor,
        "run_test_with_runs",
        lambda **_kwargs: RunResult(
            1,
            run_id="run-inspection-failure",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "record_upload_inspection",
        lambda *_args: (_ for _ in ()).throw(CLIError("inspection failed")),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uploaded")),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 1
    assert capsys.readouterr().err == (
        "m3 test: publishing failed: inspection failed; fix the cause and rerun tests\n"
    )


def test_test_upload_skips_inspection_and_upload_for_other_pytest_status(
    monkeypatch, tmp_path
):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(
        supervisor,
        "run_test_with_runs",
        lambda **_kwargs: RunResult(
            3,
            run_id="run-early-failure",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "record_upload_inspection",
        lambda *_args: (_ for _ in ()).throw(AssertionError("inspected")),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uploaded")),
    )
    assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 3


def test_test_upload_rejects_ui_combination(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    assert main(["test", "--upload", "--ui", "--project-root", str(tmp_path)]) == 2
    assert "--upload cannot be combined with --ui" in capsys.readouterr().err


def test_upload_refuses_run_without_inspection(monkeypatch, tmp_path, capsys):
    from m3.feedback import build_feedback, export_feedback
    from m3.storage import SQLiteExecutionStore

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    database = tmp_path / "results.sqlite"
    store = SQLiteExecutionStore(database)
    try:
        store.ensure_project("2a75f9d8-7dfa-4a30-b594-7526448d19bb", "Example")
        store.save_test_run(
            "run-uninspected",
            {
                "run_id": "run-uninspected",
                "status": "finished",
                "project_id": "2a75f9d8-7dfa-4a30-b594-7526448d19bb",
            },
        )
        feedback = build_feedback(store, "run-uninspected")
        export_feedback(
            feedback, store, tmp_path / ".m3" / "reports" / "run-uninspected"
        )
    finally:
        store.close()
    assert (
        main(
            [
                "upload",
                "run-uninspected",
                "--project-root",
                str(tmp_path),
                "--results-db",
                str(database),
            ]
        )
        == 2
    )
    assert capsys.readouterr().err == (
        "m3 upload: run run-uninspected cannot be uploaded: it was not started with "
        "--upload, pytest did not exit 0 or 1, or its credential scan failed; rerun "
        "the tests with --upload\n"
    )


def test_timings_summary_includes_upload_steps_after_published(
    monkeypatch, tmp_path, capsys
):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor
    from m3 import _timing
    from m3.feedback import build_feedback, export_feedback
    from m3.storage import SQLiteExecutionStore

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("M3_TIMINGS", "1")
    _timing._reset()
    database = tmp_path / "results.sqlite"
    run_id = "run-timed"

    def run_test(**kwargs):
        assert kwargs["run_id"].startswith("run-")
        store = SQLiteExecutionStore(database)
        try:
            store.ensure_project("2a75f9d8-7dfa-4a30-b594-7526448d19bb", "Example")
            store.save_test_run(
                run_id,
                {
                    "run_id": run_id,
                    "status": "finished",
                    "project_id": "2a75f9d8-7dfa-4a30-b594-7526448d19bb",
                },
            )
            feedback = build_feedback(store, run_id)
            export_feedback(feedback, store, tmp_path / ".m3" / "reports" / run_id)
        finally:
            store.close()
        return RunResult(
            0, run_id=run_id, database_path=database, project_root=tmp_path
        )

    monkeypatch.setattr(supervisor, "run_test_with_runs", run_test)
    monkeypatch.setattr(
        ci_upload,
        "upload_current_run",
        lambda *a, **k: ci_upload.PublishResult(None, None),
    )
    try:
        assert main(["test", "--upload", "--project-root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
    finally:
        _timing.stop()
        monkeypatch.delenv("M3_TIMINGS", raising=False)
        _timing._reset()
        supervisor._timing_state.update(directory=None, run=None)
    assert "Published: Run " in out
    summary = out[out.index("M3 timings:") :]
    assert out.index("Published: Run ") < out.index("M3 timings:")
    assert "cli.upload.inspect" in summary
    assert "cli.upload.publish" in summary
    assert "cli.credentials" in summary
