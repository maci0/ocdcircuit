# Plan: audit the research briefs in this workspace

## Interpretation
No arXiv ID or repo URL was given, so "the research here" = the four research
briefs produced in this session. The "paper" in each pass is a brief; the
"implementation" is (a) the ocdcircuit codebase for code claims, (b) the cited
source URL for literature claims. Adaptations from the template noted in the
audit output.

## Targets
- `docs/constraint-solvers.md` (+ outputs copy)
- `docs/constraint-methods.md` (+ outputs copy)
- `docs/nn-ga.md` (+ outputs copy, incl. scale appendix + Monster section)
- `docs/tidy-metrics.md` (+ outputs copy)

## Method
- Pass 1 (per brief, subagent): extract concrete falsifiable claims, numbered,
  tagged with brief section.
- Pass 2 (per brief, subagent): code claims → exact file paths + line numbers
  in `ocdcircuit/`, `tests/`, `examples/`; literature claims → fetch cited URL,
  confirm support; benchmark numbers → check methodology. Mismatches documented
  with both sides. Unreachable = blocked, never inferred.
- Lead: synthesize into `outputs/the-research-here-audit.md` (Match Summary %,
  Confirmed, Mismatches, Missing, Reproducibility Risks) + direct spot-checks
  of highest-stakes claims + docs/outputs sync check.

## Scale decision
4-way fan-out (one subagent per brief), lead synthesizes. No workflow tool.

## Task ledger
- [x] Write this plan
- [x] Fan out 4 audit subagents (one per brief)
- [x] Lead spot-checks (benchmark table, code refs, docs/outputs diff)
- [x] Synthesize `outputs/the-research-here-audit.md`
