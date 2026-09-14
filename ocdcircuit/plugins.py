"""Everything is a plugin: placers, routers, layers, drc, exporters,
parts libraries, renderers (svg + 3D stl). Stdlib only, one file."""
from __future__ import annotations
import re
import sys
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


class TidyPlacer(Plugin[float]):
    """Optimize neatness: evolve placements for the tidy scorecard
    (T1/T7/T8/T9), DRC errors at veto scale. Slower than diffusion —
    use when the board must look deliberate, not just route."""
    kind, key = "placer", "tidy"

    def run(self, board: Board, *a: object, **k: object) -> float:
        from typing import cast
        from . import tidy_ga as _ga
        pop = _i(k.get("pop"), 8)
        gen = _i(k.get("gen"), 6)
        seed = _i(k.get("seed"), 0)
        iters = _i(k.get("iters"), 60)
        return _ga.tidy_ga(board, pop=pop, gen=gen, seed=seed, iters=iters)


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
        lay0 = {n: net.layer for n, net in board.nets.items()}
        try:
            for _ in range(gen):
                scored = sorted(((eval_mask(m), m) for m in masks),
                                key=lambda t: t[0][0])
                if best is None or scored[0][0][0] < best[0][0]:
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
            # eval masks write net.layer directly (no emit): restore the
            # pre-eval assignment so an exception can't leave garbage layers
            for n, layer in lay0.items():
                if n in board.nets:
                    board.nets[n].layer = layer
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


class AllDrc(Plugin[dict[str, object]]):
    """Every mounted drc plugin, merged. Hits prefixed with the key
    (`erc: unconnected…`); a raising entry is skipped (registry fences it).
    `keys=[…]` runs a subset (board.toml `drc` list does this)."""
    kind, key = "drc", "all"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from typing import cast
        keys = k.get("keys")
        assert keys is None or isinstance(keys, list)
        errors: list[str] = []
        warnings: list[str] = []
        ran: list[str] = []
        cands = list(keys) if keys is not None else board.plugins().list("drc")
        for key in cands:
            if not isinstance(key, str) or key == "all":
                continue
            try:
                r = board.check(key)
            except (ValueError, KeyError, RuntimeError):
                continue
            for e in cast(list[object], r.get("errors", [])):
                errors.append(f"{key}: {e}" if not str(e).startswith(f"{key}:") else str(e))
            for w in cast(list[object], r.get("warnings", [])):
                warnings.append(f"{key}: {w}" if not str(w).startswith(f"{key}:") else str(w))
            ran.append(key)
        return {"errors": errors, "warnings": warnings, "ran": ran,
                "fab": board.fab}


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


class EagleExporter(Plugin[list[str]]):
    """Eagle .brd XML: libraries, elements, signals. Round-trips through
    importer:eagle-brd."""
    kind, key = "exporter", "eagle"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_eagle(board, outdir)


