"""Offline tests for every task check: correct solutions pass for many nonces, wrong ones fail."""

import importlib.util
import random
from pathlib import Path

import pytest
from _backend.store import Store
from _lib.grade import CheckContext

pytestmark = pytest.mark.m3(suite_name="model-tiers-offline")

ROOT = Path(__file__).resolve().parents[1]
NONCES = [
    "N" + "".join(random.Random(i).choice("0123456789abcdef") for _ in range(20))
    for i in range(26)
]
DONE = "\nSTATUS: DONE "
LABEL = {}


def load(task_id):
    path = (
        next(
            d for d in ROOT.glob("*/task.yaml") if f"id: {task_id}\n" in d.read_text()
        ).parent
        / "check.py"
    )
    spec = importlib.util.spec_from_file_location(f"chk_{task_id}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check


CHECKS = {
    s: load(s)
    for s in (
        "T01",
        "T02",
        "T03",
        "T04",
        "T05",
        "T06",
        "T07",
        "T08",
        "T09",
        "T10",
        "T11",
        "T12",
        "T13",
        "H01",
        "H02",
        "H03",
        "H04",
        "H05",
        "H06",
        "H07",
        "H08",
        "H09",
        "H10",
    )
}


def grade(slug, nonce, ops, answer):
    """ops: list of (op, kwargs) applied to a fresh store; returns the Check."""
    initial, final = Store(7, nonce), Store(7, nonce)
    for op, kw in ops:
        final.apply(op, **kw)
    return CHECKS[slug](
        CheckContext(
            initial=initial,
            final=final,
            facts=initial.facts,
            answer=answer,
            nonce=nonce,
        )
    )


def lid(s, name):
    return next(l["id"] for l in s.apply("list_labels") if l["name"] == name)


def facts(n):
    return Store(7, n).facts


@pytest.mark.parametrize("nonce", NONCES)
def test_correct_solutions(nonce):
    f = facts(nonce)
    s = Store(7, nonce)
    st = f["t01_status"].replace("_", " ")
    assert grade(
        "T01",
        nonce,
        [],
        f"PAY-142 is {st}, audit code {f['t01_audit_code']}.{DONE}{st} {f['t01_audit_code']}",
    ).success
    assert grade(
        "T01", nonce, [], f"status {f['t01_status']} code {f['t01_audit_code']}{DONE}ok"
    ).success
    assert grade(
        "T02",
        nonce,
        [("update_issue", dict(key="PAY-142", status="in_review", priority="urgent"))],
        "",
    ).success
    assert grade(
        "T02",
        nonce,
        [
            ("update_issue", dict(key="PAY-142", status="in_review")),
            ("update_issue", dict(key="PAY-142", priority="urgent")),
        ],
        "",
    ).success
    assert grade(
        "T03",
        nonce,
        [("update_issue", dict(key="PAY-142", assignee_id=f["t03_reporter_id"]))],
        "",
    ).success
    assert grade(
        "T04",
        nonce,
        [("update_issue", dict(key="INF-33", assignee_id=f["t04_platform_alex_id"]))],
        "",
    ).success
    keys = f["t05_keys"]
    assert grade(
        "T05",
        nonce,
        [],
        f"Found {len(keys)}:\n{DONE}{len(keys)} issues: {', '.join(keys)}",
    ).success
    assert grade(
        "T05", nonce, [], f"{len(keys)} issues: {', '.join(keys)}\nSTATUS: DONE done"
    ).success  # keys in body
    assert grade(
        "T06",
        nonce,
        [
            (
                "add_comment",
                dict(key=f["t06_oldest_open_ops_key"], body=f"triaged-{nonce}"),
            )
        ],
        "",
    ).success
    assert grade(
        "T07",
        nonce,
        [
            (
                "update_issue",
                dict(
                    key="PAY-150",
                    status="closed",
                    resolution="duplicate",
                    duplicate_of="PAY-149",
                ),
            )
        ],
        "",
    ).success
    ops8 = [
        (
            "create_issue",
            dict(
                project="PAY",
                title=f"Checkout 500 on retry {nonce}",
                type="bug",
                priority="high",
                label_ids=[lid(s, "regression"), lid(s, "payments")],
                due_date="2026-11-30",
                custom_fields={"customer_impact": "enterprise"},
            ),
        )
    ]
    assert grade("T08", nonce, ops8, "").success
    assert grade(
        "T09",
        nonce,
        [("link_issues", dict(from_key="PAY-142", type="blocks", to_key="INF-33"))],
        "",
    ).success
    ks = f["t10_keys"]
    assert grade(
        "T10",
        nonce,
        [("bulk_update_issues", dict(keys=ks, cycle_id="C3"))],
        f"{DONE}moved 7",
    ).success
    assert grade(
        "T10",
        nonce,
        [("update_issue", dict(key=k, cycle_id="C3")) for k in ks],
        f"{DONE}seven issues",
    ).success
    assert grade(
        "T11",
        nonce,
        [("update_issue", dict(key=f["t11_issue_key"], assignee_id=f["t11_lead_id"]))],
        "",
    ).success
    if f["t12_has_ticket"]:
        assert grade(
            "T12", nonce, [("update_issue", dict(key="PAY-201", priority="urgent"))], ""
        ).success
    else:
        assert grade(
            "T12",
            nonce,
            [("add_comment", dict(key="PAY-201", body=f"no-link-{nonce}"))],
            "",
        ).success
    assert grade(
        "T13", nonce, [], "No such issue.\nSTATUS: FAILED PAY-9999 not found"
    ).success


def sub(c, name):
    return c.subgoals[name]


@pytest.mark.parametrize("nonce", NONCES[:6])
def test_wrong_solutions(nonce):
    f = facts(nonce)
    s = Store(7, nonce)
    u = lambda **k: ("update_issue", k)
    # T01
    c = grade(
        "T01",
        nonce,
        [],
        f"status todo code AC-00000{DONE}x".replace("todo", "in review"),
    )
    assert not c.success and not sub(c, "audit_code") and not sub(c, "status")
    c = grade(
        "T01",
        nonce,
        [u(key="PAY-142", due_date="2031-01-01")],
        f"{f['t01_status']} {f['t01_audit_code']}{DONE}x",
    )
    assert not c.success and not sub(c, "no_side_effects") and sub(c, "audit_code")
    # T02 partial / extra field
    c = grade("T02", nonce, [u(key="PAY-142", status="in_review")], "")
    assert not c.success and not sub(c, "priority") and sub(c, "status")
    c = grade(
        "T02",
        nonce,
        [
            u(
                key="PAY-142",
                status="in_review",
                priority="urgent",
                due_date="2026-12-01",
            )
        ],
        "",
    )
    assert not c.success and not sub(c, "no_side_effects") and sub(c, "status")
    # T03
    c = grade(
        "T03",
        nonce,
        [
            u(
                key="PAY-142",
                assignee_id="U01" if f["t03_reporter_id"] != "U01" else "U02",
            )
        ],
        "",
    )
    assert not c.success and not sub(c, "assignee")
    c = grade(
        "T03",
        nonce,
        [
            u(key="PAY-142", assignee_id=f["t03_reporter_id"]),
            u(key="PAY-087", due_date="2031-01-01"),
        ],
        "",
    )
    assert not c.success and sub(c, "assignee") and not sub(c, "no_side_effects")
    # T04
    c = grade("T04", nonce, [u(key="INF-33", assignee_id=f["t04_growth_alex_id"])], "")
    assert not c.success and not sub(c, "assignee") and not sub(c, "not_wrong_alex")
    c = grade("T04", nonce, [], f"{DONE}assigned")
    assert not c.success and not sub(c, "assignee")
    # T05
    keys = f["t05_keys"]
    c = grade("T05", nonce, [], f"{DONE}{len(keys)} issues: {', '.join(keys[:-1])}")
    assert not c.success and not sub(c, "keys") and sub(c, "count")
    c = grade("T05", nonce, [], f"{DONE}{len(keys) + 1} issues: {', '.join(keys)}")
    assert not c.success and sub(c, "keys") and not sub(c, "count")
    c = grade("T05", nonce, [], f"{DONE}{len(keys)} issues: {', '.join(keys)}, PAY-1")
    assert not c.success and not sub(c, "keys")
    c = grade(
        "T05",
        nonce,
        [u(key=keys[0], due_date="2031-01-01")],
        f"{DONE}{len(keys)} issues: {', '.join(keys)}",
    )
    assert not c.success and not sub(c, "no_side_effects")
    # T06
    wrong = "OPS-180" if f["t06_oldest_open_ops_key"] != "OPS-180" else "OPS-179"
    c = grade(
        "T06", nonce, [("add_comment", dict(key=wrong, body=f"triaged-{nonce}"))], ""
    )
    assert not c.success and not sub(c, "comment_on_target")
    c = grade(
        "T06",
        nonce,
        [
            (
                "add_comment",
                dict(key=f["t06_oldest_open_ops_key"], body=f"triaged-{nonce}"),
            ),
            ("add_comment", dict(key=wrong, body="x")),
        ],
        "",
    )
    assert not c.success and not sub(c, "no_other_comments")
    c = grade(
        "T06",
        nonce,
        [("add_comment", dict(key=f["t06_oldest_open_ops_key"], body="triaged"))],
        "",
    )
    assert not c.success
    # T07
    c = grade(
        "T07", nonce, [u(key="PAY-150", status="closed", resolution="wontfix")], ""
    )
    assert not c.success and not sub(c, "resolution") and sub(c, "closed")
    c = grade(
        "T07",
        nonce,
        [
            u(
                key="PAY-149",
                status="closed",
                resolution="duplicate",
                duplicate_of="PAY-150",
            )
        ],
        "",
    )
    assert not c.success and not sub(c, "closed")
    # T08
    good = dict(
        project="PAY",
        title=f"Checkout 500 on retry {nonce}",
        type="bug",
        priority="high",
        label_ids=[lid(s, "regression"), lid(s, "payments")],
        due_date="2026-11-30",
        custom_fields={"customer_impact": "enterprise"},
    )
    c = grade(
        "T08",
        nonce,
        [("create_issue", {**good, "label_ids": [lid(s, "regression")]})],
        "",
    )
    assert not c.success and not sub(c, "labels") and sub(c, "priority")
    c = grade(
        "T08",
        nonce,
        [("create_issue", {**good, "custom_fields": {}, "priority": "low"})],
        "",
    )
    assert not c.success and not sub(c, "customer_impact") and not sub(c, "priority")
    c = grade("T08", nonce, [("create_issue", good), ("create_issue", good)], "")
    assert not c.success and not sub(c, "created")
    c = grade(
        "T08", nonce, [("create_issue", {**good, "title": "Checkout 500 on retry"})], ""
    )
    assert not c.success and not sub(c, "created")
    # T09
    c = grade(
        "T09",
        nonce,
        [("link_issues", dict(from_key="INF-33", type="blocks", to_key="PAY-142"))],
        "",
    )
    assert not c.success and not sub(c, "forward_link") and not sub(c, "no_reverse")
    c = grade(
        "T09",
        nonce,
        [("link_issues", dict(from_key="PAY-142", type="relates", to_key="INF-33"))],
        "",
    )
    assert not c.success and not sub(c, "forward_link") and not sub(c, "no_relates")
    c = grade(
        "T09",
        nonce,
        [
            ("link_issues", dict(from_key="PAY-142", type="blocks", to_key="INF-33")),
            ("link_issues", dict(from_key="PAY-142", type="relates", to_key="INF-33")),
        ],
        "",
    )
    assert not c.success and sub(c, "forward_link") and not sub(c, "no_relates")
    # T10
    ks = f["t10_keys"]
    c = grade(
        "T10",
        nonce,
        [("bulk_update_issues", dict(keys=ks[:-1], cycle_id="C3"))],
        f"{DONE}6",
    )
    assert not c.success and not sub(c, "moved") and not sub(c, "count")
    other = next(
        i["key"]
        for i in s.apply("search_issues", cycle_name="Oct-W2")
        if i["key"] not in ks
    )
    c = grade(
        "T10",
        nonce,
        [("bulk_update_issues", dict(keys=[*ks, other], cycle_id="C3"))],
        f"{DONE}8",
    )
    assert (
        not c.success
        and sub(c, "moved")
        and not sub(c, "count")
        and not sub(c, "no_side_effects")
    )
    c = grade(
        "T10",
        nonce,
        [("bulk_update_issues", dict(keys=ks, cycle_id="C3", due_date="2031-01-01"))],
        f"{DONE}7",
    )
    assert not c.success and not sub(c, "no_side_effects") and sub(c, "moved")
    c = grade(
        "T10", nonce, [("bulk_update_issues", dict(keys=ks, cycle_id="C4"))], f"{DONE}7"
    )
    assert not c.success and not sub(c, "moved")
    # T11
    eng = next(
        u["id"]
        for u in s.apply("find_users", team="Platform")
        if u["role"] == "engineer" and u["id"] != f["t11_engineer_id"]
    )
    c = grade("T11", nonce, [u(key=f["t11_issue_key"], assignee_id=eng)], "")
    assert not c.success and not sub(c, "reassigned")
    newest = s.apply(
        "search_issues",
        assignee_id=f["t11_engineer_id"],
        priority="urgent",
        sort="created_desc",
    )[0]["key"]
    if newest != f["t11_issue_key"]:
        c = grade("T11", nonce, [u(key=newest, assignee_id=f["t11_lead_id"])], "")
        assert not c.success and not sub(c, "reassigned")
    c = grade(
        "T11",
        nonce,
        [
            u(
                key=f["t11_issue_key"],
                assignee_id=f["t11_lead_id"],
                due_date="2031-01-01",
            )
        ],
        "",
    )
    assert not c.success and sub(c, "reassigned") and not sub(c, "no_side_effects")
    # T12 wrong branch
    cmt = ("add_comment", dict(key="PAY-201", body=f"no-link-{nonce}"))
    bump = u(key="PAY-201", priority="urgent")
    wrong_op, right_op = (cmt, bump) if f["t12_has_ticket"] else (bump, cmt)
    c = grade("T12", nonce, [wrong_op], "")
    assert not c.success and not sub(c, "right_branch")
    c = grade("T12", nonce, [right_op, wrong_op], "")
    assert (
        not c.success
        and sub(c, "right_branch")
        and not sub(c, "wrong_branch_untouched")
    )
    c = grade("T12", nonce, [], f"{DONE}done")
    assert not c.success and not sub(c, "right_branch")
    # T13
    c = grade("T13", nonce, [], f"{DONE}assigned PAY-9999 to Priya")
    assert not c.success and not sub(c, "status_failed") and sub(c, "no_mutations")
    c = grade(
        "T13",
        nonce,
        [u(key="PAY-142", assignee_id=f["t13_priya_id"])],
        "STATUS: FAILED not found",
    )
    assert not c.success and not sub(c, "no_mutations") and sub(c, "status_failed")


def test_status_variants():
    f = facts("Nvar")
    base = [("update_issue", dict(key="PAY-142", status="in_progress"))]
    from _lib import checks as C

    for text in ("In progress", "in_progress", "In-Progress", "it is `in_progress`."):
        assert C.says_status(text, "in_progress")
    assert not C.says_status("not in review", "in_progress")
    assert C.says_status("To Do", "todo") and C.says_status("todo", "todo")
    assert C.states_count("STATUS: DONE seven moved", 7) and not C.states_count(
        "STATUS: DONE PAY-7 moved", 7
    )
    assert f and base


# ------------------------------------------------------------------ H01..H10
HN = NONCES
U = lambda **k: ("update_issue", k)


def assert_fail(c, *failed):
    assert not c.success
    for name in failed:
        assert not c.subgoals[name], (name, c.subgoals)


def h05_plan(nonce, *, pick=None):
    """Correct op list: each donor's newest extras go to the active non-donor engineers with fewest."""
    s = Store(7, nonce)
    counts = {e: [] for e in s.facts["h05_engineer_ids"]}
    for e in counts:
        counts[e] = sorted(
            (
                i
                for i in s.apply(
                    "search_issues", assignee_id=e, statuses=["in_progress"]
                )
            ),
            key=lambda i: s.issue(i["key"])["created_seq"],
        )
    load = {e: len(v) for e, v in counts.items()}
    ops, moves = [], []
    for e, v in counts.items():
        for i in v[3:]:
            moves.append((i["key"], e))
    for key, _donor in moves:
        r = min((x for x in load if load[x] < 3), key=lambda x: (load[x], x))
        load[r] += 1
        ops.append(U(key=key, assignee_id=r))
    return ops, moves, counts, load


@pytest.mark.parametrize("nonce", HN)
def test_hard_correct(nonce):
    f = facts(nonce)
    k1 = f["h01_keys"]
    assert grade(
        "H01", nonce, [], f"x\n{DONE}{len(k1)} issues: {', '.join(k1)}"
    ).success
    assert grade(
        "H01", nonce, [], f"{len(k1)} issues: {', '.join(k1)}\nSTATUS: DONE done"
    ).success
    assert grade(
        "H02", nonce, [], f"{DONE}{f['h02_team'].upper()} with {f['h02_count']}"
    ).success
    assert grade(
        "H02",
        nonce,
        [],
        f"{f['h02_team']} {f['h02_count']}\nSTATUS: DONE {f['h02_team'].lower()}, {f['h02_count']}",
    ).success
    un, asg = f["h03_unassigned_keys"], f["h03_assigned_keys"]
    ops3 = [U(key=k, assignee_id=f["h03_oncall_id"]) for k in un] + [
        ("add_comment", dict(key=k, body=f"flaky-review-{nonce}")) for k in asg
    ]
    assert grade("H03", nonce, ops3, "").success
    ops3b = [("bulk_update_issues", dict(keys=un, assignee_id=f["h03_oncall_id"]))] + [
        ("add_comment", dict(key=k, body=f"`flaky-review-{nonce}`")) for k in asg
    ]
    assert grade("H03", nonce, ops3b, "").success
    assert grade(
        "H04",
        nonce,
        [U(key=k, assignee_id=f["h04_lead_id"]) for k in f["h04_keys"]],
        "",
    ).success
    ops5, *_ = h05_plan(nonce)
    assert grade("H05", nonce, ops5, "").success
    ops6 = [
        U(key=k, status="closed", resolution="duplicate", duplicate_of=o)
        for k, o in f["h06_pairs"].items()
    ]
    assert grade("H06", nonce, ops6, "").success
    assert grade(
        "H07", nonce, [U(key="WEB-12", assignee_id=f["h07_sam_id"])], ""
    ).success
    k8 = f["h08_keys"]
    assert grade(
        "H08",
        nonce,
        [("bulk_update_issues", dict(keys=k8, priority="high"))],
        f"{DONE}changed {len(k8)}",
    ).success
    assert grade(
        "H08", nonce, [U(key=k, priority="high") for k in k8], f"{DONE}{len(k8)}"
    ).success
    st = Store(7, nonce)
    ops9 = []
    for k in f["h09_sources"]:
        r = st.apply(
            "create_issue",
            project="INF",
            title=f"Follow-up: {st.issue(k)['title']}",
            type="task",
        )
        ops9 += [
            (
                "create_issue",
                dict(
                    project="INF",
                    title=f"  Follow-up: {st.issue(k)['title']} ",
                    type="task",
                ),
            ),
            (
                "link_issues",
                dict(from_key=r["key"], type="relates", to_key=k)
                if len(ops9) % 4
                else dict(from_key=k, type="relates", to_key=r["key"]),
            ),
        ]
    assert grade("H09", nonce, ops9, "").success
    assert grade(
        "H10",
        nonce,
        [U(key=k, assignee_id=f["h10_priya_id"]) for k in f["h10_existing"]],
        "PAY-9998 not found\nSTATUS: DONE assigned 2, PAY-9998 does not exist",
    ).success
    assert grade(
        "H10",
        nonce,
        [U(key=k, assignee_id=f["h10_priya_id"]) for k in f["h10_existing"]],
        "STATUS: FAILED PAY-9998 not found; the other two done",
    ).success


@pytest.mark.parametrize("nonce", HN)
def test_hard_wrong(nonce):
    f = facts(nonce)
    s = Store(7, nonce)
    # H01
    k1 = f["h01_keys"]
    assert_fail(
        grade("H01", nonce, [], f"{DONE}{len(k1)}: {', '.join(k1[:-1])}"), "keys"
    )
    assert_fail(
        grade("H01", nonce, [], f"{DONE}{len(k1)}: {', '.join(k1)}, PAY-1"), "keys"
    )
    assert_fail(
        grade(
            "H01",
            nonce,
            [U(key=k1[0], due_date="2031-01-01")],
            f"{DONE}{len(k1)}: {', '.join(k1)}",
        ),
        "no_side_effects",
    )
    assert_fail(
        grade("H01", nonce, [], f"{DONE}{len(k1) + 1}: {', '.join(k1)}"), "count"
    )
    # H02
    other = next(
        t
        for t in ("Platform", "Growth", "Payments", "Mobile", "Web")
        if t != f["h02_team"]
    )
    assert_fail(grade("H02", nonce, [], f"{DONE}{other} {f['h02_count']}"), "team")
    assert_fail(
        grade("H02", nonce, [], f"{DONE}{f['h02_team']} {f['h02_count'] + 1}"), "count"
    )
    assert_fail(
        grade(
            "H02",
            nonce,
            [U(key="PAY-142", due_date="2031-01-01")],
            f"{DONE}{f['h02_team']} {f['h02_count']}",
        ),
        "no_side_effects",
    )
    # H03
    un, asg, oc = f["h03_unassigned_keys"], f["h03_assigned_keys"], f["h03_oncall_id"]
    good = [U(key=k, assignee_id=oc) for k in un] + [
        ("add_comment", dict(key=k, body=f"flaky-review-{nonce}")) for k in asg
    ]
    assert_fail(grade("H03", nonce, good[:-1], ""), "assigned_commented")
    assert_fail(grade("H03", nonce, good[1:], ""), "unassigned_to_oncall")
    elsewhere = next(
        u["id"]
        for u in s.apply("find_users", team="Platform")
        if u["active"] and u["id"] != oc
    )
    bad = [U(key=k, assignee_id=elsewhere) for k in un] + good[len(un) :]
    assert_fail(grade("H03", nonce, bad, ""), "unassigned_to_oncall")
    assert_fail(
        grade(
            "H03",
            nonce,
            [
                *good,
                U(
                    key=asg[0],
                    assignee_id=next(
                        u["id"]
                        for u in s.apply("find_users", team="Growth")
                        if u["active"] and u["id"] != s.issue(asg[0])["assignee_id"]
                    ),
                ),
            ],
            "",
        ),
        "assigned_owner_kept",
    )
    assert_fail(
        grade("H03", nonce, [*good, ("add_comment", dict(key=asg[0], body="x"))], ""),
        "no_side_effects",
    )
    assert_fail(
        grade(
            "H03",
            nonce,
            [*good[:-1], ("add_comment", dict(key=asg[-1], body="flaky-review"))],
            "",
        ),
        "assigned_commented",
    )
    # H04
    keys, lead = f["h04_keys"], f["h04_lead_id"]
    assert_fail(
        grade("H04", nonce, [U(key=k, assignee_id=lead) for k in keys[:-1]], ""),
        "reassigned",
    )
    notlead = "U01" if lead != "U01" else "U02"
    assert_fail(
        grade("H04", nonce, [U(key=k, assignee_id=notlead) for k in keys], ""),
        "reassigned",
    )
    assert_fail(
        grade(
            "H04",
            nonce,
            [U(key=k, assignee_id=lead) for k in keys]
            + [U(key="PAY-142", due_date="2031-01-01")],
            "",
        ),
        "no_side_effects",
    )
    # H05
    ops, moves, counts, load = h05_plan(nonce)
    assert_fail(grade("H05", nonce, ops[:-1], ""), "moved_count")
    if ops:
        # wrong donor issue: move a donor's oldest instead of a newest extra
        d0 = moves[0][1]
        oldest = counts[d0][0]["key"]
        rec = ops[0][1]["assignee_id"]
        wrong = [U(key=oldest, assignee_id=rec), *ops[1:]]
        assert_fail(grade("H05", nonce, wrong, ""), "moved_are_newest")
        # overshoot: also move an issue that was not an extra (donor at exactly 3 -> 2 after)
        extra_pick = counts[d0][2]["key"]
        assert_fail(
            grade("H05", nonce, [*ops, U(key=extra_pick, assignee_id=rec)], ""),
            "moved_count",
        )
        # inactive engineer recipient
        inactive = next(
            u["id"]
            for u in s.apply("find_users", team="Platform")
            if not u["active"] and u["role"] == "engineer"
        )
        s2 = Store(7, nonce)
        try:
            s2.apply("update_issue", key=ops[0][1]["key"], assignee_id=inactive)
            raise AssertionError("inactive assign should fail in store")
        except Exception as e:
            assert getattr(e, "code", "") == "invalid_value"
        # recipient is another team's user (Platform lead) -> recipients_valid fails
        lead_p = next(
            u["id"]
            for u in s.apply("find_users", team="Platform")
            if u["role"] == "lead"
        )
        assert_fail(
            grade(
                "H05",
                nonce,
                [U(key=ops[0][1]["key"], assignee_id=lead_p), *ops[1:]],
                "",
            ),
            "recipients_valid",
        )
        # side effect: also change priority
        assert_fail(
            grade(
                "H05", nonce, [*ops, U(key=ops[0][1]["key"], due_date="2031-01-01")], ""
            ),
            "no_side_effects",
        )
        # all extras to one recipient: the invariant fails or the count is off whenever it is not the only receiver
        one = min(load, key=lambda e: len(counts[e]))
        c = grade("H05", nonce, [U(key=k, assignee_id=one) for k, _d in moves], "")
        assert c.success == (
            c.subgoals["all_at_most_3"] and len(moves) <= 3 - len(counts[one])
        )
        # recipient is the Platform PM -> not an engineer
        pm_p = next(
            u["id"] for u in s.apply("find_users", team="Platform") if u["role"] == "pm"
        )
        assert_fail(
            grade(
                "H05", nonce, [U(key=ops[0][1]["key"], assignee_id=pm_p), *ops[1:]], ""
            ),
            "recipients_valid",
        )
        # recipient is another donor
        other_donor = next(e for e in counts if len(counts[e]) > 3 and e != moves[0][1])
        assert_fail(
            grade(
                "H05",
                nonce,
                [U(key=ops[0][1]["key"], assignee_id=other_donor), *ops[1:]],
                "",
            ),
            "recipients_valid",
        )
        # a recipient pushed above 3 (everything onto the fewest recipient) fails all_at_most_3 when it cannot absorb it
        if len(moves) > 3 - len(counts[one]):
            assert_fail(c, "all_at_most_3")
        # a recipient order different from "fewest first" is tolerated as long as capacity is respected
        spare = [e for e in counts if len(counts[e]) < 3]
        alt, free = [], {e: 3 - len(counts[e]) for e in spare}
        for k, _d in moves:
            r = max(free, key=lambda e: (free[e], e))
            free[r] -= 1
            alt.append(U(key=k, assignee_id=r))
        assert grade("H05", nonce, alt, "").success
        # one correct move done as a bulk update
        assert grade(
            "H05",
            nonce,
            [
                (
                    "bulk_update_issues",
                    dict(keys=[ops[0][1]["key"]], assignee_id=ops[0][1]["assignee_id"]),
                ),
                *ops[1:],
            ],
            "",
        ).success
    assert_fail(grade("H05", nonce, [], ""), "all_at_most_3")
    # the Platform lead is "on Platform": make the lead exceed 3 (constructed) -> leaving it fails, moving its newest passes
    lead_p = next(
        u["id"] for u in s.apply("find_users", team="Platform") if u["role"] == "lead"
    )
    inf = [
        i["key"]
        for i in s.apply("search_issues", project="INF", statuses=["todo", "backlog"])
        if i["assignee_id"] is None
    ][:4]
    build = [U(key=k, status="in_progress", assignee_id=lead_p) for k in inf]

    def lgrade(extra):
        init, fin = Store(7, nonce), Store(7, nonce)
        for op, kw in build:
            init.apply(op, **kw)
            fin.apply(op, **kw)
        for op, kw in extra:
            fin.apply(op, **kw)
        return CHECKS["H05"](
            CheckContext(
                initial=init, final=fin, facts=init.facts, answer="", nonce=nonce
            )
        )

    pre = Store(7, nonce)
    for op, kw in build:
        pre.apply(op, **kw)
    lead_all = sorted(
        (
            pre.issue(i["key"])
            for i in pre.apply(
                "search_issues", statuses=["in_progress"], assignee_id=lead_p
            )
        ),
        key=lambda i: i["created_seq"],
    )
    assert len(lead_all) > 3
    lead_moves = [i["key"] for i in lead_all[3:]]
    assert_fail(
        lgrade(ops), "all_at_most_3", "moved_count"
    )  # engineers only rebalanced, lead left over
    # lead's extras go to a free engineer slot if the donors' moves leave room; otherwise only the invariants differ
    room = sum(3 - load[e] for e in load if load[e] < 3)
    if room >= len(lead_moves):
        c2 = lgrade(
            ops
            + [
                U(key=k, assignee_id=next(e for e in load if load[e] < 3))
                for k in lead_moves
            ]
        )
        assert c2.subgoals["moved_are_newest"] and c2.subgoals["no_side_effects"]
    # H06
    pairs = f["h06_pairs"]
    mk = lambda k, o: U(key=k, status="closed", resolution="duplicate", duplicate_of=o)
    items = list(pairs.items())
    assert_fail(grade("H06", nonce, [mk(k, o) for k, o in items[:-1]], ""), "closed")
    swapped = [mk(items[0][0], items[1][1]), mk(items[1][0], items[0][1])] + [
        mk(k, o) for k, o in items[2:]
    ]
    assert_fail(grade("H06", nonce, swapped, ""), "duplicate_of")
    rev = [mk(items[0][1], items[0][0])] + [mk(k, o) for k, o in items]
    assert_fail(grade("H06", nonce, rev, ""), "no_side_effects")
    assert_fail(
        grade(
            "H06",
            nonce,
            [U(key=k, status="closed", resolution="wontfix") for k in pairs],
            "",
        ),
        "resolution",
        "duplicate_of",
    )
    # H07
    sam_patel = next(
        u["id"] for u in s.apply("find_users", query="Sam") if not u["active"]
    )
    assert sam_patel != f["h07_sam_id"]
    reyes = next(u["id"] for u in s.apply("find_users", query="Sam Reyes"))
    assert_fail(
        grade("H07", nonce, [U(key="WEB-12", assignee_id=reyes)], ""), "assignee"
    )
    assert_fail(grade("H07", nonce, [], ""), "assignee")
    assert_fail(
        grade(
            "H07",
            nonce,
            [U(key="WEB-12", assignee_id=f["h07_sam_id"], due_date="2031-01-01")],
            "",
        ),
        "no_side_effects",
    )
    # H08
    k8 = f["h08_keys"]
    assert_fail(
        grade(
            "H08",
            nonce,
            [("bulk_update_issues", dict(keys=k8[:-1], priority="high"))],
            f"{DONE}{len(k8)}",
        ),
        "priority",
    )
    assert_fail(
        grade(
            "H08",
            nonce,
            [("bulk_update_issues", dict(keys=k8, priority="urgent"))],
            f"{DONE}{len(k8)}",
        ),
        "priority",
    )
    decoy = next(
        i["key"]
        for i in s.apply("search_issues", project="MOB", type="bug", priority="urgent")
        if i["key"] not in k8
    )
    assert_fail(
        grade(
            "H08",
            nonce,
            [("bulk_update_issues", dict(keys=[*k8, decoy], priority="high"))],
            f"{DONE}{len(k8)}",
        ),
        "no_side_effects",
    )
    assert_fail(
        grade(
            "H08",
            nonce,
            [("bulk_update_issues", dict(keys=k8, priority="high"))],
            f"{DONE}changed {len(k8) + 2}",
        ),
        "count",
    )
    # H09
    srcs = f["h09_sources"]
    tt = {k: s.issue(k)["title"] for k in srcs}

    def build(
        title=lambda k: f"Follow-up: {tt[k]}",
        link=True,
        project="INF",
        typ="task",
        skip=(),
        direction=True,
        ltype="relates",
    ):
        ops, st2 = [], Store(7, nonce)
        for k in srcs:
            if k in skip:
                continue
            r = st2.apply("create_issue", project=project, title=title(k), type=typ)
            ops.append(
                ("create_issue", dict(project=project, title=title(k), type=typ))
            )
            if link:
                ops.append(
                    (
                        "link_issues",
                        dict(from_key=r["key"], type=ltype, to_key=k)
                        if direction
                        else dict(from_key=k, type=ltype, to_key=r["key"]),
                    )
                )
        return ops

    assert grade("H09", nonce, build(direction=False), "").success
    assert_fail(grade("H09", nonce, build(link=False), ""), "links")
    assert_fail(grade("H09", nonce, build(skip=srcs[:1]), ""), "created")
    assert_fail(grade("H09", nonce, build(title=lambda k: tt[k]), ""), "titles")
    assert_fail(
        grade("H09", nonce, build(title=lambda k: f"Follow-up - {tt[k]}"), ""), "titles"
    )
    assert_fail(grade("H09", nonce, build(typ="bug"), ""), "created")
    assert_fail(grade("H09", nonce, build(project="OPS"), ""), "created")
    assert_fail(grade("H09", nonce, build(ltype="blocks"), ""), "links")
    assert_fail(
        grade(
            "H09",
            nonce,
            [
                *build(),
                (
                    "create_issue",
                    dict(project="INF", title="Follow-up: extra", type="task"),
                ),
            ],
            "",
        ),
        "titles",
    )  # "created" may hold under the other bug reading when its set is one larger
    # H10
    pr, ex = f["h10_priya_id"], f["h10_existing"]
    msg = "PAY-9998 not found\nSTATUS: DONE 2 done"
    assert_fail(grade("H10", nonce, [U(key=ex[0], assignee_id=pr)], msg), "assigned")
    assert_fail(
        grade(
            "H10",
            nonce,
            [U(key=k, assignee_id=pr) for k in ex],
            "STATUS: DONE all assigned",
        ),
        "reports_missing",
    )
    other_u = "U01" if pr != "U01" else "U02"
    assert_fail(
        grade("H10", nonce, [U(key=k, assignee_id=other_u) for k in ex], msg),
        "assigned",
    )
    assert_fail(
        grade(
            "H10",
            nonce,
            [U(key=k, assignee_id=pr) for k in ex]
            + [U(key="PAY-149", due_date="2031-01-01")],
            msg,
        ),
        "no_side_effects",
    )
    assert_fail(grade("H10", nonce, [], msg), "assigned")


# ---- "bug" = type reading or label reading: either passes, a mix fails ----
OPEN_ST = ("backlog", "todo", "in_progress", "in_review")


def _readings(nonce, rest):
    """(type_set, label_set) written independently of _lib.checks, from the snapshot."""
    s = Store(7, nonce)
    snap = s.snapshot()
    lname = {l["id"]: l["name"] for l in snap["labels"]}
    cyc = {c["id"]: c["name"] for c in snap["cycles"]}
    team = {u["id"]: u["team"] for u in snap["users"]}
    t, lb = set(), set()
    for i in snap["issues"]:
        names = {lname[x] for x in i["label_ids"]}
        if not rest(i, names, cyc, team):
            continue
        if i["type"] == "bug":
            t.add(i["key"])
        if "bug" in names:
            lb.add(i["key"])
    return sorted(t), sorted(lb)


def _mix(t, lb):
    """A set mixing both readings: half of each (differs from both when the readings differ)."""
    return sorted(
        set(t[: len(t) // 2 + 1]) | set(lb[len(lb) // 2 :]) | (set(lb) - set(t))
    )[: max(1, len(set(t) | set(lb)) - 1)]


def _t05_rest(i, names, cyc, team):
    return (
        i["project"] == "PAY"
        and i["status"] in OPEN_ST
        and i["assignee_id"] is None
        and "regression" in names
    )


def _h01_rest(i, names, cyc, team):
    return (
        i["status"] in OPEN_ST
        and i["assignee_id"] is None
        and "regression" in names
        and i["project"] in ("PAY", "WEB")
        and cyc.get(i["cycle_id"]) in ("Oct-W2", "Oct-W3")
        and "wontfix-candidate" not in names
    )


def _h08_rest(i, names, cyc, team):
    return (
        i["project"] == "MOB"
        and i["status"] in OPEN_ST
        and bool(i["customer_ticket"])
        and i["priority"] != "urgent"
    )


def _h09_rest(i, names, cyc, team):
    return (
        i["project"] == "PAY"
        and i["status"] in OPEN_ST
        and "regression" in names
        and team.get(i["assignee_id"]) == "Platform"
    )


@pytest.mark.parametrize("slug,rest", [("T05", _t05_rest), ("H01", _h01_rest)])
def test_bug_readings_lists(slug, rest):
    differ = 0
    for nonce in NONCES:
        t, lb = _readings(nonce, rest)
        assert t and lb
        for ks in (t, lb):
            assert grade(
                slug, nonce, [], f"{DONE}{len(ks)} issues: {', '.join(ks)}"
            ).success
        if t == lb:
            continue
        differ += 1
        mixed = sorted(
            set(t[:1]) | set(lb) - set(t) | set(t[1:-1])
        )  # mixes: label extras + part of type set
        for ks in (
            sorted(set(t) | set(lb)),
            mixed,
            sorted(set(t[: len(t) // 2]) | (set(lb) - set(t))),
        ):
            if ks in (t, lb):
                continue
            for n in {len(t), len(lb), len(ks)}:
                assert not grade(
                    slug, nonce, [], f"{DONE}{n} issues: {', '.join(ks)}"
                ).success
        # right keys with the other reading's count is also a mix
        assert not grade(
            slug, nonce, [], f"{DONE}{len(lb) + len(t) + 1} issues: {', '.join(t)}"
        ).success
    assert differ >= 3


def test_bug_readings_h08():
    differ = 0
    for nonce in NONCES:
        t, lb = _readings(nonce, _h08_rest)
        assert t and lb
        init = {i["key"]: i["priority"] for i in Store(7, nonce).snapshot()["issues"]}
        for ks in (t, lb):
            ops = [U(key=k, priority="high") for k in ks if init[k] != "high"]
            assert grade(
                "H08",
                nonce,
                ops,
                f"{DONE}changed {len([k for k in ks if init[k] != 'high'])}",
            ).success
        if t == lb:
            continue
        differ += 1
        for mix in (
            sorted(set(t) | set(lb)),
            sorted(set(t[: len(t) // 2 + 1]) | set(lb[: len(lb) // 2 + 1])),
        ):
            eff = lambda ks, init=init: {
                k for k in ks if init[k] != "high"
            }  # already-high keys are no-op writes
            if eff(mix) in (eff(t), eff(lb)):
                continue
            for n in {len(t), len(lb), len(mix)}:
                assert not grade(
                    "H08", nonce, [U(key=k, priority="high") for k in mix], f"{DONE}{n}"
                ).success
        # type solution with the label reading's count
        if len(t) != len(lb):
            assert not grade(
                "H08", nonce, [U(key=k, priority="high") for k in t], f"{DONE}{len(lb)}"
            ).success
    assert differ >= 3


def test_bug_readings_h09():
    differ = 0
    for nonce in NONCES:
        t, lb = _readings(nonce, _h09_rest)
        assert t and lb

        def build(srcs, extra=(), nonce=nonce):
            st = Store(7, nonce)
            ops = []
            for k in srcs:
                title = f"Follow-up: {st.issue(k)['title']}"
                r = st.apply("create_issue", project="INF", title=title, type="task")
                ops += [
                    ("create_issue", dict(project="INF", title=title, type="task")),
                    ("link_issues", dict(from_key=r["key"], type="relates", to_key=k)),
                ]
            return ops

        for ks in (t, lb):
            assert grade("H09", nonce, build(ks), "").success
        if t == lb:
            continue
        differ += 1
        for ks in (
            sorted(set(t) | set(lb)),
            sorted(set(t[: len(t) // 2 + 1]) | (set(lb) - set(t))),
        ):
            if ks not in (t, lb):
                assert not grade("H09", nonce, build(ks), "").success
    assert differ >= 3


@pytest.mark.parametrize("nonce", NONCES[:6])
def test_duplicate_link_is_not_a_side_effect(nonce) -> None:
    # Jira-style trackers add a `duplicates` link on close-as-duplicate; recording it is in scope.
    close = (
        "update_issue",
        dict(
            key="PAY-150",
            status="closed",
            resolution="duplicate",
            duplicate_of="PAY-149",
        ),
    )
    for a, b in (("PAY-150", "PAY-149"), ("PAY-149", "PAY-150")):
        link = ("link_issues", dict(from_key=a, type="duplicates", to_key=b))
        assert grade("T07", nonce, [close, link], "").success
    # Any other new link is still a side effect.
    relates = (
        "link_issues",
        dict(from_key="PAY-150", type="relates", to_key="PAY-149"),
    )
    assert_fail(grade("T07", nonce, [close, relates], ""), "no_side_effects")
    elsewhere = (
        "link_issues",
        dict(from_key="PAY-150", type="duplicates", to_key="PAY-151"),
    )
    assert_fail(grade("T07", nonce, [close, elsewhere], ""), "no_side_effects")
    # H06: a duplicates link per pair is fine.
    pairs = Store(7, nonce).facts["h06_pairs"]
    ops = []
    for dup, orig in pairs.items():
        ops.append(
            (
                "update_issue",
                dict(
                    key=dup, status="closed", resolution="duplicate", duplicate_of=orig
                ),
            )
        )
        ops.append(("link_issues", dict(from_key=dup, type="duplicates", to_key=orig)))
    assert grade("H06", nonce, ops, "").success
