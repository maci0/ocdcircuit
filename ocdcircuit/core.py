"""Unified context: revertible effects + reactive coeffects (paper §3)."""
from __future__ import annotations


class Context:
    """Every mutation goes through emit(do, undo). Undo stack = temporal
    composability. Services dict = spatial composability (declare/resolve)."""

    def __init__(self):
        self._undos: list = []
        self.services: dict = {}
        self._listeners: dict[str, list] = {}

    def emit(self, do, undo):
        do()
        self._undos.append(undo)
        return undo

    def snapshot(self) -> int:
        return len(self._undos)

    def undo(self, n: int = 1):
        for _ in range(min(n, len(self._undos))):
            self._undos.pop()()

    def rollback(self, snap: int):
        self.undo(len(self._undos) - snap)

    # --- coeffects: services ---
    def provide(self, name, svc):
        old = self.services.get(name, None)
        self.emit(lambda: self.services.__setitem__(name, svc),
                  lambda: (self.services.__setitem__(name, old) if old is not None
                           else self.services.pop(name, None)))
        self._notify(name)

    def require(self, name):
        if name not in self.services:
            raise KeyError(f"unsatisfied service: {name}")
        return self.services[name]

    def on_change(self, name, fn):
        self._listeners.setdefault(name, []).append(fn)

    def _notify(self, name):
        for fn in self._listeners.get(name, []):
            fn(self.services.get(name))


class Component:
    requires: tuple = ()
    provides: tuple = ()

    def __init__(self, name):
        self.name = name
        self._mounted = False

    def check_requires(self, ctx: Context):
        return [s for s in self.requires if s not in ctx.services]

    def mount(self, ctx: Context, *a, **k):
        missing = self.check_requires(ctx)
        if missing:
            raise RuntimeError(f"{self.name} missing services: {missing}")
        self._mounted = True

    def unmount(self, ctx: Context):
        self._mounted = False


class Registry:
    """All plugins live here, itself a service. kind: placer/router/layers/
    drc/exporter/parts/renderer. One active key per kind — hot-swap = use().
    Everything undoable via Context."""

    def __init__(self):
        self.items: dict = {}
        self.active: dict = {}

    def _add(self, kind, key, inst):
        self.items[(kind, key)] = inst

    def _drop(self, kind, key):
        self.items.pop((kind, key), None)
        if self.active.get(kind) == key:
            rest = sorted(k for (k, kk) in self.items if k == kind)
            if rest:
                self.active[kind] = rest[0]
            else:
                self.active.pop(kind, None)

    def get(self, kind, key=None):
        key = key or self.active.get(kind)
        if key is None:
            opts = sorted(str(kk) for (k, kk) in self.items if k == kind)
            raise KeyError(f"no {kind} plugin mounted (have {opts})")
        try:
            return self.items[(kind, key)]
        except KeyError:
            have = sorted(f"{k}:{v}" for k, v in self.items)
            raise KeyError(f"no plugin {kind}:{key} (have {have})")

    def use(self, kind, key):
        if (kind, key) not in self.items:
            raise KeyError(f"can't swap to unmounted {kind}:{key}")
        self.active[kind] = key

    def list(self, kind=None):
        return sorted((k, v) for (k, v) in self.items if kind is None or k == kind)


class Plugin(Component):
    """Exporter, solver, parts lib, renderer — everything is one of these.
    Mount registers, unmount/rollback unregisters (temporal composability).
    Loader swaps them live: hot-swappable, no restart."""

    kind = "misc"
    key = "base"
    provides = ("plugins",)  # reactive coeffect: swaps re-resolve dependents

    def mount(self, ctx: Context, *a, **k):
        missing = [s for s in self.requires if s not in ctx.services]
        if missing:
            raise RuntimeError(f"{self.name} missing services: {missing}")
        reg: Registry = ctx.require("plugins")
        prev = reg.active.get(self.kind)
        reg._add(self.kind, self.key, self)
        if prev is None:
            reg.active[self.kind] = self.key
        self._mounted = True
        ctx.emit(lambda: None, lambda: reg._drop(self.kind, self.key))
        ctx._notify(f"plugin:{self.kind}")

    def use(self, ctx: Context):
        """Hot-swap: make THIS instance the active one for its kind."""
        reg: Registry = ctx.require("plugins")
        prev = reg.active.get(self.kind)

        def _do():
            reg.use(self.kind, self.key)

        def _undo():
            if prev is None:
                reg.active.pop(self.kind, None)
            else:
                reg.active[self.kind] = prev

        ctx.emit(_do, _undo)
        ctx._notify(f"plugin:{self.kind}")

    def run(self, board, *a, **k):
        raise NotImplementedError


class Loader:
    """Declarative loader with reconcile + hot remount (paper §5.2)."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.modules: dict[str, Component] = {}

    def mount(self, mod: Component, *a, **k):
        mod.mount(self.ctx, *a, **k)
        self.modules[mod.name] = mod

    def unmount(self, name: str):
        if name in self.modules:
            self.modules[name].unmount(self.ctx)
            del self.modules[name]

    def reconcile(self, want: dict):
        """want: {name: factory} — add missing, drop stale, remount changed."""
        for name in list(self.modules):
            if name not in want:
                self.unmount(name)
        for name, factory in want.items():
            if name not in self.modules:
                self.mount(factory())
            elif type(self.modules[name]) is not type(factory()):
                self.unmount(name)
                self.mount(factory())

    def remount(self, name: str, factory):
        self.unmount(name)
        self.mount(factory())