class KicadSchExporter(Plugin[list[str]]):
    """KiCad .kicad_sch: box symbols on the shared sch_layout grid, one
    wire per pin-to-rail drop, one global_label per net. ERC-clean."""
    kind, key = "exporter", "kicad-sch"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_kicad_sch(board, outdir)


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
                   "x": round(p.x, 3), "y": round(p.y, 3),
                   "attrs": dict(p.attrs)}
                  for p in board.parts.values()],
        "nets": {n: {"pins": [[r, pin] for r, pin in net.pins],
                     "layer": net.layer, "width": net.width,
                     "attrs": dict(net.attrs)}
                 for n, net in board.nets.items()},
        "constraints": board.constraints,
        "includes": board.includes,
        # custom footprints ride along (from_ir restores them): without
        # this, IR round-trips silently drop customs and parts dangle.
        "_imported_fp": {fn: dict(meta) for fn, meta in board.custom_fp.items()},
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
        pref = str(p["ref"])
        # .ocd refs must survive fix/net/nc round-trips (fix is \w+):
        # reject foreign refs outside that space instead of building
        # a board whose dumps won't reload.
        if not re.fullmatch(r"\w+", pref):
            raise ValueError(f"bad part ref {pref!r} (want \\w+)")
        x = p.get("x")
        y = p.get("y")
        attrs = p.get("attrs", {})
        assert isinstance(attrs, dict)
        b.add_part(pref, str(p["fp"]), str(p.get("value", "")),
                   float(x) if isinstance(x, (int, float)) else None,
                   float(y) if isinstance(y, (int, float)) else None,
                   attrs={str(k): str(v) for k, v in attrs.items()} or None)
    for n, net in cast(dict[str, dict[str, object]], doc.get("nets", {})).items():
        for ref, pin in cast(list[list[object]], net.get("pins", [])):
            b.connect(n, str(ref), str(pin))
        nattrs = net.get("attrs", {})
        assert isinstance(nattrs, dict)
        b.nets[n].attrs.update({str(k): str(v) for k, v in nattrs.items()})
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
    ext = ".svg"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from xml.sax.saxutils import escape
        S = _f(k.get("scale", 10))
        theme = str(k.get("theme", "dark"))
        silk_key = k.get("silk")
        assert silk_key is None or isinstance(silk_key, (str, int))
        th = THEMES.get(theme, THEMES["dark"])
        layers = cast(list[str], th["layers"])
        W, H = board.width * S, board.height * S
        el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
              f'viewBox="0 0 {W} {H}" font-family="monospace">',
              f'<title>{escape(board.name)} — {len(board.parts)} parts, '
              f'{len(board.nets)} nets</title>',
              f'<rect x="0" y="0" width="{W}" height="{H}" fill="{th["bg"]}" '
              f'stroke="{th["edge"]}" stroke-width="2" rx="6"/>']
        for t in board.traces:
            if getattr(t, "via", False):
                continue
            c = layers[t.layer % len(layers)]
            el.append(f'<line x1="{t.x1 * S}" y1="{H - t.y1 * S}" x2="{t.x2 * S}" '
                      f'y2="{H - t.y2 * S}" stroke="{c}" stroke-width="{max(1.5, t.width * S)}" '
                      f'stroke-linecap="round"/>')
        vr, hr = 0.4 * S, 0.2 * S
        for t in board.traces:
            if not getattr(t, "via", False):
                continue
            el.append(f'<circle cx="{t.x1 * S:.1f}" cy="{(H - t.y1 * S):.1f}" r="{vr:.1f}" '
                      f'fill="#d9a821" stroke="#8a6d00" stroke-width="1"/>'
                      f'<circle cx="{t.x1 * S:.1f}" cy="{(H - t.y1 * S):.1f}" r="{hr:.1f}" '
                      f'fill="{th["bg"]}" stroke="none"/>')
        from .parts import hole_drill, pad_size, pads_of
        lib = board._lib()
        for p in board.parts.values():
            for pin, (dx, dy) in pads_of(p.fp, lib).items():
                rx, ry = p.rot_xy(dx, dy)
                cx, cy = (p.x + rx) * S, H - (p.y + ry) * S
                pw, ph = pad_size(p.fp, pin, lib)
                if p.rot in (90, 270):
                    pw, ph = ph, pw
                el.append(f'<rect x="{cx - pw * S / 2:.1f}" y="{cy - ph * S / 2:.1f}" '
                          f'width="{pw * S:.1f}" height="{ph * S:.1f}" rx="1" '
                          f'fill="#d9a821" stroke="#8a6d00" stroke-width="1"/>')
                drill = hole_drill(p.fp, pin, lib)
                if drill > 0:
                    el.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{drill * S / 2:.1f}" '
                              f'fill="{th["bg"]}" stroke="#8a6d00" stroke-width="1"/>')
        for p in board.parts.values():
            pw, ph = p.wh()
            x, y = (p.x - pw / 2) * S, (H - (p.y + ph / 2) * S)
            part = str(th["part"])
            el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{pw * S:.1f}" '
                      f'height="{ph * S:.1f}" rx="1.5" fill="{part}" '
                      f'stroke="{th["text"]}" stroke-width="1"/>')
            pads = pads_of(p.fp, lib)
            if "1" in pads:
                dx, dy = p.rot_xy(*pads["1"])
                el.append(f'<circle cx="{(p.x + dx) * S:.1f}" cy="{(H - (p.y + dy) * S):.1f}" '
                          f'r="{max(1.5, 0.25 * S):.1f}" fill="{th["courtyard"]}" stroke="none"/>')
        sk = board.silk(silk_key if isinstance(silk_key, str) else None)
        if isinstance(silk_key, int):
            from . import silk as _silk
            lv = _silk.labels(board, silk_key)
            sk = {"texts": list(lv.texts), "dots": list(lv.dots), "boxes": list(lv.boxes)}
        fs = max(7.0, S * 0.9)
        silk = str(th["silk"])
        silk_dim = str(th["silk_dim"])
        from .silk import Box, Dot, Text
        for tx in cast(list[Text], sk["texts"]):
            fill = {"silk-ref": silk, "silk-val": silk_dim,
                    "silk-net": silk_dim}[tx.cls]
            size = fs if tx.cls == "silk-ref" else fs * 0.85
            el.append(f'<text x="{tx.x * S:.1f}" y="{(H - tx.y * S):.1f}" fill="{fill}" '
                      f'font-size="{size:.1f}" text-anchor="middle">{escape(tx.s)}</text>')
        for d in cast(list[Dot], sk["dots"]):
            el.append(f'<circle cx="{d.x * S:.1f}" cy="{(H - d.y * S):.1f}" '
                      f'r="{max(1, 0.3 * S):.1f}" fill="{silk}" stroke="none"/>')
        for bx in cast(list[Box], sk["boxes"]):
            el.append(f'<rect x="{bx.x0 * S:.1f}" y="{(H - bx.y1 * S):.1f}" '
                      f'width="{(bx.x1 - bx.x0) * S:.1f}" height="{(bx.y1 - bx.y0) * S:.1f}" '
                      f'fill="none" stroke="{th["courtyard"]}" stroke-width="0.7" '
                      f'stroke-dasharray="3 2"/>')
        el.append("</svg>")
        return "\n".join(el)


