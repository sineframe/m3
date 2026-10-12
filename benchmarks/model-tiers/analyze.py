#!/usr/bin/env python3
"""Offline analysis of model-tiers trial records (stdlib + pyyaml, no network).

Usage: analyze.py [--records PATH] [--out DIR] [--variants good,naive] [--seed 0]

Reads JSONL trial records (written by _lib/grade.py `record`) and writes SUMMARY.md,
leaderboard.csv, by_task.csv, pass_k.json, failures.csv, costs.csv, gaps.json,
ablations.csv and pi_control.csv. Output is deterministic: everything is sorted,
bootstrap RNGs are seeded per comparison, and nothing is timestamped.

Definitions
-----------
column      (harness, model, version).
valid trial a trial that is not model-mismatched and whose state is not one of
            harness_error / timeout / grader_error / classifier_error.
success     state == "success".
config      (model, variant) with all non-pi columns (all versions) pooled; used
            for gaps. Ablations use single columns.
rate (gaps) macro mean over tasks of per-task success rate.
gap closed  (small_good - small_naive) / (big_good - small_naive): the fraction of
            the gap between the big model with the good server and the small model
            with the naive server that is closed by giving the small model the
            good server. Bootstrap resamples with |denominator| < 0.02 are skipped.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

import yaml

HERE = Path(__file__).resolve().parent

STATES = [
    "harness_error",
    "timeout",
    "escaped",
    "success",
    "looped",
    "hallucinated",
    "wrong_tool",
    "bad_args",
    "wrong_target",
    "side_effects",
    "gave_up",
    "other",
]
EXTRA_STATES = ["grader_error", "classifier_error"]
EXCLUDED_STATES = {"harness_error", "timeout", "grader_error", "classifier_error"}
VARIANT_ORDER = ["good", "naive", "good-D", "good-C", "good-S", "good-E", "good-P"]
DIM_NAMES = {
    "D": "descriptions",
    "C": "catalog",
    "S": "schema",
    "E": "errors",
    "P": "pagination/output",
}
PROVIDER_PREFIXES = ("anthropic/", "openai/")
SMALL_MODELS = {"claude-haiku-5-5", "gpt-6-luna"}
Z95 = 1.959963984540054
EQUIV_MARGIN = 0.10
NATIVE_HARNESSES_EXCLUDED = {"pi"}

# (label kind, big/other model, small/other model) definitions
SAME_PRICE = [("claude-haiku-5-5", "gpt-6-luna"), ("claude-sonnet-5-5", "gpt-6.1-sol")]
TIER_STEPS = [
    ("claude-haiku-5-5", "claude-sonnet-5-5"),
    ("claude-sonnet-5-5", "claude-opus-5-5"),
    ("gpt-6-luna", "gpt-6.1-sol"),
    ("gpt-6.1-sol", "gpt-6-astra"),
]
HEADLINES = [("claude-haiku-5-5", "claude-opus-5-5"), ("gpt-6-luna", "gpt-6-astra")]


# --------------------------------------------------------------------------- helpers
def norm_model(m):
    m = m or ""
    for p in PROVIDER_PREFIXES:
        if m.startswith(p):
            return m[len(p) :]
    return m


def model_mismatch(rec):
    obs = rec.get("observed_model")
    if not obs:
        return False
    o, r = norm_model(obs), norm_model(rec.get("model"))
    return not (o == r or o.startswith(r))


def vkey(v):
    return (
        (VARIANT_ORDER.index(v), v) if v in VARIANT_ORDER else (len(VARIANT_ORDER), v)
    )


def col_of(rec):
    return (rec.get("harness") or "", rec.get("model") or "", rec.get("version") or "")


def col_name(c):
    return f"{c[0]}/{c[1]}" + (f"@{c[2]}" if c[2] else "")


def wilson(c, n, z=Z95):
    if n <= 0:
        return (None, None)
    p = c / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def pass_hat(c, n, k):
    return math.comb(c, k) / math.comb(n, k) if 1 <= k <= n else None


def pass_at(c, n, k):
    return 1 - math.comb(n - c, k) / math.comb(n, k) if 1 <= k <= n else None


def quantile(vals, q):
    s = sorted(vals)
    if not s:
        return None
    if len(s) == 1:
        return float(s[0])
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def equivalence_verdict(ci90, ci95, margin=EQUIV_MARGIN):
    """equivalent: 90% CI inside [-margin, +margin]; different: 95% CI excludes 0."""
    if ci90 is None or ci95 is None or None in ci90 or None in ci95:
        return "n/a"
    if ci90[0] >= -margin and ci90[1] <= margin:
        return "equivalent"
    if ci95[0] > 0 or ci95[1] < 0:
        return "different"
    return "inconclusive"


def fmt(x, nd=3):
    if x is None:
        return ""
    if isinstance(x, float):
        if math.isinf(x) or math.isnan(x):
            return "n/a"
        r = round(x, nd)
        return f"{0.0 if r == 0 else r:.{nd}f}"
    return str(x)


def rnd(x, nd=6):
    if isinstance(x, float):
        if math.isinf(x) or math.isnan(x):
            return None
        r = round(x, nd)
        return 0.0 if r == 0 else r
    return x


def pct(x):
    return "" if x is None else f"{100 * x:.1f}%"


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def md_table(headers, rows):
    out = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


# --------------------------------------------------------------------------- cost
def load_prices(path=None):
    with open(path or HERE / "prices.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _tier_mults(p):
    lc = p.get("long_context")
    if not lc:
        return None, 1.0, 1.0
    if "multiplier" in lc:
        return lc["threshold"], float(lc["multiplier"]), float(lc["multiplier"])
    return lc["threshold"], lc["input"] / p["input"], lc["output"] / p["output"]


def token_cost(usage, model, prices):
    """Price-table cost for one trial. Returns (lo, hi, bounded) or None if unpriceable.

    input_tokens is taken to include cache reads (billed at the cache-read rate).
    Per-turn tiering: each turn's share of the trial's tokens is proportional to its
    prompt size; the turn is billed at long-context rates (input and output) when its
    prompt exceeds the model threshold. Without per-turn data, lo = all turns below
    the threshold, hi = all above (only if total input exceeds the threshold).
    """
    p = prices.get("models", {}).get(norm_model(model))
    if p is None or not usage:
        return None
    pr = prices["providers"][p["provider"]]
    g = lambda k: usage.get(k) or 0
    inp, out, rea = g("input_tokens"), g("output_tokens"), g("reasoning_tokens")
    cread, cwrite = g("cache_read_tokens"), g("cache_write_tokens")
    in_rate = p["input"]
    cr_rate = in_rate * pr["cache_read_multiplier"]
    cw_rate = in_rate * pr["cache_write_multiplier"]
    billed_out = out + (rea if pr.get("reasoning_billed_separately") else 0)
    base_in = (max(inp - cread, 0) * in_rate + cread * cr_rate + cwrite * cw_rate) / 1e6
    base_out = billed_out * p["output"] / 1e6
    thr, in_m, out_m = _tier_mults(p)
    base = base_in + base_out
    if thr is None:
        return (base, base, False)
    turns = usage.get("per_turn_input_tokens")
    if isinstance(turns, list) and turns and sum(turns) > 0:
        tot = float(sum(turns))
        c = sum(
            (t / tot)
            * (
                base_in * (in_m if t > thr else 1.0)
                + base_out * (out_m if t > thr else 1.0)
            )
            for t in turns
        )
        return (c, c, False)
    hi = base_in * in_m + base_out * out_m if inp > thr else base
    return (base, hi, hi > base)


def trial_cost(rec, prices):
    """-> dict(cost_lo, cost_hi, bounded, est_lo, est_hi, reported) or None."""
    usage = rec.get("usage") or {}
    est = token_cost(usage, rec.get("model"), prices)
    rep = usage.get("reported_cost_usd")
    if rep is not None:
        return {
            "cost_lo": float(rep),
            "cost_hi": float(rep),
            "bounded": False,
            "reported": True,
            "est_lo": est[0] if est else None,
            "est_hi": est[1] if est else None,
        }
    if est is None:
        return None
    return {
        "cost_lo": est[0],
        "cost_hi": est[1],
        "bounded": bool(est[2]),
        "reported": False,
        "est_lo": est[0],
        "est_hi": est[1],
    }


# --------------------------------------------------------------------------- loading / exclusion
def load_records(path):
    recs, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(r, dict):
                recs.append(r)
            else:
                bad += 1
    return recs, bad


def tstate(rec):
    return rec.get("state") or rec.get("outcome") or "other"


def prepare(recs, variants=None):
    """Split records into BASE vs trials; apply model-mismatch and BASE exclusions."""
    info = {
        "n_records": len(recs),
        "model_dropped": defaultdict(int),
        "model_dropped_obs": defaultdict(int),
        "base_failed": [],
        "base_missing_cells": 0,
        "n_base": 0,
    }
    base_by = defaultdict(list)  # (col, variant|None) -> states
    trials = []
    for r in recs:
        if r.get("task") == "BASE":
            info["n_base"] += 1
            base_by[(col_of(r), r.get("variant"))].append(tstate(r))
        else:
            trials.append(r)
    kept = []
    for r in trials:
        if model_mismatch(r):
            info["model_dropped"][col_of(r)] += 1
            info["model_dropped_obs"][
                (col_of(r), norm_model(r.get("observed_model")))
            ] += 1
            continue
        kept.append(r)

    def base_states(col, v):
        return base_by.get((col, v), []) + base_by.get((col, None), [])

    cells = defaultdict(list)
    for r in kept:
        if variants is None or r.get("variant") in variants:
            cells[(col_of(r), r.get("variant"))].append(r)
    excluded_cells = {}
    for col, v in sorted(cells, key=lambda k: (k[0], vkey(k[1]))):
        bs = base_states(col, v)
        if bs and any(s != "success" for s in bs):
            excluded_cells[(col, v)] = sorted(set(s for s in bs if s != "success"))
    info["excluded_cells"] = excluded_cells
    for k in excluded_cells:
        info["base_failed"].append((k, len(cells[k])))
    live = {
        k: sorted(
            v,
            key=lambda r: (
                r.get("task") or "",
                r.get("trial") or 0,
                r.get("nonce") or "",
            ),
        )
        for k, v in cells.items()
        if k not in excluded_cells
    }
    return live, info


# --------------------------------------------------------------------------- aggregation
def cell_stats(trials, prices):
    n_total = len(trials)
    states = defaultdict(int)
    valid = []
    for r in trials:
        s = tstate(r)
        states[s] += 1
        if s not in EXCLUDED_STATES:
            valid.append(r)
    succ = sum(1 for r in valid if tstate(r) == "success")
    per_task = defaultdict(list)
    for r in valid:
        per_task[r.get("task")].append(1 if tstate(r) == "success" else 0)
    cost = {
        "lo": 0.0,
        "hi": 0.0,
        "est_lo": 0.0,
        "est_hi": 0.0,
        "rep": 0.0,
        "rep_est": 0.0,
        "bounded": 0,
        "reported": 0,
        "missing": 0,
    }
    for r in valid:
        c = trial_cost(r, prices)
        if c is None:
            cost["missing"] += 1
            continue
        cost["lo"] += c["cost_lo"]
        cost["hi"] += c["cost_hi"]
        if c["est_lo"] is not None:
            cost["est_lo"] += c["est_lo"]
            cost["est_hi"] += c["est_hi"]
        cost["bounded"] += int(c["bounded"])
        if c["reported"]:
            cost["reported"] += 1
            cost["rep"] += c["cost_lo"]
            if c["est_lo"] is not None:
                cost["rep_est"] += (c["est_lo"] + c["est_hi"]) / 2
    return {
        "n_total": n_total,
        "states": states,
        "valid": valid,
        "n": len(valid),
        "succ": succ,
        "per_task": dict(per_task),
        "cost": cost,
    }


def series(valid, getter):
    out = []
    for r in valid:
        v = getter(r)
        if isinstance(v, (int, float)):
            out.append(v)
    return out


def pass_k_for(per_task):
    """-> {k: {"pass_hat","pass_at","tasks"}} and `complete` flag per k."""
    if not per_task:
        return {}
    maxn = max(len(v) for v in per_task.values())
    out = {}
    for k in range(1, maxn + 1):
        ph, pa = [], []
        for t in sorted(per_task):
            v = per_task[t]
            if len(v) >= k:
                ph.append(pass_hat(sum(v), len(v), k))
                pa.append(pass_at(sum(v), len(v), k))
        out[k] = {
            "pass_hat": mean(ph),
            "pass_at": mean(pa),
            "tasks": len(ph),
            "complete": len(ph) == len(per_task),
        }
    return out


# --------------------------------------------------------------------------- bootstrap
def macro(cfg, tasks):
    return mean(sum(cfg[t]) / len(cfg[t]) for t in tasks)


def bootstrap(cfgs, stat, B, rng_key, seed):
    """Task-clustered bootstrap over configs (dicts task -> list of 0/1).

    Resample tasks with replacement (shared across configs), then each config's
    trials within every drawn task with replacement. Returns (point, samples) or None.
    """
    common = sorted(set.intersection(*[set(c) for c in cfgs]))
    if not common:
        return None
    point = stat([macro(c, common) for c in cfgs])
    rng = random.Random(f"{seed}|{rng_key}")
    samples = []
    T = len(common)
    for _ in range(B):
        drawn = rng.choices(common, k=T)
        rates = []
        for c in cfgs:
            tot = 0.0
            for t in drawn:
                v = c[t]
                tot += sum(rng.choices(v, k=len(v))) / len(v)
            rates.append(tot / T)
        s = stat(rates)
        if s is not None:
            samples.append(s)
    samples.sort()
    return point, samples, len(common)


def ci(samples, level):
    if not samples:
        return (None, None)
    a = (1 - level) / 2
    return (quantile(samples, a), quantile(samples, 1 - a))


# --------------------------------------------------------------------------- main analysis
def analyze(records_path, out_dir, variants=None, seed=0, prices=None, B=2000):
    prices = prices or load_prices()
    recs, malformed = load_records(records_path)
    live, info = prepare(recs, variants)
    all_variants_in_records = sorted(
        {r.get("variant") for r in recs if r.get("task") != "BASE"}, key=vkey
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = {k: cell_stats(v, prices) for k, v in live.items()}
    keys = sorted(stats, key=lambda k: (vkey(k[1]), k[0]))
    band_of = {}
    for r in recs:
        if r.get("task") != "BASE" and r.get("task") not in band_of:
            band_of[r.get("task")] = r.get("band")
    for r in sorted(
        (r for r in recs if r.get("task") != "BASE"), key=lambda r: r.get("task") or ""
    ):
        band_of.setdefault(r.get("task"), r.get("band"))

    # ---- leaderboard
    lb_header = [
        "harness",
        "model",
        "version",
        "variant",
        "n_trials",
        "n_valid",
        "successes",
        "success_rate",
        "ci95_lo",
        "ci95_hi",
        "pass_hat_1",
        "pass_hat_4",
        "cost_per_success",
        "cost_per_success_lo",
        "cost_per_success_hi",
        "cost_bounded",
        "median_turns",
        "p90_turns",
        "median_duration_ms",
        "p90_duration_ms",
        "median_tracker_calls",
        "p90_tracker_calls",
        "median_tracker_errors",
        "p90_tracker_errors",
        "harness_error_rate",
        "timeout_rate",
        "grader_error_n",
        "classifier_error_n",
    ]
    lb_rows, lb_meta = [], {}
    pk_json = {"columns": {}}
    for k in keys:
        col, v = k
        s = stats[k]
        n, c = s["n"], s["succ"]
        lo, hi = wilson(c, n)
        pk = pass_k_for(s["per_task"])
        pk_json.setdefault("columns", {}).setdefault(col_name(col), {})[v] = {
            "n_tasks": len(s["per_task"]),
            "k": {
                str(kk): {
                    "pass_hat": rnd(d["pass_hat"]),
                    "pass_at": rnd(d["pass_at"]),
                    "tasks": d["tasks"],
                    "complete": d["complete"],
                }
                for kk, d in pk.items()
            },
        }
        p1 = pk.get(1, {}).get("pass_hat")
        p4 = pk.get(4, {}).get("pass_hat") if pk.get(4, {}).get("complete") else None
        cs = s["cost"]
        cps = cps_lo = cps_hi = None
        if c > 0 and cs["missing"] < n:
            cps, cps_lo, cps_hi = (
                (cs["lo"] + cs["hi"]) / 2 / c,
                cs["lo"] / c,
                cs["hi"] / c,
            )
        bounded = cs["bounded"] > 0
        if c > 0 and cs["lo"] == cs["hi"]:
            cps = cs["lo"] / c
        v_ = s["valid"]
        turns = series(v_, lambda r: r.get("turn_count"))
        dur = series(v_, lambda r: r.get("duration_ms"))
        tc = series(v_, lambda r: (r.get("tool_calls") or {}).get("tracker_total"))
        te = series(v_, lambda r: (r.get("tool_calls") or {}).get("tracker_errors"))
        st = s["states"]
        nt = s["n_total"]
        row = [
            col[0],
            col[1],
            col[2],
            v,
            nt,
            n,
            c,
            rnd(c / n) if n else None,
            rnd(lo),
            rnd(hi),
            rnd(p1),
            rnd(p4),
            rnd(cps),
            rnd(cps_lo),
            rnd(cps_hi),
            int(bounded),
            rnd(quantile(turns, 0.5)),
            rnd(quantile(turns, 0.9)),
            rnd(quantile(dur, 0.5)),
            rnd(quantile(dur, 0.9)),
            rnd(quantile(tc, 0.5)),
            rnd(quantile(tc, 0.9)),
            rnd(quantile(te, 0.5)),
            rnd(quantile(te, 0.9)),
            rnd(st.get("harness_error", 0) / nt),
            rnd(st.get("timeout", 0) / nt),
            st.get("grader_error", 0),
            st.get("classifier_error", 0),
        ]
        lb_rows.append(row)
        lb_meta[k] = dict(zip(lb_header, row, strict=True))
    lb_sorted = sorted(
        lb_rows,
        key=lambda r: (
            vkey(r[3]),
            -(r[7] if r[7] is not None else -1),
            r[0],
            r[1],
            r[2],
        ),
    )
    write_csv(out_dir / "leaderboard.csv", lb_header, lb_sorted)
    write_json(out_dir / "pass_k.json", pk_json)

    # ---- by_task
    bt_rows = []
    for k in keys:
        col, v = k
        s = stats[k]
        by_task_all = defaultdict(lambda: defaultdict(int))
        for r in live[k]:
            by_task_all[r.get("task")][tstate(r)] += 1
        for t in sorted(by_task_all):
            vals = s["per_task"].get(t, [])
            n, c = len(vals), sum(vals)
            lo, hi = wilson(c, n)
            sts = by_task_all[t]
            bt_rows.append(
                [
                    col[0],
                    col[1],
                    col[2],
                    v,
                    t,
                    band_of.get(t),
                    sum(sts.values()),
                    n,
                    c,
                    rnd(c / n) if n else None,
                    rnd(lo),
                    rnd(hi),
                    sts.get("harness_error", 0),
                    sts.get("timeout", 0),
                ]
            )
    write_csv(
        out_dir / "by_task.csv",
        [
            "harness",
            "model",
            "version",
            "variant",
            "task",
            "band",
            "n_trials",
            "n_valid",
            "successes",
            "success_rate",
            "ci95_lo",
            "ci95_hi",
            "harness_error_n",
            "timeout_n",
        ],
        sorted(bt_rows, key=lambda r: (vkey(r[3]), r[0], r[1], r[2], r[4])),
    )

    # ---- failures
    fcols = STATES + EXTRA_STATES
    f_rows = []
    for k in keys:
        col, v = k
        cnt = defaultdict(int)
        for st_, n_ in stats[k]["states"].items():
            cnt[st_ if st_ in fcols else "other"] += n_
        f_rows.append(
            [col[0], col[1], col[2], v, stats[k]["n_total"]]
            + [cnt.get(x, 0) for x in fcols]
        )
    write_csv(
        out_dir / "failures.csv",
        ["harness", "model", "version", "variant", "n_trials", *fcols],
        sorted(f_rows, key=lambda r: (vkey(r[3]), r[0], r[1], r[2])),
    )

    # ---- costs
    c_rows = []
    for k in keys:
        col, v = k
        s = stats[k]
        cs, n, c = s["cost"], s["n"], s["succ"]
        cps = cps_lo = cps_hi = None
        if c > 0:
            cps, cps_lo, cps_hi = (
                (cs["lo"] + cs["hi"]) / 2 / c,
                cs["lo"] / c,
                cs["hi"] / c,
            )
        ratio = cs["rep"] / cs["rep_est"] if cs["rep_est"] > 0 else None
        c_rows.append(
            [
                col[0],
                col[1],
                col[2],
                v,
                n,
                c,
                rnd(cs["lo"]),
                rnd(cs["hi"]),
                rnd((cs["lo"] + cs["hi"]) / 2),
                cs["bounded"],
                cs["reported"],
                cs["missing"],
                rnd(cs["est_lo"]),
                rnd(cs["est_hi"]),
                rnd(cs["rep"]) if cs["reported"] else None,
                rnd(ratio),
                rnd(cps),
                rnd(cps_lo),
                rnd(cps_hi),
                "n/a" if cps is None else "",
            ]
        )
    write_csv(
        out_dir / "costs.csv",
        [
            "harness",
            "model",
            "version",
            "variant",
            "n_valid",
            "successes",
            "cost_total_lo",
            "cost_total_hi",
            "cost_total_mid",
            "bounded_trials",
            "reported_cost_trials",
            "cost_missing_trials",
            "est_total_lo",
            "est_total_hi",
            "reported_total",
            "reported_over_est",
            "cost_per_success",
            "cost_per_success_lo",
            "cost_per_success_hi",
            "cost_per_success_flag",
        ],
        sorted(c_rows, key=lambda r: (vkey(r[3]), r[0], r[1], r[2])),
    )

    # ---- pooled configs for gaps / pi control
    pooled = defaultdict(
        lambda: defaultdict(list)
    )  # (model, variant) -> task -> [0/1] (non-pi)
    for k in keys:
        col, v = k
        if col[0] in NATIVE_HARNESSES_EXCLUDED:
            continue
        for t, vals in sorted(stats[k]["per_task"].items()):
            pooled[(norm_model(col[1]), v)][t].extend(vals)

    def cfg(model, v):
        d = pooled.get((model, v))
        return {t: x for t, x in d.items() if x} if d else None

    gaps = {
        "settings": {
            "bootstrap_resamples": B,
            "seed": seed,
            "equivalence_margin": EQUIV_MARGIN,
            "rate": "macro mean over tasks of per-task success rate (non-pi columns, versions pooled)",
            "diff": "first config minus second config",
        },
        "pairs": [],
        "gap_closed": [],
    }
    vlist = [
        v
        for v in sorted({k[1] for k in keys}, key=vkey)
        if variants is None or v in variants
    ]

    def add_pair(kind, label, a, b):
        ca, cb = cfg(*a), cfg(*b)
        entry = {
            "kind": kind,
            "label": label,
            "a": f"{a[0]}|{a[1]}",
            "b": f"{b[0]}|{b[1]}",
        }
        res = (
            bootstrap([ca, cb], lambda r: r[0] - r[1], B, f"pair:{label}", seed)
            if ca and cb
            else None
        )
        if res is None:
            entry.update({"status": "no_data"})
        else:
            point, smp, nt = res
            c90, c95 = ci(smp, 0.90), ci(smp, 0.95)
            common = sorted(set(ca) & set(cb))
            entry.update(
                {
                    "status": "ok",
                    "n_tasks": nt,
                    "rate_a": rnd(macro(ca, common)),
                    "rate_b": rnd(macro(cb, common)),
                    "diff": rnd(point),
                    "ci90": [rnd(c90[0]), rnd(c90[1])],
                    "ci95": [rnd(c95[0]), rnd(c95[1])],
                    "verdict": equivalence_verdict(c90, c95),
                }
            )
        gaps["pairs"].append(entry)

    for v in vlist:
        for a, b in SAME_PRICE:
            add_pair("same_price", f"{a} vs {b} [{v}]", (a, v), (b, v))
        for small, big in TIER_STEPS:
            add_pair("tier_step", f"{big} - {small} [{v}]", (big, v), (small, v))
    if variants is None or ("good" in variants and "naive" in variants):
        for small, big in HEADLINES:
            add_pair(
                "headline",
                f"{small} (good) vs {big} (naive)",
                (small, "good"),
                (big, "naive"),
            )
            sg, sn, bg = cfg(small, "good"), cfg(small, "naive"), cfg(big, "good")
            entry = {
                "small": small,
                "big": big,
                "definition": "(small_good - small_naive) / (big_good - small_naive)",
            }

            def stat(r):
                den = r[2] - r[1]
                return (r[0] - r[1]) / den if abs(den) >= 0.02 else None

            res = (
                bootstrap([sg, sn, bg], stat, B, f"closed:{small}:{big}", seed)
                if sg and sn and bg
                else None
            )
            if res is None:
                entry["status"] = "no_data"
            else:
                point, smp, nt = res
                c95 = ci(smp, 0.95)
                common = sorted(set(sg) & set(sn) & set(bg))
                entry.update(
                    {
                        "status": "ok",
                        "n_tasks": nt,
                        "small_good": rnd(macro(sg, common)),
                        "small_naive": rnd(macro(sn, common)),
                        "big_good": rnd(macro(bg, common)),
                        "gap_closed": rnd(point) if point is not None else None,
                        "ci95": [rnd(c95[0]), rnd(c95[1])],
                        "valid_resamples": len(smp),
                    }
                )
            gaps["gap_closed"].append(entry)
    write_json(out_dir / "gaps.json", gaps)

    # ---- ablations
    ab_rows, ab_deltas = [], defaultdict(list)
    abl_variants = sorted(
        {
            r.get("variant")
            for r in recs
            if r.get("task") != "BASE" and (r.get("variant") or "").startswith("good-")
        },
        key=vkey,
    )
    abl_live, _ = prepare(recs, None)
    abl_tasks = {k: defaultdict(list) for k in abl_live}
    for k, trs in abl_live.items():
        for r in trs:
            if tstate(r) not in EXCLUDED_STATES:
                abl_tasks[k][r.get("task")].append(1 if tstate(r) == "success" else 0)
    for x in abl_variants:
        dim = x.split("-", 1)[1]
        for col in sorted({k[0] for k in abl_live}):
            g, xv = abl_tasks.get((col, "good")), abl_tasks.get((col, x))
            if not g or not xv:
                continue
            res = bootstrap(
                [dict(g), dict(xv)], lambda r: r[1] - r[0], B, f"abl:{col}:{x}", seed
            )
            if res is None:
                continue
            point, smp, nt = res
            c95 = ci(smp, 0.95)
            common = sorted(set(g) & set(xv))
            small = (
                col[0] not in NATIVE_HARNESSES_EXCLUDED
                and norm_model(col[1]) in SMALL_MODELS
            )
            if small:
                ab_deltas[x].append(point)
            ab_rows.append(
                [
                    "column",
                    col[0],
                    col[1],
                    col[2],
                    x,
                    dim,
                    nt,
                    rnd(macro(g, common)),
                    rnd(macro(xv, common)),
                    rnd(point),
                    rnd(c95[0]),
                    rnd(c95[1]),
                    int(small),
                    None,
                ]
            )
    ranking = sorted(
        ((mean(d), x) for x, d in ab_deltas.items()), key=lambda t: (t[0], vkey(t[1]))
    )
    for rank, (d, x) in enumerate(ranking, 1):
        ab_rows.append(
            [
                "small_mean",
                "",
                "",
                "",
                x,
                x.split("-", 1)[1],
                len(ab_deltas[x]),
                None,
                None,
                rnd(d),
                None,
                None,
                1,
                rank,
            ]
        )
    write_csv(
        out_dir / "ablations.csv",
        [
            "scope",
            "harness",
            "model",
            "version",
            "variant",
            "dimension",
            "n_tasks_or_columns",
            "rate_good",
            "rate_variant",
            "delta_vs_good",
            "ci95_lo",
            "ci95_hi",
            "small_model",
            "rank_by_mean_small_delta",
        ],
        ab_rows,
    )

    # ---- pi control
    pi_rows = []
    pi_flags = []
    pi_pool = defaultdict(
        lambda: defaultdict(list)
    )  # (base_model, variant) -> task -> list
    for k in keys:
        col, v = k
        if col[0] == "pi":
            for t, vals in stats[k]["per_task"].items():
                pi_pool[(norm_model(col[1]), v)][t].extend(vals)
    for bm, v in sorted(pi_pool, key=lambda k: (k[0], vkey(k[1]))):
        nat = pooled.get((bm, v))
        if not nat:
            continue
        for t in sorted(set(pi_pool[(bm, v)]) | set(nat)):
            pv, nv = pi_pool[(bm, v)].get(t, []), nat.get(t, [])
            pr_ = sum(pv) / len(pv) if pv else None
            nr_ = sum(nv) / len(nv) if nv else None
            flag = ""
            if pr_ is not None and nr_ is not None:
                if pr_ >= 0.8 and nr_ <= 0.2:
                    flag = "harness_effect_pi_better"
                elif nr_ >= 0.8 and pr_ <= 0.2:
                    flag = "harness_effect_native_better"
            pi_rows.append([bm, v, t, len(nv), rnd(nr_), len(pv), rnd(pr_), flag])
            if flag:
                pi_flags.append(pi_rows[-1])
    write_csv(
        out_dir / "pi_control.csv",
        [
            "base_model",
            "variant",
            "task",
            "n_native",
            "native_rate",
            "n_pi",
            "pi_rate",
            "flag",
        ],
        pi_rows,
    )

    # ---- SUMMARY.md
    write_summary(out_dir / "SUMMARY.md", locals())
    return {"keys": keys, "stats": stats, "gaps": gaps, "info": info}


def write_summary(path, L):
    info, keys, stats, lb_meta = L["info"], L["keys"], L["stats"], L["lb_meta"]
    gaps, recs, malformed = L["gaps"], L["recs"], L["malformed"]
    lines = ["# Model tiers analysis", ""]
    n_trials_raw = sum(1 for r in recs if r.get("task") != "BASE")
    n_used = sum(s["n_total"] for s in stats.values())
    lines += [
        f"Records: {info['n_records']} read ({malformed} malformed lines skipped), {n_trials_raw} trials, "
        f"{info['n_base']} BASE; {n_used} trials kept in {len(keys)} (column, variant) cells; "
        f"bootstrap seed {L['seed']}, {L['B']} resamples.",
        "",
    ]
    for v in sorted({k[1] for k in keys}, key=vkey):
        rows = []
        vk = sorted(
            [k for k in keys if k[1] == v],
            key=lambda k: (
                -(
                    lb_meta[k]["success_rate"]
                    if lb_meta[k]["success_rate"] is not None
                    else -1
                ),
                k[0],
            ),
        )
        for k in vk:
            m = lb_meta[k]
            cps = m["cost_per_success"]
            if cps is None:
                cs = "n/a"
            else:
                cs = f"${cps:.4f}" + ("*" if m["cost_bounded"] else "")
            rows.append(
                [
                    col_name(k[0]),
                    m["n_valid"],
                    m["successes"],
                    f"{pct(m['success_rate'])} [{pct(m['ci95_lo'])}, {pct(m['ci95_hi'])}]"
                    if m["n_valid"]
                    else "n/a",
                    fmt(m["pass_hat_1"]),
                    fmt(m["pass_hat_4"]) or "-",
                    cs,
                    fmt(m["median_turns"], 1),
                    fmt(
                        m["median_duration_ms"] / 1000
                        if m["median_duration_ms"] is not None
                        else None,
                        1,
                    ),
                    pct(m["harness_error_rate"]),
                    pct(m["timeout_rate"]),
                ]
            )
        lines += [
            f"## Leaderboard: {v}",
            "",
            md_table(
                [
                    "column",
                    "n",
                    "succ",
                    "success [95% Wilson]",
                    "pass^1",
                    "pass^4",
                    "$/success",
                    "med turns",
                    "med latency s",
                    "harness err",
                    "timeout",
                ],
                rows,
            ),
            "",
        ]
    lines += [
        "`*` = cost bounded (no per-turn token data for some trials; point = midpoint of bounds, see costs.csv). "
        "n excludes harness_error/timeout/grader_error/classifier_error.",
        "",
    ]

    ok = [p for p in gaps["pairs"] if p["status"] == "ok"]
    if ok:
        lines += [
            "## Pair comparisons (task-clustered bootstrap)",
            "",
            md_table(
                [
                    "kind",
                    "comparison (a vs b)",
                    "a",
                    "b",
                    "diff",
                    "90% CI",
                    "95% CI",
                    "verdict",
                ],
                [
                    [
                        p["kind"],
                        p["label"],
                        pct(p["rate_a"]),
                        pct(p["rate_b"]),
                        fmt(p["diff"]),
                        f"[{fmt(p['ci90'][0])}, {fmt(p['ci90'][1])}]",
                        f"[{fmt(p['ci95'][0])}, {fmt(p['ci95'][1])}]",
                        p["verdict"],
                    ]
                    for p in ok
                ],
            ),
            "",
            f"Verdict: equivalent if 90% CI within +/-{EQUIV_MARGIN:.2f}; different if 95% CI excludes 0; "
            "else inconclusive. diff = a - b.",
            "",
        ]
    gc = [g for g in gaps["gap_closed"] if g["status"] == "ok"]
    if gc:
        lines += [
            "## Gap closed",
            "",
            md_table(
                [
                    "small",
                    "big",
                    "small good",
                    "small naive",
                    "big good",
                    "gap closed",
                    "95% CI",
                ],
                [
                    [
                        g["small"],
                        g["big"],
                        pct(g["small_good"]),
                        pct(g["small_naive"]),
                        pct(g["big_good"]),
                        fmt(g["gap_closed"]) or "n/a",
                        f"[{fmt(g['ci95'][0])}, {fmt(g['ci95'][1])}]",
                    ]
                    for g in gc
                ],
            ),
            "",
            "gap closed = (small_good - small_naive) / (big_good - small_naive).",
            "",
        ]
    ranking = L["ranking"]
    if ranking:
        lines += [
            "## Ablation ranking (mean delta vs good on small native columns; most harmful first)",
            "",
            md_table(
                ["rank", "variant", "dimension", "mean delta", "small columns"],
                [
                    [
                        i,
                        x,
                        DIM_NAMES.get(x.split("-", 1)[1], x.split("-", 1)[1]),
                        fmt(d),
                        len(L["ab_deltas"][x]),
                    ]
                    for i, (d, x) in enumerate(ranking, 1)
                ],
            ),
            "",
        ]
    if L["pi_flags"]:
        lines += [
            "## Pi control: harness effects",
            "",
            md_table(
                ["base model", "variant", "task", "native", "pi", "flag"],
                [[r[0], r[1], r[2], pct(r[4]), pct(r[6]), r[7]] for r in L["pi_flags"]],
            ),
            "",
        ]
    lines += ["## Exclusions", ""]
    md = info["model_dropped"]
    if md:
        lines += [
            "Trials dropped for observed_model mismatch:",
            "",
            md_table(
                ["column", "observed", "dropped"],
                [
                    [col_name(c), o, n]
                    for (c, o), n in sorted(info["model_dropped_obs"].items())
                ],
            ),
            "",
        ]
    else:
        lines += ["No trials dropped for observed_model mismatch.", ""]
    if info["excluded_cells"]:
        lines += [
            "Cells excluded because BASE was not success:",
            "",
            md_table(
                ["column", "variant", "BASE state(s)", "trials dropped"],
                [
                    [
                        col_name(c),
                        v,
                        ",".join(s),
                        len(
                            [
                                1
                                for r in L["recs"]
                                if r.get("task") != "BASE"
                                and col_of(r) == c
                                and r.get("variant") == v
                                and not model_mismatch(r)
                            ]
                        ),
                    ]
                    for (c, v), s in sorted(
                        info["excluded_cells"].items(),
                        key=lambda kv: (kv[0][0], vkey(kv[0][1])),
                    )
                ],
            ),
            "",
        ]
    else:
        lines += ["No cells excluded for BASE failure.", ""]
    ex_rows = []
    for k in keys:
        st = stats[k]["states"]
        e = [
            st.get(s, 0)
            for s in ("harness_error", "timeout", "grader_error", "classifier_error")
        ]
        if any(e):
            ex_rows.append([col_name(k[0]), k[1], *e])
    if ex_rows:
        lines += [
            "Trials excluded from the success denominator:",
            "",
            md_table(
                [
                    "column",
                    "variant",
                    "harness_error",
                    "timeout",
                    "grader_error",
                    "classifier_error",
                ],
                ex_rows,
            ),
            "",
        ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--records", default=str(HERE / "results/_inbox/records.jsonl"))
    ap.add_argument("--out", default=str(HERE / "results/analysis"))
    ap.add_argument(
        "--variants", default=None, help="comma list; default: all variants in records"
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prices", default=str(HERE / "prices.yaml"))
    ap.add_argument("--resamples", type=int, default=2000)
    a = ap.parse_args(argv)
    variants = [v for v in a.variants.split(",") if v] if a.variants else None
    analyze(a.records, a.out, variants, a.seed, load_prices(a.prices), a.resamples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
