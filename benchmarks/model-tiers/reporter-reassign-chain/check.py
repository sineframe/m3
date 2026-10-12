from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    keys = ctx.facts["h04_keys"]
    lead = ctx.facts["h04_lead_id"]
    sub = {
        "reassigned": all((d.final_issue(k) or {}).get("assignee_id") == lead for k in keys),
        "no_side_effects": C.clean(d, {k: {"assignee_id"} for k in keys}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue", "bulk_update_issues"])
