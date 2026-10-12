"""Wire-level solver shared by the e2e and surface tests. It derives every answer from tool responses only
(never from `store.facts`), so a passing golden run proves the task is solvable from what the variant exposes."""

import json
import re
from collections.abc import Mapping

OPEN = ("backlog", "todo", "in_progress", "in_review")
PROJECTS = ["PAY", "INF", "OPS", "GRO", "WEB", "MOB"]
GN = dict(
    search="search_issues",
    get="get_issue",
    update="update_issue",
    bulk="bulk_update_issues",
    create="create_issue",
    link="link_issues",
    comment="add_comment",
    users="find_users",
    labels="list_labels",
)
NN = dict(
    search="issues_list",
    get="issues_get",
    update="issues_patch",
    bulk="issues_bulk_patch",
    create="issues_create",
    link="issue_relations_create",
    comment="issues_comments_create",
    users="users_search",
    labels="labels_list",
    cycles="cycles_list",
    users_list="users_list",
    teams="teams_list",
    members="teams_members_list",
    transitions="issues_transitions_list",
    transition="issues_transition",
    priorities="priorities_list",
)
GN_WF = dict(
    transitions="list_transitions",
    transition="transition_issue",
    priorities="list_priorities",
)
# good catalog = 9 tools; S naive (no direct status write) adds the 3 workflow tools; C naive = ~70 REST tools.
COUNTS = {
    "good": 9,
    "naive": 70,
    "good-D": 9,
    "good-C": 70,
    "good-S": 12,
    "good-E": 9,
    "good-P": 9,
}
PRIO = ["none", "low", "medium", "high", "urgent"]
CHAIN = ["backlog", "todo", "in_progress", "in_review", "done"]
DISPLAY = {
    "Backlog": "backlog",
    "To Do": "todo",
    "In Progress": "in_progress",
    "In Review": "in_review",
    "Done": "done",
    "Closed": "closed",
}


def flags(var):
    bad = var[5:] if var.startswith("good-") else ("DCSEP" if var == "naive" else "")
    return {d: d not in bad for d in "DCSEP"}


def names(var):
    f = flags(var)
    if f["C"]:
        return {**GN, **({} if f["S"] else GN_WF)}
    return NN


