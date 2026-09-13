# Review: research corpus (round 1 of 3) — pre-trust critique

Scope: `docs/constraint-solvers.md`, `docs/constraint-methods.md`,
`docs/nn-ga.md`, `docs/tidy-metrics.md`, `outputs/the-research-here-audit.md`,
`benches/monster6502/` (bench.py, convert.py, SOURCES.md, monster6502.ocd).
Method: 4-angle fan-out (claims/methodology, benchmarks/bench, scorecard,
completeness) + lead spot-checks (docs/outputs sync OK; Purchase W1521554751
493 cites + B\*-tree W2100740271 548 cites re-fetched; `Part.rot` verified in
code). Credentials: HF_TOKEN/ALPHAXIV_API_KEY unset → HF blocked, arXiv +
OpenAlex fallback; no paywalls bypassed. Unreachable items marked blocked.

## Summary Assessment (revision priority)

The corpus is **literature-honest but verdict-heavy**: citations check out and
self-flags are ~90% accurate, yet the three headline verdicts (architecture
support, exact-methods skip, adoption ranking) rest on analogy + judgment
rather than measurement, and the two executable artifacts (bench harness,
scorecard) are not yet fair scorers. **Priority 1**: fix bench.py (golden WL
+ floor subtraction); **Priority 2**: make scorecard weights/edge-cases
implementable or explicitly non-normative; **Priority 3**: run the deferred
dense-demo experiments that would earn the architecture verdicts. Nothing here
requires retraction — but nothing here yet earns trust for build decisions.

## Strengths

- Zero invented sources across ~150 claims; blocked items disclosed, not
  papered over. Self-flag discipline is real and reviewer-verified.
- Literature anchors re-verified independently: Purchase 1997 abstract
  verbatim + 493 cites; B\*-tree/FLUTE/Dunnart records; libvpsc QP;
  kiwisolver verbatim; MiniZinc `diffn`; CP-SAT integer-only; copper/silk/IPC
  numbers from dual summaries; MOnSter quote verbatim.
- Prior-audit fix log held on re-check (keepout wording, rip-up reframing,
  tidy tiers, nn-ga footnote) — the corpus responds to critique.
- Monster bench exists and runs end-to-end (243 s baseline); converter
  provenance + limitations documented in SOURCES.md.

## Critical Issues

1. **bench.py never computes golden wirelength** (conf 95). Docstring
   promises "placed vs golden" comparison; code computes placed WL only
   (`bench.py:38`) — baseline "placed WL 1492514" has no denominator. Placed
   WL can beat golden by collapsing into overlaps. Fix: re-apply golden
   positions, call `solver.wirelength`, publish both.
2. **957-overlap floor not handled in scoring** (conf 95). Golden itself
   scores ~957 (F/B stacking, SOURCES.md); bench counts overlaps raw
   (`bench.py:41-42`), so baseline 2548 misreads and 0 looks like the target.
   Fix: subtract floor or normalize; publish golden-overlap baseline.
3. **Scorecard weights are non-implementable as specified** (conf 85).
   0.35/0.25/0.25/0.15 combine counts, fractions, ratios, and mm with no
   count→0..1 normalization; sub-weights undefined so T7/T8/T9 triple-count
   regularity. Literal implementation yields a meaningless sum — and invites
   the cross-board ranking the brief forbids. Fix: define normalization or
   mark weights illustrative-only.
4. **"Literature supports the current architecture" overclaims** (conf 78).
   Support is FR/graph-drawing analogy + one image-modeling abstract (Song &
   Ermon) + vendor marketing (Quilter) — no head-to-head, no failure rates on
   own demos. "Overkill while greedy+repair works" assumes the consequent.
   Fix: scope verdict to zero-dep-as-axiom, or run the deferred dense-demo
   experiments (solvers OpenQ2, methods OpenQ1–2).
5. **T10 tier was factually wrong — corrected during this review** (conf 95).
   Brief/audit claimed no rotation state; `Part.rot` exists (`circuit.py:28-50`,
   honored by export + 3D). Row fixed to 🔧 over `p.rot` and synced. Lesson:
   audit tiers need code re-verification, not prose inheritance.

