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


class HierarchicalPlacer(Plugin[float]):
    """Two-level placer for repeated blocks: solve once per block, stamp,
    then move instances as rigid bodies. Falls back to diffusion when the
    board has no instances."""
    kind, key = "placer", "hierarchical"

    def run(self, board: Board, *a: object, **k: object) -> float:
        from typing import cast
        from .solver import hierarchical
        seeds = _i(k.get("seeds"), 4)
        iters = _i(k.get("iters"), 400)
        seed = _i(k.get("seed"), 0)
        every = _i(k.get("every"), 10)
        frames = cast(list[Frame] | None, k.get("frames"))
        return hierarchical(board, seeds=seeds, iters=iters, seed=seed,
                            frames=frames, every=every)


class MultilevelPlacer(Plugin[float]):
    """Three-level placer for 1000+ part boards: prototype internals →
    super-group rigid diffuse (~40 bodies) → per-instance refine → short
    relax. Falls back to hierarchical() without instances."""
    kind, key = "placer", "multilevel"

    def run(self, board: Board, *a: object, **k: object) -> float:
        from typing import cast
        from .solver import multilevel
        seeds = _i(k.get("seeds"), 2)
        iters = _i(k.get("iters"), 200)
        seed = _i(k.get("seed"), 0)
        every = _i(k.get("every"), 10)
        frames = cast(list[Frame] | None, k.get("frames"))
        return multilevel(board, seeds=seeds, iters=iters, seed=seed,
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


class CoarseRouter(Plugin[int]):
    """Coarse-grid maze (default 2mm): ~10x faster than full-fine maze on
    1000+ part boards. Coarse geometry throughout (no fine refinement —
    run router:maze afterwards when a board earns it)."""
    kind, key = "router", "coarse"

    def run(self, board: Board, *a: object, **k: object) -> int:
        from typing import cast
        from . import maze as _maze
        coarse = float(cast(float, k.get("grid", 2.0)))
        board.constrain({"t": "route-grid", "grid": coarse})
        try:
            return _maze.maze(board, frames=cast(list[Frame] | None, k.get("frames")))
        finally:
            board.constraints = [c for c in board.constraints
                                 if not (c.get("t") == "route-grid"
                                         and c.get("grid") == coarse)]


class WireMaskRouter(Plugin[int]):
    """WireMask-EA at block level: evolutionary search over the per-net
    layer assignment (the 'wiremask'), maze-evaluated. Population of masks
    × generations, keep best. Block-level = only inter-block nets evolve
    (intra-instance nets stay greedy)."""
    kind, key = "router", "wiremask"

    def run(self, board: Board, *a: object, **k: object) -> int:
        import random
        from typing import cast
        from . import maze as _maze
        pop = int(cast(int, k.get("pop", 6)))
        gen = int(cast(int, k.get("gen", 4)))
        seed = int(cast(int, k.get("seed", 0)))
        rng = random.Random(seed)
        # evolving set: inter-block nets with ≥2 pins (rails excluded)
        cands = [n for n, net in board.nets.items()
                 if len({board.parts[r].owner for r, _ in net.pins
                         if r in board.parts}) > 1
                 and n not in ("vcc", "vss", "GND") and len(net.pins) >= 2]
        if not cands or board.layers < 2:
            return _maze.maze(board, frames=cast(list[Frame] | None, k.get("frames")))
        old_traces = list(board.traces)

        def eval_mask(mask: dict[str, int]) -> tuple[float, list[object]]:
            for n, ll in mask.items():
                board.nets[n].layer = ll
            board.traces = []
            _maze.maze(board)
            vias = sum(1 for s in board.traces if getattr(s, "via", False))
            wl = sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in board.traces)
            return vias * 50.0 + wl, list(board.traces)

        masks = [{n: rng.randrange(board.layers) for n in cands} for _ in range(pop)]
        best: tuple[tuple[float, list[object]], dict[str, int]] | None = None
        snap = board.ctx.snapshot()  # evals emit undo entries; roll back to one
        try:
            for _ in range(gen):
                scored = sorted(((eval_mask(m), m) for m in masks),
                                key=lambda t: t[0][0])
                if best is None or scored[0][0] < best[0]:
                    best = scored[0]
                elite = [m for _, m in scored[: max(2, pop // 3)]]
                masks = list(elite)
                while len(masks) < pop:
                    p = rng.choice(elite)
                    child = dict(p)
                    for n in rng.sample(cands, max(1, len(cands) // 4)):
                        child[n] = rng.randrange(board.layers)
                    masks.append(child)
        finally:
            board.ctx.rollback(snap)
        assert best is not None
        # apply winning mask layers, final maze for real traces+frames
        _, win = best
        assert isinstance(win, dict)
        for n, ll in win.items():
            board.nets[n].layer = int(ll)
        board.traces = old_traces
        return _maze.maze(board, frames=cast(list[Frame] | None, k.get("frames")))


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


class FlexDrc(FabDrc):
    kind, key = "drc", "jlc-flex"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import drc
        fab = k.get("fab")
        assert fab is None or isinstance(fab, str)
        return drc.check(board, fab=fab or "jlc-flex")


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
                  "layers": board.layers, "fab": board.fab, "meta": dict(board.meta)},
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
    meta = bb.get("meta", {})
    assert isinstance(meta, dict)
    b.meta.update({str(k): str(v) for k, v in meta.items()})
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
            pw, ph = p.wh()
            x, y = (p.x - pw / 2) * S, (H - (p.y + ph / 2) * S)
            part, court = str(th["part"]), str(th["courtyard"])
            el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{pw * S:.1f}" '
                      f'height="{ph * S:.1f}" fill="{part}" stroke="{court}"/>')
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


class AssemblyRenderer(Plugin[str]):
    """Assembly drawing: white page, part outlines + REF + value + pin-1
    dots. For hand-assembly and inspection (replaces assembly-top/bottom)."""
    kind, key = "renderer", "assembly"

    def run(self, board: Board, *a: object, **k: object) -> str:
        S = _f(k.get("scale", 12))
        W, H = board.width * S, board.height * S
        el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
              f'viewBox="0 0 {W} {H}">',
              f'<rect x="0" y="0" width="{W}" height="{H}" fill="white"/>',
              f'<text x="8" y="16" font-size="14" font-family="monospace" fill="black">'
              f'{board.name} assembly — {len(board.parts)} parts</text>']
        el.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="none" '
                  f'stroke="black" stroke-width="2"/>')
        lib = board._lib()
        from .parts import pads_of
        for p in board.parts.values():
            pw, ph = p.wh()
            x, y = (p.x - pw / 2) * S, (H - (p.y + ph / 2) * S)
            el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{pw * S:.1f}" '
                      f'height="{ph * S:.1f}" fill="#eee" stroke="black"/>')
            el.append(f'<text x="{p.x * S:.1f}" y="{(H - p.y * S):.1f}" fill="black" '
                      f'font-size="{max(8, p.wh()[1] * S * 0.35):.1f}" text-anchor="middle" '
                      f'font-family="monospace">{p.ref}</text>')
            if p.value:
                el.append(f'<text x="{p.x * S:.1f}" y="{(H - p.y * S + p.wh()[1] * S / 2 + 9):.1f}" '
                          f'fill="#333" font-size="8" text-anchor="middle" '
                          f'font-family="monospace">{p.value}</text>')
            pads = pads_of(p.fp, lib)
            if "1" in pads:
                dx, dy = pads["1"]
                el.append(f'<circle cx="{(p.x + dx) * S:.1f}" cy="{(H - (p.y + dy) * S):.1f}" '
                          f'r="2" fill="black"/>')
        el.append("</svg>")
        return "\n".join(el)