class AssemblyRenderer(Plugin[str]):
    """Assembly drawing: white page, part outlines + REF + value + pin-1
    dots. For hand-assembly and inspection (replaces assembly-top/bottom)."""
    kind, key = "renderer", "assembly"
    ext = ".assembly.svg"

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
            if p.attrs.get("dnp"):
                el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{pw * S:.1f}" '
                          f'height="{ph * S:.1f}" fill="none" stroke="black" '
                          f'stroke-dasharray="3,2"/>'
                          f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + pw * S:.1f}" '
                          f'y2="{y + ph * S:.1f}" stroke="black"/>'
                          f'<line x1="{x:.1f}" y2="{y + ph * S:.1f}" x2="{x + pw * S:.1f}" '
                          f'y1="{y:.1f}" stroke="black"/>')
                continue
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


def _cap(sym: dict[str, object], p: object) -> str:
    """Body caption: symbol `label` template ({ref} {value} {fp}), else ref."""
    from typing import cast
    ref = str(getattr(p, "ref", ""))
    tmpl = str(sym.get("label", ""))
    if not tmpl:
        return ref
    return tmpl.replace("{ref}", ref).replace(
        "{value}", str(getattr(p, "value", ""))).replace(
        "{fp}", str(getattr(p, "fp", "")))


