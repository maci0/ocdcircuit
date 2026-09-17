"""Unified context: revertible effects + reactive coeffects (paper §3, §5.1).

Theory → runtime (paper Table 2, Cordis core):
  effectΓ/ℑΓ → Context.effect (Alg 1)      Σ/Σiso/Σinter → _store/_isolate/_intercept
  get/set    → get/set (Alg 2)             isolate/intercept → isolate/intercept
  notify     → notify (Alg 3)              fiber ⟨d,p,e,π,σ,τ,θ⟩ → Fiber (Alg 4-5)
  ctx[key]   → proxy resolve (Alg 6)       loader entries → Loader.declare
  HMR        → classify/stale_entries (Alg 8-9)

Paper: https://arxiv.org/abs/2608.25512
"""
from __future__ import annotations
from collections.abc import Callable, Iterator
from itertools import count
from typing import TYPE_CHECKING, Generic, TypeVar, cast
from .types import Constraint  # noqa: F401  (re-export for convenience)
from .types import Undo

if TYPE_CHECKING:
    from .circuit import Board

Out = TypeVar("Out")
Guard = Callable[[], bool]


class InactiveAccess(RuntimeError):
    """Proxy read of a declared-but-uncommitted key (paper Alg 6)."""


class UndeclaredAccess(RuntimeError):
    """Proxy read of a key no fiber in the chain declares (paper Alg 6)."""


def execute(callback: Callable[[], object], guard: Guard) -> Undo:
    """Drive an effect iterator (paper Alg 1): run the callback, fold each
    yielded inverse into one LIFO composite. A plain callback returning its
    inverse is the degenerate one-step iterator."""
    res = callback()
    inverses: list[Undo] = []
    if isinstance(res, Iterator):
        while guard():
            try:
                v = next(res)
            except StopIteration:
                break
            if callable(v):
                inverses.append(cast(Undo, v))
    elif callable(res):
        inverses.append(cast(Undo, res))

    def _recover() -> None:
        for inv in reversed(inverses):
            inv()

    return _recover


