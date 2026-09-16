# Review: research docs (round 1 of 3, current corpus) — pre-trust critique

Scope: docs/constraint-solvers.md, constraint-methods.md, nn-ga.md,
tidy-metrics.md, simulators.md, parts-libraries.md (+ matrix + equivalence),
outputs/ AI-models brief, docs/saas.md, docs/research/our-corpus-review.md
(rounds 1+2 — prior standard), benches/monster6502/. Method: 5-angle fan-out
(A: solvers/methods, B: nn-ga/tidy/bench, C: simulators/parts, D: AI-models/saas,
E: completeness/coherence) + lead spot-checks (Purchase W1521554751 still 493
cites; docs/outputs sync; Part.rot; GatesPlugin; pour parse-only).
Credentials: HF/AlphaXiv unset → gated HF blocked, arXiv+OpenAlex fallback;
no paywalls bypassed. Unreachable = blocked, never inferred.

Note: prior reviews live at docs/research/our-corpus-review.md, and current
brief copies at docs/research/*-brief.md — outputs/ holds only 2 briefs.
Path references below use docs/ locations.

## Summary Assessment (revision priority)

The corpus is **literature-honest but stale by one build cycle**: citations
check out and flags are accurate, yet the tree has outrun the briefs —
`spice.py` + NgspicePlugin + GatesPlugin + `sim op/ac/lib/clk` + `spicepin=`
+ `alternates=` + KICAD_ALIASES + gated 2-round rip-up + MST routing +
score.py all shipped while briefs still recommend or omit them. Highest
leverage, in order: (1) reconcile briefs with shipped code (a staleness pass,
not new research); (2) fix bench/SOURCES baseline versioning (published
headroom contradicts the current tree); (3) resolve the score.py vs tidy-doc
contradiction (implemented scorecard vs "research only" + bare-scalar ban).
Nothing requires retraction; nothing new earns trust until (1) lands.

## Strengths

- Zero invented sources; blocked items disclosed; self-flags ~90% accurate
  (re-verified across rounds).
- License claims verified good: ngspice BSD + Debian-compatible; KiCad
  CC-BY-SA 4.0 + design-use exception (fetched verbatim).
- IPC-7351B scoping exemplary (summaries cited, paywall not bypassed,
  beyond-summary numbers flagged).
- AI-models brief: zero benchmark numbers without retrievable sources;
  license/secondary-source flags meet the bar; saas.md pricing carries
  re-check warnings.
- Alias-not-rename SHIPPED and verified (`parts.py` KICAD_ALIASES +
  `resolve_fp` wired into `add_part`).
- Purchase anchor stable (1997, 493 cites, re-fetched).

## Critical Issues

1. **Simulators/sim briefs stale by a full build cycle** (conf 95). The
   ranked order (.cir exporter → subprocess → SUBCKT → gate sim → XSPICE)
   reads as proposal; steps 1–4 are built (`spice.py` netlist + `-b`
   subprocess + wrdata parse; `simulate:ngspice`; `simulate:gates` 181 lines;
   `sim op/ac/lib/clk` in OCD.md). Review must reframe as verify/harden.
   Related: `sim.py` docstring overstates physics — L-prefix parts map to R
   (no L transient), C uses Backward Euler not trapezoidal, zero
   diode/Shockley/Newton paths, LED→C. Any citation of those features is wrong.
2. **Score.py contradicts tidy doc** (conf 95). Doc: "Research only — no
   implementation" + "never a bare scalar / never compare across boards."
   Code: score.py implements T1–T11/T13–T15 (~370 lines) AND ships
   `score()` → {0–100, grade A–F} with duplicate sub-metrics — inviting
   exactly the forbidden ranking. Resolve which is normative.
3. **SOURCES baseline contradicts the tree** (conf 90). Published headroom
   (r3: ratio 1.39, 1591 avoidable overlaps) vs current r4/r5 (ratio 1.28,
   2364 above floor, cost 4.9B deterministic). Doc pins no code version;
   the "below floor" multilevel framing (682 vs 957) misreads beating the
   stacking floor as a win while WL trails. Version-pin baselines or the
   headroom number is wrong.
4. **Rip-up/MST recommendations shipped but briefs unaware** (conf 85).
   Gated 2-round rip-up (`maze.py` ~199–261) and `_mst_pairs` trunk routing
   exist; solvers brief still ranks "second rip-up pass" as the upgrade and
   methods ranks MST decomposition as to-build. Dense-demo numbers live in a
   relocated file; verdicts cite unfetchable artifacts.

## Major Issues

1. **Architecture verdict still analogy-anchored** (conf 88). Only
   quantitative leg (dense-demo 0.67–0.71×) is now hard to locate
   (docs/research/dense-demo-experiment.md — verify present); remaining legs
   are FR/graph-drawing analogy + Song & Ermon image-model abstract + Quilter
   marketing. Circular "overkill while greedy+repair works" persists.
2. **Exact-methods skip unquantified** (conf 85). API-surface evidence only;
   tens-of-parts is CP-SAT's best regime, never engaged; no pilot (not even
   10-part AddNoOverlap2D verifier the brief itself proposes). Scope decision
   presented as researched verdict.
3. **saas.md opinion-as-finding + caveat loss** (conf 85/80). "Already beats
   Flux" / "useless output" with no controlled bake-off — re-label as
   Position + link proof path. Vendor benchmarks (Quilter Speedrun specs,
   DeepPCB USB) repeated without the brief's "vendor-run, unverified"
   qualifier — one qualifier per row.
4. **Pin-map rationale contradicts code** (conf 88). Parts brief says SKIPPED
   ("no consumer"); but `spice.py` hardcodes SOT23 pin roles, `sim op` takes
   positional pins, `spicepin=` already ships. Correct state: minimal attr
   exists ad hoc; full table deferred.
5. **Alternates model right, status wrong** (conf 90). `alternates=` proposed
   as BOM extra column; OCD.md documents the attr but `export.py` BOM emits
   no alternates column. Status must read PROPOSED, not shipped.
6. **Gate-sim cost basis wrong ~2×** (conf 85). "~50–100 lines" vs actual 181.
   Ranking directionally right; basis asserted.
7. **WL-model weakness unacknowledged** (conf 80). Star-model WL dominated by
   vcc/vss power nets (1350/2502 pins); ratio 1.39/1.28 is mostly power-span.
   No per-net-class split; bench defaults (1/5) ≠ shipped defaults (4/400).
8. **Envelope gaps undocumented corpus-wide** (conf 88). Thermal = placer knob
   only; SI/PI = skew reports only; cost = no live lookup; panelization = zero
   code; interactive = no push-shove story. LANDSCAPE tracks some; no single
   envelope section exists.
9. **Staleness cluster**: ADR-0002 Decision (hill-climb/L-route) vs diffusion/
   maze reality; RFC-0001 Open lists shipped match/diff/keepout; simulators.md
   "no digital/no AC" vs GatesPlugin + `sim ac`; maze keepout soft-comment vs
   hard-code. Each cheap; jointly they erode trust.
10. **Blocked-metadata overweight** (conf 80). ~30% of claims rest on
    metadata-only support where verdicts read strong; holds from prior rounds
    still apply (routing-gain, exact-methods).

## Minor Issues

- nn-ga table: no generator script, single seed, 4 sizes; suspect "7" still
  ships (flagged); maze-state math mixes 2L/6L (~3× undercount) and MOnSter
  vs discrete6502 sizes; absolutes lack hardware context (ratios verify).
- Tidy: T-table Cost column internally inconsistent (T11/T13 ✅ vs Summary);
  T7/T8/T9 + T2/T4 + T1/T4 double-count (admitted, shipped anyway — OK
  report-only, must decorrelate before cost() promotion); T13 jogs hardwired
  0; T6 gap-σ missing; T1 lacks per-cm normalization; T8 single-part vs
  <2-parts convention; T4 via monkey-patch caveat real; T14 font-box crude
  (correctly flagged); tile 5mm + copper-length-proxy unflagged constants.
- Converters: `_PIN_CAP` unused, block-pairing unvalidated, internal-net
  suppression unverified, rotations ignored; SOURCES double-"4.", committed
  JSON vs "NOT committed" line, net-count inconsistencies (2593 vs 2624).
- AI-models: "No Gerber" rests on 2 queries (disclosed — scope the universal);
  "no footprint fine-tune anywhere" outruns HF-only scope; YOLO license gaps;
  Lim RL needs ChiPBench proxy lens; HWE-Bench needs canonical abs link.
- Writing: imperatives in evidence sections; ~lines inline with citations;
  verdict-titles; 1e6/1e5 weights 150 lines from their trade-off flag;
  Purchase anchor front-loaded before T13-only cabin (170 lines later).
- Pricing tiers perishable (saas.md warns correctly — keep warnings).

## Inline Annotations

- solvers Summary/L8-18 + §3 mapping → Critical 4, Major 1–2.
- solvers §4/L169-171 + Open-Q2 → Critical 4 (stale vs maze.py:199-261).
- methods Adoption L208-217 + §3 VPSC → Major 2 (unmeasured ranking).
- simulators.md ranked order → Critical 1 (built, not proposal); sim.py
  docstring → Critical 1 (overstates physics).
- parts matrix SPICE ⚠️ + Disposition pin-map/alternates → Major 4–5.
- nn-ga table L93-145 → Minor (repro); Monster § → Major 7, Critical 3.
- bench.py + SOURCES.md:35-60 → Critical 3, Major 7.
- tidy table + score.py:375-408 → Critical 2, Minor (tiers/double-count).
- AI-brief §1/HF sections → Minor (scope caveats); saas.md §2.3/§4/§6 →
  Major 3 (opinion framing, qualifiers).
- OCD.md sim/pour sections → verify current (pour consumer state changed;
  confirm warn-only vs full-copper text).
- docs/research/our-corpus-review.md → prior standard; outputs/ review path
  from the workflow template does not exist (this file is written there
  instead per instructions).

## Blocked

HF gated content (no HF_TOKEN); AlphaXiv (no key, OpenAlex fallback);
IEEE/ACM paywalled primaries; rate-limited arXiv IDs; JS-shell pages; 403s
(Octopart captcha, KLC bot-check); MiniSAT DOI redirect; hneemann.de DNS.
Re-fetched this round: Purchase W1521554751 (493 cites, stable).
