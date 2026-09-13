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

    def pin_offset(self, fp: str, pin: PinLike, lib: object = None) -> XY:
        from typing import cast
        from .parts import pin_offset
        from .types import Footprint
        assert lib is None or isinstance(lib, dict)
        return pin_offset(fp, pin, cast(dict[str, Footprint] | None, lib))


class DiffusionPlacer(Plugin[float]):
    """Optimize wirelength + spreading (general default)."""
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


class CompactPlacer(Plugin[float]):
    """Optimize board AREA: same diffusion, tighter edge margin + stronger
    net pull, weaker spread. Mix with `edge` constraint for target size."""
    kind, key = "placer", "compact"

    def run(self, board: Board, *a: object, **k: object) -> float:
        from typing import cast
        from . import solver
        pull = _f(k.get("pull", 0.16))
        seeds = _i(k.get("seeds"), 4)
        iters = _i(k.get("iters"), 400)
        seed = _i(k.get("seed"), 0)
        frames = cast(list[Frame] | None, k.get("frames"))
        every = _i(k.get("every"), 10)
        return solver.optimize(board, seeds=seeds, iters=iters, seed=seed,
                               frames=frames, every=every,
                               pull=pull, spread=0.8, edge=0.3)


class ThermalPlacer(Plugin[float]):
    """Optimize heat: big parts (regulators, power) pushed apart + toward
    board edges. Same engine, repulsion scaled by body area."""
    kind, key = "placer", "thermal"

    def run(self, board: Board, *a: object, **k: object) -> float:
        from typing import cast
        from . import solver
        seeds = _i(k.get("seeds"), 4)
        iters = _i(k.get("iters"), 400)
        seed = _i(k.get("seed"), 0)
        frames = cast(list[Frame] | None, k.get("frames"))
        every = _i(k.get("every"), 10)
        return solver.optimize(board, seeds=seeds, iters=iters, seed=seed,
                               frames=frames, every=every,
                               thermal=True)


class GreedyLayers(Plugin[None]):
    kind, key = "layers", "greedy"

    def run(self, board: Board, *a: object, **k: object) -> None:
        from .solver import assign_layers
        assign_layers(board)


class LRouter(Plugin[int]):
    """Fast estimate routing (no obstacle avoidance). For DRC-clean boards
    use maze; for quick what-if, lroute is 10x faster."""
    kind, key = "router", "lroute"

    def run(self, board: Board, *a: object, **k: object) -> int:
        from typing import cast
        from .solver import route
        return route(board, frames=cast(list[Frame] | None, k.get("frames")))


class MazeRouter(Plugin[int]):
    """DRC-clean routing: A* wavefront avoiding parts, pads, copper.
    Slower; mix with diffusion placer for the full smart flow."""
    kind, key = "router", "maze"

    def run(self, board: Board, *a: object, **k: object) -> int:
        from typing import cast
        from .maze import maze
        return maze(board, frames=cast(list[Frame] | None, k.get("frames")))


class FabDrc(Plugin[dict[str, object]]):
    kind, key = "drc", "fab"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import drc
        fab = k.get("fab")
        assert fab is None or isinstance(fab, str)
        return drc.check(board, fab=fab)


class Erc(Plugin[dict[str, object]]):
    """Electrical rule check: netlist sanity (unconnected pins, shorts)."""
    kind, key = "drc", "erc"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import drc
        return drc.erc(board)


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


class BundleExporter(Plugin[list[str]]):
    """One-zip fab bundle: Gerbers + drill + BOM + CPL + KiCad. Upload-ready."""
    kind, key = "exporter", "bundle"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_bundle(board, outdir)


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


