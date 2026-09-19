from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from m3.services.acp_probes import (
    ACPProbeDimension,
    ACPProbeKind,
    ACPProbeRequest,
    ACPProbeResult,
    ACPProbeStatus,
    JsonValue,
)
from m3.storage import SQLiteExecutionStore
from m3.trace.redaction import RedactionConfig
from m3_app.services.acp_probe_service import ACPProbes


def _service(
    tmp_path: Path, runner: object
) -> tuple[SQLiteExecutionStore, ACPProbes, str, str]:
    store = SQLiteExecutionStore(tmp_path / "acp.sqlite")
    profile = store.create_harness_profile(
        "agent", {"manifest": {"command": "echo"}, "trusted_unsandboxed": True}
    )
    revision = store.resolve_revision(profile.id)
    return store, ACPProbes(store, runner), profile.id, str(revision.id.root)  # type: ignore[arg-type]


def test_real_shaped_protocol_output_is_projected_into_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def raw_protocol(
        _manifest: dict[str, object], _timeout_seconds: float = 30.0
    ) -> dict[str, object]:
        return {
            "status": "verified",
            "agent_info": {"name": "fixture", "version": "1"},
            "agent_capabilities": {"modes": {"availableModes": [{"id": "safe"}]}},
            "config_options": [
                {"id": "quality", "type": "select", "options": [{"value": "high"}]}
            ],
            "modes": {"availableModes": [{"id": "safe", "name": "Safe"}]},
            "frames": [{"payload": {"result": {"sessionId": "s"}}}],
        }

    monkeypatch.setattr(
        "m3_app.services.acp_probe_service.protocol_probe", raw_protocol
    )
    store = SQLiteExecutionStore(tmp_path / "acp.sqlite")
    profile = store.create_harness_profile(
        "agent", {"manifest": {"command": "echo"}, "trusted_unsandboxed": True}
    )
    revision_id = str(store.resolve_revision(profile.id).id.root)
    service = ACPProbes(store)
    profile_id = profile.id
    request = ACPProbeRequest(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    result = asyncio.run(service.run(request))
    assert result.status is ACPProbeStatus.VERIFIED
    assert result.agent_identity is not None and result.agent_identity.name == "fixture"
    assert result.config_options[0]["id"] == "quality"
    assert result.model_dump(mode="json")["evidence"]["modes"] == {
        "availableModes": [{"id": "safe", "name": "Safe"}]
    }
    assert result.evidence["frames"]
    store.close()


def test_protocol_wire_frame_fallback_recovers_snake_case_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def raw_protocol(
        _manifest: dict[str, object], _timeout_seconds: float = 30.0
    ) -> dict[str, object]:
        return {
            "status": "verified",
            "frames": [
                {
                    "payload": {
                        "result": {
                            "agent_info": {"name": "wire-fixture", "version": "2"},
                            "modes": {
                                "current_mode_id": "wire",
                                "available_modes": [{"id": "wire", "name": "Wire"}],
                            },
                            "config_options": [
                                {"id": "wire-option", "type": "boolean"}
                            ],
                        }
                    },
                }
            ],
        }

    monkeypatch.setattr(
        "m3_app.services.acp_probe_service.protocol_probe", raw_protocol
    )
    store = SQLiteExecutionStore(tmp_path / "wire-fallback.sqlite")
    profile = store.create_harness_profile(
        "agent", {"manifest": {"command": "echo"}, "trusted_unsandboxed": True}
    )
    revision_id = str(store.resolve_revision(profile.id).id.root)
    result = asyncio.run(
        ACPProbes(store).run(
            ACPProbeRequest(
                profile_id=profile.id,
                revision_id=revision_id,
                probe_type=ACPProbeKind.PROTOCOL,
            )
        )
    )
    assert (
        result.agent_identity is not None
        and result.agent_identity.name == "wire-fixture"
    )
    assert result.agent_modes[0].id == "wire" and result.current_agent_mode_id == "wire"
    assert result.config_options[0]["id"] == "wire-option"
    store.close()


def test_oversized_protocol_frames_keep_typed_modes_options_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def raw_protocol(
        _manifest: dict[str, object], _timeout_seconds: float = 30.0
    ) -> dict[str, object]:
        return {
            "status": "verified",
            "agent_info": {"name": "fixture", "version": "1"},
            "modes": {
                "currentModeId": "safe",
                "availableModes": [{"id": "safe", "name": "Safe"}],
            },
            "config_options": [
                {"id": "quality", "type": "select", "options": [{"value": "high"}]}
            ],
            "frames": [{"payload": "x" * 100_000}],
        }

    monkeypatch.setattr(
        "m3_app.services.acp_probe_service.protocol_probe", raw_protocol
    )
    store = SQLiteExecutionStore(tmp_path / "oversized.sqlite")
    profile = store.create_harness_profile(
        "agent", {"manifest": {"command": "echo"}, "trusted_unsandboxed": True}
    )
    revision_id = str(store.resolve_revision(profile.id).id.root)
    service = ACPProbes(store)
    request = ACPProbeRequest(
        profile_id=profile.id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    result = asyncio.run(service.run(request))
    assert (
        result.agent_modes[0].id == "safe"
        and result.config_options[0]["id"] == "quality"
    )
    assert "truncation" in result.evidence
    store.close()
    reopened = SQLiteExecutionStore(tmp_path / "oversized.sqlite")
    persisted = reopened.get_acp_probe(result.id)
    assert persisted is not None and persisted.agent_modes[0].id == "safe"
    from m3_app.services.readiness_service import ReadinessService
    from m3_app.settings import Settings

    readiness = ReadinessService(
        Settings(
            database_path=str(tmp_path / "unused.sqlite"),
            claude_executable="missing",
            opencode_executable="missing",
        ),
        reopened,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda _: "/fixture",
    )
    view = readiness.acp_readiness(profile.id)
    assert (
        view is not None
        and view.agent_modes[0].id == "safe"
        and view.session_config_options[0].id == "quality"
    )
    reopened.close()


def test_full_probe_requires_protocol_and_applies_defaults(tmp_path: Path) -> None:
    calls: list[ACPProbeRequest] = []

    def runner(request: ACPProbeRequest) -> dict[str, object]:
        calls.append(request)
        return {
            "status": "verified",
            "agent_identity": {"name": "fixture", "version": "1"},
        }

    store, service, profile_id, revision_id = _service(tmp_path, runner)
    full = ACPProbeRequest(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.FULL
    )
    with pytest.raises(ValueError, match="verified protocol"):
        asyncio.run(service.run(full))
    protocol = ACPProbeRequest(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    asyncio.run(
        service.run(
            protocol,
            runner=lambda _request: {
                "status": "verified",
                "evidence": {
                    "modes": {"availableModes": [{"id": "safe"}]},
                    "config_options": [{"id": "quality", "default": "high"}],
                },
            },
        )
    )
    result = asyncio.run(service.run(full))
    assert result.status is ACPProbeStatus.VERIFIED and calls[0].agent_mode_id == "safe"
    assert calls[0].session_config == {"quality": "high"}
    store.close()


def test_manifest_dispatch_preserves_protocol_deadline_and_passes_full_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol_calls: list[tuple[dict[str, object], tuple[object, ...]]] = []
    full_calls: list[tuple[dict[str, object], dict[str, object]]] = []

    async def fake_protocol(
        manifest: dict[str, object], *args: object
    ) -> dict[str, object]:
        protocol_calls.append((manifest, args))
        return {"status": "verified"}

    async def fake_full(
        manifest: dict[str, object], **kwargs: object
    ) -> dict[str, object]:
        full_calls.append((manifest, kwargs))
        return {"status": "verified"}

    monkeypatch.setattr(
        "m3_app.services.acp_probe_service.protocol_probe", fake_protocol
    )
    monkeypatch.setattr("m3_app.services.acp_probe_service.full_probe", fake_full)
    store = SQLiteExecutionStore(tmp_path / "dispatch.sqlite")
    profile = store.create_harness_profile(
        "agent", {"manifest": {"command": "echo"}, "trusted_unsandboxed": True}
    )
    revision = store.resolve_revision(profile.id)
    service = ACPProbes(store)
    protocol_request = ACPProbeRequest(
        profile_id=profile.id,
        revision_id=str(revision.id.root),
        probe_type=ACPProbeKind.PROTOCOL,
        timeout_seconds=60,
    )
    asyncio.run(service._run_current_manifest(protocol_request))
    assert protocol_calls and protocol_calls[0][1] == ()

    full_request = protocol_request.model_copy(
        update={"probe_type": ACPProbeKind.FULL, "timeout_seconds": 77}
    )
    asyncio.run(service._run_current_manifest(full_request))
    assert full_calls and full_calls[0][1]["timeout_seconds"] == 77
    store.close()


def test_full_probe_rejects_mode_when_protocol_advertises_none(tmp_path: Path) -> None:
    store, service, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )
    protocol = ACPProbeRequest(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    asyncio.run(
        service.run(
            protocol,
            runner=lambda _request: {
                "status": "verified",
                "evidence": {"modes": {"availableModes": []}},
            },
        )
    )
    full = ACPProbeRequest(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="stale",
    )
    with pytest.raises(ValueError, match="not advertised"):
        asyncio.run(service.run(full))
    store.close()


def test_neutral_protocol_unlocks_transport_specific_full_probe(tmp_path: Path) -> None:
    calls: list[ACPProbeRequest] = []

    def runner(request: ACPProbeRequest) -> dict[str, JsonValue]:
        calls.append(request)
        return {"status": "verified"}

    store, service, profile_id, revision_id = _service(tmp_path, runner)
    protocol = ACPProbeRequest(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.PROTOCOL,
        transport="http",
    )
    assert asyncio.run(service.run(protocol)).transport == "stdio"
    full_http = ACPProbeRequest(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        transport="http",
    )
    full_sse = ACPProbeRequest(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        transport="sse",
    )
    assert asyncio.run(service.run(full_http)).transport == "http"
    assert asyncio.run(service.run(full_sse)).transport == "sse"
    assert {item.transport for item in calls} == {"stdio", "http", "sse"}
    store.close()


def test_profile_revision_and_archive_are_validated_before_persistence(
    tmp_path: Path,
) -> None:
    store, service, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )
    other = store.create_harness_profile("other", {"manifest": {"command": "echo"}})
    other_revision = str(store.resolve_revision(other.id).id.root)
    bad = ACPProbeRequest(
        profile_id=profile_id,
        revision_id=other_revision,
        probe_type=ACPProbeKind.PROTOCOL,
    )
    with pytest.raises(ValueError, match="belong"):
        service.request(bad)
    store.archive_profile(profile_id)
    with pytest.raises(ValueError, match="archived"):
        service.request(
            ACPProbeRequest(
                profile_id=profile_id,
                revision_id=revision_id,
                probe_type=ACPProbeKind.PROTOCOL,
            )
        )
    store.close()


def test_sync_facade_rejects_active_loop_without_leaking_coroutine(
    tmp_path: Path,
) -> None:
    store, service, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )

    async def check() -> None:
        with pytest.raises(RuntimeError, match="inside an event loop"):
            service.probe(
                ACPProbeRequest(
                    profile_id=profile_id,
                    revision_id=revision_id,
                    probe_type=ACPProbeKind.PROTOCOL,
                )
            )

    asyncio.run(check())
    store.close()


def test_readiness_projects_identity_match_mismatch_and_missing(tmp_path: Path) -> None:
    store, _, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )
    created = datetime.now(timezone.utc)
    protocol = ACPProbeDimension(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    full = ACPProbeDimension(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="safe",
    )
    store.save_acp_probe(
        ACPProbeResult.model_validate(
            {
                **protocol.model_dump(mode="python"),
                "status": "verified",
                "agent_identity": {"name": "a", "version": "1"},
                "created_at": created,
                "finished_at": created,
            }
        )
    )
    store.save_acp_probe(
        ACPProbeResult.model_validate(
            {
                **full.model_dump(mode="python"),
                "status": "verified",
                "agent_identity": {"name": "b", "version": "1"},
                "created_at": created,
                "finished_at": created,
            }
        )
    )
    from m3_app.services.readiness_service import ReadinessService
    from m3_app.settings import Settings

    settings = Settings(
        database_path=str(tmp_path / "unused.sqlite"),
        claude_executable="missing",
        opencode_executable="missing",
    )
    readiness = ReadinessService(
        settings,
        store,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda _: "/fixture",
    )
    view = readiness.acp_readiness(profile_id)
    assert (
        view is not None
        and not view.full_verified
        and any("Identity changed" in warning for warning in view.warnings)
    )
    newer = created + timedelta(microseconds=1)
    store.save_acp_probe(
        ACPProbeResult.model_validate(
            {
                **full.model_dump(mode="python"),
                "id": "newer",
                "status": "verified",
                "agent_identity": None,
                "created_at": newer,
                "finished_at": newer,
            }
        )
    )
    view = readiness.acp_readiness(profile_id)
    assert (
        view is not None
        and view.full_verified
        and any("identity unavailable" in warning.lower() for warning in view.warnings)
    )
    store.close()


def test_readiness_keeps_latest_verified_dimensions_independent(tmp_path: Path) -> None:
    store, _probe_service, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )
    from m3_app.services.readiness_service import ReadinessService
    from m3_app.settings import Settings

    settings = Settings(
        database_path=str(tmp_path / "unused.sqlite"),
        claude_executable="missing",
        opencode_executable="missing",
    )
    readiness = ReadinessService(
        settings,
        store,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda _: "/fixture",
    )
    now = datetime.now(timezone.utc)
    protocol = ACPProbeDimension(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    matching = ACPProbeDimension(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="matching",
    )
    mismatching = ACPProbeDimension(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="mismatching",
    )
    survivor = ACPProbeDimension(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="survivor",
    )

    def save(
        dimension: ACPProbeDimension,
        probe_id: str,
        status: str,
        identity: object,
        created_at: datetime,
    ) -> None:
        store.save_acp_probe(
            ACPProbeResult.model_validate(
                {
                    **dimension.model_dump(mode="python"),
                    "id": probe_id,
                    "status": status,
                    "agent_identity": identity,
                    "created_at": created_at,
                    "finished_at": created_at,
                }
            )
        )

    save(protocol, "protocol", "verified", {"name": "agent", "version": "1"}, now)
    save(matching, "matching-old", "verified", {"name": "agent", "version": "1"}, now)
    save(
        matching,
        "matching-new-failed",
        "failed",
        {"name": "agent", "version": "1"},
        now + timedelta(seconds=1),
    )
    save(
        mismatching,
        "mismatch",
        "verified",
        {"name": "other", "version": "1"},
        now + timedelta(seconds=2),
    )
    save(
        survivor,
        "survivor",
        "verified",
        {"name": "agent", "version": "1"},
        now + timedelta(seconds=3),
    )
    view = readiness.acp_readiness(profile_id)
    assert view is not None and view.full_verified
    assert (
        view.protocol_verification is not None
        and view.protocol_verification["status"] == "verified"
    )
    assert any("identity mismatch" in warning.lower() for warning in view.warnings)
    assert tuple(item["probe_id"] for item in view.full_verifications) == (
        "survivor",
        "mismatch",
        "matching-new-failed",
    )
    store.close()


def test_readiness_reports_failed_latest_full_verification(tmp_path: Path) -> None:
    store, _probe_service, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )
    now = datetime.now(timezone.utc)
    protocol = ACPProbeDimension(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    full = ACPProbeDimension(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="safe",
    )
    for dimension, probe_id, status in (
        (protocol, "protocol", "verified"),
        (full, "full", "failed"),
    ):
        store.save_acp_probe(
            ACPProbeResult.model_validate(
                {
                    **dimension.model_dump(mode="python"),
                    "id": probe_id,
                    "status": status,
                    "created_at": now,
                    "finished_at": now,
                }
            )
        )
    from m3_app.services.readiness_service import ReadinessService
    from m3_app.settings import Settings

    readiness = ReadinessService(
        Settings(
            database_path=str(tmp_path / "unused.sqlite"),
            claude_executable="missing",
            opencode_executable="missing",
        ),
        store,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda _: "/fixture",
    )
    view = readiness.acp_readiness(profile_id)
    assert (
        view is not None
        and view.verification_status == "failed"
        and not view.full_verified
    )
    store.close()


@pytest.mark.parametrize(
    ("protocol_status", "full_status", "expected_status"),
    [
        (None, "verified", "unverified"),
        ("failed", "verified", "failed"),
        ("verified", None, "unverified"),
        ("verified", "failed", "failed"),
    ],
)
def test_readiness_requires_verified_protocol_for_full_verification(
    tmp_path: Path,
    protocol_status: str | None,
    full_status: str | None,
    expected_status: str,
) -> None:
    store, _probe_service, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )
    now = datetime.now(timezone.utc)
    if protocol_status is not None:
        protocol = ACPProbeDimension(
            profile_id=profile_id,
            revision_id=revision_id,
            probe_type=ACPProbeKind.PROTOCOL,
        )
        store.save_acp_probe(
            ACPProbeResult.model_validate(
                {
                    **protocol.model_dump(mode="python"),
                    "id": "protocol",
                    "status": protocol_status,
                    "created_at": now,
                    "finished_at": now,
                }
            )
        )
    if full_status is not None:
        full = ACPProbeDimension(
            profile_id=profile_id,
            revision_id=revision_id,
            probe_type=ACPProbeKind.FULL,
            agent_mode_id="safe",
        )
        store.save_acp_probe(
            ACPProbeResult.model_validate(
                {
                    **full.model_dump(mode="python"),
                    "id": "full",
                    "status": full_status,
                    "created_at": now,
                    "finished_at": now,
                }
            )
        )
    from m3_app.services.readiness_service import ReadinessService
    from m3_app.settings import Settings

    readiness = ReadinessService(
        Settings(
            database_path=str(tmp_path / "unused.sqlite"),
            claude_executable="missing",
            opencode_executable="missing",
        ),
        store,
        environment={},
        help_probe=lambda *_: None,
        executable_resolver=lambda _: "/fixture",
    )
    view = readiness.acp_readiness(profile_id)
    assert (
        view is not None
        and not view.full_verified
        and view.verification_status == expected_status
    )
    store.close()


