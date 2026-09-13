# Plan: other constraint methods (beyond diffusion+maze)

## Key questions
1. Which constraint-solver families were NOT (or barely) covered in `docs/constraint-solvers.md` yet matter for a small PCB tool? Global constraints (diffn), SMT/OMT, floorplan representations, packing heuristics, ALM/ADMM/projection, VPSC/separation, Cassowary, min-conflicts/LNS/tabu, GA/NSGA-II, PSO/ACO, Steiner/FLUTE, pin assignment/escape, Sugiyama schematic layout, analog symmetry constraints.
2. Per method: what is it, key paper/solver + source URL, what ocdcircuit constraint(s) it maps to, cost (lines? dependency?), verdict (adopt now / later / skip).
3. Keep zero-dependency bias: pure-Python-tens-of-lines wins rank above solver dependencies.

## Source strategy
- Same as before: abstracts/metadata/docs over PDFs; arXiv API + arxiv.org/abs; OpenAlex API; official docs (OR-Tools AddNoOverlap2D, MiniZinc diffn, Z3, OpenROAD, graphviz). No paywalls; blocked = flagged, never invented.
- No HF/AlphaXiv keys in env → arXiv + OpenAlex fallback (unchanged).

## Scale decision
Lead-owned with 5-way fan-out (one prompt per angle below), then lead synthesizes. No workflow tool.

## Task ledger
- [x] Write this plan
- [x] Fan out 5 subagent angles (a–e)
- [x] Direct-verify anchor claims (OpenAlex/docs fetches)
- [x] Write `outputs/other-constraint-methods-brief.md` (Summary/Background/Findings/Open Questions/References)
- [x] Copy to `docs/constraint-methods.md` + cross-link from `docs/constraint-solvers.md`
