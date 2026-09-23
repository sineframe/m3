from pathlib import Path

import pytest

from m3 import MCPTestKit
from m3._types.specs import AgentSpec
from m3.storage import SQLiteExecutionStore
from m3.types import (
    ACPAgent,
    ClaudeCode,
    HTTPServer,
    NativeToolPolicy,
    OpenCode,
    SecretReference,
    StdioServer,
    TextContent,
)
from m3_app.services.profile_service import (
    HarnessProfileInput,
    MCPProfileInput,
    ProfileService,
    ProfileServiceError,
)
from m3_app.services.spec_builder import ExecutionSpecBuilder, OneTurnRunDraft
from m3_app.settings import Settings


def _service(tmp_path: Path) -> tuple[SQLiteExecutionStore, ProfileService]:
    store = SQLiteExecutionStore(tmp_path / "app.sqlite", blob_root=tmp_path / "blobs")
    return store, ProfileService(store)


def _mcp(service: ProfileService) -> str:
    profile_id = service.create_mcp(
        MCPProfileInput(
            name="matrix",
            config={
                "mcpServers": {
                    "stdio": {
                        "command": "echo",
                        "args": ["ok"],
                        "env": {"TOKEN": "${MCP_TOKEN}"},
                        "cwd": "/tmp/work",
                    },
                    "http": {
                        "type": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "${MCP_AUTH}"},
                    },
                }
            },
        )
    ).record.id
    return profile_id


def test_profile_lifecycle_import_export_and_trust(tmp_path: Path) -> None:
    store, service = _service(tmp_path)
    profile = service.create_mcp(
        MCPProfileInput(
            name="one", config={"mcpServers": {"echo": {"command": "echo"}}}
        )
    )
    assert profile.current_revision is not None
    revised = service.add_mcp_revision(
        profile.record.id, {"mcpServers": {"echo": {"command": "printf"}}}
    )
    assert (
        revised.current_revision is not None
        and revised.current_revision.revision_number == 2
    )
    assert (
        service.update_mcp(profile.record.id, description="").record.description == ""
    )
    with pytest.raises(ProfileServiceError, match="name must not be blank"):
        service.update_mcp(profile.record.id, name=" ")
    service.archive_mcp(profile.record.id)
    assert not service.list_mcp()
    assert service.list_mcp(include_archived=True)[0].record.archived
    service.restore_mcp(profile.record.id)
    harness = service.create_harness(
        HarnessProfileInput(
            name="agent", manifest={"command": "agent"}, trusted_unsandboxed=True
        )
    )
    assert harness.current_revision is not None
    assert '"manifest"' in service.export_harness(harness.record.id)
    imported = service.import_harness(
        {"name": "copy", "manifest": {"command": "agent"}, "trusted_unsandboxed": True}
    )
    assert imported.current_revision is not None
    assert imported.current_revision.value["trusted_unsandboxed"] is False
    with pytest.raises(ProfileServiceError, match="acknowledgment"):
        service.add_harness_revision(
            harness.record.id, {"command": "agent"}, trusted_unsandboxed=False
        )
    store.close()


def test_profile_service_reads_and_mutates_same_id_families_independently(
    tmp_path: Path,
) -> None:
    store, service = _service(tmp_path)
    try:
        server = store.create_server_profile(
            "server",
            {"mcpServers": {"echo": {"command": "echo"}}},
            profile_id="shared-profile",
            revision_id="shared-server-revision",
        )
        harness = store.create_harness_profile(
            "harness",
            {"manifest": {"command": "agent"}, "trusted_unsandboxed": True},
            profile_id="shared-profile",
            revision_id="shared-harness-revision",
        )

        assert service.get_mcp(server.id).record.kind == "server"
        assert service.get_harness(harness.id).record.kind == "harness"
        service.add_mcp_revision(
            server.id, {"mcpServers": {"echo": {"command": "printf"}}}
        )
        service.add_harness_revision(
            harness.id,
            {"command": "agent-2"},
            trusted_unsandboxed=True,
        )
        assert (
            service.get_mcp(server.id).current_revision.value["mcpServers"]["echo"][
                "command"
            ]
            == "printf"
        )
        assert (
            service.get_harness(harness.id).current_revision.value["manifest"][
                "command"
            ]
            == "agent-2"
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    ("server", "expected"),
    [("stdio", StdioServer), ("http", HTTPServer)],
)
def test_spec_builder_converts_all_transports_and_secret_references(
    tmp_path: Path, server: str, expected: type
) -> None:
    store, service = _service(tmp_path)
    profile_id = _mcp(service)
    settings = Settings(
        claude_model_ids=["claude-test"], opencode_model_ids=["openrouter/test"]
    )
    spec = ExecutionSpecBuilder(store, settings).build(
        OneTurnRunDraft(
            profile_id=profile_id,
            enabled_server=server,
            model="claude-test",
            prompt="do it",
            expected_goal="it is done",
        )
    )
    value = spec.servers[0].server
    assert isinstance(value, expected)
    assert (
        spec.message is not None
        and isinstance(spec.message.content[0], TextContent)
        and spec.message.content[0].text == "do it"
    )
    assert spec.goal == "it is done"
    if isinstance(value, StdioServer):
        assert (
            isinstance(value.environment["TOKEN"], SecretReference)
            and value.environment["TOKEN"].name == "MCP_TOKEN"
        )
        assert value.args == ("ok",)
    if isinstance(value, HTTPServer):
        assert (
            isinstance(value.headers["Authorization"], SecretReference)
            and value.headers["Authorization"].name == "MCP_AUTH"
        )
    store.close()