class SchRenderer(Plugin[str]):
    """Schematic SVG, Sugiyama-lite (research §5): parts as symbol bodies in
    one barycenter-ordered row (shared nets pull together), nets as vertical
    rails with pin dots at intersections. Structure for humans."""
    kind, key = "renderer", "sch"
    ext = ".sch.svg"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from . import symbol as _sym
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
        unit = 9.0  # px per symbol unit
        for r in order:
            p = board.parts[r]
            sym = board.symbol_of(r)
            w = float(cast(float, sym["w"])) * unit
            h = float(cast(float, sym["h"])) * unit
            x0, y0 = px[r] - w / 2, top - 34
            if bool(sym["zigzag"]):
                # resistor zigzag: 6 peaks across the body width
                pts = [f"{x0:.1f},{y0 + h / 2:.1f}"]
                for i in range(1, 7):
                    pts.append(f"{x0 + w * i / 6:.1f},{y0 + (h / 4 if i % 2 else 3 * h / 4):.1f}")
                el.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                          f'stroke="{text}" stroke-width="1.5"/>')
            else:
                el.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" '
                          f'fill="{th["part"]}" stroke="{text}"/>')
                if bool(sym["notch"]):
                    el.append(f'<circle cx="{x0 + 4:.1f}" cy="{y0 + 4:.1f}" r="2" '
                              f'fill="{text}"/>')
            el.append(f'<text x="{px[r]}" y="{top - 40}" fill="{text}" font-size="11" '
                      f'text-anchor="middle">{_cap(sym, p)}</text>')
            # pin stubs + labels around the body edges
            pins = cast(dict[str, tuple[str, int, str]], sym["pins"])
            extra = 0
            for net in nets:
                for ref, pin in board.nets[net].pins:
                    if ref != r:
                        continue
                    sx, sy, side = _sym.pin_pos(sym, str(pin),
                                                extra if str(pin) not in pins else 0)
                    if str(pin) not in pins:
                        extra += 1
                    ax, ay = x0 + sx * unit, y0 + sy * unit
                    ox = {"left": -8.0, "right": 8.0}.get(side, 0.0)
                    oy = {"top": -8.0, "bottom": 8.0}.get(side, 0.0)
                    el.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" '
                              f'x2="{ax + ox:.1f}" y2="{ay + oy:.1f}" stroke="{text}"/>')
                    el.append(f'<circle cx="{ax + ox:.1f}" cy="{ay + oy:.1f}" r="2" '
                              f'fill="{text}"/>')
                    lbl = (p.attrs.get(f"pin{pin}") or
                           pins.get(str(pin), ("", 0, ""))[2] or str(pin))
                    el.append(f'<text x="{ax + ox * 1.6:.1f}" y="{ay + oy * 1.6 + 3:.1f}" '
                              f'fill="{text}" font-size="8" text-anchor="middle" '
                              f'font-family="monospace">{lbl}</text>')
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
    ext = ".stl"

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
    ext = ".gltf"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from .geom3d import to_gltf
        thick = _f(k.get("thick", 1.6))
        return to_gltf(board, thick)


class PngRenderer(Plugin[bytes]):
    """2D raster: top-down PNG preview (mask/traces/pads/silk). Stdlib."""
    kind, key = "renderer", "png"
    ext = ".png"

    def run(self, board: Board, *a: object, **k: object) -> bytes:
        from .raster import render_top
        return render_top(board, _f(k.get("pxmm", 10.0)))


