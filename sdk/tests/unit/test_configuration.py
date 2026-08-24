"""Pure SDK configuration contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_pal.config import Settings
from mcp_pal.configuration import (
    ConfigSource,
    ConfigurationError,
    SDKConfig,
    resolve_config,
)


def test_defaults_are_frozen_and_value_only() -> None:
    config = resolve_config(env={}, cwd=Path("/tmp/mcp-pal-no-project"))

    assert config.artifact_policy == "failed"
    assert config.protocol_revision == "auto"
    assert config.telemetry_enabled is False
    assert all(info.source is ConfigSource.DEFAULT for info in config.sources.values())
    with pytest.raises((TypeError, ValueError)):
        config.telemetry_enabled = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        config.sources["telemetry_enabled"] = config.sources["telemetry_enabled"]  # type: ignore[index]


def test_direct_construction_infers_truthful_provenance() -> None:
    config = SDKConfig(artifact_policy="always", protocol_revision="2025-06-18", telemetry_enabled=True)

    assert all(info.source is ConfigSource.EXPLICIT for info in config.sources.values())
    assert config.source_for("artifact_policy").origin == "argument:artifact_policy"
    assert SDKConfig().source_for("artifact_policy").source is ConfigSource.DEFAULT


@pytest.mark.parametrize(
    "kwargs",
    [
        {"artifact_policy": "sometimes"},
        {"protocol_revision": "revision with spaces"},
        {"telemetry_enabled": 1},
    ],
)
def test_direct_construction_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SDKConfig(**kwargs)


def test_direct_construction_rejects_incomplete_or_untruthful_provenance() -> None:
    defaults = SDKConfig().sources
    incomplete = dict(defaults)
    incomplete.pop("telemetry_enabled")
    with pytest.raises(ValidationError):
        SDKConfig(sources=incomplete)
    with pytest.raises(ValidationError):
        SDKConfig(artifact_policy="always", sources=defaults)


def test_all_four_precedence_levels_and_origins(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcp-pal]\nartifact_policy = "always"\nprotocol_revision = "2025-06-18"\ntelemetry_enabled = true\n',
        encoding="utf-8",
    )
    env = {
        "MCP_PAL_ARTIFACT_POLICY": "never",
        "MCP_PAL_PROTOCOL_REVISION": "2025-03-26",
    }

    config = resolve_config(
        {"artifact_policy": "failed"},
        env=env,
        cwd=tmp_path,
        telemetry_enabled=False,
    )

    assert config.model_dump(mode="json")["sources"]
    assert config.artifact_policy == "failed"
    assert config.protocol_revision == "2025-03-26"
    assert config.telemetry_enabled is False
    assert config.source_for("artifact_policy").source is ConfigSource.EXPLICIT
    assert config.source_for("protocol_revision").origin == "env:MCP_PAL_PROTOCOL_REVISION"
    assert config.source_for("telemetry_enabled").origin == "argument:telemetry_enabled"


def test_project_config_uses_nearest_pyproject_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcp-pal]\nartifact_policy = "always"\n', encoding="utf-8"
    )
    child = tmp_path / "child"
    child.mkdir()
    (child / "pyproject.toml").write_text("[project]\nname = 'child'\n", encoding="utf-8")

    config = resolve_config(env={}, cwd=child)

    assert config.artifact_policy == "failed"
    assert config.source_for("artifact_policy").source is ConfigSource.DEFAULT


def test_supplied_environment_isolated_from_ambient_and_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "MCP_PAL_ARTIFACT_POLICY=always\nMCP_PAL_TELEMETRY_ENABLED=true\n", encoding="utf-8"
    )
    monkeypatch.setenv("MCP_PAL_ARTIFACT_POLICY", "always")

    config = resolve_config(env={}, cwd=tmp_path)

    assert config.artifact_policy == "failed"
    assert config.telemetry_enabled is False


def test_canonical_environment_names_and_boolean_validation() -> None:
    config = resolve_config(
        env={
            "MCP_PAL_ARTIFACT_POLICY": "always",
            "MCP_PAL_PROTOCOL_REVISION": "auto",
            "MCP_PAL_TELEMETRY_ENABLED": "true",
        },
        cwd=Path("/tmp/mcp-pal-no-project"),
    )

    assert config.artifact_policy == "always"
    assert config.telemetry_enabled is True
    assert config.source_for("telemetry_enabled").source is ConfigSource.ENVIRONMENT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_policy", "sometimes"),
        ("protocol_revision", "bad revision with spaces"),
        ("telemetry_enabled", "maybe"),
    ],
)
def test_invalid_values_report_field_and_origin_without_value(field: str, value: object) -> None:
    with pytest.raises(ConfigurationError) as caught:
        resolve_config({field: value}, env={}, cwd=Path("/tmp/mcp-pal-no-project"))

    message = str(caught.value)
    assert field in message
    assert "argument" in message
    assert str(value) not in message


def test_unknown_settings_are_rejected_without_echoing_values(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcp-pal]\nunknown_secret_setting = "TOP-SECRET"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigurationError) as caught:
        resolve_config(env={}, cwd=tmp_path)

    assert "unknown_secret_setting" in str(caught.value)
    assert "TOP-SECRET" not in str(caught.value)

    with pytest.raises(ConfigurationError):
        resolve_config(env={"MCP_PAL_UNKNOWN": "TOP-SECRET"}, cwd=Path("/tmp/mcp-pal-no-project"))


def test_configuration_json_round_trip_and_aliases() -> None:
    config = resolve_config(
        {"artifact_policy": "never", "protocol_revision": "2025-06-18", "telemetry_enabled": True},
        env={},
        cwd=Path("/tmp/mcp-pal-no-project"),
    )
    encoded = json.dumps(config.model_dump(mode="json"))
    restored = SDKConfig.model_validate(json.loads(encoded))

    assert restored == config


def test_application_settings_no_longer_loads_cwd_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("DATABASE_PATH=leaked-from-dotenv.sqlite\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    assert Settings().database_path == "./mcp_pal.db"


def test_application_credentials_are_process_local_not_serialized() -> None:
    settings = Settings(
        anthropic_api_key="anthropic-secret",
        openrouter_api_key="openrouter-secret",
        opencode_api_key="opencode-secret",
    )

    assert settings.anthropic_api_key == "anthropic-secret"
    assert settings.opencode_provider_credentials()["anthropic"] == "anthropic-secret"
    representation = repr(settings)
    encoded = settings.model_dump_json()
    assert "anthropic-secret" not in representation
    assert "openrouter-secret" not in representation
    assert "opencode-secret" not in representation
    assert "anthropic-secret" not in encoded
    assert "openrouter-secret" not in encoded
    assert "opencode-secret" not in encoded
    assert "anthropic_api_key" not in settings.model_dump()


def test_settings_from_env_file_is_explicit_and_ambient_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "selected.env"
    selected.write_text(
        "ANTHROPIC_API_KEY=file-secret\nDATABASE_PATH=file.sqlite\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("DATABASE_PATH=implicit.sqlite\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", "ambient.sqlite")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = Settings.from_env_file(selected)

    assert settings.anthropic_api_key == "file-secret"
    assert settings.database_path == "ambient.sqlite"


def test_settings_from_env_file_errors_are_generic(tmp_path: Path) -> None:
    missing = tmp_path / "missing-secret.env"
    with pytest.raises(ValueError, match="^could not load the selected environment file$") as caught:
        Settings.from_env_file(missing)
    assert str(missing) not in str(caught.value)

    invalid = tmp_path / "invalid.env"
    invalid.write_text("RUN_TIMEOUT_SECONDS=not-a-number\n", encoding="utf-8")
    with pytest.raises(ValueError, match="^could not load the selected environment file$") as caught:
        Settings.from_env_file(invalid)
    assert "not-a-number" not in str(caught.value)
