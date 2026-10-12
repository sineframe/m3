"""End-to-end golden and failure runs: scripted agents drive the REAL stdio server (m3 direct client)
with MT_CALL_LOG set, then `grade_trial` classifies exactly as `run_task` does."""

import json
import os
from pathlib import Path

import pytest
import yaml

from _backend.store import Store
from _lib.replay import replay
from _lib.servers import tracker_server
from _lib.tasks import grade_trial
from _server.surfaces import VARIANTS, build_surface
from m3 import MCPTestKit
from tests_offline.solver import HARD, OPEN, PROJECTS, Solver, Wire, key_of

pytestmark = pytest.mark.m3(suite_name="model-tiers-offline")

ROOT = Path(__file__).resolve().parents[1]
def _suite_tasks(suite):
    out = {}
    for y in sorted(ROOT.glob("*/task.yaml")):
        meta = yaml.safe_load(y.read_text())
        if meta.get("suite") == suite:
            out[meta["id"]] = y.parent
    return out


HARD_TASKS = _suite_tasks("hard")          # t01, t07, t12, h01..h10
EVERYDAY_TASKS = _suite_tasks("everyday")
TASKS = {**HARD_TASKS, **EVERYDAY_TASKS}
NONCES = ["Ne2eAAA1", "Ne2eBBB2", "Ne2eCCC3"]
# hard suite: 2 nonces per task and variant; everyday suite: 1
GOLDEN_CASES = [(t, n) for t in sorted(HARD_TASKS) for n in NONCES[:2]] + [(t, NONCES[0]) for t in sorted(EVERYDAY_TASKS)]
WRONG_VARIANTS = ["good", "naive"]


# ------------------------------------------------------------------ scripted solutions
def _all_rows(sv):
    return {p: sv.rows(p) for p in PROJECTS}


def g_t01(sv, f, n):
    g = sv.get("PAY-142")
    return f"PAY-142 is {g['status']}, audit code {g['audit_code']}.\nSTATUS: DONE {g['status']} {g['audit_code']}"


def g_t02(sv, f, n):
    sv.update("PAY-142", status="in_review", priority="urgent")
    return "STATUS: DONE PAY-142 is in_review and urgent"


def g_t03(sv, f, n):
    sv.update("PAY-142", assignee_id=sv.get("PAY-087")["reporter_id"])
    return "STATUS: DONE assigned PAY-142 to the reporter of PAY-087"


def g_t04(sv, f, n):
    alex = [u for u in sv.users(query="Alex Chen") if u["team"] == "Platform"][0]["id"]
    sv.update("INF-33", assignee_id=alex)
    return "STATUS: DONE INF-33 assigned to Alex Chen (Platform)"


def g_t05(sv, f, n):
    rows = sv.rows("PAY")
    keys = sorted(r["key"] for r in rows if r["status"] in OPEN and r["type"] == "bug"
                  and "regression" in r["labels"] and r["assignee_id"] is None)
    return f"Found {len(keys)}.\nSTATUS: DONE {len(keys)} issues: {', '.join(keys)}"


def g_t06(sv, f, n):
    ops = sv.rows("OPS")
    oldest = min((r for r in ops if r["status"] in OPEN), key=lambda r: r["seq"])["key"]
    sv.comment(oldest, f"triaged-{n}")
    return f"STATUS: DONE commented on {oldest}"


def g_t07(sv, f, n):
    sv.update("PAY-150", status="closed", resolution="duplicate", duplicate_of="PAY-149")
    return "STATUS: DONE PAY-150 closed as duplicate of PAY-149"


def g_t08(sv, f, n):
    sv.create(title=f"Checkout 500 on retry {n}")
    return "STATUS: DONE created the issue"


def g_t09(sv, f, n):
    sv.link("PAY-142", "blocks", "INF-33")
    return "STATUS: DONE PAY-142 blocks INF-33"


def g_t10(sv, f, n):
    flat = [r for rows in _all_rows(sv).values() for r in rows]
    keys = sorted(r["key"] for r in flat if r["status"] in OPEN and "flaky" in r["labels"] and r["cycle"] == "Oct-W2")
    sv.bulk_cycle(keys, "Oct-W3")
    return f"STATUS: DONE moved {len(keys)} issues"


def g_t11(sv, f, n):
    flat = [r for rows in _all_rows(sv).values() for r in rows]
    plat = [u["id"] for u in sv.users(team="Platform") if u["role"] == "engineer"]
    urg = {e: sorted((r for r in flat if r["assignee_id"] == e and r["status"] in OPEN and r["priority"] == "urgent"),
                     key=lambda r: r["seq"]) for e in plat}
    top = max(plat, key=lambda e: len(urg[e]))
    lead = [u["id"] for u in sv.users(team="Platform") if u["role"] == "lead"][0]
    key = urg[top][0]["key"]
    sv.update(key, assignee_id=lead)
    return f"STATUS: DONE reassigned {key} to {lead}"


