"""Logical tools: each has a good + naive name, schema(cfg), description(cfg), and a handler.

The catalog dimension picks which names exist; the schema dimension picks flat vs nested args;
the pagination dimension picks output shapes; descriptions/errors are applied by the Surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from _backend.fixture import IMPACTS, PRIORITIES, RESOLUTIONS, STATUSES, TEAMS, TYPES
from _backend.store import DomainError
from _server.surface_parts.common import (
    DEFAULT_FIELDS, DEFAULT_LIMIT, LINK_TYPES, MAX_LIMIT, NAIVE_MAX_PER_PAGE, NAIVE_PER_PAGE, PROJECT_KEYS, ROW_FIELDS,
    SORTS, STATUS_LABELS, Cfg, Ctx,
    ar, body, bo, cf_list, conn, dec_cursor, enc_cursor, good_issue_full, good_row, it, light_user_row, naive_row, obj,
    resolve_cycle, resolve_label, resolve_user, st, user_row,
)

MUT_FIELDS = DEFAULT_FIELDS + ("project", "due_date", "resolution", "duplicate_of", "customer_impact")


@dataclass(frozen=True)
class Tool:
    id: str
    g: str | None            # name in the good (9-tool) catalog
    n: str | None            # name in the naive catalog
    short: str               # naive description
    schema: Callable[[Cfg], dict]
    desc: Callable[[Cfg, str], str]
    pdesc: dict
    run: Callable[[Ctx, dict], Any]
    only: Callable[[Cfg], bool] | None = None   # extra visibility rule (tools beyond the 9-tool good catalog)


def nm(cfg: Cfg, tid: str) -> str:
    t = BY_ID[tid]
    return (t.g if cfg.C else t.n) or t.g or t.n  # type: ignore[return-value]


# ------------------------------------------------------------------ arg helpers
# JSON:API / Prisma precedent: writes are wrapped in {"body":{"data":{"attributes":{...}}}} and relations use connect/disconnect.
def attrs(a: dict) -> dict:
    d = (a.get("body") or {}).get("data") if isinstance(a.get("body"), dict) else None
    at = d.get("attributes") if isinstance(d, dict) else None
    if not isinstance(at, dict):
        raise DomainError("missing_field", "request body is required", "Send body.data.attributes as an object.", "body")
    return at


def _label_ids(ctx: Ctx, key: str, add: list, rem: list) -> list[str]:
    cur = ctx.store.apply("get_issue", key=key)["label_ids"]
    add_ids = [resolve_label(ctx, x) for x in add or []]
    rem_ids = [resolve_label(ctx, x) for x in rem or []]
    return [x for x in cur if x not in rem_ids] + [x for x in add_ids if x not in cur]


def _custom_from_list(items: Any) -> dict:
    out = {}
    for item in items or []:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or "value" not in item:
            raise DomainError("invalid_type", "custom_fields items must be {key, value}", 'Example: [{"key": "customer_impact", "value": "smb"}]', "custom_fields")
        out[item["key"]] = item["value"]
    return out


def upd_kw(ctx: Ctx, src: dict, key: str | None, labels: bool = True) -> dict:
    """Translate update arguments (flat for S good, attributes for S naive) to Store kwargs."""
    S = ctx.cfg.S
    kw: dict[str, Any] = {}
    for f in ("status", "priority", "resolution", "due_date", "duplicate_of"):
        if src.get(f) is not None:
            kw[f] = src[f]
    if not S:
        # Jira precedent: status/resolution only change through workflow transitions, never a direct field write.
        for f in ("status", "resolution", "duplicate_of"):
            if f in kw:
                raise DomainError("invalid_field", f"field '{f}' cannot be set directly",
                                  f"Move issues through their workflow with {nm(ctx.cfg, 'transition')}.", f)
        if "priority" in kw:
            kw["priority"] = prio_in(ctx, kw["priority"])
    if S:
        if src.get("assignee") is not None:
            kw["assignee_id"] = resolve_user(ctx, src["assignee"], "assignee")
        if src.get("cycle") is not None:
            kw["cycle_id"] = resolve_cycle(ctx, src["cycle"], "cycle")
        if src.get("customer_impact") is not None:
            kw["custom_fields"] = {"customer_impact": src["customer_impact"]}
        if labels and (src.get("add_labels") or src.get("remove_labels")):
            kw["label_ids"] = _label_ids(ctx, key, src.get("add_labels"), src.get("remove_labels"))
    else:
        if src.get("assignee_id") is not None:
            kw["assignee_id"] = src["assignee_id"]
        if src.get("cycle_id") is not None:
            kw["cycle_id"] = src["cycle_id"]
        if src.get("custom_fields") is not None:
            kw["custom_fields"] = _custom_from_list(src["custom_fields"])
        lab = src.get("labels")
        if labels and isinstance(lab, dict) and (lab.get("connect") or lab.get("disconnect")):
            kw["label_ids"] = _label_ids(ctx, key, [x.get("id") for x in lab.get("connect") or []],
                                         [x.get("id") for x in lab.get("disconnect") or []])
    return kw


def mut_out(ctx: Ctx, res: dict) -> dict:
    if ctx.cfg.P:
        return {"receipt": res["receipt"], "issue": good_row(ctx, res, MUT_FIELDS)}
    return ctx.out(res)


# ------------------------------------------------------------------ Jira-style workflow (S naive)
# Jira REST precedent: status changes only through POST /issue/{id}/transitions with per-status transition ids.
# Realistic workflow: Backlog -> To Do -> In Progress -> In Review -> Done, Close from any open status, Reopen from Done/Closed.
# Ids are unique per (from-status, transition) and deterministic. The backend would accept any direct status write; the
# surface only ever offers these hops, so e.g. Backlog -> In Review takes three transitions.
TRANSITIONS: dict[str, list[tuple[int, str, str]]] = {
    "backlog": [(11, "Move to To Do", "todo"), (12, "Close Issue", "closed")],
    "todo": [(21, "Start Progress", "in_progress"), (22, "Move to Backlog", "backlog"), (23, "Close Issue", "closed")],
    "in_progress": [(31, "Submit for Review", "in_review"), (32, "Stop Progress", "todo"), (33, "Close Issue", "closed")],
    "in_review": [(41, "Approve", "done"), (42, "Request Changes", "in_progress"), (43, "Close Issue", "closed")],
    "done": [(51, "Reopen Issue", "todo")],
    "closed": [(61, "Reopen Issue", "todo")],
}
CLOSE_NAME, REOPEN_NAME = "Close Issue", "Reopen Issue"
PRIORITY_LABELS = ["None", "Low", "Medium", "High", "Urgent"]


def run_transitions_list(ctx: Ctx, a: dict) -> Any:
    g = ctx.store.apply("get_issue", key=a["key"] if ctx.cfg.S else a["issue_id"])
    return [{"id": i, "name": n, "to": STATUS_LABELS[to]} for i, n, to in TRANSITIONS[g["status"]]]


def run_transition(ctx: Ctx, a: dict) -> Any:
    key = a["key"] if ctx.cfg.S else a["issue_id"]
    g = ctx.store.apply("get_issue", key=key)
    tid = a["transition_id"]
    hop = next(((i, n, to) for i, n, to in TRANSITIONS[g["status"]] if i == tid), None)
    if hop is None:
        raise DomainError("invalid_transition", f"transition {tid} is not available from status '{STATUS_LABELS[g['status']]}'",
                          f"Call {nm(ctx.cfg, 'transitions_list')} for this issue to see the transitions available right now.",
                          "transition_id")
    fields = a.get("fields") or {}
    extra = [k for k in fields if k not in ("resolution", "duplicate_of")]
    if extra:
        raise DomainError("invalid_field", f"unknown field '{extra[0]}' in fields", "Accepted fields: resolution, duplicate_of.", f"fields.{extra[0]}")
    kw: dict[str, Any] = {"status": hop[2]}
    if hop[1] == CLOSE_NAME:
        if not fields.get("resolution"):
            raise DomainError("missing_field", f"transition '{CLOSE_NAME}' requires fields.resolution",
                              "Set fields.resolution to one of: fixed, wontfix, duplicate, cannot_reproduce "
                              "(duplicate also needs fields.duplicate_of).", "fields.resolution")
        kw["resolution"] = fields["resolution"]
        if fields.get("duplicate_of") is not None:
            kw["duplicate_of"] = fields["duplicate_of"]
    else:
        if fields:
            raise DomainError("invalid_field", f"fields are only accepted by '{CLOSE_NAME}'", "Send this transition without fields.", "fields")
        if hop[1] == REOPEN_NAME:
            kw.update(resolution=None, duplicate_of=None)
    return mut_out(ctx, ctx.store.apply("update_issue", key=key, **kw))


def run_priorities(ctx: Ctx, a: dict) -> Any:
    return [{"id": i, "name": n} for i, n in enumerate(PRIORITY_LABELS)]


# Jira REST precedent: priority is an opaque id, documented only by the separate /priority listing endpoint.
def prio_in(ctx: Ctx, v: Any) -> str:
    """S naive: priority is an int 0-4 (see priorities_list)."""
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 4:
        raise DomainError("invalid_value", f"invalid priority {v!r}", f"Use an id from {nm(ctx.cfg, 'priorities')}.", "priority")
    return PRIORITIES[v]


# ------------------------------------------------------------------ handlers
def run_search(ctx: Ctx, a: dict) -> Any:
    c, s = ctx.cfg, ctx.store
    if not c.P:
        # GitHub REST precedent: page/per_page, bare array, no total_count / has_more / filters, so a model that
        # stops after page 1 silently misses rows (per_page above the max is clamped, not rejected).
        if a.get("project") is None:
            raise DomainError("missing_field", "project is required", "Pass project, one of: " + ", ".join(PROJECT_KEYS), "project")
        page, per = _page_args(a)
        rows = s.apply("search_issues", project=a["project"], sort="created_asc")
        return [naive_row(ctx, i) for i in rows[(page - 1) * per:page * per]]
    labs = a.get("labels") if c.S else a.get("label_ids")
    cyc = a.get("cycle") if c.S else a.get("cycle_id")
    asg = a.get("assignee") if c.S else a.get("assignee_id")
    kw: dict[str, Any] = {"sort": a.get("sort") or "created_asc"}
    if a.get("project") is not None:
        kw["project"] = a["project"]
    if a.get("status"):
        kw["statuses"] = list(a["status"]) if isinstance(a["status"], list) else [a["status"]]
    if labs:
        kw["label_names"] = [ctx.labels()[resolve_label(ctx, x, "labels" if c.S else "label_ids")] for x in labs]
    if asg is not None:
        kw["assignee_id"] = resolve_user(ctx, asg, "assignee" if c.S else "assignee_id")
    if a.get("unassigned") is not None:
        kw["unassigned"] = a["unassigned"]
    if cyc is not None:
        kw["cycle_name"] = ctx.cycles()[resolve_cycle(ctx, cyc, "cycle" if c.S else "cycle_id")]
    for f in ("priority", "type", "text"):
        if a.get(f) is not None:
            kw[f] = a[f] if f != "priority" or c.S else prio_in(ctx, a[f])
    fields = a.get("fields")
    if fields:
        bad = [f for f in fields if f not in ROW_FIELDS]
        if bad:
            raise DomainError("invalid_enum", f"invalid fields {bad}", "Allowed values: " + ", ".join(ROW_FIELDS), "fields")
    limit = a.get("limit")
    if limit is None:
        limit = DEFAULT_LIMIT
    elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise DomainError("invalid_value", "limit must be a positive integer", f"Use 1 to {MAX_LIMIT}.", "limit")
    limit = min(limit, MAX_LIMIT)
    res = s.apply("search_issues", **kw)
    offset = dec_cursor(a["cursor"], kw) if a.get("cursor") else 0
    page = res[offset:offset + limit]
    nxt = enc_cursor(offset + limit, kw) if offset + limit < len(res) else None
    return {"total_count": len(res), "count": len(page), "has_more": nxt is not None, "next_cursor": nxt,
            "issues": [good_row(ctx, i, tuple(fields) if fields else DEFAULT_FIELDS) for i in page]}


def _page_args(a: dict) -> tuple[int, int]:
    page, per = a.get("page"), a.get("per_page")
    page = 1 if page is None else page
    per = NAIVE_PER_PAGE if per is None else per
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise DomainError("invalid_value", "page must be a positive integer", "Pages start at 1.", "page")
    if isinstance(per, bool) or not isinstance(per, int) or per < 1:
        raise DomainError("invalid_value", "per_page must be a positive integer", f"Use 1 to {NAIVE_MAX_PER_PAGE}.", "per_page")
    return page, min(per, NAIVE_MAX_PER_PAGE)


def run_get(ctx: Ctx, a: dict) -> Any:
    g = ctx.store.apply("get_issue", key=a["key"] if ctx.cfg.S else a["issue_id"])
    return good_issue_full(ctx, g) if ctx.cfg.P else ctx.out(g)


def run_get_v2(ctx: Ctx, a: dict) -> Any:
    g = ctx.store.apply("get_issue", key=a["key"] if ctx.cfg.S else a["issue_id"])
    if ctx.cfg.P:
        g = good_issue_full(ctx, g)
    else:
        g = ctx.out(g)
    return {"data": {"id": g["key"], "type": "issue", "attributes": {k: v for k, v in g.items() if k != "key"}}}


def run_update(ctx: Ctx, a: dict) -> Any:
    if ctx.cfg.S:
        key, src = a["key"], a
    else:
        key, src = a["issue_id"], attrs(a)
    return mut_out(ctx, ctx.store.apply("update_issue", key=key, **upd_kw(ctx, src, key)))


def run_put(ctx: Ctx, a: dict) -> Any:
    S = ctx.cfg.S
    if S:
        key, src = a["key"], a
        kw = {"status": src["status"], "priority": src["priority"], "resolution": src["resolution"],
              "due_date": src["due_date"], "duplicate_of": src["duplicate_of"],
              "assignee_id": resolve_user(ctx, src["assignee"], "assignee") if src["assignee"] is not None else None,
              "cycle_id": resolve_cycle(ctx, src["cycle"], "cycle") if src["cycle"] is not None else None,
              "label_ids": [resolve_label(ctx, x) for x in src["labels"] or []],
              "custom_fields": {"customer_impact": src["customer_impact"]} if src["customer_impact"] is not None else {}}
    else:
        key, src = a["issue_id"], attrs(a)
        lab = src["labels"] if isinstance(src["labels"], dict) else {}
        for f in ("status", "resolution", "duplicate_of"):
            if src.get(f) is not None:
                raise DomainError("invalid_field", f"field '{f}' cannot be set directly",
                                  f"Move issues through their workflow with {nm(ctx.cfg, 'transition')}.", f)
        kw = {"due_date": src["due_date"], "priority": prio_in(ctx, src["priority"]) if src["priority"] is not None else "none"}
        kw.update(assignee_id=src["assignee_id"], cycle_id=src["cycle_id"],
                  label_ids=[x.get("id") for x in lab.get("connect") or []],
                  custom_fields=_custom_from_list(src["custom_fields"]))
    return mut_out(ctx, ctx.store.apply("update_issue", key=key, **kw))


def run_bulk(ctx: Ctx, a: dict) -> Any:
    if ctx.cfg.S:
        keys, src = a["keys"], a
    else:
        b = a["body"]["data"]
        keys, src = b.get("issue_ids"), attrs(a)
    res = ctx.store.apply("bulk_update_issues", keys=keys, **upd_kw(ctx, src, None, labels=False))
    if ctx.cfg.P:
        return {"count": res["count"], "results": [mut_out(ctx, r) for r in res["results"]]}
    return res


def run_create(ctx: Ctx, a: dict) -> Any:
    S = ctx.cfg.S
    src = a if S else attrs(a)
    kw: dict[str, Any] = {}
    for f in ("type", "description", "priority", "due_date"):
        if src.get(f) is not None:
            kw[f] = src[f]
    if "priority" in kw and not S:
        kw["priority"] = prio_in(ctx, kw["priority"])
    if S:
        if src.get("assignee") is not None:
            kw["assignee_id"] = resolve_user(ctx, src["assignee"], "assignee")
        if src.get("labels"):
            kw["label_ids"] = [resolve_label(ctx, x) for x in src["labels"]]
        if src.get("customer_impact") is not None:
            kw["custom_fields"] = {"customer_impact": src["customer_impact"]}
    else:
        if src.get("assignee_id") is not None:
            kw["assignee_id"] = src["assignee_id"]
        lab = src.get("labels")
        if isinstance(lab, dict) and lab.get("connect"):
            kw["label_ids"] = [x.get("id") for x in lab["connect"]]
        if src.get("custom_fields") is not None:
            kw["custom_fields"] = _custom_from_list(src["custom_fields"])
    return mut_out(ctx, ctx.store.apply("create_issue", project=src.get("project"), title=src.get("title"), **kw))


def run_comment(ctx: Ctx, a: dict) -> Any:
    if ctx.cfg.S:
        return ctx.store.apply("add_comment", key=a["key"], body=a["body"])
    return ctx.store.apply("add_comment", key=a["issue_id"], body=attrs(a).get("body"))


def run_comments_list(ctx: Ctx, a: dict) -> Any:
    g = ctx.store.apply("get_issue", key=a["key"] if ctx.cfg.S else a["issue_id"])
    return {"data": g["comments"]}


def run_link(ctx: Ctx, a: dict) -> Any:
    if ctx.cfg.S:
        return ctx.store.apply("link_issues", from_key=a["from_key"], type=a["type"], to_key=a["to_key"])
    at = attrs(a)
    return ctx.store.apply("link_issues", from_key=at.get("from_issue_id"), type=at.get("relation_type"), to_key=at.get("to_issue_id"))


def run_dependency(ctx: Ctx, a: dict) -> Any:
    raise DomainError("deprecated", "add_dependency is deprecated and always fails",
                      "Create a 'blocks' relation with link_issues / issue_relations_create instead.")


def run_find_users(ctx: Ctx, a: dict) -> Any:
    c = ctx.cfg
    if c.C:
        users = ctx.store.apply("find_users", query=a.get("query") if c.S else a.get("q"), team=a.get("team"))
        rows = [user_row(u) for u in users]
    else:
        # naive catalog: name search only, and rows carry no team/role/on_call (see teams_members_list)
        users = ctx.store.apply("find_users", query=a.get("query") if c.S else a.get("q"))
        rows = [light_user_row(u) for u in users]
    if not c.P:
        # GitHub REST precedent: bare array, page/per_page, no total
        page, per = _page_args(a)
        return rows[(page - 1) * per:page * per]
    return {"total_count": len(rows), "count": len(rows), "users": rows}


def run_users_list(ctx: Ctx, a: dict) -> Any:
    rows = [light_user_row(u) for u in ctx.store.apply("find_users")]
    page, per = _page_args(a)
    chunk = rows[(page - 1) * per:page * per]
    if not ctx.cfg.P:
        return chunk  # GitHub REST precedent: bare array, no total / has_more
    return {"total_count": len(rows), "page": page, "per_page": per, "has_more": page * per < len(rows), "users": chunk}


def run_labels(ctx: Ctx, a: dict) -> Any:
    s = ctx.store
    if ctx.cfg.C:
        return {"labels": s.apply("list_labels"), "cycles": s.apply("list_cycles"), "projects": s.apply("list_projects")}
    return {"data": s.apply("list_labels")}


def run_cycles(ctx: Ctx, a: dict) -> Any:
    return {"data": ctx.store.apply("list_cycles")}


def run_projects(ctx: Ctx, a: dict) -> Any:
    return {"data": ctx.store.apply("list_projects")}


# ------------------------------------------------------------------ schemas
def _projects(S): return st(S, PROJECT_KEYS)


def sch_search(c: Cfg) -> dict:
    S = c.S
    if not c.P:
        return obj({"project": _projects(S), "page": it(S, 1), "per_page": it(S, 1)}, ["project"], S)
    if S:
        props = {"project": _projects(S), "status": ar(S, st(S, STATUSES)), "labels": ar(S), "assignee": st(S),
                 "unassigned": bo(S), "cycle": st(S), "priority": st(S, PRIORITIES), "type": st(S, TYPES),
                 "text": st(S), "sort": st(S, SORTS), "limit": it(S, 1, MAX_LIMIT), "cursor": st(S),
                 "fields": ar(S, st(S, ROW_FIELDS))}
    else:
        props = {"project": st(S), "status": ar(S), "label_ids": ar(S), "assignee_id": st(S), "unassigned": bo(S),
                 "cycle_id": st(S), "priority": it(S), "type": st(S), "text": st(S), "sort": st(S),
                 "limit": it(S), "cursor": st(S), "fields": ar(S)}
    return obj(props, (), S)


def _key_only(c: Cfg) -> dict:
    return obj({"key": st(True)}, ["key"], True) if c.S else obj({"issue_id": {"type": "string"}}, ["issue_id"])


def _upd_props(S: bool, with_labels: bool) -> dict:
    if S:
        p = {"status": st(S, STATUSES), "priority": st(S, PRIORITIES), "assignee": st(S), "cycle": st(S),
             "due_date": st(S), "resolution": st(S, RESOLUTIONS), "duplicate_of": st(S),
             "customer_impact": st(S, IMPACTS)}
        if with_labels:
            p.update(add_labels=ar(S), remove_labels=ar(S))
        return p
    p = {"priority": it(S), "assignee_id": st(S), "cycle_id": st(S), "due_date": st(S), "custom_fields": cf_list()}
    if with_labels:
        p["labels"] = conn()
    return p


def sch_update(c: Cfg) -> dict:
    if c.S:
        return obj({"key": st(True), **_upd_props(True, True)}, ["key"], True)
    return obj({"issue_id": {"type": "string"}, "body": body(obj(_upd_props(False, True)))}, ["issue_id", "body"])


def sch_put(c: Cfg) -> dict:
    if c.S:
        props = {"key": st(True), "status": st(True, STATUSES), "priority": st(True, PRIORITIES),
                 "assignee": st(True, nullable=True), "labels": ar(True), "cycle": st(True, nullable=True),
                 "due_date": st(True, nullable=True), "resolution": st(True, RESOLUTIONS, True),
                 "duplicate_of": st(True, nullable=True), "customer_impact": st(True, IMPACTS, True)}
        return obj(props, list(props), True)
    inner = _upd_props(False, True)
    return obj({"issue_id": {"type": "string"}, "body": body(obj(inner, list(inner)))}, ["issue_id", "body"])


def sch_bulk(c: Cfg) -> dict:
    if c.S:
        return obj({"keys": ar(True), **_upd_props(True, False)}, ["keys"], True)
    return obj({"body": body(obj(_upd_props(False, False)), {"issue_ids": {"type": "array", "items": {"type": "string"}}},
                             ("issue_ids", "attributes"))}, ["body"])


def sch_create(c: Cfg) -> dict:
    S = c.S
    if S:
        return obj({"project": _projects(S), "title": st(S), "type": st(S, TYPES), "description": st(S),
                    "priority": st(S, PRIORITIES), "assignee": st(S), "labels": ar(S), "due_date": st(S),
                    "customer_impact": st(S, IMPACTS)}, ["project", "title"], True)
    at = obj({"project": {"type": "string"}, "title": {"type": "string"}, "type": st(S), "description": st(S),
              "priority": it(S), "assignee_id": st(S), "labels": conn(), "due_date": st(S), "custom_fields": cf_list()},
             ["project", "title"])
    return obj({"body": body(at)}, ["body"])


def sch_comment(c: Cfg) -> dict:
    if c.S:
        return obj({"key": st(True), "body": st(True)}, ["key", "body"], True)
    return obj({"issue_id": {"type": "string"}, "body": body(obj({"body": {"type": "string"}}, ["body"]))}, ["issue_id", "body"])


def sch_link(c: Cfg) -> dict:
    if c.S:
        return obj({"from_key": st(True), "type": st(True, LINK_TYPES), "to_key": st(True)}, ["from_key", "type", "to_key"], True)
    at = obj({"from_issue_id": {"type": "string"}, "to_issue_id": {"type": "string"}, "relation_type": {"type": "string"}},
             ["from_issue_id", "to_issue_id", "relation_type"])
    return obj({"body": body(at)}, ["body"])


def sch_dependency(c: Cfg) -> dict:
    if c.S:
        return obj({"key": st(True), "depends_on": st(True)}, ["key", "depends_on"], True)
    return obj({"issue_id": {"type": "string"}, "depends_on_id": {"type": "string"}}, ["issue_id", "depends_on_id"])


def sch_users(c: Cfg) -> dict:
    S = c.S
    props: dict = {("query" if S else "q"): st(S)}
    if c.C:
        props["team"] = st(S, TEAMS)
    if not c.P:
        props.update(page=it(S, 1), per_page=it(S, 1))
    return obj(props, (), S)


def sch_page(c: Cfg) -> dict:
    return obj({}, (), True) if c.S else obj({"page": it(False)})


def sch_users_list(c: Cfg) -> dict:
    S = c.S
    return obj({"page": it(S, 1), "per_page": it(S, 1)}, (), S)


def sch_transition(c: Cfg) -> dict:
    if c.S:
        fields = obj({"resolution": st(True, RESOLUTIONS), "duplicate_of": st(True)}, (), True)
        return obj({"key": st(True), "transition_id": it(True), "fields": fields}, ["key", "transition_id"], True)
    fields = {"anyOf": [obj({"resolution": {"type": "string"}, "duplicate_of": {"type": "string"}}), {"type": "null"}]}
    return obj({"issue_id": {"type": "string"}, "transition_id": {"type": "integer"}, "fields": fields}, ["issue_id", "transition_id"])


def sch_none(c: Cfg) -> dict:
    return obj({}, (), True)


def sch_comments_list(c: Cfg) -> dict:
    return _key_only(c)


# ------------------------------------------------------------------ descriptions
def _ret_row(c: Cfg) -> str:
    return ("compact rows {key,title,type,status,priority,assignee,assignee_id,labels,cycle,created_seq}"
            if c.P else "full issue objects (ids only for labels/cycle/assignee)")


def d_search(c: Cfg, name: str) -> str:
    if not c.P:
        return (f"List the issues of one project, oldest first, one page at a time: page (default 1) and per_page (default 20, max 50). "
                f"There are no filters or sorting, and the response carries no total count or has_more flag, so keep requesting the next "
                f"page until you get an empty one. Use {nm(c,'get')} for one issue's details. Example: {name}(project=\"OPS\", page=2, per_page=50). "
                f"Returns a bare JSON array of compact rows "
                f"{{key,project,title,type,status,priority,assignee_id,label_ids,cycle_id,created_seq,due_date,resolution}}"
                f"{'' if c.S else ' (priority is an integer id, see ' + nm(c,'priorities') + ')'}.")
    if c.S:
        ex = f'{name}(project="PAY", status=["todo","in_progress"], labels=["regression"], unassigned=true, type="bug", limit=25)'
    else:
        ex = f'{name}(project="PAY", status=["todo"], label_ids=["L03"], unassigned=true, type="bug", priority=3)'
    return (f"Find issues matching filters; the default way to look anything up by project, status, label, assignee, cycle "
            f"or text. Prefer this over {nm(c,'get')} when you do not already know the issue key. All filters combine with AND; "
            f"omit a filter to skip it. Results are oldest first unless sort=created_desc. Page with next_cursor. "
            f"Example: {ex}. Returns {{total_count, count, has_more, next_cursor, issues:[{_ret_row(c)}]}}; next_cursor is null on the last page. "
            f"Use `fields` to trim rows when you only need a few columns.")


def d_get(c: Cfg, name: str) -> str:
    ex = f'{name}(key="PAY-142")' if c.S else f'{name}(issue_id="PAY-142")'
    other = f" {nm(c,'get_v2')} returns the same data in a JSON:API envelope; prefer this one." if c.C is False else ""
    return (f"Read one issue in full by key: description, audit_code, customer_ticket, reporter, resolution, comments and "
            f"links. Use it when you know the key; use {nm(c,'search')} to find keys.{other} Example: {ex}. "
            f"Returns the issue object with fields {{key,title,description,status,priority,assignee,reporter_id,labels,cycle,"
            f"due_date,audit_code,customer_ticket,comments,links}}.")


def d_get_v2(c: Cfg, name: str) -> str:
    ex = f'{name}(key="PAY-142")' if c.S else f'{name}(issue_id="PAY-142")'
    return (f"Beta duplicate of {nm(c,'get')}: reads one issue by key but wraps it as {{data:{{id,type,attributes}}}}. "
            f"Use {nm(c,'get')} instead unless you need that envelope. Example: {ex}. Returns {{data:{{id,type:'issue',attributes:{{...}}}}}}.")


def d_update(c: Cfg, name: str) -> str:
    extra = f" Unlike {nm(c,'put')}, only the fields you send change." if c.C is False else ""
    if c.S:
        ex = f'{name}(key="PAY-142", status="in_review", priority="urgent", add_labels=["regression"])'
        return (f"Change selected fields on ONE issue: status, priority, assignee, cycle, due date, labels, resolution, "
                f"duplicate_of, customer impact. Only the fields you pass change.{extra} Use add_labels/remove_labels (names or ids) to change "
                f"labels without touching the others. Status 'closed' requires a resolution (fixed, wontfix, duplicate, cannot_reproduce); "
                f"resolution 'duplicate' also requires duplicate_of. Assignee takes an id or a name; an ambiguous name or a deactivated user is an "
                f"error. For the same change on many issues use {nm(c,'bulk')}. Example: {ex}. Returns {{receipt, issue:{{...}}}} reflecting the new state.")
    ex = (f'{name}(issue_id="PAY-142", body={{"data":{{"attributes":{{"priority":3,"assignee_id":"U12",'
          f'"labels":{{"connect":[{{"id":"L03"}}]}}}}}}}})')
    return (f"Change selected fields on ONE issue: priority, assignee, cycle, due date, labels, customer impact. Only the fields you "
            f"send change.{extra} Status and resolution cannot be written here; move the issue through its workflow with {nm(c,'transition')}. "
            f"priority is an integer id (see {nm(c,'priorities')}). assignee_id must be a user id. labels.connect adds labels and "
            f"labels.disconnect removes them by id. Example: {ex}. Returns the updated issue with its receipt.")


def d_put(c: Cfg, name: str) -> str:
    if c.S:
        return (f"FULL REPLACE of one issue's mutable fields. Every field (status, priority, assignee, labels, cycle, due date, resolution, "
                f"duplicate_of, custom fields) must be supplied, and anything you send as null is cleared. Almost always you want {nm(c,'update')} "
                f"for a partial change. Example: {name}(key=\"PAY-142\", status=\"todo\", priority=\"high\", assignee=null, labels=[], cycle=null, "
                f"due_date=null, resolution=null, duplicate_of=null, customer_impact=null). Returns the updated issue with its receipt.")
    return (f"FULL REPLACE of one issue's mutable fields (priority, assignee_id, cycle_id, due_date, labels, custom_fields). Every field must be "
            f"supplied, and anything you send as null is cleared. Status cannot be written; use {nm(c,'transition')}. Almost always you want "
            f"{nm(c,'update')} for a partial change. Returns the updated issue with its receipt.")


def d_bulk(c: Cfg, name: str) -> str:
    what = ("status, priority, assignee, cycle, due date, resolution, duplicate_of, customer impact" if c.S
            else "priority, assignee, cycle, due date, customer impact (not status; use " + nm(c, "transition") + " per issue)")
    ex = (f'{name}(keys=["OPS-3","OPS-9"], cycle="Oct-W3")' if c.S else
          f'{name}(body={{"data":{{"issue_ids":["OPS-3","OPS-9"],"attributes":{{"cycle_id":"C3"}}}}}})')
    return (f"Apply the same change ({what}) to "
            f"several issues at once; all-or-nothing, so one invalid key or value changes nothing. Use it instead of looping {nm(c,'update')}. "
            f"Labels cannot be changed here; use {nm(c,'update')} for labels. Example: {ex}. "
            f"Returns {{count, results:[{{receipt, issue}}]}} with one receipt per issue.")


def d_create(c: Cfg, name: str) -> str:
    if c.S:
        ex = f'{name}(project="PAY", title="Refund webhook retries forever", type="bug", priority="high", labels=["regression"], due_date="2026-11-30", customer_impact="enterprise")'
    else:
        ex = (f'{name}(body={{"data":{{"attributes":{{"project":"PAY","title":"...","type":"bug","priority":3,'
              f'"labels":{{"connect":[{{"id":"L03"}}]}},"custom_fields":[{{"key":"customer_impact","value":"enterprise"}}]}}}}}})')
    return (f"Create a new issue in a project. Use it only for genuinely new work; to change an existing issue use {nm(c,'update')}. "
            f"Project and title are required; type defaults to task, priority to none{'' if c.S else ' (priority is an integer id, see ' + nm(c,'priorities') + ')'}, status starts as backlog. Example: {ex}. "
            f"Returns {{receipt, issue}} including the new issue key.")


def d_comment(c: Cfg, name: str) -> str:
    ex = f'{name}(key="OPS-3", body="triaged")' if c.S else f'{name}(issue_id="OPS-3", body={{"data":{{"attributes":{{"body":"triaged"}}}}}})'
    return (f"Post a comment on an issue. Use it for notes and status text; it does not change issue fields. Example: {ex}. "
            f"Returns {{receipt, id, issue_key, author, body}}.")


def d_comments_list(c: Cfg, name: str) -> str:
    ex = f'{name}(key="OPS-3")' if c.S else f'{name}(issue_id="OPS-3")'
    return f"List the comments on one issue (also included in {nm(c,'get')}). Example: {ex}. Returns {{data:[{{id,issue_key,author,body}}]}}."


def d_link(c: Cfg, name: str) -> str:
    if c.S:
        ex = f'{name}(from_key="PAY-142", type="blocks", to_key="INF-33")'
        sem = "'from_key blocks to_key' means from_key must be done before to_key; 'duplicates' marks from_key as a copy of to_key; 'relates' is symmetric."
    else:
        ex = (f'{name}(body={{"data":{{"attributes":{{"from_issue_id":"PAY-142","to_issue_id":"INF-33","relation_type":"blocks"}}}}}})')
        sem = "relation_type is one of blocks, relates, duplicates; 'from blocks to' means from_issue_id must be done before to_issue_id."
    dep = f" Do not use add_dependency; it is deprecated and always fails." if c.C is False else ""
    return (f"Create a relation between two issues. {sem} Linking does not change either issue's status; closing a duplicate "
            f"is done with {nm(c,'update')}.{dep} Example: {ex}. Returns {{receipt, id, from_key, type, to_key}}; "
            f"an existing identical link is a conflict.")


def d_dependency(c: Cfg, name: str) -> str:
    return (f"DEPRECATED: always fails with 'deprecated'. Use {nm(c,'link')} with type 'blocks' instead. "
            f"Example: {name}(key=\"PAY-142\", depends_on=\"INF-33\") -> error.")


def _page_note(c: Cfg) -> str:
    return "" if c.P else " Paged with page/per_page (default 20, max 50); the response has no total, so request pages until one is empty."


def d_users(c: Cfg, name: str) -> str:
    if c.C:
        ex = f'{name}(query="alex", team="Platform")' if c.S else f'{name}(q="alex", team="Platform")'
        return (f"Look up users by case-insensitive name/handle substring and/or team; use it to get the user id you need for assignee fields "
                f"when a name is ambiguous (two people can share a name across teams, and some users are deactivated).{_page_note(c)} Example: {ex}. "
                f"Returns {'{total_count, count, users:[{id,name,handle,team,role,active,on_call}]}' if c.P else 'a bare array of {id,name,handle,team,role,active,on_call}'}; "
                f"role is engineer, lead or pm; on_call marks the team's on-call engineer.")
    return (f"Search users by a case-insensitive substring of the display name. Example: {name}(q=\"alex\"). "
            f"Returns {'{total_count, count, users:[{id,display_name,email}]}' if c.P else 'a bare array of {id,display_name,email}'}; "
            f"team, role and on-call state are not included (see {'teams_list + teams_members_list'}).{_page_note(c)}")


def d_users_list(c: Cfg, name: str) -> str:
    ret = ("{total_count, page, per_page, has_more, users:[{id,display_name,email}]}" if c.P
           else "a bare array of {id,display_name,email}")
    note = " Page with page and per_page (default 20, max 50)." + ("" if c.P else " There is no total; request pages until one is empty.")
    return (f"List users in the workspace.{note} Rows carry no team, role or on-call information; use {'teams_list + teams_members_list'} for that. "
            f"Example: {name}(page=1, per_page=50). Returns {ret}.")


def d_transitions_list(c: Cfg, name: str) -> str:
    ex = f'{name}(key="PAY-142")' if c.S else f'{name}(issue_id="PAY-142")'
    return (f"List the workflow transitions available for one issue from its CURRENT status; the ids differ per status and change after every "
            f"transition. Not every status is reachable in one step (e.g. Backlog -> To Do -> In Progress -> In Review -> Done), and Close and Reopen "
            f"exist too. Example: {ex}. Returns a bare array of {{id, name, to}}.")


def d_transition(c: Cfg, name: str) -> str:
    ex = (f'{name}(key="PAY-150", transition_id=23, fields={{"resolution":"duplicate","duplicate_of":"PAY-149"}})' if c.S
          else f'{name}(issue_id="PAY-150", transition_id=23, fields={{"resolution":"duplicate","duplicate_of":"PAY-149"}})')
    return (f"Perform one workflow transition on an issue; this is the only way to change its status"
            f"{'' if c.S else ' (the update tools cannot write status)'}. transition_id must come from {nm(c,'transitions_list')} for the issue's current "
            f"status. Close Issue needs fields.resolution (fixed, wontfix, duplicate, cannot_reproduce) and, for duplicate, fields.duplicate_of. "
            f"Example: {ex}. Returns the updated issue with its receipt.")


def d_priorities(c: Cfg, name: str) -> str:
    return (f"List the priority ids used by issues and update calls, from none to urgent. Example: {name}(). "
            f"Returns a bare array of {{id, name}}.")


def d_labels(c: Cfg, name: str) -> str:
    if c.C:
        return (f"List the reference data other tools accept: all labels (id,name), cycles (id,name) and projects (key,name,team). "
                f"Call it once to learn valid label and cycle names. Example: {name}(). Returns {{labels:[...], cycles:[...], projects:[...]}}.")
    return (f"List all labels (id,name). Needed to translate label names to ids; cycles are in {nm(c,'cycles')} and projects in "
            f"{nm(c,'projects')}. Example: {name}(). Returns {{data:[{{id,name}}]}}.")


def d_cycles(c: Cfg, name: str) -> str:
    return f"List the sprint cycles (Oct-W1..Oct-W4) with ids. Needed for cycle ids. Example: {name}(). Returns {{data:[{{id,name}}]}}."


def d_projects(c: Cfg, name: str) -> str:
    return f"List projects (key,name,team). Example: {name}(). Returns {{data:[{{id,key,name,team}}]}}."


# ------------------------------------------------------------------ parameter descriptions
_UPD_PD = {
    "key": "Issue key such as PAY-142.", "issue_id": "Issue key such as PAY-142 (path parameter).",
    "status": "New status: backlog, todo, in_progress, in_review, done or closed. closed needs a resolution.",
    "priority": "none, low, medium, high or urgent.",
    "assignee": "User id (U12) or full name; an ambiguous name returns the candidates with team and active flag; deactivated users are rejected.",
    "assignee_id": "User id such as U12; look ids up with the users tools.",
    "cycle": "Cycle name (Oct-W2) or id (C2).", "cycle_id": "Cycle id such as C2.",
    "due_date": "Due date as YYYY-MM-DD.", "resolution": "fixed, wontfix, duplicate or cannot_reproduce; required with status closed.",
    "duplicate_of": "Key of the original issue; required with resolution duplicate.",
    "customer_impact": "none, smb or enterprise.", "add_labels": "Label names or ids to add; existing labels are kept.",
    "remove_labels": "Label names or ids to remove.", "labels": "Label changes: connect adds labels by id, disconnect removes them by id.",
    "custom_fields": "List of {key, value}; the only custom field is customer_impact (none, smb, enterprise).",
}
PD = {
    "search": {
        "project": "Project key: PAY, INF, OPS, GRO, WEB or MOB.", "page": "Page number, starting at 1 (default 1).",
        "per_page": "Rows per page, default 20, max 50.",
        "status": "Statuses to include (any of): backlog, todo, in_progress, in_review, done, closed. Open = the first four.",
        "labels": "Label names or ids; the issue must have ALL of them.", "label_ids": "Label ids; the issue must have ALL of them.",
        "assignee": "User id or full name; an ambiguous name returns the candidates.", "assignee_id": "User id.",
        "unassigned": "true = only issues with no assignee.", "cycle": "Cycle name (Oct-W2) or id.", "cycle_id": "Cycle id.",
        "priority": "none, low, medium, high or urgent.", "type": "bug, feature or task.", "text": "Case-insensitive substring of title or description.",
        "sort": "created_asc (oldest first, default) or created_desc.", "limit": "Page size, 1-100 (default 25).",
        "cursor": "next_cursor from the previous page; keep the same filters.",
        "fields": "Columns to return per row; any of: " + ", ".join(ROW_FIELDS) + ".",
    },
    "get": {"key": "Issue key such as PAY-142.", "issue_id": "Issue key such as PAY-142 (path parameter)."},
    "update": _UPD_PD, "put": _UPD_PD,
    "bulk": {**_UPD_PD, "keys": "Issue keys to update (all-or-nothing).", "issue_ids": "Issue keys to update (all-or-nothing)."},
    "create": {**_UPD_PD, "project": "Project key: PAY, INF, OPS, GRO, WEB or MOB.", "title": "Short issue title.",
               "type": "bug, feature or task (default task).", "description": "Longer description (optional).",
               "labels": "Label names or ids to attach (names accepted); in nested form {connect:[{id}]}."},
    "comment": {"key": "Issue key.", "issue_id": "Issue key (path parameter).", "body": "Comment text."},
    "link": {"from_key": "Issue that does the blocking/duplicating.", "to_key": "The other issue.",
             "type": "blocks, relates or duplicates.", "from_issue_id": "Source issue key.", "to_issue_id": "Target issue key.",
             "relation_type": "blocks, relates or duplicates; 'from blocks to'."},
    "dependency": {"key": "Issue key.", "depends_on": "Other issue key.", "issue_id": "Issue key.", "depends_on_id": "Other issue key."},
    "users": {"query": "Case-insensitive substring of name or handle.", "q": "Case-insensitive substring of the name.",
              "team": "Platform, Growth, Payments, Mobile or Web.", "page": "Page number, starting at 1.",
              "per_page": "Rows per page, default 20, max 50."},
    "users_list": {"page": "Page number, starting at 1.", "per_page": "Rows per page, default 20, max 50."},
    "page": {"page": "Page number, starting at 1.", "per_page": "Rows per page, default 20, max 50."},
    "transitions_list": {"key": "Issue key such as PAY-142.", "issue_id": "Issue key such as PAY-142."},
    "transition": {"key": "Issue key such as PAY-142.", "issue_id": "Issue key such as PAY-142.",
                   "transition_id": "Transition id taken from the transitions list for the issue's current status.",
                   "fields": "Only for Close Issue: resolution (fixed, wontfix, duplicate, cannot_reproduce) and, for duplicate, duplicate_of.",
                   "resolution": "fixed, wontfix, duplicate or cannot_reproduce.", "duplicate_of": "Key of the original issue."},
    "comments_list": {"key": "Issue key.", "issue_id": "Issue key."},
}
PD["get_v2"] = PD["get"]

NAIVE_SHORT = {
    "search": "List issues", "get": "Get issue", "get_v2": "Get issue (v2 beta)", "update": "Patch issue", "put": "Replace issue",
    "bulk": "Bulk patch issues", "create": "Create issue", "comment": "Create comment", "comments_list": "List comments",
    "transitions_list": "List transitions", "transition": "Transition issue", "priorities": "List priorities",
    "link": "Create relation", "dependency": "Add dependency", "users": "Search users", "users_list": "List users",
    "labels": "List labels", "cycles": "List cycles", "projects": "List projects",
}


def _wf(c: Cfg) -> bool:
    return not c.C or not c.S


def _mk(id, g, n, schema, desc, pd, run, only=None):
    return Tool(id, g, n, NAIVE_SHORT[id], schema, desc, PD.get(pd, {}), run, only)


TOOLS: list[Tool] = [
    _mk("search", "search_issues", "issues_list", sch_search, d_search, "search", run_search),
    _mk("get", "get_issue", "issues_get", _key_only, d_get, "get", run_get),
    _mk("get_v2", None, "get_issue_v2_beta", _key_only, d_get_v2, "get", run_get_v2),
    _mk("update", "update_issue", "issues_patch", sch_update, d_update, "update", run_update),
    _mk("put", None, "issues_put", sch_put, d_put, "put", run_put),
    _mk("bulk", "bulk_update_issues", "issues_bulk_patch", sch_bulk, d_bulk, "bulk", run_bulk),
    _mk("create", "create_issue", "issues_create", sch_create, d_create, "create", run_create),
    _mk("comment", "add_comment", "issues_comments_create", sch_comment, d_comment, "comment", run_comment),
    _mk("comments_list", None, "issues_comments_list", sch_comments_list, d_comments_list, "comments_list", run_comments_list),
    _mk("link", "link_issues", "issue_relations_create", sch_link, d_link, "link", run_link),
    _mk("dependency", None, "add_dependency", sch_dependency, d_dependency, "dependency", run_dependency),
    _mk("users", "find_users", "users_search", sch_users, d_users, "users", run_find_users),
    _mk("users_list", None, "users_list", sch_users_list, d_users_list, "users_list", run_users_list),
    _mk("labels", "list_labels", "labels_list", lambda c: obj({}, (), c.S) if c.C or c.S else sch_page(c), d_labels, "page", run_labels),
    _mk("cycles", None, "cycles_list", sch_page, d_cycles, "page", run_cycles),
    _mk("projects", None, "projects_list", sch_page, d_projects, "page", run_projects),
    # Workflow tools beyond the 9-tool good catalog: needed whenever status cannot be written directly (S naive), and
    # part of the REST-style catalog (C naive).
    _mk("transitions_list", "list_transitions", "issues_transitions_list", _key_only, d_transitions_list, "transitions_list",
        run_transitions_list, _wf),
    _mk("transition", "transition_issue", "issues_transition", sch_transition, d_transition, "transition", run_transition, _wf),
    _mk("priorities", "list_priorities", "priorities_list", sch_none, d_priorities, "page", run_priorities, _wf),
]
BY_ID = {t.id: t for t in TOOLS}
