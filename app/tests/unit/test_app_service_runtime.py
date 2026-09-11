from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mcp_pal import AgentSpec, EventKind, ExecutionId, ExecutionOutcome
from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.harness import HarnessAdapterRegistry
from mcp_pal.services.acp_probes import ACPProbeKind, ACPProbeRequest
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal_app.services.app_service import AppRuntimeService
from mcp_pal_app.services.execution_service import AppExecutionError
from mcp_pal_app.services.profile_service import (
    BuiltinProfileError,
    HarnessProfileInput,
    MCPProfileInput,
)
from mcp_pal_app.services.spec_builder import OneTurnRunDraft
from mcp_pal_app.settings import Settings


class _FalseyHandle:
    def __init__(self, store: SQLiteExecutionStore, execution_id: ExecutionId) -> None:
        self.store = store
        self.execution_id = execution_id

    def __bool__(self) -> bool:
        return False

    def cancel(self) -> None:
        self.store.request_cancel(self.execution_id, reason="test")


class _FalseyKit:
    def __init__(self, store: SQLiteExecutionStore) -> None:
        self.store = store
        self._store = store
        self.closed = 0
        self._count = 0

    def __bool__(self) -> bool:
        return False

    def submit(self, spec: object) -> _FalseyHandle:
        self._count += 1
        execution_id = ExecutionId(f"execution-test-{self._count}")
        ExecutionTraceRecorder(
            self.store,
            execution_id,
            specification=spec.model_dump(mode="json"),
        )
        return _FalseyHandle(self.store, execution_id)

    def close(self) -> None:
        self.closed += 1


def _settings(database: Path) -> Settings:
    return Settings(database_path=str(database), claude_model_ids=["claude-test"])


def _draft(profile_id: str, **updates: object) -> OneTurnRunDraft:
    values: dict[str, object] = {
        "profile_id": profile_id,
        "enabled_server": "server",
        "model": "claude-test",
        "prompt": "draw a box",
        "expected_goal": "a box exists",
        "timeout_seconds": 7.5,
        "metadata": {"caller": "ui"},
    }
    values.update(updates)
    return OneTurnRunDraft.model_validate(values)


def _runtime(tmp_path: Path) -> tuple[AppRuntimeService, _FalseyKit, str]:
    store = SQLiteExecutionStore(tmp_path / "runtime.sqlite")
    kit = _FalseyKit(store)
    runtime = AppRuntimeService(
        _settings(tmp_path / "unused.sqlite"), store=store, kit=kit
    )
    profile = runtime.create_mcp(
        MCPProfileInput(
            name="test",
            config={"mcpServers": {"server": {"type": "stdio", "command": "echo"}}},
        )
    )
    return runtime, kit, profile.record.id


def test_runtime_composes_injected_falsey_resources_and_typed_crud(
    tmp_path: Path,
) -> None:
    runtime, kit, profile_id = _runtime(tmp_path)
    view = runtime.submit(_draft(profile_id))
    assert view.specification is not None
    assert view.snapshot.execution_id == ExecutionId("execution-test-1")
    assert runtime.get(view.snapshot.execution_id).report is not None
    assert runtime.list(limit=10).total == 1
    assert (
        runtime.report(view.snapshot.execution_id, event_limit=1).events_truncated
        is False
    )
    runtime.cancel(view.snapshot.execution_id)
    assert (
        runtime.get(view.snapshot.execution_id).snapshot.outcome
        is ExecutionOutcome.CANCELLED
    )
    bounded = runtime.report(
        view.snapshot.execution_id, after_sequence=-1, event_limit=1
    )
    assert bounded.events_truncated is True
    assert bounded.next_after_sequence == bounded.events[-1].sequence
    runtime.delete(view.snapshot.execution_id)
    assert runtime.list().total == 0
    runtime.close()
    assert kit.closed == 0
    with pytest.raises(RuntimeError, match="closed"):
        runtime.list()
    runtime.close()
    kit.store.close()


def test_runtime_rejects_mismatched_injected_kit_and_registry_resources(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "runtime.sqlite")
    other_store = SQLiteExecutionStore(tmp_path / "other.sqlite")
    kit = _FalseyKit(other_store)
    with pytest.raises(ValueError, match="same runtime resources"):
        AppRuntimeService(_settings(tmp_path / "unused.sqlite"), store=store, kit=kit)
    store.close()
    other_store.close()


def test_runtime_rejects_registry_when_kit_is_injected(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "runtime.sqlite")
    with pytest.raises(ValueError, match="adapter_registry"):
        AppRuntimeService(
            _settings(tmp_path / "unused.sqlite"),
            store=store,
            kit=_FalseyKit(store),
            adapter_registry=HarnessAdapterRegistry(),
        )
    store.close()


