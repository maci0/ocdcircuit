"""Everything is a plugin: placers, routers, layers, drc, exporters,
parts libraries, renderers (svg + 3D stl + x-ray), x-ray compare. Stdlib only, one file."""
from __future__ import annotations
from .util import as_float as _f, as_int as _i
import re
import sys
from typing import TYPE_CHECKING, cast


from .core import Plugin, Registry
from .parts import pin_offset as _std_pin_offset
from .types import Constraint, Footprint, Frame, PinLike, XY

if TYPE_CHECKING:
    from .circuit import Board


class StdParts(Plugin[dict[str, Footprint]]):
    kind, key = "parts", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, Footprint]:
        from .parts import FOOTPRINTS
        return FOOTPRINTS

    def pin_offset(self, fp: str, pin: PinLike, lib: object = None) -> XY:
        # Imports hoisted to module scope: this is called once per pin per
        # cost() (467k times in one 20-iter virgo placement) and the three
        # in-function imports cost ~0.9M importlib lookups. `cast` and
        # `Footprint` are already imported above; `parts` does not import
        # `plugins`, so the module-level import below is cycle-free.
        assert lib is None or isinstance(lib, dict)
        return _std_pin_offset(fp, pin, cast(dict[str, Footprint] | None, lib))


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
        # tracked temp constraint (undoable, idempotent inverse): a crash
        # or a pre-existing user grid can't leave a phantom or eat theirs.
        # unconstrain() removes one appended instance (identity match), so
        # only what this run added is withdrawn.
        c: dict[str, object] = {"t": "route-grid", "grid": coarse}
        board.constrain(c)
        try:
            return _maze.maze(board, frames=cast(list[Frame] | None, k.get("frames")))
        finally:
            board.unconstrain(c)


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
            vias = sum(1 for s in board.traces if s.via)
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
        overlap_n = 0
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
            oc = r.get("overlap_count")
            if isinstance(oc, int):
                overlap_n = max(overlap_n, oc)
            ran.append(key)
        out: dict[str, object] = {"errors": errors, "warnings": warnings, "ran": ran,
                                  "fab": board.fab}
        if overlap_n:
            out["overlap_count"] = overlap_n
        return out


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


class SchLibExporter(Plugin[list[str]]):
    """Native binary .SchLib (one storage per symbol, pins + designators).
    Round-trips through importer:schlib."""
    kind, key = "exporter", "schlib"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_schlib(board, outdir)


class KicadSchExporter(Plugin[list[str]]):
    """KiCad .kicad_sch: box symbols on the shared sch_layout grid, one
    wire per pin-to-rail drop, one global_label per net. ERC-clean."""
    kind, key = "exporter", "kicad-sch"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_kicad_sch(board, outdir)


class AltiumExporter(Plugin[list[str]]):
    """Altium ASCII (.PcbDocAscii |RECORD= lines — opens via Altium's
    P-CAD import path). Round-trips through importer:altium."""
    kind, key = "exporter", "altium"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_altium(board, outdir)


class PcadExporter(Plugin[list[str]]):
    """P-CAD ASCII (.pcb ACCEL_ASCII — Altium opens it natively).
    Round-trips through importer:pcb."""
    kind, key = "exporter", "pcad"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_pcad(board, outdir)


class BundleExporter(Plugin[list[str]]):
    """One-zip fab bundle: Gerbers + drill + BOM + CPL + KiCad. Upload-ready.
    cordis-boundary: file emission (outside-context by §6.1); withheld
    until export() is called, no inverse claimed."""
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
        from . import footprint as _fp
        from . import symbol as _sym
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        os.makedirs(outdir, exist_ok=True)
        text = agent.dumps(board)
        # in-memory customs (no src file) materialize as fp/sym sidecars so
        # the exported .ocd reloads; file-backed ones already have fp lines.
        # sidecar filenames are sanitized (names are foreign-controlled);
        # the headers keep raw names so parts still resolve.
        bare_fp = sorted(n for n in board.custom_fp if n not in board.fp_src)
        bare_sym = sorted(n for n in board.custom_sym if n not in board.sym_src)
        if bare_fp or bare_sym:
            import re
            lines = text.splitlines()
            for n in bare_fp:
                os.makedirs(os.path.join(outdir, "fp"), exist_ok=True)
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", n) or "X"
                fn = os.path.join(outdir, "fp", f"{safe}.fp")
                with open(fn, "w", encoding="utf-8") as f:
                    f.write(_fp.dumps(n, board.custom_fp[n]))
                lines.insert(1, f"fp fp/{safe}.fp")
            for n in bare_sym:
                os.makedirs(os.path.join(outdir, "sym"), exist_ok=True)
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", n) or "X"
                fn = os.path.join(outdir, "sym", f"{safe}.sym")
                with open(fn, "w", encoding="utf-8") as f:
                    f.write(_sym.dumps(n, board.custom_sym[n]))
                lines.insert(1, f"sym sym/{safe}.sym")
            text = "\n".join(lines) + "\n"
        fn = os.path.join(outdir, f"{board.name}.ocd")
        open(fn, "w", encoding="utf-8").write(text)
        return [fn]


