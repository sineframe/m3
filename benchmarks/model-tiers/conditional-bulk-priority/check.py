from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    """Exactly the open, non-urgent MOB issues with a customer ticket become high, under the `type == bug`
    reading or under the `bug` label reading (never a mix); count stated in the answer (changed count)."""
    d = C.diff(ctx)

    def rest(i, names, _users):
        return (
            i["project"] == "MOB"
            and i["status"] in C.OPEN_STATUSES
            and bool(i["customer_ticket"])
            and i["priority"] != "urgent"
        )

    sets = C.bug_sets(ctx.initial, rest)
    init_prio = {i["key"]: i["priority"] for i in d.a["issues"]}
    res = {}
    for name, keys in sets.items():
        # already-high issues need no change; "changed" count is those that actually change
        changed = [k for k in keys if init_prio[k] != "high"]
        sub = {
            "priority": all(
                (d.final_issue(k) or {}).get("priority") == "high" for k in keys
            ),
            "count": C.states_count(ctx.answer, len(changed))
            or C.states_count(ctx.answer, len(keys)),
            "done": C.is_done(ctx.answer),
            "no_side_effects": C.clean(d, {k: {"priority"} for k in changed}),
        }
        res[name] = (all(sub.values()), sub)
    ok, sub = C.pick_interpretation(res)
    return Check(
        success=ok, subgoals=sub, target_ops=["update_issue", "bulk_update_issues"]
    )
