"""Board / Module / Part / Net — all mutations flow through Context.

Circuits are written in plain Python (this API) or in JSON (agent.ir
snapshot — same schema, see agent.from_ir). No custom parser (YAGNI).
"""
from __future__ import annotations
from .core import Context, Component, Registry
from .parts import FOOTPRINTS, pin_offset as _std_pin_offset


class Part:
    def __init__(self, ref, fp, value="", x=0.0, y=0.0, w=None, h=None):
        self.ref, self.fp, self.value = ref, fp, value
        self.x, self.y = x, y
        if w is None:
            meta = FOOTPRINTS[fp]
            w, h = meta["w"], meta["h"]
        self.w, self.h = w, h

    def bbox(self):
        return (self.x - self.w / 2, self.y - self.h / 2,
                self.x + self.w / 2, self.y + self.h / 2)


class Net:
    def __init__(self, name, width=0.3, layer=None):
        self.name = name
        self.pins: list[tuple[str, str]] = []  # (ref, pin)
        self.width = width
        self.layer = layer  # None = auto


class Seg:
    def __init__(self, net, x1, y1, x2, y2, layer, width):
        self.net, self.x1, self.y1, self.x2, self.y2 = net, x1, y1, x2, y2
        self.layer, self.width = layer, width


class Board(Component):
    def __init__(self, name="board", width=40.0, height=30.0, layers=2):
        super().__init__(name)
        self.ctx = Context()
        self.width, self.height, self.layers = width, height, layers
        self.parts: dict[str, Part] = {}
        self.nets: dict[str, Net] = {}
        self.traces: list[Seg] = []
        self.constraints: list[dict] = []
        self.ctx.services["plugins"] = Registry()
        from .plugins import mount_defaults  # deferred: plugins -> solver -> circuit
        mount_defaults(self)

    # -- plugin dispatch: Board never calls solver/drc/export directly --
    def plugins(self) -> Registry:
        return self.ctx.require("plugins")

    def use(self, kind, key):
        """Hot-swap the active plugin for a kind. Undoable."""
        self.plugins().get(kind, key).use(self.ctx)

    def place(self, key=None, **k):
        return self.plugins().get("placer", key).run(self, **k)

    def route_board(self, key=None, **k):
        return self.plugins().get("router", key).run(self, **k)

    def check(self, key=None):
        return self.plugins().get("drc", key).run(self)

    def export(self, key=None, **k):
        return self.plugins().get("exporter", key).run(self, **k)

    def render(self, key=None, **k):
        return self.plugins().get("renderer", key).run(self, **k)

    # -- parts library via plugin, stdlib fallback --
    def _lib(self):
        try:
            return self.plugins().get("parts").run(self)
        except KeyError:
            return FOOTPRINTS

    def _pin_offset(self, fp, pin):
        try:
            return self.plugins().get("parts").pin_offset(fp, pin)
        except KeyError:
            return _std_pin_offset(fp, pin)

    # -- parts --
    def add_part(self, ref, fp, value="", x=None, y=None):
        lib = self._lib()
        if fp not in lib:
            raise KeyError(f"unknown footprint {fp}")
        x = self.width / 2 if x is None else x
        y = self.height / 2 if y is None else y
        meta = lib[fp]
        p = Part(ref, fp, value, x, y, meta["w"], meta["h"])
        self.ctx.emit(lambda: self.parts.__setitem__(ref, p),
                      lambda: self.parts.pop(ref, None))

    def move_part(self, ref, x, y):
        p = self.parts[ref]
        ox, oy = p.x, p.y
        self.ctx.emit(lambda: (setattr(p, "x", x), setattr(p, "y", y)),
                      lambda: (setattr(p, "x", ox), setattr(p, "y", oy)))

    def remove_part(self, ref):
        p = self.parts[ref]
        affected = [(n, list(net.pins)) for n, net in self.nets.items()
                    if any(r == ref for r, _ in net.pins)]

        def _do():
            self.parts.pop(ref, None)
            for net in self.nets.values():
                net.pins[:] = [pk for pk in net.pins if pk[0] != ref]

        def _undo():
            self.parts[ref] = p
            for n, pins in affected:
                self.nets[n].pins[:] = pins

        self.ctx.emit(_do, _undo)

    # -- nets --
    def net(self, name) -> Net:
        if name not in self.nets:
            n = Net(name)
            self.ctx.emit(lambda: self.nets.__setitem__(name, n),
                          lambda: self.nets.pop(name, None))
        return self.nets[name]

    def connect(self, netname, ref, pin):
        net = self.net(netname)
        if (ref, pin) not in net.pins:
            self.ctx.emit(lambda: net.pins.append((ref, pin)),
                          lambda: net.pins.remove((ref, pin)))

    # -- board-level --
    def set_board(self, w, h):
        ow, oh = self.width, self.height
        self.ctx.emit(lambda: (setattr(self, "width", w), setattr(self, "height", h)),
                      lambda: (setattr(self, "width", ow), setattr(self, "height", oh)))

    def constrain(self, c: dict):
        self.ctx.emit(lambda: self.constraints.append(c),
                      lambda: self.constraints.remove(c))

    def pad_pos(self, ref, pin):
        p = self.parts[ref]
        dx, dy = self._pin_offset(p.fp, pin)
        return (p.x + dx, p.y + dy)


class Module(Component):
    """Hierarchical subcircuit (atopile-style). Tracks refs it added so
    unmount removes exactly those (temporal composability)."""

    def __init__(self, name):
        super().__init__(name)
        self._refs: list[str] = []

    def add(self, board: Board, ref, fp, value="", x=None, y=None):
        board.add_part(ref, fp, value, x, y)
        self._refs.append(ref)
        return ref

    def mount(self, ctx: Context, board: Board):
        super().mount(ctx)
        self._board = board
        self.build(board)

    def build(self, board: Board):
        pass

    def unmount(self, ctx: Context, board: Board | None = None):
        board = board if board is not None else getattr(self, "_board", None)
        if board is not None:
            for ref in self._refs:
                if ref in board.parts:
                    board.remove_part(ref)
        self._refs = []
        super().unmount(ctx)