class JsonExporter(Plugin[list[str]]):
    kind, key = "exporter", "json"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        import os
        import json
        from .agent import ir_of
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        os.makedirs(outdir, exist_ok=True)
        fn = os.path.join(outdir, f"{board.name}.json")
        open(fn, "w", encoding="utf-8").write(json.dumps(ir_of(board), indent=1))
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
            if t.via:
                continue
            c = layers[t.layer % len(layers)]
            el.append(f'<line x1="{t.x1 * S}" y1="{H - t.y1 * S}" x2="{t.x2 * S}" '
                      f'y2="{H - t.y2 * S}" stroke="{c}" stroke-width="{max(1.5, t.width * S)}" '
                      f'stroke-linecap="round"/>')
        vr, hr = 0.4 * S, 0.2 * S
        for t in board.traces:
            if not t.via:
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


def _cap(sym: dict[str, object], p: object) -> str:
    """Body caption: symbol `label` template ({ref} {value} {fp}), else ref."""
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
        from .sch import sch_layout
        theme = str(k.get("theme", "dark"))
        th = THEMES.get(theme, THEMES["dark"])
        layers = cast(list[str], th["layers"])
        lay = sch_layout(board)
        order, nets, px, rail_y = lay.order, lay.nets, lay.px, lay.rail_y
        top, W, H = lay.top, lay.W, lay.H
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
    3/4 product angle, key+fill suns, EEVEE. ~30-60s per board.
    cordis-boundary: child-process emission (outside-context by §6.1);
    withheld until run() is called, no inverse claimed."""
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
            open(src, "w", encoding="utf-8").write(to_gltf(board))
            open(script, "w", encoding="utf-8").write(self.SCRIPT)
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
    missing binary → RuntimeError naming the apt package).
    cordis-boundary: child-process + tempdir emission (outside-context
    by §6.1); withheld until run() is called, no inverse claimed. Exports the
    board to .kicad_pcb, renders, returns PNG bytes. Mask color follows
    `meta mask <color>` (green/red/blue/black/white/purple/yellow)."""
    kind, key = "renderer", "kicad"
    ext = ".ray.png"

    def run(self, board: Board, *a: object, **k: object) -> bytes:
        import os
        import shutil
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
            src = os.path.join(tmp, board.name + ".kicad_pcb")
            out = os.path.join(tmp, board.name + ".png")
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
        from . import envcfg
        cfg = envcfg.kicad_3d_viewer_cfg()
        try:
            d = json.load(open(cfg, encoding="utf-8")) if cfg else None
        except (OSError, ValueError):
            d = None
        if d is None:
            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
            return
        presets = d.get("layer_presets", [])
        if not presets:
            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
            return
        assert cfg is not None
        saved = json.dumps(presets[0].get("colors", []))
        try:
            for c in presets[0].get("colors", []):
                if c.get("layer") in ("soldermask_top", "soldermask_bottom"):
                    r, g, b = mask
                    c["color"] = f"rgba({r}, {g}, {b}, 0.831)"
            json.dump(d, open(cfg, "w", encoding="utf-8"), indent=2)
            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
        finally:
            d["layer_presets"][0]["colors"] = json.loads(saved)
            json.dump(d, open(cfg, "w", encoding="utf-8"), indent=2)
        if not os.path.isfile(out):
            raise RuntimeError("kicad-cli did not produce a rendered image")


class PcbdrawRenderer(Plugin[str]):
    """Stylized fabrication drawing via pcbdraw (needs `pip install pcbdraw`;
    missing → RuntimeError).
    cordis-boundary: child-process + tempdir emission (outside-context
    by §6.1); withheld until run() is called, no inverse claimed.
    Exports .kicad_pcb, plots styled SVG.
    Style follows `meta style <name>` (default jlcpcb-green-enig)."""
    kind, key = "renderer", "pcbdraw"
    ext = ".fab.svg"

    def run(self, board: Board, *a: object, **k: object) -> str:
        import os
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
            src = os.path.join(tmp, board.name + ".kicad_pcb")
            out = os.path.join(tmp, board.name + ".svg")
            subprocess.run([exe, "plot", "-s", style, "--side", side,
                            "--silent", src, out],
                           capture_output=True, check=True, timeout=300)
            return open(out, encoding="utf-8").read()


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
            doc = json.load(open(fn, encoding="utf-8"))
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


class XrayRenderer(Plugin[str]):
    """X-ray reference: every copper layer stacked on black, no mask/bodies.
    What the fab scan should look like — the baseline `xray compare` diffs."""
    kind, key = "renderer", "xray"
    ext = ".xray.svg"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from . import xray as _xray
        return _xray.render(board, _f(k.get("scale", 10.0)))


