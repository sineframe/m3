"""Offline tests for the Trackly store: determinism, invariants, ops, validation."""

import json
import random

import pytest

from _backend.store import DomainError, Store

pytestmark = pytest.mark.m3(suite_name="model-tiers-offline")

OPEN = ("backlog", "todo", "in_progress", "in_review")
NONCES = [f"N{random.Random(1).getrandbits(40):x}{i}" for i in range(300)]


def err(store, op, **kw):
    with pytest.raises(DomainError) as e:
        store.apply(op, **kw)
    return e.value


@pytest.fixture
def s():
    return Store(7, "N0")


def test_determinism_same_nonce():
    a, b = Store(7, "Nabc"), Store(7, "Nabc")
    assert a.snapshot() == b.snapshot() and a.facts == b.facts
    json.dumps(a.snapshot())


def test_facts_vary_across_nonces():
    facts = [Store(7, n).facts for n in NONCES[:50]]
    for k in ("t01_status", "t03_reporter_id", "t05_keys", "t06_oldest_open_ops_key", "t10_keys",
              "t11_engineer_id", "t11_issue_key", "t12_has_ticket"):
        vals = {json.dumps(f[k]) for f in facts}
        assert len(vals) > 1, k


def test_invariants_300_nonces():
    for n in NONCES:
        st = Store(7, n)  # __init__ asserts the planted invariants
        f = st.facts
        assert 4 <= len(f["t05_keys"]) <= 9 and len(f["t10_keys"]) == 7
        assert st.issue("PAY-9999") is None
        for i in st._issues:
            assert len(i["description"]) <= 160
        assert len({i["created_seq"] for i in st._issues}) == 520


def test_counts_and_entities(s):
    snap = s.snapshot()
    per = {}
    for i in snap["issues"]:
        per[i["project"]] = per.get(i["project"], 0) + 1
    assert per == {"PAY": 150, "INF": 45, "OPS": 180, "GRO": 45, "WEB": 50, "MOB": 50}
    assert len(snap["users"]) == 40 and len(snap["labels"]) == 33 and len(snap["cycles"]) == 4
    for team in ("Platform", "Growth", "Payments", "Mobile", "Web"):
        assert sum(u["role"] == "lead" for u in snap["users"] if u["team"] == team) == 1
    assert len(s.apply("find_users", query="alex chen")) == 2
    assert len(s.apply("find_users", query="priya")) == 1


def test_t11_semantics():
    for n in NONCES[:30]:
        st = Store(7, n)
        e = st.facts["t11_engineer_id"]
        assert st.user(e)["team"] == "Platform" and st.user(e)["role"] == "engineer"
        mine = st.apply("search_issues", statuses=list(OPEN), assignee_id=e, priority="urgent")
        assert st.facts["t11_issue_key"] == mine[0]["key"]


def test_search_filters(s):
    r = s.apply("search_issues", project="PAY", unassigned=True, type="bug", label_names=["regression"],
                statuses=list(OPEN))
    assert [i["key"] for i in r] == sorted(s.facts["t05_keys"], key=lambda k: s.issue(k)["created_seq"])
    d = s.apply("search_issues", project="OPS", sort="created_desc")
    assert d[0]["created_seq"] > d[-1]["created_seq"]
    assert s.apply("search_issues", text="3DS retry")
    assert s.apply("search_issues", cycle_name="Oct-W2", label_names=["flaky"])
    assert err(s, "search_issues", statuses=["nope"]).code == "invalid_enum"
    assert err(s, "search_issues", label_names=["nope"]).code == "not_found"
    assert err(s, "search_issues", cycle_name="Nov").code == "not_found"
    assert err(s, "search_issues", project="ZZZ").code == "not_found"
    assert err(s, "search_issues", sort="x").code == "invalid_enum"


def test_get_issue(s):
    s.apply("link_issues", from_key="PAY-149", type="blocks", to_key="PAY-150")
    s.apply("add_comment", key="PAY-149", body="hi")
    a, b = s.apply("get_issue", key="PAY-149"), s.apply("get_issue", key="PAY-150")
    assert a["comments"][-1]["body"] == "hi" and len(a["links"]) == 1 and len(b["links"]) == 1
    e = err(s, "get_issue", key="PAY-1429")
    assert e.code == "not_found" and "Did you mean PAY-14" in e.hint
    assert err(s, "get_issue", key="PAY-9999").code == "not_found"


