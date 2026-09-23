"""Value-model and expansion contracts for SDK test matrices."""

from __future__ import annotations

import builtins
import importlib
from typing import Any

import pytest
from pydantic import ValidationError

import m3
from m3._types.specs import AgentSpec
from m3.errors import UnsupportedFeature
from m3.matrix import (
    HarnessCase,
    HarnessMatrix,
    HarnessMatrixCase,
    ServerCase,
    ToolCase,
    ToolMatrix,
    ToolMatrixCase,
)
from m3.types import (
    ACPAgent,
    CallTool,
    ClaudeCode,
    DirectSpec,
    InProcessServer,
    NativeToolPolicy,
    OpenCode,
    RestrictiveToolPolicy,
    StdioServer,
    TextContent,
    UserMessage,
)


def _server(name: str, *tools: ToolCase) -> ServerCase:
    return ServerCase(
        name=name,
        server=StdioServer(name=name, command="fixture-server"),
        tools=tools or (ToolCase(name="default"),),
    )


def _harness(name: str) -> HarnessCase:
    return HarnessCase(name=name, harness=ACPAgent(model=f"model-{name}"))


class _RecordingKit:
    def __init__(self) -> None:
        self.spec: Any = None
        self.closed = False

    def run(self, spec: Any) -> str:
        self.spec = spec
        return "result"

    def close(self) -> None:
        self.closed = True


class _AsyncRecordingKit:
    def __init__(self) -> None:
        self.spec: Any = None
        self.closed = False

    async def run(self, spec: Any) -> str:
        self.spec = spec
        return "result"

    async def aclose(self) -> None:
        self.closed = True


class _RecordingSession:
    result = "session-result"

    def __init__(self) -> None:
        self.entered = False
        self.closed = False

    def __enter__(self) -> _RecordingSession:
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True


class _SessionKit(_RecordingKit):
    def __init__(self) -> None:
        super().__init__()
        self.session = _RecordingSession()

    def agent_session(self, spec: Any) -> _RecordingSession:
        self.spec = spec
        return self.session


class _AsyncRecordingSession:
    result = "session-result"

    def __init__(self) -> None:
        self.entered = False
        self.closed = False

    async def __aenter__(self) -> _AsyncRecordingSession:
        self.entered = True
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True


class _AsyncSessionKit(_AsyncRecordingKit):
    def __init__(self) -> None:
        super().__init__()
        self.session = _AsyncRecordingSession()

    def agent_session(self, spec: Any) -> _AsyncRecordingSession:
        self.spec = spec
        return self.session


class _FailingRunKit(_RecordingKit):
    def run(self, spec: Any) -> str:
        self.spec = spec
        raise RuntimeError("run failed")


class _FailingAsyncRunKit(_AsyncRecordingKit):
    async def run(self, spec: Any) -> str:
        self.spec = spec
        raise RuntimeError("run failed")


class _FailingEnterSession(_RecordingSession):
    def __enter__(self) -> _FailingEnterSession:
        raise RuntimeError("session enter failed")


class _FailingEnterSessionKit(_SessionKit):
    def __init__(self) -> None:
        super().__init__()
        self.session = _FailingEnterSession()


class _FailingAsyncEnterSession(_AsyncRecordingSession):
    async def __aenter__(self) -> _FailingAsyncEnterSession:
        raise RuntimeError("session enter failed")


class _FailingAsyncEnterSessionKit(_AsyncSessionKit):
    def __init__(self) -> None:
        super().__init__()
        self.session = _FailingAsyncEnterSession()


def test_tool_case_defaults_logical_id_and_freezes_nested_arguments() -> None:
    case = ToolCase(
        name="search",
        arguments={"filters": {"kind": "keyboard"}, "values": ["a", "b"]},
    )

    assert case.id == "search"
    assert case.arguments["filters"]["kind"] == "keyboard"
    assert case.arguments["values"] == ("a", "b")
    with pytest.raises(TypeError):
        case.arguments["new"] = "value"  # type: ignore[index]
    with pytest.raises(ValidationError):
        ToolCase(name="search", id="")


