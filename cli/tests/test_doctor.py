"""Deterministic tests for the stable ``m3 doctor`` command."""

from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path

import pytest

import m3_cli.doctor as doctor_module
from m3_cli import main


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
    selected.write_text("M3_ARTIFACT_POLICY=always\n", encoding="utf-8")
    monkeypatch.setenv("M3_ARTIFACT_POLICY", "never")

    assert (
        main(["doctor", "--require", "config", "--env-file", str(selected), "--json"])
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    settings = report["configuration"]["settings"]
    assert settings["artifact_policy"] == "never"
    assert settings["sources"]["artifact_policy"]["source"] == "environment"


def test_doctor_discovers_project_root_env(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / ".env").write_text("M3_ARTIFACT_POLICY=always\n", encoding="utf-8")
    monkeypatch.delenv("M3_ARTIFACT_POLICY", raising=False)

    assert (
        main(
            [
                "doctor",
                "--require",
                "config",
                "--project-root",
                str(tmp_path),
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["configuration"]["settings"]["artifact_policy"] == "always"


def test_doctor_reads_config_from_the_invocation_directory(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Implicit-root test runs keep the invocation cwd, so the SDK reads the
    # nearest pyproject.toml from there; doctor must validate the same file.
    (tmp_path / "m3.toml").write_text("", encoding="utf-8")
    package = tmp_path / "packages" / "server"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text(
        '[tool.m3]\nartifact_policy = "sometimes"\n', encoding="utf-8"
    )
    monkeypatch.delenv("M3_ARTIFACT_POLICY", raising=False)
    monkeypatch.chdir(package)

    assert main(["doctor", "--require", "config", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["field"] == "artifact_policy"


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
            "M3_PROTOCOL_REVISION",
            "secret revision with spaces",
            "protocol_revision",
            "env:M3_PROTOCOL_REVISION",
            "must be auto or a non-empty protocol revision identifier",
        ),
        (
            "M3_ARTIFACT_POLICY",
            "secret-policy",
            "artifact_policy",
            "env:M3_ARTIFACT_POLICY",
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
    monkeypatch.setenv("M3_CLAUDE_MODEL", "claude-sonnet-5")
    assert main(["doctor", "--require", "config", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True


def test_doctor_configuration_error_human_output_has_structured_diagnostic(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("M3_ARTIFACT_POLICY", "not-a-secret-policy")
    assert main(["doctor", "--require", "config"]) == 2
    captured = capsys.readouterr()
    assert "code=invalid_configuration" in captured.err
    assert "field=artifact_policy" in captured.err
    assert "origin=env:M3_ARTIFACT_POLICY" in captured.err
    assert "reason=must be one of failed, always, or never" in captured.err
    assert "not-a-secret-policy" not in captured.err


def test_doctor_ignores_removed_telemetry_environment_variable(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("M3_TELEMETRY_ENABLED", "true")
    assert main(["doctor", "--require", "config", "--json"]) == 0
    settings = json.loads(capsys.readouterr().out)["configuration"]["settings"]
    assert "telemetry_enabled" not in settings
    assert "telemetry_enabled" not in settings["sources"]


def test_doctor_rejects_removed_telemetry_project_setting(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("M3_TELEMETRY_ENABLED", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3]\ntelemetry_enabled = true\n", encoding="utf-8"
    )
    assert (
        main(
            [
                "doctor",
                "--require",
                "config",
                "--project-root",
                str(tmp_path),
                "--json",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "unknown_setting"
    assert payload["error"]["field"] == "telemetry_enabled"


def test_doctor_rejects_removed_telemetry_config_requirement(capsys) -> None:
    assert main(["doctor", "--require", "config:telemetry_enabled"]) == 2


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
        == "m3 doctor: --env-file requires a config or harness requirement"
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
    assert report["project_python"]["version"] == metadata.version("sf-m3")
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
    assert "m3 setup" in report["project_python"]["reason"]


def test_doctor_human_not_ready_has_direct_remediation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    assert main(["doctor", "--project-root", str(tmp_path)]) == 1
    assert "Next: m3 setup" in capsys.readouterr().out


def test_doctor_m3_config_reports_settings_origin_and_digest(
    tmp_path: Path, capsys
) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[tool.m3.results]\nbusy_timeout_seconds = 30\n", encoding="utf-8")
    assert main(
        ["doctor", "--require", "config", "--m3-config", str(cfg), "--json"]
    ) in {0, 1}
    report = json.loads(capsys.readouterr().out)
    settings = report["configuration"]["settings"]
    assert settings["results"]["busy_timeout_seconds"] == 30.0
    assert (
        settings["sources"]["results.busy_timeout_seconds"]["origin"]
        == f"config-file:{cfg}"
    )
    assert report["configuration"]["digest"].startswith("sha256:")


def test_doctor_m3_config_missing_file(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.toml"
    assert (
        main(["doctor", "--require", "config", "--m3-config", str(missing), "--json"])
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
    assert payload["error"]["code"] == "invalid_project"
    assert payload["error"]["field"] == "config_file"


def _capture_project_python_timeout(monkeypatch) -> list[float]:
    import m3_cli.supervisor as supervisor

    timeouts: list[float] = []

    def fake(*_args, timeout_seconds: float, **_kwargs) -> str:
        timeouts.append(timeout_seconds)
        return "3.13.0"

    monkeypatch.setattr(supervisor, "validate_project_python", fake)
    return timeouts


def test_doctor_uses_cli_timeout_without_config_requirement(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.cli]\nproject_python_timeout_seconds = 60\n", encoding="utf-8"
    )
    timeouts = _capture_project_python_timeout(monkeypatch)
    assert (
        main(
            [
                "doctor",
                "--require",
                "storage:memory",
                "--project-root",
                str(tmp_path),
                "--python",
                sys.executable,
                "--json",
            ]
        )
        == 0
    )
    assert timeouts == [60.0]
    assert json.loads(capsys.readouterr().out)["configuration"] is None


def test_doctor_m3_config_is_honored_without_config_requirement(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[tool.m3.cli]\nproject_python_timeout_seconds = 45\n", encoding="utf-8"
    )
    timeouts = _capture_project_python_timeout(monkeypatch)
    assert (
        main(
            [
                "doctor",
                "--require",
                "storage:memory",
                "--project-root",
                str(tmp_path),
                "--python",
                sys.executable,
                "--m3-config",
                str(cfg),
                "--json",
            ]
        )
        == 0
    )
    assert timeouts == [45.0]
    capsys.readouterr()


def test_doctor_invalid_config_fails_without_config_requirement(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.cli]\nproject_python_timeout_seconds = 0\n", encoding="utf-8"
    )
    assert (
        main(
            [
                "doctor",
                "--require",
                "storage:memory",
                "--project-root",
                str(tmp_path),
                "--json",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "invalid_configuration"


_CREDENTIAL_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENCODE_API_KEY",
    "PI_CODING_AGENT_DIR",
    "MY_CLAUDE_TOKEN",
)


def _harness_executable(directory: Path, name: str) -> str:
    """A stand-in harness executable that only answers ``--version``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!{sys.executable}\nprint('1.2.3')\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def no_credentials(tmp_path: Path, monkeypatch) -> Path:
    """An environment with no agent credential and an empty Codex home."""
    for name in _CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    # An empty explicit env file keeps a developer's project .env out of it.
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    return tmp_path


def _doctor_harness(tmp_path: Path, name: str, *extra: str) -> list[str]:
    executable = _harness_executable(tmp_path / "bin", name)
    arguments = ["doctor", "--require", f"harness:{executable}", "--json"]
    if "--env-file" not in extra:
        arguments += ["--env-file", str(tmp_path / "empty.env")]
    return [*arguments, *extra]


def test_doctor_claude_without_credential_is_not_ready(
    no_credentials: Path, capsys
) -> None:
    assert main(_doctor_harness(no_credentials, "claude")) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False
    result = report["results"][0]
    assert result["capability"]["status"] == "unavailable"
    assert result["capability"]["reason"] == "no credential for Claude Code"
    # The executable itself still probed successfully.
    assert result["evidence"]["details"]["returncode"] == 0
    credentials = result["credentials"]
    assert credentials["status"] == "missing"
    assert credentials["harness"] == "claude_code"
    assert credentials["sources"] == [
        {
            "name": "ANTHROPIC_API_KEY",
            "kind": "environment",
            "used_for": "any model",
            "status": "not set",
        },
        {"name": "Claude Code login", "kind": "login", "status": "not used"},
    ]
    assert "ANTHROPIC_API_KEY" in credentials["fix"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" in credentials["fix"]


def test_doctor_claude_human_output_names_the_fix(no_credentials: Path, capsys) -> None:
    arguments = [
        item for item in _doctor_harness(no_credentials, "claude") if item != "--json"
    ]
    assert main(arguments) == 1
    output = capsys.readouterr().out
    assert "m3 doctor: not ready" in output
    assert "harness: unavailable (no credential for Claude Code)" in output
    assert "ANTHROPIC_API_KEY: not set" in output
    assert "Claude Code login: not used" in output
    assert "Next: set ANTHROPIC_API_KEY, or export a token" in output
    assert "claude_code:CLAUDE_CODE_OAUTH_TOKEN=MY_CLAUDE_TOKEN" in output
    assert "Next: m3 doctor" not in output


def test_doctor_claude_with_api_key_is_ready_and_value_free(
    no_credentials: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary-value")
    assert main(_doctor_harness(no_credentials, "claude")) == 0
    output = capsys.readouterr().out
    assert "credential-canary-value" not in output
    report = json.loads(output)
    assert report["ready"] is True
    credentials = report["results"][0]["credentials"]
    assert credentials["status"] == "ready"
    assert credentials["sources"][0]["status"] == "set"
    assert credentials["sources"][0]["origin"] == "environment"


def test_doctor_claude_empty_api_key_is_not_a_credential(
    no_credentials: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert main(_doctor_harness(no_credentials, "claude")) == 1
    credentials = json.loads(capsys.readouterr().out)["results"][0]["credentials"]
    assert credentials["status"] == "missing"
    assert credentials["sources"][0]["status"] == "empty"


def test_doctor_claude_mapped_token_is_ready_and_value_free(
    no_credentials: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MY_CLAUDE_TOKEN", "credential-canary-value")
    mapping = "claude:CLAUDE_CODE_OAUTH_TOKEN=MY_CLAUDE_TOKEN"
    assert (
        main(_doctor_harness(no_credentials, "claude", "--credential-env", mapping))
        == 0
    )
    output = capsys.readouterr().out
    assert "credential-canary-value" not in output
    assert "MY_CLAUDE_TOKEN" not in output
    credentials = json.loads(output)["results"][0]["credentials"]
    assert credentials["status"] == "ready"
    assert credentials["sources"] == [
        {
            "name": "CLAUDE_CODE_OAUTH_TOKEN",
            "kind": "mapped",
            "status": "set",
            "origin": "environment",
        }
    ]


def test_doctor_mapping_replaces_the_default_credential(
    no_credentials: Path, monkeypatch, capsys
) -> None:
    # An explicit mapping is the complete set, so a set ANTHROPIC_API_KEY does
    # not make up for an unset mapped source.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary-value")
    mapping = "claude_code:CLAUDE_CODE_OAUTH_TOKEN=MY_CLAUDE_TOKEN"
    assert (
        main(_doctor_harness(no_credentials, "claude", "--credential-env", mapping))
        == 1
    )
    credentials = json.loads(capsys.readouterr().out)["results"][0]["credentials"]
    assert credentials["status"] == "missing"
    assert credentials["reason"] == "1 mapped credential source is not set or empty"
    assert credentials["sources"][0]["status"] == "not set"


def test_doctor_mapping_for_another_harness_is_ignored(
    no_credentials: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary-value")
    mapping = "codex:OPENAI_API_KEY=MY_CLAUDE_TOKEN"
    assert (
        main(_doctor_harness(no_credentials, "claude", "--credential-env", mapping))
        == 0
    )
    credentials = json.loads(capsys.readouterr().out)["results"][0]["credentials"]
    assert credentials["sources"][0]["kind"] == "environment"


def test_doctor_reads_harness_credential_from_env_file(
    no_credentials: Path, capsys
) -> None:
    env_file = no_credentials / "agent.env"
    env_file.write_text("ANTHROPIC_API_KEY=credential-canary-value\n", encoding="utf-8")
    arguments = _doctor_harness(no_credentials, "claude", "--env-file", str(env_file))
    assert main(arguments) == 0
    output = capsys.readouterr().out
    assert "credential-canary-value" not in output
    source = json.loads(output)["results"][0]["credentials"]["sources"][0]
    assert source["status"] == "set"
    assert source["origin"] == "env_file"

    assert main([item for item in arguments if item != "--json"]) == 0
    human = capsys.readouterr().out
    assert "credential-canary-value" not in human
    assert "ANTHROPIC_API_KEY: set (from the env file" in human


def test_doctor_empty_ambient_credential_overrides_env_file(
    no_credentials: Path, monkeypatch, capsys
) -> None:
    env_file = no_credentials / "agent.env"
    env_file.write_text("ANTHROPIC_API_KEY=credential-canary-value\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    arguments = _doctor_harness(no_credentials, "claude", "--env-file", str(env_file))
    assert main(arguments) == 1
    credentials = json.loads(capsys.readouterr().out)["results"][0]["credentials"]
    assert credentials["sources"][0]["status"] == "empty"


def test_doctor_codex_without_key_or_login_is_not_ready(
    no_credentials: Path, capsys
) -> None:
    assert main(_doctor_harness(no_credentials, "codex")) == 1
    credentials = json.loads(capsys.readouterr().out)["results"][0]["credentials"]
    assert credentials["status"] == "missing"
    assert credentials["reason"] == "no credential for Codex"
    assert [(item["name"], item["status"]) for item in credentials["sources"]] == [
        ("OPENAI_API_KEY", "not set"),
        ("Codex login file", "not found"),
    ]
    assert "codex login" in credentials["fix"]


def test_doctor_codex_login_file_is_ready_without_reading_it(
    no_credentials: Path, capsys
) -> None:
    (no_credentials / "codex-home" / "auth.json").write_text(
        "credential-canary-value", encoding="utf-8"
    )
    assert main(_doctor_harness(no_credentials, "codex")) == 0
    output = capsys.readouterr().out
    assert "credential-canary-value" not in output
    credentials = json.loads(output)["results"][0]["credentials"]
    assert credentials["status"] == "ready"
    assert credentials["sources"][-1] == {
        "name": "Codex login file",
        "kind": "login",
        "status": "present",
    }


def test_doctor_opencode_credentials_depend_on_the_model(
    no_credentials: Path, monkeypatch, capsys
) -> None:
    assert main(_doctor_harness(no_credentials, "opencode")) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is True
    credentials = report["results"][0]["credentials"]
    assert credentials["status"] == "not checked"
    assert credentials["reason"].endswith("none is set")
    assert [item["name"] for item in credentials["sources"]] == [
        "OPENCODE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ]

    monkeypatch.setenv("OPENAI_API_KEY", "credential-canary-value")
    assert main(_doctor_harness(no_credentials, "opencode")) == 0
    output = capsys.readouterr().out
    assert "credential-canary-value" not in output
    credentials = json.loads(output)["results"][0]["credentials"]
    assert credentials["status"] == "not checked"
    assert credentials["reason"].endswith("set: OPENAI_API_KEY")


def test_doctor_unknown_harness_executable_credentials_are_not_checked(
    no_credentials: Path, capsys
) -> None:
    assert main(_doctor_harness(no_credentials, "my-agent")) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is True
    credentials = report["results"][0]["credentials"]
    assert credentials["status"] == "not checked"
    assert credentials["harness"] is None


def test_doctor_non_harness_results_have_no_credentials(capsys) -> None:
    assert main(["doctor", "--require", f"binary:{sys.executable}", "--json"]) == 0
    assert "credentials" not in json.loads(capsys.readouterr().out)["results"][0]


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ("sk-secret-canary", "--credential-env requires TARGET=SOURCE"),
        (
            "CLAUDE_CODE_OAUTH_TOKEN=sk-secret-canary",
            "credential environment names must be Python identifiers",
        ),
        ("nope:TARGET=sk_secret_canary", "unknown credential scope"),
        (
            "M3_ACCESS_TOKEN=sk_secret_canary",
            "M3_ACCESS_TOKEN cannot be mapped to a test credential",
        ),
    ],
)
def test_doctor_rejects_credential_mapping_without_echoing_input(
    no_credentials: Path, capsys, mapping: str, message: str
) -> None:
    arguments = _doctor_harness(no_credentials, "claude", "--credential-env", mapping)
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert "canary" not in captured.out + captured.err
    assert message in captured.out + captured.err


def test_doctor_credential_env_requires_a_harness_requirement(capsys) -> None:
    assert (
        main(
            [
                "doctor",
                "--require",
                "storage:memory",
                "--credential-env",
                "OPENAI_API_KEY=MY_OPENAI_KEY",
            ]
        )
        == 2
    )
    assert (
        capsys.readouterr().err.strip()
        == "m3 doctor: --credential-env requires a harness requirement"
    )
