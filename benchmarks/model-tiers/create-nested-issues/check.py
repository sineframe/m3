from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    title = f"Checkout 500 on retry {ctx.nonce}"
    new = d.new_issues
    one = len(new) == 1 and new[0]["title"].strip() == title
    i = new[0] if one else None
    sub = {
        "created": one,
        "project": bool(i) and i["project"] == "PAY",
        "type": bool(i) and i["type"] == "bug",
        "priority": bool(i) and i["priority"] == "high",
        "labels": bool(i) and C.label_names(d, i) == {"regression", "payments"},
        "due_date": bool(i) and i["due_date"] == "2026-11-30",
        "customer_impact": bool(i)
        and i["custom_fields"].get("customer_impact") == "enterprise",
        "no_side_effects": C.clean(d, {}, issues=1),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["create_issue"])