## Major Issues

1. **CP-SAT/ILP "overkill" verdict outruns evidence** (conf 82). Evidence is
   API surface; scale numbers self-flagged unverified; tens-of-parts is exact
   methods' *best* regime, never engaged. No spike, no timing. Fix: quantify
   the alternative or scope the skip to the zero-dep axiom.
2. **Adoption ranking priced in unmeasured currency** (conf 72–76). Every
   "~N lines" is judgment without method; VPSC-first transfers graph-drawing
   overlap removal to PCB without PCB evidence; "biggest gain" (2nd rip-up
   pass) is an unmeasured superlative with the decisive experiment deferred.
3. **Bench confounds quality with legality** (conf 80). Release-fix + flat
   diffusion (no legalizer, no hierarchy) means overlaps measure missing
   legalization, not wirelength merit; any legalizer-equipped contender wins
   regardless. Displacement rewards mimicking one human reference (die-true
   ≠ wirelength-optimal; star-model WL ≠ HPWL), penalizing equally-good
   layouts. Fix: primary = placed WL vs golden WL + (overlaps − 957).
4. **nn-ga table still not reproducible + harbors a known-bad cell** (conf 90).
   No generator script, missing sizes/seeds/net-counts/router-settings/hardware;
   300-row "7" coincides with the sparse value (likely mislabeled) yet ships
   as data; quality cliff (hence 100–300 band) interpolates over it.
   Fix: generator script or drop the row; add random-placement baseline, single
   variance, per-stage timing.
5. **Purchase→PCB-trace transfer invalid as labeled** (conf 80). Purchase
   studied on-screen path-tracing; PCB same-layer "crossings" are DRC shorts
   the maze already avoids; comprehension cost ≠ violation cost. Transfer holds
   for T13 schematic columns only. Fix: re-label T1/T2 "(L)" as proposal, keep
   (L) for T13.
6. **Edge cases undefined across T1–T9** (conf 90). div0 when unrouted/empty
   (T1/T2/T3/T9), 0/0 single-part (T7), vacuous 1.0 unplaced (T7 — parts
   default to board center), undefined populations (T5 no net-class entity;
   T6 gap-σ population; T8 empty mean). No metric defines
   empty/single-net/unrouted/unplaced behavior.