def g_t12(sv, f, n):
    if sv.get("PAY-201")["customer_ticket"]:
        sv.update("PAY-201", priority="urgent")
        return "STATUS: DONE bumped PAY-201 to urgent"
    sv.comment("PAY-201", f"no-link-{n}")
    return f"STATUS: DONE commented no-link-{n}"


def g_t13(sv, f, n):
    priya = sv.users(query="Priya")[0]["id"]
    sv.update("PAY-9999", assignee_id=priya)
    return "PAY-9999 does not exist.\nSTATUS: FAILED PAY-9999 not found"


# wrong target: the right kind of operation aimed at the wrong thing
def w_t01(sv, f, n):
    g = sv.get("PAY-143")
    return f"STATUS: DONE {g['status']} {g['audit_code']}"


def w_t02(sv, f, n):
    sv.update("PAY-143", status="in_review", priority="urgent")
    return "STATUS: DONE"


def w_t03(sv, f, n):
    sv.update("PAY-142", assignee_id="U01" if f["t03_reporter_id"] != "U01" else "U02")
    return "STATUS: DONE"


def w_t04(sv, f, n):
    sv.update("INF-33", assignee_id=f["t04_growth_alex_id"])
    return "STATUS: DONE"


def w_t05(sv, f, n):
    sv.rows("INF")
    return "STATUS: DONE 2 issues: INF-1, INF-2"


def w_t06(sv, f, n):
    sv.comment("PAY-100", f"triaged-{n}")
    return "STATUS: DONE"


def w_t07(sv, f, n):
    sv.update("PAY-149", status="closed", resolution="duplicate", duplicate_of="PAY-150")
    return "STATUS: DONE"


def w_t08(sv, f, n):
    sv.create(title=f"Wrong title {n}")
    return "STATUS: DONE"


def w_t09(sv, f, n):
    sv.link("INF-33", "blocks", "PAY-142")
    return "STATUS: DONE"


def w_t10(sv, f, n):
    sv.bulk_cycle(f["t10_keys"][:-1], "Oct-W3")
    return "STATUS: DONE moved 6 issues"


def w_t11(sv, f, n):
    sv.update("PAY-143", assignee_id=f["t11_lead_id"])
    return "STATUS: DONE"


def w_t12(sv, f, n):
    if f["t12_has_ticket"]:
        sv.update("PAY-202", priority="urgent")
    else:
        sv.comment("PAY-202", f"no-link-{n}")
    return "STATUS: DONE"


def w_t13(sv, f, n):
    sv.update("PAY-142", assignee_id=f["t13_priya_id"])
    return "STATUS: FAILED could not do it"


def _hard(fn):
    return lambda sv, f, n: fn(sv, n)


# wrong-target runs for the hard suite: the right kind of operation aimed at the wrong thing
def w_h01(sv, f, n):
    sv.find(["PAY"], labels=["regression"])
    return "STATUS: DONE 3 issues: PAY-100, PAY-101, PAY-102"


def w_h02(sv, f, n):
    sv.find(priority="urgent")
    return "STATUS: DONE nobody has any, 0"


def w_h03(sv, f, n):
    wrong = next(u["id"] for u in sv.users(team="Growth") if u["active"])
    for r in sv.find(["OPS"], labels=["flaky"]):
        sv.update(r["key"], assignee_id=wrong)
    return "STATUS: DONE"


def w_h04(sv, f, n):
    reporter = sv.get("PAY-142")["reporter_id"]
    sv.update("PAY-100", assignee_id=reporter)
    return "STATUS: DONE"


def w_h05(sv, f, n):
    donor = next(u for u in sv.users(team="Platform") if u["role"] == "engineer" and u["active"]
                 and sum(1 for r in sv.find(["PAY", "INF", "OPS", "GRO", "WEB", "MOB"], assignee_id=u["id"])
                         if r["status"] == "in_progress") > 3)
    other = next(u["id"] for u in sv.users(team="Platform") if u["role"] == "lead")
    top = max((r for r in sv.find(assignee_id=donor["id"]) if r["status"] == "in_progress"), key=lambda r: r["seq"])
    sv.update(top["key"], assignee_id=other)  # moves one issue, to the lead, who is not an engineer
    return "STATUS: DONE moved 1 issue"


