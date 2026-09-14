# Plugins: everything is one

One pattern for all behavior: subclass `Plugin[Out]`, set `kind` + `key`,
implement `run(board, **k)`, mount it. `Board` dispatches (`b.place()`,
`b.check()`, `b.export("easyeda")`…); a crashing plugin is fenced and the
previous entry keeps serving (`Registry.failed`, re-arm via `use()`).
Fixable input errors (`ValueError`/`KeyError`/`OSError`/`AssertionError`/`TimeoutExpired`)
propagate unfenced — fix the input and retry, no re-arm needed.

```python
from ocdcircuit.core import Plugin

class MyRouter(Plugin[int]):          # Out = what run() returns
    kind, key = "router", "mine"      # kind = dispatch slot, key = name

    def run(self, board, **k):        # k = caller kwargs (seeds, iters…)
        from ocdcircuit import maze   # engine imports stay inside run()
        return maze.maze(board)

MyRouter("router:mine").mount(board.ctx)  # or add to _DEFAULTS in plugins.py
board.use("router", "mine")               # hot-swap, undoable
```

Kinds (see `plugins.py` for keys): `placer placer→float` · `router→int` ·
`layers` · `drc/erc→dict` · `exporter→[files]` · `renderer→str|bytes` ·
`silk/importer/calc/simulate/lint/score/doctor→dict` · `diff→str` · `parts`.

Current keys (from a live registry — count, don't hand-edit):
`placer` compact/diffusion/hierarchical/multilevel/thermal/tidy ·
`router` coarse/lroute/maze/wiremask · `exporter`
bundle/eagle/easyeda/jlc/json/kicad/kicad-sch/ocd · `importer`
eagle/eagle-brd/easyeda/fp/kicad/pcb/sym/tscircuit · `renderer`
all/assembly/blender/easyeda/gltf/html3d/kicad/pcbdraw/png/sch/stl/svg ·
`simulate` gates/mna/ngspice · `silk` fab/full/ref · `drc` all/erc/fab/jlc-flex ·
`layers` greedy · `config` toml · `calc`/`diff`/`doctor`/`lint`/`parts`/`score` std.

Rules: engine imports inside `run()` (keeps `import ocdcircuit` light);
failure memory means `run` may raise — the registry fences it, no cleanup
needed. UI slots (`UiSlots` in `core.py`, served by `apps/studio.py`) work
the same way: `SLOTS.register(slot, id, fn)` where `fn(state) -> html`;
a crashing entry abdicates, `/slots` lists the ledger. Slots:
`toolbar`, `panel-left`, `panel-right`, `view`, `status`.
