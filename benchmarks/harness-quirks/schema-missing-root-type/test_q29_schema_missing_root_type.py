from pathlib import Path

import pytest
from _lib.evals import run_eval

pytestmark = pytest.mark.m3(suite_name="quirks")


def test_q29_schema_missing_root_type(agent) -> None:
    state = run_eval(agent, Path(__file__).resolve().parent)
    assert state == "works", state
