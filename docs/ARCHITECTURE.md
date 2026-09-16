# Architecture (current — read this before the ADRs)

Three layers. Dependencies point inward only: apps → Board → plugins →
engines. Engines never import each other except leaf math (`pads_of`,
`_seg_dist`, cost terms); behavior always dispatches through the registry.

```
apps/ocd.py ──┐
apps/studio.py ─┼─→ Board (circuit.py) ──→ Registry (core.py) ──→ plugins.py
apps/mcp.py ───┘         │                        │                     │
                         │ one Board method       │ kind:key,           │ run() holds
                         │ per kind               │ failure memory     │ engine imports
                         ▼                        ▼                     ▼
                   agent.py (.ocd ⇄ IR)     core.py (Context/undo)  solver/maze/drc/…
```

- **Board** (`circuit.py`): model (parts/nets/traces/constraints/meta) +
  one dispatch method per plugin kind (`place/route_board/check/export/
  render/silk/import_fp/import_sym/calc/simulate/lint/score/doctor/diff/
  collab/xray/scan/quote/price/configure`). Never calls
  engines directly. `_run()` funnels all dispatch: a crashing plugin is
  marked failed, the previous entry keeps serving, explicit `use()` re-arms.
  Fixable input errors (`ValueError`/`KeyError`/`OSError`/`AssertionError`/`TimeoutExpired`)
  bypass the fence — retry works without re-arm. Recommendations are
  `ocdcircuit.recommend.recommend(board)` (module API), not a mounted plugin.
- **Registry** (`core.py`): `items[(kind,key)]`, one `active` per kind,
  `failed` map. `UiSlots` beside it: named UI slots (`toolbar/panel-left/
  panel-right/view/status`), plugins register render fns, crash abdicates.
- **Plugins** (`plugins.py`): 70+ classes, `kind`+`key`, `run(board, **k)`.
  Engine imports live inside `run()` so `import ocdcircuit` stays light.
  Mounted per-Board by `mount_defaults` (swappable per board, undoable).
- **Engines**: `solver` (diffusion place + min-conflicts repair), `maze`
  (A* route, per-layer copper/halo, MST legs, rip-up), `drc` (fab-profile
  checks + shared `in_zone`), `export` (gerber/kicad/kicad-sch/eagle/easyeda),
  `agent` (.ocd text ⇄ IR), `foreign` (kicad/eagle/easyeda/tscircuit import),
  `score/silk/sim/spice/calc/lint/doctor/diff` (analysis), `geom3d/raster/
  view3d` (3D), `parts/footprint/fab` (data), `kb` (the board's `kb/`:
  notes + datasheets, text-extracted on demand, embeddings for `ask` via
  `llm.embed` with a term-match floor — CLI, MCP and studio read the same
  directory, so there is no index to invalidate beyond `kb/.cache/`).
- **Apps** (`apps/`): `ocd` (CLI), `studio` (webui, slot-composed page),
  `mcp` (30-tool agent server). `tools/` holds one-shot porters
  (tscircuit/atopile/mitox); `boards/` one dir per board; `benches/`
  discrete6502 stress; `tests/` suite + geometry goldens.

Invariants: no list mutation outside `Context.emit`; `dumps`/`loads`
round-trip byte-identically (goldens enforce); DRC errors block fab,
warnings don't; `Board` methods never bypass `_run`.
