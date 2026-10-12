from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    """Team name (case-insensitive, whole word) and count must appear on the STATUS line (else anywhere)."""
    d = C.diff(ctx)
    _s, rest = C.status_info(ctx.answer)
    text = rest if rest.strip() else (ctx.answer or "")
    team = ctx.facts["h02_team"]
    sub = {
        "team": C.mentions(text.lower(), team.lower()),
        "count": C.states_count(ctx.answer, ctx.facts["h02_count"]),
        "done": C.is_done(ctx.answer),
        "no_side_effects": d.nothing,
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["search_issues", "find_users"])