# tmog rules 21/28/29: one concept one hue everywhere; themes change skin,
# never structure; dark quiet chrome, brightest = live data.
THEMES: dict[str, dict[str, object]] = {
    "dark": {"bg": "#0b3d0b", "edge": "#1e5a1e", "part": "#111111",
             "courtyard": "#f1c40f", "silk": "#f5f5f5", "silk_dim": "#9a9a9a",
             "layers": ["#e74c3c", "#3498db", "#2ecc71", "#9b59b6"],
             "net": "#e67e22", "grid": "#123f12", "text": "#e8e8e8",
             "panel": "#101010", "accent": "#f1c40f"},
    "light": {"bg": "#f4f1e8", "edge": "#999999", "part": "#ffffff",
              "courtyard": "#8a6d00", "silk": "#222222", "silk_dim": "#666666",
              "layers": ["#c0392b", "#2471a3", "#1e8449", "#7d3c98"],
              "net": "#b9770e", "grid": "#ddd6c4", "text": "#222222",
              "panel": "#ffffff", "accent": "#8a6d00"},
}


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
    for fn, meta in cast(dict[str, dict[str, object]], doc.get("_imported_fp", {})).items():
        if fn not in b._lib():
            b.add_footprint(fn, meta)
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
        S = _f(k.get("scale", 10))
        theme = str(k.get("theme", "dark"))
        silk_key = k.get("silk")
        assert silk_key is None or isinstance(silk_key, (str, int))
        th = THEMES.get(theme, THEMES["dark"])
        layers = cast(list[str], th["layers"])
        W, H = board.width * S, board.height * S
        el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
              f'viewBox="0 0 {W} {H}">',
              f'<rect x="0" y="0" width="{W}" height="{H}" fill="{th["bg"]}" '
              f'stroke="{th["edge"]}"/>']
        for t in board.traces:
            c = layers[t.layer % len(layers)]
            el.append(f'<line x1="{t.x1 * S}" y1="{H - t.y1 * S}" x2="{t.x2 * S}" '
                      f'y2="{H - t.y2 * S}" stroke="{c}" stroke-width="{max(1, t.width * S)}"/>')
        for p in board.parts.values():
            x, y = (p.x - p.w / 2) * S, (H - (p.y + p.h / 2) * S)
            part, court = str(th["part"]), str(th["courtyard"])
            el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{p.w * S:.1f}" '
                      f'height="{p.h * S:.1f}" fill="{part}" stroke="{court}"/>')
        sk = board.silk(silk_key if isinstance(silk_key, str) else None)
        if isinstance(silk_key, int):
            from . import silk as _silk
            lv = _silk.labels(board, silk_key)
            sk = {"texts": list(lv.texts), "dots": list(lv.dots), "boxes": list(lv.boxes)}
        fs = 4 * S / 10
        silk = str(th["silk"])
        silk_dim = str(th["silk_dim"])
        from .silk import Box, Dot, Text
        for tx in cast(list[Text], sk["texts"]):
            fill = {"silk-ref": silk, "silk-val": silk_dim,
                    "silk-net": silk_dim}[tx.cls]
            el.append(f'<text x="{tx.x * S:.1f}" y="{(H - tx.y * S):.1f}" fill="{fill}" '
                      f'font-size="{fs:.1f}" text-anchor="middle">{tx.s}</text>')
        for d in cast(list[Dot], sk["dots"]):
            el.append(f'<circle cx="{d.x * S:.1f}" cy="{(H - d.y * S):.1f}" '
                      f'r="{max(1, 0.3 * S):.1f}" fill="{silk}"/>')
        for bx in cast(list[Box], sk["boxes"]):
            el.append(f'<rect x="{bx.x0 * S:.1f}" y="{(H - bx.y1 * S):.1f}" '
                      f'width="{(bx.x1 - bx.x0) * S:.1f}" height="{(bx.y1 - bx.y0) * S:.1f}" '
                      f'fill="none" stroke="{silk_dim}" stroke-width="0.5"/>')
        el.append("</svg>")
        return "\n".join(el)


