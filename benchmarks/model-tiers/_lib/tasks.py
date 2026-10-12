"""run_task: run one task prompt against the tracker server, grade it, record it."""

from __future__ import annotations

import importlib.util
import secrets
import traceback
from pathlib import Path
from typing import Any

from _lib import grade, telemetry
from _lib.servers import load_task, tracker_server

SUFFIX = (
    "Use only the tracker tools. End your reply with one line: "
    "`STATUS: DONE <answer>` or `STATUS: FAILED <reason>`."
)


def _load_check(task_dir: Path):
    path = Path(task_dir) / "check.py"
    spec = importlib.util.spec_from_file_location(f"mt_check_{Path(task_dir).name.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def grade_trial(
    task_dir: Path,
    variant: str,
    nonce: str,
    calls: list[tuple[str, dict]],
    answer: str | None,
    *,
    outcome: str | None = "completed",
    error: Any = None,
    native_total: int = 0,
) -> tuple[str, dict[str, Any]]:
    """Pure grading of one finished trial (no agent, no I/O besides loading the task's check.py).

    Replays the wire `calls` on a fresh Store, runs the task check, classifies. Returns
    (state, details); details has subgoals, status_line, outputs, mutations, target_ops
    (per-op attempted/errored/succeeded) and, on a grading exception, grader_traceback
    (state is then "grader_error")."""
    status, _rest = grade.parse_status_line(answer)
    details: dict[str, Any] = {"subgoals": {}, "status_line": status, "outputs": [], "mutations": [],
                               "target_ops": {}, "grader_traceback": None}
    try:
        from _backend.store import Store
        from _lib.replay import replay
        from _server.surfaces import build_surface

        final, outputs = replay(variant, nonce, calls)
        details["outputs"], details["mutations"] = outputs, list(final.mutations)
        initial = Store(7, nonce)
        chk = _load_check(Path(task_dir))(
            grade.CheckContext(initial=initial, final=final, facts=initial.facts, answer=answer, nonce=nonce)
        )
        op_of = build_surface(variant).op_of
        details["subgoals"] = chk.subgoals
        details["target_ops"] = grade.target_op_stats(op_of, chk.target_ops, calls, outputs, final.mutations)
        state = grade.classify(
            outcome, error=error, tracker_calls=calls, outputs=outputs, native_total=native_total,
            check=chk, mutations=details["mutations"], status=status, op_of=op_of,
        )
        return state, details
    except Exception:
        details["grader_traceback"] = traceback.format_exc()
        return "grader_error", details


def run_task(agent: Any, task_dir: Path, variant: str) -> str:
    task_dir = Path(task_dir)
    task = load_task(task_dir)
    nonce = "N" + secrets.token_hex(10)
    prompt = task["prompt"].replace("{nonce}", nonce).rstrip() + "\n\n" + SUFFIX
    result = agent.run(prompt, server=tracker_server(variant, nonce))

    calls = telemetry.wire_tracker_calls(result)
    text = telemetry.final_text(result)
    state, d = grade_trial(
        task_dir, variant, nonce, calls, text,
        outcome=telemetry.outcome_value(result), error=telemetry.error_text(result),
        native_total=telemetry.tool_call_stats(result)["native_total"],
    )
    return grade.record(
        agent, result, task=task.get("id", task_dir.name), band=task.get("band"), variant=variant,
        nonce=nonce, state=state, subgoals=d["subgoals"], status_line=d["status_line"],
        tracker_calls=calls, outputs=d["outputs"], mutations=d["mutations"], text=text,
        grader_traceback=d["grader_traceback"],
    )