class BlenderRenderer(Plugin[bytes]):
    """Studio product shot via Blender headless (needs flatpak
    org.blender.Blender; missing → RuntimeError). Imports our glTF,
    3/4 product angle, key+fill suns, EEVEE. ~30-60s per board."""
    kind, key = "renderer", "blender"
    ext = ".studio.png"

    SCRIPT = """
import bpy, sys
from mathutils import Vector
ai = sys.argv.index('--') + 1
SRC, DST = sys.argv[ai], sys.argv[ai+1]
W, H = int(sys.argv[ai+2]), int(sys.argv[ai+3])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=SRC)
sc = bpy.context.scene
sc.world = bpy.data.worlds.new('W')
sc.world.use_nodes = True
bg = sc.world.node_tree.nodes['Background']
bg.inputs[0].default_value = (0.06, 0.06, 0.09, 1.0)
bg.inputs[1].default_value = 1.2
mn = Vector((1e9,)*3); mx = Vector((-1e9,)*3)
for o in sc.objects:
    if o.type != 'MESH':
        continue
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        mn = Vector(map(min, mn, w)); mx = Vector(map(max, mx, w))
ctr, size = (mn+mx)/2, mx-mn
span = max(size.x, size.y, size.z)
bpy.ops.object.empty_add(location=ctr)
tgt = bpy.context.active_object
cam = bpy.data.cameras.new('Cam')
co = bpy.data.objects.new('Cam', cam)
sc.collection.objects.link(co)
sc.camera = co
co.location = (ctr.x + span*0.55, ctr.y - span*0.75, ctr.z + span*0.75)
con = co.constraints.new('TRACK_TO')
con.target = tgt
con.track_axis = 'TRACK_NEGATIVE_Z'
con.up_axis = 'UP_Y'
key = bpy.data.lights.new('Key', 'SUN')
ko = bpy.data.objects.new('Key', key)
sc.collection.objects.link(ko)
ko.location = (ctr.x+30, ctr.y-30, 50)
key.energy = 8.0
fill = bpy.data.lights.new('Fill', 'SUN')
fo = bpy.data.objects.new('Fill', fill)
sc.collection.objects.link(fo)
fo.location = (ctr.x-30, ctr.y+30, 20)
fill.energy = 3.0
try:
    sc.render.engine = 'BLENDER_EEVEE_NEXT'
except TypeError:
    sc.render.engine = 'BLENDER_EEVEE'
sc.render.resolution_x, sc.render.resolution_y = W, H
sc.render.film_transparent = False
sc.render.filepath = DST
bpy.ops.render.render(write_still=True)
"""

    def run(self, board: Board, *a: object, **k: object) -> bytes:
        import os
        import shutil
        import subprocess
        import tempfile
        from .geom3d import to_gltf
        if shutil.which("flatpak") is None:
            raise RuntimeError("flatpak not found; use renderer kicad "
                               "for the fast preview")
        w = _i(k.get("width"), 1200)
        h = _i(k.get("height"), 800)
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, board.name + ".gltf")
            out = os.path.join(tmp, board.name + ".png")
            script = os.path.join(tmp, "studio.py")
            open(src, "w").write(to_gltf(board))
            open(script, "w").write(self.SCRIPT)
            r = subprocess.run(
                ["flatpak", "run", "--filesystem=" + tmp,
                 "org.blender.Blender", "--background",
                 "--python", script, "--", src, out, str(w), str(h)],
                capture_output=True, timeout=300)
            if r.returncode != 0 or not os.path.isfile(out):
                raise RuntimeError(
                    "blender render failed "
                    f"(have org.blender.Blender? {r.stderr.decode()[-300:]})")
            with open(out, "rb") as f:
                return f.read()


class KicadRenderer(Plugin[bytes]):
    """Photorealistic PNG via kicad-cli's 3D raytracer (needs KiCad 9+;
    missing binary → RuntimeError naming the apt package). Exports the
    board to .kicad_pcb, renders, returns PNG bytes. Mask color follows
    `meta mask <color>` (green/red/blue/black/white/purple/yellow)."""
    kind, key = "renderer", "kicad"
    ext = ".ray.png"

    def run(self, board: Board, *a: object, **k: object) -> bytes:
        import shutil
        import subprocess
        import tempfile
        from .export import MASK_COLORS, export_kicad
        exe = shutil.which("kicad-cli")
        if exe is None:
            raise RuntimeError("kicad-cli not found (apt install kicad); "
                               "use renderer png for the stdlib preview")
        side = str(k.get("side", "top"))
        w = _i(k.get("width"), 1200)
        h = _i(k.get("height"), 800)
        mask = MASK_COLORS.get(str(board.meta.get("mask", "green")).lower(),
                               MASK_COLORS["green"])
        with tempfile.TemporaryDirectory() as tmp:
            export_kicad(board, tmp)
            src = f"{tmp}/{board.name}.kicad_pcb"
            out = f"{tmp}/{board.name}.png"
            cmd = [exe, "pcb", "render", "--side", side, "--width", str(w),
                   "--height", str(h), "--quality", "high", "--floor",
                   "--perspective", "--background", "opaque",
                   "--light-top", "0.7", "--light-bottom", "0.3",
                   "--output", out, src]
            self._render_with_mask(cmd, mask, out)
            with open(out, "rb") as f:
                return f.read()

    @staticmethod
    def _render_with_mask(cmd: list[str], mask: tuple[int, int, int],
                          out: str) -> None:
        """Run kicad-cli with the board's soldermask color patched into
        the 3D-viewer preset (restored after). No mask patch = KiCad's
        default drab olive."""
        import json
        import os
        import subprocess
        cfg = os.path.expanduser("~/.config/kicad/10.0/3d_viewer.json")
        try:
            d = json.load(open(cfg))
        except (OSError, ValueError):
            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
            return
        presets = d.get("layer_presets", [])
        if not presets:
            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
            return
        saved = json.dumps(presets[0].get("colors", []))
        try:
            for c in presets[0].get("colors", []):
                if c.get("layer") in ("soldermask_top", "soldermask_bottom"):
                    r, g, b = mask
                    c["color"] = f"rgba({r}, {g}, {b}, 0.831)"
            json.dump(d, open(cfg, "w"), indent=2)
            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
        finally:
            d["layer_presets"][0]["colors"] = json.loads(saved)
            json.dump(d, open(cfg, "w"), indent=2)
        if not os.path.isfile(out):
            raise RuntimeError("kicad-cli did not produce a rendered image")