def thaw(x):
    if isinstance(x, Mapping):
        return {k: thaw(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [thaw(v) for v in x]
    return x


class Wire:
    """Wraps an m3 direct client: call() -> (parsed-or-text, is_error, raw text)."""

    def __init__(self, client):
        self.client = client

    def call(self, name, args=None):
        r = self.client.call_tool(name, args or {})
        text = r.content[0]["text"]
        try:
            return json.loads(text), r.is_error, text
        except ValueError:
            return text, r.is_error, text


# ---------------------------------------------------------------- a tiny agent-like solver (any var)
class Solver:
    """Solves the benchmark tasks using only what each var's tools expose."""

    def __init__(self, var, call):
        self.v, self.f, self.n, self.call = var, flags(var), names(var), call
        self._labels = self._cycles = None

    def ok(self, name, args):
        data, err, text = self.call(name, args)
        assert not err, f"{name} {args} -> {text}"
        return data

    def labels(self):
        if self._labels is None:
            d = self.ok(self.n["labels"], {})
            self._labels = {
                l["id"]: l["name"] for l in (d["labels"] if self.f["C"] else d["data"])
            }
        return self._labels

    def cycles(self):
        if self._cycles is None:
            if self.f["C"]:
                cs = self.ok(self.n["labels"], {})["cycles"]
            else:
                cs = self.ok(self.n["cycles"], {})["data"]
            self._cycles = {c["id"]: c["name"] for c in cs}
        return self._cycles

    def id_of(self, table, name):
        return next(i for i, n in table.items() if n == name)

    def _pname(self, p):
        return PRIO[p] if isinstance(p, int) else p

    def rows(self, project):
        """All issues of a project, normalised to {key, status, assignee_id, labels, cycle, type, priority, seq}."""
        out, cursor, page = [], None, 1
        while True:
            if self.f["P"]:
                args = {"project": project, "limit": 100}
                if cursor:
                    args["cursor"] = cursor
                d = self.ok(self.n["search"], args)
                raw, cursor = d["issues"], d["next_cursor"]
                for r in raw:
                    out.append(
                        dict(
                            key=r["key"],
                            title=r["title"],
                            status=r["status"],
                            assignee_id=r["assignee_id"],
                            labels=r["labels"],
                            cycle=r["cycle"],
                            type=r["type"],
                            priority=self._pname(r["priority"]),
                            seq=r["created_seq"],
                        )
                    )
                if not cursor:
                    return out
            else:
                # GitHub-style: no total and no has_more, so page until a short page comes back
                raw = self.ok(
                    self.n["search"], {"project": project, "page": page, "per_page": 50}
                )
                assert isinstance(raw, list)
                for r in raw:
                    out.append(
                        dict(
                            key=r["key"],
                            title=r["title"],
                            status=r["status"],
                            assignee_id=r["assignee_id"],
                            labels=[self.labels()[i] for i in r["label_ids"]],
                            cycle=self.cycles().get(r["cycle_id"]),
                            type=r["type"],
                            priority=self._pname(r["priority"]),
                            seq=r["created_seq"],
                        )
                    )
                if len(raw) < 50:
                    return out
                page += 1

    def all_users(self):
        """Rich user rows {id, name, team, role, active, on_call} via whatever this catalog offers."""
        if getattr(self, "_users", None) is None:
            if self.f["C"]:
                out, page = [], 1
                while True:
                    d = self.ok(
                        self.n["users"],
                        {} if self.f["P"] else {"page": page, "per_page": 50},
                    )
                    rows = d["users"] if self.f["P"] else d
                    out += rows
                    if self.f["P"] or len(rows) < 50:
                        break
                    page += 1
                self._users = out
            else:
                self._users = []
                for t in self.ok(self.n["teams"], {})["data"]:
                    for m in self.ok(self.n["members"], {"team_id": t["id"]})["data"]:
                        self._users.append(
                            dict(
                                id=m["id"],
                                name=m["display_name"],
                                team=t["name"],
                                role=m["role"],
                                active=m["active"],
                                on_call=m["on_call"],
                            )
                        )
        return self._users

    def users(self, **kw):
        q, team = kw.get("query"), kw.get("team")
        return [
            u
            for u in self.all_users()
            if (team is None or u["team"] == team)
            and (q is None or q.lower() in u["name"].lower())
        ]

    def prio(self, name):
        if self.f["S"] or name is None:
            return name
        if getattr(self, "_prio", None) is None:
            d = self.ok(self.n["priorities"], {})
            self._prio = {p["name"].lower(): p["id"] for p in d}
        return self._prio.get(name, name)

    def status_of(self, key):
        return self.get(key)["status"]

    def set_status(self, key, target, resolution=None, duplicate_of=None):
        """Naive S: walk the workflow with the transitions the server offers. Returns the last call result."""
        last, hops = None, 0
        while True:
            cur = self.status_of(key)
            if cur == target:
                return last
            hops += 1
            assert hops < 8
            args = {"issue_id": key}
            opts = self.ok(self.n["transitions"], args)
            to_by = {DISPLAY[o["to"]]: o for o in opts}
            if target == "closed" and "closed" in to_by:
                o, fields = to_by["closed"], {"resolution": resolution}
                if duplicate_of:
                    fields["duplicate_of"] = duplicate_of
            else:
                if cur in ("done", "closed"):
                    nxt = "todo"
                else:
                    ci, ti = CHAIN.index(cur), CHAIN.index(target)
                    nxt = CHAIN[ci + (1 if ti > ci else -1)]
                o, fields = to_by[nxt], None
            a = {**args, "transition_id": o["id"]}
            if fields:
                a["fields"] = fields
            last = self.call(self.n["transition"], a)
            assert not last[1], last[2]

    def update(
        self,
        key,
        *,
        status=None,
        priority=None,
        assignee_id=None,
        cycle=None,
        resolution=None,
        duplicate_of=None,
    ):
        if self.f["S"]:
            a = {
                "key": key,
                "status": status,
                "priority": priority,
                "assignee": assignee_id,
                "cycle": cycle,
                "resolution": resolution,
                "duplicate_of": duplicate_of,
            }
            return self.call(
                self.n["update"], {k: v for k, v in a.items() if v is not None}
            )
        attrs = {
            "priority": self.prio(priority),
            "assignee_id": assignee_id,
            "cycle_id": self.id_of(self.cycles(), cycle) if cycle else None,
        }
        attrs = {k: v for k, v in attrs.items() if v is not None}
        last = None
        if attrs or status is None:
            last = self.call(
                self.n["update"],
                {"issue_id": key, "body": {"data": {"attributes": attrs}}},
            )
            if last[1]:
                return last
        if status is not None:
            last = self.set_status(key, status, resolution, duplicate_of) or last
        return last

    def bulk_cycle(self, keys, cycle):
        if self.f["S"]:
            a = {"keys": keys, "cycle": cycle}
        else:
            a = {
                "body": {
                    "data": {
                        "issue_ids": keys,
                        "attributes": {"cycle_id": self.id_of(self.cycles(), cycle)},
                    }
                }
            }
        return self.ok(self.n["bulk"], a)

    def create_task(self, project, title):
        """A plain task (no labels / priority), as the H09 follow-ups need."""
        if self.f["S"]:
            a = {"project": project, "title": title, "type": "task"}
        else:
            a = {
                "body": {
                    "data": {
                        "attributes": {
                            "project": project,
                            "title": title,
                            "type": "task",
                        }
                    }
                }
            }
        return self.ok(self.n["create"], a)

    def create(self, title="New regression bug", project="PAY"):
        if self.f["S"]:
            a = {
                "project": project,
                "title": title,
                "type": "bug",
                "priority": "high",
                "labels": ["regression", "payments"],
                "due_date": "2026-11-30",
                "customer_impact": "enterprise",
            }
        else:
            lab = [
                {"id": self.id_of(self.labels(), n)} for n in ("regression", "payments")
            ]
            a = {
                "body": {
                    "data": {
                        "attributes": {
                            "project": project,
                            "title": title,
                            "type": "bug",
                            "priority": self.prio("high"),
                            "labels": {"connect": lab},
                            "due_date": "2026-11-30",
                            "custom_fields": [
                                {"key": "customer_impact", "value": "enterprise"}
                            ],
                        }
                    }
                }
            }
        return self.ok(self.n["create"], a)

    def link(self, a, t, b):
        if self.f["S"]:
            args = {"from_key": a, "type": t, "to_key": b}
        else:
            args = {
                "body": {
                    "data": {
                        "attributes": {
                            "from_issue_id": a,
                            "to_issue_id": b,
                            "relation_type": t,
                        }
                    }
                }
            }
        return self.ok(self.n["link"], args)

    def comment(self, key, text):
        if self.f["S"]:
            return self.ok(self.n["comment"], {"key": key, "body": text})
        return self.ok(
            self.n["comment"],
            {"issue_id": key, "body": {"data": {"attributes": {"body": text}}}},
        )

    def get(self, key):
        return self.ok(
            self.n["get"], {"key": key} if self.f["S"] else {"issue_id": key}
        )


# ------------------------------------------------------------------ search with filters (server-side on P good)
def _find(
    self,
    projects=None,
    *,
    labels=(),
    unassigned=None,
    type=None,
    priority=None,
    assignee_id=None,
    is_open=True,
):
    """Issues matching ALL filters. P good: filters go to the server (cursor-paged); P naive: page every requested
    project to a short page and filter client-side. Results are always re-filtered locally."""
    want = list(projects) if projects else list(PROJECTS)
    if self.f["P"]:
        out = []
        for p in [None] if not projects else want:
            args = {"limit": 100}
            if p:
                args["project"] = p
            if is_open:
                args["status"] = list(OPEN)
            if self.f["S"]:
                if labels:
                    args["labels"] = list(labels)
                if assignee_id:
                    args["assignee"] = assignee_id
                if priority:
                    args["priority"] = priority
            else:
                if labels:
                    args["label_ids"] = [self.id_of(self.labels(), n) for n in labels]
                if assignee_id:
                    args["assignee_id"] = assignee_id
                if priority:
                    args["priority"] = self.prio(priority)
            if unassigned is not None:
                args["unassigned"] = unassigned
            if type:
                args["type"] = type
            cursor = None
            while True:
                d = self.ok(
                    self.n["search"], {**args, **({"cursor": cursor} if cursor else {})}
                )
                out += [
                    dict(
                        key=r["key"],
                        title=r["title"],
                        status=r["status"],
                        assignee_id=r["assignee_id"],
                        labels=r["labels"],
                        cycle=r["cycle"],
                        type=r["type"],
                        priority=self._pname(r["priority"]),
                        seq=r["created_seq"],
                    )
                    for r in d["issues"]
                ]
                cursor = d["next_cursor"]
                if not cursor:
                    break
    else:
        out = [r for p in want for r in self.rows(p)]
    return [
        r
        for r in out
        if (not is_open or r["status"] in OPEN)
        and all(l in r["labels"] for l in labels)
        and (unassigned is None or (r["assignee_id"] is None) == unassigned)
        and (type is None or r["type"] == type)
        and (priority is None or r["priority"] == priority)
        and (assignee_id is None or r["assignee_id"] == assignee_id)
        and (not projects or r["key"].split("-")[0] in want)
    ]


def _projects(self):
    if getattr(self, "_proj", None) is None:
        if self.f["C"]:
            self._proj = self.ok(self.n["labels"], {})["projects"]
        else:
            self._proj = self.ok(self.n["projects"], {})["data"]
    return self._proj


def key_of(created):
    return created["issue"]["key"] if "issue" in created else created["key"]


Solver.find, Solver.projects = _find, _projects
NN["projects"] = "projects_list"


# ------------------------------------------------------------------ hard-suite strategies: (solver, nonce) -> answer
def _team_of(sv):
    return {u["id"]: u["team"] for u in sv.all_users()}


def h01(sv, n):
    keys = sorted(
        r["key"]
        for r in sv.find(
            ["PAY", "WEB"], labels=["regression"], unassigned=True, type="bug"
        )
        if r["cycle"] in ("Oct-W2", "Oct-W3") and "wontfix-candidate" not in r["labels"]
    )
    return f"Found {len(keys)}.\nSTATUS: DONE {len(keys)} issues: {', '.join(keys)}"


def h02(sv, n):
    team, cnt = _team_of(sv), {}
    for r in sv.find(priority="urgent"):
        if r["assignee_id"]:
            cnt[team[r["assignee_id"]]] = cnt.get(team[r["assignee_id"]], 0) + 1
    top = max(cnt, key=cnt.get)
    return f"STATUS: DONE {top} has {cnt[top]} open urgent issues"


def h03(sv, n):
    ops_team = next(p["team"] for p in sv.projects() if p["key"] == "OPS")
    oncall = next(
        u["id"] for u in sv.users(team=ops_team) if u["on_call"] and u["active"]
    )
    rows = sv.find(["OPS"], labels=["flaky"])
    for r in rows:
        if r["assignee_id"] is None:
            sv.update(r["key"], assignee_id=oncall)
        else:
            sv.comment(r["key"], f"flaky-review-{n}")
    return f"STATUS: DONE triaged {len(rows)} issues"


def h04(sv, n):
    reporter = sv.get("PAY-142")["reporter_id"]
    me = next(u for u in sv.all_users() if u["id"] == reporter)
    lead = next(u["id"] for u in sv.users(team=me["team"]) if u["role"] == "lead")
    moved = []
    # rows carry no reporter, so read each open urgent Oct-W3 candidate in full
    for r in sv.find(priority="urgent"):
        if r["cycle"] == "Oct-W3" and sv.get(r["key"])["reporter_id"] == reporter:
            sv.update(r["key"], assignee_id=lead)
            moved.append(r["key"])
    return f"STATUS: DONE reassigned {len(moved)} issues to {lead}: {', '.join(sorted(moved))}"


def h05(sv, n):
    # "on Platform" = every active Platform member (lead and PM included); only engineers can receive
    plat = [u for u in sv.users(team="Platform") if u["active"]]
    engs = [u["id"] for u in plat if u["role"] == "engineer"]
    mine = {u["id"]: [] for u in plat}
    for r in sv.find(["PAY", "INF", "OPS", "GRO", "WEB", "MOB"]):
        if r["assignee_id"] in mine and r["status"] == "in_progress":
            mine[r["assignee_id"]].append(r)
    counts = {u: len(v) for u, v in mine.items()}
    donors = [u for u in mine if counts[u] > 3]
    moves = 0
    for e in donors:
        for r in sorted(mine[e], key=lambda r: r["seq"])[3:]:
            tgt = min(
                (x for x in engs if x not in donors and counts[x] < 3),
                key=lambda x: counts[x],
            )
            sv.update(r["key"], assignee_id=tgt)
            counts[tgt] += 1
            counts[e] -= 1
            moves += 1
    return f"STATUS: DONE moved {moves} issues"


def h06(sv, n):
    done = []
    for r in sv.find(["GRO"], labels=["duplicate-candidate"]):
        m = re.search(r"[Dd]uplicate of ([A-Z]+-\d+)", sv.get(r["key"])["description"])
        sv.update(
            r["key"], status="closed", resolution="duplicate", duplicate_of=m.group(1)
        )
        done.append(r["key"])
    return f"STATUS: DONE closed {len(done)} duplicates"


def h07(sv, n):
    sam = [u for u in sv.users(query="Sam", team="Web") if u["active"]]
    assert len(sam) == 1
    sv.update("WEB-12", assignee_id=sam[0]["id"])
    return f"STATUS: DONE assigned WEB-12 to {sam[0]['name']}"


def h08(sv, n):
    changed = 0
    for r in sv.find(["MOB"], type="bug"):
        if r["priority"] != "urgent" and sv.get(r["key"])["customer_ticket"]:
            sv.update(r["key"], priority="high")
            changed += 1
    return f"STATUS: DONE changed {changed} issues"


def h09(sv, n):
    plat = {u["id"] for u in sv.users(team="Platform")}
    made = 0
    for r in sv.find(["PAY"], labels=["regression"], type="bug"):
        if r["assignee_id"] in plat:
            new = key_of(sv.create_task("INF", f"Follow-up: {r['title']}"))
            sv.link(new, "relates", r["key"])
            made += 1
    return f"STATUS: DONE created and linked {made} follow-ups"


def h10(sv, n):
    priya = sv.users(query="Priya")[0]["id"]
    for k in ("PAY-150", "PAY-9998", "PAY-151"):
        sv.update(k, assignee_id=priya)
    return "Assigned PAY-150 and PAY-151 to Priya; PAY-9998 not found.\nSTATUS: DONE PAY-150 and PAY-151 assigned, PAY-9998 not found"


HARD = {f"H{i:02d}": globals()[f"h{i:02d}"] for i in range(1, 11)}
