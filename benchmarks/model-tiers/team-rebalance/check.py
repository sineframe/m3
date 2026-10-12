from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    """Invariant check. Let in_progress(u) be u's open in_progress issues in the initial state.
    "On Platform" means any active Platform member, whatever the role (engineer, lead, PM), as in the prompt.
    Donors are active Platform members with > 3. Required: after, every active Platform member has <= 3;
    the only changes are assignee changes on donors' in_progress issues; the moved set is exactly each
    donor's newest (highest created_seq) c-3 issues, so the number moved == sum(max(0, c-3));
    each moved issue goes to an active Platform ENGINEER who was not a donor and who had room (< 3 initially).
    The "fewest" rule is deliberately weak: order and exact recipient choice are not enforced, only that no
    recipient ends above 3 (all_at_most_3) and that recipients are valid engineers with room."""
    d = C.diff(ctx)
    users = {u["id"]: u for u in d.a["users"]}
    members = [u["id"] for u in d.a["users"] if u["team"] == "Platform" and u["active"]]
    engs = {e for e in members if users[e]["role"] == "engineer"}
    ip = {
        e: sorted(
            (
                i
                for i in d.a["issues"]
                if i["assignee_id"] == e and i["status"] == "in_progress"
            ),
            key=lambda i: i["created_seq"],
        )
        for e in members
    }
    donors = {e for e in members if len(ip[e]) > 3}
    expected = {i["key"] for e in donors for i in ip[e][3:]}
    excess = sum(max(0, len(ip[e]) - 3) for e in members)
    donor_issues = {i["key"] for e in donors for i in ip[e]}
    moved = {k for k, ch in d.changed.items() if "assignee_id" in ch}
    after = {
        e: sum(
            1
            for i in d.b["issues"]
            if i["assignee_id"] == e and i["status"] == "in_progress"
        )
        for e in members
    }

    def ok_recipient(k):
        r = d.changed[k]["assignee_id"]
        return r in engs and r not in donors and len(ip[r]) < 3

    sub = {
        "all_at_most_3": all(n <= 3 for n in after.values()),
        "no_side_effects": C.clean(d, {k: {"assignee_id"} for k in donor_issues}),
        "moved_count": len(moved) == excess,
        "moved_are_newest": moved == expected,
        "recipients_valid": all(ok_recipient(k) for k in moved),
    }
    return Check(
        success=all(sub.values()),
        subgoals=sub,
        target_ops=["update_issue", "bulk_update_issues"],
    )
