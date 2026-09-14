# OCD Studio — operator guide

Visual editor for `.ocd` boards: source pane, PCB, schematic, 3D, DRC —
one cockpit, no tabs. Edits are real: every good build is saved to disk
and committed to a 100-deep undo history.

```bash
make run                            # :8077, blinky_555
BOARD=boards/psu.ocd make run       # another board
OCD_PORT=8078 python -m apps.studio boards/blinky_555.ocd   # custom port
```

## The loop

1. **Edit** `.ocd` (left) → 400ms debounce → quick rebuild
   (1 seed × 100 iters + `lroute` estimate, ~0.1s). Full quality comes
   from **solve ▶** (or Ctrl+Enter): 5 seeds × 500 iters + chosen router.
2. **Drag parts** on the PCB → drops `fix REF at x y` into the source,
   re-solves around it. Double-click a part to unpin. Instanced parts
   (`block`/`instance`) drag as a rigid group — one `fix` line per
   member, double-click unpins the whole group. Groups show as dashed
   outlines with `Z1`/`Z2`… tags (one hue each). **⧉ stamp** appends
   another copy of the hovered instance and rebuilds.
3. **🎲** generates N candidate layouts (filmstrip) → click picks,
   drag nudges, re-run any engine.
4. **Δ** shows what changed since the last edit (undo-history diff).

## Header, left to right

`cost` (wirelength) · `OCD nn/100 (grade)` neatness badge · theme ·
solve ▶ · ⬇ fab (one-zip fab bundle) · 🎲 + count · undo/redo · Δ ·
`⤓ svg` (cycles svg → sch → png, shift-click backwards) · `Ω`
(trace/via/divider calculators, instant) · `⚡ dc` (simulate current
board; shift-click toggles tran; needs `sim` lines or it tells you so) · `🩺`
(tooling health: python, ngspice, plugins — lazy-checks on first open) · placer/router/fab/silk
dropdowns · `route@1L ✓ 2L ✓` congestion hint per layer count
(wirelength comparison — the real verdict is the DRC panel).

## Schematic

Click pin → click net to rewire · alt-click drops a pin ·
double-click a label renames the net. All edits rewrite `.ocd` lines the
parser accepts (`<-->`-joined, attrs preserved).

## Endpoints (same shapes as MCP tools)

`/init /build /solve /candidates /pick /render /export /diff_prev`
`/simulate /doctor /undo /redo` (POST JSON) · `/slots` (plugin inventory). Any failure
returns `{"error": "Type: msg"}` — the server never 500s the UI thread.

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
