from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from mcp_pal import (
    ACPAgent,
    AgentExecutionSpec,
    ClaudeCode,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    OpenCode,
    SecretReference,
    ServerBinding,
    StdioServer,
    TextContent,
    UserMessage,
)
from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.harness.acp import AcpHarnessAdapter, _isolated_acp_env, _resolve_environment_secret
from mcp_pal.harness.claude import ClaudeCodeHarnessAdapter
from mcp_pal.harness.contracts import HarnessLaunch
from mcp_pal.harness.opencode import OpenCodeHarnessAdapter
from mcp_pal.harness.native import _isolated_environment, _resolve_runtime_value, write_config
from mcp_pal.harness.opencode import _resolve_opencode_environment_value, opencode_configuration
from mcp_pal.server_group import HarnessServerConfiguration, ServerGroupSnapshot
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.types import NativeToolPolicy, TransportKind
from mcp_pal_app.services.app_service import build_harness_adapter_registry
from mcp_pal_app.settings import Settings


def test_settings_credentials_are_explicit_and_not_serialized(monkeypatch, tmp_path: Path) -> None:
    canary = "settings-only-credential-canary"
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        anthropic_api_key=canary,
        openrouter_api_key=canary,
        opencode_api_key=canary,
    )
    registry = build_harness_adapter_registry(settings)
    servers = (ServerBinding(server=StdioServer(name="server", command="echo")),)
    message = UserMessage(content=(TextContent(text="hello"),))
    claude_spec = AgentExecutionSpec(
        servers=servers,
        message=message,
        harness=ClaudeCode(
            model="model",
            credential_references={
                "ANTHROPIC_API_KEY": SecretReference(
                    source="environment", name="ANTHROPIC_API_KEY"
                )
            },
        ),
    )
    claude = cast(ClaudeCodeHarnessAdapter, registry.resolve(claude_spec))
    opencode = cast(
        OpenCodeHarnessAdapter,
        registry.resolve(
            claude_spec.model_copy(
                update={
                    "harness": OpenCode(
                        model="provider/model",
                        credential_references={
                            "OPENROUTER_API_KEY": SecretReference(
                                source="environment", name="OPENROUTER_API_KEY"
                            )
                        },
                    )
                }
            )
        ),
    )
    acp = cast(
        AcpHarnessAdapter,
        registry.resolve(
            claude_spec.model_copy(
                update={
                    "harness": ACPAgent(
                        model="agent-default",
                        manifest={
                            "command": "echo",
                            "env": {"TOKEN": "${ANTHROPIC_API_KEY}"},
                        },
                    )
                }
            )
        ),
    )
    assert acp._environment is not None
    assert claude.environment["ANTHROPIC_API_KEY"] == canary
    assert opencode.environment["OPENROUTER_API_KEY"] == canary
    assert acp._environment["ANTHROPIC_API_KEY"] == canary
    assert _resolve_runtime_value(
        SecretReference(source="environment", name="ANTHROPIC_API_KEY"),
        environment=claude.environment,
    ) == canary
    opencode_env = dict(opencode.environment)
    secrets: set[str] = set()
    _resolve_opencode_environment_value(
        SecretReference(source="environment", name="OPENROUTER_API_KEY"),
        opencode_env,
        secrets,
    )
    assert opencode_env["OPENROUTER_API_KEY"] == canary
    assert _resolve_environment_secret(
        SecretReference(source="environment", name="ANTHROPIC_API_KEY"),
        acp._environment,
    ) == canary
    child_env = _isolated_acp_env(
        {"command": "echo", "env": {"CHILD_TOKEN": "${ANTHROPIC_API_KEY}"}},
        "/bin/echo",
        set(),
        root=str(tmp_path),
        environment=acp._environment,
    )
    assert child_env["CHILD_TOKEN"] == canary
    assert "OPENROUTER_API_KEY" not in child_env
    assert "OPENCODE_API_KEY" not in child_env
    assert canary not in repr(settings)
    assert canary not in repr(settings.model_dump())


def test_named_reference_prefers_explicit_value_and_falls_back_to_ambient(
    monkeypatch,
) -> None:
    reference = SecretReference(source="environment", name="AMBIENT_CANARY")
    monkeypatch.setenv("AMBIENT_CANARY", "ambient-value")
    assert _resolve_runtime_value(reference) == "ambient-value"
    assert _resolve_runtime_value(reference, environment={}) == "ambient-value"

    opencode_env: dict[str, str] = {}
    _resolve_opencode_environment_value(reference, opencode_env, set())
    assert opencode_env["AMBIENT_CANARY"] == "ambient-value"
    strict_empty: dict[str, str] = {}
    _resolve_opencode_environment_value(reference, strict_empty, set(), resolver_environment={})
    assert strict_empty["AMBIENT_CANARY"] == "ambient-value"

    assert _resolve_environment_secret(reference) == "ambient-value"
    assert _resolve_environment_secret(reference, {}) == "ambient-value"


