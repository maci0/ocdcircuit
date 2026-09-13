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
                 owner: str | None = None, attrs: dict[str, str] | None = None) -> None:
        self.ref, self.fp, self.value = ref, fp, value
        self.x, self.y = x, y
        self.owner = owner  # include prefix that owns it (None = local)
        self.attrs: dict[str, str] = dict(attrs or {})  # lcsc, rot, mpn...
        if w is None or h is None:
            meta = FOOTPRINTS[fp]
            assert isinstance(meta["w"], float) and isinstance(meta["h"], float)
            w, h = meta["w"], meta["h"]
        self.w, self.h = w, h

    @property
    def rot(self) -> int:
        """Rotation degrees (0/90/180/270). 90/270 swap the bbox axes."""
        try:
            return int(self.attrs.get("rot", "0")) % 360
        except ValueError:
            return 0

    def wh(self) -> tuple[float, float]:
        """Effective (w, h) after rotation."""
        if self.rot in (90, 270):
            return (self.h, self.w)
        return (self.w, self.h)

    def rot_xy(self, dx: float, dy: float) -> tuple[float, float]:
        """Rotate a footprint-frame offset into board frame."""
        r = self.rot
        if r == 90:
            return (-dy, dx)
        if r == 180:
            return (-dx, -dy)
        if r == 270:
            return (dy, -dx)
        return (dx, dy)

    def bbox(self) -> BBox:
        w, h = self.wh()
        return (self.x - w / 2, self.y - h / 2,
                self.x + w / 2, self.y + h / 2)

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


