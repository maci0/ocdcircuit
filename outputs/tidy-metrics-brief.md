# Quantifying OCD-compatible layout — research brief (tidy metrics)

## Summary

"Neat" decomposes into ~15 measurable scorecard items in four groups:
**trace geometry** (crossings, bends, orthogonality), **placement regularity**
(alignment, grid-snap, orientation, spacing uniformity), **DFM headroom**
(clearance/current margin, via discipline, copper balance, skew/gap), and
**readability** (schematic crossings/jogs, silk overlap/consistency). The human
evidence anchor is Purchase 1997: crossings dominate comprehension, bends and
symmetry matter less, grid-fixing was non-significant
([record](https://eprints.gla.ac.uk/35804/), 493 cites). Draft `tidy(board)`
scorecard below: 6 metrics already computable (~0 lines), 6 at ~10 lines each,
rest behind a geometry engine. Report the component vector + weights, never a
bare scalar; never compare scalars across boards.

## Background

Current `cost()` (`ocdcircuit/solver.py`) sees: Manhattan wirelength, overlap
(×1e6), edge (×1e5), near/match/diff penalties. Maze router (`maze.py`) prices
bend 1.5 + via 8.0. DRC (`drc.py` + fab profiles) reports pass/fail errors and
coarse warnings (skew~mm, clearance). None of these measure *tidiness*: a
zero-warning board can still snake, stagger, and scatter labels. The scorecard
fills that gap as continuous 0..1 (or physical-unit) metrics, stdlib only,
O(n)/O(n²) at tens of parts. Research only — no implementation this round.

## Findings

### 1. Graph aesthetics → trace geometry (angle a, verified anchor)

- **Purchase 1997** ("Which aesthetic has the greatest effect on human
  understanding?", GD'97, LNCS 1353): five aesthetics tested online, time +
  errors, confounds controlled. Abstract: reducing edge crossings "by far the
  most important"; fewer bends + more symmetry lesser effects; min-angle and
  orthogonal-grid fixes **not significant** — verified by direct fetch of the
  [Glasgow record](https://eprints.gla.ac.uk/35804/) and OpenAlex (1997,
  [doi](https://doi.org/10.1007/3-540-63938-1_67), 493 cites). Follow-ups
  (2000/2001 UML studies) NOT retrieved — flagged.
- Crossings/bends are the standard Tamassia-era orthogonal-drawing objectives
  (min-cost-flow bend minimization, planarization) — survey-level background
  ([bibliography PDF](https://www.csd.uoc.gr/~hy583/papers/1994-CG.pdf)).
- **Symmetry metrics: THIN** — "Measuring Symmetry in Drawings of Graphs"
  record exists but unreachable (503); no closed-form display-symmetry formula
  verified. Any mirror score is our operationalization, not literature.
- **Stress does NOT apply** to PCBs (it measures distance preservation;
  [Chen & Buja](https://doi.org/10.1198/jasa.2009.0111),
  [Brandes & Pich](https://doi.org/10.1007/978-3-540-70904-6_6)) — Manhattan +
  DRC dominate distance fidelity. Skip.
- Direct transfers with formulas: (i) same-layer crossings C (vias exempt — a
  layer change resolves a crossing), plus C per routed cm; (ii) bends/net B =
  Σ direction-changes / Σ routed length (L/Z good, snakes bad); (iii)
  alignment A = fraction of endpoints sharing x/y within ε (suggest 0.1 mm —
  judgment, flagged); (iv) mirror score S = 1 − min-axis mean matched-feature
  distance / board diagonal (heuristic pending verified metric — flagged).

### 2. Orthogonality / regularity (angle b — agent unresponsive, partial)

Agent `aa863d4e` acknowledged but never delivered despite a status nudge;
findings below are lead-synthesized from verified adjacent sources, flagged
accordingly. Constraint lineage: Dunnart provides alignment/distribution
constraints alongside separation (verified via Dunnart record in methods
brief, [doi](https://doi.org/10.1007/978-3-642-00219-9_41)) — the "distribute
evenly" operation is exactly spacing-uniformity enforcement.

- **45° discipline**: practitioner consensus prefers 45° bends over 90°
  corners (SI + etch); Altium's myth-review article surfaced via search
  ([link](https://resources.altium.com.cn/p/pcb-routing-angle-myths-45-degree-angle-versus-90-degree-angle))
  but the fetch timed out — content NOT verified, flagged. Metric anyway:
  orthogonality fraction = axis-aligned length / total routed length (target
  1.0 for Manhattan router; 45°-tolerant variant counts 45° segments too).
- **Orientation consistency**: fraction of parts at 0°/90° (tool parts are
  axis-aligned rectangles, so this is ~1.0 by construction today — free metric,
  guards future rotation features). Source: convention, flagged.
- **Grid-snap residual**: mean distance of part origins/pad centers to the
  routing-grid multiple (0.25 mm) — cheap placement-regularity proxy.
  Formula ours, flagged.
- **Spacing uniformity**: coefficient of variation of nearest-neighbor gaps
  along rows/columns — Dunnart-"distribution" flavored, formula ours, flagged.

### 3. DFM headroom as tidiness proxies (angle c, grounded)

- **Headroom ratios** (already-have, ~0 lines): clearance and current margins
  exist as pass/fail — normalize to actual/min per net class as continuous
  scores. IPC-2221B low-voltage floor 0.10 mm external uncoated confirmed via
  independent calculator summary
  ([AtlasPCB](https://www.atlaspcb.com/tools/conductor-spacing-calculator/),
  corroborated by [Altium](https://resources.altium.com/p/pcb-trace-and-pad-clearance-low-vs-high-voltage)).
  Flag: headroom is design guidance, NOT safety/UL compliance.
- **Via discipline** (~10 lines): via-count/net + layer-change count as
  neatness proxies — practitioner heuristic, NO standard caps it (flagged).
  Quantified neighbors: aspect ratio ≤10:1, drill-to-copper ≥8 mil, annular
  ring ≥3.5 mil PTH ([DFM guide](https://www.atlaspcb.com/blog/pcb-dfm-check-pre-order-verification-guide/)).
- **Acid traps**: acute copper <90° traps etchant; standard DRC misses them;
  ~8% of one fab's incoming DFM flags ([same guide](https://www.atlaspcb.com/blog/pcb-dfm-check-pre-order-verification-guide/)).
  Needs wedge-angle scan — geometry-engine tier.
- **Copper balance** (verified numbers, direct fetch): 40–60% ideal density,
  mirrored-layer delta ≤15–20%, warpage cap 0.75% SMT —
  [JLCPCB guide](https://jlcpcb.com/blog/pcb-copper-balancing) (fetched).
  Tile-variance proxy (grid copper % per layer + inter-layer delta) is
  grounded; thieving fixes sparse layers (keep ≥3W from fast traces).
- **Skew/gap**: practitioner table (USB2 ±150 mil … USB3/HDMI ±2.5–5 mil —
  fab guide, NOT interface specs, flagged;
  [source](https://www.atlaspcb.com/blog/differential-pair-routing-pcb/)).
  Tool's skew~mm warning is coarser than fast-serial needs → continuous max
  intra-pair skew + gap σ fit naturally. Rules: constant gap, 3S isolation,
  serpentine at mismatch source (same source).
- **Assembly tidiness** (~10 lines): silkscreen-on-pad = zero overlap;
  mask dam ≥3 mil; courtyard bbox overlap (KiCad users already write custom
  DRC for this —
  [forum](https://forum.kicad.info/t/custom-drc-for-overlapping-footprints-silkscreen-elements/57279/4)).
  Courtyard numbers from IPC-7351 NOT verified — flagged.

### 4. Readability: schematic + silk (angle d, Purchase-backed)

- **Schematic**: (i) column-crossing count — highest weight, Purchase-backed;
  (ii) jog/bend count per net; (iii) pin-alignment fraction. Schematic-specific
  metric papers are THIN (one metadata-only lead + paywalled RL-schematic
  paper — flagged); orthogonal/left-to-right/aligned-pins are tool
  conventions, not measured results.
- **Silk**: (i) label-overlap count (text–text + text–copper — veto, not
  score); measurable placement criteria come from map-labeling literature
  (candidate positions + overlap/displacement evaluation,
  [survey](https://en.wikipedia.org/wiki/Automatic_label_placement)); PCB
  sources are grey-lit only — flagged. Concrete silk rules verified by direct
  fetch: text ≥1.0 mm (40 mil) / 0.15 mm stroke, 0.15 mm pad clearance, refs
  adjacent + consistently oriented, polarity marks mandatory —
  [AtlasPCB guide](https://www.atlaspcb.com/blog/pcb-silkscreen-legend-design/).
  (ii) offset-direction consistency (modal-offset %); (iii) text density
  (ink area / board area, distinct sizes ≤ levels).
- **Composite scores**: prior art aggregates normalized aesthetics
  ([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0020025515003874#1),
  abstracts only — flagged); weights are always author-chosen. Lesson:
  **publish components + weights, never a bare scalar; never compare scalars
  across boards.**

## Draft `tidy(board)` scorecard

Status: ✅ = derivable from existing state (~0 lines); 🔧 = ~10 lines;
🏗️ = needs geometry engine. Targets are judgment (flagged J) except
literature-backed (L) and fab-verified (F).

| # | Metric | Formula | Target | Cost |
|---|---|---|---|---|
| T1 | Same-layer crossings | count + per routed cm | 0 (L) | ✅ |
| T2 | Bends per mm | Σ direction-changes / Σ length | min (L) | ✅ |
| T3 | Orthogonality fraction | axis-aligned length / total | 1.0 (J) | ✅ |
| T4 | Via count / layer changes | per net + board total | min (J) | ✅ |
| T5 | Clearance headroom | min(actual/min) per net class | ≥1.2 (J) | ✅ |
| T6 | Length skew + gap σ | max intra-pair ΔL, gap stdev | spec-dep (F-table) | ✅ |
| T7 | Placement alignment | shared-x/y fraction @ ε=0.1mm | →1 (J) | 🔧 |
| T8 | Grid-snap residual | mean dist to 0.25mm multiple | 0 (J) | 🔧 |
| T9 | Spacing uniformity | 1 − CV of neighbor gaps | →1 (J) | 🔧 |
| T10 | Orientation consistency | 0°/90° fraction | 1.0 (J) | ✅ |
| T11 | Copper tile variance | σ of tile density + layer Δ | Δ≤20% (F) | 🏗️ |
| T12 | Acid-trap scan | # acute <90° copper wedges | 0 (F) | 🏗️ |
| T13 | Schematic crossings/jogs | column crossings + jogs/net | min (L) | 🔧 |
| T14 | Silk overlap veto | text–text + text–copper count | 0 (F) | 🔧 |
| T15 | Silk consistency | modal-offset % + sizes ≤ levels | →1 (J) | 🔧 |

Suggested default weights (shown, overridable): traces 0.35 (T1–T4),
placement 0.25 (T7–T10), DFM 0.25 (T5–T6, T11–T12), readability 0.15
(T13–T15); T14 veto-gated (any overlap fails neat regardless of sum).

## Open questions

1. Do T-scores predict anything humans care about? Needs a blind ranking study
   (show engineers pairs of boards, correlate with scorecard) — literature
   backs crossings only.
2. Threshold calibration: is ε=0.1 mm right for alignment? Is headroom 1.2 the
   useful "neat" line? Needs board-corpus measurement.
3. Should `tidy` feed back into `cost()` (optimize neatness) or stay a
   report-only scorecard? Recommendation: report-only first; promote T1/T2
   terms into cost only if diffusion boards score badly.
4. Angle-b gaps: 45°-article content, orientation/regularity primary sources —
   still open after agent non-delivery.

## Verification notes

- Directly fetched & confirmed: Purchase 1997 abstract (crossings ≫ bends +
  symmetry; grid n.s.) + 493 cites; JLCPCB copper numbers (40–60%, Δ≤20%,
  warpage 0.75%); AtlasPCB silk rules (1.0 mm, 0.15 mm clearance); IPC-2221B
  0.10 mm floor via two independent summaries.
- OpenAlex full-text search misfired again (ForceAtlas2 for Purchase query;
  unrelated records for Tamassia query) — those records NOT cited.
- Blocked/partial, never inferred: Altium 45° article (fetch timeout);
  Purchase 2000/2001 follow-ups (503/blocked); symmetry-measurement paper
  (503); schematic-metric papers (paywalled/metadata-only); angle-b subagent
  (unresponsive — §2 marked partial, formulas flagged as ours).
- No `HF_TOKEN`/`ALPHAXIV_API_KEY` in env — arXiv + OpenAlex fallback
  (unchanged); no paywalls bypassed.

## References

- Purchase 1997 — https://eprints.gla.ac.uk/35804/ · https://doi.org/10.1007/3-540-63938-1_67
- Graph-drawing bibliography — https://www.csd.uoc.gr/~hy583/papers/1994-CG.pdf
- Stress: Chen & Buja — https://doi.org/10.1198/jasa.2009.0111 · Brandes & Pich — https://doi.org/10.1007/978-3-540-70904-6_6
- Dunnart — https://doi.org/10.1007/978-3-642-00219-9_41
- 45° routing (unverified content) — https://resources.altium.com.cn/p/pcb-routing-angle-myths-45-degree-angle-versus-90-degree-angle
- IPC-2221 summaries — https://www.atlaspcb.com/tools/conductor-spacing-calculator/ · https://resources.altium.com/p/pcb-trace-and-pad-clearance-low-vs-high-voltage
- DFM guide (vias, acid traps, dam, balance) — https://www.atlaspcb.com/blog/pcb-dfm-check-pre-order-verification-guide/
- Copper balancing — https://jlcpcb.com/blog/pcb-copper-balancing
- Diff-pair rules/skew — https://www.atlaspcb.com/blog/differential-pair-routing-pcb/
- KiCad custom DRC precedent — https://forum.kicad.info/t/custom-drc-for-overlapping-footprints-silkscreen-elements/57279/4
- Silk design guide — https://www.atlaspcb.com/blog/pcb-silkscreen-legend-design/
- Label placement survey — https://en.wikipedia.org/wiki/Automatic_label_placement
- Aesthetics aggregation — https://www.sciencedirect.com/science/article/abs/pii/S0020025515003874
- Empirical evaluation survey — https://ieeexplore.ieee.org/document/9309216
