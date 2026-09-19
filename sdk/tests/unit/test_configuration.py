"""Pure SDK configuration contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from m3.configuration import Config, ConfigError, ConfigSource, load_config


def test_defaults_are_frozen_and_value_only() -> None:
    config = load_config(env={}, cwd=Path("/tmp/m3-no-project"))
    assert config.artifact_policy == "failed"
    assert config.protocol_revision == "auto"
    assert config.telemetry_enabled is False
    assert all(info.source is ConfigSource.DEFAULT for info in config.sources.values())
    with pytest.raises((TypeError, ValueError)):
        config.telemetry_enabled = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        config.sources["telemetry_enabled"] = config.sources["telemetry_enabled"]  # type: ignore[index]


def test_direct_construction_infers_truthful_provenance() -> None:
    config = Config(
        artifact_policy="always", protocol_revision="2025-06-18", telemetry_enabled=True
    )
    assert all(info.source is ConfigSource.EXPLICIT for info in config.sources.values())
    assert config.source_for("artifact_policy").origin == "argument:artifact_policy"
    assert Config().source_for("artifact_policy").source is ConfigSource.DEFAULT


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
        Config(**kwargs)


def test_direct_construction_rejects_incomplete_or_untruthful_provenance() -> None:
    defaults = Config().sources
    incomplete = dict(defaults)
    incomplete.pop("telemetry_enabled")
    with pytest.raises(ValidationError):
        Config(sources=incomplete)
    with pytest.raises(ValidationError):
        Config(artifact_policy="always", sources=defaults)


def test_all_four_precedence_levels_and_origins(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3]\nartifact_policy = "always"\nprotocol_revision = "2025-06-18"\ntelemetry_enabled = true\n',
        encoding="utf-8",
    )
    config = load_config(
        {"artifact_policy": "failed"},
        env={
            "M3_ARTIFACT_POLICY": "never",
            "M3_PROTOCOL_REVISION": "2025-03-26",
        },
        cwd=tmp_path,
        telemetry_enabled=False,
    )
    assert config.model_dump(mode="json")["sources"]
    assert config.artifact_policy == "failed"
    assert config.protocol_revision == "2025-03-26"
    assert config.telemetry_enabled is False
    assert config.source_for("artifact_policy").source is ConfigSource.EXPLICIT
    assert config.source_for("protocol_revision").origin == "env:M3_PROTOCOL_REVISION"
    assert config.source_for("telemetry_enabled").origin == "argument:telemetry_enabled"


def test_project_config_uses_nearest_pyproject_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3]\nartifact_policy = "always"\n', encoding="utf-8"
    )
    child = tmp_path / "child"
    child.mkdir()
    (child / "pyproject.toml").write_text(
        "[project]\nname = 'child'\n", encoding="utf-8"
    )
    config = load_config(env={}, cwd=child)
    assert config.artifact_policy == "failed"
    assert config.source_for("artifact_policy").source is ConfigSource.DEFAULT


def test_supplied_environment_isolated_from_ambient_and_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "M3_ARTIFACT_POLICY=always\nM3_TELEMETRY_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("M3_ARTIFACT_POLICY", "always")
    config = load_config(env={}, cwd=tmp_path)
    assert config.artifact_policy == "failed"
    assert config.telemetry_enabled is False


def test_stable_environment_names_and_boolean_validation() -> None:
    config = load_config(
        env={
            "M3_ARTIFACT_POLICY": "always",
            "M3_PROTOCOL_REVISION": "auto",
            "M3_TELEMETRY_ENABLED": "true",
        },
        cwd=Path("/tmp/m3-no-project"),
    )
    assert config.artifact_policy == "always"
    assert config.telemetry_enabled is True
    assert config.source_for("telemetry_enabled").source is ConfigSource.ENVIRONMENT


@pytest.mark.parametrize(
    "field, value",
    [
        ("artifact_policy", "sometimes"),
        ("protocol_revision", "bad revision with spaces"),
        ("telemetry_enabled", "maybe"),
    ],
)
def test_invalid_values_report_field_and_origin_without_value(
    field: str, value: object
) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config({field: value}, env={}, cwd=Path("/tmp/m3-no-project"))
    message = str(caught.value)
    assert field in message and "argument" in message and str(value) not in message


def test_unknown_project_settings_are_rejected_without_echoing_values(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3]\nunknown_secret_setting = "TOP-SECRET"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError) as caught:
        load_config(env={}, cwd=tmp_path)
    assert "unknown_secret_setting" in str(caught.value)
    assert "TOP-SECRET" not in str(caught.value)


def test_unknown_prefixed_environment_variables_are_ignored() -> None:
    config = load_config(
        env={
            "M3_CLAUDE_MODEL": "claude-sonnet-5",
            "M3_TELEMETRY_ENABLED": "true",
        },
        cwd=Path("/tmp/m3-no-project"),
    )
    assert config.telemetry_enabled is True


def test_configuration_json_round_trip_and_aliases() -> None:
    config = load_config(
        {
            "artifact_policy": "never",
            "protocol_revision": "2025-06-18",
            "telemetry_enabled": True,
        },
        env={},
        cwd=Path("/tmp/m3-no-project"),
    )
    restored = Config.model_validate(
        json.loads(json.dumps(config.model_dump(mode="json")))
    )
    assert restored == config
