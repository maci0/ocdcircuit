# Plan: PCB-design fine-tuned AI models (pure research)

## Key questions
1. Which fine-tuned models exist specifically for PCB/schematic/layout tasks (placement, routing, footprint, DRC, BOM, datasheet parsing)? Names, base models, licenses.
2. What datasets underpin them (board images, Gerbers, netlists, KiCad, circuit.json, datasheets)? Size, format, access (open vs gated).
3. What do papers report: methods (SFT/RL/adapter), benchmarks, metrics, and limits vs general LLMs?
4. What do industry players use (Quilter RL, DeepPCB RL, Flux copilot, CELUS, Circuit Mind, JITX)? Where fine-tuning vs prompting vs RL-from-physics?
5. What open-source code/repos fine-tune or distill PCB knowledge (tscircuit, KiCad MCP, DeepSeek/Claude workflows)?
6. Where are the gaps a small team could exploit (local unmetered solve + LLM-native text format)?

## Source strategy
- arXiv API + abs pages for papers (prefer abstracts/HTML over PDF).
- OpenAlex API for metadata/citations/references.
- Hugging Face Hub read-only API for models/datasets (no key → public only; gated = blocked).
- Vendor docs/blogs (Quilter, DeepPCB, Flux, CELUS, Circuit Mind, JITX, tscircuit) + saas.md in-repo.
- No paywall bypass. Unreachable = blocked, never inferred.

## Scale decision
Broad multi-angle → fan out 4 subagents (papers / HF models+datasets / industry tools / code+workflows), lead synthesizes into brief.

## Task ledger
- [ ] write plan (this file)
- [ ] fan out 4 subagents
- [ ] lead: arXiv + OpenAlex spot-checks
- [ ] synthesize brief with inline citations
- [ ] verify claims, record caveats