def w_h06(sv, f, n):
    for r in sv.find(["GRO"], labels=["duplicate-candidate"]):
        sv.update(r["key"], status="closed", resolution="duplicate", duplicate_of="PAY-149")
    return "STATUS: DONE"


def w_h07(sv, f, n):
    sv.update("WEB-12", assignee_id=sv.users(query="Sam", team="Mobile")[0]["id"])
    return "STATUS: DONE"


def w_h08(sv, f, n):
    for r in sv.find(["MOB"], type="bug"):
        if not sv.get(r["key"])["customer_ticket"]:
            sv.update(r["key"], priority="high")
            break
    return "STATUS: DONE changed 1 issue"


def w_h09(sv, f, n):
    sv.create_task("INF", "Follow-up: something else")
    return "STATUS: DONE"


def w_h10(sv, f, n):
    sv.update("PAY-150", assignee_id=sv.users(query="Priya")[0]["id"])
    return "STATUS: DONE"


GOLDEN = {f"T{i:02d}": globals()[f"g_t{i:02d}"] for i in range(1, 14)}
GOLDEN.update({k: _hard(v) for k, v in HARD.items()})
WRONG = {f"T{i:02d}": globals()[f"w_t{i:02d}"] for i in range(1, 14)}
WRONG.update({f"H{i:02d}": globals()[f"w_h{i:02d}"] for i in range(1, 11)})


def bad_call(sv, task, f):
    """One errored call to the task's target tool (no status line afterwards)."""
    S, n = sv.f["S"], sv.n
    nested = lambda **a: {"body": {"data": {"attributes": a}}}
    op = {"T01": "get", "T05": "search", "T06": "comment", "T08": "create", "T09": "link",
          "H01": "search", "H02": "search", "H09": "create"}.get(task, "update")
    if task == "T12":
        op = "update" if f["t12_has_ticket"] else "comment"
    if op == "get":
        args = {"key": "ZZZ-1"} if S else {"issue_id": "ZZZ-1"}
    elif op == "search":
        args = {"project": "ZZZ"} if sv.f["P"] else {}
    elif op == "comment":
        args = {"key": "ZZZ-1", "body": "x"} if S else {"issue_id": "ZZZ-1", **nested(body="x")}
    elif op == "create":
        args = {"project": "ZZZ", "title": "x"} if S else nested(project="ZZZ", title="x")
    elif op == "link":
        args = ({"from_key": "ZZZ-1", "type": "blocks", "to_key": "ZZZ-2"} if S
                else nested(from_issue_id="ZZZ-1", to_issue_id="ZZZ-2", relation_type="blocks"))
    else:
        args = {"key": "ZZZ-1", "status": "in_review"} if S else {"issue_id": "ZZZ-1", **nested(status="in_review")}
    data, err, text = sv.call(n[op], args)
    assert err, (task, op, text)
    return "I could not complete this."


def extra_mutation(sv, nonce):
    """A mutation outside every task's allowed set: change PAY-100's priority."""
    cur = Store(7, nonce).issue("PAY-100")["priority"]
    sv.update("PAY-100", priority="low" if cur != "low" else "high")


# ------------------------------------------------------------------ harness
def drive(variant, nonce, script, tmp_path, monkeypatch, *, extra=False):
    """Run `script(solver, facts, nonce) -> answer` against the real server. Returns (calls, answer, log lines)."""
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MT_CALL_LOG", str(log))
    calls: list[tuple[str, dict]] = []
    sizes: list[int] = []
    kit = MCPTestKit(env={})
    with kit, kit.direct(tracker_server(variant, nonce)) as client:
        wire = Wire(client)

        def call(name, args=None):
            calls.append((name, json.loads(json.dumps(args or {}))))
            out = wire.call(name, args)
            sizes.append(len(out[2]))
            return out

        sv = Solver(variant, call)
        answer = script(sv, Store(7, nonce).facts, nonce)
        if extra:
            extra_mutation(sv, nonce)
    lines = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    drive.sizes = sizes
    return calls, answer, lines


def assert_replay_matches_log(variant, nonce, calls, lines):
    """Replay of the logged calls reproduces the server's errors and mutation receipts exactly."""
    assert [(l["tool"], l["arguments"]) for l in lines] == calls
    assert [l["seq"] for l in lines] == list(range(1, len(lines) + 1))
    store, surface = Store(7, nonce), build_surface(variant)
    for (tool, args), line in zip(calls, lines):
        before = len(store.mutations)
        _text, err = surface.dispatch(store, tool, args)
        assert err == line["is_error"], (tool, args)
        assert line["receipt"] == (store.mutations[-1]["receipt"] if len(store.mutations) > before else None)
    final, outs = replay(variant, nonce, calls)
    assert [m["receipt"] for m in final.mutations] == [m["receipt"] for m in store.mutations]
    assert [e for _, e in outs] == [l["is_error"] for l in lines]


