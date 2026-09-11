from __future__ import annotations

from pathlib import Path

import pytest

from mcp_pal.storage import (
    ProfileRecord,
    ProfileRevisionRecord,
    SQLiteExecutionStore,
    StorageError,
)
from mcp_pal_app.services.app_service import AppRuntimeService
from mcp_pal_app.services.readiness_service import (
    REQUIRED_CLAUDE_FLAGS,
    ReadinessService,
    ReadinessView,
    StorageHealthView,
)
from mcp_pal_app.settings import Settings


def _settings(database: Path, **kwargs: object) -> Settings:
    values: dict[str, object] = {
        "database_path": str(database),
        "claude_executable": "claude-fixture",
        "opencode_executable": "opencode-fixture",
        "claude_model_ids": ["claude/one"],
        "opencode_model_ids": ["openrouter/model", "anthropic/model"],
        "run_timeout_seconds": 9,
        "claude_max_turns": 4,
        "claude_max_budget_usd": 1.25,
    }
    values.update(kwargs)
    return Settings.model_validate(values)


def _help(_executable: str, args: tuple[str, ...]) -> str:
    # The probe seam receives argv without exposing it in a descriptor. Keep
    # this deterministic and representative of bounded fixture help output.
    if args[:1] == ("serve",):
        return "serve"
    if args == ("--version",):
        return "OpenCode 1.2.3"
    return " ".join(REQUIRED_CLAUDE_FLAGS)


def _store(tmp_path: Path) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(tmp_path / "readiness.sqlite")


def test_builtins_use_settings_snapshot_and_expose_typed_limits(tmp_path: Path) -> None:
    store = _store(tmp_path)
    settings = _settings(
        tmp_path / "unused.sqlite", openrouter_api_key="settings-only-key"
    )
    service = ReadinessService(
        settings,
        store,
        environment={},
        help_probe=_help,
        executable_resolver=lambda _: "/fixture",
    )

    snapshot = service.capabilities()
    claude, opencode = snapshot.builtins
    assert claude.ready is False  # the settings snapshot has no Claude key
    assert opencode.ready is True
    assert claude.models == ("claude/one",)
    assert opencode.models == ("openrouter/model", "anthropic/model")
    assert (
        claude.tool_modes
        == opencode.tool_modes
        == ("mcp_only", "mcp_read_only", "full")
    )
    assert claude.limits.timeout_seconds == 9
    assert claude.limits.max_turns == 4
    assert claude.limits.max_budget_usd == 1.25
    assert opencode.limits.max_turns is None
    assert snapshot.ready is True
    store.close()


def test_missing_executable_and_required_flags_are_explicit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    settings = _settings(tmp_path / "unused.sqlite", anthropic_api_key="key")
    service = ReadinessService(
        settings,
        store,
        environment={},
        help_probe=lambda *_: "--print",
        executable_resolver=lambda _: None,
    )
    descriptor = service.capabilities().builtins[0]
    assert descriptor.executable is False
    assert descriptor.ready is False
    assert descriptor.missing_flags == REQUIRED_CLAUDE_FLAGS

    partial = ReadinessService(
        settings,
        store,
        environment={},
        help_probe=lambda *_: "--print",
        executable_resolver=lambda _: "/fixture",
    )
    descriptor = partial.capabilities().builtins[0]
    assert descriptor.executable is True
    assert descriptor.required_flags_ok is False
    assert descriptor.missing_flags == tuple(
        flag for flag in REQUIRED_CLAUDE_FLAGS if flag != "--print"
    )
    store.close()


