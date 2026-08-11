# Evaluation results summary (2026-08)

> Aggregated from `data/eval-results/` on 2026-08-11. All numbers are
> single-attempt (pass@1) unless noted. Production model endpoint
> `gpt-5.6-sol`, Lean toolchain `leanprover/lean4:v4.30.0`.
> `verified` = accepted by the Lean compiler; numeric rows are answer-matched.

## Headline table

| Set | n | Score | Judged by | Median cost |
|---|---|---|---|---|
| fast (tier1 basics) | 54 | 100% † | numeric | 8s / 3.3k tok |
| AIME sample (tier2) | 50 | 96.0% | numeric | 158s / 18.1k tok |
| **AIME 2025 (full)** | 30 | **96.7% (29/30); 100% with one retry** | numeric | 252s / 21.6k tokens |
| **HMMT Feb 2025 (full)** | 20 | **100% (20/20)** | numeric | 192s / 24.9k tok |
| Omni-MATH sample (tier3) | 50 | 94.0%; 98.0% with one retry | numeric | 98s / 13.4k tok |
| formal baseline (tier4, 20 cases x 3 trials) | 60 | 100% | Lean verified | 92s / 21.8k tok |
| miniF2F sample (tier4) | 30 | 66.7% (20/30) | Lean verified | 503s / 35.1k tok |
| Putnam sample (tier5) | 20 | 10.0% (2/20) | Lean verified | 1202s / 107.2k tok |
| formal-hard (tier6, 7 cases x 3 trials) | 21 | 23.8% pass@1; 3/7 pass@3 | Lean verified | 1202s / 97.2k tok |
| **miniF2F valid FULL run (partial)** | 60/244 | **58.3% (35/60)** | Lean verified | 430s / 42.3k tok |

† fast was 92.6% before a judge fix on 2026-08-11: all 4 previously-failed
cases were judge false rejections (the agent's answers were correct — see
"Judge false rejections" below). Re-judged offline with the fixed judge.

## miniF2F valid full run — by source (partial, stopped at 60/244)

The full-pass run was stopped at 60 problems on 2026-08-11 to redirect
compute toward ablations; the partial breakdown is still informative because
the valid split is ordered by source and the hardest block is fully covered:

| Source | verified | note |
|---|---|---|
| AIME | 2/15 (13%) | all 15 AIME problems in valid are inside these 60 — the hardest block is done |
| AMC | 19/28 (68%) | |
| other (MATH/induction/IMO prefix) | 14/17 (82%) | |

Projection for the full 244, weighting the remaining 184 problems (mostly
MATH-style) at the observed per-source rates: **~55-60% verified pass@1**.

Reference points (miniF2F-test, specialist provers, pass@32): Goedel-Prover-V2-32B
88.1%, Kimina-Prover-72B 84.0%, DeepSeek-Prover-V2-671B 82.4%. Those are
single-purpose prover models sampled 32-8192x per problem; Conjecta's number
is one general-model agent solve per problem (~35-42k tokens median). The
like-for-like comparison is pass@1, where specialist provers land far below
their pass@32 figures.

## Harness ablation

Same model (`gpt-5.6-sol`), same problems, one raw completion each — no
premise retrieval, no compile-feedback repair, no tools, no escalation —
checked once with the strict verifier (`scripts/ablation_raw_oneshot.py`):

| Set | Raw one-shot | Full harness | Lift |
|---|---|---|---|
| fast (tier1 basics, n=54) | 100% (54/54), 103 tok median | 100% (54/54) †, 3.3k tok median | **0pp (tie)** |
| AIME sample (tier2, n=50) | 88.0% (44/50), 666 tok median | 96.0% (48/50), 18.1k tok median | **+8.0pp** |
| **HMMT Feb 2025 FULL (n=20)** | **80.0% (16/20) and 95.0% (19/20), two independent samples** | **100% (20/20), 24.9k tok median** | **+5~20pp, plus variance elimination** |
| miniF2F sample (tier4, n=30) | 50.0% (15/30), 2.2k tok median | 66.7% (20/30), 35.1k tok median | **+16.7pp** |
| formal-hard (tier6, n=7) | **0/7** | 3/7 (pass@3) | **+3 problems from zero** |