def test_cancelled_run_is_persisted_and_reraised(tmp_path: Path) -> None:
    async def hanging(_request: ACPProbeRequest) -> dict[str, JsonValue]:
        await asyncio.sleep(10)
        return {"status": "verified"}

    store, service, profile_id, revision_id = _service(tmp_path, hanging)
    request = ACPProbeRequest(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )

    async def check() -> None:
        task = asyncio.create_task(service.run(request))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        history = service.history(request)
        assert (
            history.latest is not None
            and history.latest.status is ACPProbeStatus.CANCELLED
        )

    asyncio.run(check())
    store.close()


def test_timeout_is_persisted_and_archive_is_inspectable_but_not_selectable(
    tmp_path: Path,
) -> None:
    async def hanging(_request: ACPProbeRequest) -> dict[str, JsonValue]:
        await asyncio.sleep(10)
        return {"status": "verified"}

    store, service, profile_id, revision_id = _service(tmp_path, hanging)
    request = ACPProbeRequest(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.PROTOCOL,
        timeout_seconds=0.01,
    )
    result = asyncio.run(service.run(request))
    assert result.status is ACPProbeStatus.TIMED_OUT
    store.archive_profile(profile_id)
    assert service.history(request).latest is not None
    with pytest.raises(ValueError, match="archived"):
        service.request(request)
    store.close()