class SchRenderer(Plugin[str]):
    """Schematic SVG: one column per net, parts as labeled boxes on their
    nets. Readable, not pretty — structure for humans, routing for machines."""
    kind, key = "renderer", "sch"

    def run(self, board: Board, *a: object, **k: object) -> str:
        theme = str(k.get("theme", "dark"))
        th = THEMES.get(theme, THEMES["dark"])
        layers = cast(list[str], th["layers"])
        nets = sorted(board.nets)
        col_w, row_h = 130, 34
        W = max(1, len(nets)) * col_w + 20
        H = 60 + max([len(board.nets[n].pins) for n in nets] + [1]) * row_h
        el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
              f'viewBox="0 0 {W} {H}">',
              f'<rect x="0" y="0" width="{W}" height="{H}" fill="{th["panel"]}"/>']
        text = str(th["text"])
        for i, n in enumerate(nets):
            x = 10 + i * col_w + col_w / 2
            el.append(f'<line x1="{x}" y1="30" x2="{x}" y2="{H - 10}" '
                      f'stroke="{layers[i % len(layers)]}" stroke-width="2"/>')
            el.append(f'<text x="{x}" y="20" fill="{text}" font-size="12" '
                      f'text-anchor="middle">{n}</text>')
            for j, (ref, pin) in enumerate(board.nets[n].pins):
                y = 50 + j * row_h
                el.append(f'<rect x="{x - 45}" y="{y - 10}" width="90" height="20" '
                          f'fill="{th["part"]}" stroke="{layers[i % len(layers)]}"/>')
                el.append(f'<text x="{x}" y="{y + 4}" fill="{text}" font-size="10" '
                          f'text-anchor="middle">{ref}.{pin}</text>')
        el.append("</svg>")
        return "\n".join(el)


class StlRenderer(Plugin[str]):
    """3D exporter: ASCII STL, board slab + real part bodies. No deps."""
    kind, key = "renderer", "stl"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from .geom3d import build
        thick = _f(k.get("thick", 1.6))
        out = [f"solid {board.name}"]
        for ta, tb, tc, _mat in build(board, thick):
            out.append("facet normal 0 0 0")
            out.append("outer loop")
            out += [f"vertex {x:.3f} {y:.3f} {z:.3f}" for x, y, z in (ta, tb, tc)]
            out.append("endloop")
            out.append("endfacet")
        out.append(f"endsolid {board.name}")
        return "\n".join(out)


class GltfRenderer(Plugin[str]):
    """3D exporter: glTF 2.0 with PBR materials (mask/copper/silk/parts).
    Textured 3D for viewers + mechanical checks. No deps."""
    kind, key = "renderer", "gltf"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from .geom3d import to_gltf
        thick = _f(k.get("thick", 1.6))
        return to_gltf(board, thick)


class RefSilk(Plugin[dict[str, object]]):
    """Minimal silk for dense boards: refs only, nothing else."""
    kind, key = "silk", "ref"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import silk as _silk
        s = _silk.labels(board, 0)
        return {"texts": list(s.texts), "dots": [], "boxes": []}


class FullSilk(Plugin[dict[str, object]]):
    """Assembly-friendly silk: refs + values + pin-1 + outlines."""
    kind, key = "silk", "full"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import silk as _silk
        s = _silk.labels(board, 2)
        return {"texts": list(s.texts), "dots": list(s.dots), "boxes": list(s.boxes)}


class FabSilk(Plugin[dict[str, object]]):
    """Fab silk: everything + net labels (debug/rework friendly)."""
    kind, key = "silk", "fab"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import silk as _silk
        s = _silk.labels(board, 3)
        return {"texts": list(s.texts), "dots": list(s.dots), "boxes": list(s.boxes)}


class FpImporter(Plugin[dict[str, object]]):
    """Footprint importer: native .fp (re-exported for plugin listing)."""
    kind, key = "importer", "fp"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .footprint import load_file
        path = k.get("path", "")
        assert isinstance(path, str) and path
        name, meta = load_file(path)
        board.add_footprint(name, meta, path)
        return {"name": name}


