"""Classify one tracker trial and append it to the trial inbox.

States, checked in this order (first match wins):

1. harness_error  outcome is neither completed nor timed_out, OR outcome completed with zero
                  tracker wire calls AND an error present.
2. timeout        outcome is timed_out.
3. escaped        any native / non-tracker tool call (Bash, Read, shell...).
4. success        check.success.
5. looped         >=3 identical consecutive (tool, args) calls, or >120 tracker calls.
6. hallucinated   STATUS: DONE present, check failed, and no target op was hit.
7. gave_up        STATUS: FAILED, check failed, and no target op was hit (whether or not the
                  model ever attempted the op, and whether or not it saw error responses).
8. wrong_tool     no target op hit, no FAILED status, and no error responses.
9. bad_args       no target op hit, no FAILED status, and at least one error response.
10. wrong_target  a target op was hit but the check failed (for a reason other than
                  side effects only), whatever the status line.
11. side_effects  every subgoal except `no_side_effects` is True and `no_side_effects` is False.
12. other         fallback (e.g. no target op hit and no status line is covered by 8/9, so
                  this is rarely reached).

A target op is "hit" if it appears among final-store mutation ops, or a non-error tracker call
maps to it through the explicit table `build_surface(variant).op_of(tool_name)` (no name matching:
naive `issues_get` / `get_issue_v2_beta` -> get_issue, `issues_patch` / `issues_put` -> update_issue,
distractors and `add_dependency` -> None). `target_op_stats` also reports attempted / errored /
succeeded counts per target op. Empty target_ops counts as hit.
Side-effects-only failures skip hallucinated/gave_up/wrong_*/bad_args and go to side_effects,
since the target work was done. The status line is always recorded separately.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from _lib import telemetry

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "results" / "_inbox" / "records.jsonl"

STATES = (
    "harness_error", "timeout", "escaped", "success", "looped", "hallucinated",
    "gave_up", "wrong_tool", "bad_args", "wrong_target", "side_effects", "other",
)


@dataclass
class CheckContext:
    initial: Any
    final: Any
    facts: dict
    answer: str | None
    nonce: str


@dataclass
class Check:
    success: bool
    subgoals: dict[str, bool] = field(default_factory=dict)
    target_ops: list[str] = field(default_factory=list)


# Not anchored to a line start: Codex joins streamed message chunks without a separator,
# so a correct answer can arrive as "...audit code.STATUS: DONE ...".
_STATUS = re.compile(r"STATUS\s*[*_`]*\s*:\s*[*_`]*\s*(DONE|FAILED)\b[*_`]*[ \t]*([^\n]*)", re.I)


def parse_status_line(text: str | None) -> tuple[str | None, str]:
    """Last `STATUS: DONE|FAILED rest` marker anywhere in the text, tolerant of backticks/bold."""
    if not text:
        return None, ""
    found: tuple[str | None, str] = (None, "")
    for m in _STATUS.finditer(text):
        found = (m.group(1).upper(), m.group(2).strip().strip("`*_ ").strip())
    return found


def identity_op(tool: str) -> str | None:
    return tool


def target_op_stats(op_of, target_ops, tracker_calls, outputs, mutations=()) -> dict[str, dict[str, int]]:
    """Per target op: calls mapping to it that were attempted / errored / succeeded.

    Successful store mutations also count as succeeded (they are ground truth)."""
    out = {}
    for op in target_ops:
        att = err = ok = 0
        for i, (tool, _args) in enumerate(tracker_calls):
            if op_of(tool) != op:
                continue
            att += 1
            if outputs and i < len(outputs) and outputs[i][1]:
                err += 1
            else:
                ok += 1
        ok = max(ok, sum(1 for m in mutations if m.get("op") == op))
        out[op] = {"attempted": att, "errored": err, "succeeded": ok}
    return out


# H04 on the naive server needs ~45 calls for a perfect run (page every project, read each candidate)
MAX_TRACKER_CALLS = 120


def _looped(calls) -> bool:
    if len(calls) > MAX_TRACKER_CALLS:
        return True
    run = 1
    for a, b in zip(calls, calls[1:]):
        run = run + 1 if a == b else 1
        if run >= 3:
            return True
    return False


def classify(
    outcome: str | None,
    *,
    error: Any = None,
    tracker_calls: list[tuple[str, dict]],
    outputs: list[tuple[str, bool]] | None = None,
    native_total: int = 0,
    check: Check | None = None,
    mutations: list[dict] | None = None,
    status: str | None = None,
    op_of: Callable[[str], str | None] = identity_op,
) -> str:
    outputs = outputs or []
    mutations = mutations or []
    if outcome not in ("completed", "timed_out"):
        return "harness_error"
    if outcome == "completed" and not tracker_calls and error:
        return "harness_error"
    if outcome == "timed_out":
        return "timeout"
    if native_total > 0:
        return "escaped"
    if check is None:
        return "harness_error"
    if check.success:
        return "success"
    if _looped(tracker_calls):
        return "looped"
    sub = dict(check.subgoals)
    side_only = (
        sub.get("no_side_effects") is False
        and all(v for k, v in sub.items() if k != "no_side_effects")
    )
    stats = target_op_stats(op_of, check.target_ops, tracker_calls, outputs, mutations)
    hit = (not check.target_ops) or any(v["succeeded"] > 0 for v in stats.values())
    if not side_only:
        if status == "DONE" and not hit:
            return "hallucinated"
        if status == "FAILED" and not hit:
            return "gave_up"
        if not hit:
            return "bad_args" if any(o[1] for o in outputs) else "wrong_tool"
        return "wrong_target"
    if side_only:
        return "side_effects"
    return "other"


def record(
    agent: Any,
    result: Any,
    *,
    task: str,
    band: str | None,
    variant: str | None,
    nonce: str | None,
    state: str,
    subgoals: dict | None = None,
    status_line: str | None = None,
    tracker_calls: list[tuple[str, dict]] | None = None,
    outputs: list[tuple[str, bool]] | None = None,
    mutations: list[dict] | None = None,
    text: str | None = None,
    grader_traceback: str | None = None,
) -> str:
    stats = telemetry.tool_call_stats(result)
    if outputs:
        stats["tracker_errors"] = sum(1 for o in outputs if o[1])
    calls = tracker_calls or []
    rec = {
        "task": task, "band": band, "variant": variant,
        "harness": getattr(agent, "harness", None),
        "model": getattr(agent, "model", None),
        "trial": getattr(agent, "trial", None),
        "nonce": nonce,
        "outcome": telemetry.outcome_value(result),
        "error": telemetry.error_text(result),
        "state": state,
        "subgoals": subgoals or {},
        "status_line": status_line,
        "duration_ms": telemetry.duration_ms(result),
        "turn_count": telemetry.turn_count(result),
        "tool_calls": stats,
        "usage": telemetry.usage_stats(result),
        "mutations": [{"op": m.get("op"), "args": m.get("args")} for m in (mutations or [])],
        "calls": [
            {"tool": t, "is_error": (outputs[i][1] if outputs and i < len(outputs) else None)}
            for i, (t, _a) in enumerate(calls)
        ],
        "final_text": text[:2000] if text else None,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **telemetry.identity(result),
    }
    if grader_traceback:
        rec["grader_traceback"] = grader_traceback
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    with INBOX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    return state
