"""Trackly domain logic: validation, ops, receipts, and grader-only facts.

Deterministic by construction: no clock, no global random. Mutations are logged in
`Store.mutations`; reads never touch state.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import hmac
import re
from datetime import date
from typing import Any

from _backend import fixture as fx
from _backend.fixture import OPEN, PRIORITIES, RESOLUTIONS, STATUSES, TEAMS, TYPES

LINK_TYPES = ("blocks", "relates", "duplicates")
SORTS = ("created_asc", "created_desc")
UPDATE_FIELDS = ("status", "priority", "assignee_id", "label_ids", "cycle_id", "due_date",
                 "resolution", "duplicate_of", "custom_fields")
SEARCH_FIELDS = ("project", "statuses", "label_names", "assignee_id", "unassigned", "cycle_name",
                 "priority", "type", "text", "sort")
CREATE_FIELDS = ("project", "title", "type", "description", "priority", "assignee_id", "label_ids",
                 "due_date", "custom_fields")
_KEY_RE = re.compile(r"^([A-Za-z]+)-0*(\d+)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DomainError(Exception):
    def __init__(self, code: str, message: str, hint: str = "", field: str | None = None):
        super().__init__(message)
        self.code, self.message, self.hint, self.field = code, message, hint, field


def _allowed(values) -> str:
    return "Allowed values: " + ", ".join(values)


class Store:
    def __init__(self, seed: int = 7, nonce: str = "N0"):
        self.seed, self.nonce = seed, nonce
        state, planted = fx.build(seed, nonce)
        self._users = state["users"]
        self._projects = state["projects"]
        self._labels = state["labels"]
        self._cycles = state["cycles"]
        self._issues = state["issues"]
        self._comments = state["comments"]
        self._links = state["links"]
        self._by_key = {i["key"]: i for i in self._issues}
        self._by_user = {u["id"]: u for u in self._users}
        self.mutations: list[dict] = []
        self.facts = self._compute_facts()
        self._check_invariants(planted)

    # ---- helpers for graders ----
    def issue(self, key: str) -> dict | None:
        return self._by_key.get(self._norm_key(key) or key)

    def user(self, uid: str) -> dict | None:
        return self._by_user.get(uid)

    def snapshot(self) -> dict:
        return copy.deepcopy({"users": self._users, "projects": self._projects, "labels": self._labels,
                              "cycles": self._cycles, "issues": self._issues,
                              "comments": self._comments, "links": self._links})

    def apply(self, op: str, **kw: Any) -> Any:
        fn = getattr(self, f"_op_{op}", None) if op in self._OPS else None
        if fn is None:
            raise DomainError("not_found", f"unknown operation {op}", _allowed(sorted(self._OPS)), "op")
        return fn(**kw)

    _OPS = ("get_issue", "search_issues", "update_issue", "bulk_update_issues", "create_issue",
            "add_comment", "link_issues", "find_users", "list_labels", "list_cycles", "list_projects")

    # ---- lookups / validation ----
    def _norm_key(self, key: Any) -> str | None:
        m = _KEY_RE.match(key.strip()) if isinstance(key, str) else None
        return fx.fmt_key(m.group(1).upper(), int(m.group(2))) if m else None

    def _get(self, key: Any, field: str = "key") -> dict:
        norm = self._norm_key(key)
        if norm in self._by_key:
            return self._by_key[norm]
        close = difflib.get_close_matches(norm or str(key).upper(), list(self._by_key), n=1, cutoff=0.6)
        hint = f"Did you mean {close[0]}?" if close else "Use search_issues to find valid issue keys."
        raise DomainError("not_found", f"issue {key} not found", hint, field)

    def _enum(self, field: str, value: Any, allowed) -> Any:
        if value not in allowed:
            raise DomainError("invalid_enum", f"invalid {field} {value!r}", _allowed(allowed), field)
        return value

    def _user_id(self, uid: Any, field: str = "assignee_id") -> str:
        if uid in self._by_user:
            return uid
        close = difflib.get_close_matches(str(uid), list(self._by_user), n=1, cutoff=0.6)
        hint = (f"Did you mean {close[0]} ({self._by_user[close[0]]['name']})? " if close else "") + \
            "Use find_users to look up user ids."
        raise DomainError("not_found", f"user {uid} not found", hint.strip(), field)

    def _assignee_id(self, uid: Any, field: str = "assignee_id") -> str:
        uid = self._user_id(uid, field)
        u = self._by_user[uid]
        if not u["active"]:
            raise DomainError("invalid_value", f"user {uid} ({u['name']}) is deactivated",
                              "user is deactivated; assign to an active user (find_users shows each user's active flag).",
                              field)
        return uid

    def _label_ids(self, ids: Any, field: str = "label_ids") -> list[str]:
        if not isinstance(ids, (list, tuple)):
            raise DomainError("invalid_type", f"{field} must be a list", "Pass a list of label ids.", field)
        known = {l["id"] for l in self._labels}
        out: list[str] = []
        for x in ids:
            if x not in known:
                close = difflib.get_close_matches(str(x), sorted(known), n=1, cutoff=0.6)
                hint = (f"Did you mean {close[0]}? " if close else "") + "Use list_labels to see label ids."
                raise DomainError("not_found", f"label {x} not found", hint.strip(), field)
            if x not in out:
                out.append(x)
        return out

    def _label_by_name(self, name: Any) -> str:
        names = {l["name"].lower(): l["id"] for l in self._labels}
        if isinstance(name, str) and name.lower() in names:
            return names[name.lower()]
        close = difflib.get_close_matches(str(name).lower(), list(names), n=1, cutoff=0.6)
        hint = f"Did you mean {close[0]}?" if close else "Use list_labels to see label names."
        raise DomainError("not_found", f"label {name} not found", hint, "label_names")

    def _cycle_id(self, cid: Any, field: str = "cycle_id") -> str:
        ids = {c["id"]: c for c in self._cycles}
        if cid in ids:
            return cid
        raise DomainError("not_found", f"cycle {cid} not found",
                          "Valid cycles: " + ", ".join(f"{c['id']} ({c['name']})" for c in self._cycles), field)

    def _date(self, value: Any, field: str = "due_date") -> str:
        ok = isinstance(value, str) and _DATE_RE.match(value)
        if ok:
            try:
                date.fromisoformat(value)
            except ValueError:
                ok = False
        if not ok:
            raise DomainError("invalid_date", f"invalid {field} {value!r}",
                              "Use YYYY-MM-DD, e.g. 2026-10-31.", field)
        return value

    def _custom(self, cf: Any) -> dict:
        if not isinstance(cf, dict):
            raise DomainError("invalid_type", "custom_fields must be an object",
                              'Example: {"customer_impact": "smb"}', "custom_fields")
        for k, v in cf.items():
            if k != "customer_impact":
                raise DomainError("invalid_field", f"unknown custom field {k}",
                                  "Only customer_impact is defined.", "custom_fields")
            self._enum("customer_impact", v, fx.IMPACTS)
        return dict(cf)

    def _unknown(self, kw: dict, allowed) -> None:
        bad = sorted(set(kw) - set(allowed))
        if bad:
            raise DomainError("invalid_field", f"unknown field {bad[0]}", "Allowed fields: " + ", ".join(allowed), bad[0])

    # ---- receipts / mutation log ----
    def _receipt(self, seq: int, op: str) -> str:
        return "R-" + hmac.new(self.nonce.encode(), f"{seq}:{op}".encode(), hashlib.sha256).hexdigest()[:8]

    def _log(self, op: str, args: dict) -> str:
        seq = len(self.mutations) + 1
        receipt = self._receipt(seq, op)
        self.mutations.append({"seq": seq, "op": op, "args": copy.deepcopy(args), "receipt": receipt})
        return receipt

    # ---- reads ----
    def _op_get_issue(self, key: str) -> dict:
        i = self._get(key)
        out = copy.deepcopy(i)
        out["comments"] = copy.deepcopy([c for c in self._comments if c["issue_key"] == i["key"]])
        out["links"] = copy.deepcopy([l for l in self._links if i["key"] in (l["from_key"], l["to_key"])])
        return out

    def _op_search_issues(self, **kw: Any) -> list[dict]:
        self._unknown(kw, SEARCH_FIELDS)
        project, statuses = kw.get("project"), kw.get("statuses")
        if project is not None and project not in {p["key"] for p in self._projects}:
            raise DomainError("not_found", f"project {project} not found",
                              "Valid projects: " + ", ".join(p["key"] for p in self._projects), "project")
        if isinstance(statuses, str):
            statuses = [statuses]
        for s in statuses or []:
            self._enum("statuses", s, STATUSES)
        names = kw.get("label_names")
        lids = [self._label_by_name(n) for n in ([names] if isinstance(names, str) else names or [])]
        if kw.get("assignee_id") is not None:
            self._user_id(kw["assignee_id"])
        cycle = None
        if kw.get("cycle_name") is not None:
            by_name = {c["name"].lower(): c["id"] for c in self._cycles}
            cycle = by_name.get(str(kw["cycle_name"]).lower())
            if cycle is None:
                raise DomainError("not_found", f"cycle {kw['cycle_name']} not found",
                                  "Valid cycles: " + ", ".join(c["name"] for c in self._cycles), "cycle_name")
        if kw.get("priority") is not None:
            self._enum("priority", kw["priority"], PRIORITIES)
        if kw.get("type") is not None:
            self._enum("type", kw["type"], TYPES)
        sort = self._enum("sort", kw.get("sort") or "created_asc", SORTS)
        text = kw["text"].lower() if kw.get("text") else None
        out = []
        for i in self._issues:
            if project and i["project"] != project:
                continue
            if statuses and i["status"] not in statuses:
                continue
            if lids and not all(l in i["label_ids"] for l in lids):
                continue
            if kw.get("assignee_id") is not None and i["assignee_id"] != kw["assignee_id"]:
                continue
            if kw.get("unassigned") and i["assignee_id"] is not None:
                continue
            if cycle and i["cycle_id"] != cycle:
                continue
            if kw.get("priority") is not None and i["priority"] != kw["priority"]:
                continue
            if kw.get("type") is not None and i["type"] != kw["type"]:
                continue
            if text and text not in (i["title"] + " " + i["description"]).lower():
                continue
            out.append(i)
        out.sort(key=lambda i: i["created_seq"], reverse=(sort == "created_desc"))
        return copy.deepcopy(out)

    def _op_find_users(self, query: str | None = None, team: str | None = None) -> list[dict]:
        if team is not None:
            match = {t.lower(): t for t in TEAMS}.get(str(team).lower())
            if match is None:
                raise DomainError("invalid_enum", f"invalid team {team!r}", _allowed(TEAMS), "team")
            team = match
        q = query.lower() if query else None
        return copy.deepcopy([u for u in self._users if (team is None or u["team"] == team)
                              and (q is None or q in u["name"].lower() or q in u["handle"].lower())])

    def _op_list_labels(self) -> list[dict]:
        return copy.deepcopy(self._labels)

    def _op_list_cycles(self) -> list[dict]:
        return copy.deepcopy(self._cycles)

    def _op_list_projects(self) -> list[dict]:
        return copy.deepcopy(self._projects)

    # ---- mutations ----
    def _plan_update(self, issue: dict, kw: dict) -> dict:
        self._unknown(kw, UPDATE_FIELDS)
        if not kw:
            raise DomainError("missing_field", "no fields to update", "Provide at least one of: " + ", ".join(UPDATE_FIELDS))
        ch: dict[str, Any] = {}
        if "status" in kw:
            ch["status"] = self._enum("status", kw["status"], STATUSES)
        if "priority" in kw:
            ch["priority"] = self._enum("priority", kw["priority"], PRIORITIES)
        if "resolution" in kw and kw["resolution"] is not None:
            ch["resolution"] = self._enum("resolution", kw["resolution"], RESOLUTIONS)
        elif "resolution" in kw:
            ch["resolution"] = None
        if kw.get("assignee_id") is not None:
            ch["assignee_id"] = self._assignee_id(kw["assignee_id"])
        elif "assignee_id" in kw:
            ch["assignee_id"] = None
        if "label_ids" in kw:
            ch["label_ids"] = self._label_ids(kw["label_ids"])
        if kw.get("cycle_id") is not None:
            ch["cycle_id"] = self._cycle_id(kw["cycle_id"])
        elif "cycle_id" in kw:
            ch["cycle_id"] = None
        if kw.get("due_date") is not None:
            ch["due_date"] = self._date(kw["due_date"])
        elif "due_date" in kw:
            ch["due_date"] = None
        if kw.get("duplicate_of") is not None:
            dup = self._get(kw["duplicate_of"], "duplicate_of")
            if dup["key"] == issue["key"]:
                raise DomainError("invalid_value", "an issue cannot duplicate itself",
                                  "Pass the key of the original issue.", "duplicate_of")
            ch["duplicate_of"] = dup["key"]
        elif "duplicate_of" in kw:
            ch["duplicate_of"] = None
        if "custom_fields" in kw:
            ch["custom_fields"] = {**issue["custom_fields"], **self._custom(kw["custom_fields"])}
        merged = {**issue, **ch}
        if merged["status"] == "closed" and merged["resolution"] is None:
            raise DomainError("missing_field", "closed issues require a resolution",
                              "Set resolution to one of: " + ", ".join(RESOLUTIONS), "resolution")
        if merged["resolution"] == "duplicate" and merged["duplicate_of"] is None:
            raise DomainError("missing_field", "resolution duplicate requires duplicate_of",
                              "Pass duplicate_of with the key of the original issue, e.g. PAY-149.", "duplicate_of")
        return ch

    def _op_update_issue(self, key: str, **kw: Any) -> dict:
        issue = self._get(key)
        ch = self._plan_update(issue, kw)
        issue.update(ch)
        receipt = self._log("update_issue", {"key": issue["key"], **kw})
        return {"receipt": receipt, **copy.deepcopy(issue)}

    def _op_bulk_update_issues(self, keys: list[str], **kw: Any) -> dict:
        if not isinstance(keys, (list, tuple)) or not keys:
            raise DomainError("missing_field", "keys must be a non-empty list", "Pass issue keys, e.g. [\"PAY-149\"].", "keys")
        issues: list[dict] = []
        for k in keys:
            i = self._get(k, "keys")
            if i not in issues:
                issues.append(i)
        plans = []
        for i in issues:  # validate everything first: all-or-nothing
            try:
                plans.append(self._plan_update(i, kw))
            except DomainError as e:
                raise DomainError(e.code, f"{i['key']}: {e.message}", e.hint, e.field) from None
        results = []
        for i, ch in zip(issues, plans):
            i.update(ch)
            receipt = self._log("bulk_update_issues", {"keys": [i["key"]], **kw})
            results.append({"receipt": receipt, **copy.deepcopy(i)})
        return {"count": len(results), "results": results}

    def _op_create_issue(self, project: str, title: str, **kw: Any) -> dict:
        self._unknown(kw, CREATE_FIELDS)
        projects = [p["key"] for p in self._projects]
        if project not in projects:
            close = difflib.get_close_matches(str(project).upper(), projects, n=1, cutoff=0.5)
            raise DomainError("not_found", f"project {project} not found",
                              f"Did you mean {close[0]}?" if close else _allowed(projects), "project")
        if not isinstance(title, str) or not title.strip():
            raise DomainError("missing_field", "title must not be empty", "Provide a short descriptive title.", "title")
        itype = self._enum("type", kw.get("type", "task"), TYPES)
        priority = self._enum("priority", kw.get("priority", "none"), PRIORITIES)
        assignee = self._assignee_id(kw["assignee_id"]) if kw.get("assignee_id") is not None else None
        labels = self._label_ids(kw.get("label_ids") or [])
        due = self._date(kw["due_date"]) if kw.get("due_date") is not None else None
        custom = self._custom(kw.get("custom_fields") or {})
        nums = [int(i["key"].split("-")[1]) for i in self._issues if i["project"] == project]
        key = fx.fmt_key(project, max(nums) + 1)
        seq = max(i["created_seq"] for i in self._issues) + 1
        digest = hashlib.sha256(f"{self.seed}:{self.nonce}:{key}".encode()).hexdigest()
        issue = {
            "key": key, "project": project, "title": title.strip(),
            "description": str(kw.get("description") or "")[:2000], "type": itype, "status": "backlog",
            "priority": priority, "assignee_id": assignee, "reporter_id": None, "label_ids": labels,
            "cycle_id": None, "created_seq": seq, "due_date": due, "custom_fields": custom,
            "resolution": None, "duplicate_of": None, "audit_code": f"AC-{int(digest[:8], 16) % 90000 + 10000}",
            "customer_ticket": None,
        }
        self._issues.append(issue)
        self._by_key[key] = issue
        receipt = self._log("create_issue", {"project": project, "title": title, **kw})
        return {"receipt": receipt, **copy.deepcopy(issue)}

    def _op_add_comment(self, key: str, body: str) -> dict:
        issue = self._get(key)
        if not isinstance(body, str) or not body.strip():
            raise DomainError("missing_field", "comment body must not be empty", "Provide the comment text.", "body")
        c = {"id": f"CM{len(self._comments) + 1:03d}", "issue_key": issue["key"], "author": "agent", "body": body}
        self._comments.append(c)
        receipt = self._log("add_comment", {"key": issue["key"], "body": body})
        return {"receipt": receipt, **copy.deepcopy(c)}

    def _op_link_issues(self, from_key: str, type: str, to_key: str) -> dict:
        ltype = self._enum("type", type, LINK_TYPES)
        a, b = self._get(from_key, "from_key"), self._get(to_key, "to_key")
        if a is b:
            raise DomainError("invalid_value", "cannot link an issue to itself", "Pick two different issues.", "to_key")
        for l in self._links:
            same = (l["from_key"], l["type"], l["to_key"]) == (a["key"], ltype, b["key"])
            mirrored = ltype == "relates" and (l["from_key"], l["type"], l["to_key"]) == (b["key"], ltype, a["key"])
            if same or mirrored:
                raise DomainError("conflict", f"link {a['key']} {ltype} {b['key']} already exists",
                                  f"Existing link is {l['id']}; no action needed.", "type")
        link = {"id": f"LK{len(self._links) + 1:03d}", "from_key": a["key"], "type": ltype, "to_key": b["key"]}
        self._links.append(link)
        receipt = self._log("link_issues", {"from_key": a["key"], "type": ltype, "to_key": b["key"]})
        return {"receipt": receipt, **copy.deepcopy(link)}

    # ---- facts: computed by querying the data like a grader would ----
    def _compute_facts(self) -> dict:
        s = self._op_search_issues
        f: dict[str, Any] = {"nonce": self.nonce}
        p142 = self._by_key["PAY-142"]
        f["t01_status"], f["t01_audit_code"] = p142["status"], p142["audit_code"]
        f["t03_reporter_id"] = self._by_key["PAY-087"]["reporter_id"]
        alex = self._op_find_users("Alex Chen")
        f["t04_platform_alex_id"] = next(u["id"] for u in alex if u["team"] == "Platform")
        f["t04_growth_alex_id"] = next(u["id"] for u in alex if u["team"] == "Growth")
        f["t05_keys"] = sorted(i["key"] for i in s(project="PAY", statuses=list(OPEN),
                                                  label_names=["regression"], unassigned=True, type="bug"))
        ops = [i for i in s(project="OPS", statuses=list(OPEN))]
        f["t06_oldest_open_ops_key"] = ops[0]["key"]
        f["t10_keys"] = sorted(i["key"] for i in s(statuses=list(OPEN), label_names=["flaky"], cycle_name="Oct-W2"))
        engs = [u["id"] for u in self._users if u["team"] == "Platform" and u["role"] == "engineer"]
        urgent = {e: s(statuses=list(OPEN), assignee_id=e, priority="urgent") for e in engs}
        ranked = sorted(engs, key=lambda e: len(urgent[e]), reverse=True)
        f["t11_engineer_id"] = ranked[0]
        f["t11_issue_key"] = urgent[ranked[0]][0]["key"]
        f["t11_lead_id"] = next(u["id"] for u in self._users if u["team"] == "Platform" and u["role"] == "lead")
        f["t12_has_ticket"] = self._by_key["PAY-201"]["customer_ticket"] is not None
        f["t13_priya_id"] = self._op_find_users("Priya")[0]["id"]
        self._urgent_counts = {e: len(v) for e, v in urgent.items()}
        self._compute_v2_facts(f)
        return f

    def _team_of(self, uid: str | None) -> str | None:
        return self._by_user[uid]["team"] if uid else None

    def _h01_fails(self, i: dict) -> set[str]:
        """Which h01 criteria an issue violates (empty set = member)."""
        lid = {l["name"]: l["id"] for l in self._labels}
        cyc = {c["id"]: c["name"] for c in self._cycles}
        out = set()
        if i["status"] not in OPEN:
            out.add("status")
        if i["assignee_id"] is not None:
            out.add("assignee")
        if i["type"] != "bug":
            out.add("type")
        if lid["regression"] not in i["label_ids"]:
            out.add("label")
        if i["project"] not in ("PAY", "WEB"):
            out.add("project")
        if cyc.get(i["cycle_id"]) not in ("Oct-W2", "Oct-W3"):
            out.add("cycle")
        if lid["wontfix-candidate"] in i["label_ids"]:
            out.add("wontfix")
        return out

    def _compute_v2_facts(self, f: dict) -> None:
        s, OP = self._op_search_issues, list(OPEN)
        f["h01_keys"] = sorted(i["key"] for i in self._issues if not self._h01_fails(i))
        counts = {t: 0 for t in TEAMS}
        for i in self._issues:
            if i["status"] in OPEN and i["priority"] == "urgent" and i["assignee_id"]:
                counts[self._team_of(i["assignee_id"])] += 1
        self._team_urgent = counts
        top = max(counts, key=lambda t: counts[t])
        f["h02_team"], f["h02_count"] = top, counts[top]
        flaky = s(project="OPS", statuses=OP, label_names=["flaky"])
        f["h03_unassigned_keys"] = sorted(i["key"] for i in flaky if i["assignee_id"] is None)
        f["h03_assigned_keys"] = sorted(i["key"] for i in flaky if i["assignee_id"] is not None)
        f["h03_oncall_id"] = next(u["id"] for u in self._users if u["team"] == "Platform" and u["on_call"])
        rep = self._by_key["PAY-142"]["reporter_id"]
        f["h04_reporter_id"] = rep
        f["h04_lead_id"] = next(u["id"] for u in self._users
                                if u["team"] == self._by_user[rep]["team"] and u["role"] == "lead")
        f["h04_keys"] = sorted(i["key"] for i in s(statuses=OP, priority="urgent", cycle_name="Oct-W3")
                               if i["reporter_id"] == rep)
        engs = sorted(u["id"] for u in self._users if u["team"] == "Platform" and u["role"] == "engineer" and u["active"])
        f["h05_engineer_ids"] = engs
        f["h05_initial_counts"] = {e: len(s(statuses=["in_progress"], assignee_id=e)) for e in engs}
        f["h05_other_counts"] = {u["id"]: len(s(statuses=["in_progress"], assignee_id=u["id"])) for u in sorted(
            self._users, key=lambda u: u["id"]) if u["team"] == "Platform" and u["role"] != "engineer"}
        f["h06_pairs"] = {}
        for i in s(project="GRO", statuses=OP, label_names=["duplicate-candidate"]):
            m = re.search(r"Duplicate of ((?:GRO|PAY)-\d+)", i["description"])
            f["h06_pairs"][i["key"]] = m.group(1) if m else None
        f["h06_pairs"] = dict(sorted(f["h06_pairs"].items()))
        f["h07_sam_id"] = next(u["id"] for u in self._op_find_users("Sam")
                               if u["team"] == "Web" and u["active"] and u["name"] == "Sam Okafor")
        f["h07_issue"] = "WEB-12"
        f["h08_keys"] = sorted(i["key"] for i in s(project="MOB", statuses=OP, type="bug")
                               if i["customer_ticket"] and i["priority"] != "urgent")
        f["h09_sources"] = sorted(i["key"] for i in s(project="PAY", statuses=OP, label_names=["regression"], type="bug")
                                  if self._team_of(i["assignee_id"]) == "Platform")
        f["h10_existing"], f["h10_missing"] = ["PAY-150", "PAY-151"], "PAY-9998"
        f["h10_priya_id"] = f["t13_priya_id"]

    def _check_invariants(self, planted: dict) -> None:
        f = self.facts
        for k, v in planted.items():
            assert f[k] == v, f"planted {k}={v!r} but queried {f[k]!r}"
        assert 4 <= len(f["t05_keys"]) <= 9
        assert len(f["t10_keys"]) == 7
        counts = sorted(self._urgent_counts.values(), reverse=True)
        assert counts[0] > counts[1], "t11 max must be strict"
        assert isinstance(f["t12_has_ticket"], bool)
        assert f["t01_status"] in ("backlog", "todo", "in_progress")
        assert self._by_key["PAY-142"]["priority"] != "urgent"
        for k in ("PAY-142", "PAY-087", "PAY-149", "PAY-150", "PAY-201", "INF-33"):
            assert k in self._by_key, k
        assert "PAY-9999" not in self._by_key
        assert self._by_key["INF-33"]["assignee_id"] is None
        for k in ("PAY-149", "PAY-150"):
            assert self._by_key[k]["status"] in OPEN
        assert not any({l["from_key"], l["to_key"]} == {"PAY-142", "INF-33"} for l in self._links)
        assert len(self._op_find_users("Priya")) == 1
        assert len([u for u in self._users if u["name"] == "Alex Chen"]) == 2
        assert self._by_key["PAY-142"]["key"] and self._by_user[self._by_key["PAY-142"]["assignee_id"]]["team"] == "Payments"
        assert f["t06_oldest_open_ops_key"].startswith("OPS-")
        self._check_v2_invariants()

    def _check_v2_invariants(self) -> None:
        f = self.facts
        assert 10 <= len(f["h01_keys"]) <= 18, f["h01_keys"]
        for crit in ("status", "assignee", "type", "label", "project", "cycle", "wontfix"):
            n = sum(1 for i in self._issues if self._h01_fails(i) == {crit})
            assert n >= 3, f"h01 decoys for {crit}: {n}"
        # h02: strict unique max, runner-up within 2
        ranked = sorted(self._team_urgent.values(), reverse=True)
        assert ranked[0] > ranked[1] >= ranked[0] - 2, self._team_urgent
        assert self._team_urgent[f["h02_team"]] == f["h02_count"] == ranked[0]
        un, asg = f["h03_unassigned_keys"], f["h03_assigned_keys"]
        assert 9 <= len(un) + len(asg) <= 13 and len(un) >= 3 and len(asg) >= 3
        oncalls = [u for u in self._users if u["on_call"]]
        assert sorted(u["team"] for u in oncalls) == sorted(TEAMS) and all(u["active"] for u in oncalls)
        assert self._by_user[f["h03_oncall_id"]]["team"] == "Platform"
        assert self._by_user[f["h04_reporter_id"]]["active"] and self._by_user[f["h04_lead_id"]]["role"] == "lead"
        assert 4 <= len(f["h04_keys"]) <= 7
        assert len({self._by_key[k]["project"] for k in f["h04_keys"]}) >= 2
        assert all(self._by_key[k]["assignee_id"] != f["h04_lead_id"] for k in f["h04_keys"])
        counts = f["h05_initial_counts"]
        assert len(f["h05_engineer_ids"]) >= 3 and sorted(counts) == f["h05_engineer_ids"]
        donors = [e for e, c in counts.items() if c > 3]
        assert len(donors) >= 2 and min(counts.values()) <= 1
        excess = sum(c - 3 for c in counts.values() if c > 3)
        rec = sorted(c for e, c in counts.items() if c <= 3)
        assert sum(3 - c for c in rec) >= excess + 1, "h05 slack"
        assert len(rec) == 1 or rec[0] < rec[1], "h05 unique fewest recipient"
        # lead/PM are on Platform too: never over 3 (<=2), inactive Platform members hold nothing
        assert f["h05_other_counts"] and all(c <= 2 for c in f["h05_other_counts"].values()), f["h05_other_counts"]
        assert all(self._by_user[u]["role"] != "engineer" for u in f["h05_other_counts"])
        for u in self._users:
            if u["team"] == "Platform" and not u["active"]:
                assert not self._op_search_issues(statuses=["in_progress"], assignee_id=u["id"])
        pairs = f["h06_pairs"]
        assert 4 <= len(pairs) <= 6
        for d, o in pairs.items():
            assert o and o != d and self._by_key[o]["status"] in OPEN, (d, o)
            assert o not in pairs and len(self._by_key[d]["description"]) <= 160
        sam = self._by_user[f["h07_sam_id"]]
        assert sam["name"] == "Sam Okafor" and sam["team"] == "Web" and sam["active"]
        sams = [u for u in self._users if u["name"].split()[0].lower() == "sam"]
        assert sorted(u["name"] for u in sams) == ["Sam Okafor", "Sam Patel", "Sam Reyes"]
        assert [(u["name"], u["team"], u["active"]) for u in sams if u["name"] == "Sam Patel"] == [("Sam Patel", "Web", False)]
        assert sum(not u["active"] for u in self._users) == 3
        w12 = self._by_key["WEB-12"]
        assert w12["assignee_id"] is None and w12["status"] in OPEN
        assert 5 <= len(f["h08_keys"]) <= 9
        assert all(self._by_key[k]["priority"] != "high" for k in f["h08_keys"])
        assert 3 <= len(f["h09_sources"]) <= 5
        for k in f["h10_existing"]:
            assert self._by_key[k]["assignee_id"] is None and self._by_key[k]["status"] in OPEN
        assert f["h10_missing"] not in self._by_key
        assert len(self._labels) == 33
