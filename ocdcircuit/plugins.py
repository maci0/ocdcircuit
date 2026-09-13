"""Everything is a plugin: placers, routers, layers, drc, exporters,
parts libraries, renderers (svg + 3D stl). Stdlib only, one file."""
from __future__ import annotations
from typing import TYPE_CHECKING, cast


def _i(v: object, default: int) -> int:
    if v is None:
        return default
    assert isinstance(v, (int, str))
    return int(v)


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)

from .core import Plugin, Registry
from .types import Constraint, Footprint, Frame, PinLike, XY

if TYPE_CHECKING:
    from .circuit import Board


class StdParts(Plugin[dict[str, Footprint]]):
    kind, key = "parts", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, Footprint]:
        from .parts import FOOTPRINTS
        return FOOTPRINTS

    def pin_offset(self, fp: str, pin: PinLike) -> XY:
        from .parts import pin_offset
        return pin_offset(fp, pin)


class DiffusionPlacer(Plugin[float]):
    kind, key = "placer", "diffusion"

    def run(self, board: Board, *a: object, **k: object) -> float:
        from typing import cast
        from .solver import optimize
        seeds = _i(k.get("seeds"), 4)
        iters = _i(k.get("iters"), 400)
        seed = _i(k.get("seed"), 0)
        every = _i(k.get("every"), 10)
        frames = cast(list[Frame] | None, k.get("frames"))
        return optimize(board, seeds=seeds, iters=iters, seed=seed,
                        frames=frames, every=every)


class GreedyLayers(Plugin[None]):
    kind, key = "layers", "greedy"

    def run(self, board: Board, *a: object, **k: object) -> None:
        from .solver import assign_layers
        assign_layers(board)


class LRouter(Plugin[int]):
    kind, key = "router", "lroute"

    def run(self, board: Board, *a: object, **k: object) -> int:
        from typing import cast
        from .solver import route
        return route(board, frames=cast(list[Frame] | None, k.get("frames")))


class FabDrc(Plugin[dict[str, object]]):
    kind, key = "drc", "fab"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import drc
        fab = k.get("fab")
        assert fab is None or isinstance(fab, str)
        return drc.check(board, fab=fab)


# legacy alias: JlcDrc == FabDrc(fab="jlc")
class JlcDrc(FabDrc):
    kind, key = "drc", "jlc"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import drc
        fab = k.get("fab")
        assert fab is None or isinstance(fab, str)
        return drc.check(board, fab=fab or "jlc")


class JlcExporter(Plugin[list[str]]):
    kind, key = "exporter", "jlc"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_jlc(board, outdir)


class KicadExporter(Plugin[list[str]]):
    kind, key = "exporter", "kicad"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_kicad(board, outdir)


class OcdExporter(Plugin[list[str]]):
    """The circuit language exporter (.ocd text — see agent.dumps)."""
    kind, key = "exporter", "ocd"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        import os
        from . import agent
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        os.makedirs(outdir, exist_ok=True)
        fn = os.path.join(outdir, f"{board.name}.ocd")
        open(fn, "w").write(agent.dumps(board))
        return [fn]


class JsonExporter(Plugin[list[str]]):
    kind, key = "exporter", "json"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        import os
        import json
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        os.makedirs(outdir, exist_ok=True)
        fn = os.path.join(outdir, f"{board.name}.json")
        open(fn, "w").write(json.dumps(ir_of(board), indent=1))
        return [fn]


def ir_of(board: Board) -> dict[str, object]:
    return {
        "board": {"name": board.name, "w": board.width, "h": board.height,
                  "layers": board.layers, "fab": board.fab},
        "parts": [{"ref": p.ref, "fp": p.fp, "value": p.value,
                   "x": round(p.x, 3), "y": round(p.y, 3)}
                  for p in board.parts.values()],
        "nets": {n: {"pins": [[r, pin] for r, pin in net.pins],
                     "layer": net.layer, "width": net.width}
                 for n, net in board.nets.items()},
        "constraints": board.constraints,
        "includes": board.includes,
    }


def from_ir(doc: dict[str, object]) -> Board:
    """JSON is the circuit language: agents emit this, boards load it."""
    from .circuit import Board
    bb = cast(dict[str, object], doc["board"])
    w = bb["w"]
    h = bb["h"]
    assert isinstance(w, (int, float)) and isinstance(h, (int, float))
    layers = bb.get("layers", 2)
    assert isinstance(layers, int)
    b = Board(str(bb.get("name", "board")), float(w), float(h), layers)
    fab = bb.get("fab")
    if isinstance(fab, str):
        b.fab = fab
    for p in cast(list[dict[str, object]], doc.get("parts", [])):
        x = p.get("x")
        y = p.get("y")
        b.add_part(str(p["ref"]), str(p["fp"]), str(p.get("value", "")),
                   float(x) if isinstance(x, (int, float)) else None,
                   float(y) if isinstance(y, (int, float)) else None)
    for n, net in cast(dict[str, dict[str, object]], doc.get("nets", {})).items():
        for ref, pin in cast(list[list[object]], net.get("pins", [])):
            b.connect(n, str(ref), str(pin))
        if net.get("layer") is not None:
            layer = net["layer"]
            assert isinstance(layer, int)
            b.constrain({"t": "layer", "net": n, "layer": layer})
        width = net.get("width", 0.3)
        assert isinstance(width, (int, float))
        if float(width) != 0.3:
            b.constrain({"t": "width", "net": n, "width": float(width)})
    for c in cast(list[Constraint], doc.get("constraints", [])):
        if c.get("t") not in ("layer", "width"):  # already applied above
            b.constrain(c)
    return b


