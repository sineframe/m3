"""Typed, transport-neutral local readiness for application clients.

Readiness is deliberately a snapshot, not a promise that an execution will
succeed.  This module owns the small amount of host inspection needed by the
application (CLI help, saved OpenCode auth, and ACP manifest availability),
while SDK profile and manifest contracts remain the source of truth.  Nothing
in this module knows about FastAPI, SQLAlchemy, or the legacy application
tables.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from m3.harness.manifest import ManifestValidationError, validate_manifest
from m3.harness.native import probe_help as sdk_probe_help
from m3.services.acp_probes import (
    ACPAgentIdentity,
    ACPProbeDimension,
    ACPProbeKind,
    ACPProbeResult,
    ACPProbeStatus,
)
from m3.storage import ProfileRecord, ProfileRevisionRecord
from m3_app.settings import Settings

# Match the capabilities that the public adapters actually preflight. These
# are intentionally not the obsolete v1 invocation flags.
REQUIRED_CLAUDE_FLAGS = ("stream-json",)
REQUIRED_OPENCODE_FLAGS = ("serve",)
SUPPORTED_OPENCODE_VERSION = "supported-version"
_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
StorageStatus = Literal["connected", "degraded"]


class _ProbeStore(Protocol):
    def list_profiles(
        self, kind: str, *, include_archived: bool = False
    ) -> tuple[ProfileRecord, ...]: ...

    def list_profile_revisions(
        self,
        profile_id: str,
        *,
        kind: Literal["server", "harness"] | None = None,
    ) -> tuple[ProfileRevisionRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class StorageHealthView:
    """Safe result of the application's durable storage check."""

    available: bool
    status: StorageStatus
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.available


@dataclass(frozen=True, slots=True)
class HarnessLimitsView:
    timeout_seconds: float
    max_turns: int | None = None
    max_budget_usd: float | None = None


@dataclass(frozen=True, slots=True)
class BuiltinHarnessView:
    """Immutable descriptor for a built-in native harness."""

    selection_id: str
    kind: str
    harness: str
    name: str
    ready: bool
    models: tuple[str, ...]
    tool_modes: tuple[str, ...]
    limits: HarnessLimitsView
    executable: bool
    required_flags_ok: bool
    missing_flags: tuple[str, ...]
    credential_available: bool
    providers: tuple[str, ...] = ()

    @property
    def api_key(self) -> bool:
        return self.credential_available

    @property
    def api_key_or_saved_auth(self) -> bool:
        return self.credential_available


@dataclass(frozen=True, slots=True)
class ACPAgentModeView:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ACPSessionOptionView:
    id: str
    name: str
    type: str
    required: bool = False
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ACPLocalReadinessView:
    executable: bool
    missing_environment: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ACPProfileReadinessView:
    """Immutable local readiness for one ACP profile revision.

    Probe history is intentionally absent: a profile can be locally runnable
    without claiming that a protocol or full ACP probe has verified it.
    """

    selection_id: str
    kind: str
    harness: str
    name: str
    profile_id: str
    revision_id: str | None
    ready: bool
    local_ready: bool
    trusted_unsandboxed: bool
    archived: bool
    executable: bool
    missing_environment: tuple[str, ...]
    models: tuple[str, ...] = ("agent-default",)
    tool_modes: tuple[str, ...] = ("agent_default",)
    agent_modes: tuple[ACPAgentModeView, ...] = ()
    current_agent_mode_id: str | None = None
    session_config_options: tuple[ACPSessionOptionView, ...] = ()
    protocol_verified: bool = False
    full_verified: bool = False
    verification_status: Literal[
        "unverified", "verified", "identity_mismatch", "failed"
    ] = "unverified"
    agent_identity: Mapping[str, object] | None = None
    protocol_verification: Mapping[str, object] | None = None
    full_verifications: tuple[Mapping[str, object], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str | None]:
        """Stable profile/revision identity for selection and caching."""
        return self.profile_id, self.revision_id

    @property
    def local_ready_details(self) -> ACPLocalReadinessView:
        return ACPLocalReadinessView(self.executable, self.missing_environment)


