"""Board / Module / Part / Net — all mutations flow through Context.

Circuits are written in plain Python (this API) or in JSON (agent.ir
snapshot — same schema, see agent.from_ir). No custom parser (YAGNI).
"""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional
import json
import math
import subprocess
from .core import Context, Component, Fiber, Registry, Plugin
from .parts import FOOTPRINTS, pin_offset as _std_pin_offset
from .types import Constraint, PinLike, Undo, XY

#: Constraint types dumps() can emit. constrain() rejects anything else:
#: an unknown `t` would silently vanish on save (typo'd kinds failing
#: loud here, not as false-success applies).
CONSTRAINT_TYPES = frozenset({
    "near", "fixed", "near-group", "layer", "width", "route-grid",
    "route-penalty", "silk", "nc", "pour", "keepout", "cutout", "hole",
    "bend", "stiffener", "sim", "match", "diff", "power", "class",
})


class _AttrDict(dict[str, str]):
    """Part.attrs with geometry-cache invalidation. attrs is written
    directly everywhere (tests, agents, undo closures via clear/update) —
    invalidating only in set_attr goes stale, and stale rotation feeds
    wrong geometry to DRC. Every mutating method drops the owner's cache."""

    def __init__(self, owner: Part, src: dict[str, str] | None = None) -> None:
        super().__init__(src or {})
        self._owner = owner

    def _drop(self) -> None:
        self._owner._rot = None
        self._owner._wh = None

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self._drop()

    def __delitem__(self, key: str) -> None:
        super().__delitem__(key)
        self._drop()

    def clear(self) -> None:
        super().clear()
        self._drop()

    def pop(self, key: str) -> str:  # type: ignore[override]
        out = super().pop(key)
        self._drop()
        return out

    def popitem(self) -> tuple[str, str]:
        out = super().popitem()
        self._drop()
        return out

    def setdefault(self, key: str, value: str) -> str:
        out = super().setdefault(key, value)
        self._drop()
        return out

    def update(self, *maps: object, **kw: str) -> None:
        for m in maps:
            if isinstance(m, dict):
                for k, v in m.items():
                    self[str(k)] = str(v)
            else:
                assert isinstance(m, list)
                for k, v in m:
                    self[str(k)] = str(v)
        for k, v in kw.items():
            self[k] = v


@dataclass(eq=False, kw_only=True)  # eq=False: identity semantics, as before
class Part:
    """One placed part. Keyword-only on purpose: the constructor used to take
    nine positional parameters, and a seven-argument call silently put `attrs`
    into `owner` (see the git log). `w`/`h` default from the footprint table.
    """

    ref: str
    fp: str
    value: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float
    h: float
    owner: str | None = None  # include prefix that owns it (None = local)
    attrs: dict[str, str] = field(default_factory=dict)  # lcsc, rot, mpn...
    # Cached rotation geometry. The placer/router inner loops ask for these
    # tens of millions of times per dense board (34M `rot` + 32M `wh` calls
    # was ~11s of a 41s discrete6502 placement); _AttrDict drops the cache
    # on EVERY attrs mutation, not just set_attr (direct writes are the
    # norm: tests, agents, undo closures).
    _rot: int | None = field(default=None, init=False, repr=False, compare=False)
    _wh: tuple[float, float] | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # w/h are required: every constructor already passes the footprint's
        # courtyard (Board.add_part reads it from the library). The old
        # `None` fallback looked up FOOTPRINTS here and was never reached.
        self.attrs = _AttrDict(self, self.attrs or None)

    @property
    def rot(self) -> int:
        """Rotation degrees (0/90/180/270). 90/270 swap the bbox axes."""
        r = self._rot
        if r is None:
            try:
                r = int(self.attrs.get("rot", "0")) % 360
            except ValueError:
                r = 0
            self._rot = r
        return r

    def set_attr(self, key: str, value: str) -> None:
        """Set an attribute and drop the geometry cached from it."""
        self.attrs[key] = value
        self._rot = None
        self._wh = None

    @property
    def size(self) -> tuple[float, float]:
        """(w, h) after rotation, cached. Read-only: for hot loops."""
        wh = self._wh
        if wh is None:
            wh = (self.h, self.w) if self.rot in (90, 270) else (self.w, self.h)
            self._wh = wh
        return wh

    def wh(self) -> tuple[float, float]:
        """Effective (w, h) after rotation (cached, see `size`)."""
        return self.size

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

    def pins_of(self, lib: object = None) -> list[str]:
        from typing import cast
        from .parts import pads_of
        from .types import Footprint
        return list(pads_of(self.fp, cast(dict[str, Footprint] | None, lib)))


