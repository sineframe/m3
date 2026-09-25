from __future__ import annotations

import json

import pytest

from m3_cli.ci_credentials import (
    access_token,
    resolved_environment,
    validate_credential_mappings,
)
from m3_cli.ci_credentials import test_environment as child_environment
from m3_cli.ci_metadata import resolve_ci_metadata
from m3_cli.errors import CLIError

TOKEN = "m3pat_" + "A" * 22 + "." + "A" * 43


def test_ci_credentials_from_file_keep_harness_and_judge_separate(tmp_path):
    path = tmp_path / "ci.env"
    path.write_text(
        "OPENAI_API_KEY=file-agent\nM3_JUDGE_API_KEY=file-judge\n"
        f"M3_ACCESS_TOKEN={TOKEN}\nLITERAL=${{OPENAI_API_KEY}}\n",
        encoding="utf-8",
    )
    resolved = resolved_environment(path, source={"OPENAI_API_KEY": "ambient-agent"})
    assert resolved["OPENAI_API_KEY"] == "ambient-agent"
    assert resolved["M3_JUDGE_API_KEY"] == "file-judge"
    assert resolved["LITERAL"] == "${OPENAI_API_KEY}"
    assert access_token(resolved, base_url="https://example.com") == TOKEN
    child = child_environment(resolved)
    assert "M3_ACCESS_TOKEN" not in child
    assert child["M3_JUDGE_API_KEY"] == "file-judge"


def test_ci_token_missing_and_empty_are_explicit():
    with pytest.raises(CLIError, match="required in CI"):
        access_token({"CI": "true"}, base_url="https://example.com")
    with pytest.raises(CLIError, match="is empty"):
        access_token(
            {"CI": "true", "M3_ACCESS_TOKEN": ""}, base_url="https://example.com"
        )


def test_upload_token_cannot_be_mapped_to_test_credentials():
    for mapping in (
        "OPENAI_API_KEY=M3_ACCESS_TOKEN",
        "judge:M3_ACCESS_TOKEN=MY_KEY",
    ):
        with pytest.raises(CLIError, match="cannot be mapped"):
            validate_credential_mappings([mapping])


def test_cached_upload_cannot_be_retargeted(tmp_path, monkeypatch):
    from m3.feedback import build_feedback, export_feedback
    from m3.storage import SQLiteExecutionStore
    from m3_cli.control_plane import upload_current_run

    store = SQLiteExecutionStore(tmp_path / "results.sqlite")
    try:
        feedback = build_feedback(store, "run-test")
        directory = tmp_path / "reports" / "run-test"
        export_feedback(feedback, store, directory)
        import m3_cli.control_plane as control_plane

        monkeypatch.setattr(control_plane, "_post", lambda *_args: None)
        upload_current_run(
            feedback,
            store,
            directory,
            base_url="https://one.example",
            token="m3pat_test",
        )
        assert (
            json.loads((directory / "control-plane" / "destination.json").read_text())[
                "base_url"
            ]
            == "https://one.example"
        )
        with pytest.raises(RuntimeError, match="another destination"):
            upload_current_run(
                feedback,
                store,
                directory,
                base_url="https://two.example",
                token="m3pat_test",
            )
    finally:
        store.close()


def test_ci_metadata_uses_allowlist_and_explicit_overrides(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text('{"job":"shard-2","pr_number":42}', encoding="utf-8")
    values = resolve_ci_metadata(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "123",
            "GITHUB_JOB": "shard-1",
            "UNRELATED_SECRET": "not-for-feedback",
        },
        path,
    )
    assert values["job"] == "shard-2"
    assert values["pr_number"] == "42"
    assert values["job_url"] == "https://github.com/org/repo/actions/runs/123"
    assert "UNRELATED_SECRET" not in values
    path.write_text('{"token":"secret"}', encoding="utf-8")
    with pytest.raises(CLIError, match="unsupported fields"):
        resolve_ci_metadata({}, path)


def test_upload_rejects_path_like_run_id_before_opening_store(tmp_path):
    from m3_cli.ci_upload import publish_run

    with pytest.raises(CLIError, match="invalid run ID"):
        publish_run(
            "../outside",
            project_root=tmp_path,
            database=tmp_path / "results.sqlite",
            environment={"M3_ACCESS_TOKEN": "m3pat_test"},
        )


def test_explicit_saved_run_publishes_only_after_finalization(tmp_path, monkeypatch):
    from m3.feedback import build_feedback, export_feedback
    from m3.storage import SQLiteExecutionStore
    from m3_cli import ci_upload

    root = tmp_path
    database = root / "results.sqlite"
    store = SQLiteExecutionStore(database)
    try:
        store.ensure_project("2a75f9d8-7dfa-4a30-b594-7526448d19bb", "Example")
        store.save_test_run(
            "run-test",
            {
                "run_id": "run-test",
                "status": "running",
                "project_id": "2a75f9d8-7dfa-4a30-b594-7526448d19bb",
            },
        )
        feedback = build_feedback(store, "run-test")
        export_feedback(feedback, store, root / ".m3" / "reports" / "run-test")
        sent = []
        monkeypatch.setattr(
            ci_upload,
            "upload_current_run",
            lambda *args, **kwargs: sent.append((args, kwargs)),
        )
        kwargs = {
            "project_root": root,
            "database": database,
            "environment": {"M3_ACCESS_TOKEN": TOKEN},
        }
        with pytest.raises(CLIError, match="incomplete"):
            ci_upload.publish_run("run-test", **kwargs)
        assert not sent
        record = dict(store.get_test_run("run-test") or {})
        record["status"] = "finished"
        store.save_test_run("run-test", record)
        ci_upload.publish_run("run-test", **kwargs)
        assert len(sent) == 1
        assert sent[0][0][0].run_id == "run-test"
    finally:
        store.close()