def test_server_case_lookup_is_server_owned_and_reports_missing_ids() -> None:
    search = ToolCase(id="lookup", name="catalog_search")
    server = _server("catalog", search)

    assert server.tool("lookup") is search
    with pytest.raises(KeyError, match=r"missing.*catalog"):
        server.tool("missing")


def test_tool_matrix_expands_server_outer_and_tool_inner() -> None:
    servers = (
        _server(
            "catalog", ToolCase(name="search"), ToolCase(id="get", name="get_item")
        ),
        _server("warehouse", ToolCase(name="search")),
    )
    matrix = ToolMatrix(servers=servers)

    cases = matrix.cases()
    assert isinstance(cases, tuple)
    assert [case.id for case in cases] == [
        "catalog/search",
        "catalog/get",
        "warehouse/search",
    ]
    assert all(isinstance(case, ToolMatrixCase) for case in cases)
    assert [case.server.name for case in cases] == ["catalog", "catalog", "warehouse"]
    assert [case.tool.id for case in cases] == ["search", "get", "search"]
    assert matrix.cases() == cases


def test_each_server_expands_server_harness_trial_in_declared_order() -> None:
    matrix = HarnessMatrix.each_server(
        servers=(_server("catalog"), _server("warehouse")),
        harnesses=(_harness("claude"), _harness("acp")),
        trials=2,
    )

    cases = matrix.cases()
    assert [case.id for case in cases] == [
        "catalog/claude/trial-1",
        "catalog/claude/trial-2",
        "catalog/acp/trial-1",
        "catalog/acp/trial-2",
        "warehouse/claude/trial-1",
        "warehouse/claude/trial-2",
        "warehouse/acp/trial-1",
        "warehouse/acp/trial-2",
    ]
    assert [case.mode for case in cases] == ["each_server"] * 8
    assert [case.server.name for case in cases] == [
        "catalog",
        "catalog",
        "catalog",
        "catalog",
        "warehouse",
        "warehouse",
        "warehouse",
        "warehouse",
    ]
    assert [case.trial for case in cases] == [1, 2, 1, 2, 1, 2, 1, 2]


def test_all_servers_expands_harness_outer_and_trial_inner() -> None:
    servers = (_server("catalog"), _server("warehouse"))
    matrix = HarnessMatrix.all_servers(
        servers=servers,
        harnesses=(_harness("acp"), _harness("opencode")),
        trials=2,
    )

    cases = matrix.cases()
    assert [case.id for case in cases] == [
        "all-servers/acp/trial-1",
        "all-servers/acp/trial-2",
        "all-servers/opencode/trial-1",
        "all-servers/opencode/trial-2",
    ]
    assert all(case.servers == servers for case in cases)
    assert all(case.mode == "all_servers" for case in cases)
    with pytest.raises(ValueError, match="only available"):
        _ = cases[0].server
    with pytest.raises(ValueError, match="only available"):
        _ = cases[0].tool


def test_each_tool_expands_server_tool_harness_trial() -> None:
    matrix = HarnessMatrix.each_tool(
        servers=(
            _server(
                "catalog", ToolCase(name="search"), ToolCase(id="get", name="get_item")
            ),
        ),
        harnesses=(_harness("acp"), _harness("opencode")),
        trials=2,
    )

    cases = matrix.cases()
    assert [case.id for case in cases] == [
        "catalog/search/acp/trial-1",
        "catalog/search/acp/trial-2",
        "catalog/search/opencode/trial-1",
        "catalog/search/opencode/trial-2",
        "catalog/get/acp/trial-1",
        "catalog/get/acp/trial-2",
        "catalog/get/opencode/trial-1",
        "catalog/get/opencode/trial-2",
    ]
    assert all(isinstance(case, HarnessMatrixCase) for case in cases)
    assert [case.tool.id for case in cases] == [
        "search",
        "search",
        "search",
        "search",
        "get",
        "get",
        "get",
        "get",
    ]