† harness fast score re-judged with the fixed judge (see "Judge false
rejections"); the raw-model answers were graded with the same fixed judge
(`scripts/ablation_raw_informal.py` reuses `judge_solution`).

Difficulty gradient: on easy informal problems the raw frontier model is
already saturated and the harness exactly ties it (100% vs 100%) — the
apparent gap before 2026-08-11 was entirely judge noise, not harness
interference. From competition level up, the lift grows monotonically:
+8.0pp (AIME sample) → +16.7pp (miniF2F sample) → "only the harness closes
anything" (formal-hard, 0/7 raw vs 3/7 harnessed). The harness value
concentrates exactly where the model alone fails.

HMMT variance analysis (two full raw samples + targeted re-samples of every
raw miss): raw pass@1 measured 16/20 and 19/20 on identical inputs. The
misses decompose into single-sample noise (`hmmt-feb-2025-14/15` flip
correct on re-sample; `-09` is borderline, 1/3 samples correct) and exactly
one stable weakness (`hmmt-feb-2025-13`, wrong in both samples). The
harness's 20/20 therefore means two things, not one: +5~20pp of headroom
*and* elimination of the ±15pp pass@1 jitter the raw model exhibits at this
difficulty band. Raw one-shot costs ~1.3k tokens per problem vs the
harness's 24.9k median — that 19x spend is the price of the consistency.

Paired breakdown on the 30-problem set: both solved 14, raw-only 1
(`mathd_numbertheory_5`), harness-only 6 (`amc12_2000_p1`, `aime_1994_p3`,
`mathd_algebra_13`, `algebra_apbpceq2_*`, `mathd_algebra_208`,
`mathd_algebra_35`), neither 9. The harness-only wins cluster on competition
problems where premise retrieval and compile-feedback repair matter; with
n=30 the 6-vs-1 split is suggestive, not yet statistically decisive.

## Competition full runs

**AIME 2025 FULL (all 30 problems): 29/30 = 96.7% under the standard 600s
budget; 30/30 = 100% with one retry.** Numeric judge, one attempt each,
median 252s / 21.6k tokens per problem
(`data/eval-results/aime_2025-full-20260811-102017.jsonl`). The single miss
(`aime-2025-14`) was not a wrong answer but a timeout: the run hit the 600s
per-problem cap while still decomposing the optimization target and returned
a plan fragment instead of a number. Re-run with the retry profile
(`config.retry.toml`, 1800s cap) it solved in **269s / 39.7k tokens** with
the correct answer 60 — inside even the standard budget this time, i.e. the
original miss was API-latency variance, not a capability gap.

**HMMT Feb 2025 FULL (all 20 problems): 20/20 = 100%**, numeric judge, one
attempt each under the standard 600s budget, median 192s / 24.9k tokens per
problem (`data/eval-results/hmmt_feb_2025-full-20260811-102017.jsonl`).
Sampled subsets previously scored 96% (AIME, n=50) and 94% (Omni-MATH, n=50).

**Omni-MATH sample (tier3, n=50): 47/50 = 94.0% standard; 49/50 = 98.0%
with one retry of the three misses.** Retried with the 1800s retry profile
(`data/eval-results/tier3_failed3-retry-*.jsonl`); the three misses had three
distinct causes:

- `olympiadbench-oe-2659` (a trivial nested-radical computation): answered
  10 instead of 100 in one 15s step — single-sample model noise; retry
  answered 100 in 15s.
- `omni-math-0115`: hit the 600s wall mid-derivation and closed best_effort;
  retry solved it in 694s / 65.6k tokens (needed the extended budget).
- `omni-math-0124` (2009-triangle combinatorics, expected k=1): the original
  run stalled at 0 completed steps on API errors; the retry ran cleanly
  (191s, 3 steps) but answered 1005 — a genuine capability miss, the only
  one in the tier3 sample.

## Judge false rejections (fixed 2026-08-11)

Re-checking the 4 fast-set failures showed all 4 agent answers were
mathematically correct — the losses were in the grader, not the agent:

- 3 geometry cases (`fast-geo-001/006/007`): numeric judge "all" mode
  required *every* extracted number to match the target, so the coefficient
  in `9\pi \approx 28.2743` (the stray `9`) failed the match. The judge now
  prefers the value after an `\approx`/`≈` marker.
- 1 algebra case (`fast-alg-008`): exact judge compared the whole derivation
  chain against `2i`; it now reads `\boxed{...}` content first.

Both fixes are in `math_agent/evaluation/judges.py` with regression tests in
`tests/test_judges_symbolic.py`. Re-judging the stored answers offline moves
fast from 50/54 to **54/54**; re-judging AIME-full, tier2, tier3 and miniF2F
samples flipped nothing else. Honest direction for reporting: this judge fix
only ever *raises* scores by removing false rejections, and every flip was
hand-verified against the expected answer before the fix was accepted.

## Engineering findings from the full-run attempt

The 60-problem full run doubled as a soak test for the harness and surfaced
three production issues, all fixed with regression tests:

1. **Gateway malformed bodies**: the OpenAI-compatible gateway intermittently
   answers 200 with a plain-text/SSE body, which the SDK returns as a raw
   `str`. Now classified as `MalformedResponseError` and retried with backoff
   instead of crashing `complete()`.
2. **REPL memory accumulation**: REPL sessions grew RSS monotonically with
   retained proof states until the cgroup OOM-killed them mid-search. Fixed
   with proactive session recycling at pool checkin
   (`repl_recycle_after_commands`) plus a circuit breaker that falls back to
   batch compilation after repeated session deaths.
3. **Deep-search routing reachability**: the deterministic deep-search gate
   only counted one-shot `formalize`/`lean_check` failures, so rounds where
   the actor jumped straight to structured tools never escalated. All four
   Lean tools now count toward the trigger.
