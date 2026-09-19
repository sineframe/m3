from __future__ import annotations

import inspect
from unittest.mock import Mock

import pytest

from m3 import MCPTestKit, ServerBinding, StdioServer
from m3.types import (
    FullToolPolicy,
    NativeToolPolicy,
    RestrictiveToolPolicy,
    RevisionSelection,
    ServerProfileRef,
    UserMessage,
)


def _server() -> StdioServer:
    return StdioServer(name="shipping", command="echo")


@pytest.fixture
def kit() -> MCPTestKit:
    value = MCPTestKit(embedded_worker=False)
    try:
        yield value
    finally:
        value.close()


def test_agents_expand_models_and_trials_without_io(kit: MCPTestKit) -> None:
    selected = kit.agents(
        [{"harness": "opencode", "models": ["opencode/a", "opencode/b"]}],
        trials=2,
    )
    assert [(item.model, item.trial) for item in selected] == [
        ("opencode/a", 1),
        ("opencode/a", 2),
        ("opencode/b", 1),
        ("opencode/b", 2),
    ]


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"harness": "opencode", "models": []}, "models must be a non-empty list"),
        (
            {"harness": "opencode", "models": ["m"], "unsupported": True},
            "unsupported fields",
        ),
        ({"harness": "acp", "models": ["m"]}, "ACP selection requires"),
    ],
)
def test_agent_selection_rejects_invalid_entries(
    kit: MCPTestKit, entry: dict[str, object], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        kit.agents([entry])


def test_saved_profile_selection_rejects_unknown_and_duplicate_entries(
    kit: MCPTestKit,
) -> None:
    profile = {"profile_id": "profile-1", "revision": {"mode": "latest"}}
    with pytest.raises(ValueError, match="saved harness profile"):
        kit.agents([{"harness_profile": profile, "models": ["unexpected"]}])
    with pytest.raises(ValueError, match="duplicate saved harness profile"):
        kit.agents(
            [
                {"harness_profile": profile},
                {"harness_profile": profile},
            ]
        )


def test_saved_profile_name_is_preserved_in_selection_identity(kit: MCPTestKit) -> None:
    profile = {"profile_id": "profile-1", "revision": {"mode": "latest"}}
    selected = kit.agents(
        [
            {"harness_profile": profile, "name": "primary"},
            {"harness_profile": profile, "name": "fallback"},
        ]
    )
    assert [agent.name for agent in selected] == ["primary", "fallback"]


def test_selection_validates_credential_names_and_acp_manifest_without_io(
    kit: MCPTestKit,
) -> None:
    with pytest.raises(ValueError, match="environment names"):
        kit.agents(
            [
                {
                    "harness": "opencode",
                    "models": ["vendor/model"],
                    "credential_env": {"BAD-NAME": "SOURCE"},
                }
            ]
        )
    with pytest.raises(ValueError, match="invalid ACP manifest"):
        kit.agents(
            [
                {
                    "harness": "acp",
                    "models": ["fixture"],
                    "manifest": {"protocol": "wrong", "command": "fixture"},
                }
            ]
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("executable", "", "executable"),
        ("provider", "", "provider"),
        ("dialect", "v3", "dialect"),
    ],
)
def test_selection_validates_optional_harness_fields(
    kit: MCPTestKit, field: str, value: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        kit.agents(
            [
                {
                    "harness": "opencode",
                    "models": ["vendor/model"],
                    field: value,
                }
            ]
        )


def test_explicit_credential_source_is_checked_only_when_building_execution_spec(
    monkeypatch: pytest.MonkeyPatch, kit: MCPTestKit
) -> None:
    monkeypatch.delenv("MISSING_SELECTION_KEY", raising=False)
    agent = kit.agents(
        [
            {
                "harness": "opencode",
                "models": ["vendor/model"],
                "credential_env": {"VENDOR_API_KEY": "MISSING_SELECTION_KEY"},
            }
        ]
    )[0]
    with pytest.raises(ValueError, match="MISSING_SELECTION_KEY"):
        agent._spec(UserMessage(content="x"), server=_server())


def test_named_duplicate_configurations_are_allowed_but_exact_duplicates_are_not(
    kit: MCPTestKit,
) -> None:
    assert (
        len(
            kit.agents(
                [
                    {"harness": "opencode", "models": ["m"], "name": "one"},
                    {"harness": "opencode", "models": ["m"], "name": "two"},
                ]
            )
        )
        == 2
    )
    with pytest.raises(ValueError, match="duplicate agent selection"):
        kit.agents(
            [
                {"harness": "opencode", "models": ["m"], "name": "one"},
                {"harness": "opencode", "models": ["m"], "name": "one"},
            ]
        )


def test_profile_selection_preserves_server_alias_and_profile_metadata(
    kit: MCPTestKit,
) -> None:
    agent = kit.agents(
        [
            {
                "harness_profile": {
                    "profile_id": "profile-1",
                    "revision": {"mode": "latest"},
                }
            }
        ]
    )[0]
    spec = agent._spec(
        UserMessage(content="x"), server={"alias": "orders", "server": _server()}
    )
    assert spec.harness_profile.profile_id.root == "profile-1"
    assert spec.servers[0].alias == "orders"
    assert spec.metadata["m3.matrix.harness"] == "profile"


def test_run_builder_uses_full_policy_and_identity_metadata(kit: MCPTestKit) -> None:
    agent = kit.agents([{"harness": "opencode", "models": ["opencode/a"]}], trials=2)[1]
    spec = agent._spec(UserMessage(content="ignored"), server=_server())
    assert isinstance(spec.tool_policy, FullToolPolicy)
    assert dict(spec.metadata)["trial"] == 2
    assert spec.harness.provider == "opencode"


def test_explicit_tools_are_restrictive_and_empty_denies_all(kit: MCPTestKit) -> None:
    agent = kit.agents([{"harness": "codex", "models": ["gpt"]}])[0]
    message = UserMessage(content="x")
    assert isinstance(
        agent._spec(message, server=_server(), tools=[]).tool_policy,
        RestrictiveToolPolicy,
    )
    assert (
        agent._spec(message, server=_server(), tools=[]).tool_policy.allowed_tools == ()
    )
    assert isinstance(
        agent._spec(message, server=_server()).tool_policy, FullToolPolicy
    )
    with pytest.raises(ValueError, match="tools and tool_policy"):
        agent._spec(
            message,
            server=_server(),
            tools=[],
            tool_policy=FullToolPolicy(acknowledge_risk=True),
        )


def test_advanced_execution_options_are_forwarded(kit: MCPTestKit) -> None:
    agent = kit.agents([{"harness": "opencode", "models": ["m"]}])[0]
    spec = agent._spec(
        UserMessage(content="x"),
        server=_server(),
        timeout=3,
        goal="goal",
        workspace={"kind": "temporary", "acknowledge_risk": False},
        metadata={"owner": "test"},
    )
    assert spec.timeout_seconds == 3
    assert spec.goal == "goal"
    assert spec.metadata["owner"] == "test"


def test_agent_execution_deadline_defaults_and_can_be_disabled(kit: MCPTestKit) -> None:
    agent = kit.agents([{"harness": "opencode", "models": ["m"]}])[0]
    message = UserMessage(content="x")
    assert agent._spec(message, server=_server()).timeout_seconds == 180.0
    assert agent._spec(message, server=_server(), timeout=None).timeout_seconds is None


def test_claude_uses_server_scope_and_rejects_exact_tools(kit: MCPTestKit) -> None:
    agent = kit.agents([{"harness": "claude", "models": ["claude/a"]}])[0]
    message = UserMessage(content="x")
    spec = agent._spec(message, server=_server())
    assert isinstance(spec.tool_policy, NativeToolPolicy)
    with pytest.raises(Exception, match="exact tool"):
        agent._spec(message, server=_server(), tools=["shipping:x"])


def test_claude_profile_server_binding_uses_profile_server_name(
    kit: MCPTestKit,
) -> None:
    agent = kit.agents([{"harness": "claude", "models": ["claude/a"]}])[0]
    binding = {
        "profile": {
            "profile_id": "server-profile",
            "server_name": "orders",
            "revision": {"mode": "latest"},
        },
    }
    spec = agent._spec(UserMessage(content="x"), server=binding)
    assert spec.tool_policy.policy["server"] == "orders"


def test_saved_harness_profile_uses_profile_server_name_for_tools(
    kit: MCPTestKit,
) -> None:
    agent = kit.agents(
        [
            {
                "harness_profile": {
                    "profile_id": "harness-profile",
                    "revision": {"mode": "latest"},
                },
            }
        ]
    )[0]
    binding = ServerBinding(
        profile=ServerProfileRef(
            profile_id="server-profile",
            server_name="orders",
            revision=RevisionSelection(mode="latest"),
        )
    )
    spec = agent._spec(UserMessage(content="x"), server=binding, tools=["orders:quote"])
    assert spec.tool_policy.allowed_tools == ("orders:quote",)
    with pytest.raises(ValueError, match="bound server"):
        agent._spec(UserMessage(content="x"), server=binding, tools=["wrong:quote"])


def test_explicit_harness_tool_scope_prefers_binding_alias_over_profile_name(
    kit: MCPTestKit,
) -> None:
    agent = kit.agents([{"harness": "opencode", "models": ["opencode/a"]}])[0]
    binding = ServerBinding(
        alias="orders-alias",
        profile=ServerProfileRef(
            profile_id="server-profile",
            server_name="orders",
            revision=RevisionSelection(mode="latest"),
        ),
    )
    spec = agent._spec(
        UserMessage(content="x"), server=binding, tools=["orders-alias:quote"]
    )
    assert spec.tool_policy.allowed_tools == ("orders-alias:quote",)
    with pytest.raises(ValueError, match="bound server"):
        agent._spec(UserMessage(content="x"), server=binding, tools=["orders:quote"])


def test_explicit_credential_names_are_checked_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch, kit: MCPTestKit
) -> None:
    monkeypatch.delenv("MY_PROVIDER_KEY", raising=False)
    agent = kit.agents(
        [
            {
                "harness": "opencode",
                "models": ["vendor/model"],
                "credential_env": {"VENDOR_API_KEY": "MY_PROVIDER_KEY"},
            }
        ]
    )[0]
    with pytest.raises(ValueError, match="MY_PROVIDER_KEY") as error:
        agent.run("x", server=_server())
    assert "sentinel" not in str(error.value)