@dataclass(frozen=True, slots=True)
class ReadinessView:
    """One coherent readiness snapshot made from one Settings/environment view."""

    storage: StorageHealthView
    builtins: tuple[BuiltinHarnessView, ...]
    acp_profiles: tuple[ACPProfileReadinessView, ...]

    @property
    def harnesses(self) -> tuple[BuiltinHarnessView | ACPProfileReadinessView, ...]:
        return self.builtins + self.acp_profiles

    @property
    def ready(self) -> bool:
        # ACP is independently selectable; a ready trusted local profile must
        # remain runnable if both native built-ins are unavailable.
        return self.storage.available and any(item.ready for item in self.harnesses)

    @property
    def run_ready(self) -> bool:
        return self.ready


HelpProbe = Callable[[str, tuple[str, ...]], str | None]
AuthProbe = Callable[[str, tuple[str, ...]], bool]
ExecutableResolver = Callable[[str], str | None]
LifecycleGuard = Callable[[], None]


def _resolve_executable(value: str, *, path: str | None = None) -> str | None:
    if os.path.isabs(value):
        return value if os.path.isfile(value) and os.access(value, os.X_OK) else None
    return shutil.which(value, path=path)


def _minimal_auth_probe(
    executable: str,
    providers: tuple[str, ...],
    *,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Check saved OpenCode auth without inheriting credentials or shell state."""

    with tempfile.TemporaryDirectory(prefix="m3-auth-probe-") as root:
        source = environment if environment is not None else os.environ
        # OpenCode saves auth under the user's normal HOME/XDG locations. Keep
        # only these path controls; never pass API-key or unrelated variables.
        process_environment = {
            "PATH": source.get("PATH", ""),
            "HOME": source.get("HOME", root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            if source.get(name):
                process_environment[name] = source[name]
        try:
            result = subprocess.run(
                [executable, "auth", "list"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                shell=False,
                cwd=root,
                env=process_environment,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        # Provider names are configuration identifiers, not command output
        # exposed to clients. Keep matching bounded and case-insensitive.
        output = (result.stdout + "\n" + result.stderr)[:32_768].lower()
        aliases = {"opencode-go": "opencode go", "opencode": "opencode"}
        return any(
            re.search(
                r"(?<![a-z])"
                + re.escape(
                    aliases.get(provider, provider.replace("-", " ").replace("_", " "))
                )
                + r"(?![a-z])",
                output,
            )
            for provider in providers
        )


def _provider_environment_available(
    provider: str, settings: Settings, environment: Mapping[str, str]
) -> bool:
    values = {
        "opencode": settings.opencode_api_key,
        "opencode-go": settings.opencode_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
    }
    normalized = provider.lower().replace("-", "_").upper()
    return bool(
        values.get(provider.lower())
        or environment.get(f"{normalized}_API_KEY")
        or environment.get(f"{normalized}_AUTH_TOKEN")
    )


def _current_revision(
    profile: ProfileRecord, revisions: tuple[ProfileRevisionRecord, ...]
) -> ProfileRevisionRecord | None:
    if profile.current_revision_id is None:
        return None
    return next(
        (
            revision
            for revision in revisions
            if revision.id == profile.current_revision_id
        ),
        None,
    )


def _identity_key(value: object) -> tuple[str, str] | None:
    """Normalize protocol/full identity shapes for explicit comparison."""
    if isinstance(value, ACPAgentIdentity):
        value = value.model_dump(mode="python")
    if not isinstance(value, Mapping):
        return None
    name = value.get("name")
    version = value.get("version")
    if not isinstance(name, str) and not isinstance(version, str):
        return None
    return (str(name or ""), str(version or ""))


def _identity_view(value: object) -> Mapping[str, object] | None:
    if isinstance(value, ACPAgentIdentity):
        return ACPAgentIdentity.model_validate(value).model_dump(mode="json")
    return value if isinstance(value, Mapping) else None


class ReadinessService:
    """Application-owned readiness facade with deterministic probe seams."""

    def __init__(
        self,
        settings: Settings,
        store: _ProbeStore,
        *,
        environment: Mapping[str, str] | None = None,
        help_probe: HelpProbe = sdk_probe_help,
        auth_probe: AuthProbe | None = None,
        executable_resolver: ExecutableResolver = _resolve_executable,
        lifecycle_guard: LifecycleGuard | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._environment = dict(os.environ if environment is None else environment)
        # Runtime adapters receive these standard provider keys from the
        # application Settings snapshot. Mirror that explicit injection for
        # local ACP manifest checks without ever exposing the values in a
        # readiness view or diagnostic.
        for name, value in {
            "ANTHROPIC_API_KEY": settings.anthropic_api_key,
            "OPENROUTER_API_KEY": settings.openrouter_api_key,
            "OPENCODE_API_KEY": settings.opencode_api_key,
        }.items():
            if value:
                self._environment[name] = value
        self._help_probe = help_probe
        self._auth_probe = auth_probe or (
            lambda executable, providers: _minimal_auth_probe(
                executable, providers, environment=self._environment
            )
        )
        self._resolve_executable = (
            executable_resolver
            if executable_resolver is not _resolve_executable
            else lambda value: _resolve_executable(
                value, path=self._environment.get("PATH")
            )
        )
        self._lifecycle_guard = lifecycle_guard

    def _ensure_open(self) -> None:
        if self._lifecycle_guard is not None:
            self._lifecycle_guard()

    def storage(self) -> StorageHealthView:
        self._ensure_open()
        try:
            self.store.list_profiles("server")
        except Exception:
            return StorageHealthView(False, "degraded", "storage is unavailable")
        return StorageHealthView(True, "connected")

    def _binary(
        self,
        executable: str,
        flags: tuple[str, ...],
        command: tuple[str, ...],
    ) -> tuple[bool, tuple[str, ...], bool]:
        try:
            resolved = self._resolve_executable(executable)
        except Exception:
            resolved = None
        missing = list(flags)
        if resolved is None:
            return False, tuple(missing), False
        try:
            output = self._help_probe(resolved, (*command, "--help"))
        except Exception:
            output = None
        if output is not None:
            missing = [flag for flag in flags if flag not in output]
        return not missing, tuple(missing), True

    def _builtin_views(self) -> tuple[BuiltinHarnessView, ...]:
        claude_ok, claude_missing, claude_exec = self._binary(
            self.settings.claude_executable, REQUIRED_CLAUDE_FLAGS, ()
        )
        providers = tuple(self.settings.opencode_providers())
        _, open_missing, open_exec = self._binary(
            self.settings.opencode_executable,
            REQUIRED_OPENCODE_FLAGS,
            ("serve",),
        )
        if open_exec:
            try:
                version = self._help_probe(
                    self._resolve_executable(self.settings.opencode_executable)
                    or self.settings.opencode_executable,
                    ("--version",),
                )
            except Exception:
                version = None
            if version is None or not re.search(r"\b(?:1|2)\.\d+", version):
                open_missing = tuple(
                    dict.fromkeys((*open_missing, SUPPORTED_OPENCODE_VERSION))
                )
        open_ok = not open_missing
        saved_auth = False
        if open_exec:
            try:
                saved_auth = self._auth_probe(
                    self.settings.opencode_executable, providers
                )
            except Exception:
                saved_auth = False
        auth = bool(
            open_exec
            and (
                any(
                    _provider_environment_available(
                        provider, self.settings, self._environment
                    )
                    for provider in providers
                )
                or saved_auth
            )
        )
        modes = ("mcp_only", "mcp_read_only", "full")
        return (
            BuiltinHarnessView(
                selection_id="builtin:claude-code",
                kind="builtin",
                harness="claude-code",
                name="Claude Code",
                ready=bool(claude_ok and self.settings.anthropic_api_key),
                models=tuple(self.settings.model_ids()),
                tool_modes=modes,
                limits=HarnessLimitsView(
                    self.settings.run_timeout_seconds,
                    self.settings.claude_max_turns,
                    self.settings.claude_max_budget_usd,
                ),
                executable=claude_exec,
                required_flags_ok=claude_ok,
                missing_flags=claude_missing,
                credential_available=bool(self.settings.anthropic_api_key),
            ),
            BuiltinHarnessView(
                selection_id="builtin:opencode",
                kind="builtin",
                harness="opencode",
                name="OpenCode",
                ready=bool(open_ok and auth),
                models=tuple(self.settings.opencode_models()),
                tool_modes=modes,
                limits=HarnessLimitsView(self.settings.run_timeout_seconds),
                executable=open_exec,
                required_flags_ok=open_ok,
                missing_flags=open_missing,
                credential_available=auth,
                providers=providers,
            ),
        )

    def _acp_view(self, profile: ProfileRecord) -> ACPProfileReadinessView:
        selection_id = f"profile:{profile.id}"
        revision: ProfileRevisionRecord | None = None
        warnings: list[str] = []
        try:
            revision = _current_revision(
                profile,
                self.store.list_profile_revisions(profile.id, kind="harness"),
            )
        except Exception:
            warnings.append("Harness revision is unavailable")
        value = dict(revision.value) if revision is not None else {}
        raw_manifest = (
            value.get("manifest")
            if isinstance(value.get("manifest"), Mapping)
            else value
        )
        trusted = bool(value.get("trusted_unsandboxed", False))
        executable = False
        missing: tuple[str, ...] = ()
        valid = False
        if isinstance(raw_manifest, Mapping):
            try:
                manifest = validate_manifest(dict(raw_manifest))["manifest"]
                valid = True
                command = str(manifest["command"])
                try:
                    resolved = self._resolve_executable(command)
                except Exception:
                    resolved = None
                executable = resolved is not None
                refs = sorted(
                    {
                        match.group(1)
                        for item in manifest.get("env", {}).values()
                        if isinstance(item, str)
                        for match in [_ENV_REFERENCE.fullmatch(item)]
                        if match
                    }
                )
                missing = tuple(name for name in refs if name not in self._environment)
            except (ManifestValidationError, KeyError, TypeError, ValueError):
                warnings.append("Harness manifest is invalid")
        else:
            warnings.append("Harness manifest is invalid")
        if not trusted:
            warnings.append("Trusted unsandboxed acknowledgment is required")
        if not executable:
            warnings.append("Executable is unavailable")
        if missing:
            warnings.append("Missing environment variables: " + ", ".join(missing))
        if not valid:
            warnings.append("Harness is not locally ready")
        local_ready = bool(valid and executable and not missing)
        protocol: ACPProbeResult | None = None
        full_values: tuple[ACPProbeResult, ...] = ()
        list_probes = getattr(self.store, "list_acp_probes", None)
        latest_probe = getattr(self.store, "latest_acp_probe", None)
        if revision is not None and callable(list_probes) and callable(latest_probe):
            try:
                protocol = latest_probe(
                    ACPProbeDimension(
                        profile_id=profile.id,
                        revision_id=str(revision.id.root),
                        probe_type=ACPProbeKind.PROTOCOL,
                    )
                )
                full_values = tuple(
                    item
                    for item in list_probes(include_inflight=False)
                    if isinstance(item, ACPProbeResult)
                    and item.profile_id == profile.id
                    and item.revision_id == str(revision.id.root)
                    and item.probe_type is ACPProbeKind.FULL
                )
            except Exception:
                protocol, full_values = None, ()
        protocol_verified = bool(
            protocol is not None and protocol.status is ACPProbeStatus.VERIFIED
        )
        protocol_failed = bool(
            protocol is not None
            and protocol.status
            in {
                ACPProbeStatus.FAILED,
                ACPProbeStatus.TIMED_OUT,
                ACPProbeStatus.CANCELLED,
            }
        )
        protocol_evidence_value = (
            protocol.evidence if protocol_verified and protocol is not None else {}
        )
        protocol_identity = (
            protocol.agent_identity
            if protocol_verified and protocol is not None
            else None
        )
        if protocol_identity is None and isinstance(protocol_evidence_value, Mapping):
            protocol_identity = protocol_evidence_value.get("agent_info")  # type: ignore[assignment]
        latest_by_dimension: dict[str, ACPProbeResult] = {}
        for item in full_values:
            latest_by_dimension.setdefault(item.stable_key, item)
        latest_full_values = tuple(
            sorted(
                latest_by_dimension.values(),
                key=lambda item: (item.created_at, item.id),
                reverse=True,
            )
        )
        compatible: list[ACPProbeResult] = []
        mismatched: list[ACPProbeResult] = []
        missing_identity = False
        for item in latest_full_values:
            if item.status is not ACPProbeStatus.VERIFIED:
                continue
            # A full observation is meaningful only against the latest
            # verified protocol observation for this revision.  Public stores
            # are intentionally allowed to contain imported/forged rows, so
            # readiness must enforce this invariant independently of the
            # application probe service.
            if not protocol_verified:
                continue
            full_identity = item.agent_identity
            protocol_key = _identity_key(protocol_identity)
            full_key = _identity_key(full_identity)
            if (
                protocol_key is not None
                and full_key is not None
                and protocol_key != full_key
            ):
                mismatched.append(item)
            else:
                compatible.append(item)
                missing_identity = (
                    missing_identity or protocol_key is None or full_key is None
                )
        # Legacy v1 kept a verified full probe usable when either side omitted
        # agent identity; retain that behavior but expose a warning.
        full_verified = bool(protocol_verified and compatible)
        identity_mismatch = bool(protocol_verified and mismatched and not compatible)
        if mismatched and not compatible:
            warnings.append("Identity changed; full verification downgraded")
        elif mismatched:
            warnings.append(
                "Some full verification dimensions have an identity mismatch"
            )
        if missing_identity:
            warnings.append("Agent identity unavailable; verification limited")
        if not full_verified:
            warnings.append("Harness is not fully verified")
        protocol_evidence = protocol_evidence_value
        modes_value: object = (
            {
                "availableModes": tuple(
                    mode.model_dump(mode="json") for mode in protocol.agent_modes
                ),
                "currentModeId": protocol.current_agent_mode_id,
            }
            if protocol is not None and protocol.agent_modes
            else {}
        )
        if not modes_value and isinstance(protocol_evidence, Mapping):
            modes_value = protocol_evidence.get("modes", {})
        if (
            not modes_value
            and protocol is not None
            and isinstance(getattr(protocol, "agent_capabilities", {}), Mapping)
        ):
            modes_value = getattr(protocol, "agent_capabilities", {}).get("modes", {})
        raw_modes = (
            modes_value.get(
                "availableModes",
                modes_value.get(
                    "available_modes",
                    modes_value if isinstance(modes_value, list) else [],
                ),
            )
            if isinstance(modes_value, Mapping)
            else modes_value
        )
        agent_modes = tuple(
            ACPAgentModeView(str(item.get("id")), str(item.get("name", item.get("id"))))
            if isinstance(item, Mapping) and item.get("id") is not None
            else ACPAgentModeView(str(item), str(item))
            for item in (raw_modes if isinstance(raw_modes, (list, tuple)) else ())
            if isinstance(item, (Mapping, str, int, float))
        )
        config_value = (
            getattr(protocol, "config_options", ()) if protocol is not None else ()
        )
        if not config_value and isinstance(protocol_evidence, Mapping):
            config_value = protocol_evidence.get("config_options", ())
        options = tuple(
            ACPSessionOptionView(
                id=str(item.get("id", item.get("configId"))),
                name=str(item.get("name", item.get("id", item.get("configId")))),
                type=str(item.get("type", "string")),
                required=bool(item.get("required", False)),
                values=tuple(
                    str(option.get("value", option))
                    if isinstance(option, Mapping)
                    else str(option)
                    for option in item.get("options", ())
                ),
            )
            for item in (
                config_value if isinstance(config_value, (list, tuple)) else ()
            )
            if isinstance(item, Mapping)
            and item.get("id", item.get("configId")) is not None
        )
        full_descriptors = tuple(
            {
                "probe_id": item.id,
                "transport": item.transport,
                "agent_mode_id": item.agent_mode_id,
                "session_config": dict(item.session_config),
                "status": item.status.value,
                "agent_identity": _identity_view(item.agent_identity),
                "evidence": dict(item.evidence),
                "identity_match": (
                    None
                    if _identity_key(protocol_identity) is None
                    or _identity_key(item.agent_identity) is None
                    else _identity_key(protocol_identity)
                    == _identity_key(item.agent_identity)
                ),
            }
            for item in latest_full_values
        )
        ready = bool(local_ready and trusted and not profile.archived)
        latest_full_failed = any(
            item.status
            in {
                ACPProbeStatus.FAILED,
                ACPProbeStatus.TIMED_OUT,
                ACPProbeStatus.CANCELLED,
            }
            for item in latest_full_values
        )
        if full_verified:
            verification_status: Literal[
                "unverified", "verified", "identity_mismatch", "failed"
            ] = "verified"
        elif identity_mismatch:
            verification_status = "identity_mismatch"
        elif protocol_failed or (protocol_verified and latest_full_failed):
            verification_status = "failed"
        else:
            # No protocol, an in-flight protocol, or a verified protocol with
            # no full observation is incomplete rather than failed.
            verification_status = "unverified"
        return ACPProfileReadinessView(
            selection_id=selection_id,
            kind="acp",
            harness="acp",
            name=profile.name,
            profile_id=profile.id,
            revision_id=str(revision.id.root) if revision else None,
            ready=ready,
            local_ready=local_ready,
            trusted_unsandboxed=trusted,
            archived=profile.archived,
            executable=executable,
            missing_environment=missing,
            agent_modes=agent_modes,
            current_agent_mode_id=(
                str(
                    modes_value.get("currentModeId", modes_value.get("current_mode_id"))
                )
                if isinstance(modes_value, Mapping)
                and isinstance(
                    modes_value.get(
                        "currentModeId", modes_value.get("current_mode_id")
                    ),
                    (str, int, float),
                )
                else None
            ),
            session_config_options=options,
            protocol_verified=protocol_verified,
            full_verified=full_verified,
            verification_status=verification_status,
            agent_identity=(
                _identity_view(compatible[0].agent_identity)
                if compatible
                else _identity_view(protocol_identity)
            ),
            protocol_verification=(
                {
                    "probe_id": protocol.id,
                    "status": protocol.status.value,
                    "agent_identity": _identity_view(protocol.agent_identity),
                    "agent_modes": tuple(
                        mode.model_dump(mode="json") for mode in protocol.agent_modes
                    ),
                    "current_agent_mode_id": protocol.current_agent_mode_id,
                    "config_options": tuple(
                        dict(option) for option in protocol.config_options
                    ),
                    "evidence": dict(protocol.evidence),
                }
                if protocol is not None
                else None
            ),
            full_verifications=full_descriptors,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def capabilities(self, *, include_archived: bool = False) -> ReadinessView:
        self._ensure_open()
        storage = self.storage()
        try:
            profiles = self.store.list_profiles(
                "harness", include_archived=include_archived
            )
        except Exception:
            profiles = ()
        acp = tuple(self._acp_view(profile) for profile in profiles)
        return ReadinessView(storage, self._builtin_views(), acp)

    def acp_readiness(self, profile_id: str) -> ACPProfileReadinessView | None:
        """Inspect one profile, including archived profiles, without selecting it."""
        self._ensure_open()
        try:
            profiles = self.store.list_profiles("harness", include_archived=True)
        except Exception:
            return None
        profile = next((item for item in profiles if item.id == profile_id), None)
        return self._acp_view(profile) if profile is not None else None

    snapshot = capabilities


__all__ = [
    "ACPProfileReadinessView",
    "BuiltinHarnessView",
    "HarnessLimitsView",
    "ReadinessService",
    "ReadinessView",
    "StorageHealthView",
]
