from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    i = d.final_issue("INF-33")
    sub = {
        "assignee": bool(i) and i["assignee_id"] == ctx.facts["t04_platform_alex_id"],
        "not_wrong_alex": not (
            i and i["assignee_id"] == ctx.facts["t04_growth_alex_id"]
        ),
        "no_side_effects": C.clean(d, {"INF-33": {"assignee_id"}}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue"])