def test_update_happy_and_errors(s):
    r = s.apply("update_issue", key="PAY-142", status="in_review", priority="urgent")
    assert r["receipt"].startswith("R-") and s.issue("PAY-142")["status"] == "in_review"
    s.apply("update_issue", key="PAY-142", assignee_id="U19", label_ids=["L01"], cycle_id="C2",
            due_date="2026-11-01", custom_fields={"customer_impact": "smb"})
    i = s.issue("PAY-142")
    assert i["assignee_id"] == "U19" and i["cycle_id"] == "C2" and i["custom_fields"] == {"customer_impact": "smb"}
    s.apply("update_issue", key="PAY-142", assignee_id=None)
    assert s.issue("PAY-142")["assignee_id"] is None
    e = err(s, "update_issue", key="PAY-142", status="closed")
    assert e.code == "missing_field" and "wontfix" in e.hint
    assert err(s, "update_issue", key="PAY-142", status="bogus").code == "invalid_enum"
    assert err(s, "update_issue", key="PAY-142", priority="p0").code == "invalid_enum"
    assert err(s, "update_issue", key="PAY-142", assignee_id="U99").code == "not_found"
    assert err(s, "update_issue", key="PAY-142", label_ids=["L99"]).code == "not_found"
    assert err(s, "update_issue", key="PAY-142", cycle_id="C9").code == "not_found"
    assert err(s, "update_issue", key="PAY-142", due_date="11/01/2026").code == "invalid_date"
    assert err(s, "update_issue", key="PAY-142", due_date="2026-02-31").code == "invalid_date"
    assert err(s, "update_issue", key="PAY-142", resolution="duplicate").code == "missing_field"
    assert err(s, "update_issue", key="PAY-142", resolution="duplicate", duplicate_of="PAY-9999").code == "not_found"
    assert err(s, "update_issue", key="PAY-9999", status="todo").code == "not_found"
    assert len(s.mutations) == 3  # failed ops left no trace
    s.apply("update_issue", key="PAY-150", status="closed", resolution="duplicate", duplicate_of="PAY-149")
    assert s.issue("PAY-150")["duplicate_of"] == "PAY-149"


def test_bulk_all_or_nothing(s):
    before = s.snapshot()
    e = err(s, "bulk_update_issues", keys=["PAY-149", "PAY-9999"], priority="low")
    assert e.code == "not_found" and s.snapshot() == before and not s.mutations
    err(s, "bulk_update_issues", keys=["PAY-149", "PAY-150"], status="closed")
    assert s.snapshot() == before
    r = s.apply("bulk_update_issues", keys=["PAY-149", "PAY-150"], priority="low")
    assert r["count"] == 2 and len({x["receipt"] for x in r["results"]}) == 2
    assert [m["seq"] for m in s.mutations] == [1, 2]
    assert err(s, "bulk_update_issues", keys=[], priority="low").code == "missing_field"


def test_create_issue(s):
    r = s.apply("create_issue", project="PAY", title="  New thing ", type="bug", label_ids=["L01"])
    assert r["key"] == "PAY-237" and r["title"] == "New thing" and r["status"] == "backlog"
    assert s.apply("create_issue", project="INF", title="x")["key"] == "INF-46"
    assert err(s, "create_issue", project="PAY", title=" ").code == "missing_field"
    assert err(s, "create_issue", project="ZZZ", title="x").code == "not_found"
    assert err(s, "create_issue", project="PAY", title="x", type="epic").code == "invalid_enum"
    assert err(s, "create_issue", project="PAY", title="x", due_date="soon").code == "invalid_date"
    assert err(s, "create_issue", project="PAY", title="x", assignee_id="U99").code == "not_found"


def test_link_issues(s):
    assert s.apply("link_issues", from_key="PAY-142", type="relates", to_key="INF-33")["receipt"]
    assert err(s, "link_issues", from_key="PAY-142", type="relates", to_key="INF-33").code == "conflict"
    assert err(s, "link_issues", from_key="INF-33", type="relates", to_key="PAY-142").code == "conflict"
    assert err(s, "link_issues", from_key="PAY-142", type="causes", to_key="INF-33").code == "invalid_enum"
    assert err(s, "link_issues", from_key="PAY-142", type="blocks", to_key="PAY-9999").code == "not_found"


