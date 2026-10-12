# Published results, 12 October 2026

Snapshot of the model-tiers runs behind the write-up. Harnesses: Claude Code 2.1.296 and Codex 0.162.1 (managed runtime). The headline run (good and naive servers, six models) is 8 trials per task; the one-flaw ablations (good-D, good-C, good-S, good-E, good-P on Sonnet, Haiku, Sol and Luna) are 5 trials per task. All records were graded with the final grader.

## Files

| File | What it is |
|---|---|
| `records.jsonl.gz` | 2616 trial records, one JSON object per line: the 1956 records from the main run and the Claude ablations (`results/final/records.jsonl`), followed by the 660 Codex ablation records (`results/codex-ablations/records.regraded.jsonl`). |
| `SUMMARY.md`, `leaderboard.csv`, `by_task.csv`, `pass_k.json`, `gaps.json`, `costs.csv`, `failures.csv`, `ablations.csv`, `pi_control.csv` | Output of `analyze.py` on all 2616 records. |

## Re-run

From `benchmarks/model-tiers`:

```sh
mkdir -p /tmp/mt && gunzip -c published/2026-10-12/records.jsonl.gz > /tmp/mt/records.jsonl
uv run python analyze.py --records /tmp/mt/records.jsonl --out /tmp/mt/analysis
```

The output is deterministic, so this reproduces the shipped files byte for byte.
