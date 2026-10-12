"""Private settings stay off ordinary worker metadata and survive file deletion."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from m3._configuration_snapshot import (
    ConfigurationSnapshot,
    ConfigurationSnapshotError,
    configuration_digest,
    freeze_configuration,
    parse_configuration_snapshot,
    restore_configuration_snapshot,
)
from m3._literal_store import RunLiteralStore
from m3.configuration import Config, load_config
from m3.types import HarnessTimeouts


def test_default_configuration_has_no_registry_or_bootstrap_handles() -> None:
    snapshot = freeze_configuration(Config(), None)
    restored = restore_configuration_snapshot(snapshot, None)
    assert restored.config == Config()
    assert restored.literal_registry is None
    assert snapshot.handles == ()


def test_configured_snapshot_retains_private_values_after_source_deletion(
    tmp_path: Path,
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[tool.m3.harnesses.codex.permissions]\nprofile="PRIVATE-CANARY"\n'
        "[tool.m3.harness_timeouts]\nstartup_seconds=90\n"
    )
    config = load_config(env={}, cwd=tmp_path)
    registry = RunLiteralStore("run")
    try:
        snapshot = freeze_configuration(config, registry)
        metadata = snapshot.model_dump_json()
        assert "PRIVATE-CANARY" not in metadata
        assert str(tmp_path) not in metadata
        assert "PRIVATE-CANARY" not in repr(snapshot)
        assert len(snapshot.handles) == 1
        project.unlink()
        restored = restore_configuration_snapshot(
            parse_configuration_snapshot(metadata), registry
        )
        assert restored.config == config
        assert restored.literal_registry is registry
    finally:
        registry.close()


def test_private_value_changes_do_not_change_public_projection_digest() -> None:
    first = Config(harnesses={"codex": {"permissions": {"profile": "FIRST-SECRET"}}})
    second = Config(harnesses={"codex": {"permissions": {"profile": "SECOND-SECRET"}}})
    assert configuration_digest(first) == configuration_digest(second)


def test_nonsecret_budget_changes_change_projection_digest() -> None:
    first = Config(harness_timeouts=HarnessTimeouts(startup_seconds=90))
    second = Config(harness_timeouts=HarnessTimeouts(startup_seconds=120))
    assert configuration_digest(first) != configuration_digest(second)


def test_missing_or_wrong_generation_registry_cannot_restore_private_settings() -> None:
    registry = RunLiteralStore("run")
    replacement = RunLiteralStore("run")
    try:
        snapshot = freeze_configuration(Config(artifact_policy="always"), registry)
        with pytest.raises(ConfigurationSnapshotError):
            restore_configuration_snapshot(snapshot, None)
        with pytest.raises(ConfigurationSnapshotError):
            restore_configuration_snapshot(snapshot, replacement)
    finally:
        registry.close()
        replacement.close()


def test_configured_settings_cannot_be_embedded_in_public_snapshot() -> None:
    with pytest.raises(ValidationError):
        ConfigurationSnapshot(
            digest="0" * 64,
            settings=Config(
                harnesses={"codex": {"permissions": {"profile": "SECRET"}}}
            ),
        )
    with pytest.raises(ValidationError):
        ConfigurationSnapshot(version=True, digest="0" * 64, settings=Config())


def test_closed_registry_does_not_trigger_source_rediscovery() -> None:
    registry = RunLiteralStore("run")
    snapshot = freeze_configuration(Config(artifact_policy="always"), registry)
    registry.close()
    with pytest.raises(ConfigurationSnapshotError):
        restore_configuration_snapshot(snapshot, registry)


def test_malformed_worker_snapshot_has_value_free_error() -> None:
    with pytest.raises(ConfigurationSnapshotError) as caught:
        parse_configuration_snapshot('{"version":"PRIVATE-CANARY"}')
    assert "PRIVATE-CANARY" not in str(caught.value)
