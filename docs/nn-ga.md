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

## References

- Mirhoseini et al. 2021 — https://www.mendeley.com/catalogue/fce4bd60-9727-3598-8a2d-74a5a545c044/ · https://researchr.org/publication/MirhoseiniGYJSW21/bibtex
- DREAMPlace (DAC'19) — http://yibolin.com/publications/papers/PLACE_DAC2019_Lin.pdf · https://ieeexplore.ieee.org/document/9122053
- ChiPBench (arXiv:2407.15026) — https://arxiv.org/html/2407.15026v2
- Quilter/DeepPCB comparisons — https://www.protoflow.ai/compare/ai-pcb-autorouter-comparison · https://www.allaboutcircuits.com/events/summit-series-2025/quilter/other/autonomous-pcb-design-technology-for-the-curious-minds/
- NSGA-II — https://doi.org/10.5281/zenodo.6487417
