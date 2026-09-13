"""Board / Module / Part / Net — all mutations flow through Context.

Circuits are written in plain Python (this API) or in JSON (agent.ir
snapshot — same schema, see agent.from_ir). No custom parser (YAGNI).
"""
from __future__ import annotations
from typing import Optional
from .core import Context, Component, Registry, Plugin
from .parts import FOOTPRINTS, pin_offset as _std_pin_offset
from .types import BBox, Constraint, PinLike, XY


class Part:
    def __init__(self, ref: str, fp: str, value: str = "", x: float = 0.0,
                 y: float = 0.0, w: float | None = None, h: float | None = None,
                 owner: str | None = None) -> None:
        self.ref, self.fp, self.value = ref, fp, value
        self.x, self.y = x, y
        self.owner = owner  # include prefix that owns it (None = local)
        if w is None or h is None:
            meta = FOOTPRINTS[fp]
            assert isinstance(meta["w"], float) and isinstance(meta["h"], float)
            w, h = meta["w"], meta["h"]
        self.w, self.h = w, h

    def bbox(self) -> BBox:
        return (self.x - self.w / 2, self.y - self.h / 2,
                self.x + self.w / 2, self.y + self.h / 2)

    def pins_of(self, lib: object = None) -> list[str]:
        from typing import cast
        from .parts import pads_of
        from .types import Footprint
        return list(pads_of(self.fp, cast(dict[str, Footprint] | None, lib)))


class Net:
    def __init__(self, name: str, width: float = 0.3,
                 layer: int | None = None) -> None:
        self.name = name
        self.pins: list[tuple[str, str]] = []  # (ref, pin)
        self.width = width
        self.layer = layer  # None = auto


class Seg:
    def __init__(self, net: str, x1: float, y1: float, x2: float, y2: float,
                 layer: int, width: float) -> None:
        self.net, self.x1, self.y1, self.x2, self.y2 = net, x1, y1, x2, y2
        self.layer, self.width = layer, width