class Context:
    """Every mutation goes through effect. Undo stack = temporal
    composability. Store + realm tables = spatial composability."""

    def __init__(self, parent: Context | None = None) -> None:
        self._undos: list[Undo] = []
        # paper §5.1: accumulated inverse, child tracking, coeffect slots
        self._dispose: Undo = lambda: None
        self._parent = parent
        self._children: list[Context] = []
        self._fiber: Fiber | None = None
        self._fibers: list[Fiber] = []
        self._store: dict[object, object] = {}
        self._isolate: dict[str, object] = {}
        self._intercept: dict[str, object] = {}
        self._providers: dict[str, tuple[Fiber | None, object, object]] = {}
        self._registry: dict[int, Fiber] = {}  # dom(Fγ): uid → live fiber
        self._trim_hooks: list[Callable[[int], None]] = []

    def child(self) -> Context:
        """Derive a child context (fiber ctx, isolate scope). Realm tables
        are inherited by copy; the store resolves up the parent chain."""
        c = Context(parent=self)
        c._isolate = dict(self._isolate)
        c._intercept = dict(self._intercept)
        self._children.append(c)
        return c

    def effect(self, callback: Callable[[], object]) -> Undo:
        """Paper Alg 1 ctx.effect: callback performs the mutation and
        returns (or yields) its inverse(s). Dispose fires at most once;
        the inverse is also prepended to the parent accumulator (∂²Γ)."""
        armed = {"on": True}
        recover = execute(callback, lambda: armed["on"])

        def dispose() -> None:
            if not armed["on"]:
                return
            armed["on"] = False
            recover()

        prev = self._dispose

        def _chain() -> None:
            dispose()
            prev()

        self._dispose = _chain
        if self._parent is not None:
            pprev = self._parent._dispose
            par = self._parent

            def _pchain() -> None:
                dispose()
                pprev()

            par._dispose = _pchain
        self._undos.append(dispose)
        return dispose

    def emit(self, do: Callable[[], None], undo: Undo) -> Undo:
        """Two-lambda call form of effect: do() runs now, undo is its
        inverse. One primitive underneath (paper Alg 1)."""

        def _cb() -> object:
            do()
            return undo

        return self.effect(_cb)

    def snapshot(self) -> int:
        return len(self._undos)

    def undo(self, n: int = 1) -> None:
        for _ in range(min(n, len(self._undos))):
            self._undos.pop()()
        for hook in list(self._trim_hooks):
            hook(len(self._undos))

    def rollback(self, snap: int) -> None:
        self.undo(len(self._undos) - snap)

    # --- coeffects: two-layer resolution k→ρ(k)→σ(ρ(k)) (paper §5.1.2) ---
    def _root(self) -> Context:
        c = self
        while c._parent is not None:
            c = c._parent
        return c

    def _realm_of(self, key: str) -> object:
        c: Context | None = self
        while c is not None:
            if key in c._isolate:
                return c._isolate[key]
            c = c._parent
        return key

    def _lookup(self, key: str) -> tuple[object, object]:
        realm = self._realm_of(key)
        # provider bindings are visible tree-wide while the provider is
        # ACTIVE (paper §5.1.2: a withdrawal is visible one step early —
        # UNLOADING already stopped providing, bindings still in place).
        cur = self._root()._providers.get(key)
        if cur is not None and cur[1] == realm:
            f = cur[0]
            if f is not None and f.state == Fiber.ACTIVE and len(cur) > 2:
                return (cur[2], realm)
            if f is None:
                v = cur[2] if len(cur) > 2 else None
                if v is not None:
                    return (v, realm)
        c: Context | None = self
        while c is not None:
            if realm in c._store:
                return (c._store[realm], realm)
            c = c._parent
        return (None, realm)

    def get(self, key: str) -> object:
        """Bare store lookup (never fails): k→ρ(k)→σ(ρ(k)). A `hidden`
        intercept on the access path masks the binding (paper §5.1.2:
        ι adjusts how a binding is used, not what it resolves to)."""
        if self.intercepted(key).get("hidden") is True:
            return None
        v, _ = self._lookup(key)
        return v

    def __getitem__(self, key: str) -> object:
        """Proxy access (paper Alg 6): resolve against the accessing fiber's
        committed view; declared-but-uncommitted → InactiveAccess, no
        declarer up the chain → UndeclaredAccess."""
        ctx: Context | None = self
        while ctx is not None:
            f = ctx._fiber
            if f is not None:
                if f.committed is not None and key in f.committed:
                    return f.committed[key]
                if key in f.inject:
                    raise InactiveAccess(f"fiber {f.uid} reads uncommitted {key!r}")
            ctx = ctx._parent
        raise UndeclaredAccess(f"no fiber declares {key!r}")

    def set(self, key: str, value: object) -> Undo:
        """Paper Alg 2 set(k,v): effect-tracked provision + notify.
        The inverse restores the prior binding AND its provider row, then
        notifies: an unnotified inverse leaves dependents committed to a
        provider that no longer resolves (paper Alg 3 — a context change
        is only composed once its dependents have reconciled)."""
        realm = self._realm_of(key)
        had = realm in self._store
        old = self._store.get(realm)
        prev = self._root()._providers.get(key)

        def _cb() -> object:
            self._store[realm] = value
            self._register_provider(key, realm, value)

            def _inv() -> None:
                if had:
                    self._store[realm] = old
                else:
                    self._store.pop(realm, None)
                self._unregister_provider(key, realm)
                if prev is not None:
                    self._root()._providers[key] = prev
                self.notify([key])

            return _inv

        d = self.effect(_cb)
        self.notify([key])
        return d

    def isolate(self, key: str, realm: object | None = None) -> Context:
        """Derive a child context overriding ρ at one key (fresh symbol by
        default, paper §5.1.2). Recovery is implicit: discard the child,
        no inverse. Siblings resolving the key keep the parent binding."""
        c = self.child()
        c._isolate[key] = realm if realm is not None else object()
        return c

    def intercept(self, key: str, metadata: dict[str, object]) -> Undo:
        """Merge metadata into ι (paper §5.1.2); consulted at read time."""
        old = dict(cast(dict[str, object], self._intercept.get(key, {})))

        def _cb() -> object:
            merged = dict(cast(dict[str, object], self._intercept.get(key, {})))
            merged.update(metadata)
            self._intercept[key] = merged

            def _inv() -> None:
                if old:
                    self._intercept[key] = old
                else:
                    self._intercept.pop(key, None)

            return _inv

        return self.effect(_cb)

    def intercepted(self, key: str) -> dict[str, object]:
        """Metadata ι consults at read time: merged down the context chain,
        child takes priority (paper §5.1.2)."""
        out: dict[str, object] = {}
        chain: list[Context] = []
        c: Context | None = self
        while c is not None:
            chain.append(c)
            c = c._parent
        for ctx in reversed(chain):
            m = ctx._intercept.get(key)
            if isinstance(m, dict):
                out.update(cast(dict[str, object], m))
        return out

    # --- providers + reactive notification (paper Alg 3) ---
    def _register_provider(self, key: str, realm: object, value: object = None) -> None:
        self._root()._providers[key] = (self._fiber, realm, value)
        if self._fiber is not None:
            self._fiber.provided.add(key)

    def _unregister_provider(self, key: str, realm: object) -> None:
        root = self._root()
        cur = root._providers.get(key)
        if cur is not None and cur[1] == realm:
            if cur[0] is None or cur[0] is self._fiber:
                root._providers.pop(key, None)

    def _resolve_provider(self, key: str) -> tuple[Fiber | None, object, object] | None:
        cur = self._root()._providers.get(key)
        if cur is None:
            return None
        if cur[1] != self._realm_of(key):
            return None
        return cur

    def _all_fibers(self) -> list[Fiber]:
        out: list[Fiber] = []
        seen: set[int] = set()
        # descent: self + children (fibers register on the context whose
        # use() spawned them; the upward parent walk below would re-list
        # them once per child — dedupe by uid at the shared root).
        stack: list[Context] = [self._root()]
        while stack:
            c = stack.pop()
            for f in c._fibers:
                if f.uid not in seen:
                    seen.add(f.uid)
                    out.append(f)
            stack.extend(c._children)
        return out

    def use(self, inject: tuple[str, ...] | list[str],
            apply: Callable[[Context], object],
            isolate: dict[str, object] | None = None,
            intercept: dict[str, object] | None = None) -> Fiber:
        """Instantiation primitive (paper Alg 4, O-Insert): the callback is
        an effect tracked in THIS context — refresh runs the child on
        execute, revert forces target ⊥ + unload. Unloading a parent
        therefore cascades to its children. Returns the live fiber,
        registered under a fresh uid (dom(Fγ), Table 2)."""
        fiber = Fiber(self, inject, apply)
        if isolate:
            for k, r in isolate.items():
                fiber.ctx._isolate[k] = r
        if intercept:
            for k, m in intercept.items():
                assert isinstance(m, dict)
                merged = dict(cast(dict[str, object],
                                   fiber.ctx._intercept.get(k, {})))
                merged.update(cast(dict[str, object], m))
                fiber.ctx._intercept[k] = merged

        def _cb() -> object:
            fiber.refresh(force=True)

            def _inv() -> None:
                fiber.retire()  # O-Retire first: target ⊥ even with no inject
                self._drop_fiber(fiber)  # O-Remove: uid cleared, no reissue

            return _inv

        fiber._insert = self.effect(_cb)
        return fiber

    def _drop_fiber(self, fiber: Fiber) -> None:
        """O-Remove: drop from runtime, clear uid (paper Table 2). A stale
        committed view naming the uid resolves against nothing."""
        root = self._root()
        root._registry.pop(fiber.uid, None)
        for key in list(fiber.provided):
            fiber.ctx._unregister_provider(key, fiber.ctx._realm_of(key))
        fiber.provided.clear()
        fiber.committed = None
        ctxs: list[Context] = [self._root()]
        while ctxs:
            c = ctxs.pop()
            if fiber in c._fibers:
                c._fibers.remove(fiber)
            ctxs += c._children

    @property
    def registry(self) -> dict[int, Fiber]:
        """dom(Fγ): live fibers by uid (paper Table 2)."""
        return self._root()._registry

    def notify(self, keys: list[str]) -> list[Fiber]:
        """Paper Alg 3: re-evaluate dependents sharing the realm.
        Returns the affected fibers."""
        affected: list[Fiber] = []
        for fiber in self._all_fibers():
            for key in keys:
                if key in fiber.inject and fiber.ctx._realm_of(key) == self._realm_of(key):
                    fiber.refresh()
                    affected.append(fiber)
                    break
        return affected

    # --- coeffect read + withdrawal ---
    def require(self, name: str) -> object:
        """Bare store read that fails loudly (KeyError) on absence — for
        mandatory infrastructure lookups (e.g. the plugin registry)."""
        v, _ = self._lookup(name)
        if v is None:
            raise KeyError(f"unsatisfied coeffect: {name}")
        return v

    def unset(self, name: str) -> None:
        """Ordered withdrawal (paper §5.1.3): dependents deactivate ahead
        of the removal; bindings stay readable during their teardown."""
        cur = self._root()._providers.get(name)
        if cur is not None and cur[0] is None:
            self._root()._providers.pop(name, None)
        realm = self._realm_of(name)
        had = realm in self._store
        if not had:
            self.notify([name])
            return
        old = self._store.get(realm)

        def _cb() -> object:
            self._store.pop(realm, None)

            def _inv() -> None:
                if had:
                    self._store[realm] = old
                if cur is not None:
                    self._root()._providers[name] = cur
                self.notify([name])  # dependents re-activate on restore

            return _inv

        self.effect(_cb)
        self.notify([name])


