from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    keys = ctx.facts["t10_keys"]
    w3 = C.cycle_id(d, "Oct-W3")
    sub = {
        "moved": all((d.final_issue(k) or {}).get("cycle_id") == w3 for k in keys),
        "count": C.states_count(ctx.answer, len(keys)),
        "no_side_effects": C.clean(d, {k: {"cycle_id"} for k in keys}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["bulk_update_issues", "update_issue"])
