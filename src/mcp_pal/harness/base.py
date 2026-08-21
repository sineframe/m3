"""Common harness data structures and interface."""
from dataclasses import dataclass, field
from typing import Any, Callable

@dataclass
class RunSpec:
    prompt: str; model: str; mcp_config: dict; enabled_server: str; tool_mode: str = "mcp_only"
    timeout_seconds: int = 120; max_turns: int = 5; max_budget_usd: float = .5

@dataclass
class HarnessResult:
    status: str; events: list[Any] = field(default_factory=list); normalized: list[tuple[str,dict]] = field(default_factory=list)
    final_text: str = ""; stderr: str = ""; exit_code: int | None = None; error: str | None = None
    cost_usd: float | None = None; turns: int | None = None; session_id: str | None = None
    final_result_seen: bool = False

class HarnessRunner:
    async def run(self, spec: RunSpec, on_event: Callable | None = None, cancel_event: Any = None) -> HarnessResult:
        raise NotImplementedError