def test_add_comment_and_reads_dont_mutate(s):
    s.apply("get_issue", key="PAY-142")
    s.apply("search_issues", project="PAY")
    s.apply("list_labels"), s.apply("list_cycles"), s.apply("list_projects")
    assert s.mutations == []
    s.apply("add_comment", key="PAY-142", body="note")
    assert s.mutations[0]["op"] == "add_comment" and s.mutations[0]["seq"] == 1
    assert err(s, "add_comment", key="PAY-142", body="").code == "missing_field"


def test_find_users(s):
    assert len(s.apply("find_users", team="payments")) == 8
    assert err(s, "find_users", team="Sales").code == "invalid_enum"
    assert s.apply("find_users", query="PRIYA")[0]["name"] == "Priya Nair"


OPS = [
    ("update_issue", {"key": "PAY-142", "status": "in_review", "priority": "urgent"}),
    ("add_comment", {"key": "INF-33", "body": "on it"}),
    ("link_issues", {"from_key": "PAY-142", "type": "blocks", "to_key": "INF-33"}),
    ("bulk_update_issues", {"keys": ["PAY-149", "PAY-150"], "priority": "low"}),
    ("create_issue", {"project": "OPS", "title": "Rotate pager"}),
]


def test_replay_determinism_and_stable_receipts():
    def run(nonce):
        st = Store(7, nonce)
        for op, kw in OPS:
            st.apply(op, **kw)
        return st
    a, b, c = run("Nx"), run("Nx"), run("Ny")
    assert a.snapshot() == b.snapshot() and a.mutations == b.mutations
    assert [m["receipt"] for m in a.mutations] != [m["receipt"] for m in c.mutations]
    assert [m["seq"] for m in a.mutations] == [1, 2, 3, 4, 5, 6]
    assert all(m["receipt"].startswith("R-") and len(m["receipt"]) == 10 for m in a.mutations)


V2_KEYS = ("h01_keys", "h02_team", "h02_count", "h03_unassigned_keys", "h03_assigned_keys", "h03_oncall_id",
           "h04_reporter_id", "h04_lead_id", "h04_keys", "h05_initial_counts", "h06_pairs", "h08_keys",
           "h09_sources")


def test_v2_facts_present_and_vary():
    facts = [Store(7, n).facts for n in NONCES[:60]]
    for k in V2_KEYS:
        assert len({json.dumps(f[k], sort_keys=True) for f in facts}) > 1, k
    f = facts[0]
    assert f["h07_issue"] == "WEB-12" and f["h10_existing"] == ["PAY-150", "PAY-151"]
    assert f["h10_missing"] == "PAY-9998" and f["h10_priya_id"] == f["t13_priya_id"]


def test_v2_invariants_300_nonces():
    """__init__ asserts every h01..h10 invariant; here we re-derive the key ranges independently."""
    for n in NONCES:
        st = Store(7, n)
        f = st.facts
        assert 10 <= len(f["h01_keys"]) <= 18
        assert 9 <= len(f["h03_unassigned_keys"]) + len(f["h03_assigned_keys"]) <= 13
        assert 4 <= len(f["h04_keys"]) <= 7 and 4 <= len(f["h06_pairs"]) <= 6
        assert 5 <= len(f["h08_keys"]) <= 9 and 3 <= len(f["h09_sources"]) <= 5
        c = f["h05_initial_counts"]
        assert sum(v > 3 for v in c.values()) >= 2 and min(c.values()) <= 1
        assert sum(max(0, 3 - v) for v in c.values()) >= sum(max(0, v - 3) for v in c.values()) + 1
        # h05: independently re-derived from queries. Donors are engineers only, the fewest recipient is unique,
        # lead/PM (also "on Platform") hold <= 2 each, inactive Platform members hold 0.
        mem = {u["id"]: u for u in st.apply("find_users", team="Platform")}
        per = {u: len(st.apply("search_issues", statuses=["in_progress"], assignee_id=u)) for u in mem}
        assert all(per[u] <= 2 for u, m in mem.items() if m["role"] != "engineer" and m["active"])
        assert all(per[u] == 0 for u, m in mem.items() if not m["active"])
        assert all(mem[u]["role"] == "engineer" and mem[u]["active"] for u in c)
        assert {u for u, v in per.items() if v > 3} == {u for u, v in c.items() if v > 3} and sum(v > 3 for v in c.values()) == 2
        low = sorted(v for v in c.values() if v <= 3)
        assert low[0] < low[1] and sum(1 for v in c.values() if v == min(c.values())) == 1
        assert any(per[u] >= 1 for u, m in mem.items() if m["role"] == "lead")
        assert len(st._issues) == 520 and all(len(i["description"]) <= 160 for i in st._issues)
        # v1 facts still hold beside v2
        assert 4 <= len(f["t05_keys"]) <= 9 and len(f["t10_keys"]) == 7


