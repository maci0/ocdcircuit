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
  render/silk/import_fp/calc/simulate/lint/score/doctor/diff`). Never calls
  engines directly. `_run()` funnels all dispatch: a raising plugin is
  marked failed, the previous entry keeps serving, explicit `use()` re-arms.
- **Registry** (`core.py`): `items[(kind,key)]`, one `active` per kind,
  `failed` map. `UiSlots` beside it: named UI slots (`toolbar/panel-left/
  panel-right/view/status`), plugins register render fns, crash abdicates.
- **Plugins** (`plugins.py`): 40+ classes, `kind`+`key`, `run(board, **k)`.
  Engine imports live inside `run()` so `import ocdcircuit` stays light.
  Mounted per-Board by `mount_defaults` (swappable per board, undoable).
- **Engines**: `solver` (diffusion place + min-conflicts repair), `maze`
  (A* route, per-layer copper/halo, MST legs, rip-up), `drc` (fab-profile
  checks + shared `in_zone`), `export` (gerber/kicad/easyeda), `agent`
  (.ocd text ⇄ IR), `foreign` (kicad/eagle/easyeda/tscircuit import),
  `score/silk/sim/spice/calc/lint/doctor/diff` (analysis), `geom3d/raster/
  view3d` (3D), `parts/footprint/fab` (data).
- **Apps** (`apps/`): `ocd` (CLI), `studio` (webui, slot-composed page),
  `mcp` (18-tool agent server). `tools/` holds one-shot porters
  (tscircuit/atopile/mitox); `boards/` one dir per board; `benches/`
  monster6502 stress; `tests/` suite + geometry goldens.

Invariants: no list mutation outside `Context.emit`; `dumps`/`loads`
round-trip byte-identically (goldens enforce); DRC errors block fab,
warnings don't; `Board` methods never bypass `_run`.
