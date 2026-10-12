"""Offline capability facts and final-request checks do not start a model."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import AsyncAgentSession
from m3.errors import ModelValidationError, UnsupportedFeature
from m3.harness.codex import (
    CodexHarnessAdapter,
    codex_configuration,
    render_codex_config,
)
from m3.harness.contracts import (
    HarnessAdapterCapabilities,
    HarnessLaunch,
    HarnessStartupError,
)
from m3.harness.native import _server_configuration
from m3.harness.native_capabilities import (
    NativeRuntimeIdentity,
    NativeSupport,
    discover_native_support,
)
from m3.harness.native_settings import milliseconds, validate_support
from m3.harness.opencode import opencode_configuration
from m3.server_group import HarnessServerConfig, ServerGroupSnapshot
from m3.types import (
    ClaudeCode,
    ClaudeCodePermissions,
    Codex,
    CodexPermissionProfile,
    CodexPermissions,
    McpTimeouts,
    NativeFilesystemPermissions,
    NativeNetworkPermissions,
    NativeToolPolicy,
    OpenCode,
    OpenCodePermissions,
    ServerBinding,
    StdioServer,
    TransportKind,
)


def _launch(harness: object, budgets: McpTimeouts | None = None) -> HarnessLaunch:
    budgets = McpTimeouts() if budgets is None else budgets
    server = StdioServer(name="slow", command="fixture", timeouts=budgets)
    spec = AgentSpec(harness=harness, servers=(ServerBinding(server=server),))
    config = HarnessServerConfig(
        key="slow",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection",
        command="fixture",
        timeouts=budgets,
    )
    return HarnessLaunch(spec, ServerGroupSnapshot(), (config,), spec.tool_policy)


def _facts(**kwargs: object) -> NativeSupport:
    values = dict(
        version="fixture",
        executable_digest="digest",
        dialect="legacy",
        platform="darwin-arm64",
        source_revision="audited-source",
        source_url="https://example.invalid/source",
        rpc_parameters=frozenset({"permissions", "allowed_tools", "denied_tools"}),
        reports_active_profile=True,
        permission_modes=frozenset({"plan", "dontAsk"}),
        approval_policies=frozenset({"on-request", "never"}),
        sandboxes=frozenset({"workspace-write", "read-only"}),
    )
    values.update(kwargs)
    return NativeSupport(**values)


def test_named_and_inline_profiles_emit_identical_native_config() -> None:
    profile = CodexPermissionProfile(
        name="demo",
        filesystem=NativeFilesystemPermissions(
            rules={":root": "read", ":project_roots": "write", "/tmp/private": "deny"}
        ),
        network=NativeNetworkPermissions(
            enabled=True, domains={"example.com": "allow"}
        ),
    )
    named = _launch(
        Codex(
            model="fixture",
            permissions=CodexPermissions(profile="demo", profiles=(profile,)),
        ),
        McpTimeouts(startup_seconds=60, request_seconds=25),
    )
    inline = _launch(
        Codex(model="fixture", permissions=CodexPermissions(profile=profile)),
        McpTimeouts(startup_seconds=60, request_seconds=25),
    )
    assert codex_configuration(named) == codex_configuration(inline)
    import tomli

    decoded = tomli.loads(render_codex_config(named))
    assert decoded == codex_configuration(named)
    assert decoded["default_permissions"] == "demo"
    assert decoded["permissions"]["demo"]["filesystem"][":root"] == "read"
    assert decoded["mcp_servers"]["slow"]["startup_timeout_sec"] == 60
    assert decoded["mcp_servers"]["slow"]["tool_timeout_sec"] == 25


def test_native_network_access_emits_explicit_legacy_config_only() -> None:
    launch = _launch(
        Codex(
            model="fixture",
            permissions=CodexPermissions(
                sandbox="workspace-write", network_access=True
            ),
        )
    )
    assert codex_configuration(launch)["sandbox_workspace_write"] == {
        "network_access": True
    }
    validate_support(launch.spec, launch.configurations, _facts())
    invalid = _launch(
        Codex(model="fixture", permissions=CodexPermissions(network_access=True))
    )
    with pytest.raises(ModelValidationError, match="explicit workspace-write"):
        validate_support(invalid.spec, invalid.configurations, _facts())


def test_opencode_emits_milliseconds_for_shared_phase_budget() -> None:
    launch = _launch(
        OpenCode(
            model="fixture", permissions=OpenCodePermissions(rules={"bash": "deny"})
        ),
        McpTimeouts(startup_seconds=45.125),
    )
    config = opencode_configuration(launch)
    assert config["mcp"]["slow"]["timeout"] == 45125
    assert config["permission"] == {"bash": "deny"}
    validate_support(launch.spec, launch.configurations, _facts())


def test_claude_emits_distinct_per_server_native_request_budget() -> None:
    launch = _launch(ClaudeCode(model="fixture"), McpTimeouts(request_seconds=20))
    assert _server_configuration(launch)["mcpServers"]["slow"]["timeout"] == 20000
    validate_support(launch.spec, launch.configurations, _facts())
    tiny = _launch(ClaudeCode(model="fixture"), McpTimeouts(request_seconds=0.5))
    with pytest.raises(ModelValidationError, match="timer range"):
        validate_support(tiny.spec, tiny.configurations, _facts())


@pytest.mark.parametrize("value", [0.0005, 2147483.648, 1e200])
def test_native_timers_never_silently_round_or_clamp(value: float) -> None:
    with pytest.raises(ModelValidationError):
        milliseconds(value)


def test_cached_facts_do_not_authorize_later_unsupported_request() -> None:
    facts = _facts()
    valid = _launch(
        Codex(model="fixture", permissions=CodexPermissions(profile=":read-only"))
    )
    validate_support(valid.spec, valid.configurations, facts)
    invalid = _launch(
        Codex(model="fixture", permissions=CodexPermissions(profile=":invented"))
    )
    with pytest.raises(UnsupportedFeature, match="profile is not supported"):
        validate_support(invalid.spec, invalid.configurations, facts)
    with pytest.raises(UnsupportedFeature, match="cannot select and confirm"):
        validate_support(
            valid.spec,
            valid.configurations,
            replace(facts, reports_active_profile=False),
        )


def test_unknown_config_only_support_fails_closed() -> None:
    launch = _launch(OpenCode(model="fixture"), McpTimeouts(startup_seconds=40))
    with pytest.raises(UnsupportedFeature, match="unknown for this release"):
        validate_support(
            launch.spec, launch.configurations, _facts(source_revision=None)
        )


def test_native_permission_rules_cannot_widen_existing_tool_policy() -> None:
    launch = _launch(
        ClaudeCode(
            model="fixture",
            permissions=ClaudeCodePermissions(
                permission_mode="plan", denied_tools=("mcp__slow__write",)
            ),
        )
    )
    policy = NativeToolPolicy(
        harness="claude-code",
        policy={"mode": "mcp_only", "server": "slow"},
        nonportable_reason="native scope",
    )
    spec = launch.spec.model_copy(update={"tool_policy": policy})
    validate_support(spec, launch.configurations, _facts())
    widened = spec.model_copy(
        update={
            "harness": ClaudeCode(
                model="fixture",
                permissions=ClaudeCodePermissions(allowed_tools=("Bash",)),
            )
        }
    )
    with pytest.raises(ModelValidationError, match="would widen"):
        validate_support(widened, launch.configurations, _facts())


def _probe(
    tmp_path: Path, *, permission: bool = True, profile_identity: bool = True
) -> Path:
    path = tmp_path / "native-probe"
    path.write_text(f"""#!{sys.executable}
