# ocdcircuit — PRD (historical: kept for intent)

Status: Accepted (historical)

Circuit design tool where **circuits are code**, **agents are first-class users**,
and every edit is reversible. Principles: [cordiverse/paper](https://github.com/cordiverse/paper)
(spatiotemporal composability). Steals: tscircuit (code-first, checks pipeline),
atopile (modules/interfaces), flux.ai (copilot hooks), Quilter (candidate layouts).

## Users
1. Humans writing circuits as code (Python DSL + declarative `.ocd` / JSON; ADR-0004).
2. LLM agents driving the same API via structured IR + patch ops (undoable).

## Scope
- v0 (shipped): core context/component model, parts lib, constraint solver
  (placement + layer assignment), L-router, DRC vs JLC 2-layer rules,
  JLCPCB export (Gerbers, drill, BOM, CPL), agent patch API, 555 blinky demo.
- v1 shipped: schematic SVG, simulation hooks, registry, MCP server;
  maze router as project default (`board.toml` / ADR-0002).
- v1 open: push-and-shove router (ADR-0002: crossings remain DRC warnings).
- Non-goals v0: schematic capture GUI, multi-board/panel, high-speed SI analysis.

## Success (v0)
One command builds the demo board → DRC zero errors → JLC fab files exist →
any agent patch fully undoes.

## Decisions
- ADR-0001 context core · ADR-0002 solver · ADR-0003 DRC/export ·
  ADR-0004 language · ADR-0005 paper core · RFC-0001 constraints grammar
