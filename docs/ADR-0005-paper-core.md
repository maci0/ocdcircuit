# ADR-0005 — Cordis paper (§5) in `core.py`: what landed, what didn't

Paper: [arXiv:2608.25512](https://arxiv.org/abs/2608.25512) (Table 2, Algs 1–10).

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
  retire/resume on disabled. `remount` is transactional single-entry
  (restore-on-failure, Alg 10 shape).
- HMR utils (Algs 8–9): `classify` + `stale_entries` as pure functions.
- MCP `context` tool: fibers/get/set/unset for agents.

## Open gaps (re-review 2026-09-14, honest list)

1. **No `ctx.use` / O-Insert instantiation** (Table 2, Alg 4): fibers are
   built by `Loader._spawn` directly; parts/nets/traces bypass fibers.
   No parent-cascade teardown for domain state.
2. **No `ctx.registry` / uid reissue** (Table 2, O-Remove): fibers leave
   `_fibers` by list-removal, uid stays live; no `dom(Fγ)` enumeration.
3. **Isolate goes through `effect`** (§5.1.2 says scope-derivation needs
   no inverse): realm overrides are undoable entries, and loader pokes
   `_isolate` directly instead of deriving a child context.
4. **`intercept` is write-only**: nothing consults `_intercept` at read
   time (§5.1.2: metadata adjusts *how a binding is used*).
5. **Alg 7 managed realms + delimiters**: isolate dicts are raw values,
   no local-vs-global scoping or entry-move semantics.
6. **Alg 10 is single-entry**: no cross-entry atomicity, no module cache
   invalidation (`sys.modules` untouched).
7. **`Module` fiber is decorative**: `_apply` is a no-op, build runs
   outside the fiber, nothing injects module keys — ordered withdrawal
   notifies nobody; `_refs` hand-removal stays.
8. Prior skips stand: async `create_task`, group/include components,
   file-watcher HMR engine, compile-time `ctx[key]` (§6.4), §6.2/6.3/6.6.

## Checks

`tests/test_paper.py` (LIFO fold, once-only dispose, guard Divert, proxy
errors, fiber activate/withdraw/FAILED, provider-swap reactivation,
isolate independence, classify/stale, transactional remount).
`mypy strict` clean; `test_all` + snapshots unchanged.
