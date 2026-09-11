"""Small executable examples for the public MCP Pal matrix API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcp_pal import MCPTestKit, expect
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.matrix import (
    HarnessCase,
    HarnessMatrix,
    ServerCase,
    ToolCase,
    ToolMatrix,
    ToolMatrixCase,
)
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.types import (
    ACPAgent,
    CallToolResult,
    ExecutionOutcome,
    StdioServer,
    TurnOutcome,
)

_EXAMPLES_ROOT = Path(__file__).parents[1]
_SERVER_SCRIPT = _EXAMPLES_ROOT / "servers" / "example_mcp_server.py"
_ACP_SCRIPT = _EXAMPLES_ROOT / "servers" / "deterministic_acp_agent.py"


def _server(name: str) -> StdioServer:
    return StdioServer(
        name=name,
        command=sys.executable,
        args=(str(_SERVER_SCRIPT),),
        cwd=str(_EXAMPLES_ROOT),
    )


def _server_case(name: str, *tools: ToolCase) -> ServerCase:
    return ServerCase(name=name, server=_server(name), tools=tools)


def _harness() -> HarnessCase:
    return HarnessCase(
        name="acp",
        harness=ACPAgent(
            model="deterministic-example",
            manifest={
                "schema_version": "mcp-pal.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(_ACP_SCRIPT)],
                "env": {},
            },
        ),
    )


def _prompt(zone: str) -> str:
    return json.dumps({"query": f"get a {zone} shipping quote"})


_PARAMETERIZED_MATRIX = ToolMatrix(
    servers=(
        _server_case(
            "catalog",
            ToolCase(name="normalize_customer", arguments={"name": "Ada Lovelace"}),
            ToolCase(name="batch_total", arguments={"values": [1, 2, 3]}),
        ),
        _server_case(
            "warehouse",
            ToolCase(
                name="shipping_quote", arguments={"weight_kg": 2, "zone": "regional"}
            ),
        ),
    )
)


@_PARAMETERIZED_MATRIX.parametrize()
def test_tool_matrix_parametrization_is_regular_pytest(case: ToolMatrixCase) -> None:
    with MCPTestKit(env={}) as kit:
        result = case.run(kit=kit)

    assert isinstance(result.direct_result, CallToolResult)
    assert result.trace_view.tool_calls
    if case.id == "catalog/normalize_customer":
        assert result.direct_result.structured_content == {
            "customer_id": "ada-lovelace"
        }
    elif case.id == "catalog/batch_total":
        assert result.direct_result.structured_content == {"total": 6.0}
    else:
        assert case.id == "warehouse/shipping_quote"
        assert result.direct_result.structured_content == {
            "amount": 12.0,
            "currency": "USD",
        }


def test_harness_matrix_each_server_and_each_tool_use_real_acp(
    example_server: StdioServer,
) -> None:
    server = ServerCase(
        name="example-mcp",
        server=example_server,
        tools=(ToolCase(name="shipping_quote", prompt=_prompt("local")),),
    )
    each_server = HarnessMatrix.each_server(servers=(server,), harnesses=(_harness(),))
    each_tool = HarnessMatrix.each_tool(servers=(server,), harnesses=(_harness(),))

    with MCPTestKit(env={}) as kit:
        server_result = each_server.cases()[0].run(_prompt("local"), kit=kit)
        tool_result = each_tool.cases()[0].run(kit=kit)

    assert server_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert tool_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert server_result.trace_view.tool_calls
    assert tool_result.trace_view.tool_calls


def test_harness_matrix_all_servers_session_uses_real_output_for_next_prompt(
    example_server: StdioServer,
) -> None:
    servers = tuple(
        ServerCase(
            name=name,
            server=example_server.model_copy(update={"name": name}),
            tools=(ToolCase(name="shipping_quote"),),
        )
        for name in ("catalog", "warehouse")
    )
    case = HarnessMatrix.all_servers(servers=servers, harnesses=(_harness(),)).cases()[
        0
    ]
    with MCPTestKit(env={}) as kit:
        with case.session(kit=kit) as session:
            first = session.send(
                json.dumps(
                    {
                        "server": "catalog",
                        "tool": "shipping_quote",
                        "arguments": {"weight_kg": 2, "zone": "local"},
                    }
                ),
                timeout=10,
            )
            assert first.response is not None
            first_value = json.loads(first.response.text)
            second = session.send(
                json.dumps(
                    {
                        "server": "warehouse",
                        "tool": "shipping_quote",
                        "arguments": {
                            "weight_kg": first_value["amount"],
                            "zone": "regional",
                        },
                    }
                ),
                timeout=10,
            )
        result = session.result

    assert len(result.turns) == 2
    assert first.response is not None
    assert json.loads(first.response.text) == {"amount": 9.0, "currency": "USD"}
    assert second.response is not None
    assert json.loads(second.response.text) == {"amount": 36.5, "currency": "USD"}
    expect(result).to_have_tool_call(
        "shipping_quote",
        turn=first,
        server="catalog",
        arguments={"weight_kg": 2, "zone": "local"},
    )
    expect(result).to_have_tool_call(
        "shipping_quote",
        turn=second,
        server="warehouse",
        arguments={"weight_kg": 9.0, "zone": "regional"},
    )
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED


def test_harness_matrix_trials_are_independent(example_server: StdioServer) -> None:
    server = ServerCase(
        name="example-mcp",
        server=example_server,
        tools=(ToolCase(name="shipping_quote"),),
    )
    matrix = HarnessMatrix.each_server(
        servers=(server,), harnesses=(_harness(),), trials=2
    )
    with MCPTestKit(env={}) as kit:
        results = tuple(case.run(_prompt("local"), kit=kit) for case in matrix.cases())

    assert [case.id for case in matrix.cases()] == [
        "example-mcp/acp/trial-1",
        "example-mcp/acp/trial-2",
    ]
    assert results[0].snapshot.execution_id != results[1].snapshot.execution_id


def test_harness_matrix_explicit_id_is_shared_by_trial_cases(
    example_server: StdioServer,
) -> None:
    server = ServerCase(
        name="example-mcp",
        server=example_server,
        tools=(ToolCase(name="shipping_quote"),),
    )
    matrix = HarnessMatrix.each_tool(
        id="shipping-quality",
        servers=(server,),
        harnesses=(_harness(),),
        trials=2,
    )
    cases = matrix.cases()
    assert matrix.id == "shipping-quality"
    assert {case.matrix_id for case in cases} == {"shipping-quality"}
    assert [case.trial for case in cases] == [1, 2]
    assert [case.trial_count for case in cases] == [2, 2]


def test_tool_matrix_explicit_id_and_trials_are_stable(
    example_server: StdioServer,
) -> None:
    matrix = ToolMatrix(
        id="tool-quality",
        trials=2,
        servers=(
            ServerCase(
                name="example-mcp",
                server=example_server,
                tools=(ToolCase(name="shipping_quote"),),
            ),
        ),
    )
    cases = matrix.cases()
    assert matrix.id == "tool-quality"
    assert [case.id for case in cases] == [
        "example-mcp/shipping_quote/trial-1",
        "example-mcp/shipping_quote/trial-2",
    ]
    assert {case.matrix_id for case in cases} == {"tool-quality"}


@pytest.mark.asyncio
async def test_tool_matrix_supports_the_async_helper(
    example_server: StdioServer,
) -> None:
    server = ServerCase(
        name="example-mcp",
        server=example_server,
        tools=(
            ToolCase(
                name="shipping_quote", arguments={"weight_kg": 3, "zone": "regional"}
            ),
        ),
    )
    matrix = ToolMatrix(servers=(server,))
    async with AsyncMCPTestKit(env={}) as kit:
        result = await matrix.cases()[0].run_async(kit=kit)

    assert isinstance(result.direct_result, CallToolResult)
    assert result.direct_result.structured_content == {
        "amount": 15.5,
        "currency": "USD",
    }


@pytest.mark.asyncio
async def test_harness_matrix_supports_async_run_and_session(
    example_server: StdioServer,
) -> None:
    server = ServerCase(
        name="example-mcp",
        server=example_server,
        tools=(ToolCase(name="shipping_quote"),),
    )
    case = HarnessMatrix.each_server(
        servers=(server,), harnesses=(_harness(),)
    ).cases()[0]
    async with AsyncMCPTestKit(env={}) as kit:
        run_result = await case.run_async(
            json.dumps(
                {
                    "server": "example-mcp",
                    "tool": "shipping_quote",
                    "arguments": {"weight_kg": 2, "zone": "local"},
                }
            ),
            kit=kit,
            timeout=10,
        )
        async with case.async_session(kit=kit) as session:
            turn = await session.send(
                json.dumps(
                    {
                        "server": "example-mcp",
                        "tool": "shipping_quote",
                        "arguments": {"weight_kg": 3, "zone": "regional"},
                    }
                ),
                timeout=10,
            )
        session_result = session.result

    assert run_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert run_result.trace_view.tool_calls
    assert turn.snapshot.outcome is TurnOutcome.COMPLETED
    assert session_result.snapshot.outcome is ExecutionOutcome.COMPLETED
    expect(run_result).to_have_tool_call(
        "shipping_quote",
        server="example-mcp",
        arguments={"weight_kg": 2, "zone": "local"},
    )
    expect(session_result).to_have_tool_call(
        "shipping_quote",
        turn=turn,
        server="example-mcp",
        arguments={"weight_kg": 3, "zone": "regional"},
    )


def test_tool_matrix_execution_is_reopened_from_sqlite(
    example_server: StdioServer, tmp_path: Path
) -> None:
    server = ServerCase(
        name="example-mcp",
        server=example_server,
        tools=(
            ToolCase(
                name="shipping_quote", arguments={"weight_kg": 2, "zone": "local"}
            ),
        ),
    )
    store = SQLiteExecutionStore(tmp_path / "examples.sqlite")
    try:
        with MCPTestKit(store=store, env={}) as kit:
            result = ToolMatrix(servers=(server,)).cases()[0].run(kit=kit)
        report = store.get_report(result.snapshot.execution_id)
        assert report is not None
        assert report.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert store.get_execution_spec(result.snapshot.execution_id) is not None
    finally:
        store.close()


def test_tool_matrix_failure_keeps_typed_result_and_trace(
    example_server: StdioServer,
) -> None:
    server = ServerCase(
        name="example-mcp",
        server=example_server,
        tools=(ToolCase(name="always_fails"),),
    )
    with MCPTestKit(env={}) as kit:
        result = ToolMatrix(servers=(server,)).cases()[0].run(kit=kit)

    assert isinstance(result.direct_result, CallToolResult)
    assert result.direct_result.is_error is True
    assert result.trace_view.tool_calls