import json, os, pathlib, sys
if "M3_CAPABILITY_CANARY" in os.environ:
    raise SystemExit(90)
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.162.1")
else:
    root=pathlib.Path(sys.argv[-1])/"v2"
    root.mkdir(parents=True)
    (root/"ThreadStartParams.json").write_text(json.dumps({{"properties": {{"permissions": {{"type": ["string", "null"]}}}} if {permission!r} else {{}}, "definitions": {{}}}}))
    (root/"ThreadStartResponse.json").write_text(json.dumps({{"properties": {{"activePermissionProfile": {{"type": "object", "properties": {{"id": {{"type": "string" if {profile_identity!r} else "boolean"}}}}}}}}}}))
""")
    path.chmod(0o700)
    return path


@pytest.mark.asyncio
async def test_isolated_exported_schema_discovery_and_digest_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M3_CAPABILITY_CANARY", "must-not-reach-native-query")
    executable = _probe(tmp_path)
    first = await discover_native_support(str(executable), "codex")
    assert first.version == "0.162.1"
    assert first.reports_active_profile
    assert "permissions" in first.rpc_parameters
    _probe(tmp_path, permission=False)
    changed = await discover_native_support(str(executable), "codex")
    assert changed.executable_digest != first.executable_digest
    assert "permissions" not in changed.rpc_parameters
    assert (
        changed.source_revision is None
    )  # A script's hash does not identify its transitive native binary.


@pytest.mark.asyncio
async def test_managed_artifact_identity_is_part_of_discovered_facts(
    tmp_path: Path,
) -> None:
    executable = _probe(tmp_path)
    identity = NativeRuntimeIdentity.from_provenance(
        {"sha256": "a" * 64, "version": "0.162.1", "target": "fixture-target"}
    )
    facts = await discover_native_support(str(executable), "codex", identity)
    assert facts.runtime_identity == identity
    assert facts.source_revision is not None
    with pytest.raises(UnsupportedFeature, match="version does not match"):
        await discover_native_support(
            str(executable), "codex", replace(identity, version="0.160.1")
        )


@pytest.mark.asyncio
async def test_native_receipt_field_requires_string_profile_identity(
    tmp_path: Path,
) -> None:
    executable = _probe(tmp_path, profile_identity=False)
    facts = await discover_native_support(str(executable), "codex")
    assert not facts.reports_active_profile


@pytest.mark.asyncio
async def test_native_capability_rejection_precedes_user_mcp_startup(
    tmp_path: Path,
) -> None:
    executable = _probe(tmp_path)
    launch = _launch(
        Codex(
            model="fixture", permissions=CodexPermissions(approval_policy="on-failure")
        )
    )

    class Manager:
        started = False
        closed = False
        capture = None

        def configurations(self) -> tuple[HarnessServerConfig, ...]:
            return launch.configurations

        async def start(self) -> None:
            self.started = True
            raise AssertionError("unsupported settings started the MCP server")

        async def close(self) -> None:
            self.closed = True

    manager = Manager()
    session = AsyncAgentSession(
        launch.spec,
        CodexHarnessAdapter(executable=str(executable)),
        server_manager=manager,
    )
    with pytest.raises(UnsupportedFeature, match="approval policy is not supported"):
        async with session:
            raise AssertionError("unsupported settings opened a session")
    assert manager.closed and not manager.started


@pytest.mark.asyncio
async def test_default_session_skips_added_native_discovery() -> None:
    launch = _launch(Codex(model="fixture"))

    class Adapter:
        capabilities = HarnessAdapterCapabilities(name="fixture")
        started = False

        async def validate_native_settings(
            self, *args: object, **kwargs: object
        ) -> None:
            raise AssertionError("default session performed native discovery")

        async def start(self, spec: AgentSpec) -> None:
            self.started = True

        async def close(self) -> None:
            pass

    adapter = Adapter()
    async with AsyncAgentSession(launch.spec, adapter):
        assert adapter.started


class _StartupProcess:
    root: Path | None = None

    def __init__(self, active_profile: str | None):
        self.active_profile = active_profile
        self.written: list[dict[str, object]] = []

    async def write(self, payload: dict[str, object]) -> None:
        self.written.append(payload)

    async def next(self, timeout: float | None = None) -> dict[str, object]:
        request = self.written[-1]
        result: dict[str, object] = {}
        if request["method"] == "account/read":
            result = {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}
        elif request["method"] == "thread/start":
            result = {
                "thread": {"id": "thread"},
                "activePermissionProfile": {"id": self.active_profile}
                if self.active_profile is not None
                else None,
            }
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}


@pytest.mark.asyncio
async def test_codex_profile_omits_legacy_sandbox_and_confirms_native_selection() -> (
    None
):
    launch = _launch(
        Codex(
            model="fixture",
            permissions=CodexPermissions(
                profile=CodexPermissionProfile(name="demo"), approval_policy="never"
            ),
        )
    )
    process = _StartupProcess("demo")
    adapter = CodexHarnessAdapter(executable="fixture")
    assert await adapter.initialize(process, launch) == "thread"  # type: ignore[arg-type]
    request = next(
        value for value in process.written if value.get("method") == "thread/start"
    )
    assert request["params"]["permissions"] == "demo"
    assert request["params"]["approvalPolicy"] == "never"
    assert "sandbox" not in request["params"]


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [None, "other"])
async def test_codex_rejects_unconfirmed_permission_profile(active: str | None) -> None:
    launch = _launch(
        Codex(
            model="fixture",
            permissions=CodexPermissions(profile=CodexPermissionProfile(name="demo")),
        )
    )
    adapter = CodexHarnessAdapter(executable="fixture")
    with pytest.raises(HarnessStartupError, match="did not activate"):
        await adapter.initialize(_StartupProcess(active), launch)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_wrapped_help_preserves_all_native_permission_mode_choices(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "native-help"
    executable.write_text(f"""#!{sys.executable}
import sys
if "--version" in sys.argv: print("2.1.296 (Claude Code)")
else: print('--allowedTools --disallowedTools\\n--permission-mode <mode> Mode\\n (choices: "acceptEdits", "auto",\\n "bypassPermissions", "manual",\\n "dontAsk", "plan")\\n --another-option unrelated')
""")
    executable.chmod(0o700)
    facts = await discover_native_support(str(executable), "claude_code")
    assert facts.permission_modes == frozenset(
        {"acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"}
    )
