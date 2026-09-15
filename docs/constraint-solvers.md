# Constraint solver algorithms — research brief (ocdcircuit relevance)

Companion: `constraint-methods.md` (exact layout backends, floorplan/packing,
VPSC, local-search/population methods, Steiner/schematic/analog).

## Summary

For ocdcircuit's scale (tens of parts, zero-dependency Python), measurement
supports the current architecture: released diffusion finds zero-error layouts
at 0.67–0.71× golden wirelength in ~3 s on the dense pico_tmc2209 demo
(`benches/monster6502/bench.py` holds the live golden-WL comparison;
the original `outputs/dense-demo-experiment.md` log is gone). Exact
methods (CP-SAT, ILP/MILP) buy optimality proofs but cost a dependency plus a
linearized/disjunctive formulation the true objective doesn't need yet. The
cheapest upgrades, in order: (1) negotiated-congestion-lite in
the maze router (gated 2-round rip-up retry already shipped — remaining gain
is per-cell history across iterations); (2) a greedy legalization/overlap-removal pass after diffusion;
(3) cooling-schedule tuning. Skip: ILP dependency, ePlace/RePlAce
reimplementation, ML placers, push-and-shove.

## Background

ocdcircuit today (`ocdcircuit/solver.py`, `ocdcircuit/maze.py`,
`docs/ADR-0002-solver.md`): placement = Langevin diffusion — parts drift along
net-centroid spring forces plus pairwise radial/box repulsion with decaying
temperature/noise; multi-seed best-of (Quilter-style candidates). Layer
assignment = greedy bbox-overlap minimization. Routing = ordered L-routes plus
an A* maze router (0.25 mm grid, bend + via penalties, soft courtyard terrain,
own-net copper reuse, small-nets-first ordering + one bounded rip-up retry +
jumper fallback). Constraints: `fixed`/`near` (+`near-group`)/`keepout`/
`edge`/`layer`/`width`/`power`/`match`/`diff` (`docs/OCD.md`,
`docs/RFC-0001-constraints-agent.md`; `keepout` = maze hard walls + DRC
warnings, no placer term). Cost = Manhattan wirelength + 1e6 overlap
+ 1e5 edge + near/match/diff penalties.

## Key findings (by theme)

### 1. CSP: backtracking + propagation (for discrete decisions)

