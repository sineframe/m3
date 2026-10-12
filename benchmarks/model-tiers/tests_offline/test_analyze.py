"""Offline tests for analyze.py (no network, no M3 runtime)."""

import csv
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import analyze as A

pytestmark = pytest.mark.m3(suite_name="model-tiers-offline")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "records_sample.jsonl"
PRICES = A.load_prices()

COLUMNS = [  # harness, model, version, skill
    ("claude_code", "claude-opus-5-5", "2.1.0", 0.90),
    ("claude_code", "claude-haiku-5-5", "2.1.0", 0.55),
    ("codex", "gpt-6-astra", "0.9.0", 0.90),
    ("codex", "gpt-6-luna", "0.9.0", 0.50),
    ("pi", "anthropic/claude-haiku-5-5", "1.4.0", 0.55),
]
VARIANT_PENALTY = {"good": 0.0, "naive": 0.25, "good-D": 0.12}
TASKS = [f"T{i:02d}" for i in range(1, 14)]


def generate(seed=0, trials=4):
    """Seeded synthetic records resembling real harness output."""
    rng = random.Random(seed)
    out = []
    for h, m, ver, skill in COLUMNS:
        for v, pen in VARIANT_PENALTY.items():
            base_state = (
                "harness_error" if (m == "gpt-6-luna" and v == "naive") else "success"
            )
            out.append(
                {
                    "task": "BASE",
                    "variant": v,
                    "harness": h,
                    "model": m,
                    "observed_model": m,
                    "version": ver,
                    "state": base_state,
                    "trial": 0,
                }
            )
            for t in TASKS:
                hard = (int(t[1:]) % 4) * 0.05
                for tr in range(1, trials + 1):
                    ok = rng.random() < max(0.02, skill - pen - hard)
                    state = (
                        "success"
                        if ok
                        else rng.choice(
                            ["looped", "wrong_target", "gave_up", "bad_args"]
                        )
                    )
                    r = rng.random()
                    if r < 0.03:
                        state = "harness_error"
                    elif r < 0.05:
                        state = "timeout"
                    turns = rng.randint(3, 14)
                    inp = turns * rng.randint(4000, 30000)
                    per_turn = (
                        None
                        if (m.endswith("haiku-5-5") and rng.random() < 0.5)
                        else [inp // turns] * turns
                    )
                    usage = {
                        "input_tokens": inp,
                        "output_tokens": rng.randint(500, 4000),
                        "reasoning_tokens": rng.randint(0, 800),
                        "cache_read_tokens": inp // 2,
                        "cache_write_tokens": inp // 10 if h != "codex" else 0,
                        "total_tokens": inp,
                        "reported_cost_usd": None,
                        "per_turn_input_tokens": per_turn,
                    }
                    if h == "claude_code":
                        usage["reported_cost_usd"] = round(inp * 1.5e-6 / 10, 6)
                    obs = m
                    if (
                        m == "claude-opus-5-5"
                        and v == "good"
                        and t == "T01"
                        and tr == 1
                    ):
                        obs = "claude-sonnet-5-5"  # silent fallback -> dropped
                    if m == "gpt-6-astra" and v == "good" and t == "T02" and tr == 2:
                        obs = "gpt-6-astra-20261001"  # dated suffix -> kept
                    out.append(
                        {
                            "task": t,
                            "band": "filter" if int(t[1:]) % 2 else "write",
                            "variant": v,
                            "harness": h,
                            "model": m,
                            "observed_model": obs,
                            "version": ver,
                            "trial": tr,
                            "nonce": f"N{rng.getrandbits(40):010x}",
                            "outcome": "completed",
                            "state": state,
                            "duration_ms": rng.randint(8000, 90000),
                            "turn_count": turns,
                            "tool_calls": {
                                "tracker_total": turns - 1,
                                "tracker_errors": rng.randint(0, 3),
                                "native_total": 0,
                                "max_result_chars": 1234,
                            },
                            "usage": usage,
                        }
                    )
    return out


def dump(recs, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def test_fixture_matches_generator():
    if not FIXTURE.exists():
        FIXTURE.parent.mkdir(exist_ok=True)
        dump(generate(), FIXTURE)
    assert FIXTURE.read_text() == "".join(
        json.dumps(r, sort_keys=True) + "\n" for r in generate()
    )


def read_csv(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def outdir(tmp_path_factory):
    d = tmp_path_factory.mktemp("analysis")
    A.main(["--records", str(FIXTURE), "--out", str(d), "--resamples", "300"])
    return d


# ---------------------------------------------------------------- math
def test_wilson_known_values():
    lo, hi = A.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(
        0.9433, abs=1e-3
    )
    lo, hi = A.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)
    lo, hi = A.wilson(10, 10)
    assert lo == pytest.approx(0.7225, abs=1e-3) and hi == pytest.approx(1.0)
    assert A.wilson(0, 0) == (None, None)


def test_pass_k_known_values():
    assert A.pass_hat(2, 4, 1) == pytest.approx(0.5)
    assert A.pass_hat(2, 4, 2) == pytest.approx(1 / 6)
    assert A.pass_hat(4, 4, 4) == 1.0 and A.pass_hat(3, 4, 4) == 0.0
    assert A.pass_hat(2, 4, 5) is None
    assert A.pass_at(2, 4, 1) == pytest.approx(0.5)
    assert A.pass_at(2, 4, 3) == pytest.approx(1.0)
    assert A.pass_at(0, 4, 2) == 0.0
    pk = A.pass_k_for({"T01": [1, 1, 1, 1], "T02": [1, 0, 0, 0]})
    assert pk[1]["pass_hat"] == pytest.approx((1 + 0.25) / 2)
    assert pk[4]["pass_hat"] == pytest.approx(0.5)


def test_equivalence_verdict():
    assert A.equivalence_verdict((-0.05, 0.08), (-0.07, 0.10)) == "equivalent"
    assert A.equivalence_verdict((-0.12, 0.05), (-0.15, 0.08)) == "inconclusive"
    assert A.equivalence_verdict((0.15, 0.30), (0.12, 0.33)) == "different"
    assert A.equivalence_verdict((-0.30, -0.15), (-0.33, -0.12)) == "different"
    assert A.equivalence_verdict((-0.05, 0.05), (-0.06, 0.06)) == "equivalent"
    assert A.equivalence_verdict(None, None) == "n/a"


# ---------------------------------------------------------------- cost
def usage(**kw):
    u = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reported_cost_usd": None,
        "per_turn_input_tokens": None,
    }
    u.update(kw)
    return u


def test_cost_basic_sonnet_with_cache():
    u = usage(
        input_tokens=1_000_000,
        cache_read_tokens=400_000,
        cache_write_tokens=100_000,
        output_tokens=100_000,
    )
    lo, hi, bounded = A.token_cost(u, "claude-sonnet-5-5", PRICES)
    # 600k*2 + 400k*0.2 + 100k*2.5 = 1.2+0.08+0.25 = 1.53 ; out 0.1M*10 = 1.0
    assert lo == pytest.approx(2.53) and hi == lo and not bounded


def test_cost_prefix_stripped_and_reasoning_flag():
    u = usage(input_tokens=1_000_000, output_tokens=100_000, reasoning_tokens=50_000)
    a = A.token_cost(u, "openai/gpt-6-luna", PRICES)
    assert a[0] == pytest.approx(0.1 + 0.05)  # reasoning included in output by default
    import copy

    p2 = copy.deepcopy(PRICES)
    p2["providers"]["openai"]["reasoning_billed_separately"] = True
    assert A.token_cost(u, "gpt-6-luna", p2)[0] == pytest.approx(0.1 + 0.075)


def test_cost_haiku_tier_per_turn():
    # two turns of 150k (above 100k threshold) and 50k (below); total input 200k, output 10k
    u = usage(
        input_tokens=200_000,
        output_tokens=10_000,
        per_turn_input_tokens=[150_000, 50_000],
    )
    lo, hi, bounded = A.token_cost(u, "claude-haiku-5-5", PRICES)
    base_in, base_out = 200_000 * 0.10 / 1e6, 10_000 * 0.50 / 1e6
    exp = 0.75 * (base_in * 5 + base_out * 5) + 0.25 * (base_in + base_out)
    assert lo == pytest.approx(exp) and hi == lo and not bounded


def test_cost_bounded_flag():
    u = usage(
        input_tokens=500_000, output_tokens=10_000
    )  # per_turn null, total > threshold
    lo, hi, bounded = A.token_cost(u, "anthropic/claude-haiku-5-5", PRICES)
    assert (
        bounded
        and lo == pytest.approx(0.05 + 0.005)
        and hi == pytest.approx(0.25 + 0.025)
    )
    u = usage(
        input_tokens=50_000, output_tokens=10_000
    )  # total below threshold: not bounded
    lo, hi, bounded = A.token_cost(u, "claude-haiku-5-5", PRICES)
    assert not bounded and lo == hi
    # OpenAI doubling above 272k, bounded when per-turn unknown
    lo, hi, bounded = A.token_cost(usage(input_tokens=300_000), "gpt-6-astra", PRICES)
    assert bounded and hi == pytest.approx(2 * lo)


def test_trial_cost_reported_with_estimate():
    r = {
        "model": "claude-sonnet-5-5",
        "usage": usage(input_tokens=1_000_000, reported_cost_usd=1.23),
    }
    c = A.trial_cost(r, PRICES)
    assert c["cost_lo"] == 1.23 and c["reported"] and c["est_lo"] == pytest.approx(2.0)
    assert (
        A.trial_cost({"model": "unknown-model", "usage": usage(input_tokens=1)}, PRICES)
        is None
    )


# ---------------------------------------------------------------- exclusions
def test_model_mismatch_rules():
    mm = A.model_mismatch
    assert not mm({"model": "claude-haiku-5-5", "observed_model": None})
    assert not mm(
        {"model": "anthropic/claude-haiku-5-5", "observed_model": "claude-haiku-5-5"}
    )
    assert not mm(
        {
            "model": "claude-haiku-5-5",
            "observed_model": "anthropic/claude-haiku-5-5-20261001",
        }
    )
    assert mm({"model": "claude-haiku-5-5", "observed_model": "claude-sonnet-5-5"})
    assert mm({"model": "gpt-6-luna", "observed_model": "openai/gpt-6.1-sol"})


def test_exclusions_on_sample(outdir):
    text = (outdir / "SUMMARY.md").read_text()
    assert "claude-sonnet-5-5" in text and "dropped" in text  # model mismatch listed
    assert "gpt-6-astra-20261001" not in text  # dated suffix kept, not dropped
    lb = read_csv(outdir / "leaderboard.csv")
    assert not [
        r for r in lb if r["model"] == "gpt-6-luna" and r["variant"] == "naive"
    ]  # failed BASE
    assert [r for r in lb if r["model"] == "gpt-6-luna" and r["variant"] == "good"]
    opus_good = next(
        r for r in lb if r["model"] == "claude-opus-5-5" and r["variant"] == "good"
    )
    assert int(opus_good["n_trials"]) == 13 * 4 - 1  # mismatched trial dropped
    assert int(opus_good["n_valid"]) <= int(opus_good["n_trials"])
    raw = [json.loads(x) for x in FIXTURE.read_text().splitlines()]
    he = sum(
        1
        for r in raw
        if r["task"] != "BASE"
        and r["model"] == "claude-haiku-5-5"
        and r["harness"] == "claude_code"
        and r["variant"] == "good"
        and r["state"] == "harness_error"
    )
    h = next(
        r
        for r in lb
        if r["model"] == "claude-haiku-5-5"
        and r["harness"] == "claude_code"
        and r["variant"] == "good"
    )
    assert float(h["harness_error_rate"]) == pytest.approx(
        he / int(h["n_trials"]), abs=1e-6
    )
    assert int(h["n_valid"]) == int(h["n_trials"]) - he - sum(
        1
        for r in raw
        if r["task"] != "BASE"
        and r["model"] == "claude-haiku-5-5"
        and r["harness"] == "claude_code"
        and r["variant"] == "good"
        and r["state"] == "timeout"
    )


def test_unit_prepare_excludes_failed_base():
    recs = [
        {
            "task": "BASE",
            "variant": "good",
            "harness": "h",
            "model": "m",
            "version": "1",
            "state": "timeout",
        },
        {
            "task": "T01",
            "variant": "good",
            "harness": "h",
            "model": "m",
            "version": "1",
            "state": "success",
        },
        {
            "task": "T01",
            "variant": "naive",
            "harness": "h",
            "model": "m",
            "version": "1",
            "state": "success",
        },
        {
            "task": "T01",
            "variant": "naive",
            "harness": "h",
            "model": "m",
            "version": "1",
            "state": "success",
            "observed_model": "other",
        },
    ]
    live, info = A.prepare(recs)
    assert (
        list(live) == [(("h", "m", "1"), "naive")]
        and len(live[(("h", "m", "1"), "naive")]) == 1
    )
    assert info["model_dropped"][("h", "m", "1")] == 1
    assert list(info["excluded_cells"]) == [(("h", "m", "1"), "good")]


# ---------------------------------------------------------------- outputs
def test_expected_files_and_sanity(outdir):
    for f in [
        "SUMMARY.md",
        "leaderboard.csv",
        "by_task.csv",
        "pass_k.json",
        "failures.csv",
        "costs.csv",
        "gaps.json",
        "ablations.csv",
    ]:
        assert (outdir / f).exists(), f
    lb = read_csv(outdir / "leaderboard.csv")
    for r in lb:
        assert float(r["ci95_lo"]) <= float(r["success_rate"]) <= float(r["ci95_hi"])
    costs = read_csv(outdir / "costs.csv")
    cc = next(
        r
        for r in costs
        if r["harness"] == "claude_code"
        and r["variant"] == "good"
        and r["model"] == "claude-opus-5-5"
    )
    assert int(cc["reported_cost_trials"]) > 0 and float(cc["reported_over_est"]) > 0
    haiku = next(r for r in costs if r["harness"] == "pi" and r["variant"] == "good")
    assert int(haiku["bounded_trials"]) > 0
    gaps = json.loads((outdir / "gaps.json").read_text())
    assert any(p["kind"] == "headline" and p["status"] == "ok" for p in gaps["pairs"])
    assert {p["verdict"] for p in gaps["pairs"] if p["status"] == "ok"} <= {
        "equivalent",
        "different",
        "inconclusive",
    }
    ab = read_csv(outdir / "ablations.csv")
    assert any(
        r["scope"] == "small_mean" and r["rank_by_mean_small_delta"] == "1" for r in ab
    )
    assert read_csv(outdir / "pi_control.csv")


def test_determinism(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    A.main(["--records", str(FIXTURE), "--out", str(a), "--resamples", "200"])
    A.main(["--records", str(FIXTURE), "--out", str(b), "--resamples", "200"])
    names = sorted(p.name for p in a.iterdir())
    assert names == sorted(p.name for p in b.iterdir()) and len(names) >= 8
    for n in names:
        assert (a / n).read_bytes() == (b / n).read_bytes(), n
    # shuffled input order -> same CSV/JSON (sorting is stable)
    lines = FIXTURE.read_text().splitlines()
    random.Random(1).shuffle(lines)
    sh = tmp_path / "shuf.jsonl"
    sh.write_text("\n".join(lines) + "\n")
    c = tmp_path / "c"
    A.main(["--records", str(sh), "--out", str(c), "--resamples", "200"])
    for n in [
        "leaderboard.csv",
        "by_task.csv",
        "pass_k.json",
        "failures.csv",
        "gaps.json",
        "ablations.csv",
    ]:
        assert (a / n).read_bytes() == (c / n).read_bytes(), n


def test_variants_filter(tmp_path):
    A.main(
        [
            "--records",
            str(FIXTURE),
            "--out",
            str(tmp_path),
            "--variants",
            "good",
            "--resamples",
            "50",
        ]
    )
    assert {r["variant"] for r in read_csv(tmp_path / "leaderboard.csv")} == {"good"}


def test_bootstrap_clustered_basic():
    cfg_a = {"T1": [1, 1, 1, 1], "T2": [1, 1, 1, 1]}
    cfg_b = {"T1": [0, 0, 0, 0], "T2": [0, 0, 0, 0]}
    point, smp, nt = A.bootstrap([cfg_a, cfg_b], lambda r: r[0] - r[1], 100, "x", 0)
    assert point == 1.0 and set(smp) == {1.0} and nt == 2
