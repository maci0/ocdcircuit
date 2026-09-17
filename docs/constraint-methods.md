# Other constraint methods — research brief (ocdcircuit relevance)

Companion to `constraint-solvers.md` (diffusion placer, A* maze router, CSP,
CP-SAT basics, SA, force-directed, Lee/A*/Pathfinder, Langevin). This brief
covers everything else: exact layout backends, floorplan/packing, continuous
constrained optimization + interactive solvers, local-search/population
metaheuristics, and structure/graph methods (Steiner, assignment, schematic,
analog). NN/GA verdict: `nn-ga.md`. Tidy-layout metrics: `tidy-metrics.md`.

## Summary

Five families, cheapest first for a zero-dependency Python tool at tens of
parts: (1) **VPSC-style 1D separation passes** as the overlap legalizer (~50
lines, Graphviz/Dunnart-proven); (2) ~~**min-conflicts repair**~~ — SHIPPED
(`solver._repair`, 88 lines: most-overlapped part, 12 local moves × 8 rounds);
remaining: LNS ruin-recreate; (3) ~~**rectilinear MST/Steiner net
decomposition**~~ — SHIPPED (`maze._mst_pairs` trunk routing); measured
deltas in ADR-0002 (blinky 109→98 segs, mitox 1536→987); (4) **skyline/BLF
packing** for compact mode + greedy compaction; (5) **symmetry/matching
placer terms** extending match/diff. Exact backends (`diffn`/`AddNoOverlap2D`,
Z3) stay an *optional verifier* ("prove it doesn't fit"), never the default —
scoped by measurement in `benches/exact_overlap_pilot.py` (stdlib backtracking
proxy for AddNoOverlap2D): loose feasibility is trivial at n≤20; undersized
prove-no is instant at n≤15 but times out (>3 s, >7M nodes) at n=20; adding a
chain-WL cap times out at n=12 on a tight cap. Exact methods earn a dependency
only for prove-infeasible / optimality-gap questions, not day-to-day place.
Skip: full floorplan-SA encodings, Cassowary port, GA/ACO/PSO, BayesOpt,
ADMM/ALM until stiffness bites, escape routing until dense BGA.

## 1. Exact combinatorial backends for layout

