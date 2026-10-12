from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    """Keys are the [A-Z]+-\\d+ matches on the STATUS line (whole answer if the line has none); the set must
    match, exactly, the open unassigned PAY `regression` issues under the `type == bug` reading or under the
    `bug` label reading. The count must appear on the STATUS line (else anywhere), for the same reading."""
    d = C.diff(ctx)

    def rest(i, names, _users):
        return (i["project"] == "PAY" and i["status"] in C.OPEN_STATUSES and i["assignee_id"] is None
                and "regression" in names)

    sets = C.bug_sets(ctx.initial, rest)
    got = C.listed_keys(ctx.answer)
    res = {}
    for name, want in sets.items():
        sub = {
            "keys": got == want,
            "count": C.states_count(ctx.answer, len(want)),
            "done": C.is_done(ctx.answer),
            "no_side_effects": d.nothing,
        }
        res[name] = (all(sub.values()), sub)
    ok, sub = C.pick_interpretation(res)
    return Check(success=ok, subgoals=sub, target_ops=["search_issues"])
