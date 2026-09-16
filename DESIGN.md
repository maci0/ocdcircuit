# DESIGN.md — OCD Studio

Durable visual decisions, recorded from the built world (ground truth over intention).

## Worlds

Two surfaces, one token family:

### 1. Workshop (Operate)
Paper spec sheet + one terminal. Warm paper ground (`--paper #f7f5f0`), white
cards (`--card #fffdf8`), one signal green (`--signal #0f5c37`), one dark
surface (the job-file editor, `--term #101418`). Sans carries what a person
reads; mono is the machine's voice (source, readouts, listings). State is a
word in a pill, never a bare colour.
Flux-dark is the same tokens re-pointed (`body.dark`), canvas palette
re-pointed at paint time — never a second stylesheet. Cockpit (all panels)
is the default; Layout/Schematic/3D/Docs tabs are opt-in per click,
double-click returns. The agent turn reads as a timeline (thought trace +
plan checklist + follow-up chips), rendered from the existing reply/log —
never a bare log.

### 2. Landing + gate (Persuade)
Dark cosmos (`--term` family, green primary `--term-ok #5fd894`): nav, hook
("Your whole team. One board. Zero merge conflicts." + "Open a board, send the
link, you're co-editing"), glowing board card (signal-green border + glow,
never category purple) over the live collab canvas (cursors, traces arc,
glow dots travel, deterministic starfield, gold pads — authored, never a
stock photo), one CTA ("Start a board together"). Below the fold: the collab
strip (maya + leo + priya, rev 42 — realtime story in one row) and the AI
engine story (copilot drafts schematic → places → routes, you stay lead).
Honest flow strip (1 idea → 2 schematic → 3 layout → 4 make — no invented
counts). Known exception: the board card carries a signal-green glow on dark
(detector dark-glow) — deliberate, it is the hero's one authored moment,
in-world, not decoration. The gate keeps the split-screen
form + shelf behind the CTA. Mobile collapses to form-only;
`prefers-reduced-motion` kills the canvas. Brand mark is the favicon (SVG data
URI) on both landing and workshop — never an empty `data:,` icon.

## Contracts
- Every control carries a visible word; a glyph alone is not a label.
- Overlays escape their container (layer/part dropdowns position out of the head).
- Errors name the problem and the recovery (`Username can't be blank!`,
  `wrong name or password`, `give the board a usable name`).
- Focus is always visible (2px signal/green outline).
- Sessions: HttpOnly + SameSite=Lax cookie; JS never reads the token.
- Fab tiles are the vendors' own marks, hosted locally
  (`ocdcircuit/assets/fabs/`, 192×64 PNGs — never hotlinked). The landing
  strip loads them from `/fab-logo/<key>` (cached, lazy); quote rows still
  embed `fab.logo()` data URIs. `fab.SOURCES` records each file's origin;
  the strip carries a "logos belong to their owners" note. `fab.MARKS`
  monograms remain the fallback when a tile is missing. Dark-on-
  transparent marks (Sierra, NextPCB) sit on white tiles so they read
  on the dark strip.
