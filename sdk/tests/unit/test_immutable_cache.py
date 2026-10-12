from __future__ import annotations

import asyncio
import fcntl
import hashlib
import io
import json
import os
import pickle
import select
import subprocess
import sys
import tarfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from m3._cache_settings import (
    cache_override,
    pinned_cache_root,
    resolve_cache,
)
from m3._immutable_cache import RIPGREP, CacheRequest, verified_binary
from m3._startup_deadlines import StartupBudget
from m3._types.specs import AgentSpec
from m3.configuration import Config
from m3.errors import UnsupportedFeature
from m3.harness._launch_credentials import LaunchCredentialBundle
from m3.harness.contracts import HarnessLaunch, UnsupportedHarnessFeature
from m3.harness.opencode import OpenCodeHarnessAdapter
from m3.server_group import ServerGroupSnapshot
from m3.types import (
    CachePolicy,
    Codex,
    HarnessTimeouts,
    OpenCode,
    ServerBinding,
    StdioServer,
)

_NATIVE = Path("/opt/homebrew/Cellar/opencode/1.18.35/bin/opencode")
_PAYLOAD = b"controlled-ripgrep-artifact"
_TOY_KEY = replace(
    RIPGREP,
    binary_size=len(_PAYLOAD),
    binary_sha256=hashlib.sha256(_PAYLOAD).hexdigest(),
).key


def _spec(**changes: object) -> AgentSpec:
    return AgentSpec(
        harness=OpenCode(model="opencode/test", runtime="managed", version="1.18.35"),
        servers=(ServerBinding(server=StdioServer(name="unused", command="cat")),),
        **changes,
    )


def test_cache_settings_precedence_and_explicit_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        os,
        "uname",
        lambda: os.uname_result(("Darwin", "test", "test", "test", "arm64")),
    )
    configuration = Config.model_validate(
        {
            "cache": {"mode": "immutable-seed", "root": "/global"},
            "harnesses": {"opencode": {"cache": {"root": "/harness"}}},
        }
    )
    inherited = resolve_cache(_spec(), configuration)
    assert inherited.cache == CachePolicy(mode="immutable-seed", root="/harness")
    overridden = resolve_cache(
        cache_override(_spec(), CachePolicy(root="/session")), configuration
    )
    assert overridden.cache.root == "/session"
    isolated = resolve_cache(
        cache_override(_spec(), CachePolicy(mode="isolated")), configuration
    )
    assert isolated.cache.mode == "isolated"
    assert pinned_cache_root(isolated.cache, {}) is None
    assert "cache" not in _spec().model_dump()


def test_unsupported_cache_build_fails_before_any_native_preparation() -> None:
    spec = _spec(cache=CachePolicy(mode="immutable-seed")).model_copy(
        update={"harness": Codex(model="test")}
    )
    with pytest.raises(UnsupportedFeature, match="does not support"):
        HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)


def test_cache_root_is_pinned_redacted_and_not_serializable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    environment = {"HOME": "/first-home", "XDG_CACHE_HOME": "/private-cache-canary"}
    bundle = LaunchCredentialBundle(
        (),
        credential_references={},
        resolver_environment=environment,
        _cache_policy=CachePolicy(mode="immutable-seed"),
    )
    try:
        root = bundle.cache_root()
        assert root == "/private-cache-canary/m3/immutable"
        environment["XDG_CACHE_HOME"] = "/rotated"
        assert bundle.cache_root() == root
        assert root in bundle.redaction().secrets
        assert "private-cache-canary" not in repr(bundle)
        request = CacheRequest(Path(root), _NATIVE, "1.18.35")
        assert "private-cache-canary" not in repr(request)
        with pytest.raises(TypeError, match="serialized"):
            pickle.dumps(request)
    finally:
        bundle.close()
    assert (
        pinned_cache_root(
            CachePolicy(mode="immutable-seed", root="~/explicit"), {"HOME": "/frozen"}
        )
        == "/frozen/explicit"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="native allowlist is Darwin arm64")
def test_cache_requires_original_clock_before_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(cache=CachePolicy(mode="immutable-seed", root="/private-seed"))
    launch = HarnessLaunch(spec, ServerGroupSnapshot(), (), spec.tool_policy)
    adapter = OpenCodeHarnessAdapter(executable=str(_NATIVE))

    async def run() -> None:
        with pytest.raises(
            UnsupportedHarnessFeature, match="original startup deadline"
        ):
            await adapter.open(launch)
        assert adapter._cache_owner is None

    asyncio.run(run())


@pytest.mark.skipif(os.name != "posix", reason="owned cache uses POSIX leases")
@pytest.mark.parametrize(
    "mode", ["late", "cancel", "eof", "guard_loss", "accepted_loss"]
)
def test_held_publication_is_fenced_through_deadline_cancellation_and_caller_eof(
    tmp_path: Path, mode: str
) -> None:
    os.mkfifo(tmp_path / "gate", mode=0o600)
    os.mkfifo(tmp_path / "gate-hold", mode=0o600)
    helper = Path(__file__).parents[1] / "fixtures" / "owned_cache_helper.py"
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).parents[1]
                / "fixtures"
                / "cache_publication_controller.py"
            ),
            str(tmp_path),
            mode,
            str(helper),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    lock = os.open(tmp_path / "shared" / (_TOY_KEY + ".lock"), os.O_RDWR)
    try:
        end = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.monotonic() < end
                select.select([], [], [], 0.01)
        assert not (tmp_path / "shared" / _TOY_KEY / "accepted").exists()
        if mode == "late":
            assert (tmp_path / "late").exists()
            assert list((tmp_path / "shared").glob("*.rejected-*"))
        if mode == "guard_loss":
            assert json.loads(result.stdout)["writer_lease_retained"]
        elif mode != "eof":
            assert json.loads(result.stdout)["cleanup_complete"]
    finally:
        os.close(lock)


