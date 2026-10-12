"""Shared grading helpers for the model-tiers tasks.

Checks depend only on store state (initial vs final snapshots), facts, and the final answer text,
never on tool names, so they hold for every server variant.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from _lib.grade import parse_status_line

KEY_RE = re.compile(r"\b[A-Z]+-\d+\b")
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}


# ---- state diffs ----
class Diff:
    """Differences between two snapshots (initial -> final)."""

    def __init__(self, initial, final):
        a, b = initial.snapshot(), final.snapshot()
        self.a, self.b = a, b
        ia = {i["key"]: i for i in a["issues"]}
        self.new_issues = [i for i in b["issues"] if i["key"] not in ia]
        self.changed: dict[str, dict[str, Any]] = {}  # key -> {field: new value}
        for i in b["issues"]:
            old = ia.get(i["key"])
            if old is None:
                continue
            fields = {f: v for f, v in i.items() if old.get(f) != v}
            if fields:
                self.changed[i["key"]] = fields
        old_c = {c["id"] for c in a["comments"]}
        self.new_comments = [c for c in b["comments"] if c["id"] not in old_c]
        old_l = {l["id"] for l in a["links"]}
        self.new_links = [l for l in b["links"] if l["id"] not in old_l]
        # anything outside issues/comments/links (users, labels, cycles, projects, deletions)
        self.other_changed = any(a[k] != b[k] for k in ("users", "projects", "labels", "cycles")) \
            or len(b["issues"]) < len(a["issues"]) or len(b["comments"]) < len(a["comments"]) \
            or len(b["links"]) < len(a["links"])

    @property
    def nothing(self) -> bool:
        return not (self.changed or self.new_issues or self.new_comments or self.new_links or self.other_changed)

    def final_issue(self, key: str) -> dict | None:
        return next((i for i in self.b["issues"] if i["key"] == key), None)


def diff(ctx) -> Diff:
    return Diff(ctx.initial, ctx.final)


def clean(d: Diff, allowed: dict[str, Iterable[str]] | None = None, *, comments: int = 0,
          issues: int = 0, links: int = 0) -> bool:
    """No side effects beyond: issue key -> allowed changed fields, plus up to N new comments/issues/links."""
    allowed = {k: set(v) for k, v in (allowed or {}).items()}
    if d.other_changed:
        return False
    for key, fields in d.changed.items():
        if key not in allowed or not set(fields) <= allowed[key]:
            return False
    return (len(d.new_comments) <= comments and len(d.new_issues) <= issues
            and len(d.new_links) <= links)


def dup_links_only(d: Diff, pairs: dict[str, str]) -> bool:
    """Every new link is a `duplicates` link between a dup and its original (either direction).

    Trackers like Jira add this link when an issue is closed as a duplicate, so a model that
    records it explicitly has not made an unrequested change."""
    ok = {frozenset((dup, orig)) for dup, orig in pairs.items()}
    return all(l["type"] == "duplicates" and frozenset((l["from_key"], l["to_key"])) in ok
               for l in d.new_links)


# ---- answer parsing ----
def status_info(answer: str | None) -> tuple[str | None, str]:
    return parse_status_line(answer)


def is_done(answer: str | None) -> bool:
    return status_info(answer)[0] == "DONE"


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (text or "").lower())


def says_status(answer: str | None, status: str) -> bool:
    """Whether the answer states the given status, accepting in progress / in_progress / to-do variants."""
    t = _norm(answer or "")
    names = {"in_progress": ["in progress"], "in_review": ["in review"], "todo": ["todo", "to do"],
             "backlog": ["backlog"], "done": ["done"], "closed": ["closed"]}.get(status, [_norm(status)])
    return any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", t) for n in names)


def mentions(answer: str | None, token: str) -> bool:
    return bool(answer) and re.search(rf"(?<![A-Za-z0-9-]){re.escape(token)}(?![A-Za-z0-9])", answer) is not None


def keys_in(text: str | None) -> set[str]:
    return set(KEY_RE.findall((text or "").upper()))


def listed_keys(answer: str | None) -> set[str]:
    """Issue keys from the STATUS line; fall back to the whole answer if the line has none."""
    _s, rest = status_info(answer)
    found = keys_in(rest)
    return found or keys_in(answer)


def _ints(text: str) -> set[int]:
    text = KEY_RE.sub(" ", text.upper()).lower()
    out = {int(m) for m in re.findall(r"(?<![\w.-])\d+(?![\w.-]|\.\d)", text)}
    out |= {_WORDS[w] for w in re.findall(r"[a-z]+", text) if w in _WORDS}
    return out


def states_count(answer: str | None, n: int) -> bool:
    """The number n appears (digits or word) in the STATUS line, else anywhere in the answer."""
    _s, rest = status_info(answer)
    nums = _ints(rest)
    if not nums:
        nums = _ints(answer or "")
    return n in nums


# ---- lookups ----
def label_names(d: Diff, issue: dict) -> set[str]:
    by_id = {l["id"]: l["name"] for l in d.b["labels"]}
    return {by_id[i] for i in issue["label_ids"]}


def cycle_id(d: Diff, name: str) -> str:
    return next(c["id"] for c in d.b["cycles"] if c["name"] == name)


# ---- "bug" interpretations ----
# The fixture has both an issue `type` (bug|feature|task) and a label named `bug`; prompts say "bugs".
# Checks accept an answer/final state consistent with EITHER reading (never a mix).
INTERPRETATIONS = ("type", "label")


def bug_sets(initial, rest) -> dict[str, set[str]]:
    """Expected key set under each reading of "bug", from the initial snapshot.

    `rest(issue, label_names)` holds every other criterion. Returns {"type": keys, "label": keys}.
    """
    snap = initial.snapshot()
    by_id = {l["id"]: l["name"] for l in snap["labels"]}
    user = {u["id"]: u for u in snap["users"]}
    out: dict[str, set[str]] = {"type": set(), "label": set()}
    for i in snap["issues"]:
        names = {by_id[x] for x in i["label_ids"]}
        if not rest(i, names, user):
            continue
        if i["type"] == "bug":
            out["type"].add(i["key"])
        if "bug" in names:
            out["label"].add(i["key"])
    if not out["label"]:  # an empty label set would let a do-nothing run pass vacuously
        del out["label"]
    return out


def pick_interpretation(results: dict):
    """results: {interpretation: Check-like (success, subgoals)}; prefer a passing one, else the closest
    (most True subgoals, ties to the type reading)."""
    names = [n for n in INTERPRETATIONS if n in results]
    for name in names:
        if results[name][0]:
            return results[name]
    return max((results[n] for n in names), key=lambda r: sum(r[1].values()))


def in_cycles(snap_cycles, cid_, names) -> bool:
    return {c["id"]: c["name"] for c in snap_cycles}.get(cid_) in names


OPEN_STATUSES = ("backlog", "todo", "in_progress", "in_review")