def test_custom_credential_mapping_keeps_known_provider_default(
    monkeypatch: pytest.MonkeyPatch, kit: MCPTestKit
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "ambient")
    monkeypatch.setenv("VENDOR_KEY", "vendor")
    agent = kit.agents(
        [
            {
                "harness": "opencode",
                "models": ["opencode/model"],
                "credential_env": {"VENDOR_API_KEY": "VENDOR_KEY"},
            }
        ]
    )[0]
    spec = agent._spec(UserMessage(content="x"), server=_server())
    assert set(spec.harness.credential_references) == {
        "OPENCODE_API_KEY",
        "VENDOR_API_KEY",
    }


def test_custom_mapping_overrides_known_default_target(
    monkeypatch: pytest.MonkeyPatch, kit: MCPTestKit
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "ambient")
    monkeypatch.setenv("MY_OPENAI_KEY", "mapped")
    agent = kit.agents(
        [
            {
                "harness": "codex",
                "models": ["gpt/model"],
                "credential_env": {"OPENAI_API_KEY": "MY_OPENAI_KEY"},
            }
        ]
    )[0]
    spec = agent._spec(UserMessage(content="x"), server=_server())
    assert spec.harness.credential_references["OPENAI_API_KEY"].name == "MY_OPENAI_KEY"


