from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    """Unassigned flaky OPS issues -> on-call assignee only; assigned ones -> exactly one new
    `flaky-review-{nonce}` comment (backticks/whitespace tolerated) and the assignee unchanged. Nothing else."""
    d = C.diff(ctx)
    f = ctx.facts
    body = f"flaky-review-{ctx.nonce}"
    una, asg = f["h03_unassigned_keys"], f["h03_assigned_keys"]
    assigned_ok = {}
    for k in una:
        i = d.final_issue(k)
        assigned_ok[k] = bool(i) and i["assignee_id"] == f["h03_oncall_id"]
    comments = {
        k: [
            c
            for c in d.new_comments
            if c["issue_key"] == k and c["body"].strip().strip("`").strip() == body
        ]
        for k in asg
    }
    allowed = {k: {"assignee_id"} for k in una}
    sub = {
        "unassigned_to_oncall": all(assigned_ok.values()),
        "assigned_commented": all(len(v) == 1 for v in comments.values()),
        "assigned_owner_kept": all(
            "assignee_id" not in d.changed.get(k, {}) for k in asg
        ),
        "no_side_effects": C.clean(d, allowed, comments=len(asg))
        and all(c["issue_key"] in asg for c in d.new_comments),
    }
    return Check(
        success=all(sub.values()),
        subgoals=sub,
        target_ops=["update_issue", "bulk_update_issues", "add_comment"],
    )
