from __future__ import annotations

import asyncio
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

import pytest
from pydantic import ValidationError

from m3._types.specs import AgentSpec
from m3.configuration import Config, ConfigError, load_config
from m3.harness.claude import _claude_mcp_startup
from m3.harness.codex import CodexHarnessAdapter, render_codex_config
from m3.harness.contracts import HarnessLaunch, HarnessStartupError
from m3.harness.opencode import OpenCodeHarnessAdapter, opencode_configuration
from m3.server_group import HarnessServerConfig, ServerGroupSnapshot
from m3.types import (
    Codex,
    HarnessTimeouts,
    McpTimeouts,
    OpenCode,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    TransportKind,
)


def _server(key: str, budget: float | None = None) -> HarnessServerConfig:
    return HarnessServerConfig(
        key=key,
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id=f"{key}-connection",
        command="fixture",
        mcp_startup_seconds=budget,
    )


def _launch(
    harness: object,
    *servers: HarnessServerConfig,
    config: Config | None = None,
) -> HarnessLaunch:
    spec = AgentSpec(
        harness=harness,
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
    )
    return HarnessLaunch(
        spec,
        ServerGroupSnapshot(),
        servers,
        RestrictiveToolPolicy(),
        config=config or Config(),
    )


class _NotifyingProcess:
    """App Server that answers startup requests but never answers thread/start.

    It keeps sending unrelated notifications, which must not extend the
    thread/start deadline.
    """

    root: Path | None = None

    def __init__(self) -> None:
        self.written: list[dict[str, object]] = []

    async def write(self, payload: dict[str, object]) -> None:
        self.written.append(payload)

    async def next(self, timeout: float | None = None) -> dict[str, object]:
        request = self.written[-1]
        if request["method"] == "initialize":
            return {"jsonrpc": "2.0", "id": request["id"], "result": {}}
        if request["method"] == "account/read":
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True},
            }
        await asyncio.wait_for(asyncio.sleep(0.05), timeout)
        return {"jsonrpc": "2.0", "method": "mcpServer/startupStatus", "params": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness", "config", "limit"),
    (
        (
            Codex(model="fixture"),
            load_config(
                {"harnesses": {"codex": {"timeouts": {"startup_seconds": 0.3}}}}, env={}
            ),
            "0.3s",
        ),
        (
            Codex(model="fixture", timeouts=HarnessTimeouts(startup_seconds=0.2)),
            load_config(
                {"harnesses": {"codex": {"timeouts": {"startup_seconds": 60}}}}, env={}
            ),
            "0.2s",
        ),
    ),
    ids=("run-setting", "typed-harness-wins"),
)
async def test_codex_thread_start_deadline_is_not_extended_by_other_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: Codex,
    config: Config,
    limit: str,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-home"))
    adapter = CodexHarnessAdapter(executable="codex")
    launch = _launch(harness, config=config)
    adapter.environment_for_launch(launch, tmp_path)
    with pytest.raises(HarnessStartupError) as caught:
        await asyncio.wait_for(adapter.initialize(_NotifyingProcess(), launch), 5)  # type: ignore[arg-type]
    assert str(caught.value) == (
        f"Codex thread/start did not finish within {limit} "
        "(harnesses.codex.timeouts.startup_seconds)"
    )


def test_codex_mcp_startup_budget_is_written_only_when_set() -> None:
    config = load_config({"mcp_timeouts": {"startup_seconds": 45}}, env={})
    rendered = tomllib.loads(
        render_codex_config(
            _launch(
                Codex(model="fixture"),
                _server("inherits"),
                _server("own", budget=90),
                config=config,
            )
        )
    )
    assert rendered["mcp_servers"]["inherits"]["startup_timeout_sec"] == 45.0
    assert rendered["mcp_servers"]["own"]["startup_timeout_sec"] == 90.0

    native = tomllib.loads(
        render_codex_config(_launch(Codex(model="fixture"), _server("a")))
    )
    assert "startup_timeout_sec" not in native["mcp_servers"]["a"]


@pytest.mark.parametrize("dialect", ("legacy", "v2"))
def test_opencode_mcp_startup_budget_is_written_in_milliseconds(dialect: str) -> None:
    launch = _launch(
        OpenCode(model="fixture"),
        _server("slow", budget=60),
        _server("native"),
    )
    servers = opencode_configuration(launch, dialect=dialect)["mcp"]
    servers = servers.get("servers", servers)
    assert servers["slow"]["timeout"] == 60000
    assert "timeout" not in servers["native"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("seconds", "found"), ((0.2, False), (2.0, True)))
