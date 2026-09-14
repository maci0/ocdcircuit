# Dense-demo experiment (round-1 review Priority 3) — pico_tmc2209, 20 parts / 31 nets

Date: round 2. Method: `fix` released (as-shipped positions are golden
reference only), place seeds=3 iters=400 × 2 seeds, then route. Scripts were
throwaway (/tmp/dense_exp*.py) — rerun from this description.

## Placement vs golden (golden WL = 2185, star-model Manhattan)

| placer | seed | time | WL | ratio vs golden | errors | warnings |
|---|---|---|---|---|---|---|
| diffusion | 0 | 2.96 s | 1461 | 0.67 | 0 | 0 |
| diffusion | 1 | 2.96 s | 1556 | 0.71 | 0 | 0 |
| compact | 0 | 3.09 s | 1254 | 0.57 | 5 | 0 |
| compact | 1 | 3.05 s | 1322 | 0.61 | 1 | 0 |
| thermal | 0 | 3.03 s | 3449 | 1.58 | 5 | 0 |
| thermal | 1 | 3.03 s | 2958 | 1.35 | 4 | 0 |

Caveat (bench.py lesson): golden is a human ported layout, not WL-optimal —
beating it on star-WL is expected, not a triumph. What IS earned: diffusion
finds zero-error layouts at 0.67–0.71× golden WL in ~3 s; compact goes shorter
but overlaps (area pressure without legalizer); thermal trades WL for spread
as designed. Presets behave as labeled.

## Routing on released diffusion layout (seed 0)

| router | segs | routed WL | time | errors | warnings |
|---|---|---|---|---|---|
| lroute | 114 | 1461 | 0.00 s | 0 | 26 (all clearance) |
| maze | 372 | 2363 | 21.9 s | 0 | 197 (airwire fallbacks on Z2_EN, Z1_STEP…) |

Earned: on this dense board maze costs 22 s, inflates WL 1.6×, and still
falls back to airwires on contested nets — while lroute is instant with 26
clearance warnings. The "maze = DRC-clean" story from blinky does NOT transfer
here. This bounds the rip-up verdict: 2nd pass is worth trying precisely
because fallback count (not warnings) is now the measurable gap.

## Verdicts earned (replace analogy with these)

1. "Greedy+repair works" → diffusion works *for placement* at n=20 dense
   (zero-error, beats golden WL). Repair/legalizer still unmeasured (nothing
   to repair — diffusion was already clean).
2. "2nd rip-up pass = biggest gain" → restated measurably: close the
   airwire-fallback gap on pico (currently ~197 maze warnings w/ fallbacks
   vs 26 lroute clearance warnings). Experiment: 2nd pass → count fallbacks.
3. "Maze = fewer DRC warnings" → FALSE on dense boards (197 vs 26). Maze wins
   only where it completes; completion rate, not warning count, is the metric.

## Baseline job status

`bench.py` background run (bash-341) was started pre-fix with stale defaults;
its output (baseline_r2.txt) is superseded — rerun `python -m benches.monster6502.bench` (now
defaults seeds=1 iters=5, prints golden WL + floor) for the headroom number.