@pytest.mark.parametrize("constructor", (ToolMatrix,))
def test_tool_matrix_rejects_empty_and_duplicate_servers(
    constructor: type[ToolMatrix],
) -> None:
    with pytest.raises(ValidationError, match="at least one server"):
        constructor(servers=())
    duplicate = (_server("catalog"), _server("catalog"))
    with pytest.raises(ValidationError, match=r"duplicate server name.*catalog"):
        constructor(servers=duplicate)


def test_harness_matrix_rejects_empty_duplicate_and_invalid_trials() -> None:
    with pytest.raises(ValidationError, match="at least one server"):
        HarnessMatrix.each_server(servers=(), harnesses=(_harness("acp"),))
    with pytest.raises(ValidationError, match="at least one harness"):
        HarnessMatrix.each_server(servers=(_server("catalog"),), harnesses=())
    with pytest.raises(ValidationError, match=r"duplicate harness name.*acp"):
        HarnessMatrix.each_server(
            servers=(_server("catalog"),),
            harnesses=(_harness("acp"), _harness("acp")),
        )
    with pytest.raises(ValidationError, match="trials"):
        HarnessMatrix.each_server(
            servers=(_server("catalog"),), harnesses=(_harness("acp"),), trials=0
        )
    with pytest.raises(ValidationError, match="trials"):
        HarnessMatrix.each_server(
            servers=(_server("catalog"),), harnesses=(_harness("acp"),), trials=True
        )


def test_server_case_rejects_empty_duplicate_tools_and_in_process_servers() -> None:
    with pytest.raises(ValidationError, match="at least one tool"):
        ServerCase(
            name="catalog",
            server=StdioServer(name="catalog", command="fixture-server"),
            tools=(),
        )
    with pytest.raises(ValidationError, match=r"duplicate tool id.*search"):
        _server(
            "catalog",
            ToolCase(id="search", name="one"),
            ToolCase(id="search", name="two"),
        )
    with pytest.raises(ValidationError, match="InProcessServer"):
        ServerCase(
            name="loopback",
            server=InProcessServer(name="loopback", factory=lambda: None),
            tools=(ToolCase(name="search"),),
        )


def test_all_servers_allows_claude_code_for_one_server() -> None:
    matrix = HarnessMatrix.all_servers(
        servers=(_server("catalog"),),
        harnesses=(
            HarnessCase(name="claude", harness=ClaudeCode(model="claude-test")),
        ),
    )
    assert [case.id for case in matrix.cases()] == ["all-servers/claude"]


def test_all_servers_rejects_claude_code_for_multiple_servers_as_unsupported() -> None:
    with pytest.raises(UnsupportedFeature, match="does not support ClaudeCode"):
        HarnessMatrix.all_servers(
            servers=(_server("catalog"), _server("warehouse")),
            harnesses=(
                HarnessCase(name="claude", harness=ClaudeCode(model="claude-test")),
            ),
        )


def test_generated_case_ids_reject_path_segment_collisions() -> None:
    with pytest.raises(ValidationError, match=r"duplicate case id.*a/b/c"):
        ToolMatrix(
            servers=(
                _server("a/b", ToolCase(id="c", name="first")),
                _server("a", ToolCase(id="b/c", name="second")),
            )
        )

    with pytest.raises(ValidationError, match=r"duplicate case id.*a/b/c"):
        HarnessMatrix.each_server(
            servers=(_server("a/b"), _server("a")),
            harnesses=(
                HarnessCase(name="c", harness=ACPAgent(model="first")),
                HarnessCase(name="b/c", harness=ACPAgent(model="second")),
            ),
        )
    with pytest.raises(ValidationError, match=r"duplicate case id.*a/b/c/acp"):
        HarnessMatrix.each_tool(
            servers=(
                _server("a/b", ToolCase(id="c", name="first")),
                _server("a", ToolCase(id="b/c", name="second")),
            ),
            harnesses=(_harness("acp"),),
        )


def test_matrix_values_and_cases_are_immutable() -> None:
    server = _server("catalog")
    matrix = ToolMatrix(servers=(server,))
    with pytest.raises(ValidationError):
        matrix.servers = ()  # type: ignore[misc]
    case = matrix.cases()[0]
    with pytest.raises(ValidationError):
        case.id = "changed"  # type: ignore[misc]


