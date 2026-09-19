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
    redact_probe,
    run_acp_probe,
)
from m3.storage import (
    InMemoryExecutionStore,
    SQLiteExecutionStore,
    StorageConflict,
)
from m3.trace.redaction import RedactionConfig


def _result(
    dimension: ACPProbeDimension,
    *,
    probe_id: str,
    status: ACPProbeStatus,
    created_at: datetime,
) -> ACPProbeResult:
    return ACPProbeResult.model_validate(
        {
            **dimension.model_dump(mode="python"),
            "id": probe_id,
            "status": status,
            "created_at": created_at,
            "finished_at": created_at,
        }
    )


def test_protocol_dimension_is_neutral_and_config_is_normalize() -> None:
    first = ACPProbeDimension(
        profile_id="p",
        revision_id="r",
        probe_type=ACPProbeKind.PROTOCOL,
        transport="STDIO",
        agent_mode_id="mode",
        session_config={"b": 2, "a": 1},
    )
    second = ACPProbeDimension(
        profile_id="p",
        revision_id="r",
        probe_type=ACPProbeKind.PROTOCOL,
        transport="stdio",
    )
    assert first.agent_mode_id is None and dict(first.session_config) == {}
    assert first.stable_key == second.stable_key
    full_a = ACPProbeDimension(
        profile_id="p",
        revision_id="r",
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="m",
        session_config={"b": 2, "a": 1},
    )
    full_b = full_a.model_copy(update={"session_config": {"a": 1, "b": 2}})
    assert full_a.stable_key == full_b.stable_key


@pytest.mark.parametrize(
    "store_factory", [InMemoryExecutionStore, SQLiteExecutionStore]
)
def test_store_latest_is_exact_dimension_and_sqlite_reopens(
    tmp_path: Path, store_factory: object
) -> None:
    store = (
        store_factory()
        if store_factory is InMemoryExecutionStore
        else store_factory(tmp_path / "probes.sqlite")
    )  # type: ignore[operator]
    if isinstance(store, SQLiteExecutionStore):
        profile = store.create_harness_profile(
            "agent", {"manifest": {"command": "echo"}}
        )
        revision = store.resolve_revision(profile.id)
        profile_id, revision_id = profile.id, str(revision.id.root)
    else:
        profile_id, revision_id = "p", "r"
    dimension = ACPProbeDimension(
        profile_id=profile_id,
        revision_id=revision_id,
        probe_type=ACPProbeKind.FULL,
        transport="stdio",
        agent_mode_id="m",
        session_config={"x": 1},
    )
    now = datetime.now(timezone.utc)
    store.save_acp_probe(
        _result(
            dimension, probe_id="older", status=ACPProbeStatus.VERIFIED, created_at=now
        )
    )
    store.save_acp_probe(
        _result(
            dimension,
            probe_id="newer",
            status=ACPProbeStatus.FAILED,
            created_at=now + timedelta(seconds=1),
        )
    )
    other = dimension.model_copy(update={"transport": "http"})
    store.save_acp_probe(
        _result(
            other,
            probe_id="other",
            status=ACPProbeStatus.VERIFIED,
            created_at=now + timedelta(seconds=2),
        )
    )
    with pytest.raises(StorageConflict):
        store.save_acp_probe(
            _result(
                other, probe_id="older", status=ACPProbeStatus.VERIFIED, created_at=now
            )
        )
    latest = store.latest_acp_probe(dimension)
    other_latest = store.latest_acp_probe(other)
    assert latest is not None and latest.id == "newer"
    assert other_latest is not None and other_latest.id == "other"
    store.close()
    if isinstance(store, SQLiteExecutionStore):
        reopened = SQLiteExecutionStore(tmp_path / "probes.sqlite")
        assert reopened.get_acp_probe("newer").status is ACPProbeStatus.FAILED
        reopened.close()


