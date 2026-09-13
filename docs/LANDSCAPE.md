# LANDSCAPE — what we steal from whom

- **tscircuit**: circuits-as-code, footprint registry convention, `check`
  pipeline (netlist → placement → routing → DRC) mirrored in our demo/tests.
- **atopile**: hierarchical modules + interfaces → our `Module` components and
  `requires/provides` services; declarative config ≈ `.ato` intent.
- **flux.ai**: copilot-side structured access → our IR + patch ops; sim hooks
  reserved as v1 `simulate` op.
- **Quilter**: generate N candidate layouts, keep best → multi-seed `optimize`.
- **Paper (Cordis)**: revertible effects, reactive coeffects, declarative
  loader + HMR → `core.py` / `Loader` verbatim as architecture.
