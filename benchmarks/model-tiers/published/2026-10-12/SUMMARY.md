# Model tiers analysis

Records: 2616 read (0 malformed lines skipped), 2548 trials, 68 BASE; 2548 trials kept in 32 (column, variant) cells; bootstrap seed 0, 2000 resamples.

## Leaderboard: good

| column | n | succ | success [95% Wilson] | pass^1 | pass^4 | $/success | med turns | med latency s | harness err | timeout |
|---|---|---|---|---|---|---|---|---|---|---|
| claude_code/claude-opus-5-5@2.1.296 | 104 | 104 | 100.0% [96.4%, 100.0%] | 1.000 | 1.000 | $0.0814 | 1.0 | 18.0 | 0.0% | 0.0% |
| claude_code/claude-sonnet-5-5@2.1.296 | 104 | 104 | 100.0% [96.4%, 100.0%] | 1.000 | 1.000 | $0.0369 | 1.0 | 13.1 | 0.0% | 0.0% |
| claude_code/claude-haiku-5-5@2.1.296 | 104 | 103 | 99.0% [94.8%, 99.8%] | 0.990 | 0.962 | $0.0027 | 1.0 | 12.8 | 0.0% | 0.0% |
| codex/gpt-6.1-sol@0.162.1 | 102 | 101 | 99.0% [94.7%, 99.8%] | 0.989 | 0.956 | $0.0605 | 1.0 | 37.7 | 1.9% | 0.0% |
| codex/gpt-6-astra@0.162.1 | 104 | 100 | 96.2% [90.5%, 98.5%] | 0.962 | 0.924 | $0.3065 | 1.0 | 27.9 | 0.0% | 0.0% |
| codex/gpt-6-luna@0.162.1 | 104 | 89 | 85.6% [77.6%, 91.1%] | 0.856 | 0.649 | $0.0036 | 1.0 | 21.7 | 0.0% | 0.0% |

## Leaderboard: naive

| column | n | succ | success [95% Wilson] | pass^1 | pass^4 | $/success | med turns | med latency s | harness err | timeout |
|---|---|---|---|---|---|---|---|---|---|---|
| claude_code/claude-opus-5-5@2.1.296 | 104 | 103 | 99.0% [94.8%, 99.8%] | 0.990 | 0.962 | $0.2749 | 1.0 | 32.4 | 0.0% | 0.0% |
| codex/gpt-6-astra@0.162.1 | 104 | 103 | 99.0% [94.8%, 99.8%] | 0.990 | 0.962 | $0.5743 | 1.0 | 40.5 | 0.0% | 0.0% |
| codex/gpt-6.1-sol@0.162.1 | 100 | 95 | 95.0% [88.8%, 97.8%] | 0.952 | 0.923 | $0.1299 | 1.0 | 55.0 | 3.8% | 0.0% |
| claude_code/claude-sonnet-5-5@2.1.296 | 104 | 71 | 68.3% [58.8%, 76.4%] | 0.683 | 0.522 | $0.1441 | 1.0 | 21.5 | 0.0% | 0.0% |
| claude_code/claude-haiku-5-5@2.1.296 | 104 | 67 | 64.4% [54.9%, 73.0%] | 0.644 | 0.451 | $0.0110 | 1.0 | 24.3 | 0.0% | 0.0% |
| codex/gpt-6-luna@0.162.1 | 104 | 52 | 50.0% [40.6%, 59.4%] | 0.500 | 0.353 | $0.0119 | 1.0 | 28.8 | 0.0% | 0.0% |

## Leaderboard: good-D

| column | n | succ | success [95% Wilson] | pass^1 | pass^4 | $/success | med turns | med latency s | harness err | timeout |
|---|---|---|---|---|---|---|---|---|---|---|
| codex/gpt-6.1-sol@0.162.1 | 65 | 64 | 98.5% [91.8%, 99.7%] | 0.985 | 0.938 | $0.0669 | 1.0 | 32.9 | 0.0% | 0.0% |
| claude_code/claude-haiku-5-5@2.1.296 | 65 | 62 | 95.4% [87.3%, 98.4%] | 0.954 | 0.862 | $0.0031 | 1.0 | 15.1 | 0.0% | 0.0% |
| claude_code/claude-sonnet-5-5@2.1.296 | 65 | 61 | 93.8% [85.2%, 97.6%] | 0.938 | 0.923 | $0.0400 | 1.0 | 14.0 | 0.0% | 0.0% |
| codex/gpt-6-luna@0.162.1 | 65 | 53 | 81.5% [70.4%, 89.1%] | 0.815 | 0.569 | $0.0042 | 1.0 | 21.8 | 0.0% | 0.0% |

