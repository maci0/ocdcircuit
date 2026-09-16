# OCD Studio — operator guide

Visual editor for `.ocd` boards: source pane, PCB, schematic, 3D, DRC —
one cockpit, no tabs. Edits are real: every good build is saved to disk
and committed to a 100-deep undo history.

```bash
make run                            # :8077, blinky_555
BOARD=boards/psu.ocd make run       # another board
OCD_PORT=8078 python -m apps.studio boards/blinky_555.ocd   # custom port
```

First visit: create a local Studio account (`.ocd-users` beside the boards),
then open a board from the shelf (or the launch file). Creating a blank board,
starting from the prompt, or picking a template opens the workshop immediately
(no second click on the new card). Collab still needs that session cookie.

## The loop

1. **Edit** `.ocd` (left) → 400ms debounce → quick rebuild
   (1 seed × 100 iters + `lroute` estimate, ~0.1s). Full quality comes
   from **solve** (or Ctrl+Enter): 5 seeds × 500 iters + chosen router.
2. **Drag parts** on the PCB → drops `fix REF at x y` into the source,
   re-solves around it. Double-click a part to unpin. Instanced parts
   (`block`/`instance`) drag as a rigid group — one `fix` line per
   member, double-click unpins the whole group. Groups show as dashed
   outlines with `Z1`/`Z2`… tags (one hue each). **stamp** appends
   another copy of the hovered instance and rebuilds.
3. **candidates** generates N candidate layouts (a filmstrip across the
   top) → click picks, drag nudges, re-run any engine.
4. **diff** shows what changed since the previous revision (undo-history diff).

## Header, left to right

The chrome is a paper spec sheet with one terminal: warm paper ground, white
panel cards, the `.ocd` editor as the single dark surface, one signal green
for actions and live state. Every control carries a visible word (a glyph
alone is not a label). The header is a menubar, not a button strip: `solve`
stays top-level (it is the one action you reach for constantly), everything
else lives in a named menu — `Board` (candidates, stamp, fab zip, render) ·
`Edit` (undo, redo, diff, commit, with kbd hints) · `Engines`
(placer/router/fab/silk) · `Simulate` (sim dc, shift-click tran) · `Tools`
(chat, auto-apply, calc, health, quote) · `Share` (live roster + invite
link) — with the state pills on the right. Alt+B/E/G/S/T jumps to a menu,
Esc closes.

- **live presence first:** the header carries the room pill (`2 here: maya,
  leo`) right after the brand — collab is the headline, not a corner.
- **quote wears logos:** every price row shows its fab's real logo
  (vendor tile from `ocdcircuit/assets/fabs/`, embedded by `fab.logo()`
  as a data URI) next to the name. The public landing strip loads the
  same tiles from `/fab-logo/<key>` so ~100 KB of PNGs stay off the first HTML.
- **status pills:** `cost` (wirelength) · `OCD nn/100 (grade)` neatness ·
  routing feasibility per layer count (`1L routable`, `2L unroutable (this
  board)`) — wirelength is a hint, the real verdict is the DRC panel.

## Panels

Job file (left, the only dark surface) · PCB (centre, drag parts, DRC strip
along the bottom) · schematic · 3D · tidy metrics · **knowledgebase**
(`kb/` beside the board: ask/search its notes and datasheet text, `add` a URL
or path, `fetch datasheets` for every `datasheet=`/`lcsc=` part, click a doc to
read it page by page). Each panel head carries its
title and a one-line note on what the panel does. Status is always a word in a
pill or a line of the listing, never a bare colour. Canvases draw on the paper
ground: parts read as ink boxes, pinned parts wear the signal wash, pours are a
copper hatch with thermal gaps cut out of the flood, and net hues are
red/blue/green/violet. Selecting text in the job file highlights every ref it
names on the PCB and in the schematic (a pin like `U1.7` rings that pin).

## Schematic

Click pin → click net to rewire · alt-click drops a pin ·
double-click a label renames the net. All edits rewrite `.ocd` lines the
parser accepts (`<-->`-joined, attrs preserved).

## Endpoints (same shapes as MCP tools)

`/init /build /solve /candidates /pick /render /export /diff_prev`
`/simulate /doctor /undo /redo` (POST JSON) · `/quote /xray /scan` ·
`/kb/list /kb/read /kb/search /kb/ask /kb/add /kb/fetch` (the board's
knowledgebase; `/kb/fetch` runs in a worker thread and reports through
`/kb/list`) · `/kb/prefs*` · `/slots` (plugin inventory) ·
`/auth/signup|/login|/logout|/me` · `/shelf*` · `/chat*` ·
`/fs/open|/read|/import` · `/vcs*` · `/poll /load /reload`.
`/collab/sync /collab/push /collab/cursor /collab/op` (POST JSON) +
`/collab/events` (SSE): realtime multiplayer, one room per board —
rev-guarded pushes (stale loser reloads), presence pills + PCB rings,
structured ops through the `collab` plugin. Any failure
returns `{"error": "Type: msg"}` — the server never 500s the UI thread.

## Realtime collab (Google-docs-shaped, N engineers)

Open the same board in any number of browsers: the room pill in the header
reads `4 here: maya, leo, priya +1` (first three names, then the overflow —
the full roster is one hover away), selecting a part rings it in your color
on everyone's PCB (stacked tags when cursors collide), and an edit on any
side rebuilds all of them (~14ms push on blinky). A rev mismatch means
someone else edited first — their text wins, yours stays in undo. `Share`
(in the menubar) shows the live roster and copies the invite link. No
accounts to merge, no invites, no seat cap: sign up, open the same board,
you're co-editing.

Screengrabs (`docs/shots/`): `collab-landing.png` (hero strip, three live), `collab-crowd.png` (4-user room pill), `collab-live.png` (the room pill), `collab-ring.png` (both editors open), `fab-strip.png` (landing fab badges), `fab-quote.png` (quote rows with badges).

## Perf contract (enforced by `tests/test_studio.py`)

- Quick rebuild < 0.2s on blinky_555 (measures ~0.07s steady-state,
  warm-up build first so cold caches don't fake-fail CI).
- Static board costs zero frames (render-on-demand; 3D spins 4s
  after new state, then rests). Verified manually via `dirty` eval in
  headless chromium — not in the gate: asserting it headlessly would
  need test-only hooks in the UI, which is theater. Re-verify by hand
  when touching the render loop.
- Headless screenshot: PCB region must show content, console clean.

```bash
python tests/test_studio.py   # also runs under `make test`
```
