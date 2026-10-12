"""Strict sparse configuration construction and resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from m3.configuration import (
    Config,
    ConfigError,
    ConfigSource,
    load_config,
    merge_settings,
)
from m3.types import (
    UNSET,
    CachePolicy,
    ClaudeCodePermissions,
    CodexPermissionProfile,
    CodexPermissions,
    HarnessTimeouts,
    McpTimeouts,
    NativeFilesystemPermissions,
    NativeNetworkPermissions,
    OpenCodePermissions,
    StartupAdmission,
)


def test_explicit_leaves_inherit_and_none_disables_only_optional_budget() -> None:
    base = HarnessTimeouts(startup_seconds=90, total_seconds=120, session_seconds=300)
    merged = merge_settings(base, HarnessTimeouts(initialize_seconds=15))
    assert merged.startup_seconds == 90
    assert merged.total_seconds == 120
    assert merged.session_seconds == 300
    assert merged.initialize_seconds == 15
    disabled = merge_settings(merged, HarnessTimeouts(total_seconds=None))
    assert disabled.total_seconds is None
    assert disabled.startup_seconds == 90
    assert merge_settings(disabled, HarnessTimeouts(total_seconds=UNSET)) == disabled


def test_unset_and_explicit_presence_survive_json_roundtrip() -> None:
    value = HarnessTimeouts(initialize_seconds=15, total_seconds=None)
    restored = HarnessTimeouts.model_validate_json(value.model_dump_json())
    assert restored == value
    assert restored.model_fields_set == value.model_fields_set
    assert restored.startup_seconds is UNSET
    assert json.loads(restored.model_dump_json()) == {
        "initialize_seconds": 15.0,
        "total_seconds": None,
    }
    assert (
        StartupAdmission.model_validate_json(
            StartupAdmission().model_dump_json()
        ).model_fields_set
        == set()
    )
    with pytest.raises(ValidationError):
        HarnessTimeouts(startup_seconds="m3.unset")


@pytest.mark.parametrize(
    "value", [True, False, 0, -1, float("inf"), float("nan"), "30", None]
)
def test_required_phase_is_strict_positive_finite(value: object) -> None:
    with pytest.raises(ValidationError):
        McpTimeouts(startup_seconds=value)


def test_optional_budget_accepts_none_but_not_python_false() -> None:
    assert HarnessTimeouts(total_seconds=None).total_seconds is None
    assert (
        StartupAdmission(normalized_load_threshold=None).normalized_load_threshold
        is None
    )
    with pytest.raises(ValidationError):
        HarnessTimeouts(total_seconds=False)
    with pytest.raises(ValidationError):
        HarnessTimeouts(session_seconds=None)
    with pytest.raises(ValidationError):
        StartupAdmission(slots=True)


def test_invalid_merge_is_revalidated_without_mutating_sources() -> None:
    profile = CodexPermissions(profile="demo")
    sandbox = CodexPermissions(sandbox="read-only")
    with pytest.raises(ValidationError):
        merge_settings(profile, sandbox)
    assert profile.profile == "demo" and profile.sandbox is UNSET
    with pytest.raises(TypeError):
        merge_settings(profile, HarnessTimeouts())
    with pytest.raises(ValueError):
        merge_settings()


def test_defaulted_override_does_not_reset_requested_settings() -> None:
    cache = merge_settings(
        CachePolicy(mode="immutable-seed", root="/cache"), CachePolicy()
    )
    assert cache.mode == "immutable-seed" and cache.root == "/cache"
    admission = merge_settings(
        StartupAdmission(enabled=True, slots=8),
        StartupAdmission(queue_timeout_seconds=60),
    )
    assert admission.enabled is True and admission.slots == 8
    with pytest.raises(ValidationError):
        merge_settings(cache, CachePolicy(mode="isolated"))


def test_permissions_are_typed_and_frozen() -> None:
    profile = CodexPermissionProfile(
        name="demo",
        filesystem=NativeFilesystemPermissions(
            rules={":workspace_roots": "write", "**/.env": "deny"}
        ),
        network=NativeNetworkPermissions(
            enabled=True, domains={"api.example.com": "allow"}
        ),
    )
    assert CodexPermissions(profile=profile).profile == profile
    with pytest.raises(TypeError):
        profile.filesystem.rules["/secret"] = "read"  # type: ignore[index]
    with pytest.raises(ValidationError):
        CodexPermissions(profile=profile, network_access=True)
    with pytest.raises(ValidationError):
        CodexPermissionProfile(name=":workspace")
    assert ClaudeCodePermissions(permission_mode="plan").permission_mode == "plan"
    assert OpenCodePermissions(rules={"read": "deny"}).rules["read"] == "deny"


def test_same_schema_explicit_file_and_pyproject(tmp_path: Path) -> None:
    document = """[tool.m3.harnesses.codex.timeouts]