def test_runtime_clone_preserves_pinned_one_turn_inputs(tmp_path: Path) -> None:
    runtime, _kit, profile_id = _runtime(tmp_path)
    harness = runtime.create_harness(
        HarnessProfileInput(
            name="acp",
            manifest={"command": "echo"},
            trusted_unsandboxed=True,
        )
    )
    draft = _draft(
        profile_id,
        harness="acp",
        harness_profile_id=harness.record.id,
        model="agent-default",
        tool_mode="agent_default",
        agent_mode_id="plan",
        session_config={"fast": True},
    )
    view = runtime.submit(draft)
    cloned = runtime.clone_draft_inputs(view.snapshot.execution_id)
    assert cloned.profile_id == draft.profile_id
    assert cloned.profile_revision.mode == "pinned"
    specification = view.specification
    assert isinstance(specification, AgentSpec)
    revision_id = cloned.profile_revision.revision_id
    assert revision_id is not None
    assert revision_id.root == specification.metadata["mcp_revision_id"]
    assert cloned.prompt == draft.prompt
    assert cloned.expected_goal == draft.expected_goal
    assert cloned.timeout_seconds == draft.timeout_seconds
    assert cloned.metadata == draft.metadata
    assert cloned.agent_mode_id == draft.agent_mode_id
    assert cloned.session_config == draft.session_config
    runtime.close()


def test_runtime_clear_terminal_history_and_seed_idempotency(tmp_path: Path) -> None:
    runtime, _kit, _profile_id = _runtime(tmp_path)
    store = runtime.store
    runtime.profiles.ensure_builtins()
    assert (
        len([item for item in runtime.list_mcp() if item.record.name == "Excalidraw"])
        == 1
    )
    execution_id = ExecutionId("finished-test")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.finalize(ExecutionOutcome.COMPLETED)
    active_id = ExecutionId("active-test")
    active_recorder = ExecutionTraceRecorder(store, active_id)
    with pytest.raises(AppExecutionError, match="must finish"):
        runtime.clear_terminal_history()
    assert runtime.list().total == 2
    active_recorder.finalize(ExecutionOutcome.COMPLETED)
    assert set(runtime.clear_terminal_history()) == {execution_id, active_id}
    assert runtime.list().total == 0
    runtime.close()
    store.close()


def test_terminal_trace_and_spec_reopen_in_a_second_runtime(tmp_path: Path) -> None:
    runtime, _kit, profile_id = _runtime(tmp_path)
    successful = runtime.submit(_draft(profile_id, prompt="succeed"))
    failed = runtime.submit(_draft(profile_id, prompt="fail"))
    ExecutionTraceRecorder(runtime.store, successful.snapshot.execution_id).finalize(
        ExecutionOutcome.COMPLETED
    )
    ExecutionTraceRecorder(runtime.store, failed.snapshot.execution_id).finalize(
        ExecutionOutcome.FAILED
    )
    runtime.close()
    runtime.store.close()

    reopened_store = SQLiteExecutionStore(tmp_path / "runtime.sqlite")
    reopened = AppRuntimeService(
        _settings(tmp_path / "other.sqlite"),
        store=reopened_store,
        kit=_FalseyKit(reopened_store),
    )
    reopened_success = reopened.view(successful.snapshot.execution_id)
    assert reopened_success.specification is not None
    assert (
        reopened.report(successful.snapshot.execution_id).snapshot.outcome
        is ExecutionOutcome.COMPLETED
    )
    reopened_failed = reopened.report(failed.snapshot.execution_id)
    assert reopened_failed.snapshot.outcome is ExecutionOutcome.FAILED
    assert reopened_success.report.events[-1].kind is EventKind.EXECUTION_FINISHED
    assert reopened_failed.events[-1].kind is EventKind.EXECUTION_FINISHED
    reopened.close()
    reopened_store.close()


def test_builtin_fixed_id_wrong_kind_is_typed_failure(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "collision.sqlite")
    store.create_harness_profile(
        "collision",
        {"command": "echo"},
        profile_id="00000000-0000-4000-8000-000000000001",
    )
    runtime_settings = _settings(tmp_path / "unused.sqlite")
    with pytest.raises(BuiltinProfileError, match="reserved Excalidraw profile ID"):
        AppRuntimeService(runtime_settings, store=store, kit=_FalseyKit(store))
    store.close()


def test_owned_runtime_rejects_settings_only_secret_in_acp_session_config(
    tmp_path: Path,
) -> None:
    canary = "settings-owned-acp-session-canary"
    settings = Settings(
        database_path=str(tmp_path / "owned.sqlite"),
        claude_model_ids=["claude-test"],
        anthropic_api_key=canary,
    )
    runtime = AppRuntimeService(
        settings,
        acp_probe_runner=lambda request: (
            {
                "status": "verified",
                "config_options": [
                    {"id": "credential", "type": "string", "default": "safe"}
                ],
            }
            if request.probe_type is ACPProbeKind.PROTOCOL
            else {"status": "verified"}
        ),
    )
    profile = runtime.create_harness(
        HarnessProfileInput(
            name="acp", manifest={"command": "echo"}, trusted_unsandboxed=True
        )
    )
    revision = runtime.store.resolve_revision(profile.record.id)
    protocol_request = ACPProbeRequest(
        profile_id=profile.record.id,
        revision_id=str(revision.id.root),
        probe_type=ACPProbeKind.PROTOCOL,
    )
    assert (
        asyncio.run(runtime.acp_probes.run(protocol_request)).status.value == "verified"
    )
    request = ACPProbeRequest(
        profile_id=profile.record.id,
        revision_id=str(revision.id.root),
        probe_type=ACPProbeKind.FULL,
        session_config={"credential": canary},
    )
    with pytest.raises(ValueError, match="session configuration"):
        asyncio.run(runtime.acp_probes.run(request))
    assert canary not in repr(runtime.store)
    runtime.close()
    database_bytes = (tmp_path / "owned.sqlite").read_bytes()
    assert canary.encode() not in database_bytes
