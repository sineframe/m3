"""Builds the Trackly dataset from (seed, nonce).

Base data depends only on `seed`. Everything the graders ask about (the `facts`
the task checks read) is arranged afterwards from a second RNG keyed on (seed, nonce), by
rearranging real issues, with decoys beside each planted set.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import random
from datetime import date, timedelta

OPEN = ("backlog", "todo", "in_progress", "in_review")
STATUSES = (*OPEN, "done", "closed")
PRIORITIES = ("none", "low", "medium", "high", "urgent")
TYPES = ("bug", "feature", "task")
RESOLUTIONS = ("fixed", "wontfix", "duplicate", "cannot_reproduce")
IMPACTS = ("none", "smb", "enterprise")
TEAMS = ("Platform", "Growth", "Payments", "Mobile", "Web")

PROJECTS = [  # key, name, team(s), first number, count
    ("PAY", "Payments", ("Payments",), 87, 150),
    ("INF", "Infrastructure", ("Platform",), 1, 45),
    ("OPS", "Operations", ("Platform", "Growth"), 1, 180),
    ("GRO", "Growth", ("Growth",), 1, 45),
    ("WEB", "Web App", ("Web",), 1, 50),
    ("MOB", "Mobile App", ("Mobile",), 1, 50),
]
LABELS = [
    "bug",
    "feature",
    "regression",
    "regressions-old",
    "payments",
    "flaky",
    "customer",
    "infra",
    "security",
    "performance",
    "docs",
    "ux",
    "tech-debt",
    "api",
    "mobile",
    "web",
    "ios",
    "android",
    "growth",
    "experiment",
    "onboarding",
    "billing",
    "observability",
    "on-call",
    "compliance",
    "migration",
    "accessibility",
    "i18n",
    "needs-design",
    "blocked",
    "wontfix-candidate",
    "duplicate-candidate",
    "sla-breach",
]
CYCLES = ["Oct-W1", "Oct-W2", "Oct-W3", "Oct-W4"]
SPECIAL = ("PAY-142", "PAY-087", "PAY-149", "PAY-150", "PAY-201", "INF-33")
SPECIAL_V2 = ("PAY-151", "WEB-12")
INACTIVE = {
    ("Platform", 7),
    ("Growth", 7),
    ("Web", 3),
}  # (team, slot); Web slot 3 is Sam Patel
SAMS = {
    ("Web", 3): ("Sam Patel", "sam.patel"),
    ("Web", 4): ("Sam Okafor", "sam.okafor"),
    ("Mobile", 3): ("Sam Reyes", "sam.reyes"),
}
_KEEP = object()

FIRST = [
    "Marcus",
    "Elena",
    "Tomas",
    "Hana",
    "Diego",
    "Sofia",
    "Kenji",
    "Amara",
    "Lars",
    "Noor",
    "Jonah",
    "Mei",
    "Callum",
    "Ines",
    "Rafael",
    "Yara",
    "Owen",
    "Zainab",
    "Felix",
    "Leila",
    "Mateo",
    "Ingrid",
    "Soren",
    "Dana",
    "Theo",
    "Anika",
    "Victor",
    "Rosa",
    "Isaac",
    "Maya",
    "Gideon",
    "Nadia",
    "Carlos",
    "Freya",
    "Ravi",
    "Tessa",
    "Hugo",
    "Lucia",
    "Emil",
    "Bianca",
]
LAST = [
    "Okafor",
    "Lindqvist",
    "Moreau",
    "Tanaka",
    "Silva",
    "Novak",
    "Haddad",
    "Brennan",
    "Kowalski",
    "Reyes",
    "Ito",
    "Fischer",
    "Mbeki",
    "Larsen",
    "Costa",
    "Petrov",
    "Walsh",
    "Dubois",
    "Sato",
    "Ahmed",
    "Grant",
    "Rossi",
    "Weber",
    "Quinn",
    "Mehta",
    "Lopez",
    "Nguyen",
    "Kaplan",
    "Olsen",
    "Bauer",
    "Duarte",
    "Farouk",
    "Hale",
    "Ivanov",
    "Jensen",
    "Kim",
    "Lang",
    "Marsh",
    "Nilsson",
    "Ortiz",
]

SUBJECTS = {
    "PAY": [
        "3DS challenge flow",
        "refund webhook",
        "ledger reconciliation",
        "payout scheduler",
        "card tokenization",
        "chargeback evidence upload",
        "invoice PDF tax lines",
        "Stripe Connect onboarding",
        "FX rate cache",
        "SEPA mandate",
        "Apple Pay session",
        "dunning emails",
        "subscription proration",
        "fraud score callback",
        "settlement report export",
        "currency rounding",
        "wallet top-up",
        "PayPal capture",
        "dispute notifications",
        "idempotency keys",
    ],
    "INF": [
        "Terraform state lock",
        "Kubernetes node autoscaler",
        "staging TLS certs",
        "Postgres replica lag",
        "Vault secret rotation",
        "CI runner pool",
        "S3 lifecycle rules",
        "VPC peering",
        "Redis eviction policy",
        "service mesh sidecar",
        "artifact registry",
        "DNS failover",
        "log shipping pipeline",
        "Prometheus alerts",
        "bastion hosts",
    ],
    "OPS": [
        "on-call rotation",
        "incident postmortem template",
        "pager escalation policy",
        "status page",
        "support macros",
        "vendor invoice approvals",
        "SLA dashboard",
        "runbook index",
        "access reviews",
        "backup restore drill",
        "change calendar",
        "capacity forecast",
        "release checklist",
        "audit evidence",
        "shift handoff notes",
    ],
    "GRO": [
        "signup funnel",
        "referral banner",
        "onboarding checklist",
        "pricing page experiment",
        "email drip campaign",
        "trial expiry nudge",
        "UTM attribution",
        "churn survey",
        "landing page copy",
        "in-app announcement",
        "lifecycle segments",
        "coupon codes",
        "SEO sitemap",
        "waitlist form",
        "NPS prompt",
    ],
    "WEB": [
        "settings page",
        "data table pagination",
        "dark mode tokens",
        "search autocomplete",
        "session timeout banner",
        "CSV export button",
        "keyboard navigation",
        "form validation",
        "dashboard charts",
        "file upload widget",
        "notification center",
        "SSO login redirect",
        "breadcrumb nav",
        "modal focus trap",
        "i18n date formats",
    ],
    "MOB": [
        "push notification",
        "offline sync",
        "biometric login",
        "deep link handling",
        "launch screen",
        "image cache",
        "app store review prompt",
        "in-app purchase restore",
        "camera permission flow",
        "tab bar layout",
        "background refresh",
        "widget extension",
        "accessibility labels",
        "locale switching",
        "upload retry",
    ],
}
CONTEXTS = {
    "PAY": [
        "for EU merchants",
        "after the last deploy",
        "in sandbox",
        "on retries",
        "for JPY payments",
        "for enterprise accounts",
        "during peak load",
        "in the nightly job",
        "for partial refunds",
        "on Safari",
        "for recurring charges",
        "with expired cards",
    ],
    "INF": [
        "in us-east-1",
        "in eu-west-1",
        "on the staging cluster",
        "after the upgrade",
        "for cost reduction",
        "before the audit",
        "on spot instances",
        "for the data plane",
    ],
    "OPS": [
        "for Q4",
        "this week",
        "for the EU team",
        "after the outage",
        "before the freeze",
        "for new hires",
        "across regions",
        "per compliance request",
    ],
    "GRO": [
        "for self-serve users",
        "on mobile web",
        "in the EU",
        "for returning visitors",
        "in variant B",
        "for the Q4 campaign",
        "on the pricing page",
        "after signup",
    ],
    "WEB": [
        "in Firefox",
        "on small screens",
        "for screen readers",
        "after login",
        "in the admin console",
        "with long names",
        "on slow networks",
        "in dark mode",
    ],
    "MOB": [
        "on iOS 17",
        "on Android 14",
        "on low-memory devices",
        "after app update",
        "on tablets",
        "in airplane mode",
        "on first launch",
        "with VoiceOver",
    ],
}
TITLES = {
    "bug": [
        "{s} fails {c}",
        "{s} returns wrong result {c}",
        "Intermittent errors in {s} {c}",
        "{s} times out {c}",
        "Fix {s} crash {c}",
    ],
    "feature": [
        "Add {s} support {c}",
        "Support configurable {s} {c}",
        "Expose {s} in the dashboard {c}",
        "Allow bulk edits to {s} {c}",
    ],
    "task": [
        "Update runbook for {s} {c}",
        "Migrate {s} {c}",
        "Audit {s} {c}",
        "Clean up {s} configuration {c}",
        "Document {s} {c}",
    ],
}
WHO = ["support", "QA", "on-call", "a customer", "monitoring", "the account team"]
IMPACT = [
    "blocks release checks",
    "low volume but recurring",
    "needs triage before the next cycle",
    "a workaround exists",
    "regression risk is unclear",
    "affects a handful of accounts",
]
LABEL_POOL = {
    "PAY": [
        "payments",
        "billing",
        "api",
        "security",
        "compliance",
        "customer",
        "performance",
        "tech-debt",
    ],
    "INF": [
        "infra",
        "observability",
        "security",
        "migration",
        "on-call",
        "tech-debt",
        "performance",
    ],
    "OPS": [
        "on-call",
        "compliance",
        "docs",
        "observability",
        "blocked",
        "tech-debt",
        "customer",
    ],
    "GRO": ["growth", "experiment", "onboarding", "ux", "i18n", "customer", "docs"],
    "WEB": ["web", "ux", "accessibility", "i18n", "performance", "needs-design", "api"],
    "MOB": [
        "mobile",
        "ios",
        "android",
        "ux",
        "performance",
        "accessibility",
        "needs-design",
    ],
}
COMMENTS = [
    "Looking into this now.",
    "Can we get a repro from support?",
    "Blocked on the vendor reply.",
    "Fix is in review, should ship Thursday.",
    "Seen again in last night's run.",
    "Moving to next cycle, capacity is tight.",
]


def fmt_key(project: str, num: int) -> str:
    return f"{project}-{num:03d}" if project == "PAY" else f"{project}-{num}"


def nonce_rng(seed: int, nonce: str) -> random.Random:
    return random.Random(hashlib.sha256(f"{seed}:{nonce}".encode()).digest())


def build(seed: int, nonce: str) -> tuple[dict, dict]:
    """Return (state, planted): fresh state dict and the nonce-planted expected facts."""
    state = copy.deepcopy(_base(seed))
    planted = _plant(state, nonce_rng(seed, nonce))
    _finish(state)
    return state, planted


@functools.lru_cache(maxsize=4)
def _base(seed: int) -> dict:
    rng = random.Random(seed)
    users = _users(rng)
    labels = [{"id": f"L{i + 1:02d}", "name": n} for i, n in enumerate(LABELS)]
    lid = {l["name"]: l["id"] for l in labels}
    cycles = [{"id": f"C{i + 1}", "name": n} for i, n in enumerate(CYCLES)]
    # "team" is the owning team (OPS is staffed by Platform and Growth but owned by Platform)
    projects = [
        {"id": k, "key": k, "name": n, "team": t[0]} for k, n, t, _, _ in PROJECTS
    ]

    # created_seq interleaves projects; local jitter keeps seq order != key order.
    order = []
    for key, _, _, first, count in PROJECTS:
        for i in range(count):
            order.append(
                (max(0.0, (i + rng.uniform(-2.5, 2.5)) / count), key, first + i)
            )
    order.sort()
    issues, titles, codes = [], set(), set()
    for seq, (_, proj, num) in enumerate(order, start=1):
        team_users = [
            u
            for u in users
            if u["team"] in dict((k, t) for k, _, t, _, _ in PROJECTS)[proj]
        ]
        itype = rng.choices(TYPES, [40, 25, 35])[0]
        title = _title(rng, proj, itype, titles)
        status = rng.choices(STATUSES, [20, 20, 18, 10, 22, 10])[0]
        assignee = (
            rng.choice([u["id"] for u in team_users if u["role"] != "pm"])
            if (
                status in ("in_progress", "in_review", "done", "closed")
                or rng.random() < 0.65
            )
            else None
        )
        pool = LABEL_POOL[proj]
        names = rng.sample(pool, rng.randint(1, 2))
        if itype == "bug" and rng.random() < 0.7:
            names.append("bug")
        if itype == "feature" and rng.random() < 0.5:
            names.append("feature")
        code = f"AC-{rng.randrange(10000, 99999)}"
        while code in codes:
            code = f"AC-{rng.randrange(10000, 99999)}"
        codes.add(code)
        issue = {
            "key": fmt_key(proj, num),
            "project": proj,
            "title": title,
            "description": _desc(rng, title),
            "type": itype,
            "status": status,
            "priority": rng.choices(PRIORITIES, [10, 20, 35, 25, 10])[0],
            "assignee_id": assignee,
            "reporter_id": rng.choice(team_users)["id"],
            "label_ids": [lid[n] for n in names],
            "cycle_id": rng.choice(["C1", "C2", "C3", "C4", None, None]),
            "created_seq": seq,
            "due_date": (
                date(2026, 10, 1) + timedelta(days=rng.randrange(0, 80))
            ).isoformat()
            if rng.random() < 0.4
            else None,
            "custom_fields": {"customer_impact": rng.choice(IMPACTS)}
            if rng.random() < 0.3
            else {},
            "resolution": None,
            "duplicate_of": None,
            "audit_code": code,
            "customer_ticket": f"CS-{rng.randrange(1000, 9999)}"
            if rng.random() < 0.2
            else None,
        }
        issues.append(issue)
    by_key = {i["key"]: i for i in issues}
    for i in issues:
        if i["status"] == "done":
            i["resolution"] = "fixed"
        elif i["status"] == "closed":
            i["resolution"] = rng.choice(RESOLUTIONS)
            if i["resolution"] == "duplicate":
                others = [
                    o["key"]
                    for o in issues
                    if o["project"] == i["project"] and o["key"] != i["key"]
                ]
                i["duplicate_of"] = rng.choice(others)
    _static_specials(by_key, users)
    # Labelled-by-hand data never carries the planted labels.
    banned = {lid["regression"], lid["regressions-old"], lid["flaky"]}
    for i in issues:
        assert not banned & set(i["label_ids"])

    links, seen = [], set()
    keys = [i["key"] for i in issues if i["key"] not in SPECIAL]
    while len(links) < 40:
        a, b = rng.sample(keys, 2)
        if (a, b) in seen or (b, a) in seen:
            continue
        seen.add((a, b))
        links.append(
            {
                "id": f"LK{len(links) + 1:03d}",
                "from_key": a,
                "type": rng.choice(["blocks", "relates"]),
                "to_key": b,
            }
        )
    comments = []
    for k in rng.sample(keys, 25):
        author = rng.choice(users)["handle"]
        comments.append(
            {
                "id": f"CM{len(comments) + 1:03d}",
                "issue_key": k,
                "author": author,
                "body": rng.choice(COMMENTS),
            }
        )
    return {
        "users": users,
        "projects": projects,
        "labels": labels,
        "cycles": cycles,
        "issues": issues,
        "comments": comments,
        "links": links,
    }


def _users(rng: random.Random) -> list[dict]:
    firsts, lasts = FIRST[:], LAST[:]
    rng.shuffle(firsts)
    rng.shuffle(lasts)
    names = [
        f"{f} {l}" for f, l in zip(firsts, lasts, strict=True)
    ]  # 40 unique, none contain alex/priya/chen
    users = []
    for t, team in enumerate(TEAMS):
        for j in range(8):
            if j == 2 and team in ("Platform", "Growth"):
                name, handle = (
                    "Alex Chen",
                    "alex.chen" if team == "Platform" else "a.chen",
                )
            elif j == 2 and team == "Payments":
                name, handle = "Priya Nair", "priya.nair"
            elif (team, j) in SAMS:
                name, handle = SAMS[(team, j)]
            else:
                name = names[t * 8 + j]
                handle = name.lower().replace(" ", ".")
            role = "lead" if j == 0 else "pm" if j == 1 else "engineer"
            users.append(
                {
                    "id": f"U{t * 8 + j + 1:02d}",
                    "name": name,
                    "handle": handle,
                    "team": team,
                    "role": role,
                    "active": (team, j) not in INACTIVE,
                    "on_call": j == 5,
                }
            )
    return users


def _title(rng, proj, itype, used):
    for _ in range(50):
        t = rng.choice(TITLES[itype]).format(
            s=rng.choice(SUBJECTS[proj]), c=rng.choice(CONTEXTS[proj])
        )
        t = t[0].upper() + t[1:]
        if t not in used:
            used.add(t)
            return t
    t = f"{t} (#{len(used)})"
    used.add(t)
    return t


def _desc(rng, title):
    return f"{title}. Raised by {rng.choice(WHO)}; {rng.choice(IMPACT)}."[:160]


def _static_specials(by_key, users):
    pay = [
        u["id"] for u in users if u["team"] == "Payments" and u["role"] == "engineer"
    ]
    inf = by_key["INF-33"]
    inf.update(
        title="Rotate staging TLS certificates before expiry",
        type="task",
        status="todo",
        priority="medium",
        assignee_id=None,
        resolution=None,
        duplicate_of=None,
        description="Staging wildcard cert expires in three weeks. Rotate via Vault and verify ingress.",
    )
    a, b = by_key["PAY-149"], by_key["PAY-150"]
    a.update(
        title="Card declines spike on 3DS retry",
        type="bug",
        status="todo",
        priority="high",
        assignee_id=pay[0],
        resolution=None,
        duplicate_of=None,
        description="Issuers decline the second 3DS attempt for the same card within five minutes.",
    )
    b.update(
        title="3DS retry causes spurious card declines",
        type="bug",
        status="in_progress",
        priority="high",
        assignee_id=pay[1],
        resolution=None,
        duplicate_of=None,
        description="Customers retrying 3DS see declines that clear on the third attempt.",
    )
    by_key["PAY-087"].update(
        title="Payout reconciliation drifts by one cent on JPY", type="bug"
    )
    by_key["PAY-201"].update(
        title="Enterprise refund export is missing tax columns",
        type="bug",
        custom_fields={"customer_impact": "enterprise"},
    )
    by_key["PAY-142"].update(
        title="Duplicate settlement lines after partial refund retry",
        type="bug",
        resolution=None,
        duplicate_of=None,
    )
    for k in ("PAY-087", "PAY-201", "PAY-142"):
        by_key[k]["reporter_id"] = pay[2]
    # Specials stay out of closed-state accidents.
    for k in ("PAY-087", "PAY-201"):
        by_key[k]["status"] = (
            "todo" if by_key[k]["status"] in ("done", "closed") else by_key[k]["status"]
        )
        by_key[k]["resolution"] = None
        by_key[k]["duplicate_of"] = None


def _set_status(i, status, rng):
    i["status"] = status
    i["duplicate_of"] = None
    if status in OPEN:
        i["resolution"] = None
    elif status == "done":
        i["resolution"] = "fixed"
    else:
        i["resolution"] = rng.choice(["fixed", "wontfix", "cannot_reproduce"])


def _plant(state: dict, rng: random.Random) -> dict:
    issues = state["issues"]
    by_key = {i["key"]: i for i in issues}
    users = state["users"]
    by_user = {u["id"]: u for u in users}
    lid = {l["name"]: l["id"] for l in state["labels"]}
    cid = {c["name"]: c["id"] for c in state["cycles"]}
    protected = set(SPECIAL) | set(SPECIAL_V2)
    planted: dict = {}

    def pool(pred=lambda i: True):
        return sorted(i["key"] for i in issues if i["key"] not in protected and pred(i))

    def take(keys, n):
        picked = rng.sample(keys, n)
        protected.update(picked)
        return [by_key[k] for k in picked]

    def label(i, *names):
        for n in names:
            if lid[n] not in i["label_ids"]:
                i["label_ids"].append(lid[n])

    def is_open(i):
        return i["status"] in OPEN

    def setup(
        i,
        *,
        type=None,
        status=None,
        assignee=_KEEP,
        cycle=_KEEP,
        priority=None,
        names=(),
        ticket=_KEEP,
        reporter=_KEEP,
    ):
        if type:
            i["type"] = type
        if status:
            _set_status(i, status, rng)
        if assignee is not _KEEP:
            i["assignee_id"] = assignee
        if cycle is not _KEEP:
            i["cycle_id"] = cycle
        if priority:
            i["priority"] = priority
        if ticket is not _KEEP:
            i["customer_ticket"] = ticket
        if reporter is not _KEEP:
            i["reporter_id"] = reporter
        label(i, *names)
        return i

    def proj(p):
        return lambda i: i["project"] == p

    def uids(teams, roles=("engineer", "lead"), active=True):
        return [
            u["id"]
            for u in users
            if u["team"] in teams and u["role"] in roles and (u["active"] or not active)
        ]

    open_status = lambda: rng.choice(["backlog", "todo", "todo", "in_review"])
    W2, W3 = cid["Oct-W2"], cid["Oct-W3"]
    off_cycle = lambda: rng.choice([cid["Oct-W1"], cid["Oct-W4"], None])
    in_w23 = lambda: rng.choice([W2, W3])
    plat = [u for u in users if u["team"] == "Platform"]
    plat_eng_all = [u["id"] for u in plat if u["role"] == "engineer"]
    plat_eng = [u["id"] for u in plat if u["role"] == "engineer" and u["active"]]
    lead = next(u["id"] for u in plat if u["role"] == "lead")
    pay_users = [u["id"] for u in users if u["team"] == "Payments"]
    pay_eng = [
        u["id"] for u in users if u["team"] == "Payments" and u["role"] == "engineer"
    ]

    # on-call: one active user per team; Platform's rotates with the nonce
    for u in plat:
        u["on_call"] = False
    oncall = rng.choice(
        [
            u
            for u in plat
            if u["role"] == "engineer" and u["active"] and u["name"] != "Alex Chen"
        ]
    )
    oncall["on_call"] = True

    # t01 / t03 / t12 on named issues
    p142 = by_key["PAY-142"]
    p142["status"] = rng.choice(["backlog", "todo", "in_progress"])
    p142["priority"] = rng.choice(["medium", "high"])
    p142["assignee_id"] = rng.choice(pay_eng)
    used = {i["audit_code"] for i in issues}
    code = f"AC-{rng.randrange(10000, 99999)}"
    while code in used:
        code = f"AC-{rng.randrange(10000, 99999)}"
    p142["audit_code"] = code
    by_key["PAY-087"]["reporter_id"] = rng.choice(pay_users)
    by_key["PAY-201"]["customer_ticket"] = (
        f"CS-{rng.randrange(1000, 9999)}" if rng.random() < 0.5 else None
    )

    # h07 / h10 named issues
    setup(by_key["WEB-12"], status="todo", assignee=None, type="task")
    setup(by_key["PAY-150"], assignee=None)
    setup(by_key["PAY-151"], status=rng.choice(["backlog", "todo"]), assignee=None)

    # t06: nonce decides how many of the oldest OPS issues are already closed
    ops = sorted(
        (i for i in issues if i["project"] == "OPS"), key=lambda i: i["created_seq"]
    )[:8]
    k = rng.randint(2, 6)
    for n, i in enumerate(ops):
        protected.add(i["key"])
        if n < k:
            _set_status(i, rng.choice(["done", "closed"]), rng)
        elif n == k:
            _set_status(i, rng.choice(["backlog", "todo", "in_progress"]), rng)
    planted["t06_oldest_open_ops_key"] = ops[k]["key"]

    # t05 (+ h01's PAY half): open+unassigned+bug+regression in PAY, with decoys
    n5 = rng.randint(4, 9)
    p1 = rng.randint(
        2, 3
    )  # how many of the t05 keys sit in Oct-W2/W3 (the h01 PAY targets)
    pay_pool = pool(proj("PAY"))
    chosen = take(pay_pool, n5 + 2 + 2 + 2 + 1)
    targets, rest = chosen[:n5], chosen[n5:]
    for n, i in enumerate(targets):
        i.update(type="bug", assignee_id=None)
        _set_status(i, rng.choice(["backlog", "todo", "todo"]), rng)
        i["cycle_id"] = in_w23() if n < p1 else off_cycle()
        label(i, "regression", "bug")
    old, assigned, closed, feat = rest[0:2], rest[2:4], rest[4:6], rest[6]
    for i in old:
        i.update(type="bug", assignee_id=None, cycle_id=in_w23())
        _set_status(i, "todo", rng)
        label(i, "regressions-old", "bug")
    for i in assigned:
        i.update(type="bug", assignee_id=rng.choice(pay_eng), cycle_id=in_w23())
        _set_status(i, "in_progress", rng)
        label(i, "regression", "bug")
    for i in closed:
        i.update(type="bug", assignee_id=None, cycle_id=in_w23())
        _set_status(i, rng.choice(["done", "closed"]), rng)
        label(i, "regression", "bug")
    feat.update(type="feature", assignee_id=None, cycle_id=in_w23())
    _set_status(feat, "backlog", rng)
    label(feat, "regression")
    inf = take(pool(proj("INF")), 1)[0]
    inf.update(type="bug", assignee_id=None, cycle_id=in_w23())
    _set_status(inf, "todo", rng)
    label(inf, "regression", "bug")
    planted["t05_keys"] = sorted(i["key"] for i in targets)
    h01_keys = [i["key"] for i in targets[:p1]]

    # t10: exactly 7 open flaky issues in Oct-W2 (never OPS: that is h03's territory), with decoys
    not_ops = lambda i: i["project"] != "OPS"
    flaky = take(pool(not_ops), 7)
    for i in flaky:
        _set_status(i, rng.choice(["todo", "in_progress", "in_review", "backlog"]), rng)
        i["cycle_id"] = cid["Oct-W2"]
        label(i, "flaky")
    decoys = take(pool(not_ops), 3 + 3 + 3 + 1)
    for n, i in enumerate(decoys):
        label(i, "flaky")
        if n < 3:
            _set_status(i, rng.choice(["done", "closed"]), rng)
            i["cycle_id"] = cid["Oct-W2"]
        elif n < 6:
            _set_status(i, "todo", rng)
            i["cycle_id"] = cid["Oct-W1"]
        elif n < 9:
            _set_status(i, "in_progress", rng)
            i["cycle_id"] = cid["Oct-W3"]
        else:
            _set_status(i, "todo", rng)
            i["cycle_id"] = None
    planted["t10_keys"] = sorted(i["key"] for i in flaky)

    # t11: one active Platform engineer with strictly the most open urgent issues
    for i in issues:
        if (
            i["assignee_id"] in plat_eng_all
            and is_open(i)
            and i["priority"] == "urgent"
        ):
            i["priority"] = "high"
    winner = rng.choice(plat_eng)
    others = [e for e in plat_eng_all if e != winner]
    runner = rng.choice([e for e in others if by_user[e]["active"]])
    m = rng.randint(3, 4)
    counts = {winner: m, runner: m - 1}
    for e in others:
        counts.setdefault(e, rng.randint(0, 1))
    plat_proj = lambda i: i["project"] in ("INF", "OPS")
    open_pool = pool(lambda i: plat_proj(i) and is_open(i))
    rng.shuffle(open_pool)
    mine = {}
    t11_keys = set()
    for e in plat_eng_all:
        mine[e] = [by_key[k] for k in open_pool[: counts[e]]]
        open_pool = open_pool[counts[e] :]
        for i in mine[e]:
            i.update(assignee_id=e, priority="urgent")
            protected.add(i["key"])
            t11_keys.add(i["key"])
    for i in (by_key[k] for k in open_pool[:2]):
        i.update(assignee_id=lead, priority="urgent")
        protected.add(i["key"])
    oldest = min(mine[winner], key=lambda i: i["created_seq"])
    closed_pool = sorted(
        pool(lambda i: plat_proj(i) and not is_open(i)),
        key=lambda i: by_key[i]["created_seq"],
    )
    for e, n in ((winner, 2), (runner, 3)):
        for i in (by_key[k] for k in closed_pool[:n]):
            i.update(assignee_id=e, priority="urgent")
            protected.add(i["key"])
        closed_pool = closed_pool[n:]
    planted["t11_engineer_id"] = winner
    planted["t11_issue_key"] = oldest["key"]
    planted["t11_lead_id"] = lead

    # ---- v2 plants ----
    # h01: open unassigned regression bugs in PAY/WEB, Oct-W2/W3, not wontfix-candidate
    n1 = rng.randint(10, 16)
    w = n1 - p1
    web = take(pool(proj("WEB")), w + 2 + 2 + 2 + 2 + 3 + 3)
    web_t, web_rest = web[:w], web[w:]
    for i in web_t:
        setup(
            i,
            type="bug",
            assignee=None,
            status=open_status(),
            cycle=in_w23(),
            names=("regression", "bug"),
        )
        h01_keys.append(i["key"])
    web_users = uids(("Web",), ("engineer", "lead"))
    for i in web_rest[0:2]:  # status decoys
        setup(
            i,
            type="bug",
            assignee=None,
            status=rng.choice(["done", "closed"]),
            cycle=in_w23(),
            names=("regression", "bug"),
        )
    for i in web_rest[2:4]:  # assignee decoys
        setup(
            i,
            type="bug",
            assignee=rng.choice(web_users),
            status="in_progress",
            cycle=in_w23(),
            priority="medium",
            names=("regression", "bug"),
        )
    for i in web_rest[4:6]:  # type decoys
        setup(
            i,
            type=rng.choice(["feature", "task"]),
            assignee=None,
            status=open_status(),
            cycle=in_w23(),
            names=("regression",),
        )
    for i in web_rest[6:8]:  # label decoys
        setup(
            i,
            type="bug",
            assignee=None,
            status=open_status(),
            cycle=in_w23(),
            names=("regressions-old", "bug"),
        )
    for i in web_rest[8:11]:  # cycle decoys
        setup(
            i,
            type="bug",
            assignee=None,
            status=open_status(),
            cycle=off_cycle(),
            names=("regression", "bug"),
        )
    for i in web_rest[11:14]:  # wontfix-candidate decoys
        setup(
            i,
            type="bug",
            assignee=None,
            status=open_status(),
            cycle=in_w23(),
            names=("regression", "bug", "wontfix-candidate"),
        )
    for p_ in ("GRO", "MOB"):  # project decoys (INF one comes from t05)
        i = take(pool(proj(p_)), 1)[0]
        setup(
            i,
            type="bug",
            assignee=None,
            status=open_status(),
            cycle=in_w23(),
            names=("regression", "bug"),
        )
    planted["h01_keys"] = sorted(h01_keys)

    # h03: open OPS flaky issues, unassigned vs assigned (never Oct-W2: t10 counts those)
    n3 = rng.randint(9, 13)
    nu = rng.randint(3, n3 - 3)
    ops_users = [
        u["id"]
        for u in users
        if u["team"] in ("Platform", "Growth") and u["role"] != "pm" and u["active"]
    ]
    ops_pick = take(pool(proj("OPS")), n3 + 3)
    un, asg = [], []
    for n, i in enumerate(ops_pick[:n3]):
        setup(
            i,
            status=rng.choice(["backlog", "todo", "in_progress", "in_review"]),
            cycle=rng.choice([cid["Oct-W1"], cid["Oct-W3"], cid["Oct-W4"], None]),
            assignee=None if n < nu else rng.choice(ops_users),
            names=("flaky",),
        )
        (un if n < nu else asg).append(i["key"])
    for n, i in enumerate(ops_pick[n3:]):  # closed flaky OPS decoys
        setup(
            i,
            status=rng.choice(["done", "closed"]),
            assignee=None if n == 0 else rng.choice(ops_users),
            names=("flaky",),
        )
    for p_ in ("INF", "GRO"):  # open flaky outside OPS, unassigned
        i = take(pool(proj(p_)), 1)[0]
        setup(i, status="todo", assignee=None, cycle=cid["Oct-W1"], names=("flaky",))
    planted["h03_unassigned_keys"], planted["h03_assigned_keys"] = (
        sorted(un),
        sorted(asg),
    )
    planted["h03_oncall_id"] = oncall["id"]

    # h04: urgent open issues filed by PAY-142's reporter in Oct-W3 (reporter = a non-lead, any team)
    rep = rng.choice([u for u in users if u["active"] and u["role"] != "lead"])
    p142["reporter_id"] = rep["id"]
    rep_lead = next(
        u["id"] for u in users if u["team"] == rep["team"] and u["role"] == "lead"
    )
    h04_assignees = [
        u["id"]
        for u in users
        if u["active"]
        and u["role"] != "pm"
        and u["id"] != rep_lead
        and not (u["team"] == "Platform" and u["role"] == "engineer")
    ]
    pick_assignee = lambda: None if rng.random() < 0.25 else rng.choice(h04_assignees)
    n4 = rng.randint(4, 7)
    projs = rng.sample(["PAY", "INF", "OPS", "WEB", "MOB"], 2)
    projs += [rng.choice(["PAY", "INF", "OPS", "WEB", "MOB"]) for _ in range(n4 - 2)]
    h04 = []
    for p_ in projs:
        i = take(pool(proj(p_)), 1)[0]
        setup(
            i,
            status=open_status(),
            priority="urgent",
            cycle=W3,
            reporter=rep["id"],
            assignee=pick_assignee(),
        )
        h04.append(i["key"])
    for i in take(pool(), 3):  # cycle decoys
        setup(
            i,
            status=open_status(),
            priority="urgent",
            cycle=rng.choice([cid["Oct-W1"], W2, cid["Oct-W4"]]),
            reporter=rep["id"],
            assignee=pick_assignee(),
        )
    for i in take(pool(), 3):  # priority decoys
        setup(
            i,
            status=open_status(),
            priority=rng.choice(["high", "medium"]),
            cycle=W3,
            reporter=rep["id"],
            assignee=pick_assignee(),
        )
    for i in take(pool(), 3):  # status decoys
        setup(
            i,
            status=rng.choice(["done", "closed"]),
            priority="urgent",
            cycle=W3,
            reporter=rep["id"],
            assignee=pick_assignee(),
        )
    mates = [
        u["id"] for u in users if u["team"] == rep["team"] and u["id"] != rep["id"]
    ]
    for i in take(pool(), 3):  # other-reporter decoys (teammates of the reporter)
        setup(
            i,
            status=open_status(),
            priority="urgent",
            cycle=W3,
            reporter=rng.choice(mates),
            assignee=pick_assignee(),
        )
    planted["h04_reporter_id"], planted["h04_lead_id"] = rep["id"], rep_lead
    planted["h04_keys"] = sorted(h04)

    # h06: GRO duplicate-candidates that name their original in the description
    n6 = rng.randint(4, 6)
    gro = take(pool(proj("GRO")), n6 + (n6 - 1) + 2 + 2)
    dups, origs, closed_d, unlabeled = (
        gro[:n6],
        gro[n6 : 2 * n6 - 1],
        gro[2 * n6 - 1 : 2 * n6 + 1],
        gro[2 * n6 + 1 : 2 * n6 + 3],
    )
    wrong_proj = take(pool(proj("WEB")), 1)[0]
    pay_orig = take(pool(lambda i: proj("PAY")(i) and is_open(i)), 1)[0]
    orig_keys = [pay_orig["key"]] + [i["key"] for i in origs]
    for i in origs:
        if not is_open(i):
            _set_status(i, "todo", rng)
    texts = [
        "{t}. Duplicate of {o}; same symptom was already reported.",
        "Duplicate of {o}. {t}. Raised by support.",
        "{t}. This is a Duplicate of {o} (same repro steps).",
    ]
    pairs = {}
    for n, i in enumerate(dups):
        o = orig_keys[n]
        _set_status(i, open_status(), rng)
        i["priority"] = rng.choice(["none", "low", "medium"])
        i["description"] = rng.choice(texts).format(
            t=i["title"][:70].rstrip(". "), o=o
        )[:160]
        if n % 2 == 1:
            i["description"] = (
                i["description"] + f" See also {rng.choice(orig_keys)}."
            )[:160]
        label(i, "duplicate-candidate")
        pairs[i["key"]] = o
    for i in closed_d:
        _set_status(i, rng.choice(["done", "closed"]), rng)
        i["description"] = (
            f"{i['title'][:70].rstrip('. ')}. Duplicate of {rng.choice(orig_keys)}; already handled."[
                :160
            ]
        )
        label(i, "duplicate-candidate")
    for i in unlabeled:
        _set_status(i, open_status(), rng)
        i["description"] = (
            f"{i['title'][:70].rstrip('. ')}. Similar to {rng.choice(orig_keys)}, but a different flow."[
                :160
            ]
        )
    _set_status(wrong_proj, open_status(), rng)
    wrong_proj["description"] = (
        f"{wrong_proj['title'][:70].rstrip('. ')}. Duplicate of {rng.choice(orig_keys)}."[
            :160
        ]
    )
    label(wrong_proj, "duplicate-candidate")
    planted["h06_pairs"] = dict(sorted(pairs.items()))

    # h08: open MOB bugs with a customer ticket, not urgent (none already high)
    n8 = rng.randint(5, 9)
    mob = take(pool(proj("MOB")), n8 + 3 + 3 + 3)
    tick = lambda: f"CS-{rng.randrange(1000, 9999)}"
    mob_t = mob[:n8]
    for i in mob_t:
        setup(
            i,
            type="bug",
            status=open_status(),
            priority=rng.choice(["none", "low", "medium"]),
            ticket=tick(),
        )
    for i in mob[n8 : n8 + 3]:
        setup(i, type="bug", status=open_status(), priority="urgent", ticket=tick())
    for i in mob[n8 + 3 : n8 + 6]:
        setup(
            i,
            type=rng.choice(["feature", "task"]),
            status=open_status(),
            priority=rng.choice(["low", "medium", "high"]),
            ticket=tick(),
        )
    for i in mob[n8 + 6 :]:
        setup(i, type="bug", status=rng.choice(["done", "closed"]), ticket=tick())
    mob_keys = {i["key"] for i in mob}
    for i in issues:  # no other open MOB bug carries a ticket
        if (
            i["project"] == "MOB"
            and i["key"] not in mob_keys
            and is_open(i)
            and i["type"] == "bug"
        ):
            i["customer_ticket"] = None
    planted["h08_keys"] = sorted(i["key"] for i in mob_t)

    # h09: open PAY regression bugs assigned to the Platform team
    n9 = rng.randint(3, 5)
    p9 = take(pool(proj("PAY")), n9 + 2 + 2 + 1 + 1)
    plat_users = [u["id"] for u in plat if u["role"] != "pm"]
    for i in p9[:n9]:
        setup(
            i,
            type="bug",
            status=open_status(),
            assignee=rng.choice(plat_users),
            names=("regression", "bug"),
        )
    for i in p9[n9 : n9 + 2]:  # closed
        setup(
            i,
            type="bug",
            status=rng.choice(["done", "closed"]),
            assignee=rng.choice(plat_users),
            names=("regression", "bug"),
        )
    for i in p9[n9 + 2 : n9 + 4]:  # no regression label
        setup(
            i,
            type="bug",
            status=open_status(),
            assignee=rng.choice(plat_users),
            names=("bug",),
        )
    setup(
        p9[n9 + 4],
        type="feature",
        status=open_status(),
        assignee=rng.choice(plat_users),
        names=("regression",),
    )
    setup(
        p9[n9 + 5],
        type="bug",
        status=open_status(),
        assignee=rng.choice(pay_eng),
        names=("regression", "bug"),
    )
    planted["h09_sources"] = sorted(i["key"] for i in p9[:n9])

    # sweep: Platform engineers' urgent/in_progress counts are fully owned by t11 and h05; the Platform lead/PM
    # in_progress counts are owned by the h05 plant below (re-swept after h02, which may assign to the lead)
    plat_other = [u["id"] for u in plat if u["role"] != "engineer"]
    for i in issues:
        if i["assignee_id"] in plat_eng_all and is_open(i):
            if i["priority"] == "urgent" and i["key"] not in t11_keys:
                i["priority"] = "high"
            if i["status"] == "in_progress":
                i["status"] = "todo"

    # h05: Platform active engineers' open in_progress counts. Exactly 2 donors (>3, engineers only); the 3
    # recipient engineers have a UNIQUE minimum (rc[0] in 0..1, the others strictly greater, <=3), and the donors'
    # total excess is <= total recipient spare capacity - 1, so a fewest-first greedy always fits.
    order = plat_eng[:]
    rng.shuffle(order)
    donors, recv = order[:2], order[2:]
    while True:
        rc = [rng.randint(0, 1)]
        for _ in recv[1:]:
            rc.append(rng.randint(rc[0] + 1, 3))
        spare = sum(3 - c for c in rc)
        if spare >= 4:  # two donors, each over by >= 1, plus slack 1
            break
    e0 = rng.randint(1, min(3, spare - 2))
    e1 = rng.randint(1, min(3, spare - 1 - e0))
    want = {donors[0]: 3 + e0, donors[1]: 3 + e1}
    want.update(dict(zip(recv, rc, strict=True)))
    ip_pool = pool(lambda i: i["project"] in ("INF", "OPS"))
    for e in order:
        for i in take(ip_pool, want[e]):
            setup(
                i,
                status="in_progress",
                assignee=e,
                priority="high" if i["priority"] == "urgent" else None,
            )
            ip_pool.remove(i["key"])
    planted["h05_initial_counts"] = {e: want[e] for e in sorted(want)}

    for i in issues:  # nothing else may accidentally match h04
        if i["key"] not in h04 and (
            i["reporter_id"] == rep["id"]
            and is_open(i)
            and i["priority"] == "urgent"
            and i["cycle_id"] == W3
        ):
            if i["key"] in SPECIAL:
                i["priority"] = "high"
            else:
                i["reporter_id"] = rng.choice(
                    [
                        u["id"]
                        for u in users
                        if u["team"] == rep["team"] and u["id"] != rep["id"]
                    ]
                )

    # h02: team with the most open urgent issues assigned to its members (all projects)
    def team_counts():
        c = dict.fromkeys(TEAMS, 0)
        for i in issues:
            if is_open(i) and i["priority"] == "urgent" and i["assignee_id"]:
                c[by_user[i["assignee_id"]]["team"]] += 1
        return c

    for i in issues:
        if (
            i["key"] not in protected
            and is_open(i)
            and i["priority"] == "urgent"
            and i["assignee_id"]
        ):
            i["priority"] = "high"
    fc = team_counts()
    T = rng.choice(TEAMS)
    oth = [t for t in TEAMS if t != T]
    M = max(fc[o] for o in oth)
    tc = max(fc[T], M + 1, 8 + rng.randint(0, 3))
    rm = max(oth, key=lambda o: (fc[o], o))
    if fc[rm] >= tc - 2:
        run, r = rm, rng.randint(fc[rm], tc - 1)
    else:
        run, r = rng.choice(oth), rng.randint(tc - 2, tc - 1)
    goal = {T: tc, run: r}
    for o in oth:
        if o != run:
            goal[o] = max(fc[o], min(r, rng.randint(2, 6)))
    for team in TEAMS:
        need = goal[team] - fc[team]
        if need <= 0:
            continue
        owners = uids(
            (team,), ("lead",) if team == "Platform" else ("engineer", "lead")
        )
        for i in take(
            pool(
                lambda i: (
                    is_open(i)
                    and i["assignee_id"]
                    and i["priority"] != "urgent"
                    and not (i["reporter_id"] == rep["id"] and i["cycle_id"] == W3)
                )
            ),
            need,
        ):
            i["priority"] = "urgent"
            i["assignee_id"] = rng.choice(owners)
    planted["h02_team"], planted["h02_count"] = T, tc

    # h05 (continued): Platform lead/PM are "on Platform" too but never over: lead 1-2, PM 0-2 open in_progress.
    # Re-sweep first, since h02 may have given the lead in_progress issues.
    for i in issues:
        if i["assignee_id"] in plat_other and i["status"] == "in_progress":
            i["status"] = "todo"
    lead_pm = {}
    for u in plat_other:
        n_u = rng.randint(1, 2) if u == lead else rng.randint(0, 2)
        ip2 = pool(
            lambda i: i["project"] in ("INF", "OPS") and i["status"] != "in_progress"
        )
        for i in take(ip2, n_u):
            setup(
                i,
                status="in_progress",
                assignee=u,
                priority="high" if i["priority"] == "urgent" else None,
            )
        lead_pm[u] = n_u
    planted["h05_other_counts"] = dict(sorted(lead_pm.items()))

    # noise labels: no issue below is part of any planted set
    for i in take(pool(), 14):
        label(i, "sla-breach" if rng.random() < 0.6 else "wontfix-candidate")
    return planted


def _finish(state: dict) -> None:
    # Duplicate resolutions get their `duplicates` link, derived from final data.
    links = state["links"]
    have = {(l["from_key"], l["type"], l["to_key"]) for l in links}
    for i in sorted(state["issues"], key=lambda i: i["created_seq"]):
        if i["resolution"] == "duplicate" and i["duplicate_of"]:
            t = (i["key"], "duplicates", i["duplicate_of"])
            if t not in have:
                have.add(t)
                links.append(
                    {
                        "id": f"LK{len(links) + 1:03d}",
                        "from_key": t[0],
                        "type": t[1],
                        "to_key": t[2],
                    }
                )
