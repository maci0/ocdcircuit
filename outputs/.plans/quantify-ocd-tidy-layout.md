# Plan: quantify OCD-compatible layout (neat/tidy metrics)

## Key questions
1. What does "OCD-neat" mean measurably? Candidate metric families: straightness/bend count, orthogonality (45°/90° adherence), alignment (shared x/y rows/columns), grid snap, symmetry/mirroring, uniform spacing/pitch, orientation consistency, length-matching skew, via count, crossing count, layer discipline, copper balance, silk alignment/overlap.
2. What does the literature offer? Aesthetics metrics from graph drawing (Purchase et al.), orthogonal layout quality, DRC/manufacturability metrics (already have fab profiles), IPC.npmjs trace/clearance rules (already have), human readability studies.
3. What should the tool compute? A small `tidy(board) -> dict[str, float]` scorecard: each metric 0..1 or physical units, cheap O(n)/O(n²) on tens of parts, stdlib only. Rank by cost × value; wire into DRC warnings or a `tidy` report command — recommendation only, no implementation this round (research brief).
4. Thresholds: what counts as "neat" per metric (e.g. bends/net, % orthogonal segs, alignment tolerance mm)? Literature-backed where possible, judgment flagged otherwise.

## Source strategy
- Same as before: abstracts/metadata/docs over PDFs; arXiv + arxiv.org/abs; OpenAlex API; official docs (graphviz, KiCad DRC, IPC summaries, adaptagrams). No paywalls; blocked = flagged.
- Local: `ocdcircuit/solver.py` cost(), `drc.py`, `maze.py` (bend/via penalties exist), `silk.py`, fab profiles.

## Scale decision
Lead-owned with 4-way fan-out (one prompt per angle below), then lead synthesizes + defines the draft `tidy()` scorecard. No workflow tool.

## Task ledger
- [x] Write this plan
- [ ] Fan out 4 subagent angles (a–d)
- [ ] Direct-verify anchor claims
- [ ] Write `outputs/tidy-metrics-brief.md` (Summary/Background/Findings incl. metric table/Open Questions/References)
- [ ] Copy to `docs/tidy-metrics.md` + cross-link from solver/methods briefs