class Block:
    """Reusable subcircuit template: raw .ocd lines with LOCAL refs.
    Stamped per `instance` (see agent._instance). Never placed directly."""

    def __init__(self, name: str, lines: list[str]) -> None:
        self.name = name
        self.lines = list(lines)


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
        self.fp_src: dict[str, str] = {}  # fp name -> source path
        self.blocks: dict[str, Block] = {}  # block templates
        self.instances: list[dict[str, object]] = []  # {block, prefix, join}
        self._block_open: str | None = None  # parser scratch (not dumped)
        self._block_lines: list[str] | None = None
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

    def import_fp(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Footprint import: fp (native) / kicad / eagle / tscircuit / pcb."""
        plug = self.plugins().get("importer", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, dict)
        return out

    def calc(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Embedded calculators: trace width, via current, divider."""
        plug = self.plugins().get("calc", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, dict)
        return out

    def simulate(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Circuit simulation: what="dc" (default) | "tran"."""
        plug = self.plugins().get("simulate", key)
        assert isinstance(plug, Plugin)
        out = plug.run(self, **k)
        assert isinstance(out, dict)
        return out

    def lint(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Static lint: source hygiene, no place/route. DRC owns geometry."""
        plug = self.plugins().get("lint", key)
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

    def add_footprint(self, name: str, fp: dict[str, object],
                        src: str | None = None) -> None:
        """Register a custom (.fp) footprint. Undoable like everything.
        src: originating file path, so dumps() can re-emit the `fp` line."""
        had = name in self.custom_fp
        old = self.custom_fp.get(name)
        old_src = self.fp_src.get(name)

        def _do() -> None:
            self.custom_fp[name] = fp
            if src is not None:
                self.fp_src[name] = src

        def _undo() -> None:
            if had and old is not None:
                self.custom_fp[name] = old
                if old_src is not None:
                    self.fp_src[name] = old_src
            else:
                self.custom_fp.pop(name, None)
                self.fp_src.pop(name, None)

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
                 x: float | None = None, y: float | None = None,
                 attrs: dict[str, str] | None = None) -> None:
        lib = self._lib()
        if fp not in lib:
            raise KeyError(f"unknown footprint {fp}")
        px = self.width / 2 if x is None else x
        py = self.height / 2 if y is None else y
        meta = lib[fp]
        w = meta["w"]
        h = meta["h"]
        assert isinstance(w, float) and isinstance(h, float)
        p = Part(ref, fp, value, px, py, w, h, attrs=attrs)

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

    # -- declarative desired-state (reconcile, not verbs) --
    def declare(self, want: dict[str, object]) -> dict[str, int]:
        """Reconcile board to a desired state: {"parts": {ref: {fp, value?}},
        "nets": {net: [REF.PIN...]}, "constraints": [...], "board": {...}}.
        Adds missing, drops stale, updates changed — order-independent,
        idempotent. Returns counts. One undoable unit via snapshot/rollback
        by the caller (each sub-op already emits its own inverse)."""
        counts = {"added": 0, "removed": 0, "updated": 0, "nets": 0}
        parts = want.get("parts", {})
        nets = want.get("nets", {})
        constr = want.get("constraints", [])
        bd = want.get("board", {})
        assert isinstance(parts, dict) and isinstance(nets, dict)
        assert isinstance(constr, list) and isinstance(bd, dict)
        if "w" in bd or "h" in bd:
            bw = bd.get("w", self.width)
            bh = bd.get("h", self.height)
            assert isinstance(bw, (int, float)) and isinstance(bh, (int, float))
            if (float(bw), float(bh)) != (self.width, self.height):
                self.set_board(float(bw), float(bh))
                counts["updated"] += 1
        for ref in list(self.parts):
            if ref not in parts:
                self.remove_part(str(ref))
                counts["removed"] += 1
        for ref, spec in parts.items():
            assert isinstance(spec, dict)
            fp = str(spec.get("fp", ""))
            value = str(spec.get("value", ""))
            if ref not in self.parts:
                self.add_part(str(ref), fp, value)
                counts["added"] += 1
            else:
                p = self.parts[str(ref)]
                if p.fp != fp or p.value != value:
                    self.remove_part(str(ref))
                    self.add_part(str(ref), fp, value)
                    counts["updated"] += 1
        want_pins: dict[str, set[tuple[str, str]]] = {}
        for n, pins in nets.items():
            assert isinstance(pins, list)
            want_pins[str(n)] = {(str(r), str(q)) for tok in pins
                                 for r, q in [str(tok).split(".")]}
        for n in list(self.nets):
            if n not in want_pins:
                for ref, pin in list(self.nets[n].pins):
                    self.disconnect(n, ref, pin)
                if not self.nets[n].pins:
                    self.drop_net(n)
                counts["nets"] += 1
        for n, pins in want_pins.items():
            cur = {(r, q) for r, q in self.net(n).pins}
            for ref, pin in pins - cur:
                self.connect(n, ref, pin)
                counts["nets"] += 1
            for ref, pin in cur - pins:
                self.disconnect(n, ref, pin)
                counts["nets"] += 1
        # constraints: exact-set semantics (order-independent)
        cur_c = [self._ckey(c) for c in self.constraints]
        want_c = [self._ckey(c) for c in constr if isinstance(c, dict)]
        if sorted(cur_c) != sorted(want_c):
            for c in list(self.constraints):
                self.unconstrain(c)
            for c in constr:
                assert isinstance(c, dict)
                self.constrain(c)
            counts["updated"] += 1
        return counts

    @staticmethod
    def _ckey(c: Constraint) -> str:
        import json
        return json.dumps(c, sort_keys=True, default=str)

    def disconnect(self, netname: str, ref: str, pin: PinLike) -> None:
        """Remove one pin from a net (inverse of connect)."""
        net = self.nets.get(netname)
        if net is None:
            return
        entry = (ref, str(pin))
        if entry in net.pins:
            def _drop() -> None:
                net.pins.remove(entry)

            def _add() -> None:
                net.pins.append(entry)

            self.ctx.emit(_drop, _add)

    def drop_net(self, name: str) -> None:
        """Remove an empty net."""
        n = self.nets.get(name)
        if n is None or n.pins:
            return

        def _drop() -> None:
            self.nets.pop(name, None)

        def _add() -> None:
            self.nets[name] = n

        self.ctx.emit(_drop, _add)

    def unconstrain(self, c: Constraint) -> None:
        """Remove one constraint (inverse of constrain)."""
        if c in self.constraints:
            def _drop() -> None:
                self.constraints.remove(c)

            def _add() -> None:
                self.constraints.append(c)

            self.ctx.emit(_drop, _add)

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
        rx, ry = p.rot_xy(dx, dy)
        return (p.x + rx, p.y + ry)


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
