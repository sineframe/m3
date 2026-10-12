from _lib.grade import Check, CheckContext
from _lib import checks as C


def check(ctx: CheckContext) -> Check:
    d = C.diff(ctx)
    pair = {"PAY-142", "INF-33"}
    links = [l for l in d.b["links"] if {l["from_key"], l["to_key"]} == pair]
    sub = {
        "forward_link": any(l["from_key"] == "PAY-142" and l["type"] == "blocks" and l["to_key"] == "INF-33" for l in links),
        "no_reverse": not any(l["from_key"] == "INF-33" and l["type"] == "blocks" for l in links),
        "no_relates": not any(l["type"] == "relates" for l in links),
        "no_side_effects": C.clean(d, {}, links=1),
    }
    return Check(success=all(sub.values()), subgoals=sub, target_ops=["link_issues"])
