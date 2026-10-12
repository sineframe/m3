"""Offline native support discovery, separate from requested permission decisions."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import UnsupportedFeature
from .native import ProcessOwner, drain_bounded

if TYPE_CHECKING:
    from .._bootstrap_operations import OwnedBootstrapOperations

_QUERY_SECONDS = 15.0
_MAXIMUM_BYTES = 262_144


@dataclass(frozen=True)
class NativeRuntimeIdentity:
    """Pinned identity extracted from a verified managed runtime lease."""

    archive_digest: str
    version: str
    target: str

    @classmethod
    def from_provenance(cls, provenance: Mapping[str, object]) -> NativeRuntimeIdentity:
        digest = provenance.get("sha256")
        version = provenance.get("version")
        target = provenance.get("target")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
            or not isinstance(version, str)
            or re.fullmatch(r"\d+\.\d+\.\d+", version) is None
            or not isinstance(target, str)
            or not target
            or len(target) > 128
        ):
            raise UnsupportedFeature(
                "managed native capability identity is unavailable"
            )
        return cls(digest.lower(), version, target)


@dataclass(frozen=True)
class NativeSupport:
    """Facts from one native executable and an identified native contract."""

    version: str
    executable_digest: str
    dialect: str
    platform: str
    source_revision: str | None
    source_url: str | None
    rpc_parameters: frozenset[str] = frozenset()
    reports_active_profile: bool = False
    permission_modes: frozenset[str] = frozenset()
    approval_policies: frozenset[str] = frozenset()
    sandboxes: frozenset[str] = frozenset()
    runtime_identity: NativeRuntimeIdentity | None = None


# Config-only fields are absent from exported RPC/help schemas. These exact
# releases were audited against their published native source/distribution.
_RELEASE_SOURCES: dict[tuple[str, str], tuple[str, str]] = {
    ("codex", "0.160.1"): (
        "d27764b82f7118f674371e6d6e76271d9d606edb",
        "https://github.com/openai/codex/tree/rust-v0.160.1/codex-rs",
    ),
    ("codex", "0.162.1"): (
        "092d3acd6bec3e3a14bdc7e7a2810ab628ab759d",
        "https://github.com/openai/codex/tree/rust-v0.162.1/codex-rs",
    ),
    ("opencode", "1.18.31"): (
        "014614d35b397775e5d397a490fc72368c894ec2",
        "https://github.com/anomalyco/opencode/tree/014614d35b397775e5d397a490fc72368c894ec2/packages/core/src/v1/config",
    ),
    ("opencode", "1.18.35"): (
        "53d1eabb61e21162157817bf677da0a4ad3332e3",
        "https://github.com/anomalyco/opencode/tree/53d1eabb61e21162157817bf677da0a4ad3332e3/packages/core/src/v1/config",
    ),
}

_CLAUDE_DARWIN_ARM64_SOURCES = {
    "2.1.295": "sha512-qGetxxeqaqHR7Ev5AfTFfYuze0S99d27DV2IuFfjXigY9CKkLUy2jf3cCXjt8y4BAlGyNrqwKmIdrCjwtjAe8g==",
    "2.1.296": "sha512-vqtx3kS3r1hNLMk+N8JMG5J4kcLHIbI3t/NSIdcFQZrRVCFA6NtcxJyn/B+Sf3GxJaFJOCEfoBQXsFo10+Oj9A==",
}

_CACHE: dict[
    tuple[str, str, str, str, str, NativeRuntimeIdentity | None], NativeSupport
] = {}


@dataclass(frozen=True)
class _ExecutableStamp:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int

    @classmethod
    def read(cls, path: Path) -> _ExecutableStamp:
        try:
            status = path.stat()
        except OSError:
            raise UnsupportedFeature(
                "native capability executable is unavailable"
            ) from None
        return cls(
            status.st_dev,
            status.st_ino,
            status.st_size,
            status.st_mtime_ns,
            status.st_ctime_ns,
            status.st_mode,
        )


_ARTIFACT_CACHE: dict[
    tuple[str, Path, str, NativeRuntimeIdentity | None],
    tuple[_ExecutableStamp, NativeSupport],
] = {}


@dataclass(frozen=True, repr=False)
class _OwnedNativeFacts:
    path: str
    support: NativeSupport
    stamp: _ExecutableStamp | None


_OWNED_FACTS_CACHE: dict[
    tuple[str, str, str, NativeRuntimeIdentity | None], _OwnedNativeFacts
] = {}


def _executable_path(executable: str) -> Path:
    found = shutil.which(executable)
    return Path(found if found is not None else executable).resolve()


def _identity(executable: str) -> tuple[Path, str, bool]:
    path = _executable_path(executable)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            magic = stream.read(4)
            digest.update(magic)
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise UnsupportedFeature(
            "native capability executable is unavailable"
        ) from None
    native = (
        magic.startswith(b"\x7fELF")
        or magic.startswith(b"MZ")
        or magic
        in {
            b"\xcf\xfa\xed\xfe",
            b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xfe\xed\xfa\xce",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
            b"\xca\xfe\xba\xbf",
            b"\xbf\xba\xfe\xca",
        }
    )
    return path, digest.hexdigest(), native


def _environment(root: Path) -> dict[str, str]:
    # Help/schema commands never inherit a user configuration or credentials.
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(root),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_DATA_HOME": str(root / "data"),
        "CODEX_HOME": str(root / "codex"),
        "NO_COLOR": "1",
    }


async def _query(
    executable: Path,
    args: tuple[str, ...],
    root: Path,
    *,
    owned_group: bool = False,
    include_stderr: bool = False,
) -> str:
    if owned_group:
        # Only an owned preparation child selects this path. Query descendants
        # remain in its reserved group and cannot outlive accepted publication.
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *args,
            env=_environment(root),
            cwd=root,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        output, errors = await asyncio.gather(
            drain_bounded(process.stdout, maximum=_MAXIMUM_BYTES + 1),
            drain_bounded(process.stderr, maximum=_MAXIMUM_BYTES + 1),
        )
        await process.wait()
        if include_stderr and errors:
            output += b"\n" + errors
        if process.returncode != 0 or len(output) > _MAXIMUM_BYTES:
            raise UnsupportedFeature("native capability query failed")
        return output.decode("utf-8")
    owner = ProcessOwner(root)

    async def read() -> str:
        await owner.spawn([str(executable), *args], _environment(root))
        process = owner.process
        assert process is not None and process.stdout is not None
        output = await drain_bounded(process.stdout, maximum=_MAXIMUM_BYTES + 1)
        await process.wait()
        if process.returncode != 0 or len(output) > _MAXIMUM_BYTES:
            raise UnsupportedFeature("native capability query failed")
        return output.decode("utf-8")

    try:
        return await asyncio.wait_for(read(), timeout=_QUERY_SECONDS)
    except (asyncio.TimeoutError, UnicodeError):
        raise UnsupportedFeature(
            "native capability query did not complete safely"
        ) from None
    finally:
        await owner.terminate()


def _schema(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAXIMUM_BYTES + 1)
        if len(data) > _MAXIMUM_BYTES:
            raise ValueError
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise UnsupportedFeature("native RPC schema is unavailable") from None


def _variants(
    schema: dict[str, object], value: object, depth: int = 0
) -> tuple[dict[str, object], ...]:
    """Read the local nullable/union shapes exported by the native RPC schema."""
    if not isinstance(value, dict) or depth >= 16:
        return ()
    definitions = schema.get("definitions")
    reference = value.get("$ref")
    if (
        isinstance(reference, str)
        and reference.startswith("#/definitions/")
        and isinstance(definitions, dict)
    ):
        return _variants(
            schema, definitions.get(reference.removeprefix("#/definitions/")), depth + 1
        )
    for key in ("anyOf", "oneOf"):
        choices = value.get(key)
        if isinstance(choices, list):
            return tuple(
                variant
                for choice in choices
                for variant in _variants(schema, choice, depth + 1)
            )
    return (value,)


def _string_type(value: dict[str, object]) -> bool:
    kind = value.get("type")
    return kind == "string" or (isinstance(kind, list) and "string" in kind)


def _field_enums(schema: dict[str, object], value: object) -> frozenset[str]:
    values: set[str] = set()
    for variant in _variants(schema, value):
        choices = variant.get("enum")
        if _string_type(variant) and isinstance(choices, list):
            values.update(item for item in choices if isinstance(item, str))
    return frozenset(values)


def _reports_profile(schema: dict[str, object], value: object) -> bool:
    for variant in _variants(schema, value):
        properties = variant.get("properties")
        if isinstance(properties, dict) and any(
            _string_type(identity)
            for identity in _variants(schema, properties.get("id"))
        ):
            return True
    return False


async def discover_native_support(
    executable: str,
    kind: str,
    runtime_identity: NativeRuntimeIdentity | None = None,
    *,
    owned_root: Path | None = None,
) -> NativeSupport:
    """Cache immutable facts by digest, version, dialect, and host platform.

    Callers validate every final request against the returned facts. A cache
    entry never records authorization or a previously accepted request.
    """
    path = _executable_path(executable)
    host = f"{platform.system().lower()}-{platform.machine().lower()}"
    artifact_key = (kind, path, host, runtime_identity)
    stamp = _ExecutableStamp.read(path)
    cached = _ARTIFACT_CACHE.get(artifact_key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    path, digest, native_binary = await asyncio.to_thread(_identity, str(path))
    context = (
        tempfile.TemporaryDirectory(prefix="m3-native-capabilities-")
        if owned_root is None
        else contextlib.nullcontext(str(owned_root))
    )
    with context as directory:
        root = Path(directory)
        reported = await _query(
            path,
            ("--version",),
            root,
            **({"owned_group": True} if owned_root is not None else {}),
        )
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", reported)
        if match is None:
            raise UnsupportedFeature("native runtime version is unavailable")
        version = match.group(1)
        if runtime_identity is not None and runtime_identity.version != version:
            raise UnsupportedFeature(
                "managed native capability version does not match its verified identity"
            )
        dialect = (
            "app-server"
            if kind == "codex"
            else "stream-json"
            if kind == "claude_code"
            else "legacy"
            if version.startswith("1.")
            else "unknown"
        )
        key = (kind, version, digest, dialect, host, runtime_identity)
        cacheable = native_binary or runtime_identity is not None
        if cacheable and key in _CACHE:
            support = _CACHE[key]
            if _ExecutableStamp.read(path) == stamp:
                _ARTIFACT_CACHE[artifact_key] = (stamp, support)
            return support
        source = _RELEASE_SOURCES.get((kind, version))
        if (
            kind == "claude_code"
            and host == "darwin-arm64"
            and version in _CLAUDE_DARWIN_ARM64_SOURCES
        ):
            source = (
                _CLAUDE_DARWIN_ARM64_SOURCES[version],
                f"https://registry.npmjs.org/@anthropic-ai/claude-code-darwin-arm64/{version}",
            )
        if not native_binary and runtime_identity is None:
            source = None
        parameters: frozenset[str] = frozenset()
        active_profile = False
        modes: frozenset[str] = frozenset()
        approvals: frozenset[str] = frozenset()
        sandboxes: frozenset[str] = frozenset()
        if kind == "codex":
            schema_root = root / "schema"
            await _query(
                path,
                (
                    "app-server",
                    "generate-json-schema",
                    "--experimental",
                    "--out",
                    str(schema_root),
                ),
                root,
                **({"owned_group": True} if owned_root is not None else {}),
            )
            request = _schema(schema_root / "v2" / "ThreadStartParams.json")
            response = _schema(schema_root / "v2" / "ThreadStartResponse.json")
            properties = request.get("properties")
            if not isinstance(properties, dict):
                raise UnsupportedFeature("native thread/start schema is unavailable")
            parameters = frozenset(
                {"permissions", "approvalPolicy", "sandbox"}.intersection(properties)
            )
            if not any(
                _string_type(variant)
                for variant in _variants(request, properties.get("permissions"))
            ):
                parameters = parameters - {"permissions"}
            approvals = _field_enums(request, properties.get("approvalPolicy"))
            sandboxes = _field_enums(request, properties.get("sandbox"))
            response_properties = response.get("properties")
            active_profile = isinstance(response_properties, dict) and _reports_profile(
                response, response_properties.get("activePermissionProfile")
            )
        elif kind == "claude_code":
            help_text = await _query(
                path,
                ("--help",),
                root,
                **({"owned_group": True} if owned_root is not None else {}),
            )
            choices = re.search(
                r"--permission-mode(?:(?!\n[ \t]*--).)*?\(choices:\s*([^)]*)\)",
                help_text,
                re.DOTALL,
            )
            if choices is not None:
                modes = frozenset(re.findall(r'"([A-Za-z]+)"', choices.group(1)))
            if "--allowedTools" in help_text and "--disallowedTools" in help_text:
                parameters = frozenset({"allowed_tools", "denied_tools"})
        support = NativeSupport(
            version,
            digest,
            dialect,
            host,
            source[0] if source else None,
            source[1] if source else None,
            parameters,
            active_profile,
            modes,
            approvals,
            sandboxes,
            runtime_identity,
        )
        if cacheable:
            _CACHE[key] = support
            # Never publish a fast entry for a file changed during discovery.
            if _ExecutableStamp.read(path) == stamp:
                _ARTIFACT_CACHE[artifact_key] = (stamp, support)
        return support


async def discover_native_support_receipt(
    executable: str,
    kind: str,
    runtime_identity: NativeRuntimeIdentity | None,
    cached: _OwnedNativeFacts | None,
    root: Path,
) -> _OwnedNativeFacts:
    """Verify a candidate's filesystem identity only inside an owned job."""
    path = _executable_path(executable)
    host = f"{platform.system().lower()}-{platform.machine().lower()}"
    key = (kind, path, host, runtime_identity)
    if (
        cached is not None
        and cached.stamp is not None
        and cached.path == str(path)
        and cached.support.platform == host
        and cached.support.runtime_identity == runtime_identity
    ):
        _ARTIFACT_CACHE[key] = (cached.stamp, cached.support)
    support = await discover_native_support(
        executable, kind, runtime_identity, owned_root=root
    )
    artifact = _ARTIFACT_CACHE.get(key)
    stamp = None
    if artifact is not None and artifact[1] == support:
        if _ExecutableStamp.read(path) == artifact[0]:
            stamp = artifact[0]
    return _OwnedNativeFacts(str(path), support, stamp)


