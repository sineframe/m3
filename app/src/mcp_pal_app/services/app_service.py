"""Application-owned runtime composition for direct clients.

This is the seam between the UI and the public SDK.  It owns one settings
snapshot, one durable v2 store, one toolkit, and the small typed services that
operate on them; transport adapters should not construct any of these pieces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from mcp_pal import (
    AgentExecutionSpec,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionSpec,
    ExecutionSnapshot,
    MCPTestKit,
    PersistedExecutionReport,
    RevisionId,
    RevisionSelection,
)
from mcp_pal.harness import AcpHarnessAdapter, ClaudeCodeHarnessAdapter, HarnessAdapterRegistry, OpenCodeHarnessAdapter
from mcp_pal.services.acp_probes import Runner
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.trace.redaction import RedactionConfig
from mcp_pal.types import LifecycleState

from mcp_pal_app.services.execution_service import AppExecutionError, AppExecutionService
from mcp_pal_app.services.acp_probe_service import ACPProbeService
from mcp_pal_app.services.profile_service import (
    HarnessProfileInput,
    MCPProfileInput,
    ProfileService,
    ProfileView,
)
from mcp_pal_app.services.spec_builder import ExecutionSpecBuilder, OneTurnRunDraft
from mcp_pal_app.services.readiness_service import ReadinessService, ReadinessView
from mcp_pal_app.settings import Settings


def build_harness_adapter_registry(settings: Settings) -> HarnessAdapterRegistry:
    """Build process adapters with an explicit, non-serialized credential map."""
    credentials = {
        name: value
        for name, value in {
            "ANTHROPIC_API_KEY": settings.anthropic_api_key,
            "OPENROUTER_API_KEY": settings.openrouter_api_key,
            "OPENCODE_API_KEY": settings.opencode_api_key,
        }.items()
        if value
    }
    registry = HarnessAdapterRegistry()

    def selected_environment(harness: object) -> dict[str, str]:
        references = getattr(harness, "credential_references", {})
        names = {
            reference.name
            for reference in references.values()
            if getattr(reference, "source", None) == "environment"
        }
        return {name: credentials[name] for name in names if name in credentials}

    registry.register(
        "claude_code",
        lambda harness: ClaudeCodeHarnessAdapter(
            executable=getattr(harness, "executable", None) or settings.claude_executable,
            environment=selected_environment(harness),
        ),
    )
    registry.register(
        "opencode",
        lambda harness: OpenCodeHarnessAdapter(
            executable=getattr(harness, "executable", None) or settings.opencode_executable,
            environment=selected_environment(harness),
        ),
    )
    registry.register(
        "acp",
        lambda harness: AcpHarnessAdapter(
            manifest=getattr(harness, "manifest", {}),
            # ACP's selected MCP server credentials are supplied in the launch
            # configurations, not the harness value.  The adapter needs this
            # resolver map, while _isolated_acp_env still copies only manifest
            # values explicitly referenced by the child environment.
            environment=credentials,
        ),
    )
    return registry


@dataclass(frozen=True, slots=True)
class ExecutionView:
    """Typed history projection with immutable spec and bounded report."""

    snapshot: ExecutionSnapshot
    specification: ExecutionSpec | None
    report: PersistedExecutionReport


class RuntimeKit(Protocol):
    """Small composition seam accepted by the application runtime.

    Keeping this protocol app-owned allows tests and future UI clients to
    inject a lifecycle-compatible kit without depending on SDK internals.
    """

    @property
    def store(self) -> object: ...

    def submit(self, spec: ExecutionSpec) -> Any: ...

    def close(self) -> None: ...


class ReadinessProvider(Protocol):
    """Typed readiness seam for deterministic application-client tests."""

    def capabilities(self, *, include_archived: bool = False) -> ReadinessView: ...


class AppRuntimeService:
    """Own or use injected application runtime resources with explicit close."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: SQLiteExecutionStore | None = None,
        kit: RuntimeKit | None = None,
        adapter_registry: HarnessAdapterRegistry | None = None,
        readiness_service: ReadinessProvider | None = None,
        acp_probe_service: ACPProbeService | None = None,
        acp_probe_runner: Runner | None = None,
    ) -> None:
        if kit is not None and store is None:
            raise ValueError("an injected kit requires its matching execution store")
        if kit is not None:
            kit_store = getattr(kit, "store", None)
            if kit_store is not store:
                raise ValueError("injected kit and store must be the same runtime resources")
            if adapter_registry is not None:
                raise ValueError("adapter_registry must be supplied when the kit is constructed")
        self.settings = settings if settings is not None else Settings()
        if store is None:
            # Settings credentials are process-only values, but they must be
            # supplied to the owned store's redaction boundary so an ACP
            # session configuration cannot persist one accidentally.  The
            # config itself contains no serialized/logged projection.
            credential_values = (
                self.settings.anthropic_api_key,
                self.settings.openrouter_api_key,
                self.settings.opencode_api_key,
            )
            redaction = RedactionConfig.from_environment(
                secrets=(value for value in credential_values if value)
            )
            self.store = SQLiteExecutionStore(self.settings.database_path, config=redaction)
        else:
            self.store = store
        self._owns_store = store is None
        self._owns_kit = False
        try:
            self.profiles = ProfileService(self.store)
            self.profiles.ensure_builtins()
            registry = (
                adapter_registry
                if adapter_registry is not None
                else build_harness_adapter_registry(self.settings)
            )
            self.kit = kit if kit is not None else MCPTestKit(
                store=self.store,
                adapter_registry=registry,
            )
            self._owns_kit = kit is None
            self.specs = ExecutionSpecBuilder(self.store, self.settings)
            self.executions = AppExecutionService(self.store, self.kit)
            if acp_probe_service is not None and acp_probe_runner is not None:
                raise ValueError("acp_probe_service and acp_probe_runner are mutually exclusive")
            self._acp_probes = acp_probe_service or ACPProbeService(self.store, acp_probe_runner)
            if acp_probe_service is not None and acp_probe_service.store is not self.store:
                raise ValueError("injected ACP probe service and store must be the same runtime resources")
            # Readiness is an application-owned, transport-neutral snapshot
            # facade. It shares this runtime's settings/store/profile seam so
            # direct clients and the eventual UI cannot drift into separate
            # host or persistence views.
            self._readiness = (
                readiness_service
                if readiness_service is not None
                else ReadinessService(self.settings, self.store, lifecycle_guard=self._ensure_open)
            )
            self._closed = False
        except BaseException:
            if self._owns_kit:
                try:
                    self.kit.close()
                except BaseException:
                    pass
            if self._owns_store:
                try:
                    self.store.close()
                except BaseException:
                    pass
            raise

    def submit(self, draft: OneTurnRunDraft) -> ExecutionView:
        self._ensure_open()
        specification = self.specs.build(draft)
        report = self.executions.create(specification)
        return self.get(report.snapshot.execution_id)

    def capabilities(self) -> ReadinessView:
        """Return one typed local readiness snapshot for direct clients."""
        self._ensure_open()
        return self._readiness.capabilities()

    @property
    def acp_probes(self) -> ACPProbeService:
        """Return the lifecycle-owned ACP protocol/full probe service."""
        self._ensure_open()
        return self._acp_probes

    @property
    def readiness(self) -> ReadinessProvider:
        """Return the readiness facade while enforcing runtime lifecycle."""
        self._ensure_open()
        return self._readiness

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("application runtime is closed")

    def view(self, execution_id: ExecutionId | str) -> ExecutionView:
        self._ensure_open()
        report = self.executions.report(execution_id, event_limit=100, artifact_limit=100)
        specification = self.store.get_execution_spec(execution_id)
        return ExecutionView(report.snapshot, specification, report)

    get = view

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: LifecycleState | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
    ) -> ExecutionPage:
        self._ensure_open()
        return self.executions.list(limit=limit, offset=offset, lifecycle=lifecycle, outcome=outcome)

    def report(self, execution_id: ExecutionId | str, *, after_sequence: int = -1, event_limit: int = 100, artifact_limit: int = 100) -> PersistedExecutionReport:
        self._ensure_open()
        return self.executions.report(execution_id, after_sequence=after_sequence, event_limit=event_limit, artifact_limit=artifact_limit)

    def cancel(self, execution_id: ExecutionId | str, reason: str | None = None) -> ExecutionView:
        self._ensure_open()
        self.executions.cancel(execution_id, reason)
        return self.view(execution_id)

    def delete(self, execution_id: ExecutionId | str) -> ExecutionId:
        self._ensure_open()
        return self.executions.delete(execution_id)

    def clear_terminal_history(self) -> tuple[ExecutionId, ...]:
        self._ensure_open()
        # Preflight the complete view before mutating anything.  The legacy UI
        # treats clear-history as an all-or-nothing operation when even one
        # execution is still active.
        snapshots: list[ExecutionSnapshot] = []
        offset = 0
        while True:
            page = self.list(limit=100, offset=offset)
            snapshots.extend(page.items)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break
        if any(snapshot.lifecycle is not LifecycleState.FINISHED for snapshot in snapshots):
            raise AppExecutionError(
                "execution_active",
                "active executions must finish before history can be cleared",
            )
        return tuple(self.delete(snapshot.execution_id) for snapshot in snapshots)

    def clone_draft_inputs(self, execution_id: ExecutionId | str) -> OneTurnRunDraft:
        """Reconstruct a pinned one-turn draft from the immutable submitted spec."""
        view = self.view(execution_id)
        spec = view.specification
        if not isinstance(spec, AgentExecutionSpec):
            raise ValueError("execution does not contain an agent draft")
        metadata = dict(spec.metadata)
        message = next((getattr(block, "text", "") for block in (spec.message.content if spec.message else ())), "")
        harness = spec.harness
        if harness is None:
            raise ValueError("execution harness is unavailable")
        mode = str(metadata.get("tool_mode", "agent_default"))
        selected_model = harness.model
        system_metadata = {
            "mcp_profile_id",
            "mcp_revision_id",
            "enabled_server",
            "expected_goal",
            "tool_mode",
            "harness_profile_id",
            "harness_revision_id",
        }
        user_metadata = {
            key: value for key, value in metadata.items() if key not in system_metadata
        }
        return OneTurnRunDraft(
            profile_id=str(metadata["mcp_profile_id"]),
            profile_revision=self._pinned(str(metadata["mcp_profile_id"]), str(metadata["mcp_revision_id"])),
            enabled_server=str(metadata["enabled_server"]),
            harness={"claude_code": "claude-code", "opencode": "opencode", "acp": "acp"}[harness.kind],
            harness_profile_id=str(metadata["harness_profile_id"]) if metadata.get("harness_profile_id") else None,
            harness_revision=self._pinned(str(metadata["harness_profile_id"]), str(metadata["harness_revision_id"])) if metadata.get("harness_profile_id") and metadata.get("harness_revision_id") else RevisionSelection(mode="latest"),
            model=selected_model,
            prompt=message,
            expected_goal=str(metadata.get("expected_goal", spec.goal or "goal")),
            tool_mode=mode,
            agent_mode_id=getattr(harness, "agent_mode_id", None),
            session_config=dict(getattr(harness, "session_config", {})),
            timeout_seconds=spec.timeout_seconds,
            metadata=user_metadata,
        )

    def _pinned(self, profile_id: str, revision_id: str) -> RevisionSelection:
        revisions = self.profiles.store.list_profile_revisions(profile_id)
        revision = next((item for item in revisions if str(item.id.root) == revision_id), None)
        if revision is None:
            raise ValueError("execution profile revision is unavailable")
        return RevisionSelection(mode="pinned", revision_id=RevisionId(revision_id), revision_number=revision.revision_number)

    # Explicit profile operations keep the UI independent of the persistence
    # implementation and avoid exposing transport-shaped dictionaries.
    def create_mcp(self, value: MCPProfileInput) -> ProfileView:
        return self.profiles.create_mcp(value)

    def list_mcp(self, *, include_archived: bool = False) -> tuple[ProfileView, ...]:
        return self.profiles.list_mcp(include_archived=include_archived)

    def get_mcp(self, profile_id: str) -> ProfileView:
        return self.profiles.get_mcp(profile_id)

    def create_harness(self, value: HarnessProfileInput) -> ProfileView:
        return self.profiles.create_harness(value)

    def list_harness(self, *, include_archived: bool = False) -> tuple[ProfileView, ...]:
        return self.profiles.list_harness(include_archived=include_archived)

    def get_harness(self, profile_id: str) -> ProfileView:
        return self.profiles.get_harness(profile_id)

    def update_mcp(
        self, profile_id: str, *, name: str | None = None, description: str | None = None
    ) -> ProfileView:
        return self.profiles.update_mcp(profile_id, name=name, description=description)

    def add_mcp_revision(self, profile_id: str, config: dict[str, Any]) -> ProfileView:
        return self.profiles.add_mcp_revision(profile_id, config)

    def archive_mcp(self, profile_id: str) -> ProfileView:
        return self.profiles.archive_mcp(profile_id)

    def restore_mcp(self, profile_id: str) -> ProfileView:
        return self.profiles.restore_mcp(profile_id)

    def update_harness(
        self, profile_id: str, *, name: str | None = None, description: str | None = None
    ) -> ProfileView:
        return self.profiles.update_harness(profile_id, name=name, description=description)

    def add_harness_revision(
        self,
        profile_id: str,
        value: HarnessProfileInput | dict[str, Any],
        *,
        trusted_unsandboxed: bool | None = None,
    ) -> ProfileView:
        return self.profiles.add_harness_revision(
            profile_id, value, trusted_unsandboxed=trusted_unsandboxed
        )

    def archive_harness(self, profile_id: str) -> ProfileView:
        return self.profiles.archive_harness(profile_id)

    def restore_harness(self, profile_id: str) -> ProfileView:
        return self.profiles.restore_harness(profile_id)

    def import_harness(self, value: dict[str, Any]) -> ProfileView:
        return self.profiles.import_harness(value)

    def export_harness(self, profile_id: str) -> str:
        return self.profiles.export_harness(profile_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: BaseException | None = None
        self._acp_probes.close()
        if self._owns_kit:
            try:
                self.kit.close()
            except BaseException as exc:
                failure = exc
        if self._owns_store:
            try:
                self.store.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    def __enter__(self) -> "AppRuntimeService":
        self._ensure_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


ApplicationService = AppRuntimeService
AppService = AppRuntimeService

__all__ = [
    "AppRuntimeService",
    "ApplicationService",
    "AppService",
    "ExecutionView",
    "RuntimeKit",
    "ReadinessProvider",
    "build_harness_adapter_registry",
]
