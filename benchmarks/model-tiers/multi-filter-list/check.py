from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    """Keys are the issue-key matches on the STATUS line (whole answer if none); the set must equal the
    expected set under the `type == bug` reading or under the `bug` label reading (count to match)."""
    d = C.diff(ctx)
    cyc = {c["id"]: c["name"] for c in d.a["cycles"]}

    def rest(i, names, _users):
        return (i["status"] in C.OPEN_STATUSES and i["assignee_id"] is None and "regression" in names
                and i["project"] in ("PAY", "WEB") and cyc.get(i["cycle_id"]) in ("Oct-W2", "Oct-W3")
                and "wontfix-candidate" not in names)

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