class XrayCompare(Plugin[dict[str, object]]):
    """Fab x-ray vs design: upload the fab's PNG (`png=` path or bytes),
    get {score 0-100, divs, svg, overlay}. dx/dy/scale re-register a scan
    that doesn't sit on the grid (real scans never do); thr sets the
    copper cutoff."""
    kind, key = "xray", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import os
        from . import xray as _xray
        from .util import as_int as _ii
        raw = k.get("png", k.get("raw"))
        assert isinstance(raw, (str, bytes)), "xray needs png=<path or PNG bytes>"
        if (isinstance(raw, str) and len(raw) < 1024
                and ("/" in raw or "\\" in raw or os.path.isfile(raw))):
            with open(raw, "rb") as f:  # path → bytes (fixable: missing = OSError)
                raw = f.read()
        assert isinstance(raw, bytes), "xray needs png=<path or PNG bytes>"
        mc = k.get("min_cells")
        md = k.get("max_divs")
        assert mc is None or isinstance(mc, int)
        assert md is None or isinstance(md, int)
        return _xray.compare(board, raw, _f(k.get("pxmm", 10.0)),
                             _ii(k.get("thr"), 100),
                             _f(k.get("dx", 0.0)), _f(k.get("dy", 0.0)),
                             _f(k.get("scale", 1.0)),
                             mc if mc is not None else 3,
                             md if md is not None else 50)


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
            fn = os.path.join(outdir, board.name + ext)
            if isinstance(out, str):
                with open(fn, "w", encoding="utf-8") as f:
                    f.write(out)
            elif isinstance(out, (bytes, bytearray)):
                with open(fn, "wb") as f:
                    f.write(out)
            else:
                print(f"ocd: render {key} skipped: unexpected {type(out).__name__}",
                      file=sys.stderr)
                continue
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


def _import_path(k: dict[str, object]) -> str:
    path = k.get("path", "")
    assert isinstance(path, str) and path
    return path


def _import_foreign_fps(board: Board, path: str) -> dict[str, object]:
    """KiCad/Eagle/etc.: load_foreign sniffs the file; both plugin keys share
    this body so format-named dispatch stays without a duplicated loop."""
    from .foreign import load_foreign
    names = []
    for name, meta in load_foreign(path):
        _guarded_add(board, name, meta, path)
        names.append(name)
    return {"names": names}


class SymImporter(Plugin[dict[str, object]]):
    """Symbol importer: native .sym (custom schematic bodies)."""
    kind, key = "importer", "sym"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import symbol as _sym
        path = _import_path(k)
        name, meta = _sym.load_file(path)
        board.add_symbol(name, meta, path)
        return {"name": name}


class SchLibImporter(Plugin[dict[str, object]]):
    """Symbol importer: native binary .SchLib (all symbols, pin names +
    designators; sides by designator-half split)."""
    kind, key = "importer", "schlib"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import _bin_schlib
        path = _import_path(k)
        with open(path, "rb") as f:
            pairs = _bin_schlib(f.read())
        names = []
        for name, meta in pairs:
            if name not in board.custom_sym:
                board.add_symbol(name, meta, path)
            names.append(name)
        return {"names": names}


class FpImporter(Plugin[dict[str, object]]):
    """Footprint importer: native .fp (re-exported for plugin listing)."""
    kind, key = "importer", "fp"
    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .footprint import load_file
        path = _import_path(k)
        name, meta = load_file(path)
        _guarded_add(board, name, meta, path)
        return {"name": name}


class KicadImporter(Plugin[dict[str, object]]):
    """Footprint importer: KiCad .kicad_mod/.pretty (pads, holes, models)."""
    kind, key = "importer", "kicad"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        return _import_foreign_fps(board, _import_path(k))


class EagleImporter(Plugin[dict[str, object]]):
    """Footprint importer: Eagle .lbr (all packages)."""
    kind, key = "importer", "eagle"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        return _import_foreign_fps(board, _import_path(k))


class TscircuitImporter(Plugin[dict[str, object]]):
    """tscircuit Circuit-JSON pad soups; EasyEDA Std JSON (sniffed) → board."""
    kind, key = "importer", "tscircuit"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import json
        from .foreign import easyeda_doc
        path = _import_path(k)
        with open(path, encoding="utf-8") as f:
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
        return _import_foreign_fps(board, path)


class PcbImporter(Plugin[dict[str, object]]):
    """Board importer: .kicad_pcb/.kicad_sch (sniffed), Eagle .brd,
    P-CAD .pcb, or Altium ASCII → parts/nets."""
    kind, key = "importer", "pcb"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import altium_ascii, eagle_brd, kicad_pcb_netlist, kicad_sch_netlist, pcad_ascii
        path = _import_path(k)
        with open(path, encoding="latin-1") as f:
            text = f.read()
        s = text.lstrip()
        if s.startswith("<eagle"):
            ir = eagle_brd(text)
        elif s.startswith("(ACCEL_ASCII") or s.startswith("ACCEL_ASCII"):
            ir = pcad_ascii(text)
        elif "|RECORD=" in s.upper():
            ir = altium_ascii(text)
        elif s.startswith("(kicad_sch"):
            ir = kicad_sch_netlist(text)
        else:
            ir = kicad_pcb_netlist(text)
        return _board_ir_into(board, ir)


