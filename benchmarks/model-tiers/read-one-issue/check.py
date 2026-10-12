from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    sub = {
        "status": C.says_status(ctx.answer, ctx.facts["t01_status"]),
        "audit_code": C.mentions(ctx.answer, ctx.facts["t01_audit_code"]),
        "done": C.is_done(ctx.answer),
        "no_side_effects": d.nothing,
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["get_issue"])