class Board(Component):
    def __init__(self, name: str = "board", width: float = 40.0,
                 height: float = 30.0, layers: int = 2) -> None:
        super().__init__(name)
        self.ctx = Context()
        self.width, self.height, self.layers = width, height, layers
        self.fab: str = "jlc"
        self.parts: dict[str, Part] = {}
        self.nets: dict[str, Net] = {}
        self.traces: list[Seg] = []
        self.constraints: list[Constraint] = []
        self.includes: list[dict[str, object]] = []  # {path, prefix, join}
        self.custom_fp: dict[str, dict[str, object]] = {}  # from `fp` lines
        self.ctx.services["plugins"] = Registry()
        from .plugins import mount_defaults  # deferred: plugins -> solver -> circuit
        mount_defaults(self)

    # -- plugin dispatch: Board never calls solver/drc/export directly --
    def plugins(self) -> Registry:
        reg = self.ctx.require("plugins")
        assert isinstance(reg, Registry)
        return reg

    def use(self, kind: str, key: str) -> None:
        """Hot-swap the active plugin for a kind. Undoable."""
        plug = self.plugins().get(kind, key)
        assert isinstance(plug, Plugin)
        plug.use(self.ctx)

    def place(self, key: str | None = None, **k: object) -> float:
        plug = self.plugins().get("placer", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, float)
        return out

    def route_board(self, key: str | None = None, **k: object) -> int:
        plug = self.plugins().get("router", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, int)
        return out

    def check(self, key: str | None = None) -> dict[str, object]:
        plug = self.plugins().get("drc", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self)
        assert isinstance(out, dict)
        return out

    def export(self, key: str | None = None, **k: object) -> list[str]:
        plug = self.plugins().get("exporter", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, list)
        return out

    def render(self, key: str | None = None, **k: object) -> str:
        plug = self.plugins().get("renderer", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, str)
        return out

    def silk(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Silkscreen generation, mix-and-match: ref (dense) / full
        (assembly) / fab (debug). Default follows the `silk <n>` line."""
        if key is None:
            from .silk import level_of
            key = ("ref" if level_of(self) == 0 else "fab"
                   if level_of(self) >= 3 else "full")
        plug = self.plugins().get("silk", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, dict)
        return out

    # -- parts library via plugin, stdlib fallback --
    def _lib(self) -> dict[str, dict[str, object]]:
        from .parts import FOOTPRINTS as STD
        merged: dict[str, dict[str, object]] = dict(STD)
        merged.update(self.custom_fp)
        try:
            plug = self.plugins().get("parts")
            assert isinstance(plug, Plugin)
            out = plug.run(self)
            assert isinstance(out, dict)
            merged.update(out)
        except KeyError:
            pass
        return merged

    def add_footprint(self, name: str, fp: dict[str, object]) -> None:
        """Register a custom (.fp) footprint. Undoable like everything."""
        had = name in self.custom_fp
        old = self.custom_fp.get(name)

        def _do() -> None:
            self.custom_fp[name] = fp

        def _undo() -> None:
            if had and old is not None:
                self.custom_fp[name] = old
            else:
                self.custom_fp.pop(name, None)

        self.ctx.emit(_do, _undo)

    def _pin_offset(self, fp: str, pin: PinLike) -> XY:
        try:
            plug = self.plugins().get("parts")
            assert isinstance(plug, Plugin)
            meth = getattr(plug, "pin_offset")
            out: XY = meth(fp, pin, self._lib())
            return out
        except (KeyError, TypeError):
            return _std_pin_offset(fp, pin, self._lib())

    # -- parts --
    def add_part(self, ref: str, fp: str, value: str = "",
                 x: float | None = None, y: float | None = None) -> None:
        lib = self._lib()
        if fp not in lib:
            raise KeyError(f"unknown footprint {fp}")
        px = self.width / 2 if x is None else x
        py = self.height / 2 if y is None else y
        meta = lib[fp]
        w = meta["w"]
        h = meta["h"]
        assert isinstance(w, float) and isinstance(h, float)
        p = Part(ref, fp, value, px, py, w, h)

        def _add() -> None:
            self.parts[ref] = p

        def _drop() -> None:
            self.parts.pop(ref, None)

        self.ctx.emit(_add, _drop)

    def move_part(self, ref: str, x: float, y: float) -> None:
        p = self.parts[ref]
        ox, oy = p.x, p.y

        def _do() -> None:
            p.x, p.y = x, y

        def _undo() -> None:
            p.x, p.y = ox, oy

        self.ctx.emit(_do, _undo)

    def remove_part(self, ref: str) -> None:
        p = self.parts[ref]
        affected = [(n, list(net.pins)) for n, net in self.nets.items()
                    if any(r == ref for r, _ in net.pins)]

        def _do() -> None:
            self.parts.pop(ref, None)
            for net in self.nets.values():
                net.pins[:] = [pk for pk in net.pins if pk[0] != ref]

        def _undo() -> None:
            self.parts[ref] = p
            for n, pins in affected:
                self.nets[n].pins[:] = pins

        self.ctx.emit(_do, _undo)

    # -- nets --
    def net(self, name: str) -> Net:
        if name not in self.nets:
            n = Net(name)

            def _add() -> None:
                self.nets[name] = n

            def _drop() -> None:
                self.nets.pop(name, None)

            self.ctx.emit(_add, _drop)
        return self.nets[name]

    def connect(self, netname: str, ref: str, pin: PinLike) -> None:
        net = self.net(netname)
        entry = (ref, str(pin))
        if entry not in net.pins:
            def _add() -> None:
                net.pins.append(entry)

            def _drop() -> None:
                net.pins.remove(entry)

            self.ctx.emit(_add, _drop)

    # -- board-level --
    def set_board(self, w: float, h: float) -> None:
        ow, oh = self.width, self.height

        def _do() -> None:
            self.width, self.height = w, h

        def _undo() -> None:
            self.width, self.height = ow, oh

        self.ctx.emit(_do, _undo)

    def constrain(self, c: Constraint) -> None:
        def _add() -> None:
            self.constraints.append(c)

        def _drop() -> None:
            self.constraints.remove(c)

        self.ctx.emit(_add, _drop)

    def pad_pos(self, ref: str, pin: PinLike) -> XY:
        p = self.parts[ref]
        dx, dy = self._pin_offset(p.fp, pin)
        return (p.x + dx, p.y + dy)


class Module(Component):
    """Hierarchical subcircuit (atopile-style). Tracks refs it added so
    unmount removes exactly those (temporal composability)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._refs: list[str] = []
        self._board: Optional[Board] = None

    def add(self, board: Board, ref: str, fp: str, value: str = "",
            x: float | None = None, y: float | None = None) -> str:
        board.add_part(ref, fp, value, x, y)
        self._refs.append(ref)
        return ref

    def mount(self, ctx: Context, *a: object, **k: object) -> None:
        super().mount(ctx)
        board = a[0] if a else k.get("board")
        assert isinstance(board, Board)
        self._board = board
        self.build(board)

    def build(self, board: Board) -> None:
        pass

    def unmount(self, ctx: Context, board: Board | None = None) -> None:
        bd = board if board is not None else self._board
        if bd is not None:
            for ref in self._refs:
                if ref in bd.parts:
                    bd.remove_part(ref)
        self._refs = []
        super().unmount(ctx)
