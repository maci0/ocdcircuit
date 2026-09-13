# ADR-0001 — Context paradigm as the core model

## Decision
All board mutations go through one unified `Context` (`ocdcircuit/core.py`):
every effect carries its inverse (temporal composability), every module declares
`requires`/`provides` services re-resolved on change (spatial composability).

## Why
Paper §3–4: revertible effects + reactive coeffects → components interleave
without disturbing each other. Concretely: an agent's bad patch undoes cleanly;
removing a module removes exactly its parts/traces/nets; hot-reload remounts
one module in place. VSCode-style restart-the-world is the failure mode we avoid.

## Consequences
Board/Module are `Component`s; `Loader.reconcile()` diffs declarative configs.
No direct list mutation outside `Context.emit` (undo breaks otherwise).