class AltiumImporter(Plugin[dict[str, object]]):
    """Board importer: native binary .PcbDoc (OLE: param streams +
    Tracks/Arcs/Vias/Pads/Fills + Polygons6 pours), Altium ASCII export
    (|RECORD= lines), or P-CAD .pcb — sniffed, onto THIS board."""
    kind, key = "importer", "altium"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import _bin_pcbdoc, _OLE_MAGIC, altium_ascii, pcad_ascii
        path = _import_path(k)
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:8] == _OLE_MAGIC:
            return _board_ir_into(board, _bin_pcbdoc(raw))
        text = raw.decode("latin-1")
        s = text.lstrip()
        ir = (pcad_ascii(text) if s.startswith("(ACCEL_ASCII")
              or s.startswith("ACCEL_ASCII") else altium_ascii(text))
        return _board_ir_into(board, ir)


class AltiumSchImporter(Plugin[dict[str, object]]):
    """Schematic importer: native binary .SchDoc (components + wires +
    netlabels/powerports → parts/nets) onto THIS board. Repeat per sheet:
    shared netlabels merge, colliding refs get a sheet-stem prefix,
    sheet-local N1..Nn auto-nets stay separate. Placement is schematic,
    not physical — run a placer after import."""
    kind, key = "importer", "altium-sch"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import os
        from .foreign import _bin_schdoc, _OLE_MAGIC
        path = _import_path(k)
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:8] != _OLE_MAGIC:
            raise ValueError("not a binary .SchDoc (OLE); "
                             "native schematic import needs the .SchDoc file")
        stem = os.path.splitext(os.path.basename(path))[0]
        return _board_ir_into(board, _bin_schdoc(raw, sheet=stem))


class EagleBoardImporter(Plugin[dict[str, object]]):
    """Board importer: Eagle .brd (elements + signals) onto THIS board."""
    kind, key = "importer", "eagle-brd"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from .foreign import eagle_brd
        path = _import_path(k)
        with open(path, encoding="utf-8") as f:
            ir = eagle_brd(f.read())
        return _board_ir_into(board, ir)


class EasyedaImporter(Plugin[dict[str, object]]):
    """Board importer: EasyEDA Std JSON — schematic (docType 1), PCB
    (docType 3), or footprint (docType 4), sniffed by head. Schematic
    pin dots take ox/oy (sheet px) when stored origin-relative."""
    kind, key = "importer", "easyeda"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import json
        from .foreign import easyeda_doc, easyeda_sch
        path = _import_path(k)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        assert isinstance(doc, dict)
        if str(doc.get("head", "")).split("~")[0] == "1":
            return _board_ir_into(board, easyeda_sch(
                doc, _f(k.get("ox", 0.0)), _f(k.get("oy", 0.0))))
        out = easyeda_doc(doc)
        if isinstance(out, list):  # footprint doc → fp import
            for name, meta in out:
                _guarded_add(board, name, meta, path)
            return {"names": [n for n, _ in out]}
        assert isinstance(out, dict)
        return _board_ir_into(board, out)


