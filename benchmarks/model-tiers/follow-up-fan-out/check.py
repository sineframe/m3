from collections import Counter

from _lib import checks as C
from _lib.grade import Check, CheckContext


def _grade(d, srcs):
    title = {i["key"]: i["title"] for i in d.a["issues"]}
    want = Counter(f"Follow-up: {title[k]}" for k in srcs)
    new = d.new_issues
    got = Counter(i["title"].strip() for i in new)
    by_title: dict[str, list[dict]] = {}
    for i in new:
        by_title.setdefault(i["title"].strip(), []).append(i)
    links_ok = len(d.new_links) == len(srcs)
    used: set[str] = set()
    for k in srcs:
        cands = by_title.get(f"Follow-up: {title[k]}", [])
        hit = next(
            (
                i
                for i in cands
                for l in d.new_links
                if l["id"] not in used
                and l["type"] == "relates"
                and {l["from_key"], l["to_key"]} == {i["key"], k}
            ),
            None,
        )
        if hit is None:
            links_ok = False
            continue
        used.update(
            l["id"]
            for l in d.new_links
            if {l["from_key"], l["to_key"]} == {hit["key"], k}
        )
        cands.remove(hit)
    sub = {
        "created": len(new) == len(srcs)
        and all(i["project"] == "INF" and i["type"] == "task" for i in new),
        "titles": got == want,
        "links": links_ok and len(used) == len(srcs),
        "no_side_effects": C.clean(d, {}, issues=len(srcs), links=len(srcs)),
    }
    return all(sub.values()), sub


def check(ctx: CheckContext) -> Check:
    """One new INF task per source titled exactly "Follow-up: <title>" (surrounding whitespace tolerated),
    one `relates` link per new task to its source (either direction accepted), nothing else. Sources are the
    open PAY `regression` issues assigned to the Platform team, under the `type == bug` reading or under the
    `bug` label reading (never a mix)."""
    d = C.diff(ctx)

    def rest(i, names, users):
        u = users.get(i["assignee_id"])
        return (
            i["project"] == "PAY"
            and i["status"] in C.OPEN_STATUSES
            and "regression" in names
            and u is not None
            and u["team"] == "Platform"
        )

    res = {
        name: _grade(d, sorted(srcs))
        for name, srcs in C.bug_sets(ctx.initial, rest).items()
    }
    ok, sub = C.pick_interpretation(res)
    return Check(success=ok, subgoals=sub, target_ops=["create_issue", "link_issues"])
