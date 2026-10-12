from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    key = ctx.facts["t11_issue_key"]
    i = d.final_issue(key)
    sub = {
        "reassigned": bool(i) and i["assignee_id"] == ctx.facts["t11_lead_id"],
        "no_side_effects": C.clean(d, {key: {"assignee_id"}}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue"])