class SvgRenderer(Plugin[str]):
    kind, key = "renderer", "svg"

    def run(self, board: Board, *a: object, **k: object) -> str:
        S = float(k.get("scale", 10))  # type: ignore[arg-type]
        W, H = board.width * S, board.height * S
        cols = ["#c0392b", "#2980b9", "#27ae60", "#8e44ad"]
        el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
              f'viewBox="0 0 {W} {H}">',
              f'<rect x="0" y="0" width="{W}" height="{H}" fill="#0b3d0b" '
              f'stroke="white"/>']
        for t in board.traces:
            c = cols[t.layer % len(cols)]
            el.append(f'<line x1="{t.x1 * S}" y1="{H - t.y1 * S}" x2="{t.x2 * S}" '
                      f'y2="{H - t.y2 * S}" stroke="{c}" stroke-width="{max(1, t.width * S)}"/>')
        for p in board.parts.values():
            x, y = (p.x - p.w / 2) * S, (H - (p.y + p.h / 2) * S)
            el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{p.w * S:.1f}" '
                      f'height="{p.h * S:.1f}" fill="#111" stroke="#f1c40f"/>')
            el.append(f'<text x="{p.x * S:.1f}" y="{(H - p.y * S):.1f}" fill="white" '
                      f'font-size="{4 * S / 10:.1f}" text-anchor="middle">{p.ref}</text>')
        el.append("</svg>")
        return "\n".join(el)


class StlRenderer(Plugin[str]):
    """3D exporter: ASCII STL, board slab + real part bodies. No deps."""
    kind, key = "renderer", "stl"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from .parts import bodies_of
        thick = float(k.get("thick", 1.6))  # type: ignore[arg-type]
        Tri = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
        tri: list[Tri] = []

        def box(x0: float, y0: float, z0: float,
                x1: float, y1: float, z1: float) -> None:
            v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                 (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
            for ai, bi, ci, di in [(0, 1, 2, 3), (4, 6, 5, 4), (0, 4, 5, 1),
                                   (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]:
                tri.append((v[ai], v[bi], v[ci]))
                tri.append((v[ai], v[ci], v[di]))

        def cyl(cx: float, cy: float, z0: float, r: float, h: float,
                seg: int = 12) -> None:
            import math
            for i in range(seg):
                a0 = 2 * math.pi * i / seg
                a1 = 2 * math.pi * (i + 1) / seg
                p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0))
                p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
                cc = (cx, cy)
                tri.append(((cc[0], cc[1], z0), (p0[0], p0[1], z0), (p1[0], p1[1], z0)))
                tri.append(((cc[0], cc[1], z0 + h), (p1[0], p1[1], z0 + h), (p0[0], p0[1], z0 + h)))
                tri.append(((p0[0], p0[1], z0), (p0[0], p0[1], z0 + h), (p1[0], p1[1], z0 + h)))
                tri.append(((p0[0], p0[1], z0 + h), (p1[0], p1[1], z0), (p1[0], p1[1], z0 + h)))

        box(0, 0, 0, board.width, board.height, thick)
        for p in board.parts.values():
            for body in bodies_of(p.fp):
                z0 = thick + _f(body.get("z", 0))
                if "box" in body:
                    w2, h2, bh = (_f(v) for v in cast(list[object], body["box"]))
                    ats = cast(list[object], body.get("at", [(0.0, 0.0)]))
                    for at in ats:
                        ax, ay = (_f(v) for v in cast(list[object], at))
                        box(p.x + ax - w2 / 2, p.y + ay - h2 / 2, z0,
                            p.x + ax + w2 / 2, p.y + ay + h2 / 2, z0 + bh)
                elif "cyl" in body:
                    r, bh = (_f(v) for v in cast(list[object], body["cyl"]))
                    cyl(p.x, p.y, z0, r, bh)
        out = [f"solid {board.name}"]
        for ta, tb, tc in tri:
            out.append("facet normal 0 0 0")
            out.append("outer loop")
            out += [f"vertex {x:.3f} {y:.3f} {z:.3f}" for x, y, z in (ta, tb, tc)]
            out.append("endloop")
            out.append("endfacet")
        out.append(f"endsolid {board.name}")
        return "\n".join(out)


_DEFAULTS = (StdParts, DiffusionPlacer, GreedyLayers, LRouter, FabDrc,
             JlcDrc, JlcExporter, KicadExporter, OcdExporter, JsonExporter,
             SvgRenderer, StlRenderer)


def mount_defaults(board: Board) -> Registry:
    svc = board.ctx.require("plugins")
    assert isinstance(svc, Registry)
    reg: Registry = svc
    for cls in _DEFAULTS:
        if (cls.kind, cls.key) in reg.items:
            continue
        cls(f"{cls.kind}:{cls.key}").mount(board.ctx)
    return reg