- **MiniZinc `diffn(x,y,dx,dy)`**: constrains origin/size rectangles to
  non-overlap; variants `diffn_k`, `diffn_nonstrict`, plus `geost*` —
  [globals packing docs](https://docs.minizinc.dev/en/2.8.5/lib-globals-packing.html).
  Globals get solver-specific propagators or library decomposition —
  [globals overview](https://docs.minizinc.dev/en/2.8.5/lib-globals.html).
  Per-solver native-vs-decomposed matrix NOT verified — flagged.
- **OR-Tools CP-SAT `AddNoOverlap2D`**: 2D non-overlap over x/y interval lists
  built with `NewIntervalVar(start,size,end)`; verified in the API reference,
  which shows incremental `addRectangle(xInterval, yInterval)` —
  [NoOverlap2dConstraint](https://or-tools.github.io/docs/javadoc/com/google/ortools/sat/NoOverlap2dConstraint.html).
  CP-SAT internals/propagator details NOT verified — flagged. Integers-only
  (scale mm→µm/mil) per [CP-SAT docs](https://developers.google.com/optimization/cp/cp_solver).
- **Under the hood** (standard account): pairwise non-overlap is a 4-way
  disjunction (left/right/above/below), encoded via big-M, reified booleans,
  optional intervals, or lazy clause generation; big-M-vs-hull background —
  [Pyomo GDP notes](https://secquoia.github.io/pyomo-summer-ws/notebooks/gdp/gdp/).
- **Z3 `Optimize`**: `add` hard + `add_soft` weighted (MaxSMT) +
  `minimize`/`maximize` + `unsat_core` —
  [API](https://z3prover.github.io/api/html/classz3_1_1optimize.html),
  [tutorial (LRA, bitvectors, MaxSAT)](https://theory.stanford.edu/~nikolaj/programmingz3.html),
  [arithmetical optimization](https://microsoft.github.io/z3guide/docs/optimization/arithmeticaloptimization/).
  Layout-relevant theories: LRA (coordinates), bitvectors (grid cells).
- **SMT layout literature is thin for PCB**: one SMT floorplanning paper
  (Banerjee et al., overlap-free + area minimization + rotation, MCNC/GSRC —
  [arXiv:1709.07241](https://arxiv.org/abs/1709.07241)); SAT-routing hits are
  VLSI/escape-flavored and paywalled (abstracts only — flagged); an OpenAlex
  "SMT-based PCB routing placement" query returned mostly false positives
  (decoupling-capacitor papers), confirming thinness.
- **Relevance**: clean maps — fixed→domain fixing, keepout/edge→forbidden
  regions (keepout already exists as maze walls + DRC warnings),
  overlap→`diffn`/`AddNoOverlap2D`, match/diff→equality/symmetry.
  Exactness pays for **INFEASIBLE proofs / unsat cores** ("why doesn't it
  fit"), as an optional verifier on tens-of-parts boards — not the default
  placer. Dependency + integer-scaling cost violates zero-dep until then.

## 2. Floorplan representations + packing heuristics

- **Slicing / Polish expression**: Wong–Liu SA-based slicing floorplanner
  (1986), slicing tree as normalized Polish expression; SA perturbs, decode to
  packing. Lineage corroborated via a later abstract calling it the "well-known
  simulated annealing based Wong-Liu algorithm [1986]" —
  [doi](https://doi.org/10.1109/ISCAS.2000.856081) — and the ISPD 2024 Wong
  retrospective ([slides](http://ispd.cc/slides/2024/protected/12_3_slides_final.pdf)).
  Exact SA move set NOT verified — flagged.
- **Sequence-pair** (Murata et al. 1996): two permutations whose relative order
  defines H/V relations; called a "topological representation… not…
  restricted to slicing floorplan topologies" —
  [doi](https://doi.org/10.1145/309847.309930). Original 1996 record + move
  details NOT retrieved — flagged.
- **B\*-tree** (Chang et al. 2000, DAC'00): ordered binary tree on admissible
  placements, O(1) search/insert, direct evaluation; MCNC result ~4.5× faster,
  ~60% less memory, smaller area than O-tree — record verified via OpenAlex
  (2000, [doi](https://doi.org/10.1145/337292.337541), 548 cites).
- **O-tree** (Guo/Cheng/Wong 1999): ordered-tree encoding decoded by DFS
  contour placement —
  [record](https://www.semanticscholar.org/paper/An-O-tree-representation-of-non-slicing-floorplan-Guo-Cheng/75dc1a3ba5250f2b343614ae8fbc0f010eb6e6de).
- **Constructive packing** ([Jylänki RectangleBinPack](https://github.com/juj/RectangleBinPack)
  implements the family): BL (~15 lines, holes), BLF (~25, denser), best-fit
  scoring wrapper, **skyline** (~30–40, rooftop contour, best density/line),
  maximal-rectangles (densest, ~80–150 lines + free-list). ms-scale claim is
  engineering judgment, not benchmarked — flagged.
- **Relevance**: for post-diffusion **legalization** (positions exist, minimize
  displacement): rank 1 = position-preserving greedy compaction (sort by x,
  re-place against contour, iterate y); 2 = skyline-guided repair; 3 = BLF
  re-decode; last = MaxRects (ignores positions). For **compact-mode** coarse
  placement: skyline best-fit first. Pin fixed parts first, seed keepouts as
  blocked rects — none of these natively honor near/match constraints.
  Full sequence-pair/B\*-tree + SA is overkill: n is tens, and the tool's real
  constraints don't fit those encodings.

## 3. Continuous constrained optimization + interactive solvers

- **Penalty → ALM → ADMM**: quadratic penalty (μ→1e6) makes Hessian condition
  ~μ → stiffness/oscillation (textbook account, Nocedal & Wright ch.17 — NOT
  OA-verified, flagged). ALM (L = f + λᵀc + μ/2‖c‖², λ += μc —
  [Drake header](https://github.com/RobotLocomotion/drake/blob/3089354e16867440d4c17c0a5c290c683e2d5af4/solvers/augmented_lagrangian.h))
  converges without μ→∞ because λ learns the constraint force. ADMM (Boyd et
  al., verified monograph page: *Foundations and Trends in ML* 3(1), 2011 —
  [Stanford page](https://web.stanford.edu/~boyd/papers/admm_distr_stats.html))
  splits f(x)+g(z) s.t. Ax+Bz=c into cheap alternating solves — buys
  decomposability the tool has no use for. **Overkill; skip.**
- **VPSC** (variable placement with separation constraints): QP minimizing
  Σwᵢ(xᵢ−idealᵢ)² subject to separation constraints u + gap ≤ v, solved
  per-axis by projection passes merging violations into blocks. Verified:
  Adaptagrams docs describe exactly this QP + cite Fast Node Overlap Removal
  (Dwyer–Marriott–Stuckey, GD'05) —
  [libvpsc](https://www.adaptagrams.org/documentation/libvpsc.html); used by
  Graphviz and Dunnart per the
  [Adaptagrams README](https://github.com/cmears/adaptagrams).
  Dunnart record verified via OpenAlex (Dwyer–Marriott–Wybrow 2009,
  [doi](https://doi.org/10.1007/978-3-642-00219-9_41), 51 cites).
  **This is the legalizer design: ~50 lines/sweep, no deps. Adopt-first
  candidate — residual-overlap delta vs diffusion+_repair still unmeasured.**
- **Cassowary** (incremental linear equality+inequality solver, stay/edit
  constraints for interactive UI): verified via kiwisolver docs — "efficient
  C++ implementation of the Cassowary constraint solving algorithm… 10x to
  500x faster than the original" —
  [docs](https://kiwisolver.readthedocs.io/en/latest/). Hand-port ≈ 500+ lines
  and bug-prone; kiwisolver is a compiled dep. **Reject both** unless
  schematic/UI alignment constraints arrive.
- **Projection for hard regions**: per-iteration clamp = Euclidean projection
  onto board box; alternating projections cycle board-box → keepouts (convex
  convergence; Dykstra adds corrections). Keepout rectangles make the feasible
  set non-convex → heuristic repair, not exact. **Do now: one-line clips and
  keepout push-out.**
- Ranking (judgment, not measured): (i) VPSC-style 1D passes — FIRST candidate;
  (ii) clamp/projection — NOW; (iii) ALM — only if 1e6-weight stiffness bites;
  (iv) Cassowary — only if UI constraints grow.

## 4. Local search + population metaheuristics

- **Min-conflicts** (Minton et al.): start complete-but-flawed, repeatedly
  reassign the most-conflicted variable to its min-conflict value; wins where
  near-solutions are dense —
  [AAAI-1990 record](https://mlanthology.org/aaai/1990/minton1990aaai-solving/)
  (metadata only; year/pages flagged). Adjacent repair lineage verified via
  OpenAlex (Zweben et al. iterative repair). Map: greedy place → loop "pick
  most-overlapped part, try N local moves, keep best". **SHIPPED** as
  `solver._repair` (88 lines, 12 moves × 8 rounds, inside the undoable
  effect) — was estimated "~20 lines" pre-implementation.
- **LNS / destroy-and-repair**: ruin a subset (worst part + net-neighbors),
  re-optimize the hole while freezing the rest — preserves backbone, searches
  an exponentially large implicit neighborhood. Verified descendants: Pisinger
  & Røpke ALNS (via OpenAlex); LNS inside CP-SAT —
  [primer](https://d-krupke.github.io/cpsat-primer/09_lns.html),
  [OR-Tools manual](https://lia.disi.unibo.it/Staff/MicheleLombardi/or-tools-doc/user_manual/manual/metaheuristics/jobshop_lns.html#a-heuristic-to-solve-the-job-shop-problem).
  "Shaw 1998" origin NOT directly returned — flagged. **Second-cheapest
  upgrade.**
- **Tabu** ([Handbook](https://doi.org/10.1007/978-1-4419-1665-5)): forbid
  recent reverse moves (~10-line list) — only if cycling observed, else skip.
  ILS (perturb + re-climb) / GLS (penalize recurring costly features): defer.
- **GA**: crossover on layouts needs order-preserving operators
  ([AIPS-96](https://aaaipress.org/Papers/AIPS/1996/AIPS96-022.pdf#2#2)); naive
  coordinate-blend crossover is destructive (consensus, single source
  unverified — flagged). **Skip.** **NSGA-II** (Deb et al. 2002 — record
  verified via OpenAlex: title/year match,
  [doi](https://doi.org/10.5281/zenodo.6487417); proxy review
  [doi](https://doi.org/10.1109/access.2021.3070634)): Pareto fronts for
  wirelength-vs-area-vs-thermal — **only if the 3 named presets
  (diffusion/compact/thermal) fail users**; heavy machinery otherwise.
- **ACO/PSO**: ACO lineage verified ([Dorigo & Blum 2005](https://doi.org/10.1016/j.tcs.2005.05.020));
  but PCB-*trace-routing* ACO evidence is THIN (one paywalled record, assembly
  hits are scheduling not routing — flagged). No PCB placement PSO record
  verified — flagged. **Skip while A\* maze wins.**
- **CMA-ES / BayesOpt** for pull/spread/noise knobs: random search over ~30–60
  samples is the baseline to beat —
  [Bergstra & Bengio](https://jmlr.csail.mit.edu/papers/v13/bergstra12a.html#1).
  **Overkill at ≤4 noisy knobs.**

## 5. Structure/graph methods

- **Rectilinear Steiner (RSMT)**: Hanan's theorem restricts optima to the
  pin-line grid (reported —
  [doi](https://doi.org/10.1137/0114025), venue NOT re-verified, flagged);
  RSMT is NP-hard → rectilinear-MST heuristic + improvement (survey —
  [doi](https://doi.org/10.1109/ACCESS.2020.2986138)). **FLUTE** (Chu & Wong):
  lookup tables for low-degree nets + D&C — record verified via OpenAlex (TCAD,
  [doi](https://doi.org/10.1109/tcad.2007.907068), 354 cites). Buy over
  pin-to-pin chaining: one shared trunk replaces N−1 competing maze paths —
  fewer detours/layer changes (exact % unverified — flagged). **Rectilinear-MST
  trunk legs SHIPPED** (`maze._mst_pairs`; blinky 109→98 segs, mitox
  1536→987). Hanan RSMT improvement still open (~lookup/D&C; not required
  for the MST win).
- **Pin assignment / escape**: assignment = min-cost bipartite matching
  (Hungarian) over swappable pins (textbook account, record NOT fetched —
  flagged); escape = simultaneous flow/pattern routing (e.g.
  [doi](https://doi.org/10.22452/mjcs.vol29no2.2)) + diff-pair-aware unified
  routing ([doi](https://doi.org/10.1145/3394885.3431568)). **Defer until dense
  BGA.**
- **Schematic layout (Sugiyama)**: layer → crossing-minimize (barycenter/median
  sweeps) → coordinate assign; canonical dot implementation —
  [Gansner et al. PDF](https://pdfs.semanticscholar.org/3d41/015569bf4299ac83451c3f42b13a02ce29fb.pdf)
  (line-item specifics NOT verified — flagged). **Sugiyama-lite**
  (longest-path layers + barycenter sweeps) replaces one-column-per-net;
  medium-cheap. Spectral layout ([Koren 2005](https://doi.org/10.1016/j.camwa.2004.08.015))
  is placement-seeding only, not schematic drawing.
- **Analog constraints**: classical taxonomy via
  [survey](https://doi.org/10.3390/microelectronics1010002); symmetry groups in
  topological representations —
  [O-tree symmetry, DAC 2000](https://doi.org/10.1145/337292.337545),
  [multi-symmetry](https://doi.org/10.1109/ISCAS.2007.378437),
  [LP symmetry](https://doi.org/10.1109/TCAD.2007.891365). Map: extend DSL with
  symmetry-group declarations (mirror axis + pair list) as placer mirror terms,
  plus alignment/proximity terms — placer-cost only, no router change.
  Common-centroid canonical record NOT isolated (folklore — flagged).

## Adoption ranking (all five angles merged)

1. VPSC-style 1D separation legalizer (~50 lines, §3) — next build candidate;
   ranking unmeasured (no pilot vs diffusion+_repair residual overlaps yet).
2. ~~Min-conflicts repair loop~~ — SHIPPED (`solver._repair`, 88 lines);
   next in this family: LNS ruin-recreate.
3. ~~Rectilinear-MST/Steiner decomposition~~ — SHIPPED (`_mst_pairs` trunk
   routing in `maze.py`); measured segs: blinky 109→98, mitox 1536→987
   (ADR-0002).
4. Per-iteration clamp/projection one-liners (§3) + cooling tuning (prior brief).
5. Skyline/BLF for compact mode + greedy compaction (§2).
6. Symmetry/matching placer terms; Sugiyama-lite schematic (§5).
7. Later/conditional: CP-SAT/`diffn` verifier (see exact_overlap_pilot —
   only for prove-infeasible / gap), ALM, NSGA-II, escape routing.
8. Skip: B\*-tree/SA machinery, Cassowary, GA, ACO/PSO, BayesOpt, ADMM.

## Open questions

1. Does VPSC-1D close the residual overlap gap on dense demos / synthetic
   n=1000 (1335 dense overlaps after diffusion+_repair) without touching
   diffusion further?
2. ~~MST-decomposition vs chained pin-to-pin: measured wirelength/via delta~~
   ANSWERED (ADR-0002): blinky 109→98 segs, mitox 1536→987.
3. ~~At what density does the exact-verifier (`AddNoOverlap2D`) earn its
   dependency?~~ ANSWERED (`benches/exact_overlap_pilot.py`): feasibility
   alone is cheap at n≤20; prove-infeasible times out at n=20 undersized
   (>3 s); WL-capped exact search times out at n=12 on a tight cap. Keep
   exact backends optional-verifier only — do not take an OR-Tools dep for
   day-to-day place.
4. Unverified items carried forward: per-solver `diffn` support, CP-SAT
   propagator internals, Hanan/Hwang/Sugiyama/Hungarian citation details,
   Hwang 3/2 ratio, ACO-PCB record, PSO-placement record.

## Verification notes

- Directly fetched & confirmed this round: B\*-tree 2000/548 cites; NSGA-II
  2002 title/year; Dunnart 2009/51 cites; libvpsc QP+separation definition;
  `NoOverlap2DConstraint.addRectangle`; kiwisolver/Cassowary lineage + speed
  claim; Boyd ADMM 2011 monograph page; FLUTE TCAD/354 cites.
- OpenAlex full-text search misfires (returned ParamILS for min-conflicts,
  Chang 1972 for Hanan, Gajos 2005 for Cassowary) — those records are NOT cited
  for those claims; subagent-provided landing pages/DOIs used instead with
  flags where venues weren't re-verified.
- Blocked, never inferred: no `HF_TOKEN` / `ALPHAXIV_API_KEY` in env (fell back
  to arXiv + OpenAlex, unchanged); IEEE/ACM paywalled primaries; guessed arXiv
  IDs never cited.

## References

- MiniZinc packing globals — https://docs.minizinc.dev/en/2.8.5/lib-globals-packing.html · https://docs.minizinc.dev/en/2.8.5/lib-globals.html
- OR-Tools NoOverlap2D — https://or-tools.github.io/docs/javadoc/com/google/ortools/sat/NoOverlap2dConstraint.html
- CP-SAT docs — https://developers.google.com/optimization/cp/cp_solver
- Z3 Optimize API / tutorial / arith-opt — https://z3prover.github.io/api/html/classz3_1_1optimize.html · https://theory.stanford.edu/~nikolaj/programmingz3.html · https://microsoft.github.io/z3guide/docs/optimization/arithmeticaloptimization/
- SMT floorplanning — https://arxiv.org/abs/1709.07241
- Wong–Liu retrospective — http://ispd.cc/slides/2024/protected/12_3_slides_final.pdf · https://doi.org/10.1109/ISCAS.2000.856081
- B\*-tree — https://doi.org/10.1145/337292.337541 · https://openalex.org/W2100740271
- Sequence-pair analog use — https://doi.org/10.1145/309847.309930
- O-tree — https://www.semanticscholar.org/paper/An-O-tree-representation-of-non-slicing-floorplan-Guo-Cheng/75dc1a3ba5250f2b343614ae8fbc0f010eb6e6de
- RectangleBinPack — https://github.com/juj/RectangleBinPack
- Boyd ADMM — https://web.stanford.edu/~boyd/papers/admm_distr_stats.html
- Drake ALM — https://github.com/RobotLocomotion/drake/blob/3089354e16867440d4c17c0a5c290c683e2d5af4/solvers/augmented_lagrangian.h
- libvpsc / Adaptagrams — https://www.adaptagrams.org/documentation/libvpsc.html · https://github.com/cmears/adaptagrams
- Dunnart — https://doi.org/10.1007/978-3-642-00219-9_41
- kiwisolver — https://kiwisolver.readthedocs.io/en/latest/
- Minton repair — https://mlanthology.org/aaai/1990/minton1990aaai-solving/
- LNS in CP-SAT / OR-Tools — https://d-krupke.github.io/cpsat-primer/09_lns.html · https://lia.disi.unibo.it/Staff/MicheleLombardi/or-tools-doc/user_manual/manual/metaheuristics/jobshop_lns.html#a-heuristic-to-solve-the-job-shop-problem
- Handbook of Metaheuristics — https://doi.org/10.1007/978-1-4419-1665-5
- AIPS-96 crossover — https://aaaipress.org/Papers/AIPS/1996/AIPS96-022.pdf#2#2
- NSGA-II — https://doi.org/10.5281/zenodo.6487417 · review https://doi.org/10.1109/access.2021.3070634
- ACO survey — https://doi.org/10.1016/j.tcs.2005.05.020
- Random search baseline — https://jmlr.csail.mit.edu/papers/v13/bergstra12a.html#1
- FLUTE — https://doi.org/10.1109/tcad.2007.907068 · https://openalex.org/W2125831674
- Hanan (reported) — https://doi.org/10.1137/0114025
- Steiner survey — https://doi.org/10.1109/ACCESS.2020.2986138
- Escape flow — https://doi.org/10.22452/mjcs.vol29no2.2
- Unified PCB router — https://doi.org/10.1145/3394885.3431568
- dot/Gansner — https://pdfs.semanticscholar.org/3d41/015569bf4299ac83451c3f42b13a02ce29fb.pdf
- Koren spectral — https://doi.org/10.1016/j.camwa.2004.08.015
- Analog survey — https://doi.org/10.3390/microelectronics1010002
- O-tree symmetry / multi-symmetry / LP symmetry — https://doi.org/10.1145/337292.337545 · https://doi.org/10.1109/ISCAS.2007.378437 · https://doi.org/10.1109/TCAD.2007.891365