## Leaderboard: good-C

| column | n | succ | success [95% Wilson] | pass^1 | pass^4 | $/success | med turns | med latency s | harness err | timeout |
|---|---|---|---|---|---|---|---|---|---|---|
| claude_code/claude-haiku-5-5@2.1.296 | 65 | 64 | 98.5% [91.8%, 99.7%] | 0.985 | 0.938 | $0.0035 | 1.0 | 14.9 | 0.0% | 0.0% |
| claude_code/claude-sonnet-5-5@2.1.296 | 65 | 64 | 98.5% [91.8%, 99.7%] | 0.985 | 0.938 | $0.0451 | 1.0 | 15.1 | 0.0% | 0.0% |
| codex/gpt-6.1-sol@0.162.1 | 64 | 62 | 96.9% [89.3%, 99.1%] | 0.969 | 0.923 | $0.0811 | 1.0 | 38.5 | 1.5% | 0.0% |
| codex/gpt-6-luna@0.162.1 | 65 | 53 | 81.5% [70.4%, 89.1%] | 0.815 | 0.600 | $0.0053 | 1.0 | 28.9 | 0.0% | 0.0% |

## Leaderboard: good-S

| column | n | succ | success [95% Wilson] | pass^1 | pass^4 | $/success | med turns | med latency s | harness err | timeout |
|---|---|---|---|---|---|---|---|---|---|---|
| claude_code/claude-sonnet-5-5@2.1.296 | 65 | 65 | 100.0% [94.4%, 100.0%] | 1.000 | 1.000 | $0.0404 | 1.0 | 14.9 | 0.0% | 0.0% |
| codex/gpt-6.1-sol@0.162.1 | 64 | 63 | 98.4% [91.7%, 99.7%] | 0.985 | 0.938 | $0.0658 | 1.0 | 38.0 | 1.5% | 0.0% |
| claude_code/claude-haiku-5-5@2.1.296 | 64 | 62 | 96.9% [89.3%, 99.1%] | 0.969 | 0.923 | $0.0034 | 1.0 | 15.2 | 1.5% | 0.0% |
| codex/gpt-6-luna@0.162.1 | 65 | 54 | 83.1% [72.2%, 90.3%] | 0.831 | 0.569 | $0.0034 | 1.0 | 24.1 | 0.0% | 0.0% |

## Leaderboard: good-E

| column | n | succ | success [95% Wilson] | pass^1 | pass^4 | $/success | med turns | med latency s | harness err | timeout |
|---|---|---|---|---|---|---|---|---|---|---|
| claude_code/claude-sonnet-5-5@2.1.296 | 65 | 65 | 100.0% [94.4%, 100.0%] | 1.000 | 1.000 | $0.0363 | 1.0 | 13.2 | 0.0% | 0.0% |
| codex/gpt-6.1-sol@0.162.1 | 62 | 62 | 100.0% [94.2%, 100.0%] | 1.000 | - | $0.0613 | 1.0 | 34.5 | 4.6% | 0.0% |
| claude_code/claude-haiku-5-5@2.1.296 | 64 | 61 | 95.3% [87.1%, 98.4%] | 0.954 | 0.862 | $0.0029 | 1.0 | 14.2 | 1.5% | 0.0% |
| codex/gpt-6-luna@0.162.1 | 65 | 52 | 80.0% [68.7%, 87.9%] | 0.800 | 0.585 | $0.0038 | 1.0 | 21.2 | 0.0% | 0.0% |

## Leaderboard: good-P

| column | n | succ | success [95% Wilson] | pass^1 | pass^4 | $/success | med turns | med latency s | harness err | timeout |
|---|---|---|---|---|---|---|---|---|---|---|
| codex/gpt-6.1-sol@0.162.1 | 65 | 63 | 96.9% [89.5%, 99.2%] | 0.969 | 0.923 | $0.0772 | 1.0 | 40.6 | 0.0% | 0.0% |
| claude_code/claude-sonnet-5-5@2.1.296 | 64 | 61 | 95.3% [87.1%, 98.4%] | 0.946 | 0.862 | $0.1125 | 1.0 | 19.1 | 1.5% | 0.0% |
| claude_code/claude-haiku-5-5@2.1.296 | 64 | 55 | 85.9% [75.4%, 92.4%] | 0.858 | 0.585 | $0.0084 | 1.0 | 19.9 | 1.5% | 0.0% |
| codex/gpt-6-luna@0.162.1 | 65 | 42 | 64.6% [52.5%, 75.1%] | 0.646 | 0.431 | $0.0071 | 1.0 | 26.7 | 0.0% | 0.0% |