def test_full_dimension_validation_and_revision_isolation(tmp_path: Path) -> None:
    store, service, profile_id, revision_id = _service(
        tmp_path, lambda _request: {"status": "verified"}
    )
    protocol = ACPProbeRequest(
        profile_id=profile_id, revision_id=revision_id, probe_type=ACPProbeKind.PROTOCOL
    )
    protocol_result = asyncio.run(
        service.run(
            protocol,
            runner=lambda _request: {
                "status": "verified",
                "evidence": {
                    "config_options": [
                        {
                            "id": "quality",
                            "type": "boolean",
                            "required": True,
                            "default": True,
                        }
                    ],
                    "modes": {"availableModes": [{"id": "safe"}]},
                },
            },
        )
    )
    assert protocol_result.status is ACPProbeStatus.VERIFIED
    with pytest.raises(ValueError, match="mode"):
        asyncio.run(
            service.run(
                ACPProbeRequest(
                    profile_id=profile_id,
                    revision_id=revision_id,
                    probe_type=ACPProbeKind.FULL,
                    agent_mode_id="missing",
                    session_config={"quality": True},
                )
            )
        )
    with pytest.raises(ValueError, match="unadvertised"):
        asyncio.run(
            service.run(
                ACPProbeRequest(
                    profile_id=profile_id,
                    revision_id=revision_id,
                    probe_type=ACPProbeKind.FULL,
                    agent_mode_id="safe",
                    session_config={"other": True},
                )
            )
        )
    with pytest.raises(ValueError, match="boolean"):
        asyncio.run(
            service.run(
                ACPProbeRequest(
                    profile_id=profile_id,
                    revision_id=revision_id,
                    probe_type=ACPProbeKind.FULL,
                    agent_mode_id="safe",
                    session_config={"quality": 1},
                )
            )
        )
    newer = store.add_revision(
        profile_id, {"manifest": {"command": "echo"}, "trusted_unsandboxed": True}
    )
    with pytest.raises(ValueError, match="current"):
        service.request(protocol)
    newer_request = ACPProbeRequest(
        profile_id=profile_id,
        revision_id=str(newer.id.root),
        probe_type=ACPProbeKind.PROTOCOL,
    )
    assert (
        asyncio.run(
            service.run(newer_request, runner=lambda _request: {"status": "failed"})
        ).status
        is ACPProbeStatus.FAILED
    )
    latest = service.latest(protocol)
    assert latest is not None and latest.id == protocol_result.id
    store.close()


