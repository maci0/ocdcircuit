# ADR-0005 — Cordis paper (§5) in `core.py`: what landed, what didn't

Paper: [arXiv:2608.25512](https://arxiv.org/abs/2608.25512) (Table 2, Algs 1–10).

## Landed (this round)

- `Context.effect` (Alg 1): ALL mutations flow through it; callback returns
  or yields inverses folded LIFO; dispose fires at most once; dispose
  prepends to the parent accumulator (∂²Γ). `emit/provide/require` stay as
  thin wrappers — boards, plugins, fuzz suite untouched.
- Coeffect slots (Alg 2–3, §5.1.2): `@@store/@@isolate/@@intercept` via
  `_store/_isolate/_intercept`, two-layer `k→ρ(k)→σ(ρ(k))` resolution,
  `get/set/isolate/intercept` + `notify` (dependent refresh, realm test).
- `Fiber` (Alg 4–5, §5.1.3): `inject/apply/ctx/state/target/committed/
  dispose/provided`, `LOADING→ACTIVE / UNLOADING→INACTIVE` with inertial
  chaining, UNLOADING-before-inverse, dependent-drain wait. `target` is a
  provider-uid digest (fresh uids, no value-aliasing).
- Proxy access (Alg 6, §5.1.4): `ctx[key]` reads the committed view;
  `InactiveAccess` / `UndeclaredAccess` replace silent `KeyError`-mid-run.
- `Module` mounts as a fiber (ordered withdrawal, `_refs` fallback kept).
- `Loader.declare` (Def 81): `{id,url,inject,isolate,intercept,config,
  disabled,args,kwargs}` entries, keyed diff, per-field dispatch —
  rebuild on url, realm-reassign+reload on isolate, in-place on intercept,
  `apply_config` handoff (rebuild fallback) on config,
  retire/resume on disabled. `reconcile/remount` kept; `remount` is now
  transactional (restore-on-failure, Alg 10 single-entry).
- HMR utils (Algs 8–9): `classify` + `stale_entries` as pure functions.
- `Context.unprovide` (ordered withdrawal of legacy services).
- MCP `context` tool (27th): fibers/get/set/unprovide for agents.

## Skipped (deliberate)

- Async `create_task` inertia: sync codebase, no event loop — `inertia` is a
  reentrancy flag, chaining is direct recursion. Add a task queue when a real
  async host needs coalescing, not before.
- `@cordisjs/group/include` components: `Board.declare` already diffs
  parts/nets; `Loader.declare` diffs entries. A group component is sugar.
- Full HMR engine (file watching, cache invalidation, backup/restore across
  entries): `classify`/`stale_entries`/`remount` are the pure core; the
  watcher is host-specific (Node ESM/CJS caches) and has no Python caller.
- Delimiter-based realm migration (Alg 7 `δₖ` tags): isolate-reassign
  reloads instead of surgically moving bindings. Correct, just coarser.
- Compile-time `ctx[key]` checking (§6.4): runtime errors suffice; mypy
  plugin when someone actually trips on it repeatedly.
- Service multiplexing / sandboxing / versioning (§6.2/6.3/6.6): no caller.

## Checks

`tests/test_paper.py` (one assert-file: LIFO fold, once-only dispose,
guard Divert, proxy errors, fiber activate/withdraw, provider-swap
reactivation, isolate independence, classify/stale, transactional remount).
`mypy strict` clean; `test_all` + snapshots unchanged.