class Fiber:
    """A component instantiation (paper §5.1.3, Alg 4-5): inject (d) +
    config-bound apply (e) run in a child ctx; LOADING→ACTIVE / UNLOADING→
    INACTIVE with inertial chaining. Sync: inertia is a reentrancy flag."""

    LOADING = "LOADING"
    ACTIVE = "ACTIVE"
    UNLOADING = "UNLOADING"
    INACTIVE = "INACTIVE"
    FAILED = "FAILED"

    _uids = count()

    def __init__(self, parent: Context, inject: tuple[str, ...] | list[str],
                 apply: Callable[[Context], object], capture: bool = True) -> None:
        self.uid = next(Fiber._uids)
        self.parent = parent
        self.inject = tuple(inject)
        self.apply = apply
        self.capture = capture  # False = component-owned teardown (unmount compensates)
        self.ctx = parent.child()
        self.ctx._fiber = self
        self.state = Fiber.INACTIVE
        self.target: tuple[int, ...] | None = None
        self.committed: dict[str, object] | None = None
        self.provided: set[str] = set()
        self.dispose: Undo = lambda: None
        self.inertia = False
        self._retired = False
        self.error: Exception | None = None  # FAILED outcome (paper §4.4)
        self._insert: Undo = lambda: None  # O-Insert dispose (set by ctx.use)
        parent._fibers.append(self)
        parent._root()._registry[self.uid] = self

    def target_of(self) -> tuple[int, ...] | None:
        """Digest of target(γ,n): provider uid per declared key (paper Alg 5).
        None = ⊥ (unsatisfied). Retired entries stay ⊥ until re-enabled."""
        if self._retired:
            return None
        uids: list[int] = []
        for key in self.inject:
            cur = self.ctx._resolve_provider(key)
            if cur is None:
                return None
            f, _realm, _v = cur
            if f is None:
                uids.append(-1)  # external binding (no owning fiber): always satisfied
            elif f is self:
                uids.append(self.uid)
            elif f.state != Fiber.ACTIVE:
                return None
            else:
                uids.append(f.uid)
        return tuple(uids)

    def refresh(self, force: bool = False) -> None:
        """Recompute target; (un)load on change. Idempotent: neutral
        changes are harmless (paper §5.1.2). A raising apply parks the
        fiber FAILED with target ⊥ (paper Table 2) instead of breaking
        the notify loop mid-reconciliation."""
        t = self.target_of()
        if not force and t == self.target:
            return
        self.target = t
        if self.inertia:
            return
        self.inertia = True
        try:
            if t is None:
                self.state = Fiber.UNLOADING  # L-Leave: out before inverses
                self._unload()
            else:
                self.state = Fiber.LOADING
                self._reload()
        except Exception as e:
            self.error = e
            self.target = None
            self.committed = None
            self.state = Fiber.FAILED
        finally:
            self.inertia = False

    def retire(self) -> None:
        """Administrative disable (paper: O-Retire); re-enable via resume."""
        self._retired = True
        self.error = None
        self.refresh(force=True)

    def resume(self) -> None:
        self._retired = False
        self.error = None
        self.refresh(force=True)

    def _reload(self) -> None:
        t0 = self.target
        committed: dict[str, object] = {}
        for key in self.inject:
            committed[key] = self.ctx.get(key)
        self.committed = committed
        snap = self.ctx.snapshot()
        try:
            recover = execute(lambda: self.apply(self.ctx),
                              lambda: self.target == t0)
        except Exception:
            # Partial apply (paper §3): a raising apply still installed the
            # effects it got through, and refresh() parks the fiber FAILED
            # with target ⊥ — a FAILED fiber holding live effects is a leak
            # no dispose path can reach. Run them LIFO, then fail.
            span = self.ctx._undos[snap:]
            del self.ctx._undos[snap:]
            for u in reversed(span):
                u()
            raise
        if self.capture:
            span = self.ctx._undos[snap:]
            del self.ctx._undos[snap:]
            prev = self.dispose

            def _new() -> None:
                recover()
                for u in reversed(span):
                    u()
                prev()

            self.dispose = _new
        else:
            prev2 = self.dispose

            def _new2() -> None:
                recover()
                prev2()

            self.dispose = _new2
        if self.target == t0 and t0 is not None:
            self.state = Fiber.ACTIVE
            self.ctx.notify(list(self.provided))
        else:
            self.state = Fiber.UNLOADING
            self._unload()

    def _unload(self) -> None:
        """Stop providing → drain dependents → recover (paper Alg 5).
        Bindings stay in place while dependents drain, so their teardown
        still reads the withdrawing coeffects."""
        for key in list(self.provided):
            self.ctx._unregister_provider(key, self.ctx._realm_of(key))
        self.ctx.notify(list(self.provided))
        self.provided.clear()
        d = self.dispose
        self.dispose = lambda: None
        d()
        self.committed = None
        if self.target is None:
            self.state = Fiber.INACTIVE
        else:
            self.state = Fiber.LOADING
            self._reload()


