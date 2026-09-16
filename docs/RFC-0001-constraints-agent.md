# RFC-0001 — Constraints DSL + agent patch API

Status: Accepted (historical — grammar reference; decisions in ADR-0002, ADR-0004)

Shipped proposal kept here for the constraint/patch surface. Not an open
RFC: options were decided; do not treat this as a pending design review.

## Constraints (dicts, JSON-serializable)
- `fixed {ref,x,y}` · `near {a,b,w}` · `keepout` (rect `{x,y,w,h}` / round
  `d` / `near` ref; ADR-0004) · `edge {margin}`
- `layer {net,layer}` · `width {net,width}` · `power {nets[]}` (wider traces)
- `match {nets[]}` · `diff {p,n,gap}` (placer cost + DRC skew; ADR-0002)
- `pour {net,layer}` (negative Gerber plane + KiCad zone; routers skip)

Canonical grammar / dumps forms: `docs/OCD.md`. This list is the agent dict
shape; do not duplicate prose there.

## Patch ops (`agent.apply_patch`)
`add_part move_part remove_part connect constrain set_board route optimize`
(+ `check export render` result-capture; `add_part`/`connect` take `attrs`).
Each op runs through `Context` → fully undoable; returns applied count.

## NL shortcuts (`agent.parse_constraint`)
"keep U1 near C1" · "fix J1 at 3 10" · "route GND on bottom" ·
"trace VCC 0.5" · "board 30 x 20". Regexes only — LLM does the real parsing,
this is the deterministic fallback.

## Open (v1)
Remaining: keepout-by-net-class. (`match`/`diff` and maze-soft `keepout`
already shipped — see ADR-0002.)