def _board_ir_into(board: Board, ir: dict[str, object]) -> dict[str, object]:
    """IR (from_ir already loaded _imported_fp) → footprints + parts + nets.
    Imported copper (`_imported_traces`: tracks as Seg, vias as (x, y,
    drill) net markers) lands as fixed traces — re-route overwrites them."""
    from .agent import from_ir
    from .circuit import Seg
    nb = from_ir(ir)
    for fn, meta in nb.custom_fp.items():
        if fn not in board._lib():
            board.add_footprint(fn, meta)
    # multi-sheet merge: IR may carry board.sheet (SchDoc filename stem).
    # Shared netlabels (GND…) merge by name; colliding refs get a sheet
    # prefix; sheet-local N1..Nn auto-nets get one too (never merge).
    sheet = str(cast(dict[str, object], ir.get("board", {})).get("sheet", ""))
    prefix = (sheet + "_") if sheet else ""
    ren: dict[str, str] = {}
    for ref in nb.parts:
        ren[ref] = (prefix + ref) if prefix and ref in board.parts else ref
    for ref, p in nb.parts.items():
        board.add_part(ren[ref], p.fp, p.value, p.x, p.y,
                       attrs=dict(p.attrs) or None)
    for n, net in nb.nets.items():
        if prefix and n.startswith("N") and n[1:].isdigit():
            n = prefix + n
        for ref, pin in net.pins:
            board.connect(n, ren.get(ref, ref), pin)
    binfo = cast(dict[str, object], ir.get("board", {}))
    ncu = binfo.get("layers", 2)
    assert isinstance(ncu, int)
    iw, ih = binfo.get("w", board.width), binfo.get("h", board.height)
    assert isinstance(iw, (int, float)) and isinstance(ih, (int, float))
    if abs(iw - board.width) > 1e-9 or abs(ih - board.height) > 1e-9:
        board.set_board(float(iw), float(ih))
    if ncu > board.layers:
        # layers is a validating property, not an effect: route through
        # Board.layers only via a recorded undo (loaders own no fiber).
        old_layers = board.layers

        def _do_layers() -> None:
            board.layers = ncu

        def _undo_layers() -> None:
            board.layers = old_layers

        board.emit(_do_layers, _undo_layers)
    segs: list[Seg] = []
    for t in cast(list[dict[str, object]], ir.get("_imported_traces", [])):
        if t.get("via"):
            x, y = float(cast(float, t["x"])), float(cast(float, t["y"]))
            seg = Seg(str(t.get("net", "")), x, y, x, y, 0, 0.8)
            seg.via = True
            seg.drill = float(cast(float, t.get("drill", 0.4)))
            segs.append(seg)
        else:
            lay = t.get("layer", 0)
            assert isinstance(lay, int)
            segs.append(Seg(str(t.get("net", "")),
                            float(cast(float, t["x1"])), float(cast(float, t["y1"])),
                            float(cast(float, t["x2"])), float(cast(float, t["y2"])),
                            min(lay, board.layers - 1),
                            float(cast(float, t.get("width", 0.3)))))
    ntr = len(segs)
    if segs:
        old = list(board.traces)
        board.emit(lambda: board.traces.extend(segs),
                   lambda: board.traces.__setitem__(slice(None), old))
    npour = 0
    for c in cast(list[dict[str, object]], ir.get("constraints", [])):
        if c.get("t") == "pour" and all(q.get("net") != c.get("net") or
                                         q.get("layer") != c.get("layer")
                                         for q in board.constraints):
            board.constrain({"t": "pour", "net": str(c["net"]),
                             "layer": int(cast(int, c["layer"]))})
            npour += 1
    nother = 0
    for c in cast(list[dict[str, object]], ir.get("constraints", [])):
        if c.get("t") in ("cutout", "keepout", "class", "diff", "match"):
            cc: Constraint = {"t": str(c["t"])}
            for k in ("net", "name", "p", "n", "x", "y", "w", "h", "d",
                      "layer", "gap", "nets", "layers"):
                if k in c:
                    v = c[k]
                    cc[k] = (list(cast(list[object], v)) if isinstance(v, list)
                             else float(cast(float, v)) if isinstance(v, (int, float))
                             else str(v) if isinstance(v, str) else v)
            board.constrain(cc)
            nother += 1
    ntx = 0
    new_comments = [f"{t.get('text', '')} @ {t.get('x', 0)}, {t.get('y', 0)}"
                    for t in cast(list[dict[str, object]], ir.get("_imported_texts", []))]
    if new_comments:
        old_comments = list(board.comments)
        board.emit(lambda: board.comments.extend(new_comments),
                   lambda: board.comments.__setitem__(slice(None), old_comments))
        ntx = len(new_comments)
    out: dict[str, object] = {"parts": len(nb.parts), "nets": len(nb.nets)}
    if ntr:
        out["traces"] = ntr
    if npour:
        out["pours"] = npour
    if nother:
        out["constraints"] = nother
    if ntx:
        out["texts"] = ntx
    skip = ir.get("_skipped", [])
    assert isinstance(skip, list)
    if skip:
        out["skipped"] = list(skip)
    return out


class EasyedaExporter(Plugin[list[str]]):
    """EasyEDA Std PCB JSON (opens in EasyEDA/JLCEDA import)."""
    kind, key = "exporter", "easyeda"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_easyeda(board, outdir)


class EasyedaSchExporter(Plugin[list[str]]):
    """EasyEDA Std schematic JSON (docType 1). Round-trips through
    importer:easyeda (docType sniffed)."""
    kind, key = "exporter", "easyeda-sch"

    def run(self, board: Board, *a: object, **k: object) -> list[str]:
        from . import export
        outdir = k.get("outdir", "out")
        assert isinstance(outdir, str)
        return export.export_easyeda_sch(board, outdir)


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


