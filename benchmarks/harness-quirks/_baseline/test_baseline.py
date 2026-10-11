"""Baseline: control server alone, once per agent column.

Proves the agent, credentials and setup work. Classifier rule: a failed quirk
execution counts as "breaks session" only if this baseline passed.
"""

import pytest
from _lib.servers import CONTROL
from _lib.trial import record_trial

pytestmark = pytest.mark.m3(suite_name="quirks")

PROMPT = "Call control:echo with text='ok'. Do nothing else."


def test_baseline(agent) -> None:
    result = agent.run(PROMPT, server=CONTROL)
    state = record_trial(
        agent,
        result,
        quirk="baseline",
        backlog_id="BASE",
        tool="echo",
        server="control",
        arguments={"text": "ok"},
        control_gate=False,
    )
    assert state == "works", state
