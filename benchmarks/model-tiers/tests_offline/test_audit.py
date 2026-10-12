import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

pytestmark = pytest.mark.m3(suite_name="model-tiers-offline")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import audit
from _backend.store import Store

NONCE = "Ntestaudit0001"


def obs(v):
    return NS(value=v)


def entry(seq, tool, args, text, *, ok=True, correlation="correlated", server="tracker"):
    return NS(
        sequence_start=seq, sequence_end=seq, correlation=correlation, server=obs(server),
        tool=obs(tool), arguments=obs(args), tool_status="success" if ok else "error",
        result=obs(NS(content=[NS(text=text)])), reported=None,
    )


def native_entry(seq):
    return NS(
        sequence_start=seq, sequence_end=seq, correlation="reported_only", server=obs(None),
        tool=obs("Bash"), arguments=obs({"command": "ls"}), tool_status="success",
        result=obs(NS(content=[NS(text="x")])),
        reported=obs(NS(server=obs(None), tool=obs("Bash"))),
    )


class FakeStore:
    def __init__(self, entries_by_id, messages_by_id):
        self.e, self.m = entries_by_id, messages_by_id

    def get_trace_view(self, eid):
        if eid not in self.e:
            return None
        return NS(tool_calls=self.e[eid], messages=[
            NS(role="assistant", content=[NS(kind="text", text=self.m[eid])])
        ])