@pytest.mark.skipif(
    sys.platform != "darwin" or not _NATIVE.is_file(),
    reason="verified real native build unavailable",
)
def test_real_native_find_uses_private_verified_copy_and_rejects_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_name = os.environ.get("M3_TEST_IMMUTABLE_ARCHIVE")
    if archive_name is None:
        pytest.skip("requires the pinned archive for deterministic native transactions")
    archive = Path(archive_name)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == RIPGREP.archive_sha256
    helper = Path(__file__).parents[1] / "fixtures" / "verified_cache_helper.py"
    original = subprocess.Popen

    class ArchivePopen(original):
        def __init__(self, argv, *args, **kwargs):
            if isinstance(argv, list) and "m3._preparation_worker" in argv:
                argv = [argv[0], str(helper), str(archive), *argv[3:]]
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", ArchivePopen)

    async def run() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "probe.txt").write_text("CACHE_PROBE\n")
        seed_root = tmp_path / "shared"
        timings = []
        for index in range(4):
            enabled = index > 0
            spec = _spec(
                cache=CachePolicy(mode="immutable-seed", root=str(seed_root))
                if enabled
                else CachePolicy()
            )
            clock = StartupBudget(HarnessTimeouts(total_seconds=60))
            clock.begin_native()
            launch = HarnessLaunch(
                spec,
                ServerGroupSnapshot(),
                (),
                spec.tool_policy,
                workspace_root=str(workspace),
                _startup_clock=clock,
            )
            adapter = OpenCodeHarnessAdapter(
                executable=str(_NATIVE), environment={"PATH": "/usr/bin:/bin"}
            )
            begin = time.monotonic()
            session = await adapter.open(launch)
            staged = None
            try:
                assert adapter._client is not None
                started = time.monotonic()
                response = await adapter._client.get(
                    "/find", params={"pattern": "CACHE_PROBE"}, timeout=20
                )
                response.raise_for_status()
                assert len(response.json()) == 1
                timings.append(
                    {
                        "enabled": enabled,
                        "open": started - begin,
                        "find": time.monotonic() - started,
                    }
                )
                if enabled:
                    assert adapter._cache_owner is not None
                    resource = adapter._cache_owner._resources[0]
                    staged = resource.root
                    seed = seed_root / RIPGREP.key / "rg"
                    assert verified_binary(seed)
                    assert resource.path.stat().st_ino != seed.stat().st_ino
                    resource.path.chmod(0o600)
                    resource.path.write_bytes(b"session-only-mutation")
                    assert verified_binary(seed)
                else:
                    assert adapter._cache_owner is None
                    assert not seed_root.exists()
            finally:
                await session.close()
                assert staged is None or not staged.exists()
                await adapter.close()
            if index == 2:
                seed = seed_root / RIPGREP.key / "rg"
                seed.chmod(0o600)
                seed.write_bytes(b"corruption")
                seed.chmod(0o500)
        assert list(seed_root.glob("*.corrupt-*"))
        print(json.dumps({"native_cache_timings": timings}))

    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["archive", "member", "binary"])
def test_download_rejects_unverified_artifact(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    from m3 import _immutable_cache as cache

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(cache._MEMBER)
        if invalid == "member":
            member.type = tarfile.SYMTYPE
            member.linkname = "elsewhere"
            archive.addfile(member)
        else:
            member.size = len(_PAYLOAD)
            archive.addfile(member, io.BytesIO(_PAYLOAD))
    raw = buffer.getvalue()
    artifact = replace(
        RIPGREP,
        archive_sha256="invalid"
        if invalid == "archive"
        else hashlib.sha256(raw).hexdigest(),
        binary_size=len(_PAYLOAD),
        binary_sha256="invalid"
        if invalid == "binary"
        else hashlib.sha256(_PAYLOAD).hexdigest(),
    )
    monkeypatch.setattr(cache, "RIPGREP", artifact)
    monkeypatch.setattr(
        cache.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(raw)
    )
    with pytest.raises(cache.ImmutableCacheError, match="verification failed"):
        cache.download_binary(timeout=1)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.uname().machine != "arm64",
    reason="native artifact is Darwin arm64",
)
def test_rejected_native_digest_has_zero_writers_and_completes_cleanup(
    tmp_path: Path,
) -> None:
    from m3._bootstrap_operations import OwnedBootstrapOperations, PrivateOperationError

    native = tmp_path / "unverified-native"
    native.write_bytes(b"unverified executable bytes")
    shared = tmp_path / "shared"
    owner = OwnedBootstrapOperations()
    try:
        with pytest.raises(PrivateOperationError):
            owner.materialize_immutable_cache(
                CacheRequest(shared, native, "1.18.35"), deadline=time.monotonic() + 5
            )
        assert not shared.exists()
    finally:
        owner.close(deadline=time.monotonic() + 5)
    assert owner.cleanup_complete