def test_runner_whitelists_output_and_preserves_request_identity() -> None:
    request = ACPProbeRequest(
        profile_id="p",
        revision_id="r",
        probe_type=ACPProbeKind.PROTOCOL,
        timeout_seconds=1,
    )

    async def runner(_request: ACPProbeRequest) -> dict[str, JsonValue]:
        return {
            "status": "queued",
            "profile_id": "evil",
            "id": "evil",
            "created_at": "evil",
            "evidence": {"safe": True},
        }

    result = asyncio.run(run_acp_probe(request, runner))
    assert result.status is ACPProbeStatus.FAILED
    assert (
        result.profile_id == "p" and result.revision_id == "r" and result.id != "evil"
    )
    assert result.created_at != "evil"
    assert result.evidence == {"safe": True}


def test_runner_timeout_and_redaction() -> None:
    request = ACPProbeRequest(
        profile_id="p",
        revision_id="r",
        probe_type=ACPProbeKind.PROTOCOL,
        timeout_seconds=0.01,
    )

    async def slow(_request: ACPProbeRequest) -> dict[str, JsonValue]:
        await asyncio.sleep(1)
        return {}

    result = asyncio.run(run_acp_probe(request, slow))
    assert result.status is ACPProbeStatus.TIMED_OUT and result.finished_at is not None
    dimension = ACPProbeDimension(
        profile_id="p", revision_id="r", probe_type=ACPProbeKind.PROTOCOL
    )
    safe = redact_probe(
        _result(
            dimension,
            probe_id="safe",
            status=ACPProbeStatus.VERIFIED,
            created_at=datetime.now(timezone.utc),
        ).model_copy(update={"evidence": {"token": "secret"}}),
        RedactionConfig(secrets=frozenset({"secret"}), include_environment=False),
    )
    dumped = safe.model_dump(mode="json")
    assert "secret" not in repr(dumped) and dumped["evidence"]["token"] == "[REDACTED]"


def test_real_shaped_timeout_status_is_normalized() -> None:
    request = ACPProbeRequest(
        profile_id="p",
        revision_id="r",
        probe_type=ACPProbeKind.PROTOCOL,
        timeout_seconds=1,
    )

    async def runner(_request: ACPProbeRequest) -> dict[str, JsonValue]:
        return {"status": "failed", "error": "timed_out"}

    result = asyncio.run(run_acp_probe(request, runner))
    assert result.status is ACPProbeStatus.TIMED_OUT


def test_hostile_config_options_are_bounded_to_typed_empty_options() -> None:
    dimension = ACPProbeDimension(
        profile_id="p", revision_id="r", probe_type=ACPProbeKind.PROTOCOL
    )
    result = _result(
        dimension,
        probe_id="options",
        status=ACPProbeStatus.VERIFIED,
        created_at=datetime.now(timezone.utc),
    ).model_copy(
        update={"config_options": ({"id": "quality", "description": "x" * 100_000},)}
    )
    safe = redact_probe(
        result, RedactionConfig(secrets=frozenset(), include_environment=False)
    )
    assert safe.config_options == ()
    assert "truncation" in safe.evidence


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_session_secret_is_rejected_before_write(
    tmp_path: Path, store_kind: str
) -> None:
    config = RedactionConfig(
        secrets=frozenset({"session-canary"}), include_environment=False
    )
    store = (
        InMemoryExecutionStore(config=config)
        if store_kind == "memory"
        else SQLiteExecutionStore(tmp_path / "secret.sqlite", config=config)
    )
    if isinstance(store, SQLiteExecutionStore):
        profile = store.create_harness_profile(
            "agent", {"manifest": {"command": "echo"}}
        )
        profile_id, revision_id = (
            profile.id,
            str(store.resolve_revision(profile.id).id.root),
        )
    else:
        profile_id, revision_id = "p", "r"
    result = _result(
        ACPProbeDimension(
            profile_id=profile_id,
            revision_id=revision_id,
            probe_type=ACPProbeKind.FULL,
            session_config={"quality": "session-canary"},
        ),
        probe_id="secret",
        status=ACPProbeStatus.VERIFIED,
        created_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="contains a secret"):
        store.save_acp_probe(result)
    assert store.get_acp_probe("secret") is None
    store.close()
