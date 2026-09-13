# Review: research corpus (round 2 of 3) — re-review after fixes

Scope: same corpus as round 1 (`outputs/our-corpus-review.md`), plus fixes
committed in `1e9e8f5` (round-2 corpus fixes) and `outputs/dense-demo-experiment.md`.
Method: lead-executed (no fan-out — fixes were surgical, verification is
diff-against-round-1). Credentials unchanged (no HF/AlphaXiv keys; no
paywalls). Monster baseline job still running at write time (fixed-harness
numbers pending — flagged below, not blocking).

## Summary Assessment (revision priority)

Round 1's 5 criticals: **4 closed, 1 partially closed**. Round 1's 12 majors:
**7 closed, 3 partially closed, 2 open** (both need blocked sources or new
code, correctly deferred). The corpus graduates from "honest but verdict-heavy"
to **"measured where it counts, flagged where it isn't"**. Remaining work is
bounded: fixed-harness baseline numbers, CP-SAT spike-or-scope, and the 2nd
rip-up experiment the dense demo now precisely bounds. **Recommend: trust the
placement verdicts and the scorecard conventions; hold the routing-gain and
exact-methods verdicts for round 3.**

## Strengths (new this round)

- bench.py is now a fair scorer: golden WL both sides, floor subtraction,
  similarity labeled secondary, defaults reproduce the baseline config.
- Dense-demo experiment earned three verdicts with numbers (diffusion
  0.67–0.71× golden, zero-error; maze airwire gap bounds rip-up work;
  "maze = fewer warnings" falsified on dense boards).
- Scorecard conventions are now normative and implementable (0..1 + RAW +
  None-semantics); veto removed; uncalibrated constants labeled as such.
- pour silent no-op → warn-only with passing tests (laziest correct fix).

## Critical Issues (round-1 → round-2 status)

1. **Golden WL missing → CLOSED.** bench.py computes + prints golden WL and
   placed/golden ratio (same star-model both sides).
2. **Floor unscored → CLOSED.** `golden_ov` baseline + `above_floor` metric;
   SOURCES limitation retained as documentation.
3. **Weights non-implementable → CLOSED.** Normalization conventions added;
   weights marked illustrative-only; veto removed.
4. **Architecture verdict analogy → PARTIALLY CLOSED.** Placement half now
   measured (diffusion zero-error, beats golden WL); routing-gain half still
   rests on judgment — but is now a bounded experiment (count fallbacks),
   not a superlative. CP-SAT alternative still unquantified (deferred, correctly
   scoped to zero-dep axiom in the interim).
5. **T10 wrong tier → CLOSED** (was fixed during round 1 itself; conventions
   now cite `p.rot` correctly).

## Major Issues (status)

1. CP-SAT overkill — OPEN (needs spike or blocked-source numbers; deferred).
2. Unmeasured ranking — PARTIALLY (superlatives removed where measured;
   "~N lines" still judgment, now typographically separated).
3. Bench mimicry metric — CLOSED (displacement labeled secondary).
4. nn-ga table repro — PARTIALLY (footnote + monster pointer stand; generator
   script still absent; known-bad "7" still ships — now explicitly suspect).
5. Purchase transfer labels — CLOSED (T1/T2 re-labeled proposal, T13 keeps L).
6. Edge cases — CLOSED (None-semantics + coverage reporting).
7. T14 veto — CLOSED (scored, veto removed with rationale).
8. ε/headroom constants — CLOSED as documented-judgment (placeholder labels +
   Q2 calibration path; not yet measured — honest).
9. Net-class/gap-σ — CLOSED (documented as future work + population TBD).
10. Related-work holes — OPEN (needs blocked sources; correctly deferred to
    round 3 or never — none block build decisions).
11. Corpus drift (OCD.md/pour/RFC/ADR) — CLOSED for pour (warn-only);
    OCD.md already documented keepout (round-1 finding was stale);
    RFC-0001/ADR-0002 staleness untouched (cheap, still open — 2-line fixes).
12. Baseline mismatch — CLOSED (defaults = baseline config; stale job killed,
    rerun in flight).

## Minor Issues

- Converter/SOURCES recounts applied (raw vs generated numbers reconciled);
  convert.py docstring corrected; baseline logs gitignored.
- Tidy 45°/Johnson flags updated to confirmed; refs unified on ES mirror.
- Redundancy (T7/T8/T9, T2/T3, T1/T4) now documented as known-conflict
  (T9) rather than fixed — acceptable for a research brief.
- Fixed-harness monster baseline numbers pending (job running); SOURCES still
  shows pre-fix baseline until rerun lands — refresh on completion.

## Inline Annotations

- bench.py:28-56 → Criticals 1–2 closed; displacement secondary labeled.
- tidy L164-186 → Critical 3 closed; Major 5–9 closed as documented-judgment.
- solvers L8-16 + dense-demo-experiment.md → Critical 4 partially closed
  (placement earned; routing bounded).
- agent.py `_validate` pour note → Major 11 (pour) closed; tests pass.
- nn-ga L98-110 footnote → Major 4 partially closed (generator still absent).

## Blocked (unchanged)

HF/AlphaXiv keys; paywalled primaries; rate-limited arXiv IDs. Nothing new
needed this round — all fixes were code/prose, no sources.
