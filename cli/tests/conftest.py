"""Shared CLI test isolation."""

from __future__ import annotations

import pytest

from m3_cli import init, setup


@pytest.fixture(autouse=True)
def disable_agent_skill_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init, "ensure_agent_skill", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup, "ensure_agent_skill", lambda *args, **kwargs: None)
