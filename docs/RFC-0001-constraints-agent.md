# RFC-0001 — Constraints DSL + agent patch API (historical: shipped, kept for grammar)

## Constraints (dicts, JSON-serializable)
- `fixed {ref,x,y}` · `near {a,b,w}` · `keepout {x,y,w,h}` · `edge {margin}`
- `layer {net,layer}` · `width {net,width}` · `power {nets[]}` (wider traces)

## Patch ops (`agent.apply_patch`)
`add_part move_part remove_part connect constrain set_board route optimize`
(+ `check export render` result-capture; `add_part`/`connect` take `attrs`).
Each op runs through `Context` → fully undoable; returns applied count.

## NL shortcuts (`agent.parse_constraint`)
"keep U1 near C1" · "fix J1 at 3 10" · "route GND on bottom" ·
"trace VCC 0.5" · "board 30 x 20". Regexes only — LLM does the real parsing,
this is the deterministic fallback.

## Open (v1): length-matching/diff-pair constraints, keepout-by-net-class.