@dataclass(eq=False)
class Net:
    name: str
    width: float = 0.3
    layer: int | None = None  # None = auto
    attrs: dict[str, str] = field(default_factory=dict)  # class=, etc.
    pins: list[tuple[str, str]] = field(default_factory=list, init=False)  # (ref, pin)

    def __post_init__(self) -> None:
        self.attrs = dict(self.attrs or {})  # own the dict, never alias a caller's


@dataclass(eq=False)  # eq=False: identity semantics, as before dataclass
class Seg:
    """One routed copper segment. `via` marks the zero-length layer change,
    `jumper` the wire bridge DRC exempts, `drill` a via's hole.

    These three used to be set after construction and read back through
    `getattr(x, "via", False)` in 24 places across nine modules — an attribute
    the type checker could not see, which is how such a field goes missing."""

    net: str
    x1: float
    y1: float
    x2: float
    y2: float
    layer: int
    width: float
    via: bool = False
    jumper: bool = False
    drill: float = 0.4


class Block:
    """Reusable subcircuit template: raw .ocd lines with LOCAL refs.
    Stamped per `instance` (see agent._instance). Never placed directly."""

    def __init__(self, name: str, lines: list[str],
                 ports: list[str] | None = None) -> None:
        self.name = name
        self.lines = list(lines)
        # typed interface: only these nets may `join` (rest stay private).
        # Empty = legacy loose stamping (any net may join).
        self.ports = list(ports or [])


