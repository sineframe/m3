"""Kits inherit invocation settings without repeating project discovery."""

import pickle
from pathlib import Path

import pytest

from m3._configuration_context import (
    ConfigurationContext,
    install_configuration,
    resolve_configuration,
    restore_configuration,
)
from m3._literal_store import RunLiteralStore
from m3.async_api import AsyncMCPTestKit
from m3.configuration import Config, ConfigSource, load_config
from m3.sync_api import MCPTestKit
from m3.types import HarnessTimeouts


@pytest.mark.parametrize("kit_type", [MCPTestKit, AsyncMCPTestKit])
def test_kit_preserves_snapshot_after_source_deletion_and_environment_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kit_type: type
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[tool.m3]\nartifact_policy="always"\n'
        "[tool.m3.harness_timeouts]\nstartup_seconds=90\ntotal_seconds=120\n"
    )
    frozen = load_config(env={}, cwd=tmp_path)
    token = install_configuration(ConfigurationContext(frozen))
    project.unlink()
    monkeypatch.setenv("M3_ARTIFACT_POLICY", "never")
    try:
        kit = kit_type(
            {"harness_timeouts": HarnessTimeouts(initialize_seconds=15)},
            env={"M3_ARTIFACT_POLICY": "failed"},
            cwd=tmp_path,
        )
        assert kit.config.artifact_policy == "always"
        assert kit.config.harness_timeouts.startup_seconds == 90
        assert kit.config.harness_timeouts.total_seconds == 120
        assert kit.config.harness_timeouts.initialize_seconds == 15
        assert kit.config.source_for("artifact_policy") == frozen.source_for(
            "artifact_policy"
        )
        assert (
            kit.config.source_for("harness_timeouts.initialize_seconds").source
            is ConfigSource.EXPLICIT
        )
    finally:
        restore_configuration(token)


def test_typed_python_override_keeps_omitted_invocation_leaves() -> None:
    base = Config(
        artifact_policy="always", harness_timeouts=HarnessTimeouts(startup_seconds=90)
    )
    token = install_configuration(ConfigurationContext(base))
    try:
        kit = MCPTestKit(Config(harness_timeouts=HarnessTimeouts(total_seconds=None)))
        assert kit.config.artifact_policy == "always"
        assert kit.config.harness_timeouts.startup_seconds == 90
        assert kit.config.harness_timeouts.total_seconds is None
        assert resolve_configuration(None, env={}, cwd=None).config is base
    finally:
        restore_configuration(token)


def test_kit_close_does_not_own_invocation_literal_registry() -> None:
    registry = RunLiteralStore("invocation")
    handle = registry.freeze("frozen-input")
    context = ConfigurationContext(Config(), registry)
    token = install_configuration(context)
    try:
        with MCPTestKit() as kit:
            assert kit._literal_registry is registry
        assert registry.resolve(handle) == "frozen-input"
    finally:
        restore_configuration(token)
        registry.close()


def test_context_cannot_escape_through_repr_or_generic_worker_serialization() -> None:
    context = ConfigurationContext(
        Config(harnesses={"codex": {"permissions": {"profile": "PRIVATE-CANARY"}}})
    )
    assert "PRIVATE-CANARY" not in repr(context)
    with pytest.raises(TypeError, match="private snapshot transport"):
        pickle.dumps(context)


def test_restoring_nested_context_does_not_reactivate_closed_owner() -> None:
    outer = install_configuration(
        ConfigurationContext(Config(artifact_policy="always"))
    )
    inner = install_configuration(ConfigurationContext(Config(artifact_policy="never")))
    try:
        restore_configuration(outer)
        assert (
            resolve_configuration(None, env={}, cwd=None).config.artifact_policy
            == "never"
        )
    finally:
        restore_configuration(inner)
        restore_configuration(outer)
    assert (
        resolve_configuration(
            None, env={}, cwd=Path("/tmp/no-project")
        ).config.artifact_policy
        == "failed"
    )
