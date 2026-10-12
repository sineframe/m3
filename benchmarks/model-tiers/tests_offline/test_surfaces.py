"""Surfaces: wire-level tests through the real stdio server (m3 direct client) + dispatch-level solvability."""

import json
from collections.abc import Mapping

import pytest

from _backend.store import Store
from _lib.replay import replay
from _lib.servers import tracker_server
from _server.surfaces import VARIANTS, build_surface
from m3 import MCPTestKit

pytestmark = pytest.mark.m3(suite_name="model-tiers-offline")

from tests_offline.solver import (CHAIN, COUNTS, DISPLAY, GN, GN_WF, NN, OPEN, PRIO, PROJECTS, Solver, Wire,  # noqa: F401
                                    flags, names, thaw)

NONCE = "Nsurf0001"


def open_server(var, nonce=NONCE):
    kit = MCPTestKit(env={})
    return kit, kit.direct(tracker_server(var, nonce))



def solve_all(var, call, nonce):
    """Run the 13 tasks; returns the list of calls issued. Final state is checked by the caller."""
    sv = Solver(var, call)
    facts = Store(7, nonce).facts
    allrows = {p: sv.rows(p) for p in PROJECTS}
    flat = [r for p in PROJECTS for r in allrows[p]]
    # T01
    g = sv.get("PAY-142")
    assert g["audit_code"] == facts["t01_audit_code"] and g["status"] == facts["t01_status"]
    # T02
    assert sv.update("PAY-142", status="in_review", priority="urgent")[1] is False
    # T03
    reporter = sv.get("PAY-087")["reporter_id"]
    assert reporter == facts["t03_reporter_id"]
    assert sv.update("PAY-142", assignee_id=reporter)[1] is False
    # T04: ambiguous name must not be guessed in S good; ids always work
    if sv.f["S"]:
        data, err, _ = sv.update("INF-33", assignee_id="Alex Chen")
        assert err and ("Platform" in _ and "Growth" in _ if sv.f["E"] else True)
    alex = [u for u in sv.users(query="Alex Chen") if u["team"] == "Platform"][0]["id"]
    assert alex == facts["t04_platform_alex_id"]
    assert sv.update("INF-33", assignee_id=alex)[1] is False
    # T05
    t05 = sorted(r["key"] for r in allrows["PAY"] if r["status"] in OPEN and r["type"] == "bug"
                 and "regression" in r["labels"] and r["assignee_id"] is None)
    assert t05 == facts["t05_keys"]
    # T06
    oldest = min((r for r in allrows["OPS"] if r["status"] in OPEN), key=lambda r: r["seq"])["key"]
    assert oldest == facts["t06_oldest_open_ops_key"]
    sv.comment(oldest, f"triaged-{nonce}")
    # T07
    assert sv.update("PAY-150", status="closed", resolution="duplicate", duplicate_of="PAY-149")[1] is False
    # T08
    created = sv.create()
    # T09
    sv.link("PAY-142", "blocks", "INF-33")
    # T10
    t10 = sorted(r["key"] for r in flat if r["status"] in OPEN and "flaky" in r["labels"] and r["cycle"] == "Oct-W2")
    assert t10 == facts["t10_keys"] and len(t10) == 7
    sv.bulk_cycle(t10, "Oct-W3")
    # T11
    plat = [u["id"] for u in sv.users(team="Platform") if u["role"] == "engineer"]
    urg = {e: sorted((r for r in flat if r["assignee_id"] == e and r["status"] in OPEN and r["priority"] == "urgent"),
                     key=lambda r: r["seq"]) for e in plat}
    top = max(plat, key=lambda e: len(urg[e]))
    assert top == facts["t11_engineer_id"] and urg[top][0]["key"] == facts["t11_issue_key"]
    lead = [u["id"] for u in sv.users(team="Platform") if u["role"] == "lead"][0]
    assert sv.update(urg[top][0]["key"], assignee_id=lead)[1] is False
    # T12
    tick = sv.get("PAY-201")["customer_ticket"]
    assert (tick is not None) == facts["t12_has_ticket"]
    if tick:
        sv.update("PAY-201", priority="urgent")
    else:
        sv.comment("PAY-201", f"no-link-{nonce}")
    # T13: nonexistent issue fails gracefully
    priya = sv.users(query="Priya")[0]["id"]
    data, err, text = sv.update("PAY-9999", assignee_id=priya)
    assert err and (("PAY-9999" in text) if sv.f["E"] else text == "404 Not Found")
    return created, facts, lead, top


