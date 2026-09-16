# ADR-0002 — Constraint solver: diffusion + maze, not ILP/MILP

## Decision
Placement = Langevin diffusion (net-spring drift + repulsion + decaying
noise, multi-seed best-of) on
`wirelength + overlap×BIG + edge penalties` (+ keepout as maze soft
preferential cost + DRC warnings, not a placer term); layer assignment =
greedy bbox-overlap minimization; routing = ordered L-routes, A* maze
default (MST trunk legs + gated 2-round rip-up). Zero dependencies.

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
- Router ladder (blinky @ seeds=2, measured): lroute instant/14 warnings
  (estimate) → coarse instant/23 warnings (10× maze speed for 1000+ part
  boards, refine with maze after) → maze 0.3s/clean (default) → wiremask
  0.9s/clean (EA layer assignment; same quality, 3× time — use when layer
  choice, not geometry, is the bottleneck).
- Auto-select at 1000+ parts (no flags): placer → multilevel, router →
  coarse. Explicit flags always win; the default path follows the ladder.
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

## Update (maze net order + rip-up victim)
- Big-nets-first maze order (multi-pin power busses claim trunks while the
  board is open; small wires thread gaps after): breath_ketone 190→52
  jumpers, all other boards unchanged-or-clean (pico 20→24 jumpers, DRC
  clean). Small-first walled big nets off — completion beats convention.
- Corridor rip-up victim (blocker with most cells in failed net's pads
  bbox + 4mm) replaces endpoint-distance scoring. Pathfinder history
  adder tried and reverted: +24 jumpers on breath (rematches re-fight
  the same corridor); order + victim choice carry the gain.