class PcbScanPlugin(Plugin[dict[str, object]]):
    """Reverse-engineer a physical board from photos: stitch N handheld
    shots per side, enhance for markings/copper/edges, recover standoff by
    parallax, bake a 3D gaussian splat, then let a vision model read the
    lot and emit a draft .ocd.

    scan(photos=[...] | {'top': [...], 'bottom': [...]}, outdir=..,
         board_mm=<known board width in mm>, note=.., docs=[manual.pdf],
         answers={question: reply}, llm=False)

    note/docs are what the owner knows (a description, a manual, a
    datasheet); the model may ask questions back, returned under
    `questions`, and answers= feeds them to a better-informed second pass.
    llm=False stops after the deterministic artifacts (no endpoint needed).
    Needs numpy; Pillow only for non-PNG photos.
    cordis-boundary: file reads/writes + HTTP are outside-context
    emissions (§6.1), withheld until run()."""
    kind, key = "scan", "photo"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import pcbscan as _scan
        photos = k.get("photos", k.get("paths"))
        assert isinstance(photos, (list, dict)), (
            "scan needs photos=[path, ...] or "
            "photos={'top': [...], 'bottom': [...]}")
        mm = k.get("board_mm")
        assert mm is None or isinstance(mm, (int, float, str))
        outdir = k.get("outdir")
        assert outdir is None or isinstance(outdir, str)
        note = k.get("note", "")
        assert isinstance(note, str)
        docs = k.get("docs")
        assert docs is None or isinstance(docs, list), (
            "scan docs= wants a list of manual/datasheet paths")
        answers = k.get("answers")
        assert answers is None or isinstance(answers, dict), (
            "scan answers= wants {question: reply}")
        return _scan.reverse(
            cast("list[str] | dict[str, list[str]]", photos),
            outdir or f"{board.name}-scan",
            board_mm=(_f(mm, 0.0) or None), note=note,
            zoom=_i(k.get("zoom"), 2), maxdim=_i(k.get("maxdim"), 1600),
            tall_mm=_f(k.get("tall_mm"), 5.0),
            docs=cast("list[str] | None", docs),
            answers=cast("dict[str, str] | None", answers),
            llm_analysis=bool(k.get("llm", True)))


class DiffPlugin(Plugin[str]):
    """Board diff vs another board."""
    kind, key = "diff", "std"

    def run(self, board: Board, *a: object, **k: object) -> str:
        from . import diff as _diff
        from .circuit import Board as _B
        other = k.get("other")
        assert isinstance(other, _B)
        return _diff.diff(board, other)