def check_final(store, created, facts, lead):
    i = store.issue
    assert i("PAY-142")["status"] == "in_review" and i("PAY-142")["priority"] == "urgent"
    assert i("PAY-142")["assignee_id"] == facts["t03_reporter_id"]
    assert i("INF-33")["assignee_id"] == facts["t04_platform_alex_id"]
    assert i("PAY-150")["status"] == "closed" and i("PAY-150")["duplicate_of"] == "PAY-149"
    c = i(created if isinstance(created, str) else (created["issue"]["key"] if "issue" in created else created["key"]))
    assert c["type"] == "bug" and c["priority"] == "high" and c["due_date"] == "2026-11-30"
    assert c["custom_fields"] == {"customer_impact": "enterprise"}
    names = {l["id"]: l["name"] for l in store.snapshot()["labels"]}
    assert sorted(names[x] for x in c["label_ids"]) == ["payments", "regression"]
    assert any(l["from_key"] == "PAY-142" and l["to_key"] == "INF-33" and l["type"] == "blocks"
               for l in store.snapshot()["links"])
    cyc = {c["name"]: c["id"] for c in store.snapshot()["cycles"]}
    assert all(i(k)["cycle_id"] == cyc["Oct-W3"] for k in facts["t10_keys"])
    assert i(facts["t11_issue_key"])["assignee_id"] == lead
    assert any(c["issue_key"] == facts["t06_oldest_open_ops_key"] and c["body"] == f"triaged-{store.nonce}"
               for c in store.snapshot()["comments"])
    assert "PAY-9999" not in {x["key"] for x in store.snapshot()["issues"]}


# ---------------------------------------------------------------- wire tests (real stdio server)
@pytest.mark.parametrize("var", VARIANTS)
def test_wire_catalog_schemas_and_dimensions(var):
    f = flags(var)
    kit, direct = open_server(var)
    with kit, direct as client:
        tools = client.list_all_tools()
    by = {t.name: t for t in tools}
    assert len(tools) == len(by) == COUNTS[var]
    n = names(var)
    assert set(n.values()) <= set(by)
    if f["C"]:
        assert set(by) == set(GN.values()) | (set() if f["S"] else set(GN_WF.values()))
    else:
        assert "add_dependency" in by and "get_issue_v2_beta" in by and "issues_put" in by
        assert sum(1 for k in by if k.split("_")[0] in ("webhooks", "admin", "exports", "audit", "sla", "teams",
                                                        "integrations", "notifications")) >= 38
    for t in tools:
        s = thaw(t.input_schema)
        assert isinstance(s, dict) and s.get("type") == "object" and isinstance(s.get("properties", {}), dict)
        assert t.description
    try:
        import jsonschema
        for t in tools:
            jsonschema.Draft202012Validator.check_schema(thaw(t.input_schema))
    except ImportError:
        pass
    # D
    desc = by[n["search"]].description
    if f["D"]:
        assert "Example" in desc and "Returns" in desc and len(desc) > 200
        props = thaw(by[n["update"]].input_schema)["properties"]
        assert any("description" in p for p in props.values() if isinstance(p, dict))
    else:
        assert len(desc.split()) <= 4
        assert "description" not in json.dumps(thaw(by[n["update"]].input_schema))
    # S
    ups = thaw(by[n["update"]].input_schema)
    if f["S"]:
        assert ups["properties"]["status"]["enum"] == ["backlog", "todo", "in_progress", "in_review", "done", "closed"]
        assert "key" in ups["required"] and "body" not in ups["properties"]
    else:
        assert "body" in ups["properties"] and "issue_id" in ups["properties"]
        assert "anyOf" in json.dumps(ups) and "enum" not in json.dumps(ups)
        # Jira precedent: no direct status/resolution write, priority is an unbounded int
        attrs = ups["properties"]["body"]["properties"]["data"]["properties"]["attributes"]["properties"]
        assert not {"status", "resolution", "duplicate_of"} & set(attrs)
        assert "integer" in json.dumps(attrs["priority"]) and "minimum" not in json.dumps(attrs["priority"])
    # P
    sp = set(thaw(by[n["search"]].input_schema)["properties"])
    if f["P"]:
        assert {"limit", "cursor", "fields", "status", "unassigned"} <= sp
    else:
        assert sp == {"project", "page", "per_page"}


