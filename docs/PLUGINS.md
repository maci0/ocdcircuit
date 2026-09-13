# Plugins: everything is one

One pattern for all behavior: subclass `Plugin[Out]`, set `kind` + `key`,
implement `run(board, **k)`, mount it. `Board` dispatches (`b.place()`,
`b.check()`, `b.export("easyeda")`…); a raising plugin is fenced and the
previous entry keeps serving (`Registry.failed`, re-arm via `use()`).

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

Rules: engine imports inside `run()` (keeps `import ocdcircuit` light);
failure memory means `run` may raise — the registry fences it, no cleanup
needed. UI slots (`UiSlots` in `core.py`, served by `apps/studio.py`) work
the same way: `SLOTS.register(slot, id, fn)` where `fn(state) -> html`;
a crashing entry abdicates, `/slots` lists the ledger. Slots:
`toolbar`, `panel-left`, `panel-right`, `view`, `status`.