class KicadImporter(Plugin[dict[str, object]]):
    """Footprint importer: KiCad .kicad_mod/.pretty (pads, holes, models)."""
    kind, key = "importer", "kicad"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import load_foreign
        path = k.get("path", "")
        assert isinstance(path, str) and path
        names = []
        for name, meta in load_foreign(path):
            if name not in board._lib() or name in board.custom_fp:
                board.add_footprint(name, meta, path)
            names.append(name)
        return {"names": names}


class EagleImporter(Plugin[dict[str, object]]):
    """Footprint importer: Eagle .lbr (all packages)."""
    kind, key = "importer", "eagle"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import load_foreign
        path = k.get("path", "")
        assert isinstance(path, str) and path
        names = []
        for name, meta in load_foreign(path):
            board.add_footprint(name, meta, path)
            names.append(name)
        return {"names": names}


class TscircuitImporter(Plugin[dict[str, object]]):
    """Footprint importer: tscircuit Circuit-JSON pad soups."""
    kind, key = "importer", "tscircuit"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import load_foreign
        path = k.get("path", "")
        assert isinstance(path, str) and path
        names = []
        for name, meta in load_foreign(path):
            board.add_footprint(name, meta, path)
            names.append(name)
        return {"names": names}


class PcbImporter(Plugin[dict[str, object]]):
    """Netlist importer: .kicad_pcb → parts/nets/positions on THIS board."""
    kind, key = "importer", "pcb"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import kicad_pcb_netlist
        path = k.get("path", "")
        assert isinstance(path, str) and path
        with open(path) as f:
            ir = kicad_pcb_netlist(f.read())
        nb = from_ir(ir)
        for ref, p in nb.parts.items():
            board.add_part(ref, p.fp, p.value, p.x, p.y)
        for n, net in nb.nets.items():
            for ref, pin in net.pins:
                board.connect(n, ref, pin)
        return {"parts": len(nb.parts), "nets": len(nb.nets)}


class CalcPlugin(Plugin[dict[str, object]]):
    """Embedded calculators: trace width, via current, divider."""
    kind, key = "calc", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import calc as _calc
        what = str(k.get("what", "trace"))
        if what == "trace":
            return {"mm": _calc.trace_width(_f(k.get("amps", 1.0)),
                                            _f(k.get("rise", 10.0)),
                                            _f(k.get("oz", 1.0)))}
        if what == "via":
            return {"amps": _calc.via_amps(_f(k.get("drill", 0.3)))}
        if what == "divider":
            return {"vout": _calc.divider(_f(k.get("vin", 9.0)),
                                          _f(k.get("rtop", 10000.0)),
                                          _f(k.get("rbot", 4700.0)))}
        raise ValueError(f"unknown calc {what!r} (trace|via|divider)")


class SimPlugin(Plugin[dict[str, object]]):
    """Circuit simulator: DC operating point + transient (MNA, stdlib)."""
    kind, key = "simulate", "mna"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import sim as _sim
        what = k.pop("what", "dc")
        assert isinstance(what, str)
        return _sim.run(board, what, **k)


_DEFAULTS = (StdParts, DiffusionPlacer, CompactPlacer, ThermalPlacer,
             GreedyLayers, LRouter, MazeRouter, FabDrc, Erc,
             JlcDrc, JlcExporter, KicadExporter, BundleExporter, OcdExporter, JsonExporter,
             RefSilk, FullSilk, FabSilk,
             FpImporter, KicadImporter, EagleImporter, TscircuitImporter, PcbImporter,
             CalcPlugin, SimPlugin,
             SvgRenderer, SchRenderer, StlRenderer, GltfRenderer)


def mount_defaults(board: Board) -> Registry:
    svc = board.ctx.require("plugins")
    assert isinstance(svc, Registry)
    reg: Registry = svc
    for cls in _DEFAULTS:
        if (cls.kind, cls.key) in reg.items:
            continue
        cls(f"{cls.kind}:{cls.key}").mount(board.ctx)
    return reg
