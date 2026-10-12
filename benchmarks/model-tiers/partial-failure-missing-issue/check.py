from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    """Both existing issues assigned to Priya, nothing else changed; the answer must mention the missing
    key (STATUS DONE or FAILED are both acceptable)."""
    d = C.diff(ctx)
    keys = ctx.facts["h10_existing"]
    sub = {
        "assigned": all(
            (d.final_issue(k) or {}).get("assignee_id") == ctx.facts["h10_priya_id"]
            for k in keys
        ),
        "reports_missing": C.mentions(ctx.answer, ctx.facts["h10_missing"]),
        "no_side_effects": C.clean(d, {k: {"assignee_id"} for k in keys}),
    }
    return Check(
        success=all(sub.values()),
        subgoals=sub,
        target_ops=["update_issue", "bulk_update_issues"],
    )
