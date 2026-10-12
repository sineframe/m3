"""Baseline: control server alone, once per agent column."""

import pytest
from _lib import grade, telemetry
from _lib.servers import CONTROL

pytestmark = pytest.mark.m3(suite_name="model-tiers")

PROMPT = "Call control:echo with text='ok'. Do nothing else."


def test_baseline(agent) -> None:
    from m3 import expect

    result = agent.run(PROMPT, server=CONTROL)
    outcome = telemetry.outcome_value(result)
    ok = False
    if outcome == "completed":
        try:
            expect(result).to_have_tool_call(
                "echo",
                server="control",
                arguments={"text": "ok"},
                arguments_partial=True,
                status="success",
                evidence="wire",
            )
            ok = True
        except AssertionError:
            pass
    if ok:
        state = "success"
    elif outcome == "timed_out":
        state = "timeout"
    elif outcome != "completed":
        state = "harness_error"
    else:
        state = "other"
    grade.record(
        agent,
        result,
        task="BASE",
        band="baseline",
        variant=None,
        nonce=None,
        state=state,
        text=telemetry.final_text(result),
    )
    assert state == "success", state