async def test_opencode_server_startup_uses_one_configured_deadline(
    seconds: float, found: bool
) -> None:
    stream = asyncio.StreamReader()

    async def announce() -> None:
        stream.feed_data(b"starting\n")
        await asyncio.sleep(0.5)
        stream.feed_data(b"opencode server listening on http://127.0.0.1:4096\n")

    feeder = asyncio.create_task(announce())
    adapter = OpenCodeHarnessAdapter(executable="opencode")
    try:
        if found:
            assert await adapter._read_server_url(stream, seconds) == (
                "http://127.0.0.1:4096"
            )
        else:
            with pytest.raises(HarnessStartupError) as caught:
                await adapter._read_server_url(stream, seconds)
            assert str(caught.value) == (
                "OpenCode server startup did not finish within 0.2s "
                "(harnesses.opencode.timeouts.startup_seconds)"
            )
    finally:
        await feeder


def test_claude_uses_one_mcp_timeout_for_every_server() -> None:
    config = load_config({"mcp_timeouts": {"startup_seconds": 20}}, env={})
    harness = Codex(model="fixture")
    assert _claude_mcp_startup(_launch(harness, _server("a"), config=config)) == 20
    assert _claude_mcp_startup(_launch(harness, _server("a"))) is None
    assert (
        _claude_mcp_startup(
            _launch(harness, _server("a", 30), _server("b", 30), config=config)
        )
        == 30
    )
    with pytest.raises(HarnessStartupError, match="one MCP startup timeout"):
        _claude_mcp_startup(_launch(harness, _server("a", 30), _server("b")))


def test_nested_timeout_tables_resolve_from_project_file(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.harnesses.codex.timeouts]\n"
        "startup_seconds = 90\n"
        "[tool.m3.mcp_timeouts]\n"
        "startup_seconds = 60\n",
        encoding="utf-8",
    )
    config = load_config(env={}, cwd=tmp_path)
    assert config.harnesses.codex.timeouts.startup_seconds == 90.0
    assert config.harnesses.codex.timeouts.initialize_seconds == 30.0
    assert config.harnesses.opencode.timeouts.startup_seconds == 60.0
    assert config.mcp_timeouts.startup_seconds == 60.0
    origin = config.source_for("harnesses.codex.timeouts.startup_seconds").origin
    assert origin == f"pyproject:{tmp_path / 'pyproject.toml'}"
    assert (
        config.source_for("harnesses.codex.timeouts.initialize_seconds").origin
        == "default"
    )


@pytest.mark.parametrize(
    ("values", "field"),
    (
        (
            {"harnesses": {"codex": {"timeouts": {"session_creation_seconds": 5}}}},
            "harnesses.codex.timeouts.session_creation_seconds",
        ),
        (
            {"harnesses": {"claude_code": {"timeouts": {"startup_seconds": 5}}}},
            "harnesses.claude_code",
        ),
        (
            {"harnesses": {"codex": {"startup_seconds": 5}}},
            "harnesses.codex.startup_seconds",
        ),
        (
            {"harnesses": {"codex.timeouts": {"startup_seconds": 5}}},
            "harnesses.codex.timeouts",
        ),
    ),
)
def test_unknown_or_misplaced_timeout_settings_are_rejected(
    values: dict[str, object], field: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(values, env={})
    assert caught.value.field == field
    assert caught.value.code == "unknown_setting"


@pytest.mark.parametrize("value", (0, -1, 3601, "60", float("inf")))
def test_timeout_settings_reject_invalid_durations(value: object) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(
            {"harnesses": {"opencode": {"timeouts": {"startup_seconds": value}}}},
            env={},
        )
    assert caught.value.field == "harnesses.opencode.timeouts.startup_seconds"


def test_harness_rejects_timeouts_for_a_phase_it_does_not_have() -> None:
    with pytest.raises(ValidationError, match="Codex has no session_creation_seconds"):
        Codex(model="fixture", timeouts=HarnessTimeouts(session_creation_seconds=5))
    with pytest.raises(ValidationError, match="OpenCode has no initialize_seconds"):
        OpenCode(model="fixture", timeouts=HarnessTimeouts(initialize_seconds=5))


def test_unset_timeouts_are_omitted_from_serialized_specs() -> None:
    assert "timeouts" not in StdioServer(name="s", command="c").model_dump()
    assert "timeouts" not in Codex(model="m").model_dump()
    server = StdioServer(name="s", command="c", timeouts=McpTimeouts(startup_seconds=5))
    assert server.model_dump(mode="json")["timeouts"] == {"startup_seconds": 5.0}
    assert StdioServer.model_validate(server.model_dump(mode="json")) == server
