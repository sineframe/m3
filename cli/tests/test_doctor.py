"""Deterministic tests for the stable ``mcp-pal doctor`` command."""

from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path

import pytest

import mcp_pal_cli.doctor as doctor_module
from mcp_pal_cli import main


def test_doctor_without_requirements_checks_config_and_memory(capsys) -> None:
    assert main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is True
    assert report["requirements"] == ["config", "storage:memory"]
    assert report["configuration"]["status"] == "ready"
    assert report["results"][0]["capability"]["status"] == "ready"


def test_doctor_only_probes_explicit_requirements(capsys) -> None:
    assert main(["doctor", "--require", "storage:memory", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["configuration"] is None
    assert [item["capability"]["name"] for item in report["results"]] == ["memory"]


def test_doctor_binary_and_unavailable_exit_codes(capsys) -> None:
    target = f"binary:{sys.executable}"
    assert main(["doctor", "--require", target, "--json"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["results"][0]["capability"]["status"] == "ready"
    assert ready["requirements"] == ["binary"]
    assert ready["project_python"]["executable"]
    assert sys.executable not in json.dumps(ready["results"])

    assert (
        main(["doctor", "--require", "binary:/definitely/missing-secret", "--json"])
        == 1
    )
    unavailable = json.loads(capsys.readouterr().out)
    assert unavailable["ready"] is False
    assert unavailable["results"][0]["capability"]["status"] == "unavailable"
    assert unavailable["requirements"] == ["binary"]
    assert "/definitely/missing-secret" not in json.dumps(unavailable["results"])


def test_doctor_selected_env_file_and_ambient_precedence(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    selected = tmp_path / "selected.env"
    selected.write_text("MCP_PAL_ARTIFACT_POLICY=always\n", encoding="utf-8")
    monkeypatch.setenv("MCP_PAL_ARTIFACT_POLICY", "never")

    assert (
        main(["doctor", "--require", "config", "--env-file", str(selected), "--json"])
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    settings = report["configuration"]["settings"]
    assert settings["artifact_policy"] == "never"
    assert settings["sources"]["artifact_policy"]["source"] == "environment"


def test_doctor_rejects_invalid_requirement_without_echoing_input(capsys) -> None:
    secret = "secret-token-value"
    assert main(["doctor", "--require", secret]) == 2
    captured = capsys.readouterr()
    assert "secret-token-value" not in captured.err


def test_doctor_rejects_unknown_option_without_echoing_input(capsys) -> None:
    secret = "--credential=secret-token-value"
    assert main(["doctor", secret]) == 2
    captured = capsys.readouterr()
    assert "secret-token-value" not in captured.err


@pytest.mark.parametrize(
    ("variable", "value", "field", "origin", "reason"),
    (
        (
            "MCP_PAL_TELEMETRY_ENABLED",
            "secret-not-bool",
            "telemetry_enabled",
            "env:MCP_PAL_TELEMETRY_ENABLED",
            "must be true or false",
        ),
        (
            "MCP_PAL_ARTIFACT_POLICY",
            "secret-policy",
            "artifact_policy",
            "env:MCP_PAL_ARTIFACT_POLICY",
            "must be one of failed, always, or never",
        ),
    ),
)
def test_doctor_configuration_errors_are_actionable_and_value_free(
    variable: str,
    value: str,
    field: str,
    origin: str,
    reason: str,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv(variable, value)
    assert main(["doctor", "--require", "config", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
    assert payload["error"] == {
        "code": "invalid_configuration",
        "field": field,
        "origin": origin,
        "reason": reason,
    }
    assert value not in json.dumps(payload)


def test_doctor_ignores_unknown_prefixed_environment_variables(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MCP_PAL_CLAUDE_MODEL", "claude-sonnet-5")
    assert main(["doctor", "--require", "config", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True


def test_doctor_configuration_error_human_output_has_structured_diagnostic(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MCP_PAL_TELEMETRY_ENABLED", "not-a-secret-bool")
    assert main(["doctor", "--require", "config"]) == 2
    captured = capsys.readouterr()
    assert "code=invalid_configuration" in captured.err
    assert "field=telemetry_enabled" in captured.err
    assert "origin=env:MCP_PAL_TELEMETRY_ENABLED" in captured.err
    assert "reason=must be true or false" in captured.err
    assert "not-a-secret-bool" not in captured.err


def test_doctor_env_file_without_config_fails_before_reading_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def fail_if_read(_path: Path):
        pytest.fail("doctor read an env file without a config requirement")

    monkeypatch.setattr(doctor_module, "_read_selected_environment", fail_if_read)
    assert (
        main(
            [
                "doctor",
                "--require",
                "storage:memory",
                "--env-file",
                str(tmp_path / "secret.env"),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert (
        captured.err.strip()
        == "mcp-pal doctor: --env-file requires a config requirement"
    )


def test_doctor_transport_and_storage_namespaces(capsys) -> None:
    assert (
        main(
            [
                "doctor",
                "--require",
                "transport:stdio",
                "--require",
                "storage:memory",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert [item["capability"]["status"] for item in report["results"]] == [
        "ready",
        "ready",
    ]


def test_doctor_checks_discovered_project_python(capsys) -> None:
    assert main(["doctor", "--python", "./.venv/bin/python", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["project_python"]["status"] == "ready"
    assert report["project_python"]["version"] == metadata.version("mcp-pal")
    assert report["project_python"]["source"] == "--python"
    assert report["project_python"]["executable"].endswith("/.venv/bin/python")


def test_doctor_checks_default_project_python(capsys) -> None:
    assert main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["project_python"]["status"] == "ready"


def test_doctor_project_python_failure_is_actionable_and_safe(
    capsys, tmp_path: Path
) -> None:
    secret_path = tmp_path / "secret-python"
    assert main(["doctor", "--python", str(secret_path), "--json"]) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["error"]["code"] == "project_python_unavailable"
    assert (
        "install" in report["error"]["reason"] or "started" in report["error"]["reason"]
    )
    assert str(secret_path) not in captured.out
    assert str(secret_path) not in captured.err


def test_doctor_reports_unconfigured_project_without_operational_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    assert main(["doctor", "--project-root", str(tmp_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["cli"]["status"] == "ready"
    assert report["project_python"]["status"] == "not ready"
    assert "mcp-pal setup" in report["project_python"]["reason"]


def test_doctor_human_not_ready_has_direct_remediation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    assert main(["doctor", "--project-root", str(tmp_path)]) == 1
    assert "Next: mcp-pal setup" in capsys.readouterr().out