def test_matrix_accepts_generators_without_reordering_or_side_effects() -> None:
    server_values = (_server("catalog"), _server("warehouse"))
    harness_values = (_harness("acp"), _harness("other"))
    matrix = HarnessMatrix.each_server(
        servers=(server for server in server_values),
        harnesses=(harness for harness in harness_values),
    )
    first = matrix.cases()
    second = matrix.cases()
    assert first == second
    assert [case.id for case in first] == [
        "catalog/acp",
        "catalog/other",
        "warehouse/acp",
        "warehouse/other",
    ]


def test_matrix_module_and_root_exports_are_stable() -> None:
    module = importlib.import_module("m3.matrix")
    expected = ("ToolCase", "ServerCase", "ToolMatrix", "ToolMatrixCase")
    assert tuple(module.__all__) == expected
    assert all(hasattr(m3, name) for name in expected)
    assert all(getattr(m3, name) is getattr(module, name) for name in expected)
    assert all(
        hasattr(module, name)
        for name in ("HarnessCase", "HarnessMatrix", "HarnessMatrixCase")
    )


def test_matrix_parametrize_uses_immutable_cases_and_stable_ids() -> None:
    tool_matrix = ToolMatrix(
        servers=(
            _server("catalog", ToolCase(name="search"), ToolCase(name="get")),
            _server("warehouse", ToolCase(name="stock")),
        )
    )
    harness_matrix = HarnessMatrix.each_server(
        servers=(_server("catalog"), _server("warehouse")),
        harnesses=(_harness("alpha"), _harness("beta")),
        trials=2,
    )

    tool_mark = tool_matrix.parametrize("tool_case")
    harness_mark = harness_matrix.parametrize()

    assert tool_mark.mark.args[0] == "tool_case"
    assert tool_mark.mark.args[1] == tool_matrix.cases()
    assert tool_mark.mark.kwargs["ids"] == tuple(
        case.id for case in tool_matrix.cases()
    )
    assert harness_mark.mark.args[0] == "case"
    assert harness_mark.mark.args[1] == harness_matrix.cases()
    assert harness_mark.mark.kwargs["ids"] == tuple(
        case.id for case in harness_matrix.cases()
    )


@pytest.mark.parametrize(
    "argname", ("", "1case", "case,other", "case other", "class", "for")
)
def test_matrix_parametrize_rejects_invalid_argument_names(argname: str) -> None:
    matrix = ToolMatrix(servers=(_server("catalog"),))
    with pytest.raises(ValueError, match="one non-empty Python identifier"):
        matrix.parametrize(argname)


def test_matrix_parametrize_reports_only_missing_pytest_and_cases_stay_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = ToolMatrix(servers=(_server("catalog"),))
    original_import = builtins.__import__

    def block_pytest(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pytest":
            raise ModuleNotFoundError("No module named 'pytest'", name="pytest")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_pytest)
    with pytest.raises(ImportError, match=r"m3\[pytest\]"):
        matrix.parametrize()
    assert [case.id for case in matrix.cases()] == ["catalog/default"]


def test_matrix_parametrize_preserves_unrelated_import_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = ToolMatrix(servers=(_server("catalog"),))
    original_import = builtins.__import__

    def block_dependency(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pytest":
            raise ModuleNotFoundError("No module named 'pluggy'", name="pluggy")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_dependency)
    with pytest.raises(ModuleNotFoundError, match="pluggy"):
        matrix.parametrize()


def test_tool_case_run_builds_direct_spec_and_preserves_caller_kit() -> None:
    case = ToolMatrix(
        servers=(_server("catalog", ToolCase(name="search", arguments={"q": "x"})),)
    ).cases()[0]
    kit = _RecordingKit()

    assert (
        case.run(kit=kit, timeout=3, validate_schemas=True, metadata={"suite": "unit"})
        == "result"
    )
    assert isinstance(kit.spec, DirectSpec)
    assert kit.spec.servers[0].alias == "catalog"
    assert isinstance(kit.spec.operation, CallTool)
    assert kit.spec.operation.server == "catalog"
    assert kit.spec.operation.name == "search"
    assert kit.spec.operation.arguments["q"] == "x"
    assert kit.spec.timeout_seconds == 3
    assert kit.spec.validate_schemas is True
    assert kit.spec.metadata["m3.matrix.case_id"] == "catalog/search"
    assert kit.spec.metadata["suite"] == "unit"
    assert kit.closed is False


