from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    i = d.final_issue("PAY-150")
    sub = {
        "closed": bool(i) and i["status"] == "closed",
        "resolution": bool(i) and i["resolution"] == "duplicate",
        "duplicate_of": bool(i) and i["duplicate_of"] == "PAY-149",
        "no_side_effects": C.clean(d, {"PAY-150": {"status", "resolution", "duplicate_of"}},
                                   links=len(d.new_links))
        and C.dup_links_only(d, {"PAY-150": "PAY-149"}),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["update_issue"])