class Board(Component):
    def __init__(self, name: str = "board", width: float = 40.0,
                 height: float = 30.0, layers: int = 2, *,
                 dispatch: bool = True) -> None:
        super().__init__(name)  # name setter validates (no path separators)
        self.ctx = Context()
        # property setters validate (positive + finite size, ≥1 layer)
        self.width, self.height, self.layers = width, height, layers
        self._fab: str = "jlc"
        self.meta: dict[str, str] = {}  # `meta k v` lines: title/rev/desc/...
        self.comments: list[str] = []  # `#` lines: filled by the parser, re-emitted by dumps
        self.proj: dict[str, object] = {}  # board.toml: placer/router/drc picks (never dumped)
        self.parts: dict[str, Part] = {}
        self.nets: dict[str, Net] = {}
        self.traces: list[Seg] = []
        self.constraints: list[Constraint] = []
        self.includes: list[dict[str, object]] = []  # {path, prefix, join}
        self.custom_fp: dict[str, dict[str, object]] = {}  # from `fp` lines
        self.fp_src: dict[str, str] = {}  # fp name -> source path
        self.custom_sym: dict[str, dict[str, object]] = {}  # from `sym` lines
        self.sym_src: dict[str, str] = {}  # sym name -> source path
        self.blocks: dict[str, Block] = {}  # block templates
        self.block_src: dict[str, str] = {}  # block name -> `use` path (imported, not dumped)
        self.instances: list[dict[str, object]] = []  # {block, prefix, join}
        self._block_open: str | None = None  # parser scratch (not dumped)
        self._block_ports: list[str] = []  # ports of the open block
        self._block_lines: list[str] | None = None
        self._lib_cache: dict[str, dict[str, object]] | None = None
        self._lib_parts_key: str | None = None
        self._reg: Registry | None = None
        self.ctx.set("plugins", Registry())
        # board-owned fiber (paper Alg 4): every domain edit journals into
        # its dispose chain, so unloading the board reverts all board state.
        # The flat undo stack is untouched — snapshots/rollback keep working,
        # and rollback trims the journal via the trim hook (no stale replays).
        def _noop(_fctx: Context) -> object:
            return lambda: None

        self._fiber = self.ctx.use((), _noop)
        self._chain: list[tuple[int, Undo]] = []  # (stack depth, inverse)
        self.ctx._trim_hooks.append(self._trim_chain)
        base_dispose = self._fiber.dispose
        board = self

        def _drain() -> None:
            for _, u in reversed(board._chain):
                u()
            base_dispose()

        self._fiber.dispose = _drain
        if dispatch:
            from .plugins import mount_defaults  # deferred: plugins -> solver -> circuit
            mount_defaults(self)

    def _trim_chain(self, depth: int) -> None:
        """Drop journal entries popped off the flat stack — rollback/undo
        already ran them; the fiber chain must not replay."""
        while self._chain and self._chain[-1][0] > depth:
            self._chain.pop()

    def _load_journal(self, undo: Undo) -> None:
        """Journal a load-path inverse (agent.log_load): the parse wrote
        outside emit, so record it here at the current stack depth —
        rollback trims it, unload replays it."""
        self._chain.append((len(self.ctx._undos), undo))

    @property
    def fab(self) -> str:
        return self._fab

    @fab.setter
    def fab(self, key: str) -> None:
        # every assignment validates: a typo'd fab fails here, not deep
        # in DRC. (ValueError = fixable input, unfenced — retry works.)
        from .fab import PROFILES
        if key not in PROFILES:
            raise ValueError(f"unknown fab {key!r} (have {sorted(PROFILES)})")
        self._fab = key

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        # export filenames derive from it: no path separators, ever.
        # (ValueError = fixable input, unfenced — retry works.)
        if not name or "/" in name or "\\" in name or ".." in name or "\x00" in name:
            raise ValueError(f"bad board name {name!r} (export filenames derive from it)")
        self._name = name

    @property
    def width(self) -> float:
        return self._width

    @width.setter
    def width(self, w: float) -> None:
        if not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0:
            raise ValueError(f"board width must be positive (got {w!r})")
        self._width = w

    @property
    def height(self) -> float:
        return self._height

    @height.setter
    def height(self, h: float) -> None:
        if not isinstance(h, (int, float)) or not math.isfinite(h) or h <= 0:
            raise ValueError(f"board height must be positive (got {h!r})")
        self._height = h

    @property
    def layers(self) -> int:
        return self._layers

    @layers.setter
    def layers(self, n: int) -> None:
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(f"board needs ≥1 layer (got {n!r})")
        self._layers = n

    def emit(self, do: Callable[[], None], undo: Undo) -> Undo:
        """Board domain edit: flat-stack undo + journal into the board
        fiber's dispose chain (paper §5.1.1 — unload reverts). All
        board/engine mutations go through here, never ctx.emit directly."""
        d = self.ctx.emit(do, undo)
        self._chain.append((len(self.ctx._undos), undo))
        return d

    # -- plugin dispatch: Board never calls solver/drc/export directly --
    def proj_str(self, key: str) -> str | None:
        """A board.toml value as a string, None when unset or not a string."""
        v = self.proj.get(key)
        return v if isinstance(v, str) else None

    def plugins(self) -> Registry:
        if self._reg is None:
            reg = self.ctx.require("plugins")
            assert isinstance(reg, Registry)
            self._reg = reg
        return self._reg

    def use(self, kind: str, key: str) -> None:
        """Hot-swap the active plugin for a kind. Undoable. Explicit use
        re-arms a failed entry (failure memory only blocks implicit runs)."""
        reg = self.plugins()
        try:
            plug = reg.get(kind, key)
        except KeyError:
            if (kind, key) not in reg.items:
                raise
            plug = reg.items[(kind, key)]
        assert isinstance(plug, Plugin)
        plug.use(self.ctx)

    def _run(self, kind: str, key: str | None, **k: object) -> object:
        """Dispatch with failure memory: a crashing plugin is marked failed
        and the previous entry keeps serving (harness-loader style).
        ValueError/KeyError/OSError/AssertionError/TimeoutExpired
        (deterministic input, environment, or transient errors — bad
        constraint, unknown ref, typo'd config, missing file, wrong kwarg
        type, hung subprocess) propagate unfenced: fixing the input and
        retrying must work without explicit re-arm."""
        reg = self.plugins()
        plug = reg.get(kind, key)
        assert isinstance(plug, Plugin)
        try:
            return plug.run(self, **k)
        except (ValueError, KeyError, OSError, AssertionError,
                subprocess.TimeoutExpired):
            raise
        except Exception as e:
            reg.fail(plug.kind, plug.key, f"{type(e).__name__}: {e}")
            raise

    def place(self, key: str | None = None, **k: object) -> float:
        if key is None and len(self.parts) >= 1000:
            # ponytail: 10.6s vs 320s + fewer overlaps on discrete6502;
            # multilevel wins past ~1000 parts, diffusion below.
            key = "multilevel"
        out = self._run("placer", key, **k)
        assert isinstance(out, float)
        return out

    def route_board(self, key: str | None = None, **k: object) -> int:
        if key is None and len(self.parts) >= 1000:
            # coarse-grid maze drafts huge boards ~10x faster; refine
            # with maze after (mirrors placer multilevel auto-select).
            key = "coarse"
        out = self._run("router", key, **k)
        assert isinstance(out, int)
        return out

    def check(self, key: str | None = None, **k: object) -> dict[str, object]:
        out = self._run("drc", key, **k)
        assert isinstance(out, dict)
        return out

    def export(self, key: str | None = None, **k: object) -> list[str]:
        out = self._run("exporter", key, **k)
        assert isinstance(out, list)
        return out

    def render(self, key: str | None = None, **k: object) -> str | bytes | list[str]:
        out = self._run("renderer", key, **k)
        assert isinstance(out, (str, bytes, list))
        return out

    def render_all(self, outdir: str, keys: list[str] | None = None) -> list[str]:
        """Every mounted renderer → outdir. Thin wrapper over renderer:all."""
        out = self._run("renderer", "all", outdir=outdir, keys=keys)
        assert isinstance(out, list)
        return out

    def silk(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Silkscreen generation, mix-and-match: ref (dense) / full
        (assembly) / fab (debug). Default follows the `silk <n>` line."""
        if key is None:
            from .silk import level_of
            key = ("ref" if level_of(self) == 0 else "fab"
                   if level_of(self) >= 3 else "full")
        out = self._run("silk", key, **k)
        assert isinstance(out, dict)
        return out

    def import_fp(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Import: fp / kicad / eagle (.lbr) / eagle-brd (.brd) /
        tscircuit / pcb (.kicad_pcb, .brd, .pcb, Altium ASCII — sniffed) /
        easyeda (Std JSON) / altium (Altium ASCII or P-CAD .pcb)."""
        out = self._run("importer", key, **k)
        assert isinstance(out, dict)
        return out

    def import_sym(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Import: sym (native .sym)."""
        out = self._run("importer", key or "sym", **k)
        assert isinstance(out, dict)
        return out

    def calc(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Embedded calculators: trace width, via current, divider."""
        out = self._run("calc", key, **k)
        assert isinstance(out, dict)
        return out

    def simulate(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Circuit simulation: what="dc" (default) | "tran"."""
        out = self._run("simulate", key, **k)
        assert isinstance(out, dict)
        return out

    def lint(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Static lint: source hygiene, no place/route. DRC owns geometry."""
        out = self._run("lint", key, **k)
        assert isinstance(out, dict)
        return out

    def recommend(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Add/cut recommendations. Report-only; ops are apply_patch-ready."""
        out = self._run("recommend", key, **k)
        assert isinstance(out, dict)
        return out

    def score(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Neatness scorecard. Prefer tidy components over the scalar."""
        out = self._run("score", key, **k)
        assert isinstance(out, dict)
        return out

    def doctor(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Tooling self-check via the registry (this board proves mounting)."""
        out = self._run("doctor", key, **k)
        assert isinstance(out, dict)
        return out

    def diff(self, other: Board, key: str | None = None, **k: object) -> str:
        """What changed vs another board (knoll diff style)."""
        out = self._run("diff", key, other=other, **k)
        assert isinstance(out, str)
        return out

    def configure(self, key: str | None = None, **k: object) -> dict[str, object]:
        """Project config: board.toml defaults (fab/placer/router/drc…).
        A plugin like everything else; applied picks land in board.proj."""
        out = self._run("config", key, **k)
        assert isinstance(out, dict)
        return out

    # -- parts library via plugin, stdlib fallback --
    def _lib(self) -> dict[str, dict[str, object]]:
        active = self.plugins().active.get("parts")
        if self._lib_cache is not None and self._lib_parts_key == active:
            return self._lib_cache
        merged: dict[str, dict[str, object]] = dict(FOOTPRINTS)
        merged.update(self.custom_fp)
        try:
            plug = self.plugins().get("parts")
            assert isinstance(plug, Plugin)
            out = plug.run(self)
            assert isinstance(out, dict)
            merged.update(out)
        except KeyError:
            pass
        self._lib_cache = merged
        self._lib_parts_key = active
        return merged

    def add_footprint(self, name: str, fp: dict[str, object],
                        src: str | None = None) -> None:
        """Register a custom (.fp) footprint. Undoable like everything.
        src: originating file path, so dumps() can re-emit the `fp` line."""
        w, h = fp.get("w"), fp.get("h")
        if (not isinstance(w, (int, float)) or not isinstance(h, (int, float))
                or not (math.isfinite(w) and math.isfinite(h))):
            raise ValueError(f"footprint {name!r} needs numeric w/h (got {w!r}, {h!r})")
        if name in self._lib() and name not in self.custom_fp:
            raise ValueError(f"footprint {name!r} shadows std lib (rename it)")
        had = name in self.custom_fp
        old = self.custom_fp.get(name)
        old_src = self.fp_src.get(name)

        def _do() -> None:
            self.custom_fp[name] = fp
            self._lib_cache = None  # merged lib goes stale
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
            self._lib_cache = None

        self.emit(_do, _undo)

    def add_symbol(self, name: str, sym: dict[str, object],
                   src: str | None = None) -> None:
        """Register a custom (.sym) symbol. Undoable like everything.
        src: originating file path, so dumps() can re-emit the `sym` line."""
        w, h, pins = sym.get("w"), sym.get("h"), sym.get("pins", {})
        if (not isinstance(w, (int, float)) or not isinstance(h, (int, float))
                or not (math.isfinite(w) and math.isfinite(h))
                or not isinstance(pins, dict)):
            raise ValueError(
                f"symbol {name!r} needs numeric w/h + pins dict "
                f"(got {w!r}, {h!r}, {pins!r})")
        from .symbol import SYMBOLS
        if name in SYMBOLS and name not in self.custom_sym:
            raise ValueError(f"symbol {name!r} shadows std lib (rename it)")
        had = name in self.custom_sym
        old = self.custom_sym.get(name)
        old_src = self.sym_src.get(name)

        def _do() -> None:
            self.custom_sym[name] = sym
            if src is not None:
                self.sym_src[name] = src

        def _undo() -> None:
            if had and old is not None:
                self.custom_sym[name] = old
                if old_src is not None:
                    self.sym_src[name] = old_src
            else:
                self.custom_sym.pop(name, None)
                self.sym_src.pop(name, None)

        self.emit(_do, _undo)

    def symbol_of(self, ref: str) -> dict[str, object]:
        """Resolved + sized symbol for a part (`sym=` attr wins, else fp map)."""
        from . import symbol as _sym
        p = self.parts[ref]
        lib = dict(_sym.SYMBOLS)
        lib.update(self.custom_sym)
        s = _sym.resolve(p.fp, p.attrs.get("sym", ""), lib)
        return _sym.sized(s)

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
        if x is not None and not math.isfinite(x):
            raise ValueError(f"add {ref}: non-finite x ({x})")
        if y is not None and not math.isfinite(y):
            raise ValueError(f"add {ref}: non-finite y ({y})")
        from .parts import resolve_fp
        fp = resolve_fp(fp)  # KiCad aliases land on stdlib names here
        lib = self._lib()
        if fp not in lib:
            raise KeyError(f"unknown footprint {fp}")
        px = self.width / 2 if x is None else x
        py = self.height / 2 if y is None else y
        meta = lib[fp]
        w = meta["w"]
        h = meta["h"]
        assert isinstance(w, float) and isinstance(h, float)
        # 9-slot signature: (ref, fp, value, x, y, w, h, owner, attrs).
        # Calling with 7 positionals put attrs into owner and then collided
        # with attrs=, so every part with a custom footprint raised TypeError.
        p = Part(ref=ref, fp=fp, value=value, x=px, y=py, w=w, h=h,
                 attrs=attrs or {})
        old = self.parts.get(ref)

        def _add() -> None:
            self.parts[ref] = p

        def _drop() -> None:
            if old is not None:
                self.parts[ref] = old  # overwrite undoes to previous part
            else:
                self.parts.pop(ref, None)

        self.emit(_add, _drop)

    def move_part(self, ref: str, x: float, y: float) -> None:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(f"move {ref}: non-finite position ({x}, {y})")
        p = self.parts[ref]
        ox, oy = p.x, p.y

        def _do() -> None:
            p.x, p.y = x, y

        def _undo() -> None:
            p.x, p.y = ox, oy

        self.emit(_do, _undo)

    def set_attrs(self, ref: str, attrs: dict[str, str]) -> None:
        """Replace a part's attrs wholesale. Undoable (declare reconciles)."""
        p = self.parts[ref]
        old = dict(p.attrs)
        new = dict(attrs)

        def _do() -> None:
            p.attrs.clear()
            p.attrs.update(new)

        def _undo() -> None:
            p.attrs.clear()
            p.attrs.update(old)

        self.emit(_do, _undo)

    def set_net_attrs(self, net: str, attrs: dict[str, str]) -> None:
        """Replace a net's attrs wholesale. Undoable (declare reconciles)."""
        n = self.nets[net]
        old = dict(n.attrs)
        new = dict(attrs)

        def _do() -> None:
            n.attrs.clear()
            n.attrs.update(new)

        def _undo() -> None:
            n.attrs.clear()
            n.attrs.update(old)

        self.emit(_do, _undo)

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

        self.emit(_do, _undo)

    # -- declarative desired-state (reconcile, not verbs) --
    def declare(self, want: dict[str, object]) -> dict[str, int]:
        """Reconcile board to a desired state: {"parts": {ref: {fp, value?}},
        "nets": {net: [REF.PIN...]}, "constraints": [...], "board": {...}}.
        Adds missing, drops stale, updates changed — order-independent,
        idempotent. Atomic: a mid-reconcile failure rolls everything back.
        Returns counts."""
        snap = self.ctx.snapshot()
        try:
            return self._declare_inner(want)
        except Exception:
            self.ctx.rollback(snap)
            raise

    def _declare_inner(self, want: dict[str, object]) -> dict[str, int]:
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
            want_attrs = spec.get("attrs", {})
            assert isinstance(want_attrs, dict)
            want_attrs = {str(k): str(v) for k, v in want_attrs.items()}
            if ref not in self.parts:
                self.add_part(str(ref), fp, value, attrs=want_attrs or None)
                counts["added"] += 1
            else:
                p = self.parts[str(ref)]
                if p.fp != fp or p.value != value:
                    self.remove_part(str(ref))
                    self.add_part(str(ref), fp, value, attrs=want_attrs or None)
                    counts["updated"] += 1
                elif p.attrs != want_attrs:
                    self.set_attrs(str(ref), want_attrs)
                    counts["updated"] += 1
        want_pins: dict[str, set[tuple[str, str]]] = {}
        for n, spec in nets.items():
            pins = spec.get("pins", []) if isinstance(spec, dict) else spec
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
        for n, spec in nets.items():
            # net spec is [pins...] or {"pins": [...], "attrs": {...}}
            na: object = spec.get("attrs", {}) if isinstance(spec, dict) else {}
            assert isinstance(na, dict)
            na = {str(k): str(v) for k, v in na.items()}
            if n in self.nets and self.nets[n].attrs != na:
                self.set_net_attrs(str(n), na)
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

            self.emit(_drop, _add)

    def drop_net(self, name: str) -> None:
        """Remove an empty net."""
        n = self.nets.get(name)
        if n is None or n.pins:
            return

        def _drop() -> None:
            self.nets.pop(name, None)

        def _add() -> None:
            self.nets[name] = n

        self.emit(_drop, _add)

    def unconstrain(self, c: Constraint) -> None:
        """Remove one constraint (inverse of constrain)."""
        if c in self.constraints:
            def _drop() -> None:
                self.constraints.remove(c)

            def _add() -> None:
                self.constraints.append(c)

            self.emit(_drop, _add)

    # -- nets --
    def net(self, name: str) -> Net:
        if name not in self.nets:
            # whitespace never survives a dumps round-trip (net lines
            # split on it): reject at creation, not at save.
            if not name or any(ch.isspace() for ch in name):
                raise ValueError(f"bad net name {name!r} (no whitespace)")
            n = Net(name)

            def _add() -> None:
                self.nets[name] = n

            def _drop() -> None:
                self.nets.pop(name, None)

            self.emit(_add, _drop)
        return self.nets[name]

    def connect(self, netname: str, ref: str, pin: PinLike) -> None:
        # phantom pins build boards whose dumps won't reload (_validate
        # rejects unknown parts/pins at load): fail here instead, with
        # the same ValueError spelling _validate uses.
        p = self.parts.get(ref)
        if p is None:
            raise ValueError(f"net {netname}: unknown part {ref!r}")
        from .parts import pads_of
        if str(pin) not in pads_of(p.fp, self._lib()):
            raise ValueError(f"net {netname}: {ref} has no pin {pin!r}")
        net = self.net(netname)
        entry = (ref, str(pin))
        if entry not in net.pins:
            def _add() -> None:
                net.pins.append(entry)

            def _drop() -> None:
                net.pins.remove(entry)

            self.emit(_add, _drop)

    # -- board-level --
    def set_board(self, w: float, h: float) -> None:
        if not (math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0):
            raise ValueError(f"board size must be positive (got {w}x{h})")
        ow, oh = self.width, self.height

        def _do() -> None:
            self.width, self.height = w, h

        def _undo() -> None:
            self.width, self.height = ow, oh

        self.emit(_do, _undo)

    def constrain(self, c: Constraint) -> None:
        t = c.get("t")
        if t not in CONSTRAINT_TYPES:
            raise ValueError(f"unknown constraint type {t!r} (have {sorted(CONSTRAINT_TYPES)})")
        if t in ("layer", "pour"):
            # out-of-range layers crash routers deep inside (KeyError on
            # layer tables): fail here with the board context instead.
            # (spelled `route` like lint: `layer` is its storage type.)
            from typing import cast
            try:
                ll = int(cast(int, c.get("layer", -1)))
            except (TypeError, ValueError):
                raise ValueError(f"route {c.get('net')} has non-numeric layer")
            if not 0 <= ll < self.layers:
                raise ValueError(f"route {c.get('net')} on layer {ll} "
                                 f"(board has {self.layers}L)")
        if t == "width":
            # non-positive widths draw nothing (DRC flags them, but fail
            # fast here instead of emitting dead copper).
            w = c.get("width", 0.3)
            if not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0:
                raise ValueError(f"trace {c.get('net')} has bad width {w!r}")
        self._constrain_raw(c)

    def _constrain_raw(self, c: Constraint) -> None:
        """Append without validation: the file parser accepts junk so lint
        can report it (order-free + error-collecting, unlike this method)."""

        def _add() -> None:
            self.constraints.append(c)

        def _drop() -> None:
            # idempotent: temp constraints (coarse route-grid) are removed
            # out-of-band by their owner's finally; undo must not crash.
            if c in self.constraints:
                self.constraints.remove(c)

        self.emit(_add, _drop)

    def pad_pos(self, ref: str, pin: PinLike) -> XY:
        p = self.parts[ref]
        dx, dy = self._pin_offset(p.fp, pin)
        rx, ry = p.rot_xy(dx, dy)
        return (p.x + rx, p.y + ry)


class Module(Component):
    """Hierarchical subcircuit (atopile-style). Mount spawns a Fiber in
    the board ctx: teardown orders dependent-drain before recovery, so a
    withdrawn module deactivates its dependents while its nets still
    read (paper §5.1.3). Tracks refs it added so unmount removes exactly
    those (temporal composability)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._refs: list[str] = []
        self._board: Optional[Board] = None
        self._fiber: object | None = None

    def add(self, board: Board, ref: str, fp: str, value: str = "",
            x: float | None = None, y: float | None = None) -> str:
        board.add_part(ref, fp, value, x, y)
        self._refs.append(ref)
        return ref

    def mount(self, ctx: Context, *a: object, **k: object) -> None:
        from .core import Fiber as _Fiber
        board = a[0] if a else k.get("board")
        assert isinstance(board, Board)
        self._board = board

        def _apply(_fctx: Context) -> object:
            refs_before = set(board.parts)
            self.build(board)
            mine = [r for r in board.parts
                    if r not in refs_before or r in self._refs]
            self._refs = list(dict.fromkeys(self._refs + mine))

            def _inv() -> None:
                for ref in self._refs:
                    if ref in board.parts:
                        board.remove_part(ref)

            return _inv

        fiber = ctx.use((), _apply)
        assert isinstance(fiber, _Fiber)
        self._fiber = fiber

    def build(self, board: Board) -> None:
        pass

    def unmount(self, ctx: Context, board: Board | None = None) -> None:
        from .core import Fiber as _Fiber
        fiber = self._fiber
        if isinstance(fiber, _Fiber):
            fiber.retire()
            fiber._insert()  # undo O-Insert: ordered withdrawal + part removal
            self._fiber = None
        self._refs = []