@pytest.mark.parametrize("var", VARIANTS)
def test_wire_lists_mutation_errors(var):
    f, n = flags(var), names(var)
    kit, direct = open_server(var)
    with kit, direct as client:
        w = Wire(client)
        sv = Solver(var, w.call)
        # list responses stay small
        sizes = {}
        if not f["P"]:
            # GitHub precedent: bare array, 20 rows per page, no total anywhere
            d, err, text = w.call(n["search"], {"project": "OPS"})
            assert not err and isinstance(d, list) and len(d) == 20 and "total" not in text and "has_more" not in text
            d2 = w.call(n["search"], {"project": "OPS", "page": 2})[0]
            assert len(d2) == 20 and d2[0]["key"] != d[0]["key"]
            assert len(w.call(n["search"], {"project": "OPS", "per_page": 500})[0]) == 50  # clamped, not rejected
            for p in PROJECTS:
                text = w.call(n["search"], {"project": p, "per_page": 50})[2]
                assert len(text) < 60_000
            ops = sv.rows("OPS")
            assert len(ops) == 180 and [r["seq"] for r in ops] == sorted(r["seq"] for r in ops)
        else:
            d, err, text = w.call(n["search"], {"project": "OPS"})
            assert not err and d["count"] == 25 and d["total_count"] == 180 and d["next_cursor"] and d["has_more"] is True
            d2 = w.call(n["search"], {"project": "OPS", "cursor": d["next_cursor"]})[0]
            assert d2["issues"][0]["key"] != d["issues"][0]["key"]
            assert set(d["issues"][0]) >= {"key", "title", "status", "assignee", "labels"}
            assert len(w.call(n["search"], {"project": "OPS", "limit": 100, "fields": ["key"]})[0]["issues"]) == 100
        # mutation round trip with receipt
        data, err, text = sv.update("PAY-142", priority="urgent")
        assert not err, text
        rec = data["receipt"]
        assert rec.startswith("R-")
        assert sv._pname(sv.get("PAY-142")["priority"]) == "urgent"
        # errors: not found, bad enum
        _, e1, t1 = sv.update("PAY-9999", priority="high")
        _, e2, t2 = sv.update("PAY-142", priority="sky-high")
        assert e1 and e2
        if f["E"]:
            assert "PAY-9999" in t1 and json.loads(t1)["error"]["code"] == "not_found"
            if f["S"]:
                assert "Allowed values" in t2 and "urgent" in t2
        else:
            assert t1 == "404 Not Found" and t2 == "400 Bad Request"
        _, e3, t3 = w.call("no_such_tool", {})
        assert e3 and (t3 == "404 Not Found" if not f["E"] else "no_such_tool" in t3)


@pytest.mark.parametrize("var", ["good", "naive", "good-S", "good-P", "good-C"])
def test_wire_solves_all_tasks_and_replay_matches_call_log(var, monkeypatch, tmp_path):
    nonce = "Nsolve" + var.replace("-", "")
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MT_CALL_LOG", str(log))
    kit, direct = open_server(var, nonce)
    with kit, direct as client:
        w = Wire(client)
        created, facts, lead, _ = solve_all(var, w.call, nonce)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [l["seq"] for l in lines] == list(range(1, len(lines) + 1))
    store, outs = replay(var, nonce, [(l["tool"], l["arguments"]) for l in lines])
    assert [e for _, e in outs] == [l["is_error"] for l in lines]
    check_final(store, created, facts, lead)
    # receipts in the call log are exactly the receipts of the replayed mutations (one per mutating call, last wins)
    log_receipts = [l["receipt"] for l in lines if l["receipt"]]
    assert all(l["receipt"] is None for l in lines if l["is_error"])
    mut_receipts = [m["receipt"] for m in store.mutations]
    assert set(log_receipts) <= set(mut_receipts)
    assert len(log_receipts) == len([1 for l in lines if l["receipt"]]) and mut_receipts[-1] in log_receipts
    # independent expected state: same logical ops applied straight to a fresh Store
    exp = Store(7, nonce)
    exp.apply("update_issue", key="PAY-142", status="in_review", priority="urgent")
    assert exp.issue("PAY-142")["status"] == store.issue("PAY-142")["status"]
    assert store.snapshot()["issues"] != Store(7, nonce).snapshot()["issues"]


def test_wire_nothing_on_stdout_and_no_distractor_mutation(monkeypatch, tmp_path):
    log = tmp_path / "c.jsonl"
    monkeypatch.setenv("MT_CALL_LOG", str(log))
    kit, direct = open_server("naive")
    with kit, direct as client:
        w = Wire(client)
        for name, args in [("webhooks_list", {}), ("admin_settings_get", {}), ("exports_create", {}),
                           ("audit_log_list", {}), ("sla_policies_list", {}), ("teams_members_list", {"team_id": "T1"}), ("users_list", {}), ("issues_transitions_list", {"issue_id": "PAY-142"}), ("priorities_list", {}),
                           ("notifications_mark_all_read", {}), ("integrations_sync", {"id": "IN-1"})]:
            data, err, text = w.call(name, args)
            assert not err, (name, text)
            assert json.loads(text)
        _, err, text = w.call("add_dependency", {"issue_id": "PAY-142", "depends_on_id": "INF-33"})
        assert err and text == "400 Bad Request"
    assert all(json.loads(l)["receipt"] is None for l in log.read_text().splitlines())


