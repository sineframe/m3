"""Black-box coverage for the direct SDK ACP probe runtime seam."""

from __future__ import annotations

import asyncio
import os
import signal
import stat
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from _pid_marker import read_pid, wait_for_pid
from acp_fixture import probe_agent

from m3.harness.acp import full_probe, protocol_probe
from m3.services.acp_probes import ACPProbeKind, ACPProbeRequest, ACPProbeStatus
from m3.storage import SQLiteExecutionStore
from m3_app.services.app_service import AppRuntimeService
from m3_app.services.profile_service import HarnessProfileInput
from m3_app.settings import Settings


class _ReopenedKit:
    """Minimal injected execution kit for inspecting a reopened runtime."""

    def __init__(self, store: SQLiteExecutionStore) -> None:
        self.store = store

    def submit(self, _spec: object) -> None:
        return None

    def close(self) -> None:
        return None


def _ambient_canary_agent(path: Path) -> str:
    path.write_text(
        """import json, os, pathlib, sys
marker = pathlib.Path(sys.argv[1])
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
for line in sys.stdin:
    request = json.loads(line); method = request.get('method'); ident = request.get('id')
    if method == 'initialize':
        marker.write_text(os.environ.get('M3_UNRELATED_CANARY', '<missing>'), encoding='utf-8')
        send({'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':1}})
    elif method == 'session/new':
        send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'canary-session'}})
    elif method == 'session/prompt':
        send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _prompt_behavior_agent(path: Path, marker: Path, behavior: str) -> str:
    path.write_text(
        """import json, os, pathlib, signal, sys, time
marker = sys.argv[1]
behavior = sys.argv[2]
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
def write_pid_marker():
    temporary = marker + '.' + str(os.getpid()) + '.tmp'
    pathlib.Path(temporary).write_text(str(os.getpid()))
    os.replace(temporary, marker)
for line in sys.stdin:
    request = json.loads(line); method = request.get('method'); ident = request.get('id')
    if method == 'initialize':
        if behavior == 'initialize_hang':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            write_pid_marker()
            while True:
                time.sleep(1)
        send({'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':1}})
    elif method == 'session/new':
        send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'probe-session'}})
    elif method == 'session/prompt':
        if behavior == 'hang':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        write_pid_marker()
        if behavior == 'hang':
            while True:
                time.sleep(1)
        time.sleep(float(behavior))
        send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.parametrize(
    ("content", "expected"),
    [("", None), ("not-a-pid", None), ("0", None), ("-42", None), ("123", 123)],
)
def test_pid_marker_is_ready_only_with_positive_pid(
    tmp_path: Path, content: str, expected: int | None
) -> None:
    marker = tmp_path / "agent.pid"
    assert read_pid(marker) is None
    marker.write_text(content, encoding="utf-8")
    assert read_pid(marker) == expected


def test_wait_for_pid_ignores_empty_marker_until_written(tmp_path: Path) -> None:
    marker = tmp_path / "agent.pid"
    marker.write_text("", encoding="utf-8")

    def publish_pid() -> None:
        time.sleep(0.05)
        marker.write_text("123", encoding="utf-8")

    writer = threading.Thread(target=publish_pid)
    writer.start()
    try:
        assert wait_for_pid(marker, timeout=1.0) == 123
    finally:
        writer.join()