def sch_layout(board: Board) -> dict[str, object]:
    """Shared schematic geometry (renderer + studio canvas draw the same
    picture): barycenter-ordered part columns, one rail row per net."""
    refs = sorted(board.parts)
    nets = sorted(board.nets)
    pin_nets: dict[str, set[str]] = {r: set() for r in refs}
    for n, net in board.nets.items():
        for r, _ in net.pins:
            if r in pin_nets:
                pin_nets[r].add(n)
    # barycenter sweeps: order parts so shared-net neighbors sit close
    order = list(refs)
    pos = {r: float(i) for i, r in enumerate(order)}
    for _ in range(6):
        for r in order:
            nb = [q for q in refs if q != r and pin_nets[r] & pin_nets[q]]
            if nb:
                pos[r] = sum(pos[q] for q in nb) / len(nb)
        order.sort(key=lambda r: pos[r])
    col_w, top = 120, 70
    return {"order": order, "nets": nets,
            "px": {r: 10 + i * col_w + col_w / 2 for i, r in enumerate(order)},
            "rail_y": {n: top + 20 + i * 26 for i, n in enumerate(nets)},
            "col_w": col_w, "top": top,
            "W": max(1, len(order)) * col_w + 20,
            "H": top + len(nets) * 26 + 30 + 40}