class PcbdrawRenderer(Plugin[str]):
    """Stylized fabrication drawing via pcbdraw (needs `pip install pcbdraw`;
    missing → RuntimeError). Exports .kicad_pcb, plots styled SVG.
    Style follows `meta style <name>` (default jlcpcb-green-enig)."""
    kind, key = "renderer", "pcbdraw"
    ext = ".fab.svg"

    def run(self, board: Board, *a: object, **k: object) -> str:
        import shutil
        import subprocess
        import tempfile
        from .export import export_kicad
        exe = shutil.which("pcbdraw")
        if exe is None:
            raise RuntimeError("pcbdraw not found (pip install pcbdraw); "
                               "use renderer svg for the stdlib drawing")
        style = str(k.get("style", board.meta.get("style", "jlcpcb-green-enig")))
        side = str(k.get("side", "front"))
        with tempfile.TemporaryDirectory() as tmp:
            export_kicad(board, tmp)
            src = f"{tmp}/{board.name}.kicad_pcb"
            out = f"{tmp}/{board.name}.svg"
            subprocess.run([exe, "plot", "-s", style, "--side", side,
                            "--silent", src, out],
                           capture_output=True, check=True, timeout=300)
            return open(out).read()


class EasyedaRenderer(Plugin[str]):
    """EasyEDA-editor-look SVG: black canvas, red/blue copper, yellow pads
    — the colors you see after importing our .easyeda.json. Stdlib, parses
    our own Std JSON export back (no client, no bridge)."""
    kind, key = "renderer", "easyeda"
    ext = ".easyeda.svg"

    def run(self, board: Board, *a: object, **k: object) -> str:
        import json
        import tempfile
        from .export import export_easyeda
        from xml.sax.saxutils import escape
        S = _f(k.get("scale", 10))
        with tempfile.TemporaryDirectory() as tmp:
            fn = export_easyeda(board, tmp)[0]
            doc = json.load(open(fn))
        W, H = board.width * S, board.height * S
        mm = 1 / 0.254  # export units: 10-mil
        el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
              f'viewBox="0 0 {W} {H}" font-family="monospace">',
              f'<title>{escape(board.name)} — EasyEDA view</title>',
              f'<rect x="0" y="0" width="{W}" height="{H}" fill="black"/>']
        copper = {"1": "#FF0000", "2": "#0000FF"}
        for sh in doc["shape"]:
            parts = sh.split("~")
            if parts[0] == "TRACK":
                _w, layer = float(parts[1]) * 0.254, parts[2]
                x1, y1, x2, y2 = (float(v) / mm * S for v in parts[4].split())
                c = copper.get(layer, "#FF0000")
                el.append(f'<line x1="{x1:.1f}" y1="{H - y1:.1f}" x2="{x2:.1f}" '
                          f'y2="{H - y2:.1f}" stroke="{c}" '
                          f'stroke-width="{max(1.5, _w * S):.1f}" '
                          f'stroke-linecap="round"/>')
            elif parts[0] == "VIA":
                x, y = float(parts[1]) / mm * S, H - float(parts[2]) / mm * S
                el.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{0.4 * S:.1f}" '
                          f'fill="none" stroke="#FFFF00" stroke-width="1.5"/>')
            elif parts[0] == "LIB":
                bx, by = float(parts[1]) / mm * S, H - float(parts[2]) / mm * S
                for kid in sh.split("#@$")[1:]:
                    f = kid.split("~")
                    if f[0] == "PAD":
                        px, py = bx + float(f[2]) / mm * S, by - float(f[3]) / mm * S
                        pw, ph = float(f[4]) / mm * S, float(f[5]) / mm * S
                        el.append(f'<rect x="{px - pw / 2:.1f}" y="{py - ph / 2:.1f}" '
                                  f'width="{pw:.1f}" height="{ph:.1f}" '
                                  f'fill="#FFFF00" stroke="#CCAA00" stroke-width="0.7"/>')
                    elif f[0] == "TEXT":
                        el.append(f'<text x="{bx:.1f}" y="{by - 8:.1f}" fill="white" '
                                  f'font-size="{max(7.0, S * 0.9):.1f}" '
                                  f'text-anchor="middle">{escape(f[10])}</text>')
        el.append("</svg>")
        return "\n".join(el)


