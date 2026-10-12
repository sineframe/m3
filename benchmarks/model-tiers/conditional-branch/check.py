from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    """Branch is taken from facts t12_has_ticket. Comment body compared after strip() and
    removing wrapping backticks."""
    d = C.diff(ctx)
    i = d.final_issue("PAY-201")
    body = f"no-link-{ctx.nonce}"
    commented = [c for c in d.new_comments if c["body"].strip().strip("`").strip() == body and c["issue_key"] == "PAY-201"]
    bumped = bool(i) and i["priority"] == "urgent"
    if ctx.facts["t12_has_ticket"]:
        sub = {
            "right_branch": bumped,
            "wrong_branch_untouched": not d.new_comments,
            "no_side_effects": C.clean(d, {"PAY-201": {"priority"}}),
        }
        ops = ["update_issue"]
    else:
        sub = {
            "right_branch": len(commented) == 1 and len(d.new_comments) == 1,
            "wrong_branch_untouched": "PAY-201" not in d.changed,
            "no_side_effects": C.clean(d, {}, comments=1),
        }
        ops = ["add_comment"]
    return Check(success=all(sub.values()), subgoals=sub, target_ops=ops)
