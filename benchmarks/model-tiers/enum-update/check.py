from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    i = d.final_issue("PAY-142")
    sub = {
        "status": bool(i) and i["status"] == "in_review",
        "priority": bool(i) and i["priority"] == "urgent",
        "no_side_effects": C.clean(d, {"PAY-142": {"status", "priority"}}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue"])