def test_acp_probes_do_not_inherit_unrelated_ambient_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both probe entry points launch a real child with an allowlisted env."""
    canary = "ambient-probe-canary"
    monkeypatch.setenv("M3_UNRELATED_CANARY", canary)
    agent = _ambient_canary_agent(tmp_path / "canary-agent.py")
    protocol_marker = tmp_path / "protocol-marker"
    full_marker = tmp_path / "full-marker"

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        protocol = await protocol_probe(
            {"command": sys.executable, "args": [agent, str(protocol_marker)]}
        )
        full = await full_probe(
            {"command": sys.executable, "args": [agent, str(full_marker)]}
        )
        return protocol, full

    protocol, full = asyncio.run(run())
    assert protocol_marker.read_text(encoding="utf-8") == "<missing>"
    assert full_marker.read_text(encoding="utf-8") == "<missing>"
    assert canary not in repr(protocol) and canary not in repr(full)


def test_runtime_sdk_acp_protocol_full_persists_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "runtime-acp.sqlite"
    settings = Settings(
        database_path=str(database),
        claude_executable="missing-claude",
        opencode_executable="missing-opencode",
    )
    agent = probe_agent(tmp_path / "agent.py")
    runtime = AppRuntimeService(settings)
    profile = runtime.create_harness(
        HarnessProfileInput(
            name="runtime-acp",
            manifest={"command": sys.executable, "args": [agent]},
            trusted_unsandboxed=True,
        )
    )
    revision = runtime.store.resolve_revision(profile.record.id)
    protocol_request = ACPProbeRequest(
        profile_id=profile.record.id,
        revision_id=str(revision.id.root),
        probe_type=ACPProbeKind.PROTOCOL,
    )
    protocol = asyncio.run(runtime.acp_probes.run(protocol_request))
    assert protocol.status is ACPProbeStatus.VERIFIED
    assert protocol.agent_identity is not None
    assert protocol.agent_identity.name == "probe-echo"
    assert protocol.agent_capabilities["mcpCapabilities"] == {
        "http": True,
    }
    assert tuple(mode.id for mode in protocol.agent_modes) == ("mode-a",)
    assert protocol.config_options[0]["id"] == "quality"

    full_request = ACPProbeRequest(
        profile_id=profile.record.id,
        revision_id=str(revision.id.root),
        probe_type=ACPProbeKind.FULL,
        agent_mode_id="mode-a",
        session_config={"quality": "high"},
    )
    full = asyncio.run(runtime.acp_probes.run(full_request))
    assert full.status is ACPProbeStatus.VERIFIED
    assert full.agent_identity is not None
    assert full.agent_identity.name == "probe-echo"
    nonce = full.evidence["nonce"]
    calls = full.evidence["calls"]
    assert isinstance(nonce, str)
    assert isinstance(calls, (list, tuple)) and calls
    call = calls[0]
    assert isinstance(call, Mapping)

    arguments = call.get("arguments")
    assert isinstance(arguments, Mapping) and dict(arguments) == {"text": nonce}
    result = call.get("result")
    assert isinstance(result, Mapping)
    content = result.get("content")
    assert isinstance(content, (list, tuple)) and content
    first_content = content[0]
    assert isinstance(first_content, Mapping)
    assert first_content.get("type") == "text" and first_content.get("text") == nonce

    view = runtime.capabilities().acp_profiles
    descriptor = next(item for item in view if item.profile_id == profile.record.id)
    assert descriptor.protocol_verified and descriptor.full_verified
    assert descriptor.agent_identity is not None
    assert descriptor.agent_identity["name"] == "probe-echo"
    assert descriptor.agent_modes[0].id == "mode-a"
    assert descriptor.session_config_options[0].id == "quality"
    assert descriptor.protocol_verification is not None
    assert descriptor.protocol_verification["status"] == "verified"
    full_evidence = descriptor.full_verifications[0].get("evidence")
    assert isinstance(full_evidence, Mapping) and full_evidence.get("nonce") == nonce
    runtime.close()

    reopened_store = SQLiteExecutionStore(database)
    reopened = AppRuntimeService(
        settings, store=reopened_store, kit=_ReopenedKit(reopened_store)
    )
    reopened_descriptor = next(
        item
        for item in reopened.capabilities().acp_profiles
        if item.profile_id == profile.record.id
    )
    assert reopened_descriptor.full_verified
    persisted = reopened.acp_probes.latest(full_request)
    assert persisted is not None and persisted.status is ACPProbeStatus.VERIFIED
    assert tuple(mode.id for mode in persisted.agent_modes) == ("mode-a",)
    assert persisted.evidence["nonce"] == nonce
    assert "HarnessProbe" not in type(reopened.acp_probes).__module__
    assert "persistence.models" not in type(reopened.acp_probes).__module__
    reopened.close()
    reopened_store.close()


def test_full_probe_propagates_requested_turn_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "prompt-started"
    agent = _prompt_behavior_agent(tmp_path / "slow-agent.py", marker, "0.2")
    result = asyncio.run(
        full_probe(
            {
                "command": sys.executable,
                "args": [agent, str(marker), "0.2"],
            },
            timeout_seconds=0.05,
        )
    )
    assert result["status"] == "timed_out"


def test_protocol_probe_has_intrinsic_initialize_deadline(tmp_path: Path) -> None:
    marker = tmp_path / "initialize-started"
    agent = _prompt_behavior_agent(
        tmp_path / "hanging-agent.py", marker, "initialize_hang"
    )
    result = asyncio.run(
        protocol_probe(
            {
                "command": sys.executable,
                "args": [agent, str(marker), "initialize_hang"],
            },
            timeout_seconds=0.05,
        )
    )
    assert result["status"] == "timed_out"
    pid = wait_for_pid(marker)
    deadline = time.monotonic() + 2.0
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_alive(pid)


def test_runtime_protocol_probe_keeps_intrinsic_deadline_with_long_request_timeout(
    tmp_path: Path,
) -> None:
    """The app request may be long, but protocol initialize remains capped at 5s."""
    marker = tmp_path / "service-initialize-started"
    agent = _prompt_behavior_agent(
        tmp_path / "service-hanging-agent.py", marker, "initialize_hang"
    )
    settings = Settings(
        database_path=str(tmp_path / "service-protocol-timeout.sqlite"),
        claude_executable="missing-claude",
        opencode_executable="missing-opencode",
    )
    runtime = AppRuntimeService(settings)
    try:
        profile = runtime.create_harness(
            HarnessProfileInput(
                name="service-timeout-agent",
                manifest={
                    "command": sys.executable,
                    "args": [agent, str(marker), "initialize_hang"],
                },
                trusted_unsandboxed=True,
            )
        )
        revision = runtime.store.resolve_revision(profile.record.id)
        request = ACPProbeRequest(
            profile_id=profile.record.id,
            revision_id=str(revision.id.root),
            probe_type=ACPProbeKind.PROTOCOL,
            timeout_seconds=8.0,
        )
        started = time.monotonic()
        result = asyncio.run(runtime.acp_probes.run(request))
        elapsed = time.monotonic() - started
        assert result.status is ACPProbeStatus.TIMED_OUT
        assert elapsed < 7.0
        pid = wait_for_pid(marker)
        deadline = time.monotonic() + 2.0
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _pid_alive(pid)
    finally:
        runtime.close()


def test_cancelling_full_probe_reaps_uncooperative_acp_process(tmp_path: Path) -> None:
    marker = tmp_path / "prompt-started"
    agent = _prompt_behavior_agent(tmp_path / "hanging-agent.py", marker, "hang")
    pid: int | None = None

    async def run_and_cancel() -> None:
        nonlocal pid
        task = asyncio.create_task(
            full_probe(
                {"command": sys.executable, "args": [agent, str(marker), "hang"]}
            )
        )
        try:
            deadline = time.monotonic() + 2.0
            while (
                ready_pid := read_pid(marker)
            ) is None and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert ready_pid is not None, "ACP fixture never wrote a positive PID"
            pid = ready_pid
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            if pid is not None and _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)

    asyncio.run(run_and_cancel())
    assert pid is not None
    deadline = time.monotonic() + 2.0
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_alive(pid)
