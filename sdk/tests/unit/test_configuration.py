"""Pure SDK configuration contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from m3.configuration import (
    _SETTING_NAMES,
    Config,
    ConfigError,
    ConfigSource,
    ResultsSettings,
    _config_from_snapshot,
    _config_snapshot,
    _install_run_config,
    _resolve_kit_config,
    configuration_digest,
    load_config,
)


def test_defaults_are_frozen_and_value_only() -> None:
    config = load_config(env={}, cwd=Path("/tmp/m3-no-project"))
    assert config.artifact_policy == "failed"
    assert config.protocol_revision == "auto"
    assert all(info.source is ConfigSource.DEFAULT for info in config.sources.values())
    with pytest.raises((TypeError, ValueError)):
        config.artifact_policy = "always"  # type: ignore[misc]
    with pytest.raises(TypeError):
        config.sources["artifact_policy"] = config.sources["artifact_policy"]  # type: ignore[index]


def test_direct_construction_infers_truthful_provenance() -> None:
    config = Config(artifact_policy="always", protocol_revision="2025-06-18")
    assert config.source_for("artifact_policy").source is ConfigSource.EXPLICIT
    assert config.source_for("protocol_revision").source is ConfigSource.EXPLICIT
    assert (
        config.source_for("results.busy_timeout_seconds").source is ConfigSource.DEFAULT
    )
    assert config.source_for("artifact_policy").origin == "argument:artifact_policy"
    assert Config().source_for("artifact_policy").source is ConfigSource.DEFAULT


@pytest.mark.parametrize(
    "kwargs",
    [
        {"artifact_policy": "sometimes"},
        {"protocol_revision": "revision with spaces"},
        {"telemetry_enabled": True},
    ],
)
def test_direct_construction_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Config(**kwargs)


def test_direct_construction_rejects_incomplete_or_untruthful_provenance() -> None:
    defaults = Config().sources
    incomplete = dict(defaults)
    incomplete.pop("protocol_revision")
    with pytest.raises(ValidationError):
        Config(sources=incomplete)
    with pytest.raises(ValidationError):
        Config(artifact_policy="always", sources=defaults)


def test_all_four_precedence_levels_and_origins(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3]\nartifact_policy = "always"\nprotocol_revision = "2025-06-18"\n',
        encoding="utf-8",
    )
    config = load_config(
        {"artifact_policy": "failed"},
        env={
            "M3_ARTIFACT_POLICY": "never",
            "M3_PROTOCOL_REVISION": "2025-03-26",
        },
        cwd=tmp_path,
    )
    assert config.model_dump(mode="json")["sources"]
    assert config.artifact_policy == "failed"
    assert config.protocol_revision == "2025-03-26"
    assert config.source_for("artifact_policy").source is ConfigSource.EXPLICIT
    assert config.source_for("protocol_revision").origin == "env:M3_PROTOCOL_REVISION"


def test_keyword_argument_overrides_project_and_project_overrides_default(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3]\nartifact_policy = "always"\nprotocol_revision = "2025-06-18"\n',
        encoding="utf-8",
    )
    config = load_config(env={}, cwd=tmp_path, protocol_revision="2025-03-26")
    assert config.artifact_policy == "always"
    assert config.source_for("artifact_policy").source is ConfigSource.PROJECT
    assert config.protocol_revision == "2025-03-26"
    assert config.source_for("protocol_revision").origin == "argument:protocol_revision"


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
        "M3_ARTIFACT_POLICY=always\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("M3_ARTIFACT_POLICY", "always")
    config = load_config(env={}, cwd=tmp_path)
    assert config.artifact_policy == "failed"


def test_stable_environment_names() -> None:
    config = load_config(
        env={
            "M3_ARTIFACT_POLICY": "always",
            "M3_PROTOCOL_REVISION": "2025-06-18",
        },
        cwd=Path("/tmp/m3-no-project"),
    )
    assert config.artifact_policy == "always"
    assert config.protocol_revision == "2025-06-18"
    assert config.source_for("artifact_policy").source is ConfigSource.ENVIRONMENT
    assert config.source_for("protocol_revision").origin == "env:M3_PROTOCOL_REVISION"


def test_invalid_environment_value_reports_variable_without_value() -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(
            env={"M3_ARTIFACT_POLICY": "TOP-SECRET"}, cwd=Path("/tmp/m3-no-project")
        )
    message = str(caught.value)
    assert "artifact_policy" in message and "env:M3_ARTIFACT_POLICY" in message
    assert "TOP-SECRET" not in message


@pytest.mark.parametrize(
    "field, value",
    [
        ("artifact_policy", "sometimes"),
        ("protocol_revision", "bad revision with spaces"),
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


def test_removed_telemetry_setting_is_an_unknown_project_setting(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3]\ntelemetry_enabled = true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as caught:
        load_config(env={}, cwd=tmp_path)
    assert caught.value.code == "unknown_setting"
    assert caught.value.field == "telemetry_enabled"


def test_removed_telemetry_setting_is_an_unknown_explicit_setting() -> None:
    with pytest.raises(ConfigError) as caught:
        load_config({"telemetry_enabled": True}, env={}, cwd=Path("/tmp/m3-no-project"))
    assert caught.value.code == "unknown_setting"
    with pytest.raises(TypeError):
        load_config(env={}, cwd=Path("/tmp/m3-no-project"), telemetry_enabled=True)  # type: ignore[call-arg]


def test_unknown_prefixed_environment_variables_are_ignored() -> None:
    config = load_config(
        env={
            "M3_CLAUDE_MODEL": "claude-sonnet-5",
            "M3_TELEMETRY_ENABLED": "true",
        },
        cwd=Path("/tmp/m3-no-project"),
    )
    assert config == load_config(env={}, cwd=Path("/tmp/m3-no-project"))


def test_configuration_json_round_trip_and_aliases() -> None:
    config = load_config(
        {
            "artifact_policy": "never",
            "protocol_revision": "2025-06-18",
        },
        env={},
        cwd=Path("/tmp/m3-no-project"),
    )
    restored = Config.model_validate(
        json.loads(json.dumps(config.model_dump(mode="json")))
    )
    assert restored == config


_NO_PROJECT = Path("/tmp/m3-no-project")


def test_nested_project_sections_and_origins(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.results]\nbusy_timeout_seconds = 30\n", encoding="utf-8"
    )
    config = load_config(env={}, cwd=tmp_path)
    assert config.results.busy_timeout_seconds == 30.0
    assert (
        config.source_for("results.busy_timeout_seconds").origin
        == f"pyproject:{tmp_path / 'pyproject.toml'}"
    )
    assert config.cli.project_python_timeout_seconds == 15.0
    assert (
        config.source_for("cli.project_python_timeout_seconds").source
        is ConfigSource.DEFAULT
    )


def test_explicit_nested_leaf_overrides_file_leaf_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.results]\nbusy_timeout_seconds = 30\n", encoding="utf-8"
    )
    config = load_config({"results": {"busy_timeout_seconds": 1}}, env={}, cwd=tmp_path)
    source = config.source_for("results.busy_timeout_seconds")
    assert config.results.busy_timeout_seconds == 1.0
    assert source.source is ConfigSource.EXPLICIT
    assert source.origin == "argument:results.busy_timeout_seconds"
    assert config.source_for("artifact_policy").source is ConfigSource.DEFAULT


@pytest.mark.parametrize(
    ("toml", "explicit", "code", "field", "reason"),
    [
        ("[tool.m3.results]\nfoo = 1\n", None, "unknown_setting", "results.foo", None),
        (
            "[tool.m3]\nresults = 3\n",
            None,
            "invalid_configuration",
            "results",
            "must be a table",
        ),
        (
            "",
            {"results.busy_timeout_seconds": 1},
            "unknown_setting",
            "results.busy_timeout_seconds",
            None,
        ),
    ],
)
def test_nested_unknown_and_shape_errors(
    tmp_path: Path,
    toml: str,
    explicit: dict[str, object] | None,
    code: str,
    field: str,
    reason: str | None,
) -> None:
    (tmp_path / "pyproject.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(explicit, env={}, cwd=tmp_path)
    assert caught.value.code == code
    assert caught.value.field == field
    if reason is not None:
        assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("section", "name", "value", "reason", "secret"),
    [
        (
            "results",
            "busy_timeout_seconds",
            -1,
            "Input should be greater than or equal to 0",
            "-1",
        ),
        (
            "results",
            "busy_timeout_seconds",
            3601,
            "Input should be less than or equal to 3600",
            "3601",
        ),
        (
            "results",
            "busy_timeout_seconds",
            True,
            "Input should be a valid number",
            "True",
        ),
        (
            "results",
            "busy_timeout_seconds",
            "5",
            "Input should be a valid number",
            "'5'",
        ),
        (
            "results",
            "busy_timeout_seconds",
            float("nan"),
            "Input should be a finite number",
            "nan",
        ),
        (
            "cli",
            "project_python_timeout_seconds",
            0,
            "Input should be greater than 0",
            "= 0",
        ),
    ],
)
def test_nested_value_errors_are_value_free(
    section: str, name: str, value: object, reason: str, secret: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config({section: {name: value}}, env={}, cwd=_NO_PROJECT)
    assert caught.value.code == "invalid_configuration"
    assert caught.value.reason == reason
    assert caught.value.field == f"{section}.{name}"
    assert secret not in str(caught.value)


def test_nested_toml_nan_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.results]\nbusy_timeout_seconds = nan\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as caught:
        load_config(env={}, cwd=tmp_path)
    assert caught.value.reason == "Input should be a finite number"


def test_config_file_replaces_discovery(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.results]\nbusy_timeout_seconds = 30\n", encoding="utf-8"
    )
    other = tmp_path / "other.toml"
    other.write_text(
        "[tool.m3.cli]\nproject_python_timeout_seconds = 40\n", encoding="utf-8"
    )
    config = load_config(env={}, cwd=tmp_path, config_file=other)
    assert config.cli.project_python_timeout_seconds == 40.0
    assert (
        config.source_for("cli.project_python_timeout_seconds").origin
        == f"config-file:{other}"
    )
    assert config.results.busy_timeout_seconds == 5.0
    assert (
        config.source_for("results.busy_timeout_seconds").source is ConfigSource.DEFAULT
    )


def test_config_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as missing:
        load_config(env={}, cwd=tmp_path, config_file=tmp_path / "missing.toml")
    assert missing.value.code == "invalid_project"
    assert missing.value.field == "config_file"
    assert missing.value.reason == "file not found"
    other = tmp_path / "other.toml"
    other.write_text("[tool.other]\nx = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as no_table:
        load_config(env={}, cwd=tmp_path, config_file=other)
    assert no_table.value.code == "invalid_project"
    assert no_table.value.field == "project"
    assert no_table.value.reason == "missing [tool.m3] table"


def test_configuration_digest_ignores_sources(tmp_path: Path) -> None:
    explicit = load_config(
        {"results": {"busy_timeout_seconds": 30}}, env={}, cwd=_NO_PROJECT
    )
    thirty = tmp_path / "thirty.toml"
    thirty.write_text(
        "[tool.m3.results]\nbusy_timeout_seconds = 30\n", encoding="utf-8"
    )
    from_file = load_config(env={}, cwd=_NO_PROJECT, config_file=thirty)
    thirty_one = load_config(
        {"results": {"busy_timeout_seconds": 31}}, env={}, cwd=_NO_PROJECT
    )
    assert explicit.sources != from_file.sources
    assert configuration_digest(explicit) == configuration_digest(from_file)
    assert configuration_digest(explicit) != configuration_digest(thirty_one)
    assert configuration_digest(explicit).startswith("sha256:")


@pytest.mark.parametrize(
    "bad",
    ["{}", "x", None, json.dumps({"version": 2, "config": {}})],
)
def test_snapshot_round_trip_and_rejection(bad: object) -> None:
    config = load_config(
        {"results": {"busy_timeout_seconds": 30}}, env={}, cwd=_NO_PROJECT
    )
    assert _config_from_snapshot(_config_snapshot(config)) == config
    with pytest.raises(ValueError, match="M3 configuration snapshot is invalid"):
        _config_from_snapshot(bad)


def test_kit_inherits_run_config() -> None:
    previous = _install_run_config(
        load_config({"results": {"busy_timeout_seconds": 30}}, env={}, cwd=_NO_PROJECT)
    )
    try:
        assert (
            _resolve_kit_config(None, env=None, cwd=None).results.busy_timeout_seconds
            == 30.0
        )
        overridden = _resolve_kit_config(
            {"results": {"busy_timeout_seconds": 2}}, env=None, cwd=None
        )
        assert overridden.results.busy_timeout_seconds == 2.0
        assert (
            overridden.source_for("results.busy_timeout_seconds").source
            is ConfigSource.EXPLICIT
        )
        standalone = _resolve_kit_config(None, env={}, cwd=_NO_PROJECT)
        assert standalone.results.busy_timeout_seconds == 5.0
    finally:
        _install_run_config(previous)


def test_direct_construction_nested_provenance() -> None:
    config = Config(results=ResultsSettings(busy_timeout_seconds=9))
    leaf = config.source_for("results.busy_timeout_seconds")
    assert leaf.source is ConfigSource.EXPLICIT
    assert leaf.origin == "argument:results.busy_timeout_seconds"
    for name in _SETTING_NAMES:
        if name != "results.busy_timeout_seconds":
            assert config.source_for(name).source is ConfigSource.DEFAULT
    with pytest.raises(ValidationError):
        Config(
            results=ResultsSettings(busy_timeout_seconds=9), sources=Config().sources
        )