def test_h02_h05_semantics_by_query():
    for n in NONCES[:40]:
        st = Store(7, n)
        f = st.facts
        per = {}
        for i in st.apply("search_issues", statuses=list(OPEN), priority="urgent"):
            if i["assignee_id"]:
                t = st.user(i["assignee_id"])["team"]
                per[t] = per.get(t, 0) + 1
        top = sorted(per.values(), reverse=True)
        assert per[f["h02_team"]] == f["h02_count"] == top[0] > top[1] >= top[0] - 2
        for e, cnt in f["h05_initial_counts"].items():
            u = st.user(e)
            assert u["team"] == "Platform" and u["role"] == "engineer" and u["active"]
            assert len(st.apply("search_issues", statuses=["in_progress"], assignee_id=e)) == cnt
        # moved-excess feasibility: greedy "fewest" rebalance ends with everyone <= 3
        cs = dict(f["h05_initial_counts"])
        for e in [e for e in cs if cs[e] > 3]:
            for _ in range(cs[e] - 3):
                cs[min(cs, key=lambda x: cs[x])] += 1
            cs[e] = 3
        assert max(cs.values()) <= 3


def test_users_active_oncall_and_sams(s):
    users = s.apply("find_users")
    assert all({"team", "role", "active", "on_call"} <= set(u) for u in users)
    assert sum(not u["active"] for u in users) == 3
    sams = s.apply("find_users", query="sam")
    assert sorted((u["name"], u["team"], u["active"]) for u in sams) == [
        ("Sam Okafor", "Web", True), ("Sam Patel", "Web", False), ("Sam Reyes", "Mobile", True)]
    assert s.facts["h07_sam_id"] == next(u["id"] for u in sams if u["name"] == "Sam Okafor")
    for n in NONCES[:40]:
        st = Store(7, n)
        for team in ("Platform", "Growth", "Payments", "Mobile", "Web"):
            on = [u for u in st.apply("find_users", team=team) if u["on_call"]]
            assert len(on) == 1 and on[0]["active"], (n, team)
        assert st.facts["h03_oncall_id"] == next(u["id"] for u in st.apply("find_users", team="Platform")
                                                 if u["on_call"])
    assert len({Store(7, n).facts["h03_oncall_id"] for n in NONCES[:40]}) > 1


def test_assign_inactive_user_rejected(s):
    patel = next(u["id"] for u in s.apply("find_users", query="Sam Patel"))
    e = err(s, "update_issue", key="WEB-12", assignee_id=patel)
    assert e.code == "invalid_value" and "user is deactivated" in e.hint and e.field == "assignee_id"
    assert err(s, "bulk_update_issues", keys=["WEB-12"], assignee_id=patel).code == "invalid_value"
    assert err(s, "create_issue", project="WEB", title="x", assignee_id=patel).code == "invalid_value"
    assert not s.mutations
    # searching by an inactive user stays allowed
    s.apply("search_issues", assignee_id=patel)
    ok = s.apply("update_issue", key="WEB-12", assignee_id=s.facts["h07_sam_id"])
    assert ok["assignee_id"] == s.facts["h07_sam_id"]


def test_h06_h01_descriptions(s):
    for dup, orig in s.facts["h06_pairs"].items():
        assert f"Duplicate of {orig}" in s.issue(dup)["description"]
        assert s.issue(orig)["status"] in OPEN
    assert {l["name"] for l in s.apply("list_labels")} >= {"wontfix-candidate", "duplicate-candidate", "sla-breach"}


def test_v2_determinism():
    a, b = Store(7, "Ndet"), Store(7, "Ndet")
    assert a.facts == b.facts and a.snapshot() == b.snapshot()