@pytest.mark.asyncio
async def test_tool_case_async_run_builds_direct_spec_and_preserves_caller_kit() -> (
    None
):
    case = ToolMatrix(servers=(_server("catalog"),)).cases()[0]
    kit = _AsyncRecordingKit()

    assert await case.run_async(kit=kit) == "result"
    assert isinstance(kit.spec, DirectSpec)
    assert kit.spec.operation.name == "default"
    assert kit.closed is False


def test_harness_case_run_applies_prompt_and_policy_rules() -> None:
    server = _server(
        "catalog",
        ToolCase(name="search", prompt="find an item"),
        ToolCase(name="get", prompt="get the item"),
    )
    each_server = HarnessMatrix.each_server(
        servers=(server,), harnesses=(_harness("acp"),)
    ).cases()[0]
    kit = _RecordingKit()
    each_server.run("explicit message", kit=kit)
    assert isinstance(kit.spec, AgentSpec)
    assert kit.spec.message is not None
    assert kit.spec.message.content[0].text == "explicit message"
    assert isinstance(kit.spec.tool_policy, RestrictiveToolPolicy)
    assert kit.spec.tool_policy.allowed_tools == ("catalog:search", "catalog:get")

    each_tool = HarnessMatrix.each_tool(
        servers=(server,), harnesses=(_harness("acp"),)
    ).cases()[0]
    each_tool.run(kit=kit)
    assert kit.spec.message is not None
    assert kit.spec.message.content[0].text == "find an item"
    assert kit.spec.tool_policy.allowed_tools == ("catalog:search",)


def test_harness_case_claude_policy_and_explicit_policy_override() -> None:
    server = _server("catalog", ToolCase(name="search", prompt="search"))
    claude_case = HarnessMatrix.each_server(
        servers=(server,),
        harnesses=(HarnessCase(name="claude", harness=ClaudeCode(model="test")),),
    ).cases()[0]
    kit = _RecordingKit()
    claude_case.run("search", kit=kit)
    assert isinstance(kit.spec.tool_policy, NativeToolPolicy)
    assert kit.spec.tool_policy.harness == "claude-code"
    assert kit.spec.tool_policy.policy == {"mode": "mcp_only", "server": "catalog"}
    assert "server scope" in kit.spec.tool_policy.nonportable_reason

    explicit = RestrictiveToolPolicy(allowed_tools=("custom:tool",))
    claude_case.run("search", kit=kit, tool_policy=explicit)
    assert kit.spec.tool_policy == explicit


def test_harness_case_metadata_reserves_matrix_namespace_and_requires_message() -> None:
    case = HarnessMatrix.each_server(
        servers=(_server("catalog"),), harnesses=(_harness("acp"),)
    ).cases()[0]
    with pytest.raises(ValueError, match="requires an explicit message"):
        case.run(kit=_RecordingKit())
    with pytest.raises(ValueError, match="reserved"):
        case.run("message", kit=_RecordingKit(), metadata={"m3.matrix.mode": "bad"})
    kit = _RecordingKit()
    case.run("message", kit=kit, metadata={"suite": "unit"})
    assert kit.spec.metadata["m3.matrix.mode"] == "each_server"
    assert kit.spec.metadata["m3.matrix.servers"] == "catalog"
    assert kit.spec.metadata["m3.matrix.harness"] == "acp"
    assert kit.spec.metadata["m3.matrix.trial"] == 1
    assert kit.spec.metadata["suite"] == "unit"


def test_harness_session_uses_normal_session_and_preserves_supplied_kit() -> None:
    case = HarnessMatrix.each_server(
        servers=(_server("catalog"),), harnesses=(_harness("acp"),)
    ).cases()[0]
    kit = _SessionKit()
    with case.session(kit=kit) as session:
        assert session.entered is True
        assert session.result == "session-result"
    assert kit.session.closed is True
    assert kit.closed is False
    assert isinstance(kit.spec, AgentSpec)
    assert kit.spec.message is None


