"""Everything is a plugin: placers, routers, layers, drc, exporters,
parts libraries, renderers (svg + 3D stl). Stdlib only, one file."""
from __future__ import annotations
from .core import Plugin


class StdParts(Plugin):
    kind, key = "parts", "std"

    def run(self, board):
        from .parts import FOOTPRINTS
        return FOOTPRINTS

    def pin_offset(self, fp, pin):
        from .parts import pin_offset
        return pin_offset(fp, pin)


class DiffusionPlacer(Plugin):
    kind, key = "placer", "diffusion"

    def run(self, board, seeds=4, iters=400, seed=0):
        from .solver import optimize
        return optimize(board, seeds=seeds, iters=iters, seed=seed)


class GreedyLayers(Plugin):
    kind, key = "layers", "greedy"

    def run(self, board):
        from .solver import assign_layers
        return assign_layers(board)


class LRouter(Plugin):
    kind, key = "router", "lroute"

    def run(self, board):
        from .solver import route
        return route(board)


class JlcDrc(Plugin):
    kind, key = "drc", "jlc"

    def run(self, board):
        from . import drc
        return drc.check(board)


class JlcExporter(Plugin):
    kind, key = "exporter", "jlc"

    def run(self, board, outdir="out"):
        from . import export
        return export.export_jlc(board, outdir)


class OcdExporter(Plugin):
    """The circuit language exporter (.ocd text — see agent.dumps)."""
    kind, key = "exporter", "ocd"

    def run(self, board, outdir="out"):
        import os
        from . import agent
        os.makedirs(outdir, exist_ok=True)
        fn = os.path.join(outdir, f"{board.name}.ocd")
        open(fn, "w").write(agent.dumps(board))
        return [fn]


class JsonExporter(Plugin):
    kind, key = "exporter", "json"

    def run(self, board, outdir="out"):
        import os, json
        os.makedirs(outdir, exist_ok=True)
        fn = os.path.join(outdir, f"{board.name}.json")
        open(fn, "w").write(json.dumps(ir_of(board), indent=1))
        return [fn]


def ir_of(board):
    return {
        "board": {"name": board.name, "w": board.width, "h": board.height,
                  "layers": board.layers},
        "parts": [{"ref": p.ref, "fp": p.fp, "value": p.value,
                   "x": round(p.x, 3), "y": round(p.y, 3)}
                  for p in board.parts.values()],
        "nets": {n: {"pins": [[r, pin] for r, pin in net.pins],
                     "layer": net.layer, "width": net.width}
                 for n, net in board.nets.items()},
        "constraints": board.constraints,
    }


def from_ir(doc) -> "Board":
    """JSON is the circuit language: agents emit this, boards load it."""
    from .circuit import Board
    bb = doc["board"]
    b = Board(bb.get("name", "board"), bb["w"], bb["h"], bb.get("layers", 2))
    for p in doc.get("parts", []):
        b.add_part(p["ref"], p["fp"], p.get("value", ""), p.get("x"), p.get("y"))
    for n, net in doc.get("nets", {}).items():
        for ref, pin in net.get("pins", []):
            b.connect(n, ref, str(pin))
        if net.get("layer") is not None:
            b.constrain({"t": "layer", "net": n, "layer": net["layer"]})
        if net.get("width", 0.3) != 0.3:
            b.constrain({"t": "width", "net": n, "width": net["width"]})
    for c in doc.get("constraints", []):
        if c.get("t") not in ("layer", "width"):  # already applied above
            b.constrain(c)
    return b


class SvgRenderer(Plugin):
    kind, key = "renderer", "svg"

    def run(self, board, scale=10):
        S = scale
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


class StlRenderer(Plugin):
    """3D exporter: ASCII STL, board slab + part boxes. No deps."""
    kind, key = "renderer", "stl"

    def run(self, board, thick=1.6, part_h=1.0):
        tri = []

        def box(x0, y0, z0, x1, y1, z1):
            v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                 (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
            for a, b, c, dd in [(0, 1, 2, 3), (4, 6, 5, 4), (0, 4, 5, 1),
                                (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]:
                # (a,b,c)+(a,c,dd)
                tri.extend([(v[a], v[b], v[c]), (v[a], v[c], v[dd])])

        box(0, 0, 0, board.width, board.height, thick)
        for p in board.parts.values():
            box(p.x - p.w / 2, p.y - p.h / 2, thick,
                p.x + p.w / 2, p.y + p.h / 2, thick + part_h)
        out = [f"solid {board.name}"]
        for a, b, c in tri:
            out.append("facet normal 0 0 0")
            out.append("outer loop")
            out += [f"vertex {x:.3f} {y:.3f} {z:.3f}" for x, y, z in (a, b, c)]
            out.append("endloop")
            out.append("endfacet")
        out.append(f"endsolid {board.name}")
        return "\n".join(out)


_DEFAULTS = (StdParts, DiffusionPlacer, GreedyLayers, LRouter, JlcDrc,
             JlcExporter, OcdExporter, JsonExporter, SvgRenderer, StlRenderer)


def mount_defaults(board):
    reg = board.ctx.require("plugins")
    for cls in _DEFAULTS:
        if (cls.kind, cls.key) in reg.items:
            continue
        cls(f"{cls.kind}:{cls.key}").mount(board.ctx)
    return reg