def test_runtime_owns_probe_service_and_store_path_redacts(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(
        tmp_path / "acp.sqlite",
        config=RedactionConfig(
            secrets=frozenset({"probe-secret"}), include_environment=False
        ),
    )
    profile = store.create_harness_profile("agent", {"manifest": {"command": "echo"}})
    revision = store.resolve_revision(profile.id)
    service = ACPProbes(
        store,
        lambda _request: {"status": "verified", "evidence": {"token": "probe-secret"}},
    )
    result = asyncio.run(
        service.run(
            ACPProbeRequest(
                profile_id=profile.id,
                revision_id=str(revision.id.root),
                probe_type=ACPProbeKind.PROTOCOL,
            )
        )
    )
    assert "probe-secret" not in repr(result.model_dump(mode="json"))
    from m3_app.services.app_service import AppRuntimeService

    runtime = AppRuntimeService(
        store=store,
        kit=type(
            "Kit",
            (),
            {"store": store, "submit": lambda *_: None, "close": lambda *_: None},
        )(),
        acp_probe_service=service,
    )
    assert runtime.acp_probes is service
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        _ = runtime.acp_probes
    store.close()


def test_acp_service_has_no_legacy_probe_dependency() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "m3_app"
        / "services"
        / "acp_probe_service.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "HarnessProbe" not in text
    assert "persistence.models" not in text
