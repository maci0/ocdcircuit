# ADR-0004 — Circuit language: .ocd text, Python builder, JSON wire IR

## Decision
Three faces, one model: `.ocd` line-text (`agent.dumps/loads`) is what humans
read/write; Python (`Board` API) builds it; JSON (`ir/to_json/from_json`) is
the agent wire format. One constraint grammar (`parse_constraint`) serves both.

## Why
JSON is machine IR, not human-readable. A custom AST language would be a third
thing to maintain — `.ocd` reuses the constraint strings agents already emit.