async def _close_owned_query(
    operations: OwnedBootstrapOperations, deadline: float
) -> None:
    # Fence synchronously even if the executor cannot schedule its IPC drain.
    operations.cancel_pending()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(operations.close, deadline=deadline),
            timeout=max(0.0, deadline - time.monotonic()),
        )
    except Exception:
        # The run-owned cleanup registry retains unsettled query jobs.
        pass


async def discover_native_support_owned(
    executable: str,
    kind: str,
    runtime_identity: NativeRuntimeIdentity | None,
    *,
    deadline: float,
) -> NativeSupport:
    """Cache accepted facts, while rechecking identity in every owned request."""
    from pydantic import TypeAdapter

    from .._bootstrap_operations import OwnedBootstrapOperations, PrivateOperationError

    operations = OwnedBootstrapOperations()
    identity = (
        {
            "sha256": runtime_identity.archive_digest,
            "version": runtime_identity.version,
            "target": runtime_identity.target,
        }
        if runtime_identity is not None
        else None
    )
    host = f"{platform.system().lower()}-{platform.machine().lower()}"
    key = (kind, executable, host, runtime_identity)
    cached = _OWNED_FACTS_CACHE.get(key)
    candidate = (
        TypeAdapter(_OwnedNativeFacts).dump_python(cached, mode="json")
        if cached is not None
        else None
    )
    try:
        result = await asyncio.to_thread(
            operations.discover_native_support,
            executable,
            kind,
            identity,
            cached=candidate,
            deadline=deadline,
        )
        facts = TypeAdapter(_OwnedNativeFacts).validate_json(result.consume())
        if facts.stamp is not None:
            _OWNED_FACTS_CACHE[key] = facts
        else:
            _OWNED_FACTS_CACHE.pop(key, None)
        return facts.support
    except PrivateOperationError:
        raise UnsupportedFeature("owned native capability discovery failed") from None
    finally:
        await _close_owned_query(operations, deadline)


@dataclass(frozen=True)
class NativeProbeResult:
    output: str
    identity: tuple[str, int, int, int, int]


async def probe_native_owned(
    executable: str, args: tuple[str, ...], *, deadline: float
) -> NativeProbeResult:
    from pydantic import TypeAdapter

    from .._bootstrap_operations import OwnedBootstrapOperations

    operations = OwnedBootstrapOperations()
    try:
        result = await asyncio.to_thread(
            operations.probe_native, executable, args, deadline=deadline
        )
        return TypeAdapter(NativeProbeResult).validate_json(result.consume())
    finally:
        await _close_owned_query(operations, deadline)
