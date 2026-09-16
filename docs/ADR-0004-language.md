# ADR-0004 — Circuit language: .ocd text, Python builder, JSON wire IR

Status: Accepted

## Decision
Three faces, one model: `.ocd` line-text (`agent.dumps/loads`) is what humans
read/write; Python (`Board` API) builds it; JSON (`ir/to_json/from_json`) is
the agent wire format. One constraint grammar (`parse_constraint`) serves both.

## Why
JSON is machine IR, not human-readable. A custom AST language would be a third
thing to maintain — `.ocd` reuses the constraint strings agents already emit.

## Update (declarative API)
`.ocd` text was always declarative; the API caught up. `Board.declare()`
takes desired state ({parts, nets, constraints, board}) and reconciles:
add missing, drop stale, update changed — idempotent, order-independent,
atomic via rollback. MCP `set_state` exposes it (prefer over `apply_patch`
verbs for agents). Inverse ops added: `disconnect`, `drop_net`,
`unconstrain`. Studio text→build is declare-by-construction (full reload).

## Update (attrs everywhere, conflict preservation)
- Part attrs (`lcsc/rot/dnp/...`) and net attrs (`class=`) round-trip
  through all three faces: `.ocd` text, JSON IR (`ir_of`/`from_ir`),
  `apply_patch` ops, and `declare`/`set_state` (undoable setters,
  net specs accept `{pins, attrs}`).
- Dumps leaves conflicting layer/width statements unfolded (ordered
  `route`/`trace` lines) — folding first-wins would flip last-wins
  runtime resolution on reload.
- Duplicate sources resolve last-wins uniformly (parts and nets, both
  directions); precedence is by file order per target.

## Update (mermaid-style nets, meta lines, round/square zones)
- Nets read as flows: `NAME [attrs] :: A.1 <--> B.2` (legacy `net X:` still
  parses); layer/width constraints fold onto the net line in dumps.
- `meta KEY value...`: board metadata (title/rev/desc), flows to KiCad
  title_block, EasyEDA title, agent IR.
- Zones: `keepout x y dN` (round) + `keepout near REF [dN|WxH]` (live
  deadzone, anchor exempt); one `in_zone()` predicate serves maze/DRC/export.
