# ADR-0002 — Constraint solver: stochastic hill-climb, not ILP/MILP

## Decision
Placement = seeded random hill-climbing on
`wirelength + overlap×BIG + keepout/edge penalties`; layer assignment = greedy
bbox-overlap minimization; routing = ordered L-routes. Zero dependencies.

## Why
Boards here are tens of parts: an ILP solver dependency buys nothing v0.
Seeded RNG = reproducible (agents need determinism); single cost function =
new objectives plug in as one term. Quilter-style "candidates" fall out free:
run N seeds, keep the best.

## Consequences
`ponytail:` O(n²) overlap + no push-and-shove — crossings surface as DRC
*warnings*, not silent shorts. Upgrade to real router when warnings annoy.

## Update (maze + placer families)
- `router:maze` (`maze.py`): A* wavefront, part/pad/copper obstacles, vias,
  per-net frames. Honest trade: slower (~0.6s demo) for fewer DRC warnings
  (compact+maze = zero warnings on blinky).
- Placer objective knobs (`pull`/`spread`/`edge`/`thermal`) + plugin presets:
  diffusion (default), compact (area), thermal (big bodies drift to edges
  for heatsinking + repel harder; separation wins are board-dependent —
  free layouts ring the perimeter, anchored ones spread).
- Hierarchical/multilevel preserve instance structure (repeated channels
  stay identical) at the cost of packing optimality: rigid stamps can
  overlap where free placement wouldn't (pico: hierarchical overlaps at
  seeds where diffusion is clean). Pick structure when channels must
  match, diffusion when they must pack.
- `match`/`diff` constraints feed placer cost + DRC skew warnings.
- DRC `_seg_dist` collinear case now measures real 1D gaps (was: false 0).

## Update (research items 1+3, per-layer maze)
- Min-conflicts repair (`solver._repair`): most-overlapped part tries 12
  local moves × 8 rounds after the seed loop, inside the undoable effect.
- Rectilinear-MST legs (`maze._mst_pairs`): Prim's over Manhattan pad
  distance replaces pin-order chaining (blinky 109→98 segs, mitox 1536→987).
- Per-layer copper/halo: L0 copper no longer walls L1 (FR4 isolates); PTH
  pads still span all layers. Pico 197→56 warnings on the same change.