- CSP = variables, domains, constraints; backtracking = DFS over partial
  assignments; propagation prunes domains early: forward checking (neighbors
  only, O(nd²)) vs AC-3 (queue of arcs, O(n²d³)) per run. Ordering heuristics:
  MRV (fail-fast variable), LCV (least-constraining value) —
  [CMU 15-281 constraint notes](https://www.cs.cmu.edu/~15281-s23/coursenotes/constraints/index.html).
- SAT: DPLL (backtracking + unit propagation) → CDCL (+ clause learning,
  backjump, restarts). MiniSAT is the canonical compact CDCL baseline (verified
  via a paper adapting "CDCL based SAT solver MiniSat" —
  [doi:10.3233/sat190091](https://doi.org/10.3233/sat190091)); Glucose adds
  aggressive learnt-clause (LBD) management
  ([repo](https://github.com/audemard/glucose)); Kissat won SAT Competition 2024
  ([news](https://news.vm.uni-freiburg.de/en/newsarchive/kissat-triumphs-in-the-sat-2024-competition)).
  Primary-paper methods/benchmarks NOT re-verified — flagged.
- CP-SAT (OR-Tools) is the practical exact option: "uses SAT methods with CP
  methods" ([overview](https://developers.google.com/optimization/cp)), framed
  as a CP-SAT-LP hybrid
  ([talk abstract](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.CP.2023.3)).
  Verified by direct fetch: Python API (`cp_model.CpModel`, `NewIntVar`,
  `CpSolver().Solve`), **integers only** (scale floats first), statuses
  OPTIMAL/FEASIBLE/INFEASIBLE/MODEL_INVALID/UNKNOWN —
  [CP-SAT docs](https://developers.google.com/optimization/cp/cp_solver).
  Typical-size numbers NOT verified — flagged, do not quote.
- Relevance: CP-SAT fits when placement needs global feasibility proofs or
  disjunctive non-overlap + match/diff symmetry jointly optimized. Overkill
  while constraints stay local (fixed/keepout/edge) and greedy + repair works.
  Lazy rule: hand-rolled backtracking/greedy first; CP-SAT only if search stalls
  or "prove infeasible / optimality gap" is required. Never start with raw CNF.

### 2. ILP/MILP + simulated annealing / metaheuristics

- MILP = LP relaxation for bounds + branch-and-bound (+ cuts = branch-and-cut),
  proven optimality gap as the payoff vs heuristics
  ([Gurobi MIP primer](https://www.gurobi.com/resources/blog/mixed-integer-programming-an-introduction-to-the-basics),
  [provable optimality](https://www.gurobi.com/resources/blog/provable-optimality)).
  Open solvers verified: HiGHS (LP/MIP/QP, MIT, C/Python/Julia —
  [highs.dev](https://highs.dev/), fetched); SCIP (academic framework —
  [scipopt.org](https://www.scipopt.org/index.php/doc/html/doc-6.0.1/html/doc-3.2.1/html/SCIP-release-notes-4.0.1));
  CBC via PuLP ([docs](https://coin-or.github.io/pulp/main/includeme.html)).
  MILP is NP-hard; practical size is structure-dependent — size thresholds NOT
  verified, flagged.
- SA origin: Kirkpatrick–Gelatt–Vecchi 1983 ([bib](https://ic.unicamp.br/~andred/dir/bib2html/entry-Kirkpatrick-1983.html),
  [scanned paper](https://mat.uab.es/~alseda/MasterOpt/Annealing.pdf)): accept
  worsening moves with P=exp(−ΔE/T), cool T (geometric T←αT common; theory wants
  logarithmic). Schedule design: Ingber's re-annealing
  ([doi](https://doi.org/10.1016/0895-7177(89)90202-1)) and practice-vs-theory
  ([doi](https://doi.org/10.1016/0895-7177(93)90204-c)).
- VLSI heritage: TimberWolf (Sechen & Sangiovanni-Vincentelli, IEEE J. 1985) is
  the canonical SA-for-layout result — record confirms package + ~484 cites
  ([record](https://typeset.io/papers/the-timberwolf-placement-and-routing-package-56cv7755ud?citations_page=11),
  [ACM DAC entry](https://dl.acm.org/doi/10.5555/318013.318083)); cost was very
  long serial runs, later parallelized ([NASA NTRS](http://hdl.handle.net/2060/19870017987)).
  Exact TimberWolf quality numbers NOT verified (paywalled) — flagged.
- SA vs hill-climb vs Langevin: hill-climb is cheapest/step but sticks in local
  minima; SA adds temperature-controlled uphill moves; Langevin adds gradient +
  annealed noise (hybrid studied e.g.
  [CoolMomentum](http://arxiv.org/pdf/2005.14605v1)). Head-to-head winner at PCB
  scale NOT verified — depends on landscape, move set, budget.
- Relevance: an ILP dependency buys exactness on a *linearized proxy*, not the
  true objective, and costs a big-M/reified formulation. SA-with-schedule beats
  multi-start hill-climb when good basins are narrow and cost evals are cheap;
  otherwise multi-start wins on simplicity. Ship dependency-free now.

### 3. Placement: force-directed is the right weight class

- Standard flow: global placement (min wirelength s.t. density, overlap
  relaxed) → legalization (remove overlap, min displacement) → detailed
  placement (local swaps); HPWL is the wirelength proxy; OpenROAD `gpl` exposes
  target density 0.7 / overflow / bin grid —
  [OpenROAD docs](https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html).
- Three VLSI families: partitioning/min-cut (Caldwell/Kahng/Markov 2000, 392
  cites — record via OpenAlex); SA/TimberWolf (above); analytical — GORDIAN
  quadratic programming ([record](https://www.semanticscholar.org/paper/GORDIAN%3A-VLSI-placement-by-quadratic-programming-Kleinhans-Sigl/e6e1b1c20546a1a1586e0be912c97a32024ba687?sort=is-influential))
  → modern electrostatics ePlace/RePlAce (FFT + Nesterov;
  [UCSD paper](https://cseweb.ucsd.edu/~jlu/papers/eplace-todaes14/paper.pdf),
  [RePlAce journal](https://vlsicad.ucsd.edu/Publications/Journals/j126.pdf)).
  Capo/GORDIAN/Quinn-&-Breuer-1979 details NOT primary-verified — flagged.
- Force-directed lineage for ocdcircuit: Eades 1984 / Fruchterman–Reingold 1991
  (spring attraction + all-pairs repulsion + cooling; FR record verified —
  [doi](https://doi.org/10.1007/978-3-658-21742-6_49)); modern ForceAtlas2 adds
  Barnes–Hut, adaptive temperatures (3013 cites, verified via OpenAlex —
  [doi](https://doi.org/10.1371/journal.pone.0098679)).
- Mapping (design judgment): net-centroid springs ≈ FR edge attraction;
  pairwise repulsion ≈ FR all-pairs repulsion; decaying noise ≈ FR cooling ≈
  annealed Langevin T(t) — the shared trick verified in Song & Ermon, whose
  abstract states sampling "via Langevin dynamics" with "gradually decreasing
  noise levels" ([arXiv:1907.05600](https://arxiv.org/abs/1907.05600), fetched);
  multi-seed best-of ≈ multi-start annealing ≈ Quilter's "thousands of
  candidate boards" ([blog](https://www.quilter.ai/blog/pcb-autorouting-in-2026-a-review-of-traditional-tools-vs-quilters-ai-approach),
  internals proprietary — flagged).
- Open-tool reality: KiCad `AR_AUTOPLACER` is greedy constructive on a grid
  occupancy matrix ([doxygen](https://docs.kicad.org/doxygen/classAR__AUTOPLACER.html));
  Freerouting is router-only, no placement
  ([README](https://raw.githubusercontent.com/freerouting/freerouting/refs/heads/master/README.md));
  tscircuit auto-layout is flexbox/grid/packing assist, not wirelength-driven
  ([docs](https://docs.tscircuit.com/guides/tscircuit-essentials/automatic-pcb-layout)).
  KiRouter placer behavior NOT fetched — flagged.
- Cheapest upgrades: (i) density smoothing (bin-density penalty / RUDY-style
  congested-tile inflation, cf. OpenROAD); (ii) explicit legalization pass
  (overlap removal + local swaps) mirroring the 3-stage flow.

### 4. Routing: Lee → A* → Pathfinder; rip-up & reroute first

- Lee 1961 = BFS wavefront + backtrace, shortest-path guarantee
  ([record](https://www.semanticscholar.org/paper/An-Algorithm-for-Path-Connections-and-Its-Lee/6b9cbd70349aac279cb69ffb6017ee6504a729b9));
  Hadlock 1977 = detour-number expansion, optimality with target bias;
  A* (f=g+h, Manhattan h) = same structure with admissible-heuristic
  optimality —
  [Lafayette CADApps: Lee](https://sites.lafayette.edu/cadapps/main-page/maze-router-app/leerounter/)
  / [Hadlock](https://sites.lafayette.edu/cadapps/main-page/maze-router-app/hadlocks-algorithm/)
  / [A*](https://sites.lafayette.edu/cadapps/main-page/maze-router-app/a-algorithm/).
  Bend/via as additive transition costs and layer-as-3rd-dimension are standard
  textbook treatment — NOT primary-verified, flagged.
- Sequential routing ⇒ net-ordering problem ⇒ rip-up & reroute (standard
  detailed-routing remedy — [US8751989B1](https://patents.google.com/patent/US8751989B1/en)).
  Pathfinder (McMurchie & Ebeling, FPGA Symp. 1995 — record verified via
  OpenAlex: 1995, [doi](https://doi.org/10.1109/fpga.1995.242049), 281 cites)
  iterates all nets with node cost Cn=(bn+hn)·pn (base + congestion history +
  present sharing), forcing negotiation to convergence
  ([explainer](https://sites.lafayette.edu/cadapps/main-page/pathfinder-fpga-routing-algorithm/)).
- Length-matching/diff-pairs: constrain trace-length tolerance + intra/inter-pair
  skew ([PCBSync](https://pcbsync.com/length-matching-pcb/),
  [Cadence](https://resources.pcb.cadence.com/layout-and-routing/2025-differential-pair-length-matching-guidelines));
  shortfall fixed with meander/accordion jogs (practitioner-standard, no
  algorithmic primary fetched — flagged).
- Open routers: Freerouting = open autorouter (above); KiCad PNS = interactive
  walkaround + shove + diff-pair placer, *not* an autorouter
  ([walkaround](https://docs.kicad.org/doxygen/pns__walkaround_8h_source.html),
  [shove](https://docs.kicad.org/doxygen/classPNS_1_1SHOVE.html),
  [diff-pair](https://docs.kicad.org/doxygen/pns__diff__pair__placer_8cpp_source.html)).
- Relevance: current A* + bend/via + soft terrain already covers the basics.
  Upgrades in order: (a) negotiated-congestion-lite — shipped: 2-round
  rip-up retry + per-cell history/HIST adder across iterations
  (`maze.py`; blinky-1L 112→110 segs, breath_ketone 565→552, DRC-clean); (b) skip Hadlock
  re-tuning while the heuristic is admissible. Length meanders only if
  skew-driven routing is required; else keep reporting skew via DRC warnings.

### 5. Constraint handling: penalties now, projection at the end

- Standard split: global placement *drops* hard disjointness, reduces overlap,
  then a separate legalization pass removes residual overlap
  ([Bonn thesis PDF](https://bonndoc.ulb.uni-bonn.de/xmlui/bitstream/handle/20.500.11811/4667/2299.pdf?sequence=1&isAllowed=y)).
- Mapping: fixed = skip integration (hard clamp, already done via `_fixed`);
  near/group = extra spring (already done); edge = penalty (1e5) + repulsive
  push + hard clamp; keepout = maze hard walls + DRC warnings (no placer
  term). Penalty trade-off (textbook, NOT fetch-verified — flagged): too weak →
  violations survive; too strong → stiff/oscillatory without smaller steps or
  weight ramps. Standard compromise: penalties for exploration + hard
  projection/legalization at the end.
- Cheapest placer upgrades: (a) cooling-schedule tuning (geometric decay + step
  cap, noise floor → 0 late); (b) greedy legalization after dynamics (sort,
  push along min-penetration axis, clamp); (c) per-iteration hard projection
  (fixed pinned, in-bounds clamp). Avoid: full analytical legalizer,
  branch-and-bound, ML/learned placer — disproportionate at this scale.

## Open questions

1. At what part count / density does multi-start diffusion measurably lose to
   CP-SAT on a linearized model? Needs a benchmark, not literature.
2. Does a second rip-up pass close the airwire-fallback gap on dense demo
   boards (pico: ~197 maze warnings w/ fallbacks vs 26 lroute clearance
   warnings)? Count fallbacks, not warnings.
   ANSWERED (round 77): yes — gated 2nd round took pico 79→4 jumpers
   (route-grid 0.2 closed the last 4; pico now 0/0/0). Gate matters:
   unconditional round 2 churns good routes into jumpers (measured 27 vs
   4 on identical input); round 2 runs only when round 1 strictly shrank
   the failed set.
3. Length-matching currently penalizes *pad-distance* estimates pre-route; when
   should `_match_cost` switch to routed length, and are meanders ever needed?
4. Exact CP-SAT scale numbers, DPLL/CDCL primary methods, solver benchmark
   figures — left unverified (abstracts/docs only).

## Verification notes

- Directly fetched & confirmed: OR-Tools CP-SAT integer-only + Python API;
  HiGHS open LP/MIP/QP; Pathfinder 1995 record (281 cites); ForceAtlas2 2014
  (3013 cites); FR 1991 record; Song & Ermon 2019 abstract (annealed Langevin).
- OpenAlex full-text search is keyword-based: top hits for "Kirkpatrick 1983"
  and "ePlace/Nesterov" returned related-but-wrong records — not used as
  citations for those claims; arXiv ePlace primary rate-limited (blocked).
- Blocked, never inferred: HF gated datasets (no `HF_TOKEN` in env — not needed
  here); AlphaXiv (no `ALPHAXIV_API_KEY` — fell back to arXiv + OpenAlex);
  IEEE paywalled primaries (TimberWolf, Lee 1961); KiRouter placer docs;
  python-constraint performance limits.
- Local code refs (`solver.py`, `maze.py`, `drc.py`, `circuit.py`, ADR-0002,
  OCD.md, RFC-0001) read directly — no citation needed beyond file paths.

## References

- CMU 15-281 constraint notes — https://www.cs.cmu.edu/~15281-s23/coursenotes/constraints/index.html
- OR-Tools CP-SAT solver — https://developers.google.com/optimization/cp/cp_solver
- OR-Tools CP overview — https://developers.google.com/optimization/cp
- CP-SAT-LP talk — https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.CP.2023.3
- MiniSAT/CDCL (QMaxSAT) — https://doi.org/10.3233/sat190091
- Glucose — https://github.com/audemard/glucose
- Kissat SAT'24 — https://news.vm.uni-freiburg.de/en/newsarchive/kissat-triumphs-in-the-sat-2024-competition
- python-constraint — https://github.com/python-constraint/python-constraint/blob/main/docs/reference.rst
- Gurobi MIP intro — https://www.gurobi.com/resources/blog/mixed-integer-programming-an-introduction-to-the-basics
- HiGHS — https://highs.dev/
- SCIP — https://www.scipopt.org/index.php/doc/html/doc-6.0.1/html/doc-3.2.1/html/SCIP-release-notes-4.0.1
- PuLP — https://coin-or.github.io/pulp/main/includeme.html
- Kirkpatrick et al. bib/scan — https://ic.unicamp.br/~andred/dir/bib2html/entry-Kirkpatrick-1983.html · https://mat.uab.es/~alseda/MasterOpt/Annealing.pdf
- Ingber 1989 / 1993 — https://doi.org/10.1016/0895-7177(89)90202-1 · https://doi.org/10.1016/0895-7177(93)90204-c
- TimberWolf record / ACM — https://typeset.io/papers/the-timberwolf-placement-and-routing-package-56cv7755ud?citations_page=11 · https://dl.acm.org/doi/10.5555/318013.318083
- CoolMomentum — http://arxiv.org/pdf/2005.14605v1
- OpenROAD RePlAce — https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html
- ePlace / ePlace-MS / RePlAce PDFs — https://cseweb.ucsd.edu/~jlu/papers/eplace-todaes14/paper.pdf · https://cseweb.ucsd.edu/~jlu/papers/eplace-ms-tcad14/paper.pdf · https://vlsicad.ucsd.edu/Publications/Journals/j126.pdf
- GORDIAN record — https://www.semanticscholar.org/paper/GORDIAN%3A-VLSI-placement-by-quadratic-programming-Kleinhans-Sigl/e6e1b1c20546a1a1586e0be912c97a32024ba687?sort=is-influential
- FR 1991 — https://doi.org/10.1007/978-3-658-21742-6_49
- ForceAtlas2 — https://doi.org/10.1371/journal.pone.0098679
- Song & Ermon 2019 — https://arxiv.org/abs/1907.05600
- Quilter autorouting — https://www.quilter.ai/blog/pcb-autorouting-in-2026-a-review-of-traditional-tools-vs-quilters-ai-approach
- Bonn placement thesis — https://bonndoc.ulb.uni-bonn.de/xmlui/bitstream/handle/20.500.11811/4667/2299.pdf?sequence=1&isAllowed=y
- Lafayette CADApps (Lee/Hadlock/A*/Pathfinder) — https://sites.lafayette.edu/cadapps/main-page/maze-router-app/leerounter/ · https://sites.lafayette.edu/cadapps/main-page/maze-router-app/hadlocks-algorithm/ · https://sites.lafayette.edu/cadapps/main-page/maze-router-app/a-algorithm/ · https://sites.lafayette.edu/cadapps/main-page/pathfinder-fpga-routing-algorithm/
- Pathfinder DOI/OpenAlex — https://doi.org/10.1109/fpga.1995.242049
- Rip-up & reroute (US8751989B1) — https://patents.google.com/patent/US8751989B1/en
- PCBSync length-matching — https://pcbsync.com/length-matching-pcb/
- Cadence diff-pair guidelines — https://resources.pcb.cadence.com/layout-and-routing/2025-differential-pair-length-matching-guidelines
- KiCad AR_AUTOPLACER — https://docs.kicad.org/doxygen/classAR__AUTOPLACER.html
- KiCad PNS (walkaround/shove/diff-pair) — https://docs.kicad.org/doxygen/pns__walkaround_8h_source.html · https://docs.kicad.org/doxygen/classPNS_1_1SHOVE.html · https://docs.kicad.org/doxygen/pns__diff__pair__placer_8cpp_source.html
- Freerouting — https://raw.githubusercontent.com/freerouting/freerouting/refs/heads/master/README.md
- tscircuit auto-layout — https://docs.tscircuit.com/guides/tscircuit-essentials/automatic-pcb-layout
