"""Deterministic persisted workload; no native model or provider calls."""

import sys

import pytest

from m3 import MCPTestKit
from m3._types.specs import AgentSpec
from m3.harness.fakes import FakeClaudeCodeAdapter
from m3.types import ClaudeCode, FullToolPolicy, ServerBinding, StdioServer

pytestmark = pytest.mark.m3(suite_name="large-run-storage")


@pytest.mark.parametrize("trial", range(12))
def test_writer(trial: int, checkpoint) -> None:
    if checkpoint is not None and checkpoint.victim and trial != 0:
        checkpoint.hold()
    server = StdioServer(
        name="echo", command=sys.executable, args=("-m", "m3.fixtures.echo_server")
    )
    with MCPTestKit(env={}) as kit:
        with kit.direct(server) as client:
            assert client.list_all_tools()[0].name == "echo"
            for operation in range(16):
                response = client.call_tool("echo", {"text": f"{trial}:{operation}"})
                assert response.is_error is False
        spec = AgentSpec(
            harness=ClaudeCode(model="fixture"),
            servers=(ServerBinding(server=server),),
            tool_policy=FullToolPolicy(acknowledge_risk=True),
        )
        with kit.agent_session(spec, adapter=FakeClaudeCodeAdapter()) as session:
            turn_ids = []
            for turn in range(8):
                response = session.send(f"deterministic-{trial}-{turn}", timeout=20)
                assert response.response is not None
                turn_ids.append(response.turn_id.root)
                if (
                    checkpoint is not None
                    and checkpoint.victim
                    and trial == 0
                    and turn == 1
                ):
                    checkpoint.send(
                        {
                            "kind": "unfinished",
                            "trial": trial,
                            "execution_id": session.snapshot().execution_id.root,
                            "session_id": response.snapshot.session_id.root,
                            "turn_ids": turn_ids,
                        }
                    )
                    checkpoint.hold()
