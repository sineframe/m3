from _lib import checks as C
from _lib.grade import Check, CheckContext


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    pairs = ctx.facts["h06_pairs"]
    fin = {k: d.final_issue(k) or {} for k in pairs}
    sub = {
        "closed": all(i.get("status") == "closed" for i in fin.values()),
        "resolution": all(i.get("resolution") == "duplicate" for i in fin.values()),
        "duplicate_of": all(fin[k].get("duplicate_of") == o for k, o in pairs.items()),
        "originals_untouched": all(
            o in pairs or o not in d.changed for o in pairs.values()
        ),
        "no_side_effects": C.clean(
            d,
            {k: {"status", "resolution", "duplicate_of"} for k in pairs},
            links=len(d.new_links),
        )
        and C.dup_links_only(d, pairs),
    }
    return Check(
        success=all(sub.values()),
        subgoals=sub,
        target_ops=["update_issue", "bulk_update_issues"],
    )