@pytest.mark.parametrize("harness", ["claude-code", "opencode"])
def test_spec_builder_harness_and_policy_mapping(tmp_path: Path, harness: str) -> None:
    store, service = _service(tmp_path)
    profile_id = _mcp(service)
    settings = Settings(
        claude_model_ids=["claude-test"], opencode_model_ids=["openrouter/test"]
    )
    model = "claude-test" if harness == "claude-code" else "openrouter/test"
    for mode in ("mcp_only", "mcp_read_only", "full"):
        spec = ExecutionSpecBuilder(store, settings).build(
            OneTurnRunDraft(
                profile_id=profile_id,
                enabled_server="stdio",
                harness=harness,
                model=model,
                prompt="p",
                expected_goal="g",
                tool_mode=mode,
            )
        )
        assert isinstance(
            spec.harness, OpenCode if harness == "opencode" else ClaudeCode
        )
        assert isinstance(spec.tool_policy, NativeToolPolicy)
        assert spec.tool_policy.policy["mode"] == mode
        if mode == "mcp_read_only":
            expected_tools = (
                (
                    "Agent",
                    "Read",
                    "Glob",
                    "Grep",
                    "LSP",
                    "WebFetch",
                    "WebSearch",
                    "ToolSearch",
                    "ListMcpResourcesTool",
                    "ReadMcpResourceTool",
                    "TaskGet",
                    "TaskList",
                    "TaskOutput",
                )
                if harness == "claude-code"
                else ("read", "glob", "grep", "lsp", "webfetch", "websearch")
            )
            assert spec.tool_policy.policy["read_only_tools"] == expected_tools
    opencode = ExecutionSpecBuilder(store, settings).build(
        OneTurnRunDraft(
            profile_id=profile_id,
            enabled_server="stdio",
            harness="opencode",
            model="openrouter/test",
            prompt="p",
            expected_goal="g",
        )
    )
    assert isinstance(opencode.harness, OpenCode)
    assert tuple(opencode.harness.credential_references) == ("OPENROUTER_API_KEY",)
    store.close()


def test_spec_builder_acp_options_and_invalid_selection(tmp_path: Path) -> None:
    store, service = _service(tmp_path)
    profile_id = _mcp(service)
    harness = service.create_harness(
        HarnessProfileInput(
            name="acp",
            manifest={
                "command": "agent",
                "args": ["--stdio"],
                "env": {"KEY": "${AGENT_KEY}"},
            },
            trusted_unsandboxed=True,
        )
    )
    settings = Settings()
    spec = ExecutionSpecBuilder(store, settings).build(
        OneTurnRunDraft(
            profile_id=profile_id,
            enabled_server="stdio",
            harness="acp",
            harness_profile_id=harness.record.id,
            model="agent-default",
            tool_mode="agent_default",
            prompt="p",
            expected_goal="g",
            agent_mode_id="fast",
            session_config={"engine": "x"},
        )
    )
    assert isinstance(spec.harness, ACPAgent) and spec.harness.kind == "acp"
    assert spec.harness.agent_mode_id == "fast"
    assert spec.harness.session_config["engine"] == "x"
    assert isinstance(spec.tool_policy, NativeToolPolicy)
    with pytest.raises(ProfileServiceError, match="selected MCP server"):
        ExecutionSpecBuilder(store, settings).build(
            OneTurnRunDraft(
                profile_id=profile_id,
                enabled_server="missing",
                model="agent-default",
                prompt="p",
                expected_goal="g",
            )
        )
    service.archive_mcp(profile_id)
    with pytest.raises(ProfileServiceError, match="archived"):
        ExecutionSpecBuilder(store, settings).build(
            OneTurnRunDraft(
                profile_id=profile_id,
                enabled_server="stdio",
                model="agent-default",
                prompt="p",
                expected_goal="g",
            )
        )
    store.close()


def test_typed_credentials_and_acp_options_round_trip_through_durable_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "claude-credential-canary"
    monkeypatch.setenv("ANTHROPIC_API_KEY", canary)
    store, service = _service(tmp_path)
    profile_id = _mcp(service)
    harness = service.create_harness(
        HarnessProfileInput(
            name="acp", manifest={"command": "agent"}, trusted_unsandboxed=True
        )
    )
    settings = Settings(claude_model_ids=["claude-test"])
    builder = ExecutionSpecBuilder(store, settings)
    specs = (
        builder.build(
            OneTurnRunDraft(
                profile_id=profile_id,
                enabled_server="stdio",
                model="claude-test",
                prompt="p",
                expected_goal="g",
            )
        ),
        builder.build(
            OneTurnRunDraft(
                profile_id=profile_id,
                enabled_server="stdio",
                harness="acp",
                harness_profile_id=harness.record.id,
                model="agent-default",
                tool_mode="agent_default",
                prompt="p",
                expected_goal="g",
                agent_mode_id="fast",
                session_config={"quality": "high"},
            )
        ),
    )
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        for spec in specs:
            handle = kit.submit(spec)
            command = store.get_command(f"command-{handle.execution_id.root}")
            assert command is not None and isinstance(command.payload["spec"], dict)
            payload = command.payload["spec"]
            assert canary not in str(payload)
            rehydrated = AgentSpec.model_validate(payload)
            assert rehydrated.model_dump(mode="json") == spec.model_dump(mode="json")
            if isinstance(rehydrated.harness, ClaudeCode):
                assert (
                    rehydrated.harness.credential_references["ANTHROPIC_API_KEY"].name
                    == "ANTHROPIC_API_KEY"
                )
            if isinstance(rehydrated.harness, ACPAgent):
                assert rehydrated.harness.agent_mode_id == "fast"
                assert rehydrated.harness.session_config["quality"] == "high"
    finally:
        kit.close()
        store.close()