def test_pi_openai_codex_route_uses_existing_login_directory(
    monkeypatch: pytest.MonkeyPatch, kit: MCPTestKit
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "/tmp/pi-login")
    agent = kit.agents([{"harness": "pi", "models": ["openai-codex/model"]}])[0]
    spec = agent._spec(UserMessage(content="x"), server=_server())
    assert (
        spec.harness.credential_references["PI_CODING_AGENT_DIR"].name
        == "PI_CODING_AGENT_DIR"
    )


def test_public_method_defaults_and_async_expansion(kit: MCPTestKit) -> None:
    agent = kit.agents([{"harness": "codex", "models": ["gpt"]}])[0]
    assert inspect.signature(agent.run).parameters["tools"].default is None
    assert inspect.signature(agent.submit).parameters["tools"].default is None
    assert inspect.signature(agent.session).parameters["tools"].default is None
    async_kit = __import__("m3.async_api", fromlist=["AsyncMCPTestKit"]).AsyncMCPTestKit
    async_value = async_kit(embedded_worker=False)
    try:
        assert (
            len(async_value.agents([{"harness": "codex", "models": ["gpt"]}], trials=2))
            == 2
        )
    finally:
        import asyncio

        asyncio.run(async_value.aclose())


def test_public_selection_methods_preserve_run_submit_and_session_semantics() -> None:
    class FakeKit:
        def __init__(self) -> None:
            self.run = Mock(return_value="run-result")
            self.submit = Mock(return_value="submit-handle")
            self.agent_session = Mock(return_value="session")

    fake = FakeKit()
    from m3._agent_selection import expand

    agent = expand(fake, [{"harness": "opencode", "models": ["vendor/model"]}])[0]
    server = _server()
    assert agent.run("hello", server=server) == "run-result"
    assert agent.submit("hello", server=server) == "submit-handle"
    assert agent.session(server=server) == "session"
    assert fake.run.call_args.args[0].message == UserMessage(content="hello")
    assert fake.submit.call_args.args[0].message == UserMessage(content="hello")
    assert fake.agent_session.call_args.args[0].message is None


