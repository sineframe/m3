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


_CONTROLLER = r"""
import fcntl,hashlib,json,os,select,shutil,signal,subprocess,sys,threading,time
from dataclasses import replace
from pathlib import Path
from m3._bootstrap_operations import OwnedBootstrapOperations,PrivateBytes
from m3._immutable_cache import CacheRequest,RIPGREP

directory=Path(sys.argv[1]); mode=sys.argv[2]; helper=sys.argv[3]
original=subprocess.Popen
def launch(argv,*args,**kwargs):
    if isinstance(argv,list) and 'm3._preparation_worker' in argv:
        argv=[argv[0],helper,mode,str(directory/'armed'),str(directory/'ready'),str(directory/'gate'),str(directory/'late'),*argv[3:]]
    return original(argv,*args,**kwargs)
subprocess.Popen=launch
warm=OwnedBootstrapOperations()
warm.create_capsule(PrivateBytes(b'warm-helper'),deadline=time.monotonic()+10)
warm.close(deadline=time.monotonic()+5)
(directory/'armed').touch()
owner=OwnedBootstrapOperations()
done=threading.Event(); errors=[]
deadline=time.monotonic()+(1 if mode=='late' else 20)
def acquire():
    try:
        owner.materialize_immutable_cache(CacheRequest(directory/'shared',Path('/fixture/native'),'1.18.35'),deadline=deadline)
    except Exception as error:
        errors.append(type(error).__name__)
    finally:
        done.set()
thread=threading.Thread(target=acquire,daemon=True);thread.start()
end=time.monotonic()+5
while not (directory/'ready').exists():
    assert time.monotonic()<end
    done.wait(.005)
if mode=='eof':
    print('held-publication',flush=True)
    os._exit(0)
if mode=='guard_loss':
    ident,job=next(iter(owner._jobs.items()))
    stage=job.root
    guard=owner._worker._groups[ident]
    os.kill(guard,signal.SIGKILL)
    assert done.wait(3)
    owner.close(deadline=time.monotonic()+.2)
    assert not owner.cleanup_complete and stage.exists()
    raw=b'controlled-ripgrep-artifact'
    key=replace(RIPGREP,binary_size=len(raw),binary_sha256=hashlib.sha256(raw).hexdigest()).key
    lock=os.open(directory/'shared'/(key+'.lock'),os.O_RDWR)
    try:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise AssertionError('writer did not retain exclusion after guard death')
        descriptor=os.open(directory/'gate',os.O_WRONLY|os.O_NONBLOCK)
        os.write(descriptor,b'g');os.close(descriptor)
        end=time.monotonic()+5
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.monotonic()<end
                select.select([],[],[],.01)
        assert stage.exists()
        assert not (directory/'shared'/key/'accepted').exists()
        shutil.rmtree(stage) # Fixture teardown after the retained writer lease ended.
    finally:
        os.close(lock)
    print(json.dumps({'quarantined':True,'writer_lease_retained':True}),flush=True)
    sys.exit(0)
if mode=='late':
    assert done.wait(3)
    assert errors
    assert time.monotonic()>=deadline
    descriptor=os.open(directory/'gate',os.O_WRONLY|os.O_NONBLOCK)
    os.write(descriptor,b'g');os.close(descriptor)
else:
    owner.close(deadline=time.monotonic()+3)
assert done.wait(3)
owner.close(deadline=time.monotonic()+5)
assert owner.cleanup_complete
assert errors
assert mode!='late' or (directory/'late').exists()
assert not list((directory/'shared').glob('*/accepted'))
print(json.dumps({'fenced':True,'cleanup_complete':True,'late_rename':(directory/'late').exists()}),flush=True)
"""


@pytest.mark.skipif(os.name != "posix", reason="owned cache uses POSIX leases")
@pytest.mark.parametrize("mode", ["late", "cancel", "eof", "guard_loss"])
def test_held_publication_is_fenced_through_deadline_cancellation_and_caller_eof(
    tmp_path: Path, mode: str
) -> None:
    os.mkfifo(tmp_path / "gate", mode=0o600)
    helper = Path(__file__).parents[1] / "fixtures" / "owned_cache_helper.py"
    result = subprocess.run(
        [sys.executable, "-c", _CONTROLLER, str(tmp_path), mode, str(helper)],
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