startup_seconds = 90
total_seconds = 120
[tool.m3.mcp_timeouts]
startup_seconds = 60
[tool.m3.servers.slow.timeouts]
startup_seconds = 75
[tool.m3.cli]
project_python_timeout_seconds = 60
[tool.m3.results]
busy_timeout_seconds = 30
[tool.m3.worker_bootstrap]
timeout_seconds = 20
"""
    project = tmp_path / "pyproject.toml"
    project.write_text(document)
    explicit_file = tmp_path / "run.toml"
    explicit_file.write_text(document)
    discovered = load_config(env={}, cwd=tmp_path)
    explicit = load_config(config_file=explicit_file, env={}, cwd=tmp_path)
    assert discovered.harnesses == explicit.harnesses
    assert explicit.servers["slow"].timeouts.startup_seconds == 75
    assert explicit.worker_bootstrap.timeout_seconds == 20
    assert (
        explicit.source_for("harnesses.codex.timeouts.startup_seconds").source
        is ConfigSource.PROJECT
    )
    assert (
        explicit.source_for("cli.project_python_timeout_seconds").origin
        == f"file:{explicit_file}"
    )
    assert Config.model_validate_json(explicit.model_dump_json()) == explicit


def test_file_explicit_overlays_preserve_nested_leaf_origins(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("""[tool.m3.harnesses.codex.timeouts]
startup_seconds=90
total_seconds=120
[tool.m3.servers.slow.timeouts]
startup_seconds=75
""")
    config = load_config(
        {"harnesses": {"codex": {"timeouts": HarnessTimeouts(initialize_seconds=15)}}},
        env={},
        cwd=tmp_path,
    )
    assert config.harnesses.codex.timeouts == HarnessTimeouts(
        startup_seconds=90, total_seconds=120, initialize_seconds=15
    )
    assert (
        config.source_for("harnesses.codex.timeouts.startup_seconds").source
        is ConfigSource.PROJECT
    )
    assert (
        config.source_for("harnesses.codex.timeouts.initialize_seconds").source
        is ConfigSource.EXPLICIT
    )
    assert (
        config.source_for("servers.slow.timeouts.startup_seconds").source
        is ConfigSource.PROJECT
    )
    assert (
        config.source_for("servers.slow.timeouts.request_seconds").source
        is ConfigSource.DEFAULT
    )


@pytest.mark.parametrize("field", ["total_seconds"])
def test_toml_false_is_optional_disable(tmp_path: Path, field: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.m3.harness_timeouts]\n{field}=false\n"
    )
    assert getattr(load_config(env={}, cwd=tmp_path).harness_timeouts, field) is None


@pytest.mark.parametrize(
    "document",
    [
        "[tool.m3.mcp_timeouts]\nstartup_seconds=false\n",
        "[tool.m3.mcp_timeouts]\nunknown='CANARY'\n",
        "[tool.m3.harnesses.unknown]\ntimeouts.startup_seconds=20\n",
        "[tool.m3.cli]\nproject_python_timeout_seconds='CANARY'\n",
    ],
)
def test_unknown_and_invalid_nested_settings_are_value_free(
    tmp_path: Path, document: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(document)
    with pytest.raises(ConfigError) as caught:
        load_config(env={}, cwd=tmp_path)
    assert "CANARY" not in str(caught.value)


def test_effective_file_explicit_conflict_fails_before_runtime(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3.harnesses.codex.permissions]\nprofile="demo"\n'
    )
    with pytest.raises(ConfigError):
        load_config(
            {"harnesses": {"codex": {"permissions": {"sandbox": "read-only"}}}},
            env={},
            cwd=tmp_path,
        )


def test_diagnostic_projection_has_no_values_paths_or_user_identifiers() -> None:
    config = load_config(
        {
            "servers": {"CANARY": {"timeouts": {"startup_seconds": 75}}},
            "harnesses": {"codex": {"permissions": {"profile": "CANARY"}}},
        },
        env={},
        cwd=Path("/tmp/no-project"),
    )
    projection = json.dumps(config.redacted_projection())
    assert "CANARY" not in projection
    assert "75" not in projection
    assert "argument:" not in projection
    assert "<alias>" in projection


def test_explicit_file_replaces_discovery_and_does_not_accept_bare_table(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.m3]\nartifact_policy="always"\n')
    file = tmp_path / "run.toml"
    file.write_text('[tool.m3]\nartifact_policy="never"\n')
    assert (
        load_config(config_file=file, env={}, cwd=tmp_path).artifact_policy == "never"
    )
    with pytest.raises(ConfigError):
        load_config(config_file=tmp_path / "missing.toml", env={}, cwd=tmp_path)


def test_bare_explicit_file_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "run.toml"
    file.write_text('artifact_policy="never"\n')
    with pytest.raises(ConfigError):
        load_config(config_file=file, env={}, cwd=tmp_path)


def test_dotted_aliases_have_distinct_unambiguous_leaf_origins() -> None:
    config = Config(servers={"a.b": {"timeouts": {"startup_seconds": 75}}})
    assert (
        config.source_for(r"servers.a\.b.timeouts.startup_seconds").source
        is ConfigSource.EXPLICIT
    )


def test_load_mapping_cannot_supply_provenance_metadata() -> None:
    with pytest.raises(ConfigError) as caught:
        load_config({"sources": Config().sources}, env={}, cwd=Path("/tmp/no-project"))
    assert caught.value.code == "unknown_setting"


@pytest.mark.parametrize(
    "field",
    [
        "startup_seconds",
        "initialize_seconds",
        "readiness_seconds",
        "request_seconds",
        "session_seconds",
    ],
)
def test_harness_required_phases_reject_none(field: str) -> None:
    with pytest.raises(ValidationError):
        HarnessTimeouts.model_validate({field: None})


def test_root_only_cache_layer_inherits_opt_in_and_final_config_rejects_without_it(
    tmp_path: Path,
) -> None:
    base = CachePolicy(mode="immutable-seed", root="/old")
    override = CachePolicy(root="/new")
    assert merge_settings(base, override).root == "/new"
    with pytest.raises(ValidationError):
        Config(cache=override)
    with pytest.raises(ValidationError):
        merge_settings(override)
    with pytest.raises(ValidationError):
        CachePolicy(mode="isolated", root="/new")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3.cache]\nmode="immutable-seed"\nroot="/old"\n'
    )
    assert load_config({"cache": override}, env={}, cwd=tmp_path).cache.root == "/new"
    with pytest.raises(ConfigError):
        load_config({"cache": override}, env={}, cwd=Path("/tmp/no-project"))


def test_empty_rule_map_clears_but_empty_nested_model_inherits() -> None:
    base = CodexPermissions(
        profile=CodexPermissionProfile(
            name="old",
            filesystem=NativeFilesystemPermissions(rules={"/allowed": "write"}),
            network=NativeNetworkPermissions(
                enabled=True, domains={"example.com": "allow"}
            ),
        )
    )
    renamed = merge_settings(
        base, CodexPermissions(profile=CodexPermissionProfile(name="new"))
    )
    assert renamed.profile.filesystem.rules == {"/allowed": "write"}
    assert renamed.profile.network.enabled is True
    cleared = merge_settings(
        base,
        CodexPermissions(
            profile=CodexPermissionProfile(
                name="new",
                filesystem=NativeFilesystemPermissions(rules={}),
            )
        ),
    )
    assert cleared.profile.filesystem.rules == {}
    assert cleared.profile.network.domains == {"example.com": "allow"}
    assert NativeFilesystemPermissions.model_validate_json(
        NativeFilesystemPermissions(rules={}).model_dump_json()
    ).model_fields_set == {"rules"}


def test_file_rules_can_be_cleared_and_provenance_identifies_empty_map(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.m3.harnesses.opencode.permissions.rules]\nread="allow"\n'
    )
    config = load_config(
        {"harnesses": {"opencode": {"permissions": {"rules": {}}}}},
        env={},
        cwd=tmp_path,
    )
    assert config.harnesses.opencode.permissions.rules == {}
    assert (
        config.source_for("harnesses.opencode.permissions.rules").source
        is ConfigSource.EXPLICIT
    )
    assert "harnesses.opencode.permissions.rules.read" not in config.sources


def test_empty_server_settings_map_clears_aliases_without_creating_a_binding(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.servers.slow.timeouts]\nstartup_seconds=75\n"
    )
    config = load_config({"servers": {}}, env={}, cwd=tmp_path)
    assert config.servers == {}
    assert config.source_for("servers").source is ConfigSource.EXPLICIT
    assert config.redacted_projection()["native_settings"] == [
        {"field": "servers", "source": "explicit"}
    ]


def test_config_roundtrip_preserves_native_explicit_presence_including_empty_maps() -> (
    None
):
    for config in (
        Config(),
        Config(servers={}),
        Config(harness_timeouts=HarnessTimeouts(total_seconds=None)),
    ):
        restored = Config.model_validate_json(config.model_dump_json())
        assert restored == config
        native_fields = set(Config.model_fields) - {
            "artifact_policy",
            "protocol_revision",
            "sources",
        }
        assert (
            restored.model_fields_set & native_fields
            == config.model_fields_set & native_fields
        )
    assert "servers" not in Config().model_dump(mode="json")
    assert Config(servers={}).model_dump(mode="json")["servers"] == {}
