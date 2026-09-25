from __future__ import annotations

import pytest

from m3_cli.errors import CLIError
from m3_cli.main import main
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
    assert "no saved manifest" not in capsys.readouterr().err


def test_ci_inspection_failure_keeps_local_test_outcome(monkeypatch, tmp_path, capsys):
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
        lambda *_args: (_ for _ in ()).throw(CLIError("inspection unavailable")),
    )
    assert main(["ci", "test", "--project-root", str(tmp_path)]) == 0
    assert "upload inspection unavailable" in capsys.readouterr().err


def test_ci_requested_upload_stops_when_inspection_fails(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
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
        lambda *_args: (_ for _ in ()).throw(CLIError("inspection unavailable")),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uploaded")),
    )
    assert main(["ci", "test", "--upload", "--project-root", str(tmp_path)]) == 2


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


def test_passing_tests_fail_job_when_requested_upload_fails(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(ci_upload, "record_upload_inspection", lambda *_args: None)
    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(
            0,
            run_id="run-failed-upload",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("network failed")

    monkeypatch.setattr(ci_upload, "publish_run", fail)
    assert main(["ci", "test", "--upload", "--project-root", str(tmp_path)]) == 2


def test_failed_test_code_takes_priority_over_upload_failure(monkeypatch, tmp_path):
    import m3_cli.ci_upload as ci_upload
    import m3_cli.supervisor as supervisor

    monkeypatch.setenv("M3_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(ci_upload, "record_upload_inspection", lambda *_args: None)
    monkeypatch.setattr(
        supervisor,
        "run_ci_test",
        lambda **_kwargs: RunResult(
            1,
            run_id="run-failed-test",
            database_path=tmp_path / "results.sqlite",
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        ci_upload,
        "publish_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network failed")),
    )
    assert main(["ci", "test", "--upload", "--project-root", str(tmp_path)]) == 1