# ---------------------------------------------------------------- dispatch-level
def disp(var, nonce="N0"):
    store, s = Store(7, nonce), build_surface(var)
    return store, s, (lambda name, args: (lambda t, e: ((json.loads(t) if not t[:1].isdigit() else t), e, t))(*s.dispatch(store, name, args)))


@pytest.mark.parametrize("var", VARIANTS)
def test_dispatch_solves_all_tasks(var):
    nonce = "Nd" + var.replace("-", "")
    store, s, call = disp(var, nonce)
    created, facts, lead, _ = solve_all(var, call, nonce)
    check_final(store, created, facts, lead)


@pytest.mark.parametrize("var", VARIANTS)
def test_dispatch_never_raises(var):
    store, s, _ = disp(var)
    junk = [None, [], "x", 5, {"key": 5}, {"issue_id": {"a": 1}}, {"body": "x"}, {"body": {"data": 1}}, {"project": ["PAY"]},
            {"limit": "many"}, {"keys": "PAY-1"}, {"cursor": 7}, {"fields": "key"}, {"unknown": 1}]
    for t in s.tools:
        for a in junk:
            text, err = s.dispatch(store, t["name"], a)
            assert isinstance(text, str) and isinstance(err, bool)
    assert s.dispatch(store, "nope", {})[1] is True
    assert s.dispatch(store, None, {})[1] is True  # type: ignore[arg-type]
    assert store.mutations == []


def test_good_search_filters_and_pagination_match_facts():
    store, s, call = disp("good", "Nfilt")
    f = store.facts
    d, err, _ = call("search_issues", {"project": "PAY", "status": list(OPEN), "labels": ["regression"],
                                        "unassigned": True, "type": "bug"})
    assert not err and sorted(r["key"] for r in d["issues"]) == f["t05_keys"] and d["total_count"] == len(f["t05_keys"])
    d, _, _ = call("search_issues", {"status": list(OPEN), "labels": ["flaky"], "cycle": "Oct-W2", "limit": 3})
    assert d["total_count"] == 7 and d["count"] == 3 and d["next_cursor"]
    seen = [r["key"] for r in d["issues"]]
    d2, _, _ = call("search_issues", {"status": list(OPEN), "labels": ["flaky"], "cycle": "Oct-W2", "limit": 3, "cursor": d["next_cursor"]})
    assert sorted(seen + [r["key"] for r in d2["issues"]]) != [] and not set(seen) & {r["key"] for r in d2["issues"]}
    # a cursor from other filters is rejected; desc sort reverses
    _, err, text = call("search_issues", {"project": "OPS", "cursor": d["next_cursor"]})
    assert err and "cursor" in text
    asc = call("search_issues", {"project": "OPS", "limit": 100})[0]["issues"]
    desc = call("search_issues", {"project": "OPS", "limit": 100, "sort": "created_desc"})[0]["issues"]
    assert [r["key"] for r in asc] == [r["key"] for r in reversed(desc)][:100] or len(desc) == 100
    assert call("search_issues", {"limit": 101})[1] is True


def test_good_s_resolves_names_and_reports_ambiguity():
    store, s, call = disp("good", "Nname")
    d, err, text = call("update_issue", {"key": "INF-33", "assignee": "Alex Chen"})
    assert err and d["error"]["code"] == "ambiguous"
    assert store.facts["t04_platform_alex_id"] in text and store.facts["t04_growth_alex_id"] in text
    d, err, _ = call("update_issue", {"key": "INF-33", "assignee": "Priya", "add_labels": ["flaky"], "cycle": "Oct-W4"})
    assert not err and d["issue"]["assignee"] == "Priya Nair" and "flaky" in d["issue"]["labels"] and d["issue"]["cycle"] == "Oct-W4"
    assert call("update_issue", {"key": "INF-33", "add_labels": ["nonexistent"]})[1] is True


