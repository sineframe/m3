from __future__ import annotations

import pytest

from m3 import _startup_deadlines as deadlines
from m3._native_settings import resolve_native_settings, timeout_override
from m3._startup_deadlines import StartupBudget, StartupDeadlineExceeded
from m3._types.specs import AgentSpec
from m3.configuration import Config
from m3.types import (
    ClaudeCode,
    Codex,
    HarnessTimeouts,
    OpenCode,
    ServerBinding,
    StdioServer,
)


def test_active_preparation_counts_idle_and_queue_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: now)
    budget = StartupBudget(HarnessTimeouts(total_seconds=120, startup_seconds=90))
    budget.resume()
    assert budget.deadline == 220
    now += 15
    budget.pause()
    now += 1000  # Lazy session remains idle without consuming startup budget.
    budget.resume(caller_deadline=now + 200)
    assert budget.deadline == now + 105
    budget.pause()
    now += 50  # Admission queue has its own wall-clock bound.
    budget.resume()
    assert budget.deadline == now + 105
    budget.begin_native()
    assert budget.deadline == now + 90
    mcp = budget.phase("session", 75)
    now += 20
    assert mcp.remaining() == 55
    budget.begin_native()  # Repeated stage entry cannot create a new clock.
    assert budget.deadline == now + 70
    with pytest.raises(RuntimeError, match="cannot suspend"):
        budget.pause()


def test_caller_deadline_wins_even_while_total_is_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: now)
    budget = StartupBudget(HarnessTimeouts(total_seconds=120))
    budget.resume(caller_deadline=110)
    budget.pause()
    now = 111
    with pytest.raises(StartupDeadlineExceeded):
        budget.resume()


def test_finished_startup_does_not_limit_future_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: now)
    budget = StartupBudget(HarnessTimeouts(total_seconds=120, startup_seconds=90))
    budget.resume()
    budget.begin_native()
    budget.finish()
    now += 1000
    assert budget.deadline is None


@pytest.mark.parametrize("harness", [Codex, ClaudeCode, OpenCode])
def test_timeout_leaves_inherit_across_file_kit_selection_and_session(
    harness: type[Codex] | type[ClaudeCode] | type[OpenCode],
) -> None:
    config = Config.model_validate(
        {
            "harnesses": {
                harness(model="fixture").kind: {
                    "timeouts": {"startup_seconds": 90, "total_seconds": 120}
                }
            }
        }
    )
    spec = AgentSpec(
        harness=harness(
            model="fixture", timeouts=HarnessTimeouts(initialize_seconds=15)
        ),
        servers=(ServerBinding(server=StdioServer(name="slow", command="cat")),),
    )
    spec = timeout_override(spec, HarnessTimeouts(request_seconds=8))
    resolved = resolve_native_settings(
        spec, config, kit_timeouts=HarnessTimeouts(readiness_seconds=12)
    )
    assert resolved.harness is not None
    assert resolved.harness.timeouts.model_dump() == {
        "startup_seconds": 90,
        "total_seconds": 120,
        "initialize_seconds": 15,
        "request_seconds": 8,
        "readiness_seconds": 12,
    }
    disabled = resolve_native_settings(
        timeout_override(spec, HarnessTimeouts(total_seconds=None)), config
    )
    assert disabled.harness is not None
    assert disabled.harness.timeouts.total_seconds is None
    assert disabled.harness.timeouts.startup_seconds == 90


def test_owned_configuration_jobs_share_one_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: now)
    budget = StartupBudget(HarnessTimeouts(total_seconds=120, initialize_seconds=90))
    assert budget.begin_owned_preparation() == 130
    now += 10
    assert budget.begin_owned_preparation() == 130
    budget.clear_owned_preparation()
    budget.begin_native()
    assert budget.deadline == 220
    assert budget.phase("initialize", 5).deadline == 200


def test_explicit_startup_bounds_configuration_then_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: now)
    budget = StartupBudget(HarnessTimeouts(total_seconds=120, startup_seconds=90))
    assert budget.begin_owned_preparation() == 190
    now += 15
    budget.clear_owned_preparation()
    budget.begin_native()
    assert budget.deadline == 205
    assert budget.phase("session", 75).deadline == 190
