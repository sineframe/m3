from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    status, _rest = C.status_info(ctx.answer)
    sub = {
        "no_mutations": d.nothing and not ctx.final.mutations,
        "status_failed": status == "FAILED",
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue"])