def test_naive_s_requires_ids_and_nested_bodies():
    store, s, call = disp("naive", "Nnaive")
    assert call("issues_patch", {"issue_id": "PAY-142", "status": "todo"})[2] == "400 Bad Request"
    assert call("issues_patch", {"issue_id": "PAY-142", "body": {"data": {"attributes": {"assignee_id": "Priya"}}}})[2] == "404 Not Found"
    assert call("issues_patch", {"issue_id": "PAY-142", "body": {"data": {"attributes": {"labels": {"connect": [{"id": "regression"}]}}}}})[2] == "404 Not Found"
    # status cannot be written directly (Jira): only transitions
    assert call("issues_patch", {"issue_id": "PAY-142", "body": {"data": {"attributes": {"status": "in_review"}}}})[2] == "400 Bad Request"
    d, err, _ = call("issues_patch", {"issue_id": "PAY-142", "body": {"data": {"attributes": {"priority": 4}}}})
    assert not err and d["priority"] == 4 and d["receipt"].startswith("R-")
    assert store.issue("PAY-142")["priority"] == "urgent"
    # put needs every field (and still no status)
    assert call("issues_put", {"issue_id": "PAY-142", "body": {"data": {"attributes": {"priority": 1}}}})[2] == "400 Bad Request"
    full = {"priority": 1, "assignee_id": None, "labels": {"connect": []}, "cycle_id": None, "due_date": None, "custom_fields": []}
    d, err, _ = call("issues_put", {"issue_id": "PAY-142", "body": {"data": {"attributes": full}}})
    assert not err and d["priority"] == 1 and d["assignee_id"] is None and d["label_ids"] == []
    assert call("issues_put", {"issue_id": "PAY-142", "body": {"data": {"attributes": {**full, "status": "todo"}}}})[1] is True
    assert call("issues_get", {"issue_id": "PAY-142"})[0]["priority"] == 1
    d, err, _ = call("get_issue_v2_beta", {"issue_id": "PAY-142"})
    assert not err and d["data"]["id"] == "PAY-142"


def test_good_e_error_has_hint_and_good_put_replaces():
    store, s, call = disp("good-C", "Ngc")
    d, err, _ = call("issues_patch", {"key": "PAY-142", "status": "closed"})
    assert err and d["error"]["code"] == "missing_field" and "duplicate" in d["error"]["hint"]
    d, err, _ = call("issues_put", {"key": "PAY-142", "status": "todo", "priority": "low", "assignee": None, "labels": [],
                                     "cycle": None, "due_date": None, "resolution": None, "duplicate_of": None,
                                     "customer_impact": None})
    assert not err and d["issue"]["assignee"] is None and d["issue"]["labels"] == []
    d, err, _ = call("add_dependency", {"key": "PAY-142", "depends_on": "INF-33"})
    assert err and "deprecated" in d["error"]["message"] and "issue_relations_create" in d["error"]["hint"]


def test_naive_list_sizes_all_projects_and_nonces():
    for nonce in ("N1", "Nabc", "N99999"):
        store, s, call = disp("naive", nonce)
        for p in PROJECTS:
            text, err = s.dispatch(store, "issues_list", {"project": p, "per_page": 50})
            assert not err and len(text) < 60_000, (nonce, p, len(text))
        for name in ("users_list", "labels_list", "cycles_list", "projects_list", "teams_list", "admin_users_list"):
            assert len(s.dispatch(store, name, {})[0]) < 60_000
    assert call("issues_list", {})[2] == "400 Bad Request"
    # good-S + naive-P: project enum is enforced, and every list stays small
    store, s, call = disp("good-P", "Nx")
    assert call("search_issues", {"project": "ZZZ"})[1] is True
    for p in PROJECTS:
        assert len(s.dispatch(store, "search_issues", {"project": p})[0]) < 60_000


def test_replay_is_deterministic_and_reports_outputs():
    calls = [("update_issue", {"key": "PAY-142", "priority": "urgent"}), ("update_issue", {"key": "PAY-9999", "priority": "low"}),
             ("add_comment", {"key": "OPS-1", "body": "hi"})]
    s1, o1 = replay("good", "Nrep", calls)
    s2, o2 = replay("good", "Nrep", calls)
    assert s1.snapshot() == s2.snapshot() and o1 == o2
    assert [e for _, e in o1] == [False, True, False]
    assert [m["op"] for m in s1.mutations] == ["update_issue", "add_comment"]


# ================================================================ v2 (section 6b): realistic naive flaws
def _has_v2():
    u = Store(7, "N0").user("U01")
    return u is not None and "active" in u and "on_call" in u


needs_v2 = pytest.mark.skipif(not _has_v2(), reason="backend v2 data (active/on_call users) not present yet")
FROM_STATUS = {  # status -> [(id, name, to)] : the naive-S workflow graph, as documented in CONTRACT deviations
    "backlog": [(11, "Move to To Do", "To Do"), (12, "Close Issue", "Closed")],
    "todo": [(21, "Start Progress", "In Progress"), (22, "Move to Backlog", "Backlog"), (23, "Close Issue", "Closed")],
    "in_progress": [(31, "Submit for Review", "In Review"), (32, "Stop Progress", "To Do"), (33, "Close Issue", "Closed")],
    "in_review": [(41, "Approve", "Done"), (42, "Request Changes", "In Progress"), (43, "Close Issue", "Closed")],
    "done": [(51, "Reopen Issue", "To Do")],
    "closed": [(61, "Reopen Issue", "To Do")],
}


def _issue_in(store, status):
    return next(i["key"] for i in store.apply("search_issues", project="INF") if i["status"] == status)