def test_selection_session_forwards_runtime_controls() -> None:
    class FakeKit:
        def __init__(self) -> None:
            self.agent_session = Mock(return_value="session")

    fake = FakeKit()
    from m3._agent_selection import expand

    agent = expand(fake, [{"harness": "opencode", "models": ["vendor/model"]}])[0]
    adapter, runtime, handlers = object(), (object(),), object()
    assert (
        agent.session(
            server=_server(),
            adapter=adapter,
            runtime_servers=runtime,
            interaction_handlers=handlers,
        )
        == "session"
    )
    assert fake.agent_session.call_args.kwargs == {
        "adapter": adapter,
        "runtime_servers": runtime,
        "interaction_handlers": handlers,
    }


def test_public_submit_exposes_cancellable_handle_lifecycle() -> None:
    class Handle:
        def __init__(self) -> None:
            self.cancelled = False

        def snapshot(self):
            return type(
                "Snapshot",
                (),
                {"lifecycle": "running" if not self.cancelled else "cancelled"},
            )()

        def result(self, timeout=None):
            assert timeout == 5
            return "cancelled-result"

        def cancel(self):
            self.cancelled = True

    class FakeKit:
        def __init__(self):
            self.handle = Handle()

        def submit(self, _spec):
            return self.handle

    from m3._agent_selection import expand

    handle = expand(FakeKit(), [{"harness": "opencode", "models": ["m"]}])[0].submit(
        "x", server=_server()
    )
    assert handle.snapshot().lifecycle == "running"
    handle.cancel()
    assert handle.snapshot().lifecycle == "cancelled"
    assert handle.result(timeout=5) == "cancelled-result"
