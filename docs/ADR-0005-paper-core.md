# ADR-0005 — Cordis paper (§5) in `core.py`: what landed, what didn't

Status: Accepted

Paper: [arXiv:2608.25512](https://arxiv.org/abs/2608.25512) (Table 2, Algs 1–10).

## Decision
Land Cordis paper §5 primitives in `core.py` as enumerated below; gaps
stay listed under Open until closed or explicitly deferred.

## Landed

- `Context.effect` (Alg 1): the single primitive; callback returns or yields
  inverses folded LIFO; dispose fires at most once; dispose prepends to the
  parent accumulator (∂²Γ). `emit` is its two-lambda call form. No parallel
  service system: one store + realm tables, `set` (tracked + notify),
  `require` (loud read), `unset` (ordered withdrawal).
- Coeffect slots (Alg 2–3, §5.1.2): `@@store/@@isolate/@@intercept` via
  `_store/_isolate/_intercept`, two-layer `k→ρ(k)→σ(ρ(k))` resolution,
  `get/set/isolate/intercept` + `notify` (dependent refresh, realm test).
- `Fiber` (Alg 4–5, §5.1.3): `inject/apply/ctx/state/target/committed/
  dispose/provided`, `LOADING→ACTIVE / UNLOADING→INACTIVE` with inertial
  chaining, UNLOADING-before-inverse, dependent-drain wait. `target` is a
  provider-uid digest (fresh uids, no value-aliasing). Raising `apply`
  parks FAILED + target ⊥ (Table 2, §4.4) instead of breaking notify.
- Proxy access (Alg 6, §5.1.4): `ctx[key]` reads the committed view;
  `InactiveAccess` / `UndeclaredAccess` replace silent `KeyError`-mid-run.
- `Module` mounts as a fiber (ordered withdrawal; `_refs` removal kept).
- `Loader.declare` (Def 81): `{id,url,inject,isolate,intercept,config,
  disabled,args,kwargs}` entries, keyed diff, per-field dispatch —
  rebuild on url, realm-reassign+reload on isolate, in-place on intercept,
  `apply_config` handoff (rebuild fallback) on config,
  retire/resume on disabled. Two-phase `reload` (Alg 10).
  (Dead module-path `mount/unmount/reconcile/remount` deleted 2026-09-14:
  zero callers, `declare`/`reload` cover it.)
- HMR utils (Algs 8–9): `classify` + `stale_entries` as pure functions.
- MCP `context` tool: fibers/get/set/unset for agents.

## Open gaps (re-review 2026-09-14, honest list)

Closed since: `ctx.use` O-Insert (tracked callback, revert = retire +
O-Remove), `ctx.registry` + uid clearing, isolate-as-scope (no inverse;
loader respawns on scope change), intercept consulted at read (`hidden`
mask, child-overridable), Module build inside fiber apply, Board domain
edits chained into a board-owned fiber (unload reverts all board state;
flat undo stack untouched), managed realms (local `True` tagged by id,
global string refcounted, discarded when unnamed), two-phase `reload`
(reimport failure tears nothing down; mount failure parks FAILED).
Remaining:

1. **No parent-cascade for engine-internal scratch**: solver/maze evals
   use snapshot/rollback on the flat stack — correct, just not fiber
   terms. Per-part fibers have no paper basis; the board fiber covers
   unload-reverts.
2. **Alg 7 surgical migration**: reassignment respawns (retire +
   reinsert, the paper's revision composite) instead of moving live
   bindings with delimiter tags. Same endpoint, coarser route.
3. **Alg 10 has no module-cache layer**: `reimport` is host-supplied;
   `sys.modules` invalidation is the caller's job (Node ESM/CJS caches
   have no Python analogue here).
4. Prior skips stand: async `create_task`, group/include components,
   file-watcher HMR engine, compile-time `ctx[key]` (§6.4), §6.2/6.3/6.6.

## Checks

`tests/test_paper.py` (LIFO fold, once-only dispose, guard Divert, proxy
errors, fiber activate/withdraw/FAILED, provider-swap reactivation,
isolate independence, classify/stale, two-phase reload, loader fuzz).
`mypy strict` clean; `test_all` + snapshots unchanged.
