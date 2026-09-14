# Plan: parts libraries deep research

## Key questions
1. What are the major open parts/footprint libraries (KiCad official libs, SnapEDA, UltraLibrarian, SamacSys, tscircuit registry, EasyEDA/LCSC, Quadcept, Altium vault) — license, size, format, scriptability?
2. What are the footprint/spec standards (IPC-7351/7352 courtyard/silkscreen rules, JEDEC package outlines, KiCad FP naming convention) the tool's 101 stdlib footprints should align with?
3. What are the parametric part-data sources (Octopart/Nexar, LCSC/JLCPCB, DigiKey/Mouser APIs, Common Parts Library) for value→MPN/BOM enrichment?
4. What does ocd already do (101 stdlib footprints, .fp custom, KiCad/Eagle/tscircuit import via foreign.py) and what's the cheapest next enrichment (LCSC IDs? IPC courtyard audit? KiCad-lib sync? attrs already carry lcsc/mpn)?

## Source strategy
- Official docs/repos first: KiCad libraries (GitLab), IPC summaries, tscircuit docs, SKiDl, SnapEDA/UltraLibrarian terms pages, Octopart/Nexar API docs, LCSC.
- OpenAlex/arXiv only if modeling/paper claims arise (unlikely — docs research). HF/AlphaXiv unset → blocked (not expected to matter). No paywalls.

## Scale decision
Lead-owned with 4-way fan-out (one prompt per angle), then lead synthesizes. No workflow tool.

## Task ledger
- [x] Inventory local library (101 stdlib, .fp, foreign importers)
- [x] Write this plan
- [x] Fan out 4 subagent angles (a–d)
- [x] Direct-verify anchor claims (licenses, counts, API existence)
- [x] Write `outputs/do-deep-research-on-parts-libraries-and-document-them.-brief.md`
- [x] Copy to `docs/parts-libraries.md` + cross-link
