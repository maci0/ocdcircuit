# ADR-0004 — Circuit language: .ocd text, Python builder, JSON wire IR

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

## Update (mermaid-style nets, meta lines, round/square zones)
- Nets read as flows: `NAME [attrs] :: A.1 <--> B.2` (legacy `net X:` still
  parses); layer/width constraints fold onto the net line in dumps.
- `meta KEY value...`: board metadata (title/rev/desc), flows to KiCad
  title_block, EasyEDA title, agent IR.
- Zones: `keepout x y dN` (round) + `keepout near REF [dN|WxH]` (live
  deadzone, anchor exempt); one `in_zone()` predicate serves maze/DRC/export.