`*` = cost bounded (no per-turn token data for some trials; point = midpoint of bounds, see costs.csv). n excludes harness_error/timeout/grader_error/classifier_error.

## Pair comparisons (task-clustered bootstrap)

| kind | comparison (a vs b) | a | b | diff | 90% CI | 95% CI | verdict |
|---|---|---|---|---|---|---|---|
| same_price | claude-haiku-5-5 vs gpt-6-luna [good] | 99.0% | 85.6% | 0.135 | [0.038, 0.250] | [0.029, 0.279] | different |
| same_price | claude-sonnet-5-5 vs gpt-6.1-sol [good] | 100.0% | 98.9% | 0.011 | [0.000, 0.044] | [0.000, 0.055] | equivalent |
| tier_step | claude-sonnet-5-5 - claude-haiku-5-5 [good] | 100.0% | 99.0% | 0.010 | [0.000, 0.038] | [0.000, 0.048] | equivalent |
| tier_step | claude-opus-5-5 - claude-sonnet-5-5 [good] | 100.0% | 100.0% | 0.000 | [0.000, 0.000] | [0.000, 0.000] | equivalent |
| tier_step | gpt-6.1-sol - gpt-6-luna [good] | 98.9% | 85.6% | 0.133 | [0.036, 0.246] | [0.018, 0.269] | different |
| tier_step | gpt-6-astra - gpt-6.1-sol [good] | 96.2% | 98.9% | -0.027 | [-0.087, 0.000] | [-0.111, 0.003] | equivalent |
| same_price | claude-haiku-5-5 vs gpt-6-luna [naive] | 64.4% | 50.0% | 0.144 | [0.038, 0.269] | [0.019, 0.289] | different |
| same_price | claude-sonnet-5-5 vs gpt-6.1-sol [naive] | 68.3% | 95.2% | -0.269 | [-0.442, -0.106] | [-0.481, -0.087] | different |
| tier_step | claude-sonnet-5-5 - claude-haiku-5-5 [naive] | 68.3% | 64.4% | 0.038 | [-0.058, 0.125] | [-0.077, 0.154] | inconclusive |
| tier_step | claude-opus-5-5 - claude-sonnet-5-5 [naive] | 99.0% | 68.3% | 0.308 | [0.144, 0.481] | [0.115, 0.510] | different |
| tier_step | gpt-6.1-sol - gpt-6-luna [naive] | 95.2% | 50.0% | 0.452 | [0.269, 0.635] | [0.240, 0.673] | different |
| tier_step | gpt-6-astra - gpt-6.1-sol [naive] | 99.0% | 95.2% | 0.038 | [0.000, 0.115] | [0.000, 0.135] | inconclusive |
| same_price | claude-haiku-5-5 vs gpt-6-luna [good-D] | 95.4% | 81.5% | 0.138 | [0.015, 0.277] | [-0.015, 0.308] | inconclusive |
| same_price | claude-sonnet-5-5 vs gpt-6.1-sol [good-D] | 93.8% | 98.5% | -0.046 | [-0.154, 0.046] | [-0.200, 0.062] | inconclusive |
| tier_step | claude-sonnet-5-5 - claude-haiku-5-5 [good-D] | 93.8% | 95.4% | -0.015 | [-0.092, 0.046] | [-0.123, 0.062] | equivalent |
| tier_step | gpt-6.1-sol - gpt-6-luna [good-D] | 98.5% | 81.5% | 0.169 | [0.046, 0.308] | [0.031, 0.338] | different |
| same_price | claude-haiku-5-5 vs gpt-6-luna [good-C] | 98.5% | 81.5% | 0.169 | [0.031, 0.323] | [0.015, 0.354] | different |
| same_price | claude-sonnet-5-5 vs gpt-6.1-sol [good-C] | 98.5% | 96.9% | 0.015 | [-0.046, 0.092] | [-0.046, 0.108] | equivalent |
| tier_step | claude-sonnet-5-5 - claude-haiku-5-5 [good-C] | 98.5% | 98.5% | 0.000 | [-0.046, 0.046] | [-0.062, 0.062] | equivalent |
| tier_step | gpt-6.1-sol - gpt-6-luna [good-C] | 96.9% | 81.5% | 0.154 | [0.000, 0.323] | [-0.031, 0.354] | inconclusive |
| same_price | claude-haiku-5-5 vs gpt-6-luna [good-S] | 96.9% | 83.1% | 0.138 | [0.031, 0.262] | [0.015, 0.277] | different |
| same_price | claude-sonnet-5-5 vs gpt-6.1-sol [good-S] | 100.0% | 98.5% | 0.015 | [0.000, 0.062] | [0.000, 0.062] | equivalent |
| tier_step | claude-sonnet-5-5 - claude-haiku-5-5 [good-S] | 100.0% | 96.9% | 0.031 | [0.000, 0.092] | [0.000, 0.108] | equivalent |
| tier_step | gpt-6.1-sol - gpt-6-luna [good-S] | 98.5% | 83.1% | 0.154 | [0.046, 0.277] | [0.031, 0.308] | different |
| same_price | claude-haiku-5-5 vs gpt-6-luna [good-E] | 95.4% | 80.0% | 0.154 | [0.000, 0.323] | [-0.031, 0.354] | inconclusive |
| same_price | claude-sonnet-5-5 vs gpt-6.1-sol [good-E] | 100.0% | 100.0% | 0.000 | [0.000, 0.000] | [0.000, 0.000] | equivalent |
| tier_step | claude-sonnet-5-5 - claude-haiku-5-5 [good-E] | 100.0% | 95.4% | 0.046 | [0.000, 0.123] | [0.000, 0.138] | inconclusive |
| tier_step | gpt-6.1-sol - gpt-6-luna [good-E] | 100.0% | 80.0% | 0.200 | [0.077, 0.354] | [0.062, 0.385] | different |
| same_price | claude-haiku-5-5 vs gpt-6-luna [good-P] | 85.8% | 64.6% | 0.212 | [0.008, 0.415] | [-0.031, 0.462] | inconclusive |
| same_price | claude-sonnet-5-5 vs gpt-6.1-sol [good-P] | 94.6% | 96.9% | -0.023 | [-0.115, 0.077] | [-0.135, 0.092] | inconclusive |
| tier_step | claude-sonnet-5-5 - claude-haiku-5-5 [good-P] | 94.6% | 85.8% | 0.088 | [0.008, 0.188] | [-0.008, 0.212] | inconclusive |
| tier_step | gpt-6.1-sol - gpt-6-luna [good-P] | 96.9% | 64.6% | 0.323 | [0.123, 0.523] | [0.092, 0.569] | different |
| headline | claude-haiku-5-5 (good) vs claude-opus-5-5 (naive) | 99.0% | 99.0% | 0.000 | [-0.019, 0.019] | [-0.029, 0.029] | equivalent |
| headline | gpt-6-luna (good) vs gpt-6-astra (naive) | 85.6% | 99.0% | -0.135 | [-0.240, -0.048] | [-0.260, -0.029] | different |

