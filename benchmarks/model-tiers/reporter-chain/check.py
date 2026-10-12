from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    i = d.final_issue("PAY-142")
    sub = {
        "assignee": bool(i) and i["assignee_id"] == ctx.facts["t03_reporter_id"],
        "no_side_effects": C.clean(d, {"PAY-142": {"assignee_id"}}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue"])
