"""Hand-audit helper for the model-tiers benchmark.

    uv run python audit.py sample [--records PATH] [--n 30] [--seed 0] [--fail-weight 3] [--out PATH]
    uv run python audit.py show <execution_id>
    uv run python audit.py regrade [--records PATH]
    uv run python audit.py progress [--records PATH]

Traces are loaded through the SDK (sf-m3 0.2.38): `SQLiteExecutionStore(.m3/executions.sqlite)
.get_trace_view(execution_id)` returns the typed TraceView whose `.tool_calls` carry arguments,
status and result content; `_lib.telemetry` extracts from it exactly as `run_task` did at record
time (it only needs an object with `.trace_view`). Read-only use: nothing is written to .m3/.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from _lib import telemetry
from _lib.servers import load_task
from _lib.tasks import grade_trial

DEFAULT_RECORDS = ROOT / "results" / "_inbox" / "records.jsonl"
DEFAULT_DB = ROOT / ".m3" / "executions.sqlite"
DEFAULT_OUT = ROOT / "results" / "audit" / "sample.md"
REGRADE_CSV = ROOT / "results" / "audit" / "regrade.csv"

ARGS_MAX, RESULT_MAX, FINAL_MAX = 400, 300, 1200


# ---------------------------------------------------------------- loading


def load_records(
    path: Path | str = DEFAULT_RECORDS, *, include_base: bool = False
) -> list[dict]:
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # partially written trailing line during a live run
        if isinstance(rec, dict) and (include_base or rec.get("task") != "BASE"):
            out.append(rec)
    return out


class TraceSource:
    """Loads a stored execution as an object exposing `.trace_view` (what telemetry expects)."""

    def __init__(self, store: Any = None, db: Path | str = DEFAULT_DB):
        self._store, self._db = store, Path(db)
        self._cache: dict[str, Any] = {}

    @property
    def store(self) -> Any:
        if self._store is None:
            from m3.storage.sqlite import SQLiteExecutionStore

            self._store = SQLiteExecutionStore(self._db)
        return self._store

    def load(self, execution_id: str | None) -> Any | None:
        if not execution_id:
            return None
        if execution_id not in self._cache:
            try:
                view = self.store.get_trace_view(execution_id)
            except Exception:
                view = None
            self._cache[execution_id] = (
                SimpleNamespace(trace_view=view, turns=()) if view is not None else None
            )
        return self._cache[execution_id]


_TASK_DIRS: dict[str, Path] | None = None


def task_dirs() -> dict[str, Path]:
    global _TASK_DIRS
    if _TASK_DIRS is None:
        _TASK_DIRS = {}
        for y in sorted(ROOT.glob("*/task.yaml")):
            _TASK_DIRS[str(load_task(y.parent).get("id", y.parent.name))] = y.parent
    return _TASK_DIRS


# ---------------------------------------------------------------- trace extraction


def _result_text(entry: Any) -> str:
    value = telemetry._safe(lambda: entry.result.value)
    if value is None:
        return ""
    return "".join(
        getattr(b, "text", "") or "" for b in (getattr(value, "content", None) or ())
    )


def trace_calls(result: Any) -> list[dict]:
    """All tool calls in order: tool, args, is_error, result text, tracker/native flags."""
    rows = []
    for e in telemetry.tool_call_entries(result):
        args = telemetry._obs(telemetry._safe(lambda e=e: e.arguments))
        status = telemetry._enum(telemetry._safe(lambda e=e: e.tool_status))
        rows.append(
            {
                "tool": telemetry._obs(telemetry._safe(lambda e=e: e.tool)) or "?",
                "args": args if args is not None else {},
                "is_error": status != "success",
                "result": _result_text(e),
                "tracker": telemetry.is_tracker_wire(e),
                "native": telemetry.is_native(e),
            }
        )
    return rows


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 3] + "..."


def _compact(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True, default=str)


# ---------------------------------------------------------------- grading


def regrade_record(rec: dict, src: TraceSource) -> dict:
    """Re-grade one record. Returns {state, details, source, calls, rows, answer}.

    state is None if it cannot be graded (no trace and the record has no call args)."""
    tdir = task_dirs().get(str(rec.get("task")))
    result = src.load(rec.get("execution_id"))
    rows: list[dict] = []
    if result is not None:
        rows = trace_calls(result)
        calls = telemetry.wire_tracker_calls(result)
        answer = telemetry.final_text(result) or rec.get("final_text")
        native = sum(1 for r in rows if r["native"])
        source = "trace"
    else:
        rcalls = rec.get("calls") or []
        if rcalls and not all("args" in c or "arguments" in c for c in rcalls):
            return {
                "state": None,
                "details": {},
                "source": "none",
                "calls": [],
                "rows": [],
                "answer": rec.get("final_text"),
            }
        calls = [(c["tool"], c.get("args") or c.get("arguments") or {}) for c in rcalls]
        answer = rec.get("final_text")
        native = int((rec.get("tool_calls") or {}).get("native_total") or 0)
        source = "record"
    if tdir is None:
        return {
            "state": None,
            "details": {},
            "source": source,
            "calls": calls,
            "rows": rows,
            "answer": answer,
        }
    state, details = grade_trial(
        tdir,
        rec["variant"],
        rec["nonce"],
        calls,
        answer,
        outcome=rec.get("outcome", "completed"),
        error=rec.get("error"),
        native_total=native,
    )
    return {
        "state": state,
        "details": details,
        "source": source,
        "calls": calls,
        "rows": rows,
        "answer": answer,
    }


# ---------------------------------------------------------------- markdown


def _op_of(variant: str) -> Callable[[str], str | None]:
    try:
        from _server.surfaces import build_surface

        return build_surface(variant).op_of
    except Exception:
        return lambda t: None


def render_section(rec: dict, src: TraceSource, idx: int | None = None) -> str:
    g = regrade_record(rec, src)
    tdir = task_dirs().get(str(rec.get("task")))
    task = load_task(tdir) if tdir else {}
    band = rec.get("band") or task.get("band")
    prompt = (task.get("prompt") or "(task.yaml not found)").replace(
        "{nonce}", str(rec.get("nonce"))
    )
    op_of = _op_of(rec.get("variant", "good"))
    prefix = f"{idx}. " if idx is not None else ""
    sg = ", ".join(
        f"{k}={'T' if v else 'F'}" for k, v in (rec.get("subgoals") or {}).items()
    )
    L = [
        f"## {prefix}{rec.get('task')} [{band}] variant={rec.get('variant')} "
        f"{rec.get('harness')}/{rec.get('model')} trial={rec.get('trial')} state={rec.get('state')}",
        "",
        f"- execution_id: `{rec.get('execution_id')}`  nonce: `{rec.get('nonce')}`",
        f"- subgoals: {sg or '(none)'}  status_line: {rec.get('status_line')}",
        "",
        "**Prompt**",
        "",
        "> " + prompt.strip().replace("\n", "\n> "),
        "",
    ]
    rows = g["rows"]
    tracker = [r for r in rows if not r["native"]]
    native = [r for r in rows if r["native"]]
    L.append(f"**Tool calls** ({len(tracker)})")
    L.append("")
    if rows:
        for i, r in enumerate(tracker, 1):
            L += [
                f"{i}. `{r['tool']}` -> op `{op_of(r['tool'])}` is_error={r['is_error']}",
                f"   - args: `{_clip(_compact(r['args']), ARGS_MAX)}`",
                f"   - result: `{_clip(r['result'].replace(chr(10), ' '), RESULT_MAX)}`",
            ]
    else:
        L.append(
            f"(trace unavailable; record has {len(rec.get('calls') or [])} calls: "
            + ", ".join(
                f"{c.get('tool')}{'!' if c.get('is_error') else ''}"
                for c in rec.get("calls") or []
            )
            + ")"
        )
    if native:
        L += ["", f"**Native (non-tracker) calls** ({len(native)})", ""]
        for r in native:
            L.append(f"- `{r['tool']}` args: `{_clip(_compact(r['args']), ARGS_MAX)}`")
    L += [
        "",
        "**Final text**",
        "",
        "```",
        _clip(g["answer"] or "(none)", FINAL_MAX),
        "```",
        "",
    ]
    if g["state"] is None:
        L.append("**Re-grade**: unavailable (no trace and record lacks call args)")
    else:
        match = g["state"] == rec.get("state")
        extra = ""
        tb = g["details"].get("grader_traceback")
        if tb:
            extra = (
                " (grader_traceback: " + _clip(tb.strip().splitlines()[-1], 160) + ")"
            )
        L.append(
            f"**Re-grade** ({g['source']}): recorded={rec.get('state')} regraded={g['state']} "
            f"{'MATCH' if match else 'MISMATCH'}; subgoals={_compact(g['details'].get('subgoals'))}{extra}"
        )
    L += ["", "- [ ] grader correct?  notes:", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- sampling


def pick_sample(
    records: list[dict], n: int, seed: int, fail_weight: float
) -> list[dict]:
    """Deterministic: strata = (model, variant); greedy coverage of every model and variant first,
    then weighted (failures x fail_weight) sampling without replacement."""
    rng = random.Random(seed)
    recs = sorted(
        records, key=lambda r: (str(r.get("execution_id")), str(r.get("nonce")))
    )
    n = min(n, len(recs))

    def weight(r: dict) -> float:
        return 1.0 if r.get("state") == "success" else float(fail_weight)

    def draw(pool: list[dict]) -> dict:
        # Efraimidis-Spirakis: largest u**(1/w)
        return max(pool, key=lambda r: rng.random() ** (1.0 / weight(r)))

    strata: dict[tuple, list[dict]] = {}
    for r in recs:
        strata.setdefault((r.get("model"), r.get("variant")), []).append(r)
    keys = sorted(strata, key=str)
    chosen: list[dict] = []
    seen_m: set = set()
    seen_v: set = set()
    # phase 1: one per stratum, preferring strata that add a new model/variant
    remaining = list(keys)
    while remaining and len(chosen) < n:
        scored = [
            (((k[0] not in seen_m) + (k[1] not in seen_v)), rng.random(), k)
            for k in remaining
        ]
        k = max(scored)[2]
        remaining.remove(k)
        r = draw(strata[k])
        strata[k].remove(r)
        chosen.append(r)
        seen_m.add(k[0])
        seen_v.add(k[1])
    # phase 2: weighted fill
    pool = [r for k in keys for r in strata[k]]
    while len(chosen) < n and pool:
        r = draw(pool)
        pool.remove(r)
        chosen.append(r)
    return sorted(
        chosen,
        key=lambda r: (
            str(r.get("task")),
            str(r.get("variant")),
            str(r.get("model")),
            r.get("trial") or 0,
        ),
    )


# ---------------------------------------------------------------- commands


def cmd_sample(a: argparse.Namespace, src: TraceSource | None = None) -> str:
    src = src or TraceSource(db=a.db)
    recs = load_records(a.records)
    picked = pick_sample(recs, a.n, a.seed, a.fail_weight)
    head = [
        "# model-tiers hand audit sample",
        "",
        f"{len(picked)} of {len(recs)} non-BASE records; seed={a.seed} fail_weight={a.fail_weight}.",
        "",
    ]
    body = [render_section(r, src, i) for i, r in enumerate(picked, 1)]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(head) + "\n" + "\n---\n\n".join(body), encoding="utf-8")
    loaded = sum(1 for r in picked if src.load(r.get("execution_id")) is not None)
    print(
        f"wrote {out}: {len(picked)} trials, traces loaded for {loaded}/{len(picked)}"
    )
    return str(out)


def cmd_show(a: argparse.Namespace, src: TraceSource | None = None) -> None:
    src = src or TraceSource(db=a.db)
    for rec in load_records(a.records, include_base=True):
        if rec.get("execution_id") == a.execution_id:
            print(render_section(rec, src))
            return
    sys.exit(f"execution_id {a.execution_id} not in {a.records}")


def cmd_regrade(a: argparse.Namespace, src: TraceSource | None = None) -> list[dict]:
    src = src or TraceSource(db=a.db)
    rows = []
    for rec in load_records(a.records):
        g = regrade_record(rec, src)
        regraded = g["state"] or "unavailable"
        rows.append(
            {
                "execution_id": rec.get("execution_id"),
                "task": rec.get("task"),
                "variant": rec.get("variant"),
                "harness": rec.get("harness"),
                "model": rec.get("model"),
                "trial": rec.get("trial"),
                "recorded": rec.get("state"),
                "regraded": regraded,
                "source": g["source"],
                "agree": g["state"] is None or g["state"] == rec.get("state"),
            }
        )
    REGRADE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REGRADE_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["execution_id"])
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if not r["agree"]]
    na = sum(1 for r in rows if r["regraded"] == "unavailable")
    print(
        f"{len(rows)} records, {len(bad)} disagreements, {na} ungradable (no trace); csv: {REGRADE_CSV}"
    )
    if bad:
        print(
            f"{'task':5} {'variant':8} {'model':20} {'trial':>5}  recorded -> regraded   execution_id"
        )
        for r in bad:
            print(
                f"{r['task']:5} {r['variant']:8} {r['model']!s:20} {r['trial']!s:>5}  "
                f"{r['recorded']} -> {r['regraded']}   {r['execution_id']}"
            )
    return rows


def cmd_progress(a: argparse.Namespace) -> Counter:
    recs = load_records(a.records)
    c = Counter(
        (r.get("harness"), r.get("model"), r.get("variant"), r.get("state"))
        for r in recs
    )
    print(f"{len(recs)} task records in {a.records}")
    print(f"{'harness':12} {'model':20} {'variant':8} {'state':14} count")
    for (h, m, v, s), n in sorted(
        c.items(), key=lambda kv: tuple(str(x) for x in kv[0])
    ):
        print(f"{h!s:12} {m!s:20} {v!s:8} {s!s:14} {n}")
    return c


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="audit.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--records", default=str(DEFAULT_RECORDS))
        sp.add_argument("--db", default=str(DEFAULT_DB), help="m3 executions.sqlite")

    s = sub.add_parser("sample")
    common(s)
    s.add_argument("--n", type=int, default=30)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--fail-weight", type=float, default=3.0)
    s.add_argument("--out", default=str(DEFAULT_OUT))
    sh = sub.add_parser("show")
    common(sh)
    sh.add_argument("execution_id")
    common(sub.add_parser("regrade"))
    common(sub.add_parser("progress"))
    return p


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    {
        "sample": cmd_sample,
        "show": cmd_show,
        "regrade": cmd_regrade,
        "progress": cmd_progress,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