class CollabPlugin(Plugin[dict[str, object]]):
    """Realtime ops for a shared board: collab(op={ops:[...]}) validates
    and applies one op (agent.apply_patch — structured edits only, undoable
    like everything). The room (SSE + presence) lives in collab.py; this is
    the plugin-architecture face of it: swap keys to change merge policy."""
    kind, key = "collab", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import collab as _collab
        op = k.get("op", k.get("ops", {}))
        assert isinstance(op, dict), "collab wants op={ops: [...]}"
        return _collab.apply_op(board, op)


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
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{fn}: invalid TOML: {e}") from e
        except OSError as e:
            raise ValueError(f"{fn}: cannot read: {e}") from e
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
        import math
        from . import calc as _calc

        def _pos(v: object, name: str) -> float:
            # physics inputs must be positive: negatives give complex
            # widths, zeros divide by zero (and fence the plugin — a mere
            # typo must stay a clean ValueError, never a fence trip).
            f = _f(v)
            if not math.isfinite(f) or f <= 0:
                raise ValueError(f"calc {name} must be positive (got {v!r})")
            return f

        what = str(k.get("what", "trace"))
        if what == "trace":
            return {"mm": _calc.trace_width(_pos(k.get("amps", 1.0), "amps"),
                                            _pos(k.get("rise", 10.0), "rise"),
                                            _pos(k.get("oz", 1.0), "oz"))}
        if what == "amps":
            return {"amps": _calc.trace_amps(_pos(k.get("mm", 0.3), "mm"),
                                             _pos(k.get("rise", 10.0), "rise"),
                                             _pos(k.get("oz", 1.0), "oz"))}
        if what == "via":
            return {"amps": _calc.via_amps(_pos(k.get("drill", 0.3), "drill"))}
        if what == "divider":
            return {"vout": _calc.divider(_f(k.get("vin", 9.0)),
                                          _pos(k.get("rtop", 10000.0), "rtop"),
                                          _pos(k.get("rbot", 4700.0), "rbot"))}
        if what == "pick":
            _vo = _f(k.get("vout", 5.0))
            if not math.isfinite(_vo) or _vo == 0:
                raise ValueError(f"calc vout must be nonzero (got {_vo!r})")
            return {"rtop": _calc.divider_pick(_f(k.get("vin", 9.0)),
                                               _vo,
                                               _pos(k.get("rbot", 10000.0), "rbot"))}
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
    Same return shape as mna. Missing binary → RuntimeError (use mna).
    cordis-boundary: child-process + cir-file emission (outside-context
    by §6.1); withheld until run() is called, no inverse claimed."""
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


class QuotePlugin(Plugin[dict[str, object]]):
    """Fab price comparison: bare PCB per fab + JLC assembly with parts.
    Estimates from published proto pricing (not quotes); qty=N sets boards."""
    kind, key = "quote", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        from . import quote as _quote
        from .util import as_int as _ii
        qty = _ii(k.get("qty"), 5)
        fabs = k.get("fabs", k.get("fab"))
        if isinstance(fabs, str):
            fabs = [fabs]
        assert fabs is None or isinstance(fabs, list)
        no_parts = k.get("no_parts", k.get("bare", False))
        assert isinstance(no_parts, bool)
        return _quote.compare(board, qty, fabs, not no_parts)


class StdPrice(Plugin[dict[str, object]]):
    """Unit-price provider: answers {price, source} per part ref from the
    board's own data. `price=` attr when set; else the offline JLC SQLite
    (populated via the KiCad MCP download) when present; else unpriced.
    `quote` prefers this provider, then knoll live, then unpriced."""
    kind, key = "price", "std"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import math
        ref = k.get("ref", "")
        assert isinstance(ref, str) and ref
        raw = board.parts[ref].attrs.get("price")  # KeyError = unknown ref
        if isinstance(raw, bool):
            return {"price": None, "source": "unpriced"}
        if isinstance(raw, (int, float)):
            v = float(raw)
            if math.isfinite(v) and v >= 0:
                return {"price": v, "source": "manual"}
            raise ValueError(f"price {raw!r} on {ref} must be ≥0")
        if isinstance(raw, str) and raw.strip():
            try:
                v = float(raw.strip().lstrip("$"))
            except ValueError:
                raise ValueError(f"price {raw!r} on {ref} is not a number")
            if math.isfinite(v) and v >= 0:
                return {"price": v, "source": "manual"}
            raise ValueError(f"price {raw!r} on {ref} must be ≥0")
        lcsc = k.get("lcsc", "")
        assert isinstance(lcsc, str)
        off = _price_offline(lcsc)
        if off is not None:
            return {"price": off, "source": "offline"}
        return {"price": None, "source": "unpriced"}


def _price_offline(lcsc: str) -> float | None:
    """One unit price from the offline JLC SQLite, else None. Read-only open;
    missing/corrupt/empty DB is not an error — the caller falls through."""
    import math
    import os
    import sqlite3
    from . import envcfg
    db = envcfg.jlc_offline_db()
    if not lcsc or not db or not os.path.isfile(db):
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT price_json FROM components WHERE lcsc=?",
                              (lcsc.lstrip("Cc"),)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        import json
        br = json.loads(row[0] or "[]")
        v = float(br[0].get("price"))
    except (ValueError, TypeError, IndexError, AttributeError, KeyError):
        return None
    return v if math.isfinite(v) and v >= 0 else None


class KnollPrice(Plugin[dict[str, object]]):
    """Unit-price provider: knoll's live JLC lookup (LCSC exact, MPN exact).
    Needs network + knoll's checkout (KNOLL_SRC or ~/Desktop/knoll/src);
    unreachable/absent → unpriced, never an error. Loaded via importlib spec
    so knoll stays an undeclared checkout, not a dependency and never on
    sys.path. cordis-boundary: network emission, withheld until run()."""
    kind, key = "price", "knoll"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import math
        lcsc = k.get("lcsc", "")
        mpn = k.get("mpn", "")
        assert isinstance(lcsc, str) and isinstance(mpn, str)
        v, src = _knoll_price(lcsc, mpn)
        if v is None:
            return {"price": None, "source": "unpriced"}
        assert math.isfinite(v) and v >= 0
        return {"price": v, "source": src}


def _knoll_price(lcsc: str, mpn: str) -> tuple[float | None, str]:
    import importlib.util
    import math
    import os
    from . import envcfg
    cands = [envcfg.knoll_src(), os.path.expanduser("~/Desktop/knoll/src")]
    for cand in cands:
        if not cand:
            continue
        mod = os.path.join(cand, "knoll", "stock.py")
        if not os.path.isfile(mod):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_knoll_stock", mod)
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            r = m.lookup_jlc(lcsc, mpn)
        except Exception:
            return None, "unpriced"
        if not isinstance(r, dict) or r.get("price") is None:
            return None, "unpriced"
        pv = r["price"]
        assert isinstance(pv, (int, float, str))
        try:
            v = float(pv)
            if not math.isfinite(v) or v < 0:
                return None, "unpriced"
        except (TypeError, ValueError):
            return None, "unpriced"
        return v, "jlc-live"
    return None, "unpriced"


class JlcApiPrice(Plugin[dict[str, object]]):
    """Unit-price provider: official JLCPCB parts API (HMAC-SHA256 authed).
    Creds from env (JLCPCB_APP_ID/KEY/SECRET) or ~/.secrets/jlcpcb — never
    committed, never logged. Unapproved/missing creds → unpriced (the API
    returns 401 until JLC approves the app); never an error. cordis-boundary:
    network emission, withheld until run()."""
    kind, key = "price", "jlc-api"

    def run(self, board: Board, *a: object, **k: object) -> dict[str, object]:
        import math
        lcsc = k.get("lcsc", "")
        assert isinstance(lcsc, str)
        v = _jlc_api_price(lcsc)
        if v is None:
            return {"price": None, "source": "unpriced"}
        assert math.isfinite(v) and v >= 0
        return {"price": v, "source": "jlc-api"}


def _jlc_api_creds() -> tuple[str, str, str] | None:
    """(app_id, access_key, secret) from env or ~/.secrets/jlcpcb, else None.
    The secrets file holds access/secret lines; the app id rides alongside
    (env JLCPCB_APP_ID or the file's AppID line when present)."""
    import os
    from . import envcfg
    app, acc, sec = envcfg.jlc_env_creds()
    if acc and sec:
        return (app, acc, sec)
    try:
        lines = open(os.path.expanduser("~/.secrets/jlcpcb"),
                     encoding="utf-8").read().splitlines()
    except OSError:
        return None
    vals: dict[str, str] = {}
    for ln in lines:
        if ":" in ln:
            k, v = ln.split(":", 1)
            vals[k.strip().lower()] = v.strip()
    acc = vals.get("accesskey", "")
    sec = vals.get("secretkey", "")
    app = app or vals.get("appid", "")
    if acc and sec:
        return (app, acc, sec)
    return None


def _jlc_api_price(lcsc: str) -> float | None:
    """One unit price via the official parts API, else None. Any failure —
    no creds, 401 (app still in review), network, bad shape — is unpriced."""
    import base64
    import hashlib
    import hmac
    import json
    import math
    import secrets
    import time
    import urllib.error
    import urllib.request
    creds = _jlc_api_creds()
    if not creds or not lcsc:
        return None
    app_id, access, secret = creds
    if not app_id:
        return None
    body = json.dumps({}, separators=(",", ":"))
    nonce = "".join(secrets.choice(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        for _ in range(32))
    ts = int(time.time())
    sig = base64.b64encode(hmac.new(
        secret.encode(),
        f"POST\n/component/getComponentInfos\n{ts}\n{nonce}\n{body}\n".encode(),
        hashlib.sha256).digest()).decode()
    req = urllib.request.Request(
        "https://jlcpcb.com/external/component/getComponentInfos",
        data=body.encode(),
        headers={"Authorization": f'JOP appid="{app_id}",accesskey="{access}",'
                 f'nonce="{nonce}",timestamp="{ts}",signature="{sig}"',
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read(8 << 20))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    # untrusted body: tolerate any shape; find our LCSC in the page
    try:
        comps = data.get("data", {}).get("list", [])
        if not isinstance(comps, list):
            return None
        want = lcsc.lstrip("Cc").upper()
        for c in comps:
            if not isinstance(c, dict):
                continue
            num = str(c.get("componentCode", "") or c.get("lcsc", "")).lstrip("Cc").upper()
            if num != want:
                continue
            for key in ("price", "unitPrice", "priceList"):
                pv = c.get(key)
                if isinstance(pv, (int, float)) and math.isfinite(pv) and pv >= 0:
                    return float(pv)
                if isinstance(pv, list) and pv and isinstance(pv[0], dict):
                    pv0 = pv[0].get("price")
                    if isinstance(pv0, (int, float)) and math.isfinite(pv0) and pv0 >= 0:
                        return float(pv0)
    except (AttributeError, TypeError):
        return None
    return None


_DEFAULTS = (StdParts, DiffusionPlacer, CompactPlacer, ThermalPlacer,
             HierarchicalPlacer, MultilevelPlacer, TidyPlacer,
             GreedyLayers, LRouter, MazeRouter, CoarseRouter, WireMaskRouter,
             FabDrc, Erc, AllDrc,
             FlexDrc, JlcExporter, KicadExporter, KicadSchExporter,
             EagleExporter, EasyedaExporter, EasyedaSchExporter,
             AltiumExporter, PcadExporter,
             SchLibExporter,
             BundleExporter, OcdExporter, JsonExporter,
             RefSilk, FullSilk, FabSilk,
             FpImporter, KicadImporter, EagleImporter, EagleBoardImporter,
             TscircuitImporter, PcbImporter, EasyedaImporter, AltiumImporter,
             AltiumSchImporter,
             SymImporter, SchLibImporter,
             TomlConfig,
             CalcPlugin, SimPlugin, NgspicePlugin, GatesPlugin, LintPlugin, DoctorPlugin,
             ScorePlugin, DiffPlugin, CollabPlugin, XrayCompare, PcbScanPlugin, QuotePlugin,
             StdPrice, KnollPrice, JlcApiPrice,
             SvgRenderer, SchRenderer, AssemblyRenderer, StlRenderer, GltfRenderer,
             PngRenderer, KicadRenderer, BlenderRenderer, PcbdrawRenderer,
             EasyedaRenderer, Html3dRenderer, XrayRenderer, AllRenderer)


def mount_defaults(board: Board) -> Registry:
    svc = board.ctx.get("plugins")
    assert isinstance(svc, Registry)
    reg: Registry = svc
    for cls in _DEFAULTS:
        if (cls.kind, cls.key) in reg.items:
            continue
        cls(f"{cls.kind}:{cls.key}").mount(board.ctx)
    return reg