class Html3dRenderer(Plugin[str]):
    """Interactive 3D: self-contained HTML page (WebGL, orbit/zoom, no CDN)
    with the board's glTF embedded. Double-click to open, drag to orbit."""
    kind, key = "renderer", "html3d"
    ext = ".3d.html"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from .geom3d import to_gltf
        from .view3d import page
        thick = _f(k.get("thick", 1.6))
        return page(board, to_gltf(board, thick))


class AllRenderer(Plugin[list[str]]):
    """Every mounted renderer → outdir/<name><ext>. A raising renderer
    is skipped with a warning (registry fences it). `keys=[…]` subsets."""
    kind, key = "renderer", "all"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        import os
        import subprocess
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        keys = k.get("keys")
        assert keys is None or isinstance(keys, list)
        os.makedirs(outdir, exist_ok=True)
        written: list[str] = []
        cands = list(keys) if keys is not None else board.plugins().list("renderer")
        for key in cands:
            if not isinstance(key, str) or key == "all":
                continue
            try:
                out = board.render(key)
            except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError,
                    subprocess.TimeoutExpired) as e:
                print(f"ocd: render {key} skipped: {e}", file=sys.stderr)
                continue
            plug = board.plugins().get("renderer", key)
            ext = str(getattr(plug, "ext", f".{key}"))
            mode = "w" if isinstance(out, str) else "wb"
            fn = os.path.join(outdir, board.name + ext)
            with open(fn, mode) as f:
                f.write(out)
            written.append(fn)
        return written


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
    """File importers (fp-line semantics): Board.add_footprint owns the
    shape + shadowing rules; this just casts and forwards."""
    from typing import cast
    from .types import Footprint
    board.add_footprint(name, cast(Footprint, meta), path)


class SymImporter(Plugin[dict[str, object]]):
    """Symbol importer: native .sym (custom schematic bodies)."""
    kind, key = "importer", "sym"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import symbol as _sym
        path = k.get("path", "")
        assert isinstance(path, str) and path
        name, meta = _sym.load_file(path)
        board.add_symbol(name, meta, path)
        return {"name": name}


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


class TomlConfig(Plugin[dict[str, object]]):
    """Project config: board.toml next to the .ocd. Keys map to existing
    knobs only (fab/placer/router/drc/mask/style); unknown keys are an
    error (a typo'd key silently doing nothing is worse). CLI flags win
    (they apply after); missing file → {} (builtins stand). Applied picks
    land in board.proj (+fab/meta); returns what was applied."""
    kind, key = "config", "toml"

    KEYS = ("fab", "placer", "router", "drc", "mask", "style")

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import os
        import tomllib
        base = k.get("base", os.getcwd())
        assert isinstance(base, str)
        fn = os.path.join(os.path.abspath(base), "board.toml")
        try:
            with open(fn, "rb") as f:
                cfg = tomllib.load(f)
        except FileNotFoundError:
            return {}
        for key in cfg:
            if key not in self.KEYS:
                raise ValueError(f"{fn}: unknown key {key!r} (have {list(self.KEYS)})")
        reg = board.plugins()
        from . import fab as _fab
        applied: dict[str, object] = {}
        if isinstance(cfg.get("fab"), str):
            if cfg["fab"] not in _fab.list_fabs():
                raise ValueError(f"{fn}: unknown fab {cfg['fab']!r} "
                                 f"(have {_fab.list_fabs()})")
            board.fab = cfg["fab"]
            applied["fab"] = cfg["fab"]
        for kind in ("placer", "router"):
            if isinstance(cfg.get(kind), str):
                if cfg[kind] not in reg.list(kind):
                    raise ValueError(f"{fn}: unknown {kind} {cfg[kind]!r} "
                                     f"(have {reg.list(kind)})")
                applied[kind] = cfg[kind]
        drc = cfg.get("drc")
        if isinstance(drc, list) and all(isinstance(x, str) for x in drc):
            bad = [x for x in drc if x not in reg.list("drc")]
            if bad:
                raise ValueError(f"{fn}: unknown drc {bad!r} "
                                 f"(have {reg.list('drc')})")
            applied["drc"] = list(drc)
        for key in ("mask", "style"):
            if isinstance(cfg.get(key), str):
                board.meta[key] = cfg[key]
                applied[key] = cfg[key]
        board.proj.update(applied)
        return applied