# ------------------------------------------------------------------ tests
@pytest.mark.parametrize("var", VARIANTS)
@pytest.mark.parametrize("task,nonce", GOLDEN_CASES, ids=[f"{t}-nonce-{n[-1]}" for t, n in GOLDEN_CASES])
def test_golden_succeeds(task, nonce, var, tmp_path, monkeypatch):
    calls, answer, lines = drive(var, nonce, GOLDEN[task], tmp_path, monkeypatch)
    assert 0 < len(calls) <= 120
    if os.environ.get("MT_E2E_STATS"):  # calibration: calls needed and largest single response
        with open(os.environ["MT_E2E_STATS"], "a") as fh:
            fh.write(json.dumps({"task": task, "var": var, "nonce": nonce, "calls": len(calls),
                                 "max_chars": max(drive.sizes)}) + "\n")
    assert_replay_matches_log(var, nonce, calls, lines)
    state, d = grade_trial(TASKS[task], var, nonce, calls, answer)
    assert state == "success", (state, d["subgoals"], d["target_ops"])
    assert all(d["subgoals"].values())


@pytest.mark.parametrize("var", VARIANTS)
@pytest.mark.parametrize("task", sorted(TASKS))
def test_zero_calls(task, var):
    nonce = NONCES[0]
    state, _ = grade_trial(TASKS[task], var, nonce, [], "Done.\nSTATUS: DONE all good")
    assert state == "hallucinated"
    state, _ = grade_trial(TASKS[task], var, nonce, [], "Cannot.\nSTATUS: FAILED no idea")
    assert state == ("success" if task == "T13" else "gave_up")


@pytest.mark.parametrize("var", WRONG_VARIANTS)
@pytest.mark.parametrize("task", sorted(WRONG))
def test_wrong_target(task, var, tmp_path, monkeypatch):
    nonce = NONCES[1]
    calls, answer, lines = drive(var, nonce, WRONG[task], tmp_path, monkeypatch)
    assert_replay_matches_log(var, nonce, calls, lines)
    state, d = grade_trial(TASKS[task], var, nonce, calls, answer)
    assert state == "wrong_target", (state, d["subgoals"], d["target_ops"])
    assert any(v["succeeded"] for v in d["target_ops"].values())


@pytest.mark.parametrize("var", WRONG_VARIANTS)
@pytest.mark.parametrize("task", sorted(TASKS))
def test_bad_args_only(task, var, tmp_path, monkeypatch):
    nonce = NONCES[1]
    calls, answer, lines = drive(var, nonce, lambda sv, f, n: bad_call(sv, task, f), tmp_path, monkeypatch)
    assert len(calls) == 1 and lines[0]["is_error"] and lines[0]["receipt"] is None
    state, d = grade_trial(TASKS[task], var, nonce, calls, answer)
    assert state == "bad_args", (state, d["subgoals"], d["target_ops"])
    assert sum(v["errored"] for v in d["target_ops"].values()) == 1
    assert not any(v["succeeded"] for v in d["target_ops"].values())
    # claiming DONE after only failed calls is hallucination
    assert grade_trial(TASKS[task], var, nonce, calls, "STATUS: DONE ok")[0] == "hallucinated"


@pytest.mark.parametrize("var", WRONG_VARIANTS)
@pytest.mark.parametrize("task", sorted(set(HARD_TASKS) | (set(EVERYDAY_TASKS) - {"T13"})))  # T13's check has no no_side_effects subgoal
def test_side_effects(task, var, tmp_path, monkeypatch):
    nonce = NONCES[2]
    calls, answer, lines = drive(var, nonce, GOLDEN[task], tmp_path, monkeypatch, extra=True)
    assert_replay_matches_log(var, nonce, calls, lines)
    state, d = grade_trial(TASKS[task], var, nonce, calls, answer)
    assert state == "side_effects", (state, d["subgoals"])
    assert d["subgoals"]["no_side_effects"] is False


def test_grade_trial_outcomes_and_grader_error(tmp_path):
    t = TASKS["T01"]
    assert grade_trial(t, "good", NONCES[0], [], None, outcome="timed_out")[0] == "timeout"
    assert grade_trial(t, "good", NONCES[0], [], None, outcome="failed")[0] == "harness_error"
    assert grade_trial(t, "good", NONCES[0], [], "STATUS: DONE", native_total=1)[0] == "escaped"
    state, d = grade_trial(t, "no-such-var", NONCES[0], [], "STATUS: DONE")
    assert state == "grader_error" and d["grader_traceback"]
