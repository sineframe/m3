"""Shared plumbing for the Trackly surfaces: config, schema builders, resolvers, row shapers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from _backend.fixture import IMPACTS, PRIORITIES, RESOLUTIONS, STATUSES, TEAMS, TYPES
from _backend.store import DomainError

LINK_TYPES = ("blocks", "relates", "duplicates")
SORTS = ("created_asc", "created_desc")
PROJECT_KEYS = ("PAY", "INF", "OPS", "GRO", "WEB", "MOB")
ROW_FIELDS = ("key", "title", "project", "type", "status", "priority", "assignee", "assignee_id", "labels",
              "cycle", "created_seq", "due_date", "resolution", "duplicate_of", "customer_impact")
DEFAULT_FIELDS = ("key", "title", "type", "status", "priority", "assignee", "assignee_id", "labels",
                  "cycle", "created_seq")
DEFAULT_LIMIT, MAX_LIMIT = 25, 100
NAIVE_PER_PAGE, NAIVE_MAX_PER_PAGE = 20, 50
STATUS_LABELS = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress", "in_review": "In Review",
                 "done": "Done", "closed": "Closed"}


@dataclass(frozen=True)
class Cfg:
    """True = the 'good' side of that dimension."""
    D: bool = True
    C: bool = True
    S: bool = True
    E: bool = True
    P: bool = True


def cfg_for(variant: str) -> Cfg:
    if variant == "good":
        return Cfg()
    if variant == "naive":
        return Cfg(False, False, False, False, False)
    if variant.startswith("good-") and variant[5:] in ("D", "C", "S", "E", "P"):
        return Cfg(**{variant[5:]: False})
    raise ValueError(f"unknown variant {variant!r}")


class Ctx:
    def __init__(self, store, cfg: Cfg, name: str):
        self.store, self.cfg, self.name = store, cfg, name
        self._labels = self._cycles = None

    def out(self, d: Any) -> Any:
        """S naive (Jira precedent: priority is an opaque id): priority is shown as an int 0-4."""
        if self.cfg.S or not isinstance(d, dict) or not isinstance(d.get("priority"), str):
            return d
        return {**d, "priority": PRIORITIES.index(d["priority"])}

    def labels(self) -> dict[str, str]:
        if self._labels is None:
            self._labels = {l["id"]: l["name"] for l in self.store.apply("list_labels")}
        return self._labels

    def cycles(self) -> dict[str, str]:
        if self._cycles is None:
            self._cycles = {c["id"]: c["name"] for c in self.store.apply("list_cycles")}
        return self._cycles


# ---------------------------------------------------------------- schema builders
def _null(d: dict) -> dict:
    return {"anyOf": [d, {"type": "null"}]}


def st(S: bool, enum=None, nullable: bool = False) -> dict:
    if not S:
        return _null({"type": "string"})
    d: dict[str, Any] = {"type": ["string", "null"] if nullable else "string"}
    if enum:
        d["enum"] = list(enum) + ([None] if nullable else [])
    return d


def it(S: bool, lo: int | None = None, hi: int | None = None) -> dict:
    if not S:
        return _null({"type": "integer"})
    d: dict[str, Any] = {"type": "integer"}
    if lo is not None:
        d["minimum"] = lo
    if hi is not None:
        d["maximum"] = hi
    return d


def bo(S: bool) -> dict:
    return {"type": "boolean"} if S else _null({"type": "boolean"})


def ar(S: bool, item: dict | None = None, nullable: bool = False) -> dict:
    d: dict[str, Any] = {"type": "array", "items": item or {"type": "string"}}
    if not S:
        return _null(d)
    if nullable:
        d["type"] = ["array", "null"]
    return d


def obj(props: dict, req=(), strict: bool = False) -> dict:
    d: dict[str, Any] = {"type": "object", "properties": props}
    if req:
        d["required"] = list(req)
    if strict:
        d["additionalProperties"] = False
    return d


def body(attrs: dict, extra: dict | None = None, req_data=("attributes",)) -> dict:
    """Naive REST envelope: {"body": {"data": {"attributes": {...}}}}."""
    data = obj({**(extra or {}), "attributes": attrs}, req_data)
    return obj({"data": data}, ["data"])


def conn() -> dict:
    return obj({"connect": {"type": "array", "items": obj({"id": {"type": "string"}}, ["id"])},
                "disconnect": {"type": "array", "items": obj({"id": {"type": "string"}}, ["id"])}})


def cf_list() -> dict:
    return _null({"type": "array", "items": obj({"key": {"type": "string"}, "value": {}}, ["key", "value"])})


def inject_descriptions(schema: Any, pdesc: dict[str, str]) -> Any:
    """Add per-parameter descriptions (matched by property name) to a schema tree."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k == "properties" and isinstance(v, dict):
                props = {}
                for pn, ps in v.items():
                    ps2 = inject_descriptions(ps, pdesc)
                    wrapper = pn in ("data", "attributes") or (pn == "body" and "properties" in ps)
                    if pn in pdesc and not wrapper and isinstance(ps2, dict):
                        ps2 = {**ps2, "description": pdesc[pn]}
                    props[pn] = ps2
                out[k] = props
            else:
                out[k] = inject_descriptions(v, pdesc)
        return out
    if isinstance(schema, list):
        return [inject_descriptions(x, pdesc) for x in schema]
    return schema


# ---------------------------------------------------------------- resolvers
def _is_none(v: Any) -> bool:
    return v is None


def user_row(u: dict) -> dict:
    """Rich user row (good find_users)."""
    return {"id": u["id"], "name": u["name"], "handle": u["handle"], "team": u["team"], "role": u["role"],
            "active": u.get("active", True), "on_call": u.get("on_call", False)}


