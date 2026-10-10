from pathlib import Path

import pytest
from _lib.evals import run_eval

pytestmark = pytest.mark.m3(suite_name="quirks")


def test_q20_result_large_iserror_truncation(agent) -> None:
    state = run_eval(agent, Path(__file__).resolve().parent)
    assert state == "works", state
