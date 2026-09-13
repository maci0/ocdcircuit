# Neural networks & genetic algorithms for layout — verdict (ocdcircuit relevance)

Short answer: **genetic algorithms can help in one narrow role** (multi-objective
fronts, if presets ever fail); **neural networks currently cannot justify their
cost** at this scale. Both are already covered in `constraint-methods.md` §4 —
this note adds the NN evidence (RL placers, DREAMPlace, ChiPBench) and the
bottom line.

## Neural networks

- **RL macro placement** (Google): macro placement framed as a sequential MDP,
  policy places macros one at a time — Mirhoseini et al., "A graph placement
  methodology for fast chip design", Nature 2021 —
  [record](https://www.mendeley.com/catalogue/fce4bd60-9727-3598-8a2d-74a5a545c044/)
  ([bibtex](https://researchr.org/publication/MirhoseiniGYJSW21/bibtex)).
  Follow-ups: MaskPlace (visual wiremask/viewmask/positionmask + dense reward),
  ChiPFormer (offline RL pretraining, fine-tune on unseen chips) — both
  summarized in the ChiPBench paper below. Requires training distribution over
  many chips + GPU; inference is fast but generalization across designs is the
  open problem.
- **DREAMPlace**: analytical placement (ePlace/RePlAce-class wirelength +
  density) re-implemented with PyTorch operators for 30×+ GPU speedup —
  [paper PDF](http://yibolin.com/publications/papers/PLACE_DAC2019_Lin.pdf)
  ([IEEE record](https://ieeexplore.ieee.org/document/9122053)); AutoDMP adds
  multi-objective Bayesian hyperparameter tuning on top. Key insight: the "deep
  learning" here is autograd + GPU kernels as a fast numerics backend, not a
  learned layout policy — the algorithm is still classical analytical
  placement.
- **The sobering benchmark — ChiPBench** (Wang et al., 2024): 20 real circuits
  run end-to-end through OpenROAD, six SOTA AI placers (SA, WireMask-EA,
  DREAMPlace, AutoDMP, MaskPlace, ChiPFormer) evaluated on **final PPA**
  (power, timing, area), not proxy metrics —
  [full text (HTML)](https://arxiv.org/html/2407.15026v2). Finding: AI placers
  that dominate intermediate metrics (MacroHPWL) still lose to OpenROAD's
  default flow on end-to-end PPA; surrogate metrics correlate weakly with final
  quality. Quoted direction (paraphrase, see §7 of the paper): optimize the
  final objective, not the proxy.
- **Commercial AI PCB tools** (Quilter, DeepPCB) exist and route real boards —
  [comparison](https://www.protoflow.ai/compare/ai-pcb-autorouter-comparison),
  [Quilter tech talks](https://www.allaboutcircuits.com/events/summit-series-2025/quilter/other/autonomous-pcb-design-technology-for-the-curious-minds/) —
  but they are cloud services with proprietary models, not adoptable methods.
  ocdcircuit already borrows their *idea* (multi-seed candidates).
- **Verdict for ocdcircuit**: no. Reasons: (1) every NN approach needs either a
  training corpus of boards (RL/policy) or a GPU numerics stack (DREAMPlace) —
  both violate zero-dependency stdlib-only; (2) ChiPBench shows proxy-metric
  wins don't transfer to final quality, and ocdcircuit's true objective (DRC
  clean + short + human-sensible) is even harder to encode as a loss; (3) at
  tens of parts the search space is small enough that diffusion + repair + LNS
  already explore it. Revisit only if: a corpus of thousands of `.ocd` boards
  exists AND a learned cost model beats hand-tuned weights on held-out boards.
  The DREAMPlace trick worth stealing without NNs: *vectorized cost
  evaluation* (numpy-style batching) if the O(n²) loop ever becomes the
  bottleneck — but that's an implementation speedup, not a neural method.

## Genetic algorithms

- **Where they work**: black-box optimization over encodings where crossover
  preserves validity — sequence-pair/B\*-tree floorplan representations with
  order-preserving operators (see `constraint-methods.md` §2, §4). WireMask-EA
  (NeurIPS 2023) pairs a wiremask-guided greedy decoder with EA/random
  search/BO over the genotype — i.e. evolution proposes, greedy decoder
  disposes — and topped MacroHPWL in ChiPBench (while still losing end-to-end
  PPA, per above).
- **Where they break**: naive coordinate-blend crossover on raw (x,y) layouts
  is destructive — children of two good placements are typically two bad
  halves stitched together (practitioner consensus; AIPS-96 order-crossover
  note [cited in methods brief]). At tens of parts, a population of 50–200
  with full cost evals each is also 10–50× the eval budget of multi-start
  diffusion for no structural advantage.
- **The one role that fits — NSGA-II multi-objective fronts** (Deb et al.
  2002, [record](https://doi.org/10.5281/zenodo.6487417)): the tool already
  has three competing objectives with named presets (diffusion/wirelength,
  compact/area, thermal/spreading). If users ever need the actual tradeoff
  surface instead of three presets, NSGA-II's non-dominated sorting is the
  textbook answer. Until then: **skip** — presets + `pull`/`spread` knobs
  cover it.
- **Cheaper substitutes already ranked**: min-conflicts repair and LNS
  ruin-recreate (`constraint-methods.md` §4) get 80% of EA's robustness at a
  fraction of the code — a repair loop *is* a (1+1)-EA with a smart mutation.

## Bottom line

| Method | Verdict |
|---|---|
| RL/policy placers (Nature 2021, MaskPlace, ChiPFormer) | No — needs corpus + GPU, proxy-metric trap per ChiPBench |
| DREAMPlace-style GPU analytical | No — algorithm is classical; only the backend is NN-flavored |
| Learned cost model | Later — needs thousands of boards + held-out wins |
| Plain GA on (x,y) | No — destructive crossover, 10–50× eval budget |
| WireMask-style EA + greedy decoder | Interesting but later — decoder first (legalizer), EA maybe never |
| NSGA-II Pareto fronts | Conditional — only if 3 presets fail users |
| Min-conflicts / LNS (evolution-adjacent) | **Yes — already ranked #2 adoption** |

## Scale appendix: what breaks at 1000 parts (measured on this codebase)

Your largest example is 20 parts (`boards/pico_tmc2209/`); "not at this
scale" meant that. I benchmarked synthetic boards to find the real ceilings:

| n | place (seeds=1, iters=50) | DRC (no traces) | DRC (routed) | dense-net overlaps |
|---|---|---|---|---|
| 20 | 0.02 s | ~0 | — | 0 |
| 100 | 0.25 s | 0.01 s | — | 0 |
| 300 | 1.9 s | 0.05 s | 0.24 s | 7 |
| 1000 | 18.8 s | 0.47 s | 2.1 s | 396 |

Methodology footnote (audit-driven): synthetic R0805 boards, sparse-chain nets
for timing columns, dense 4-random-parts-per-net (seed 7) for the overlap
column; routed-DRC via lroute; machine-specific absolutes (±25% on re-run —
only the ~quadratic ratios are machine-independent); overlap column's 300-row
"7" coincides with the sparse-chain value and is suspect (see audit). The
`benches/monster6502/` bench supersedes this table for serious work.

Default settings multiply place by ~30× (seeds=4, iters=400): ~10 min at
n=1000. Scaling is ~quadratic (9.9× time for 3.3× parts) — the O(n²) pairwise
repulsion, exactly as the `ponytail:` comment warns. Maze router: 1.26 s at
n=50 on 100×100 mm (grid cells grow with board area, not just parts).

Two separate failures: **speed** (quadratic placer, grid-size router, O(n²)
DRC) and **quality** (396 overlaps at n=1000 on dense nets while sparse chains
stay clean — diffusion can't resolve contention it was never designed for).

What flips at that scale, in order:
1. **Spatial hashing / bin-density repulsion** replaces O(n²) pairwise loop
   (OpenROAD does RUDY-style congested-tile inflation;
   [docs](https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html)).
   Biggest speed win, still zero-dep.
2. **VPSC-style legalizer + min-conflicts/LNS repair** (`constraint-methods.md`
   §§3–4) stop being optional and become the quality backbone — the benchmarks
   above show overlap count exploding while sparse boards stay clean.
3. **WireMask-EA becomes interesting**: WireMask-BBO with plain (1+1)-EA beat
   MaskPlace RL and DREAMPlace on 5–6/7 ISPD2005 chips (hundreds of macros)
   — [full text](https://ar5iv.labs.arxiv.org/html/2306.16844). Key trick is
   the wiremask-guided greedy decoder (genotype → legal phenotype), not the EA
   itself. At n=1000 that decoder earns its keep; at n=20 it's overhead.
4. **GPU analytical (DREAMPlace) / RL policie**s: only if boards stay at 1000+
   AND a corpus exists. Note WireMask-EA beat both with zero training.
5. **Multilevel coarsening** (Walshaw force-directed multilevel —
   [PDF](https://chriswalshaw.co.uk/papers/fulltext/WalshawTR6000.pdf);
   Harel–Koren fast multiscale —
   [PDF](https://jgaa.info/accepted/2002/HarelKoren2002.6.3.pdf)):
   cluster → place coarse → refine. The standard answer when O(n²) dies;
   implement only when (1)+(2) stop being enough.

Rule of thumb: n < 100 → current stack wins. 100–300 → add (1)+(2). 1000+ →
(3) enters, (4)/(5) only with corpus or sustained pain. So: NNs still no;
GAs graduate from "skip" to "the WireMask-flavored kind, with a decoder".

## Monster6502 regime (~4000 discretes on 305×381 mm): what actually happens

Reference board: the MOnSter 6502 — "huge, at 12 × 15 inches, with over 4000
surface mount components" —
[Evil Mad Scientist](https://www.evilmadscientist.com/2016/6502/).
Extrapolating the measured table:

- **Placer**: ~5 min for the toy run (seeds=1, iters=50), **~2.5 h at default
  settings** — and quality is the real wall, not time: dense nets already show
  396 overlaps at n=1000. Diffusion alone will not produce a legal 4000-part
  board, however long you anneal.
- **Maze router**: 1221×1525×2 ≈ **3.7 M states per A\* search**, per pin pair,
  thousands of pairs. Not slow — infeasible. The fixed 0.25 mm grid is the
  `ponytail:` ceiling firing (`maze.py`: "coarser when boards grow").
- **DRC**: ~34 s routed (O(n²) trace-pair checks) — annoying but survivable;
  fix last.

Honest architecture for that regime (all in existing briefs, now load-bearing
instead of optional):

1. **Hierarchy first** — the actual MOnSter 6502 is not 4000 free parts; it's
   repeated functional blocks (gates, latches, ROM rows). `use ... as` includes
   + `near-group` already express this: place ~40 blocks of ~100 parts, never
   4000 flat. This single modeling choice beats every solver upgrade.
2. **Multilevel placer** (Walshaw / Harel–Koren, cited above): coarsen each
   block → place → refine. Replaces flat O(n²) diffusion, which is both too
   slow and too low-quality here.
3. **Coarse grid + refinement for routing**: route on 1–2 mm grid first (or
   L-route trunks), then refine — or partition per block and stitch. Never run
   0.25 mm A\* over 3.7 M states × thousands of pairs.
4. **WireMask-style EA + legalizer** as the block-level optimizer: decoder
   guarantees legality, EA explores. This is the scale WireMask-BBO was built
   for (hundreds of macros on ISPD2005).
5. **NNs**: still no — same reasons (no corpus, proxy-metric trap), now joined
   by "the classical pieces aren't built yet". A learned policy on top of a
   broken 4000-part flow optimizes nothing.

Bottom line: at Monster scale the answer isn't a better flat solver — it's
**hierarchy + multilevel + coarser grids**, with the EA/decoder combo at block
level. The current stack (flat diffusion + fine maze) is a <100-part tool;
100–300 needs items (1)+(2) from the list above; 4000 needs this section.

- Mirhoseini et al. 2021 — https://www.mendeley.com/catalogue/fce4bd60-9727-3598-8a2d-74a5a545c044/ · https://researchr.org/publication/MirhoseiniGYJSW21/bibtex
- DREAMPlace (DAC'19) — http://yibolin.com/publications/papers/PLACE_DAC2019_Lin.pdf · https://ieeexplore.ieee.org/document/9122053
- ChiPBench (arXiv:2407.15026) — https://arxiv.org/html/2407.15026v2
- Quilter/DeepPCB comparisons — https://www.protoflow.ai/compare/ai-pcb-autorouter-comparison · https://www.allaboutcircuits.com/events/summit-series-2025/quilter/other/autonomous-pcb-design-technology-for-the-curious-minds/
- NSGA-II — https://doi.org/10.5281/zenodo.6487417