class CalcPlugin(Plugin[dict[str, object]]):
    """Embedded calculators: trace width/amps, via current, divider."""
    kind, key = "calc", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import calc as _calc
        what = str(k.get("what", "trace"))
        if what == "trace":
            return {"mm": _calc.trace_width(_f(k.get("amps", 1.0)),
                                            _f(k.get("rise", 10.0)),
                                            _f(k.get("oz", 1.0)))}
        if what == "amps":
            return {"amps": _calc.trace_amps(_f(k.get("mm", 0.3)),
                                             _f(k.get("rise", 10.0)),
                                             _f(k.get("oz", 1.0)))}
        if what == "via":
            return {"amps": _calc.via_amps(_f(k.get("drill", 0.3)))}
        if what == "divider":
            return {"vout": _calc.divider(_f(k.get("vin", 9.0)),
                                          _f(k.get("rtop", 10000.0)),
                                          _f(k.get("rbot", 4700.0)))}
        if what == "pick":
            return {"rtop": _calc.divider_pick(_f(k.get("vin", 9.0)),
                                               _f(k.get("vout", 5.0)),
                                               _f(k.get("rbot", 10000.0)))}
        raise ValueError(f"unknown calc {what!r} (trace|amps|via|divider|pick)")


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


class GatesPlugin(Plugin[dict[str, object]]):
    """Digital simulator: event-driven unit-delay gates (logic= attr).
    Same return shape as mna (nets + optional waves)."""
    kind, key = "simulate", "gates"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import gates as _gates
        ticks = k.pop("ticks", None)
        assert ticks is None or isinstance(ticks, int)
        return _gates.run(board, ticks, **k)


_DEFAULTS = (StdParts, DiffusionPlacer, CompactPlacer, ThermalPlacer,
             HierarchicalPlacer, MultilevelPlacer, TidyPlacer,
             GreedyLayers, LRouter, MazeRouter, CoarseRouter, WireMaskRouter,
             FabDrc, Erc, AllDrc,
             FlexDrc, JlcExporter, KicadExporter, KicadSchExporter,
             EagleExporter, EasyedaExporter,
             BundleExporter, OcdExporter, JsonExporter,
             RefSilk, FullSilk, FabSilk,
             FpImporter, KicadImporter, EagleImporter, EagleBoardImporter,
             TscircuitImporter, PcbImporter, EasyedaImporter, SymImporter,
             TomlConfig,
             CalcPlugin, SimPlugin, NgspicePlugin, GatesPlugin, LintPlugin, DoctorPlugin,
             ScorePlugin, DiffPlugin,
             SvgRenderer, SchRenderer, AssemblyRenderer, StlRenderer, GltfRenderer,
             PngRenderer, KicadRenderer, BlenderRenderer, PcbdrawRenderer,
             EasyedaRenderer, Html3dRenderer, AllRenderer)


def mount_defaults(board: Board) -> Registry:
    svc = board.ctx.get("plugins")
    assert isinstance(svc, Registry)
    reg: Registry = svc
    for cls in _DEFAULTS:
        if (cls.kind, cls.key) in reg.items:
            continue
        cls(f"{cls.kind}:{cls.key}").mount(board.ctx)
    return reg