Verdict: equivalent if 90% CI within +/-0.10; different if 95% CI excludes 0; else inconclusive. diff = a - b.

## Gap closed

| small | big | small good | small naive | big good | gap closed | 95% CI |
|---|---|---|---|---|---|---|
| claude-haiku-5-5 | claude-opus-5-5 | 99.0% | 64.4% | 100.0% | 0.973 | [0.857, 1.000] |
| gpt-6-luna | gpt-6-astra | 85.6% | 50.0% | 96.2% | 0.771 | [0.549, 1.215] |

gap closed = (small_good - small_naive) / (big_good - small_naive).

## Ablation ranking (mean delta vs good on small native columns; most harmful first)

| rank | variant | dimension | mean delta | small columns |
|---|---|---|---|---|
| 1 | good-P | pagination/output | -0.171 | 2 |
| 2 | good-E | errors | -0.046 | 2 |
| 3 | good-D | descriptions | -0.038 | 2 |
| 4 | good-C | catalog | -0.023 | 2 |
| 5 | good-S | schema | -0.023 | 2 |

## Exclusions

No trials dropped for observed_model mismatch.

No cells excluded for BASE failure.

Trials excluded from the success denominator:

| column | variant | harness_error | timeout | grader_error | classifier_error |
|---|---|---|---|---|---|
| codex/gpt-6.1-sol@0.162.1 | good | 2 | 0 | 0 | 0 |
| codex/gpt-6.1-sol@0.162.1 | naive | 4 | 0 | 0 | 0 |
| codex/gpt-6.1-sol@0.162.1 | good-C | 1 | 0 | 0 | 0 |
| claude_code/claude-haiku-5-5@2.1.296 | good-S | 1 | 0 | 0 | 0 |
| codex/gpt-6.1-sol@0.162.1 | good-S | 1 | 0 | 0 | 0 |
| claude_code/claude-haiku-5-5@2.1.296 | good-E | 1 | 0 | 0 | 0 |
| codex/gpt-6.1-sol@0.162.1 | good-E | 3 | 0 | 0 | 0 |
| claude_code/claude-haiku-5-5@2.1.296 | good-P | 1 | 0 | 0 | 0 |
| claude_code/claude-sonnet-5-5@2.1.296 | good-P | 1 | 0 | 0 | 0 |