@pytest.mark.asyncio
async def test_harness_async_session_uses_normal_session_and_preserves_supplied_kit() -> (
    None
):
    case = HarnessMatrix.each_server(
        servers=(_server("catalog"),), harnesses=(_harness("acp"),)
    ).cases()[0]
    kit = _AsyncSessionKit()
    async with case.async_session(kit=kit) as session:
        assert session.entered is True
        assert session.result == "session-result"
    assert kit.session.closed is True
    assert kit.closed is False
    assert isinstance(kit.spec, AgentSpec)
    assert kit.spec.message is None


@pytest.mark.parametrize("mode", ("each_server", "all_servers", "each_tool"))
def test_harness_case_dump_validate_round_trip_preserves_accessors(mode: str) -> None:
    servers = (
        _server("catalog", ToolCase(id="search", name="catalog_search")),
        _server("warehouse", ToolCase(id="stock", name="inventory_stock")),
    )
    harness = _harness("acp")
    if mode == "each_server":
        original = HarnessMatrix.each_server(
            servers=(servers[0],), harnesses=(harness,)
        ).cases()[0]
    elif mode == "all_servers":
        original = HarnessMatrix.all_servers(
            servers=servers, harnesses=(harness,)
        ).cases()[0]
    else:
        original = HarnessMatrix.each_tool(
            servers=servers, harnesses=(harness,)
        ).cases()[0]

    restored = HarnessMatrixCase.model_validate(original.model_dump(mode="json"))
    assert restored == original
    if mode == "all_servers":
        with pytest.raises(ValueError, match="only available"):
            _ = restored.server
    else:
        assert restored.server.name == original.server.name
    if mode == "each_tool":
        assert restored.tool.id == "search"
    else:
        with pytest.raises(ValueError, match="only available"):
            _ = restored.tool


def test_harness_case_scope_and_selected_tool_fields_are_validated() -> None:
    server = _server("catalog", ToolCase(id="search", name="catalog_search"))
    harness = _harness("acp")
    with pytest.raises(ValidationError, match="exactly one server"):
        HarnessMatrixCase(
            id="catalog/acp",
            mode="each_server",
            harness=harness,
            servers=(server, server),
            trial=1,
        )
    with pytest.raises(ValidationError, match="selected_tool_id"):
        HarnessMatrixCase(
            id="catalog/search/acp",
            mode="each_tool",
            harness=harness,
            servers=(server,),
            trial=1,
        )
    with pytest.raises(ValidationError, match="was not found"):
        HarnessMatrixCase(
            id="catalog/missing/acp",
            mode="each_tool",
            harness=harness,
            servers=(server,),
            trial=1,
            selected_tool_id="missing",
        )
    with pytest.raises(ValidationError, match="only valid for each_tool"):
        HarnessMatrixCase(
            id="all-servers/acp",
            mode="all_servers",
            harness=harness,
            servers=(server,),
            trial=1,
            selected_tool_id="search",
        )


@pytest.mark.parametrize("harness_kind", ("acp", "opencode"))
def test_all_servers_policy_includes_all_qualified_tools_in_declared_order(
    harness_kind: str,
) -> None:
    harness = (
        _harness("acp")
        if harness_kind == "acp"
        else HarnessCase(name="opencode", harness=OpenCode(model="model-opencode"))
    )
    case = HarnessMatrix.all_servers(
        servers=(
            _server("catalog", ToolCase(name="search"), ToolCase(name="get")),
            _server("warehouse", ToolCase(name="stock")),
        ),
        harnesses=(harness,),
    ).cases()[0]
    kit = _RecordingKit()
    case.run("use the tools", kit=kit)

    assert isinstance(kit.spec.tool_policy, RestrictiveToolPolicy)
    assert kit.spec.tool_policy.allowed_tools == (
        "catalog:search",
        "catalog:get",
        "warehouse:stock",
    )