def light_user_row(u: dict) -> dict:
    """Naive user row: no team/role/on_call (those live behind teams_list/teams_members_list)."""
    return {"id": u["id"], "display_name": u["name"], "email": f"{u['handle']}@trackly.example"}


def _cand(u: dict) -> str:
    return f"{u['id']} {u['name']} ({u['team']}, {'active' if u.get('active', True) else 'inactive'})"


def resolve_user(ctx: Ctx, ref: Any, field: str = "assignee") -> str:
    s = ctx.store
    if not isinstance(ref, str) or not ref.strip():
        raise DomainError("invalid_type", f"{field} must be a user id or name", "Use find_users to look up users.", field)
    ref = ref.strip()
    if not ctx.cfg.S:
        return ref  # Jira precedent: assignee is an opaque accountId, never a display name; the store validates
    users = s.apply("find_users")
    by_id = {u["id"]: u for u in users}
    if ref.upper() in by_id:
        found = by_id[ref.upper()]
    else:
        low = ref.lower().lstrip("@")
        cands = [u for u in users if u["name"].lower() == low or u["handle"].lower() == low]
        cands = cands or [u for u in users if low in u["name"].lower() or low in u["handle"].lower()]
        if len(cands) > 1:
            raise DomainError("ambiguous", f"user '{ref}' is ambiguous: {len(cands)} matches",
                              "Candidates: " + "; ".join(_cand(u) for u in cands)
                              + f". Pass the exact id of an active user as {field}.", field)
        if not cands:
            raise DomainError("not_found", f"user '{ref}' not found", "Use find_users to look up users by name or team.", field)
        found = cands[0]
    if not found.get("active", True):
        raise DomainError("invalid_value", f"user {found['name']} ({found['id']}) is deactivated",
                          "Assign an active user instead; find_users shows the active flag.", field)
    return found["id"]


def _resolve_named(ctx: Ctx, ref: Any, table: dict[str, str], kind: str, field: str) -> str:
    """Return the id of a label/cycle given by id (always) or name (S good)."""
    if not isinstance(ref, str):
        raise DomainError("invalid_type", f"{field} must be a string", f"Pass a {kind} id or name.", field)
    if ref in table:
        return ref
    if ctx.cfg.S:
        by_name = {n.lower(): i for i, n in table.items()}
        if ref.lower() in by_name:
            return by_name[ref.lower()]
        if ref.upper() in table:
            return ref.upper()
    import difflib
    close = difflib.get_close_matches(ref.lower(), [n.lower() for n in table.values()], n=3, cutoff=0.6)
    hint = (f"Did you mean {', '.join(close)}? " if close else "") + f"List {kind}s to see valid values."
    raise DomainError("not_found", f"{kind} {ref} not found", hint, field)


def resolve_label(ctx: Ctx, ref: Any, field: str = "labels") -> str:
    return _resolve_named(ctx, ref, ctx.labels(), "label", field)


def resolve_cycle(ctx: Ctx, ref: Any, field: str = "cycle") -> str:
    return _resolve_named(ctx, ref, ctx.cycles(), "cycle", field)


# ---------------------------------------------------------------- shapers
def good_row(ctx: Ctx, issue: dict, fields=DEFAULT_FIELDS) -> dict:
    s = ctx.store
    u = s.user(issue["assignee_id"]) if issue["assignee_id"] else None
    full = {
        "key": issue["key"], "title": issue["title"], "project": issue["project"], "type": issue["type"],
        "status": issue["status"], "priority": issue["priority"],
        "assignee": u["name"] if u else None, "assignee_id": issue["assignee_id"],
        "labels": [ctx.labels().get(i, i) for i in issue["label_ids"]],
        "cycle": ctx.cycles().get(issue["cycle_id"]) if issue["cycle_id"] else None,
        "created_seq": issue["created_seq"], "due_date": issue["due_date"],
        "resolution": issue["resolution"], "duplicate_of": issue["duplicate_of"],
        "customer_impact": issue["custom_fields"].get("customer_impact"),
    }
    return ctx.out({k: full[k] for k in fields})


def naive_row(ctx: Ctx, issue: dict) -> dict:
    keep = ("key", "project", "title", "type", "status", "priority", "assignee_id", "label_ids", "cycle_id",
            "created_seq", "due_date", "resolution")
    return ctx.out({k: issue[k] for k in keep})


def good_issue_full(ctx: Ctx, g: dict) -> dict:
    out = good_row(ctx, g, ROW_FIELDS)
    rep = ctx.store.user(g["reporter_id"]) if g.get("reporter_id") else None
    out.update(description=g["description"], reporter=rep["name"] if rep else None,
               reporter_id=g.get("reporter_id"), audit_code=g["audit_code"],
               customer_ticket=g["customer_ticket"], comments=g.get("comments", []), links=g.get("links", []))
    return out


def dump(data: Any, compact: bool = False) -> str:
    return json.dumps(data, sort_keys=True, **({"separators": (",", ":")} if compact else {}))


# ---------------------------------------------------------------- cursors
def qhash(filters: dict) -> str:
    return hashlib.sha1(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:8]


def enc_cursor(offset: int, filters: dict) -> str:
    raw = json.dumps({"o": offset, "q": qhash(filters)}, sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def dec_cursor(cursor: Any, filters: dict) -> int:
    hint = "Pass next_cursor from the previous response unchanged, with the same filters."
    try:
        raw = json.loads(base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4)))
        offset, q = int(raw["o"]), raw["q"]
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise DomainError("invalid_cursor", "invalid cursor", hint, "cursor") from None
    if q != qhash(filters) or offset < 0:
        raise DomainError("invalid_cursor", "cursor does not match these filters", hint, "cursor")
    return offset


__all__ = [n for n in dir() if not n.startswith("__")]
