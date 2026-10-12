from pathlib import Path

import pytest
from _lib.tasks import run_task

pytestmark = pytest.mark.m3(suite_name="model-tiers")


def test_h03(agent, variant) -> None:
    assert run_task(agent, Path(__file__).resolve().parent, variant) == "success"