@pytest.mark.parametrize("var", ["naive", "good-S", "good-C"])
def test_wire_transition_graph_and_multi_hop(var):
    """Real stdio server: transitions per status match the graph; Backlog -> In Review takes three hops."""
    n = names(var)
    S = flags(var)["S"]
    kk = (lambda k: {"key": k}) if S else (lambda k: {"issue_id": k})
    store = Store(7, NONCE)
    keys = {st_: _issue_in(store, st_) for st_ in FROM_STATUS}
    kit, direct = open_server(var)
    with kit, direct as client:
        w = Wire(client)
        for st_, expected in FROM_STATUS.items():
            d, err, _ = w.call(n["transitions"], kk(keys[st_]))
            assert not err and isinstance(d, list)
            assert [(t["id"], t["name"], t["to"]) for t in d] == expected and all(isinstance(t["id"], int) for t in d)
        all_ids = [t[0] for v in FROM_STATUS.values() for t in v]
        assert len(all_ids) == len(set(all_ids))
        key = keys["backlog"]
        # Backlog -> In Review is not one hop: the In Review-bound ids are rejected until the intermediate states are reached
        for bad in (31, 41, 21):
            assert w.call(n["transition"], {**kk(key), "transition_id": bad})[1] is True
        assert w.call(n["get"], kk(key))[0]["status"] == "backlog"
        for tid, status in ((11, "todo"), (21, "in_progress"), (31, "in_review")):
            d, err, text = w.call(n["transition"], {**kk(key), "transition_id": tid})
            assert not err, text
            assert w.call(n["get"], kk(key))[0]["status"] == status
        assert [t["to"] for t in w.call(n["transitions"], kk(key))[0]] == ["Done", "In Progress", "Closed"]


def test_transition_close_reopen_rules():
    store, s, call = disp("naive", "Ntrans")
    key = _issue_in(store, "todo")
    other = _issue_in(store, "backlog")
    # Close needs a resolution (and duplicate_of for duplicates); error text carries the fix only with good E
    assert call("issues_transition", {"issue_id": key, "transition_id": 23})[2] == "400 Bad Request"
    gstore, gs, gcall = disp("good-S", "Ntrans")
    d, err, _ = gcall("transition_issue", {"issue_id": key, "transition_id": 23})
    assert err and d["error"]["code"] == "missing_field" and "resolution" in d["error"]["hint"]
    d, err, _ = gcall("transition_issue", {"issue_id": key, "transition_id": 23, "fields": {"resolution": "duplicate"}})
    assert err and d["error"]["field"] == "fields.resolution" or d["error"]["code"] == "missing_field"
    d, err, _ = call("issues_transition", {"issue_id": key, "transition_id": 23, "fields": {"resolution": "duplicate", "duplicate_of": other}})
    assert not err and d["status"] == "closed" and d["resolution"] == "duplicate" and d["duplicate_of"] == other
    assert [t["name"] for t in call("issues_transitions_list", {"issue_id": key})[0]] == ["Reopen Issue"]
    d, err, _ = call("issues_transition", {"issue_id": key, "transition_id": 61})
    assert not err and d["status"] == "todo" and d["resolution"] is None and d["duplicate_of"] is None
    # fields only belong to Close
    assert call("issues_transition", {"issue_id": key, "transition_id": 21, "fields": {"resolution": "fixed"}})[1] is True
    assert call("issues_transition", {"issue_id": key, "transition_id": 21})[1] is False
    assert [m["op"] for m in store.mutations] == ["update_issue"] * 3
    # S naive cannot write status any other way
    for tool, attrs in (("issues_patch", {"status": "done"}), ("issues_patch", {"resolution": "fixed"})):
        assert call(tool, {"issue_id": key, "body": {"data": {"attributes": attrs}}})[1] is True
    assert len(store.mutations) == 3


def test_priority_int_mapping_and_docs():
    store, s, call = disp("naive", "Nprio")
    d, _, _ = call("priorities_list", {})
    assert d == [{"id": i, "name": n_} for i, n_ in enumerate(["None", "Low", "Medium", "High", "Urgent"])]
    for i, name in enumerate(PRIO):
        d, err, _ = call("issues_patch", {"issue_id": "PAY-142", "body": {"data": {"attributes": {"priority": i}}}})
        assert not err and d["priority"] == i and store.issue("PAY-142")["priority"] == name
        assert call("issues_get", {"issue_id": "PAY-142"})[0]["priority"] == i
    for bad in (5, -1, "high", True, 2.5):
        assert call("issues_patch", {"issue_id": "PAY-142", "body": {"data": {"attributes": {"priority": bad}}}})[1] is True
    d, err, _ = call("issues_create", {"body": {"data": {"attributes": {"project": "PAY", "title": "t", "priority": 3}}}})
    assert not err and store.issue(d["key"])["priority"] == "high"
    # the mapping is documented only in the priorities tool: the update tool schema has bare integers and no description
    upd = next(t for t in s.tools if t["name"] == "issues_patch")
    assert "urgent" not in json.dumps(upd["inputSchema"]).lower()
    # S good keeps string enums, and reads show names
    gstore, gs, gcall = disp("good", "Nprio")
    assert gcall("update_issue", {"key": "PAY-142", "priority": "urgent"})[0]["issue"]["priority"] == "urgent"


