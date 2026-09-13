# ocdcircuit — PRD

Circuit design tool where **circuits are code**, **agents are first-class users**,
and every edit is reversible. Principles: [cordiverse/paper](https://github.com/cordiverse/paper)
(spatiotemporal composability). Steals: tscircuit (code-first, checks pipeline),
atopile (modules/interfaces), flux.ai (copilot hooks), Quilter (candidate layouts).

## Users
1. Humans writing circuits as code (Python DSL + declarative YAML/JSON).
2. LLM agents driving the same API via structured IR + patch ops (undoable).

## Scope
- v0 (this round): core context/component model, parts lib, constraint solver
  (placement + layer assignment), L-router, DRC vs JLC 2-layer rules,
  JLCPCB export (Gerbers, drill, BOM, CPL), agent patch API, 555 blinky demo.
- v1: push-and-shove router, schematic SVG, simulation hooks, registry, MCP server.
- Non-goals v0: schematic capture GUI, multi-board/panel, high-speed SI analysis.

## Success (v0)
One command builds the demo board → DRC zero errors → JLC fab files exist →
any agent patch fully undoes.