def test_opencode_saved_auth_is_independent_of_environment_credentials(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    settings = _settings(tmp_path / "unused.sqlite")
    calls: list[tuple[str, tuple[str, ...]]] = []

    def auth(executable: str, providers: tuple[str, ...]) -> bool:
        calls.append((executable, providers))
        return True

    service = ReadinessService(
        settings,
        store,
        environment={},
        help_probe=_help,
        auth_probe=auth,
        executable_resolver=lambda _: "/fixture",
    )
    descriptor = service.capabilities().builtins[1]
    assert descriptor.ready is True
    assert descriptor.credential_available is True
    assert calls == [("opencode-fixture", ("anthropic", "openrouter"))]
    store.close()


def test_opencode_default_saved_auth_probe_uses_allowlisted_user_paths(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "opencode"
    observed = tmp_path / "observed-env"
    executable.write_text(
        f"#!/bin/sh\nif [ \"$1\" = auth ]; then if [ -n \"$OPENROUTER_API_KEY\" ]; then echo present > '{observed}'; else echo absent > '{observed}'; fi; echo 'OpenRouter credentials'; fi\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    store = _store(tmp_path)
    settings = _settings(
        tmp_path / "unused.sqlite", opencode_executable=str(executable)
    )
    # The executable is a bounded fixture; no network or real provider auth is
    # involved. The explicit HOME proves the probe does not use a temp HOME.
    service = ReadinessService(
        settings,
        store,
        environment={"PATH": "/bin", "HOME": str(tmp_path / "home")},
        help_probe=_help,
    )
    descriptor = service.capabilities().builtins[1]
    assert descriptor.ready is True
    assert observed.read_text(encoding="utf-8").strip() == "absent"
    store.close()


def test_local_acp_can_run_when_global_builtins_fail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    settings = _settings(tmp_path / "unused.sqlite")
    profile = store.create_harness_profile(
        "local-acp",
        {
            "manifest": {"command": "acp-fixture", "env": {"TOKEN": "${ACP_TOKEN}"}},
            "trusted_unsandboxed": True,
        },
    )
    service = ReadinessService(
        settings,
        store,
        environment={"ACP_TOKEN": "present"},
        help_probe=lambda *_: None,
        executable_resolver=lambda value: (
            "/fixture" if value == "acp-fixture" else None
        ),
    )
    snapshot = service.capabilities()
    descriptor = next(
        item for item in snapshot.acp_profiles if item.profile_id == profile.id
    )
    assert snapshot.ready is True
    assert descriptor.selection_id == f"profile:{profile.id}"
    assert descriptor.revision_id is not None
    assert descriptor.ready is True
    assert descriptor.protocol_verified is False
    assert descriptor.verification_status == "unverified"
    assert any("fully verified" in warning for warning in descriptor.warnings)
    store.close()


def test_local_acp_uses_settings_only_provider_reference(tmp_path: Path) -> None:
    store = _store(tmp_path)
    canary = "settings-only-acp-canary"
    profile = store.create_harness_profile(
        "settings-acp",
        {
            "manifest": {
                "command": "acp-fixture",
                "env": {"TOKEN": "${ANTHROPIC_API_KEY}"},
            },
            "trusted_unsandboxed": True,
        },
    )
    service = ReadinessService(
        _settings(tmp_path / "unused.sqlite", anthropic_api_key=canary),
        store,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda value: (
            "/fixture" if value == "acp-fixture" else None
        ),
    )
    descriptor = next(
        item
        for item in service.capabilities().acp_profiles
        if item.profile_id == profile.id
    )
    assert descriptor.ready is True
    assert canary not in repr(descriptor)
    assert canary not in repr(service)
    store.close()


def test_acp_missing_environment_untrusted_and_archived_are_not_ready(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    trusted_missing = store.create_harness_profile(
        "missing-env",
        {
            "manifest": {"command": "fixture", "env": {"TOKEN": "${NEEDED}"}},
            "trusted_unsandboxed": True,
        },
    )
    untrusted = store.create_harness_profile(
        "untrusted", {"manifest": {"command": "fixture"}, "trusted_unsandboxed": False}
    )
    archived = store.create_harness_profile(
        "archived", {"manifest": {"command": "fixture"}, "trusted_unsandboxed": True}
    )
    store.archive_profile(archived.id)
    service = ReadinessService(
        _settings(tmp_path / "unused.sqlite"),
        store,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda _: "/fixture",
    )
    descriptors = {
        item.profile_id: item for item in service.capabilities().acp_profiles
    }
    assert descriptors[trusted_missing.id].ready is False
    assert "NEEDED" in descriptors[trusted_missing.id].missing_environment
    assert descriptors[trusted_missing.id].local_ready is False
    assert descriptors[untrusted.id].ready is False
    assert descriptors[untrusted.id].local_ready is True
    assert archived.id not in descriptors
    archived_descriptor = service.acp_readiness(archived.id)
    assert archived_descriptor is not None
    assert archived_descriptor.ready is False
    assert archived_descriptor.local_ready is True
    assert archived_descriptor.archived is True
    store.close()


def test_acp_executable_probe_failure_is_safe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    profile = store.create_harness_profile(
        "throwing-probe",
        {"manifest": {"command": "fixture"}, "trusted_unsandboxed": True},
    )

    def throwing_resolver(_value: str) -> str | None:
        raise RuntimeError("secret probe detail")

    service = ReadinessService(
        _settings(tmp_path / "unused.sqlite"),
        store,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=throwing_resolver,
    )
    descriptor = next(
        item
        for item in service.capabilities().acp_profiles
        if item.profile_id == profile.id
    )
    assert descriptor.executable is False
    assert descriptor.local_ready is False
    assert "secret probe detail" not in repr(descriptor)
    store.close()


def test_storage_failure_is_safe_and_does_not_claim_readiness(tmp_path: Path) -> None:
    class BrokenStore:
        def list_profiles(
            self, kind: str, *, include_archived: bool = False
        ) -> tuple[ProfileRecord, ...]:
            raise StorageError("secret database path should not escape")

        def list_profile_revisions(
            self, profile_id: str
        ) -> tuple[ProfileRevisionRecord, ...]:
            raise StorageError("secret revision should not escape")

    service = ReadinessService(
        _settings(tmp_path / "unused.sqlite", anthropic_api_key="credential-canary"),
        BrokenStore(),
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda _: None,
    )
    snapshot = service.capabilities()
    assert snapshot.storage.available is False
    assert snapshot.storage.reason == "storage is unavailable"
    assert "credential-canary" not in repr(snapshot)


def test_runtime_exposes_readiness_facade_and_close_guard(tmp_path: Path) -> None:
    runtime = AppRuntimeService(_settings(tmp_path / "runtime.sqlite"))
    retained = runtime.readiness
    assert runtime.capabilities().storage.available is True
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.capabilities()
    with pytest.raises(RuntimeError, match="closed"):
        _ = runtime.readiness
    with pytest.raises(RuntimeError, match="closed"):
        retained.capabilities()


def test_runtime_uses_injected_readiness_provider(tmp_path: Path) -> None:
    store = _store(tmp_path)

    class Kit:
        def __init__(self) -> None:
            self.store = store

        def submit(self, spec: object) -> object:
            raise AssertionError("submit should not be called")

        def close(self) -> None:
            return None

    class FakeReadiness:
        def __init__(self) -> None:
            self.calls = 0
            self.value = ReadinessView(StorageHealthView(True, "connected"), (), ())

        def capabilities(self, *, include_archived: bool = False) -> ReadinessView:
            self.calls += 1
            return self.value

    fake = FakeReadiness()
    runtime = AppRuntimeService(
        _settings(tmp_path / "unused.sqlite"),
        store=store,
        kit=Kit(),
        readiness_service=fake,
    )
    assert runtime.capabilities() is fake.value
    assert fake.calls == 1
    runtime.close()
    store.close()