class Component:
    def __init__(self, name: str) -> None:
        self.name = name

    def mount(self, ctx: Context, *a: object, **k: object) -> None:
        pass

    def unmount(self, ctx: Context) -> None:
        pass


class UiSlots:
    """Named UI slots (harness SlotCore shape, sync python): the shell
    declares slot names, plugins register (slot, id, order, render). One
    lifecycle axis — dispose removes the contribution. A crashing entry
    abdicates to the next survivor (report=True keeps ledger row)."""
    slots = ("toolbar", "panel-left", "panel-right", "view", "status")

    def __init__(self) -> None:
        self.cells: dict[str, list[dict[str, object]]] = {}
        self.crashed: set[tuple[str, str]] = set()

    def register(self, slot: str, id: str, render: object,
                 order: float = 0.0) -> Callable[[], None]:
        """Contribute render (fn(state) -> html str) into slot. Returns disposer."""
        assert slot in self.slots, f"unknown slot {slot} (have {self.slots})"
        cell = {"id": id, "order": order, "render": render}
        cells = self.cells.setdefault(slot, [])
        assert all(c["id"] != id for c in cells), f"dup {slot}:{id}"
        cells.append(cell)
        cells.sort(key=lambda c: (float(str(c["order"])), str(c["id"])))

        self.crashed.discard((slot, id))  # fresh contribution, fresh verdict

        def _dispose() -> None:
            if cell in self.cells.get(slot, []):
                self.cells[slot].remove(cell)
            # the crash verdict is this contribution's state: dropping the
            # row without it leaves a reloaded plugin permanently blank.
            self.crashed.discard((slot, id))

        return _dispose

    def render(self, slot: str, state: object) -> str:
        """All live entries in order; a raising entry abdicates to the next."""
        import sys
        out: list[str] = []
        for cell in list(self.cells.get(slot, [])):
            if (slot, str(cell["id"])) in self.crashed:
                continue
            try:
                r = cell["render"]
                assert callable(r)
                out.append(str(r(state)))
            except Exception as e:
                cid = str(cell["id"])
                self.crashed.add((slot, cid))
                # operator-visible: abdication without a cause made blank
                # toolbars undiagnosable (registry.fail records a why; so do we)
                print(f"uislots: {slot}:{cid} abdicated: {type(e).__name__}: {e}",
                      file=sys.stderr)
        return "".join(out)

    def report(self, slot: str) -> list[str]:
        """Ledger rows (ids in order) — the plugin-inventory surface."""
        return [str(c["id"]) for c in self.cells.get(slot, [])]


