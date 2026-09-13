"""Unified context: revertible effects + reactive coeffects (paper §3)."""
from __future__ import annotations
from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, Optional, TypeVar
from .types import Constraint  # noqa: F401  (re-export for convenience)

if TYPE_CHECKING:
    from .circuit import Board
    from .types import Undo

Out = TypeVar("Out")


class Context:
    """Every mutation goes through emit(do, undo). Undo stack = temporal
    composability. Services dict = spatial composability (declare/resolve)."""

    def __init__(self) -> None:
        self._undos: list[Undo] = []
        self.services: dict[str, object] = {}
        self._listeners: dict[str, list[Callable[[object], None]]] = {}

    def emit(self, do: Callable[[], None], undo: Undo) -> Undo:
        do()
        self._undos.append(undo)
        return undo

    def snapshot(self) -> int:
        return len(self._undos)

    def undo(self, n: int = 1) -> None:
        for _ in range(min(n, len(self._undos))):
            self._undos.pop()()

    def rollback(self, snap: int) -> None:
        self.undo(len(self._undos) - snap)

    # --- coeffects: services ---
    def provide(self, name: str, svc: object) -> None:
        old: Optional[object] = self.services.get(name)

        def _do() -> None:
            self.services[name] = svc

        def _undo() -> None:
            if old is not None:
                self.services[name] = old
            else:
                self.services.pop(name, None)

        self.emit(_do, _undo)
        self._notify(name)

    def require(self, name: str) -> object:
        if name not in self.services:
            raise KeyError(f"unsatisfied service: {name}")
        return self.services[name]

    def on_change(self, name: str, fn: Callable[[object], None]) -> None:
        self._listeners.setdefault(name, []).append(fn)

    def _notify(self, name: str) -> None:
        for fn in self._listeners.get(name, []):
            fn(self.services.get(name))


class Component:
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()

    def __init__(self, name: str) -> None:
        self.name = name
        self._mounted = False

    def check_requires(self, ctx: Context) -> list[str]:
        return [s for s in self.requires if s not in ctx.services]

    def mount(self, ctx: Context, *a: object, **k: object) -> None:
        missing = self.check_requires(ctx)
        if missing:
            raise RuntimeError(f"{self.name} missing services: {missing}")
        self._mounted = True

    def unmount(self, ctx: Context) -> None:
        self._mounted = False


class Registry:
    """All plugins live here, itself a service. kind: placer/router/layers/
    drc/exporter/parts/renderer. One active key per kind — hot-swap = use().
    Everything undoable via Context."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], object] = {}
        self.active: dict[str, str] = {}

    def _add(self, kind: str, key: str, inst: object) -> None:
        self.items[(kind, key)] = inst

    def _drop(self, kind: str, key: str) -> None:
        self.items.pop((kind, key), None)
        if self.active.get(kind) == key:
            rest = sorted(k for (k, _kk) in self.items if k == kind)
            if rest:
                self.active[kind] = rest[0]
            else:
                self.active.pop(kind, None)

    def get(self, kind: str, key: str | None = None) -> object:
        key = key or self.active.get(kind)
        if key is None:
            opts = sorted(str(kk) for (k, kk) in self.items if k == kind)
            raise KeyError(f"no {kind} plugin mounted (have {opts})")
        try:
            return self.items[(kind, key)]
        except KeyError:
            have = sorted(f"{k}:{v}" for k, v in self.items)
            raise KeyError(f"no plugin {kind}:{key} (have {have})")

    def use(self, kind: str, key: str) -> None:
        if (kind, key) not in self.items:
            raise KeyError(f"can't swap to unmounted {kind}:{key}")
        self.active[kind] = key

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
    provides: tuple[str, ...] = ("plugins",)  # reactive coeffect

    def mount(self, ctx: Context, *a: object, **k: object) -> None:
        missing = [s for s in self.requires if s not in ctx.services]
        if missing:
            raise RuntimeError(f"{self.name} missing services: {missing}")
        reg = ctx.require("plugins")
        assert isinstance(reg, Registry)
        prev = reg.active.get(self.kind)
        reg._add(self.kind, self.key, self)
        if prev is None:
            reg.active[self.kind] = self.key
        self._mounted = True

        def _undo() -> None:
            svc = ctx.services.get("plugins")
            assert isinstance(svc, Registry)
            svc._drop(self.kind, self.key)

        ctx.emit(lambda: None, _undo)
        ctx._notify(f"plugin:{self.kind}")

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
        ctx._notify(f"plugin:{self.kind}")

    def run(self, board: Board, *a: object, **k: object) -> Out:
        raise NotImplementedError


class Loader:
    """Declarative loader with reconcile + hot remount (paper §5.2)."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.modules: dict[str, Component] = {}

    def mount(self, mod: Component, *a: object, **k: object) -> None:
        mod.mount(self.ctx, *a, **k)
        self.modules[mod.name] = mod

    def unmount(self, name: str) -> None:
        if name in self.modules:
            self.modules[name].unmount(self.ctx)
            del self.modules[name]

    def reconcile(self, want: dict[str, Callable[[], Component]]) -> None:
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

    def remount(self, name: str, factory: Callable[[], Component]) -> None:
        self.unmount(name)
        self.mount(factory())
