# ADR-0001 — Context paradigm as the core model

## Decision
All board mutations go through one unified `Context` (`ocdcircuit/core.py`):
every effect carries its inverse (temporal composability), every fiber declares
its coeffectdeps (`inject`) re-resolved on change (spatial composability).

## Why
Paper §3–4: revertible effects + reactive coeffects → components interleave
without disturbing each other. Concretely: an agent's bad patch undoes cleanly;
removing a module removes exactly its parts/traces/nets; hot-reload remounts
one module in place. VSCode-style restart-the-world is the failure mode we avoid.

## Consequences
Board/Module are `Component`s; `Loader.declare()` diffs declarative entry
configs, `Board.declare()` diffs board state. No mutation outside
`Context.effect` (undo breaks otherwise).

## Update (registry failure memory, UI slots)
- `Registry.failed`: a raising plugin is fenced, `get()` refuses it, active
  falls back to the next healthy entry; explicit `use()` re-arms. All
  `Board` dispatch funnels through `_run()` (harness-loader shape).
  Refined: fixable input errors (`ValueError`/`KeyError`/`OSError`/
  `AssertionError`) bypass the fence — retry works without re-arm.
- `UiSlots` beside the registry: shell declares slot names, plugins
  register render fns, crash abdicates to the next survivor, `/slots`
  exposes the ledger. Studio's page is four view registrations.
