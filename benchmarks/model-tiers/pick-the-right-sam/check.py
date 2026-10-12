from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    key = ctx.facts["h07_issue"]
    i = d.final_issue(key)
    sub = {
        "assignee": bool(i) and i["assignee_id"] == ctx.facts["h07_sam_id"],
        "no_side_effects": C.clean(d, {key: {"assignee_id"}}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue"])