class SchRenderer(Plugin[str]):
    """Schematic SVG, Sugiyama-lite (research §5): parts as nodes in one
    barycenter-ordered row (shared nets pull together), nets as vertical
    rails with pin dots at intersections. Structure for humans."""
    kind, key = "renderer", "sch"

    def run(self, board: Board, *a: object, **k: object) -> str:
        theme = str(k.get("theme", "dark"))
        th = THEMES.get(theme, THEMES["dark"])
        layers = cast(list[str], th["layers"])
        lay = sch_layout(board)
        order = cast(list[str], lay["order"])
        nets = cast(list[str], lay["nets"])
        px = cast(dict[str, float], lay["px"])
        rail_y = cast(dict[str, float], lay["rail_y"])
        top = int(cast(int, lay["top"]))
        W = int(cast(int, lay["W"]))
        H = int(cast(int, lay["H"]))
        el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
              f'viewBox="0 0 {W} {H}">',
              f'<rect x="0" y="0" width="{W}" height="{H}" fill="{th["panel"]}"/>']
        text = str(th["text"])
        for r in order:
            p = board.parts[r]
            el.append(f'<rect x="{px[r] - 50}" y="{top - 34}" width="100" height="30" '
                      f'fill="{th["part"]}" stroke="{text}"/>')
            el.append(f'<text x="{px[r]}" y="{top - 20}" fill="{text}" font-size="11" '
                      f'text-anchor="middle">{r}</text>')
            el.append(f'<text x="{px[r]}" y="{top - 8}" fill="{text}" font-size="9" '
                      f'text-anchor="middle">{p.fp}</text>')
        for i, n in enumerate(nets):
            y = rail_y[n]
            xs = sorted(px[r] for r, _ in board.nets[n].pins if r in px)
            if not xs:
                continue
            el.append(f'<line x1="{xs[0]}" y1="{y}" x2="{xs[-1]}" y2="{y}" '
                      f'stroke="{layers[i % len(layers)]}" stroke-width="2"/>')
            el.append(f'<text x="{xs[0] - 8}" y="{y + 4}" fill="{text}" font-size="10" '
                      f'text-anchor="end">{n}</text>')
            for ref, pin in board.nets[n].pins:
                if ref not in px:
                    continue
                el.append(f'<line x1="{px[ref]}" y1="{top - 4}" x2="{px[ref]}" y2="{y}" '
                          f'stroke="{layers[i % len(layers)]}" stroke-width="1"/>')
                el.append(f'<circle cx="{px[ref]}" cy="{y}" r="3" '
                          f'fill="{layers[i % len(layers)]}"/>')
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


class PngRenderer(Plugin[bytes]):
    """2D raster: top-down PNG preview (mask/traces/pads/silk). Stdlib."""
    kind, key = "renderer", "png"

    def run(self, board: Board, *a: object, **k: object) -> bytes:
        from .raster import render_top
        return render_top(board, _f(k.get("pxmm", 10.0)))


class Html3dRenderer(Plugin[str]):
    """Interactive 3D: self-contained HTML page (WebGL, orbit/zoom, no CDN)
    with the board's glTF embedded. Double-click to open, drag to orbit."""
    kind, key = "renderer", "html3d"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from .geom3d import to_gltf
        from .view3d import page
        thick = _f(k.get("thick", 1.6))
        return page(board, to_gltf(board, thick))


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


def _guarded_add(board: Board, name: str, meta: object, path: str) -> None:
    """Strict shadowing: custom footprints may override each other, never
    the std lib (rename it). Shared by file importers (fp-line semantics)."""
    from typing import cast
    from .types import Footprint
    if name in board._lib() and name not in board.custom_fp:
        raise ValueError(f"footprint {name!r} shadows std lib (rename it)")
    board.add_footprint(name, cast(Footprint, meta), path)


class FpImporter(Plugin[dict[str, object]]):
    """Footprint importer: native .fp (re-exported for plugin listing)."""
    kind, key = "importer", "fp"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .footprint import load_file
        path = k.get("path", "")
        assert isinstance(path, str) and path
        name, meta = load_file(path)
        _guarded_add(board, name, meta, path)
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
            _guarded_add(board, name, meta, path)
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
            _guarded_add(board, name, meta, path)
            names.append(name)
        return {"names": names}


class TscircuitImporter(Plugin[dict[str, object]]):
    """tscircuit Circuit-JSON pad soups; EasyEDA Std JSON (sniffed) → board."""
    kind, key = "importer", "tscircuit"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import json
        from .foreign import easyeda_doc, load_foreign
        path = k.get("path", "")
        assert isinstance(path, str) and path
        with open(path) as f:
            text = f.read()
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and "shape" in doc:
            out = easyeda_doc(doc)
            if isinstance(out, dict):
                return _board_ir_into(board, out)
            assert isinstance(out, list)
            pairs = [(str(n), m) for n, m in out]
            for name, meta in pairs:
                _guarded_add(board, name, meta, path)
            return {"names": [n for n, _ in pairs]}
        names = []
        for name, meta in load_foreign(path):
            _guarded_add(board, name, meta, path)
            names.append(name)
        return {"names": names}


