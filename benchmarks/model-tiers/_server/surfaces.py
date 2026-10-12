"""Trackly MCP surfaces: 5 independent design dimensions (D, C, S, E, P) composed per variant.

`build_surface(variant)` returns a Surface whose `tools` are the exact wire tool dicts and whose
`dispatch(store, name, args)` never raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from _backend.store import DomainError, Store
from _server.surface_parts import distractors as dx
from _server.surface_parts.common import Cfg, Ctx, cfg_for, dump, inject_descriptions, obj, st, it, bo
from _server.surface_parts.tools import PD, TOOLS, nm

# Logical Store op behind each logical tool id (None = never reaches the Store: deprecated
# add_dependency, comment listing, and every distractor).
# Naive-surface flaws and their real-world precedent (one line each, kept next to the code that implements them):
#  P: GitHub REST v3 lists  - page/per_page (default 30, max 100), bare array, no total/has_more in the body (Link header only).
#  S: Jira REST v2          - issue status changes only via workflow transitions (POST /issue/{id}/transitions), ids per status.
#  S: Jira REST v2          - priority and other enums are opaque ids documented by a separate /priority endpoint.
#  S: Jira REST v2          - assignee is an accountId, never a display name; Close needs a resolution field on the transition.
#  S: JSON:API / Stripe     - nested {"data":{"attributes":{...}}} envelopes and {"connect":[{"id"}]} relation writes (Prisma-style).
#  E: bare HTTP status      - many gateways return only "400 Bad Request" / "404 Not Found" with no body detail.
#  C: Slack/GitHub/Zendesk  - 60-70 endpoints incl. v2-beta near-duplicates, PUT-vs-PATCH twins, deprecated routes, admin/webhook noise.
#  D: terse OpenAPI summary - "List issues", "Patch issue" with no parameter descriptions.
OP_BY_TOOL_ID: dict[str, str | None] = {
    "search": "search_issues", "get": "get_issue", "get_v2": "get_issue",
    "update": "update_issue", "put": "update_issue", "bulk": "bulk_update_issues",
    "create": "create_issue", "comment": "add_comment", "comments_list": None,
    "link": "link_issues", "dependency": None, "users": "find_users", "users_list": "find_users",
    "labels": "list_labels", "cycles": "list_cycles", "projects": "list_projects",
    # Jira-style workflow tools: a transition is a status write; listing transitions/priorities reads no Store op.
    "transition": "update_issue", "transitions_list": None, "priorities": None,
}
# Distractor-catalog tools that are real read-only user lookups.
OP_BY_DISTRACTOR: dict[str, str] = {"teams_members_list": "find_users", "users_get": "find_users"}

VARIANTS = ["good", "naive", "good-D", "good-C", "good-S", "good-E", "good-P"]

_NAIVE_HINT_NAMES = {"find_users": "users_search", "list_labels": "labels_list", "search_issues": "issues_list",
                     "link_issues": "issue_relations_create", "update_issue": "issues_patch"}
_HTTP = {"not_found": "404 Not Found", "unknown_tool": "404 Not Found", "conflict": "409 Conflict"}


@dataclass(frozen=True)
class Surface:
    variant: str
    tools: list[dict]
    cfg: Cfg = field(default=Cfg(), compare=False)
    _impl: dict = field(default_factory=dict, repr=False, compare=False)
    _ops: dict = field(default_factory=dict, repr=False, compare=False)

    def op_of(self, tool_name: str) -> str | None:
        """Logical Store op behind an advertised tool (None for distractors/deprecated/unknown)."""
        return self._ops.get(tool_name)

    def dispatch(self, store: Store, name: str, args: dict) -> tuple[str, bool]:
        try:
            return self._dispatch(store, name, args)
        except DomainError as e:
            return self._error(e), True
        except Exception as e:  # never raise out of dispatch
            return self._error(DomainError("internal_error", f"internal error: {type(e).__name__}: {e}", "")), True

    # -- internals
    def _error(self, e: DomainError) -> str:
        if not self.cfg.E:
            return _HTTP.get(e.code, "400 Bad Request")
        msg, hint = e.message, e.hint
        if not self.cfg.C:
            for a, b in _NAIVE_HINT_NAMES.items():
                msg, hint = msg.replace(a, b), hint.replace(a, b)
        err: dict[str, Any] = {"code": e.code, "message": msg}
        if hint:
            err["hint"] = hint
        if e.field:
            err["field"] = e.field
        return json.dumps({"error": err}, sort_keys=True)

    def _dispatch(self, store: Store, name: str, args: Any) -> tuple[str, bool]:
        spec = self._impl.get(name) if isinstance(name, str) else None
        if spec is None:
            avail = ", ".join(sorted(self._impl)) if self.cfg.C else "see tools/list"
            raise DomainError("unknown_tool", f"unknown tool {name!r}", f"Available tools: {avail}")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise DomainError("invalid_argument", "arguments must be an object", "Pass a JSON object of named arguments.")
        schema, run = spec
        check(schema, args, "", top=True)
        data = run(Ctx(store, self.cfg, name), args)
        return dump(data, compact=not self.cfg.P), False


# ------------------------------------------------------------------ loose schema validation
_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def check(schema: dict, v: Any, path: str, top: bool = False) -> None:
    label = path or "arguments"
    if "anyOf" in schema:
        first = None
        for sub in schema["anyOf"]:
            try:
                check(sub, v, path)
                return
            except DomainError as e:
                first = first or e
        raise first  # type: ignore[misc]
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](v) for x in ts):
            raise DomainError("invalid_argument", f"{label}: expected {' or '.join(ts)}, got {type(v).__name__}",
                              f"Pass {path or 'arguments'} as {' or '.join(ts)}.", path or None)
    if "enum" in schema and v not in schema["enum"]:
        raise DomainError("invalid_enum", f"invalid {label} {v!r}",
                          "Allowed values: " + ", ".join(str(x) for x in schema["enum"] if x is not None), path or None)
    if isinstance(v, int) and not isinstance(v, bool):
        if "minimum" in schema and v < schema["minimum"] or "maximum" in schema and v > schema["maximum"]:
            raise DomainError("invalid_value", f"{label} out of range: {v}",
                              f"Use a value between {schema.get('minimum', '-inf')} and {schema.get('maximum', 'inf')}.", path or None)
    if isinstance(v, dict) and "properties" in schema:
        props = schema["properties"]
        for r in schema.get("required", []):
            if r not in v:
                full = f"{path}.{r}" if path else r
                raise DomainError("missing_field", f"missing required argument '{full}'",
                                  "Required: " + ", ".join(schema["required"]), full)
        if schema.get("additionalProperties") is False:
            extra = [k for k in v if k not in props]
            if extra:
                raise DomainError("invalid_argument", f"unknown argument '{extra[0]}'",
                                  "Valid arguments: " + ", ".join(props), extra[0])
        for k, sub in props.items():
            if k in v:
                check(sub, v[k], f"{path}.{k}" if path else k)
    if isinstance(v, list) and "items" in schema:
        for i, x in enumerate(v):
            check(schema["items"], x, f"{path}[{i}]")


# ------------------------------------------------------------------ distractor tools
def _dx_schema(cfg: Cfg, params: list, write: bool) -> dict:
    S = cfg.S
    mk = {"s": st, "i": it, "b": bo}
    flat = {p: mk[k](S) for p, k, _ in params}
    req = [p for p, _, r in params if r]
    if S or not write:
        return obj(flat, req, S)
    path = {p: {"type": "string"} for p, _, _ in params if p == "id"}
    attrs = {p: s for p, s in flat.items() if p != "id"}
    inner_req = [p for p in req if p != "id"]
    from _server.surface_parts.common import body
    out = {**path, "body": body(obj(attrs, inner_req))} if attrs else dict(path)
    return obj(out, (["id"] if path else []) + (["body"] if inner_req else []))


def _dx_desc(cfg: Cfg, spec: tuple) -> str:
    name, short, core, ret, params, write = spec
    if not cfg.D:
        return short
    ex = ", ".join(f'{p}="x"' if k == "s" else f"{p}=1" if k == "i" else f"{p}=true" for p, k, r in params if r) or ""
    if name in ("teams_list", "teams_members_list", "users_get"):
        return f"{core} Useful together with users_list, which has no team, role or on-call data. Example: {name}({ex}). Returns {ret}."
    return (f"{core} Administrative/ancillary endpoint unrelated to issue tracking: it never reads or changes issues, so do not use it to "
            f"look up or edit issues. Example: {name}({ex}). Returns {ret}.")


_DX_PD = {"id": "Identifier of the record.", "page": "Page number (1-based).", "url": "HTTPS endpoint URL.",
          "events": "Event name to subscribe to.", "locale": "Locale code such as en-US.", "name": "Display name.",
          "format": "csv or json.", "provider": "Provider slug such as slack.", "team": "Team name.", "team_id": "Team id from teams_list, such as T1.", "email": "Enable email delivery."}


# ------------------------------------------------------------------ build
def build_surface(variant: str) -> Surface:
    cfg = cfg_for(variant)
    tools: list[dict] = []
    impl: dict[str, tuple[dict, Callable]] = {}
    ops: dict[str, str | None] = {}

    def add(name: str, desc: str, schema: dict, run: Callable) -> None:
        tools.append({"name": name, "description": desc, "inputSchema": {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}})
        impl[name] = (schema, run)

    for t in TOOLS:
        name = t.g if cfg.C else t.n
        if name is None:
            continue
        if t.only is not None and not t.only(cfg):
            continue
        schema = t.schema(cfg)
        if cfg.D:
            pdesc = t.pdesc if cfg.S else {**t.pdesc, "priority": f"Priority id (integer); the valid ids are listed by {nm(cfg, 'priorities')}."}
            schema = inject_descriptions(schema, pdesc)
        add(name, t.desc(cfg, name) if cfg.D else t.short, schema, t.run)
        ops[name] = OP_BY_TOOL_ID[t.id]
    if not cfg.C:
        for spec in dx.SPECS:
            name, params, write = spec[0], spec[4], spec[5]
            schema = _dx_schema(cfg, params, write)
            if cfg.D:
                schema = inject_descriptions(schema, _DX_PD)
            add(name, _dx_desc(cfg, spec), schema, (lambda n: lambda ctx, a: dx.stub(ctx.store, n, a))(name))
            ops[name] = OP_BY_DISTRACTOR.get(name)
    return Surface(variant=variant, tools=tools, cfg=cfg, _impl=impl, _ops=ops)