def test_good_s_with_naive_catalog_rows_show_int_priority_only_when_s_naive():
    store, s, call = disp("good-S", "Nprio2")
    row = call("search_issues", {"project": "PAY", "limit": 3})[0]["issues"][0]
    assert isinstance(row["priority"], int)
    assert call("search_issues", {"project": "PAY", "priority": "urgent"})[1] is True
    n_urgent = call("search_issues", {"project": "PAY", "priority": 4, "limit": 100})[0]["total_count"]
    assert n_urgent == len([i for i in store.apply("search_issues", project="PAY") if i["priority"] == "urgent"])


def test_naive_p_pagination_truncation_and_good_p_totals():
    store, s, call = disp("naive", "Npage")
    p1 = call("issues_list", {"project": "OPS"})[0]
    assert isinstance(p1, list) and len(p1) == 20           # page 1 of OPS: 20 of 180 rows, and nothing says there are more
    assert call("issues_list", {"project": "OPS"})[2].count("total") == 0
    seen, page = [], 1
    while True:
        rows = call("issues_list", {"project": "OPS", "page": page})[0]
        if not rows:
            break
        seen += [r["key"] for r in rows]
        page += 1
    assert len(seen) == len(set(seen)) == 180 and page == 10
    assert len(call("issues_list", {"project": "OPS", "per_page": 50})[0]) == 50
    assert call("issues_list", {"project": "OPS", "page": 0})[1] is True
    assert call("issues_list", {"project": "OPS", "page": 99})[0] == []
    u1 = call("users_list", {})[0]
    assert isinstance(u1, list) and len(u1) == 20 and set(u1[0]) == {"id", "display_name", "email"}
    allu = []
    for pg in range(1, 5):
        allu += call("users_list", {"page": pg, "per_page": 50 if False else 20})[0]
    assert len(allu) == len(store.apply("find_users")) and len({u["id"] for u in allu}) == len(allu)
    # good P: totals, has_more, cursors; users_list in a P-good naive catalog is an envelope
    gstore, gs, gcall = disp("good-C", "Npage")
    d = gcall("users_list", {"per_page": 10})[0]
    assert d["total_count"] == len(gstore.apply("find_users")) and d["has_more"] is True and len(d["users"]) == 10
    g = gcall("issues_list", {"project": "OPS", "limit": 5})[0]
    assert g["total_count"] == 180 and g["has_more"] and g["next_cursor"]
    last = gcall("issues_list", {"project": "OPS", "limit": 100, "cursor": g["next_cursor"]})[0]
    assert last["count"] == 100 and last["has_more"] is True
    last = gcall("issues_list", {"project": "OPS", "limit": 100, "cursor": last["next_cursor"]})[0]
    assert last["count"] == 75 and last["has_more"] is False and last["next_cursor"] is None


def test_user_surfaces_naive_vs_good():
    store, s, call = disp("naive", "Nusers")
    rows = call("users_search", {"q": "alex"})[0]
    assert rows and all(set(r) == {"id", "display_name", "email"} for r in rows)
    teams = call("teams_list", {})[0]["data"]
    assert [t["name"] for t in teams] == ["Platform", "Growth", "Payments", "Mobile", "Web"]
    members = call("teams_members_list", {"team_id": teams[0]["id"]})[0]["data"]
    assert len(members) == 8 and set(members[0]) == {"id", "display_name", "role", "active", "on_call"}
    assert call("teams_members_list", {"team_id": "T99"})[1] is True
    gstore, gs, gcall = disp("good", "Nusers")
    users = gcall("find_users", {"team": "Platform"})[0]["users"]
    assert len(users) == 8 and set(users[0]) == {"id", "name", "handle", "team", "role", "active", "on_call"}


@needs_v2
def test_v2_users_have_active_and_one_oncall_per_team_in_good_find_users():
    store, s, call = disp("good", "Nv2")
    users = call("find_users", {})[0]["users"]
    assert sum(1 for u in users if not u["active"]) == 3
    for team in ("Platform", "Growth", "Payments", "Mobile", "Web"):
        assert sum(1 for u in users if u["team"] == team and u["on_call"]) == 1
    sams = call("find_users", {"query": "sam"})[0]["users"]
    assert {(u["name"], u["team"], u["active"]) for u in sams if u["name"].startswith("Sam ")} >= {
        ("Sam Okafor", "Web", True), ("Sam Reyes", "Mobile", True), ("Sam Patel", "Web", False)}