class PcbImporter(Plugin[dict[str, object]]):
    """Board importer: .kicad_pcb (sniffed) or Eagle .brd → parts/nets."""
    kind, key = "importer", "pcb"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import eagle_brd, kicad_pcb_netlist
        path = k.get("path", "")
        assert isinstance(path, str) and path
        with open(path) as f:
            text = f.read()
        s = text.lstrip()
        ir = eagle_brd(text) if s.startswith("<eagle") else kicad_pcb_netlist(text)
        return _board_ir_into(board, ir)


class EagleBoardImporter(Plugin[dict[str, object]]):
    """Board importer: Eagle .brd (elements + signals) onto THIS board."""
    kind, key = "importer", "eagle-brd"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import eagle_brd
        path = k.get("path", "")
        assert isinstance(path, str) and path
        with open(path) as f:
            ir = eagle_brd(f.read())
        return _board_ir_into(board, ir)


class EasyedaImporter(Plugin[dict[str, object]]):
    """Board importer: EasyEDA Std JSON (footprint or PCB doc)."""
    kind, key = "importer", "easyeda"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import json
        from .foreign import easyeda_doc
        path = k.get("path", "")
        assert isinstance(path, str) and path
        with open(path) as f:
            out = easyeda_doc(json.load(f))
        if isinstance(out, list):  # footprint doc → fp import
            for name, meta in out:
                _guarded_add(board, name, meta, path)
            return {"names": [n for n, _ in out]}
        assert isinstance(out, dict)
        return _board_ir_into(board, out)


def _board_ir_into(board: Board, ir: dict[str, object]) -> dict[str, object]:
    """IR (from_ir already loaded _imported_fp) → footprints + parts + nets."""
    nb = from_ir(ir)
    for fn, meta in nb.custom_fp.items():
        if fn not in board._lib():
            board.add_footprint(fn, meta)
    for ref, p in nb.parts.items():
        board.add_part(ref, p.fp, p.value, p.x, p.y)
    for n, net in nb.nets.items():
        for ref, pin in net.pins:
            board.connect(n, ref, pin)
    return {"parts": len(nb.parts), "nets": len(nb.nets)}


class EasyedaExporter(Plugin[list[str]]):
    """EasyEDA Std PCB JSON (opens in EasyEDA/JLCEDA import)."""
    kind, key = "exporter", "easyeda"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_easyeda(board, outdir)


class LintPlugin(Plugin[dict[str, object]]):
    """Static source lint: hygiene without place/route."""
    kind, key = "lint", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import lint as _lint
        return _lint.lint(board)


class ScorePlugin(Plugin[dict[str, object]]):
    """Neatness scorecard: tidy components + 0-100 scalar."""
    kind, key = "score", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import score as _score
        if k.get("tidy"):
            return _score.tidy(board)
        return _score.score(board)


class DiffPlugin(Plugin[str]):
    """Board diff vs another board."""
    kind, key = "diff", "std"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from . import diff as _diff
        from .circuit import Board as _B
        other = k.get("other")
        assert isinstance(other, _B)
        return _diff.diff(board, other)


class DoctorPlugin(Plugin[dict[str, object]]):
    """Tooling self-check: python, optional deps, registry health."""
    kind, key = "doctor", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import doctor as _doctor
        return _doctor.doctor(board)


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


class NgspicePlugin(Plugin[dict[str, object]]):
    """Circuit simulator: ngspice backend (dc|tran|ac, diodes/BJTs/opamps).
    Same return shape as mna. Missing binary → RuntimeError (use mna)."""
    kind, key = "simulate", "ngspice"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import spice as _spice
        what = k.pop("what", "dc")
        assert isinstance(what, str)
        return _spice.run(board, what, **k)


_DEFAULTS = (StdParts, DiffusionPlacer, CompactPlacer, ThermalPlacer,
             HierarchicalPlacer, MultilevelPlacer,
             GreedyLayers, LRouter, MazeRouter, CoarseRouter, WireMaskRouter,
             FabDrc, Erc,
             FlexDrc, JlcExporter, KicadExporter, EasyedaExporter,
             BundleExporter, OcdExporter, JsonExporter,
             RefSilk, FullSilk, FabSilk,
             FpImporter, KicadImporter, EagleImporter, EagleBoardImporter,
             TscircuitImporter, PcbImporter, EasyedaImporter,
             CalcPlugin, SimPlugin, NgspicePlugin, LintPlugin, DoctorPlugin,
             ScorePlugin, DiffPlugin,
             SvgRenderer, SchRenderer, AssemblyRenderer, StlRenderer, GltfRenderer,
             PngRenderer, Html3dRenderer)


def mount_defaults(board: Board) -> Registry:
    svc = board.ctx.require("plugins")
    assert isinstance(svc, Registry)
    reg: Registry = svc
    for cls in _DEFAULTS:
        if (cls.kind, cls.key) in reg.items:
            continue
        cls(f"{cls.kind}:{cls.key}").mount(board.ctx)
    return reg