def make_record(eid, *, state="success", model="claude-haiku-5-5", variant="good", trial=1, task="T01"):
    return {"task": task, "band": "read", "variant": variant, "harness": "claude_code", "model": model,
            "trial": trial, "nonce": NONCE, "execution_id": eid, "outcome": "completed", "error": None,
            "state": state, "subgoals": {"status": True}, "status_line": "DONE",
            "calls": [{"tool": "get_issue", "is_error": False}], "final_text": "trunc",
            "tool_calls": {"native_total": 0}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    f = Store(7, NONCE).facts
    good = f"PAY-142 status {f['t01_status']}\nSTATUS: DONE {f['t01_status']} {f['t01_audit_code']}"
    entries = {
        "e-ok": [entry(1, "get_issue", {"key": "PAY-142"}, "{...}")],
        "e-native": [entry(2, "get_issue", {"key": "PAY-142"}, "{...}"), native_entry(1)],
        "e-wrong": [entry(1, "get_issue", {"key": "PAY-142"}, "{...}")],
    }
    msgs = {"e-ok": good, "e-native": good, "e-wrong": "STATUS: DONE nonsense nope"}
    store = FakeStore(entries, msgs)
    recs = [make_record("e-ok"), make_record("e-wrong", state="success")]
    path = tmp_path / "records.jsonl"
    import json
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n{partial\n", encoding="utf-8")
    monkeypatch.setattr(audit, "REGRADE_CSV", tmp_path / "regrade.csv")
    return NS(src=audit.TraceSource(store=store), path=path, tmp=tmp_path)


def test_trace_calls_ordered_with_native(world):
    rows = audit.trace_calls(world.src.load("e-native"))
    assert [r["tool"] for r in rows] == ["Bash", "get_issue"]
    assert rows[0]["native"] and not rows[0]["tracker"]
    assert rows[1]["tracker"] and rows[1]["args"] == {"key": "PAY-142"} and rows[1]["result"] == "{...}"


def test_missing_trace_is_none(world):
    assert world.src.load("nope") is None and world.src.load(None) is None


def test_regrade_match_and_mismatch(world):
    ok = audit.regrade_record(make_record("e-ok"), world.src)
    assert ok["state"] == "success" and ok["source"] == "trace"
    bad = audit.regrade_record(make_record("e-wrong"), world.src)
    assert bad["state"] != "success"


def test_native_call_counts_as_escaped(world):
    g = audit.regrade_record(make_record("e-native"), world.src)
    assert g["state"] == "escaped"
    assert sum(r["native"] for r in g["rows"]) == 1


def test_no_trace_record_without_args_ungradable(world):
    g = audit.regrade_record(make_record("missing"), world.src)
    assert g["state"] is None


def test_no_trace_record_with_args_gradable(world):
    f = Store(7, NONCE).facts
    rec = make_record("missing")
    rec["calls"] = [{"tool": "get_issue", "args": {"key": "PAY-142"}, "is_error": False}]
    rec["final_text"] = f"STATUS: DONE {f['t01_status']} {f['t01_audit_code']}"
    g = audit.regrade_record(rec, world.src)
    assert g["state"] == "success" and g["source"] == "record"


def test_render_section(world):
    rec = make_record("e-wrong")
    md = audit.render_section(rec, world.src, 1)
    assert "T01 [read] variant=good claude_code/claude-haiku-5-5 trial=1 state=success" in md
    assert NONCE in md or "PAY-142" in md  # prompt present
    assert "-> op `get_issue`" in md and 'args: `{"key":"PAY-142"}`' in md
    assert "MISMATCH" in md
    assert md.rstrip().endswith("- [ ] grader correct?  notes:")


def test_render_truncation(world):
    long = "x" * 5000
    world.src._store.e["e-long"] = [entry(1, "get_issue", {"k": "y" * 900}, long)]
    world.src._store.m["e-long"] = long
    md = audit.render_section(make_record("e-long"), world.src)
    assert "y" * 401 not in md and "x" * 1201 not in md and "x" * 300 in md


def test_pick_sample_deterministic_stratified():
    recs = []
    for m in ("a", "b", "c"):
        for v in ("good", "naive"):
            for t in range(6):
                recs.append(make_record(f"{m}{v}{t}", model=m, variant=v, trial=t,
                                        state="success" if t % 2 else "gave_up"))
    s1 = audit.pick_sample(recs, 8, 0, 3)
    s2 = audit.pick_sample(recs, 8, 0, 3)
    assert [r["execution_id"] for r in s1] == [r["execution_id"] for r in s2]
    assert len(s1) == 8
    assert {r["model"] for r in s1} == {"a", "b", "c"} and {r["variant"] for r in s1} == {"good", "naive"}
    assert len({r["execution_id"] for r in s1}) == 8
    s3 = audit.pick_sample(recs, 100, 0, 3)
    assert len(s3) == len(recs)
    # failures over-represented on average with weight
    fails = sum(r["state"] != "success" for r in audit.pick_sample(recs, 12, 1, 50))
    assert fails >= 6


def test_cli_sample_show_regrade_progress(world, capsys):
    out = world.tmp / "s.md"
    a = audit.build_parser().parse_args(["sample", "--records", str(world.path), "--n", "5", "--out", str(out)])
    audit.cmd_sample(a, world.src)
    text = out.read_text()
    assert text.count("- [ ] grader correct?") == 2
    capsys.readouterr()

    a = audit.build_parser().parse_args(["show", "e-wrong", "--records", str(world.path)])
    audit.cmd_show(a, world.src)
    assert "MISMATCH" in capsys.readouterr().out

    a = audit.build_parser().parse_args(["regrade", "--records", str(world.path)])
    rows = audit.cmd_regrade(a, world.src)
    out = capsys.readouterr().out
    assert "1 disagreements" in out and "success -> " in out
    assert len(rows) == 2 and (world.tmp / "regrade.csv").exists()

    a = audit.build_parser().parse_args(["progress", "--records", str(world.path)])
    c = audit.cmd_progress(a)
    assert c[("claude_code", "claude-haiku-5-5", "good", "success")] == 2


def test_load_records_skips_base_and_partial(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"task":"BASE","state":"success"}\n{"task":"T01"}\n{bad\n')
    assert [r["task"] for r in audit.load_records(p)] == ["T01"]
    assert len(audit.load_records(p, include_base=True)) == 2


def test_task_dirs_cover_both_suites():
    ids = set(audit.task_dirs())
    assert {"T01", "T07", "T12", *(f"H{i:02d}" for i in range(1, 11))} <= ids  # hard suite
    assert {"T02", "T05", "T13"} <= ids  # everyday suite
    assert audit.task_dirs()["H05"].name == "team-rebalance" and audit.task_dirs()["T05"].name == "filter-list"
