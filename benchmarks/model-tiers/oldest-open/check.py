from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    """Body compared after strip() and removing wrapping backticks."""
    d = C.diff(ctx)
    want_key = ctx.facts["t06_oldest_open_ops_key"]
    body = f"triaged-{ctx.nonce}"
    mine = [c for c in d.new_comments if c["body"].strip().strip("`").strip() == body]
    sub = {
        "comment_on_target": len(mine) == 1 and mine[0]["issue_key"] == want_key,
        "no_other_comments": len(d.new_comments) == 1,
        "no_side_effects": C.clean(d, {}, comments=1),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["add_comment"])
