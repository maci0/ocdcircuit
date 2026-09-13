# Plan: constraint solver algorithms (ocdcircuit relevance)

## Key questions
1. What solver families matter for PCB-scale problems (10s–100s parts)? CSP/backtracking+propagation, SAT/CP-SAT, ILP/MILP, metaheuristics (SA/hill-climb/Langevin), force-directed/analytical placement, grid routing (Lee/A*/Hadlock, rip-up & reroute, negotiated-congestion).
2. What does ocdcircuit have now? Langevin diffusion placer (`ocdcircuit/solver.py`: net-spring + repulsion + noise, multi-seed best-of), greedy bbox layer assignment, L-router + A* maze router (`ocdcircuit/maze.py`, 0.25mm grid, bend+via penalties, soft courtyard terrain). Constraints: fixed/near/keepout/edge/layer/width/power/match/diff (`docs/OCD.md`, `docs/RFC-0001-constraints-agent.md`).
3. What is the cheapest upgrade per gap? Overlap handling, constraint propagation before search, rip-up & reroute / ordering, layer assignment beyond greedy, length-matching/diff-pair routing.
4. What to explicitly NOT adopt? Full ILP/MILP dependency, industrial analytical placers (ePlace/RePlAce), push-and-shove — why (scale + zero-dep constraint).

## Source strategy
- Papers: arXiv API + arxiv.org/abs pages (force-directed, SA, ePlace, maze routing, negotiated congestion); metadata/citations via OpenAlex (api.openalex.org). Prefer abstracts/metadata over PDF extraction.
- Docs/official: OR-Tools CP-SAT, python-constraint, KiCad/KiRouter, tscircuit/snaguma context if reachable.
- Web: broad queries (placement, routing, CSP, CP-SAT, SA vs hill-climb, A* vs Lee).
- Local: `ocdcircuit/solver.py`, `maze.py`, `circuit.py`, `drc.py`, `docs/ADR-0002-solver.md`, `docs/LANDSCAPE.md`.
- No paywalls; unreachable = blocked, never inferred. HF key: treat unset as blocked (no gated HF datasets needed here). No AlphaXiv key → arXiv + OpenAlex fallback.

## Scale decision
Lead-owned with 5-way fan-out (subagents, one prompt each): (a) CSP/SAT/CP-SAT, (b) ILP/MILP + SA/metaheuristics, (c) placement algorithms PCB, (d) routing algorithms PCB, (e) force-directed/diffusion/Langevin + ocdcircuit mapping. Then lead synthesizes. No workflow tool (5 parallel subagents suffice).

## Task ledger
- [x] Map local solver + constraints
- [x] Write this plan
- [x] Fan out 5 subagent angles
- [x] Verify claims vs sources, flag misattributions
- [x] Write `outputs/do-deep-research-on-constraint-solver-algorithms-put-the-out-brief.md` (Summary/Background/Findings/Open Questions/References, inline citations)
- [x] Copy brief to `docs/constraint-solvers.md` (user asked: output in docs)
