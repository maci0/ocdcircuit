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
