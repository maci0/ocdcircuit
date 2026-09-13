# Plan: review-loop round 1 on research corpus

## Scope ("our corpus")
- `docs/constraint-solvers.md` (252 lines)
- `docs/constraint-methods.md` (277 lines)
- `docs/nn-ga.md` (193 lines, incl. scale appendix + Monster section)
- `docs/tidy-metrics.md` (238 lines, incl. T1–T15 scorecard)
- `outputs/the-research-here-audit.md` (179 lines — prior audit + fix log)
- `benches/monster6502/` (SOURCES.md, convert.py, bench.py, monster6502.ocd, .fp files)
- outputs/ copies already verified byte-identical — review docs/, note if diverged.

Credentials: HF_TOKEN unset, ALPHAXIV_API_KEY unset (verified) → gated HF
blocked, arXiv + OpenAlex fallback. No paywalls.

## Angles (4-way fan-out, then lead synthesizes the single review)
- A: claims-vs-evidence + methodology/confounds (solvers + methods briefs)
- B: experimental design — nn-ga benchmarks, Monster bench, baselines/ablations
- C: tidy scorecard — metric soundness, calibration, implementability, writing
- D: completeness — limitations, related-work gaps, audit-trail integrity,
  corpus coherence (cross-links, staleness vs moved tree)

## Task ledger
- [x] Write this plan
- [x] Fan out 4 review angles
- [x] Lead spot-checks (re-fetch 2–3 anchors, bench file read)
- [x] Write exactly one review to `outputs/our-corpus-review.md`