def test_registry_does_not_forward_unrelated_provider_keys() -> None:
    settings = Settings(
        anthropic_api_key="anthropic-canary",
        openrouter_api_key="openrouter-canary",
        opencode_api_key="opencode-canary",
    )
    registry = build_harness_adapter_registry(settings)
    servers = (ServerBinding(server=StdioServer(name="server", command="echo")),)
    message = UserMessage(content=(TextContent(text="hello"),))
    adapter = cast(
        ClaudeCodeHarnessAdapter,
        registry.resolve(
            AgentExecutionSpec(
                servers=servers,
                message=message,
                harness=ClaudeCode(
                    model="model",
                    credential_references={
                        "ANTHROPIC_API_KEY": SecretReference(
                            source="environment", name="ANTHROPIC_API_KEY"
                        )
                    },
                ),
            ),
        ),
    )
    assert adapter.environment == {"ANTHROPIC_API_KEY": "anthropic-canary"}


def test_settings_canary_stays_out_of_durable_spec_and_events(tmp_path: Path) -> None:
    canary = "durable-settings-canary"
    settings = Settings(anthropic_api_key=canary)
    assert settings.anthropic_api_key == canary
    spec = AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="server", command="echo")),),
        message=UserMessage(content=(TextContent(text="hello"),)),
        harness=ClaudeCode(
            model="model",
            credential_references={
                "ANTHROPIC_API_KEY": SecretReference(
                    source="environment", name="ANTHROPIC_API_KEY"
                )
            },
        ),
    )
    database = tmp_path / "durable.sqlite"
    store = SQLiteExecutionStore(database)
    recorder = ExecutionTraceRecorder(
        store,
        ExecutionId("durable-settings-execution"),
        specification=spec.model_dump(mode="json"),
    )
    recorder.emit(
        EventKind.DIAGNOSTIC,
        payload={"credential": SecretReference(source="environment", name="ANTHROPIC_API_KEY")},
    )
    recorder.finalize(ExecutionOutcome.COMPLETED)
    report = store.get_report("durable-settings-execution")
    assert report is not None
    assert canary not in json.dumps(report.model_dump(mode="json"))
    assert canary not in json.dumps(store.get_execution_spec("durable-settings-execution").model_dump(mode="json"))  # type: ignore[union-attr]
    store.close()
    assert canary.encode() not in database.read_bytes()


def test_native_launch_boundaries_use_selected_settings_only(tmp_path: Path) -> None:
    canary = "native-settings-canary"
    spec = AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="server", command="echo", environment={"TOKEN": SecretReference(source="environment", name="ANTHROPIC_API_KEY")})),),
        message=UserMessage(content=(TextContent(text="hello"),)),
        harness=ClaudeCode(
            model="model",
            credential_references={"ANTHROPIC_API_KEY": SecretReference(source="environment", name="ANTHROPIC_API_KEY")},
        ),
        tool_policy=NativeToolPolicy(
            harness="claude-code",
            policy={"mode": "mcp_only", "server": "server"},
            nonportable_reason="provider policy",
        ),
    )
    configuration = HarnessServerConfiguration(
        key="server", transport=TransportKind.STDIO, required=True, available=True,
        connection_id="connection", command="echo", environment={
            "TOKEN": SecretReference(source="environment", name="ANTHROPIC_API_KEY")
        },
    )
    launch = HarnessLaunch(spec, ServerGroupSnapshot(), (configuration,), spec.tool_policy)
    environment = _isolated_environment(tmp_path, {"ANTHROPIC_API_KEY": canary})
    config = write_config(tmp_path, launch, environment=environment)
    assert json.loads(config.read_text())["mcpServers"]["server"]["env"]["TOKEN"] == canary
    assert "OPENROUTER_API_KEY" not in environment
    assert "OPENCODE_API_KEY" not in environment
    config.unlink()

    opencode = spec.model_copy(
        update={
            "harness": OpenCode(model="provider/model"),
            "tool_policy": NativeToolPolicy(
                harness="opencode",
                policy={"mode": "mcp_only", "server": "server"},
                nonportable_reason="provider policy",
            ),
        }
    )
    opencode_launch = HarnessLaunch(opencode, ServerGroupSnapshot(), (configuration,), opencode.tool_policy)
    rendered = opencode_configuration(opencode_launch, dialect="legacy")
    assert rendered["mcp"]["server"]["environment"]["TOKEN"] == "{env:ANTHROPIC_API_KEY}"
    opencode_root = tmp_path / "opencode-child"
    opencode_root.mkdir()
    opencode_child = _isolated_environment(
        opencode_root, {"OPENROUTER_API_KEY": canary}
    )
    assert opencode_child["OPENROUTER_API_KEY"] == canary
    assert "ANTHROPIC_API_KEY" not in opencode_child