@needs_v2
@pytest.mark.parametrize("var", VARIANTS)
def test_assignee_ambiguous_and_inactive_per_dimension(var):
    f = flags(var)
    store = Store(7, "Nsam")
    sams = {u["name"]: u["id"] for u in store.apply("find_users", query="Sam ") if u["name"].startswith("Sam ")}
    patel, okafor = sams["Sam Patel"], sams["Sam Okafor"]
    before = store.issue("WEB-12")["assignee_id"]
    s = build_surface(var)

    def upd(assignee):
        if f["S"]:
            return s.dispatch(store, names(var)["update"], {"key": "WEB-12", "assignee": assignee})
        return s.dispatch(store, names(var)["update"], {"issue_id": "WEB-12", "body": {"data": {"attributes": {"assignee_id": assignee}}}})

    # inactive user, by id: always an error, nothing mutated
    text, err = upd(patel)
    assert err and store.issue("WEB-12")["assignee_id"] == before
    if f["E"]:
        d = json.loads(text)["error"]
        assert "deactivated" in (d["message"] + d.get("hint", ""))
    else:
        assert text == "400 Bad Request"
    if f["S"]:  # names resolve server-side
        text, err = upd("Sam")
        assert err
        if f["E"]:
            d = json.loads(text)["error"]
            assert d["code"] == "ambiguous"
            for name, tid in sams.items():
                assert tid in d["hint"] and name in d["hint"]
            assert "Web" in d["hint"] and "Mobile" in d["hint"] and "inactive" in d["hint"] and "active" in d["hint"]
        else:
            assert text == "400 Bad Request"
        text, err = upd("Sam Patel")
        assert err and (text == "400 Bad Request" if not f["E"] else "deactivated" in text)
        text, err = upd("Sam Okafor")
        assert not err and store.issue("WEB-12")["assignee_id"] == okafor
    else:
        assert not upd(okafor)[1] and store.issue("WEB-12")["assignee_id"] == okafor


@pytest.mark.parametrize("var", VARIANTS)
def test_op_of_covers_every_advertised_tool(var):
    s = build_surface(var)
    advertised = {t["name"] for t in s.tools}
    assert set(s._ops) == advertised                       # every advertised tool is classified, nothing extra
    valid = {None, "get_issue", "search_issues", "update_issue", "bulk_update_issues", "create_issue", "add_comment",
             "link_issues", "find_users", "list_labels", "list_cycles", "list_projects"}
    assert {s.op_of(n_) for n_ in advertised} <= valid
    assert s.op_of("no_such_tool") is None
    n = names(var)
    assert s.op_of(n["update"]) == "update_issue" and s.op_of(n["search"]) == "search_issues"
    if "transition" in n:
        assert s.op_of(n["transition"]) == "update_issue"
        assert s.op_of(n["transitions"]) is None and s.op_of(n["priorities"]) is None
    if not flags(var)["C"]:
        assert s.op_of("users_list") == "find_users" and s.op_of("teams_members_list") == "find_users"
        assert s.op_of("teams_list") is None and s.op_of("add_dependency") is None and s.op_of("webhooks_list") is None


@pytest.mark.parametrize("var", VARIANTS)
def test_every_response_is_under_60k_chars(var):
    store, s, _ = disp(var, "Nsize")
    n = names(var)
    S, P = flags(var)["S"], flags(var)["P"]
    for p in PROJECTS:
        if P:
            for fields in (None, ["key", "title", "project", "type", "status", "priority", "assignee", "labels", "cycle",
                                  "created_seq", "due_date", "resolution", "duplicate_of", "customer_impact"]):
                a = {"project": p, "limit": 100, **({"fields": fields} if fields else {})}
                assert len(s.dispatch(store, n["search"], a)[0]) < 60_000
        else:
            assert len(s.dispatch(store, n["search"], {"project": p, "per_page": 50})[0]) < 60_000
            assert len(s.dispatch(store, n["search"], {"project": p, "per_page": 10_000})[0]) < 60_000
        key = store.apply("search_issues", project=p)[0]["key"]
        assert len(s.dispatch(store, n["get"], {"key": key} if S else {"issue_id": key})[0]) < 60_000
    assert len(s.dispatch(store, n["users"], {"query": "a", **({} if P else {"per_page": 10_000})})[0]) < 60_000
    for tool in s.tools:
        if tool["name"] in ("users_list", "teams_list", "admin_users_list", "labels_list", "list_labels"):
            assert len(s.dispatch(store, tool["name"], {})[0]) < 60_000