class Registry:
    """All plugins live here, itself a service. kind: placer/router/layers/
    drc/exporter/parts/renderer. One active key per kind — hot-swap = use().
    Everything undoable via Context."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], object] = {}
        self.active: dict[str, str] = {}
        self.failed: dict[tuple[str, str], str] = {}

    def _add(self, kind: str, key: str, inst: object) -> None:
        self.items[(kind, key)] = inst

    def _drop(self, kind: str, key: str) -> None:
        self.items.pop((kind, key), None)
        # failure memory belongs to the mounted entry: leaving it behind
        # makes _add the non-inverse of _drop, so a fresh mount of the same
        # kind:key stays refused forever (paper §3 temporal composability).
        self.failed.pop((kind, key), None)
        if self.active.get(kind) == key:
            rest = sorted(kk for (k, kk) in self.items
                          if k == kind and (k, kk) not in self.failed)
            if rest:
                self.active[kind] = rest[0]
            else:
                self.active.pop(kind, None)

    def get(self, kind: str, key: str | None = None) -> object:
        key = key or self.active.get(kind)
        if key is None:
            opts = sorted(str(kk) for (k, kk) in self.items if k == kind)
            raise KeyError(f"no {kind} plugin mounted (have {opts})")
        if (kind, key) in self.failed:
            raise KeyError(f"plugin {kind}:{key} failed ({self.failed[(kind, key)]})")
        try:
            return self.items[(kind, key)]
        except KeyError:
            have = sorted(f"{k}:{v}" for k, v in self.items)
            raise KeyError(f"no plugin {kind}:{key} (have {have})")

    def use(self, kind: str, key: str) -> None:
        if (kind, key) not in self.items:
            raise KeyError(f"can't swap to unmounted {kind}:{key}")
        self.active[kind] = key
        self.failed.pop((kind, key), None)  # explicit re-arm clears failure

    def fail(self, kind: str, key: str, why: str) -> None:
        """Mark an entry failed (bad run): get() refuses it until re-armed
        via use() or a remount. Previous active entry keeps serving."""
        self.failed[(kind, key)] = why
        if self.active.get(kind) == key:
            rest = sorted(kk for (k, kk) in self.items
                          if k == kind and (k, kk) not in self.failed)
            if rest:
                self.active[kind] = rest[0]
            else:
                self.active.pop(kind, None)

    def list(self, kind: str | None = None) -> list[str]:
        """Key names for a kind (or 'kind:key' strings when kind is None)."""
        if kind is None:
            return sorted(f"{k}:{kk}" for (k, kk) in self.items)
        return sorted(kk for (k, kk) in self.items if k == kind)


class Plugin(Component, Generic[Out]):
    """Exporter, solver, parts lib, renderer — everything is one of these.
    Mount registers, unmount/rollback unregisters (temporal composability).
    Loader swaps them live: hot-swappable, no restart."""

    kind: str = "misc"
    key: str = "base"

    def mount(self, ctx: Context, *a: object, **k: object) -> None:
        reg = ctx.require("plugins")
        assert isinstance(reg, Registry)
        prev = reg.active.get(self.kind)
        reg._add(self.kind, self.key, self)
        if prev is None:
            reg.active[self.kind] = self.key

        def _undo() -> None:
            svc = ctx.get("plugins")
            assert isinstance(svc, Registry)
            svc._drop(self.kind, self.key)

        ctx.emit(lambda: None, _undo)
        ctx.notify([f"plugin:{self.kind}"])

    def use(self, ctx: Context) -> None:
        """Hot-swap: make THIS instance the active one for its kind."""
        reg = ctx.require("plugins")
        assert isinstance(reg, Registry)
        prev = reg.active.get(self.kind)

        def _do() -> None:
            reg.use(self.kind, self.key)

        def _undo() -> None:
            if prev is None:
                reg.active.pop(self.kind, None)
            else:
                reg.active[self.kind] = prev

        ctx.emit(_do, _undo)
        ctx.notify([f"plugin:{self.kind}"])

    def run(self, board: Board, *a: object, **k: object) -> Out:
        raise NotImplementedError

    def unmount(self, ctx: Context) -> None:
        """Compensate mount: drop the registry entry (idempotent — a stale
        mount-undo hitting the same entry is a masked no-op)."""
        svc = ctx.get("plugins")
        if isinstance(svc, Registry):
            svc._drop(self.kind, self.key)


class Entry:
    """One loader entry (paper §5.2.1 Def 81): id + url + isolate +
    intercept + config + disabled. The entry is the identity that survives
    revision; the fiber is the identity of one enablement."""

    def __init__(self, id: str, factory: Callable[[], Component],
                 url: str = "", inject: tuple[str, ...] = (),
                 isolate: dict[str, object] | None = None,
                 intercept: dict[str, object] | None = None,
                 config: object = None, disabled: bool = False,
                 args: tuple[object, ...] = (),
                 kwargs: dict[str, object] | None = None) -> None:
        self.id = id
        self.factory = factory
        self.url = url or factory.__qualname__
        self.inject = tuple(inject)
        self.isolate = dict(isolate or {})
        self.intercept = dict(intercept or {})
        self.config = config
        self.disabled = disabled
        self.args = args
        self.kwargs = dict(kwargs or {})
        self.fiber: Fiber | None = None
        self.component: Component | None = None
        self.scope: dict[str, object] = {}  # resolved managed realms


def classify(stashed: set[str], externals: set[str],
             imports: dict[str, set[str]]) -> tuple[set[str], set[str]]:
    """Paper Alg 8: accept a module once one import is accepted, decline
    once all imports are declined; cycles default to declined."""
    accepted = set(stashed)
    declined = set(externals)
    pending: set[str] = set()
    for url in stashed:
        pending |= imports.get(url, set()) - accepted - declined
    progress = True
    while progress:
        progress = False
        for url in list(pending):
            im = imports.get(url, set())
            if im & accepted:
                accepted.add(url)
                pending.discard(url)
                progress = True
            elif im <= declined:
                declined.add(url)
                pending.discard(url)
                progress = True
            else:
                new = im - accepted - declined
                if new - pending:
                    pending |= new
                    progress = True
    declined |= pending
    return (accepted, declined)


def stale_entries(entries: list[Entry], accepted: set[str],
                  declined: set[str],
                  deps_of: Callable[[str], set[str]]) -> list[Entry]:
    """Paper Alg 9: an entry is stale iff its dep tree (declined = boundary)
    reaches accepted; each stale tree folds into accepted."""
    acc = set(accepted)
    out: list[Entry] = []
    for e in entries:
        tree = _tree(e.url, declined, deps_of)
        if tree & acc:
            acc |= tree
            out.append(e)
    return out


def _tree(root: str, declined: set[str],
          deps_of: Callable[[str], set[str]]) -> set[str]:
    deps: set[str] = set()

    def _walk(url: str) -> None:
        if url in deps or url in declined:
            return
        deps.add(url)
        for child in deps_of(url):
            _walk(child)

    _walk(root)
    return deps


class Loader:
    """Declarative loader (paper §5.2): entries + two-phase reload."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.entries: dict[str, Entry] = {}
        # managed realms (paper §5.2.1): global name → shared symbol;
        # local realms live on the entry (tagged by id, die with it).
        self._realms: dict[str, tuple[object, int]] = {}

    def reload(self, stale: list[Entry],
               reimport: Callable[[Entry], Callable[[], Component]] | None = None) -> None:
        """Two-phase reload (paper Alg 10): phase 1 resolves all fresh
        factories up front — a reimport failure tears nothing down.
        Phase 2 swaps each entry (retire + reinsert). A mount failure
        parks that entry FAILED with its error recorded (paper §4.4);
        healthy swaps stand. Truly unexpected errors roll every swapped
        entry back to its previous factory, then re-raise.
        reimport maps an entry to its fresh factory (default: same
        factory — a new enablement, e.g. after the host invalidates
        sys.modules)."""
        reimport = reimport or (lambda e: e.factory)
        live = [e for e in stale if e.id in self.entries]
        fresh = {e.id: reimport(e) for e in live}  # may raise: nothing torn down
        backup = {e.id: e.factory for e in live}
        swapped: list[Entry] = []
        try:
            for e in live:
                if e.fiber is not None:
                    e.fiber.retire()
                    e.fiber._insert()
                    e.fiber = None
                e.factory = fresh[e.id]
                if not e.disabled:
                    e.fiber = self._spawn(e)
                swapped.append(e)
        except Exception:
            import sys
            for e in swapped:
                try:
                    if e.fiber is not None:
                        e.fiber.retire()
                        e.fiber._insert()
                        e.fiber = None
                    if e.id in backup:
                        e.factory = backup[e.id]
                        if not e.disabled:
                            e.fiber = self._spawn(e)
                except Exception as re:  # noqa: BLE001 — still re-raise below
                    print(f"registry: remount rollback failed for {e.id}: "
                          f"{type(re).__name__}: {re}", file=sys.stderr)
            raise

    # --- declarative entries (paper §5.2.1): per-field least-disruptive ---
    def declare(self, specs: list[dict[str, object]]) -> dict[str, int]:
        """Keyed diff over entry ids: add missing, drop stale, per-field
        update survivors. Returns {added, removed, updated}."""
        counts = {"added": 0, "removed": 0, "updated": 0}
        want = [str(s["id"]) for s in specs]
        for eid in list(self.entries):
            if eid not in want:
                self._drop_entry(eid)
                counts["removed"] += 1
        for spec in specs:
            eid = str(spec["id"])
            if eid not in self.entries:
                self._add_entry(spec)
                counts["added"] += 1
            else:
                counts["updated"] += self._update_entry(spec)
        return counts

    def _resolve_isolate(self, entry: Entry) -> dict[str, object]:
        """Managed realms (paper §5.2.1): True → local realm private to
        the entry (tagged by id, carried across its respawns, discarded
        with it); string → global realm shared by every entry naming it
        (refcounted, discarded when unnamed); raw object → as-is.
        Called when the isolate spec is (re)installed, paired with
        _release_isolate on scope replacement / entry drop."""
        out: dict[str, object] = {}
        for k, v in entry.isolate.items():
            if v is True:
                if entry.id not in self._realms:
                    self._realms[entry.id] = (object(), 0)
                sym, n = self._realms[entry.id]
                self._realms[entry.id] = (sym, n + 1)
                out[k] = sym
            elif isinstance(v, str):
                if v not in self._realms:
                    self._realms[v] = (object(), 0)
                sym, n = self._realms[v]
                self._realms[v] = (sym, n + 1)
                out[k] = sym
            else:
                out[k] = v
        return out

    def _release_isolate(self, entry: Entry) -> None:
        """Release realm refs; discard a realm once no entry names it."""
        for v in entry.isolate.values():
            name = entry.id if v is True else v if isinstance(v, str) else None
            if name is None:
                continue
            cur = self._realms.get(name)
            if cur is not None:
                sym, n = cur
                if n <= 1:
                    self._realms.pop(name, None)
                else:
                    self._realms[name] = (sym, n - 1)

    def _spawn(self, entry: Entry) -> Fiber:
        def _apply(fctx: Context) -> object:
            comp = entry.factory()
            entry.component = comp
            comp.mount(fctx, *entry.args, **entry.kwargs)
            if entry.config is not None:
                apply_cfg = getattr(comp, "apply_config", None)
                if callable(apply_cfg):
                    apply_cfg(entry.config)
            return lambda: comp.unmount(fctx)

        fiber = self.ctx.use(entry.inject, _apply, isolate=entry.scope or None,
                             intercept=entry.intercept or None)
        return fiber

    def _add_entry(self, spec: dict[str, object]) -> None:
        entry = self._entry_of(spec)
        self.entries[entry.id] = entry
        entry.scope = self._resolve_isolate(entry)
        if not entry.disabled:
            entry.fiber = self._spawn(entry)

    def _drop_entry(self, eid: str) -> None:
        entry = self.entries.pop(eid, None)
        if entry is None:
            return
        if entry.fiber is not None:
            entry.fiber.retire()  # O-Retire: ordered withdrawal, then dispose
            entry.fiber._insert()  # undo the O-Insert: retire + O-Remove
            entry.fiber = None
        self._release_isolate(entry)

    def _update_entry(self, spec: dict[str, object]) -> int:
        """Per-field dispatch: url/factory/scope → rebuild; intercept-only
        → in-place (consulted at read time, no reload); config → handoff
        or rebuild; disabled → retire/resume. Returns 1 if changed."""
        entry = self.entries[str(spec["id"])]
        new = self._entry_of(spec)
        if new.factory is not entry.factory or new.url != entry.url:
            self._drop_entry(entry.id)
            self._add_entry(spec)
            return 1
        changed = 0
        if new.disabled != entry.disabled:
            entry.disabled = new.disabled
            if entry.fiber is not None:
                if new.disabled:
                    entry.fiber.retire()
                else:
                    entry.fiber.resume()
            elif not new.disabled:
                entry.fiber = self._spawn(entry)
            changed = 1
        if new.isolate != entry.isolate:
            # scope changed: release, re-resolve, retire + reinsert under
            # the fresh scope (scopes aren't mutated in place)
            self._release_isolate(entry)
            entry.isolate = dict(new.isolate)
            entry.scope = self._resolve_isolate(entry)
            entry.intercept = dict(new.intercept)
            if entry.fiber is not None:
                entry.fiber.retire()
                entry.fiber._insert()
                entry.fiber = self._spawn(entry)
            changed = 1
        elif new.intercept != entry.intercept:
            entry.intercept = dict(new.intercept)
            if entry.fiber is not None:
                # Rebuild from the inherited base, never merge onto the live
                # table: declare() is a keyed diff, so a key the new spec
                # drops must leave. Merging made the old value survive its
                # own removal (paper §5.2.1 — the entry is the identity, the
                # spec is the whole truth about it).
                fctx = entry.fiber.ctx
                base = dict(fctx._parent._intercept) if fctx._parent else {}
                for k, m in entry.intercept.items():
                    assert isinstance(m, dict)
                    merged = dict(cast(dict[str, object], base.get(k, {})))
                    merged.update(cast(dict[str, object], m))
                    base[k] = merged
                fctx._intercept = base
            changed = 1  # in place: consulted at read time, no reload
        if new.config != entry.config:
            entry.config = new.config
            comp = entry.component
            apply_cfg = getattr(comp, "apply_config", None)
            if comp is not None and callable(apply_cfg):
                apply_cfg(new.config)  # component diffs the payload itself
            elif entry.fiber is not None:
                eid = entry.id
                entry.fiber.retire()
                entry.fiber._insert()
                entry.fiber = self._spawn(entry)
                assert entry.id == eid
            changed = 1
        entry.args, entry.kwargs = new.args, new.kwargs
        return changed

    @staticmethod
    def _entry_of(spec: dict[str, object]) -> Entry:
        factory = spec["factory"]
        assert callable(factory)
        inject = spec.get("inject", ())
        assert isinstance(inject, (tuple, list))
        isolate = spec.get("isolate", None)
        assert isolate is None or isinstance(isolate, dict)
        intercept = spec.get("intercept", None)
        assert intercept is None or isinstance(intercept, dict)
        args = spec.get("args", ())
        assert isinstance(args, tuple)
        kwargs = spec.get("kwargs", None)
        assert kwargs is None or isinstance(kwargs, dict)
        return Entry(str(spec["id"]), cast(Callable[[], Component], factory),
                     url=str(spec.get("url", "")),
                     inject=tuple(str(k) for k in inject),
                     isolate={str(k): v for k, v in isolate.items()} if isolate else None,
                     intercept={str(k): v for k, v in intercept.items()} if intercept else None,
                     config=spec.get("config"), disabled=bool(spec.get("disabled", False)),
                     args=args,
                     kwargs={str(k): v for k, v in kwargs.items()} if kwargs else None)