7. **T14 veto is dangerous** (conf 85). No glyph extents (`Text` = x,y,s,cls)
   means overlap detection needs assumed font metrics; veto-gating ("any
   overlap fails neat") on a crude heuristic false-fails fab-legal boards.
   Fix: demote to scored metric until precision is measured.
8. **ε=0.1 mm zeroes T7 by construction** (conf 75). Continuous placer with no
   alignment term cannot reach it; scores stick near 0 and invite
   snap-to-grid work that regresses wirelength. Most dangerous single constant
   (copies easiest). Headroom 1.2 likewise underived (IPC floor is a minimum,
   not +20%). Fix: derive from corpus measurement or mark aspirational.
9. **T5 "per net class" undefined; T6 mixes estimated with routed length**
   (conf 80–85). No net-class entity (single global `min_space`); `_net_length`
   falls back to pad estimates when unrouted, conflating estimate with copper.
10. **Related-work holes a skeptic would poke** (conf 70–80): spectral
    placement beyond one Koren cite; commercial EDA constraint handling
    (Allegro/Xpedition); multilevel partitioning lineage (hMetis/MLPart)
    though nn-ga leans on hierarchy; detailed placement never evaluated;
    push-and-shove dismissed by assertion; thermal/SI-PI/cost/interactive-solving/panelization
    absent corpus-wide; ChiPBench cited without rebuttal/follow-up literature.
11. **Corpus↔codebase drift**: OCD.md omits keepout/pour/cutout/hole syntax the
    parser accepts (conf 90); `pour` parses but is silently no-op downstream
    (conf 85 — users get no effect, no warning); RFC-0001 lists match/diff as
    future though implemented; ADR-0002 headline still says "hill-climbing"
    while code is diffusion (conf 85–90).
12. **Harness-default vs published-baseline mismatch** (conf 85). bench.py
    defaults seeds=1 iters=50; SOURCES baseline labeled seeds=1 iters=5,
    243.6 s — default invocation does ~10× the work and won't reproduce the
    headroom number. Routed-DRC column uses lroute while infeasibility prose
    is about maze (conf 75) — contradictory without the distinction in-table.

## Minor Issues

- Redundancy: T7/T8/T9 triple-count regularity and can conflict; T2/T3 share
  direction-change signal (T3 vacuous under Manhattan); T1/T4 double-score one
  layer-change (crossing-exempt yet via-penalized); T13/T1 same phenomenon.
- T15 vacuous by construction (deterministic label offsets → ~100%
  modal-offset); T3/T12/T13/T15 are regression-tripwires/placeholders, not
  signal — mark as such.
- convert.py artifacts: rotations ignored, Pico dropped (2593 vs 2624 net
  inconsistency — recount needed), TP placeholders, 291×322 vs 290.7×322.0
  rounding, displacement to 0.01 mm overstates reference precision; docstring
  stale ("layout.json (unused)", "mil→mm").
- Methodology bias: fetchability bias (teaching pages, vendor docs, doxygen as
  load-bearing); grey-lit fab blogs as evidence; fan-out never assigned the
  dense demo; audit "80%" mixes constants with verdicts (don't cite as
  headline support).
- Judgment/fact blur: imperative verbs in evidence sections ("Ship", "Adopt
  first", "Reject", "Do now"); "~N lines" inline with citations; verdict-first
  section titles; 1e6/1e5 weights rest on an unverified textbook account with
  the flag 150 lines away — attach at first use.
- Adoption-ranking tension solvers↔methods (rip-up #1 vs VPSC #1) with no
  pointer on which wins; cross-links one-directional (tidy never links back);
  outputs-sync claim in audit fix-log needs rewording now that outputs/ holds
  only the audit (angle-D critical noted in-tree absence — lead verified
  SYNC-OK for the four brief pairs at review time; preserved here as stated).

## Inline Annotations

- solvers Summary L8-10 → Critical 4 (analogy ≠ support; circular premise).
- solvers §1 L59-63 / §3 L89-97 → Major 1–2 (CP-SAT skip; SA comparison).
- solvers §4 L165-168 → Major 2 (unmeasured "biggest"); OpenQ2 → deferred
  experiment earning C4.
- methods Summary L12-22 / Adoption L210-217 → Major 2 (unmeasured ranking).
- methods §3 L107-117 → Major 2 (VPSC transfer leap).
- nn-ga table L98-110 → Major 4 (repro + bad cell); Monster § L140+ →
  Critical 1–2, Major 3, 12.
- bench.py:34-45 → Critical 1–2, Major 3 (golden WL, floor, mimicry metric).
- SOURCES.md:19/29, :42-44 → Major 4, 12 (net counts; baseline mismatch).
- tidy table L168-184 → Critical 3, Major 5–9 (weights, transfer labels,
  edge cases, veto, ε, net-class).
- tidy L188-190 → Critical 3 (weights); L181 → Critical 5 (T10, fixed).
- OCD.md / RFC-0001 / ADR-0002 → Major 11 (drift).
- Audit fix-log → verified held on 3 spot-checks; outputs-sync sentence needs
  rewording per angle-D finding (kept as stated, flagged).

## Blocked

HF gated datasets (no HF_TOKEN); AlphaXiv (no key, OpenAlex fallback used);
IEEE/ACM paywalled primaries; rate-limited arXiv IDs; JS-shell pages; 403s;
MiniSAT DOI Sage-redirect. Per-round-1 evidence: Purchase + B\*-tree records
re-fetched OK (493/548 cites). Unverifiable-as-stated items are flagged
inline above rather than re-pursued.