def test_typed_user_message_takes_precedence_over_tool_prompt() -> None:
    case = HarnessMatrix.each_tool(
        servers=(_server("catalog", ToolCase(name="search", prompt="fallback")),),
        harnesses=(_harness("acp"),),
    ).cases()[0]
    message = UserMessage(content=(TextContent(text="typed message"),))
    kit = _RecordingKit()

    case.run(message, kit=kit)

    assert kit.spec.message == message
    assert kit.spec.message.content[0].text == "typed message"


def test_owned_sync_run_closes_kit_on_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_api = importlib.import_module("m3.sync_api")
    created: list[_RecordingKit] = []

    def make_kit() -> _RecordingKit:
        kit = _RecordingKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(sync_api, "MCPTestKit", make_kit)
    case = ToolMatrix(servers=(_server("catalog"),)).cases()[0]
    case.run()
    assert created[-1].closed is True

    def make_failing_kit() -> _FailingRunKit:
        kit = _FailingRunKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(sync_api, "MCPTestKit", make_failing_kit)
    with pytest.raises(RuntimeError, match="run failed"):
        case.run()
    assert created[-1].closed is True


@pytest.mark.asyncio
async def test_owned_async_run_closes_kit_on_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = importlib.import_module("m3.async_api")
    created: list[_AsyncRecordingKit] = []

    def make_kit() -> _AsyncRecordingKit:
        kit = _AsyncRecordingKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(async_api, "AsyncMCPTestKit", make_kit)
    case = ToolMatrix(servers=(_server("catalog"),)).cases()[0]
    await case.run_async()
    assert created[-1].closed is True

    def make_failing_kit() -> _FailingAsyncRunKit:
        kit = _FailingAsyncRunKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(async_api, "AsyncMCPTestKit", make_failing_kit)
    with pytest.raises(RuntimeError, match="run failed"):
        await case.run_async()
    assert created[-1].closed is True


def test_owned_sync_session_closes_kit_when_enter_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_api = importlib.import_module("m3.sync_api")
    created: list[_FailingEnterSessionKit] = []

    def make_kit() -> _FailingEnterSessionKit:
        kit = _FailingEnterSessionKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(sync_api, "MCPTestKit", make_kit)
    case = HarnessMatrix.each_server(
        servers=(_server("catalog"),), harnesses=(_harness("acp"),)
    ).cases()[0]
    with pytest.raises(RuntimeError, match="session enter failed"):
        with case.session():
            pass
    assert created[0].closed is True


def test_owned_sync_session_closes_kit_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_api = importlib.import_module("m3.sync_api")
    created: list[_SessionKit] = []

    def make_kit() -> _SessionKit:
        kit = _SessionKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(sync_api, "MCPTestKit", make_kit)
    case = HarnessMatrix.each_server(
        servers=(_server("catalog"),), harnesses=(_harness("acp"),)
    ).cases()[0]
    with case.session() as session:
        assert session.entered is True
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_owned_async_session_closes_kit_when_enter_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = importlib.import_module("m3.async_api")
    created: list[_FailingAsyncEnterSessionKit] = []

    def make_kit() -> _FailingAsyncEnterSessionKit:
        kit = _FailingAsyncEnterSessionKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(async_api, "AsyncMCPTestKit", make_kit)
    case = HarnessMatrix.each_server(
        servers=(_server("catalog"),), harnesses=(_harness("acp"),)
    ).cases()[0]
    with pytest.raises(RuntimeError, match="session enter failed"):
        async with case.async_session():
            pass
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_owned_async_session_closes_kit_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = importlib.import_module("m3.async_api")
    created: list[_AsyncSessionKit] = []

    def make_kit() -> _AsyncSessionKit:
        kit = _AsyncSessionKit()
        created.append(kit)
        return kit

    monkeypatch.setattr(async_api, "AsyncMCPTestKit", make_kit)
    case = HarnessMatrix.each_server(
        servers=(_server("catalog"),), harnesses=(_harness("acp"),)
    ).cases()[0]
    async with case.async_session() as session:
        assert session.entered is True
    assert created[0].closed is True
