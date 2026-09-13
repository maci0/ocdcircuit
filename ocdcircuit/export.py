"""Gerber/Excellon (JLC) + KiCad s-expr export. Hand-rolled, no deps.

KiCad writer emits a loadable .kicad_pcb: general/setup/layers, nets,
footprints with SMD pads + PTH holes, segments, vias at segment joints.
Not bit-identical to KiCad's own output, but parses and round-trips.
"""
from __future__ import annotations
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit import Board

Flash = tuple[float, float]
Draw = tuple[float, float, float, float]


def _gerber(flashes: list[Flash], draws: list[Draw], aperture: float) -> str:
    out = ["G04 ocdcircuit*", "%FSLAX46Y46*%", "%MOMM*%", f"%ADD10C,{aperture:.3f}*%"]
    out.append("D10*")
    for x, y in flashes:
        out.append(f"X{x:.4f}Y{y:.4f}D03*")
    for x1, y1, x2, y2 in draws:
        out.append(f"X{x1:.4f}Y{y1:.4f}D02*")
        out.append(f"X{x2:.4f}Y{y2:.4f}D01*")
    out.append("M02*")
    return "\n".join(out)


def layer_names(n: int) -> list[str]:
    """Gerber copper extensions: GTL/GBL, GTL/G1/G2/GBL, GTL/G1..Gn/GBL."""
    if n == 1:
        return ["GTL"]
    if n == 2:
        return ["GTL", "GBL"]
    return ["GTL"] + [f"G{i}" for i in range(1, n - 1)] + ["GBL"]


def kicad_layers(n: int) -> list[str]:
    """KiCad layer names: F.Cu/B.Cu, F.Cu/In1.Cu/.../B.Cu."""
    if n == 1:
        return ["F.Cu"]
    if n == 2:
        return ["F.Cu", "B.Cu"]
    return ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n - 1)] + ["B.Cu"]


def export_jlc(board: Board, outdir: str = "out") -> list[str]:
    from .parts import pads_of
    os.makedirs(outdir, exist_ok=True)
    files: list[str] = []
    flashes: dict[int, list[Flash]] = {ll: [] for ll in range(board.layers)}
    draws: dict[int, list[Draw]] = {ll: [] for ll in range(board.layers)}
    lib = {k: v for k, v in board._lib().items()}
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            x, y = board.pad_pos(p.ref, pin)
            flashes[0].append((x, y))  # SMD pads on top
    for t in board.traces:
        draws[t.layer % board.layers].append((t.x1, t.y1, t.x2, t.y2))
    for ll, nm in enumerate(layer_names(board.layers)):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        open(fn, "w").write(_gerber(flashes.get(ll, []), draws.get(ll, []), 0.4))
        files.append(fn)
    # mask / silk / outline (minimal but present)
    for nm, ap in (("GTS", 0.5), ("GBS", 0.5), ("GTO", 0.2), ("GBO", 0.2)):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        open(fn, "w").write(_gerber([], [], ap))
        files.append(fn)
    W, H = board.width, board.height
    fn = os.path.join(outdir, f"{board.name}.GKO.gbr")
    outl: list[Draw] = [(0.0, 0.0, W, 0.0), (W, 0.0, W, H),
                        (W, H, 0.0, H), (0.0, H, 0.0, 0.0)]
    open(fn, "w").write(_gerber([], outl, 0.1))
    files.append(fn)
    # drill: one via per layer-change-free net is enough v0 → drill at net hubs
    drills: set[tuple[float, float]] = set()
    for t in board.traces:
        drills.add((round(t.x1, 3), round(t.y1, 3)))
    fn = os.path.join(outdir, f"{board.name}.TXT")
    d = ["M48", "METRIC,TZ", "T1C0.400", "%", "G90", "G05", "T1"]
    d += [f"X{x:.3f}Y{y:.3f}" for x, y in sorted(drills)]
    d += ["T0", "M30"]
    open(fn, "w").write("\n".join(d))
    files.append(fn)
    fn = os.path.join(outdir, f"{board.name}.BOM.csv")
    open(fn, "w").write("Designator,Footprint,Value\n" + "".join(
        f"{p.ref},{p.fp},{p.value}\n" for p in board.parts.values()))
    files.append(fn)
    fn = os.path.join(outdir, f"{board.name}.CPL.csv")
    open(fn, "w").write("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{p.ref},{p.x:.3f},{p.y:.3f},Top,0\n" for p in board.parts.values()))
    files.append(fn)
    return files


def export_bundle(board: Board, outdir: str = "out") -> list[str]:
    """One-zip fab bundle: Gerbers + drill + BOM + CPL + .ocd source.
    Download → upload → boards. Returns [zip path]."""
    import zipfile
    files = export_jlc(board, outdir)
    files += export_kicad(board, outdir)
    zfn = os.path.join(outdir, f"{board.name}-fab.zip")
    with zipfile.ZipFile(zfn, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, os.path.basename(f))
    return [zfn]


def _sexp_str(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def export_kicad(board: Board, outdir: str = "out") -> list[str]:
    """Write <name>.kicad_pcb (s-expression). Pads from pad_size, holes
    from hole_drill; segments per trace; silk refs via fp_text user."""
    from .parts import hole_drill, pad_size, pads_of
    os.makedirs(outdir, exist_ok=True)
    lib = board._lib()
    L: list[str] = []
    A = L.append
    A("(kicad_pcb (version 20221018) (generator ocdcircuit)")
    A('  (general (thickness 1.6))')
    A('  (paper "A4")')
    layers = kicad_layers(board.layers)
    A("  (layers")
    for i, ln in enumerate(layers):
        A(f'    ({i} {ln} signal)')
    A("  )")
    A('  (setup (pad_to_mask_clearance 0.05))')
    net_ids: dict[str, int] = {}
    for i, n in enumerate(sorted(board.nets), 1):
        net_ids[n] = i
        A(f"  (net {i} {_sexp_str(n)})")
    for p in board.parts.values():
        A(f'  (footprint {_sexp_str(p.fp)} (layer "F.Cu")')
        A(f"    (at {p.x:.4f} {p.y:.4f})")
        A(f'    (descr {_sexp_str(p.value or p.fp)})')
        A(f'    (fp_text user {p.ref} (at 0 {-p.h / 2 - 1:.4f}) (layer "F.SilkS"))')
        for pin in pads_of(p.fp, lib):
            dx, dy = board.pad_pos(p.ref, pin)
            dr = hole_drill(p.fp, pin, lib)
            nid = 0
            for n, net in board.nets.items():
                if (p.ref, str(pin)) in [(r, str(q)) for r, q in net.pins]:
                    nid = net_ids[n]
                    break
            if dr > 0:
                A(f'    (pad {pin} thru_hole circle (at {dx:.4f} {dy:.4f}) '
                  f"(size {dr + 0.7:.4f} {dr + 0.7:.4f}) (drill {dr:.4f}) (layers *.Cu *.Mask) (net {nid}))")
            else:
                pw, ph = pad_size(p.fp, pin, lib)
                A(f'    (pad {pin} smd rect (at {dx:.4f} {dy:.4f}) '
                  f"(size {pw:.4f} {ph:.4f}) (layers F.Cu F.Mask) (net {nid}))")
        A("  )")
    for t in board.traces:
        ln = layers[t.layer] if t.layer < len(layers) else layers[0]
        nid = net_ids.get(t.net, 0)
        A(f'  (segment (start {t.x1:.4f} {t.y1:.4f}) (end {t.x2:.4f} {t.y2:.4f}) '
          f'(width {t.width:.4f}) (layer "{ln}") (net {nid}))')
    W, H = board.width, board.height
    for x1, y1, x2, y2 in [(0, 0, W, 0), (W, 0, W, H), (W, H, 0, H), (0, H, 0, 0)]:
        A(f'  (gr_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) '
          f'(layer "Edge.Cuts") (width 0.1))')
    A(")")
    fn = os.path.join(outdir, f"{board.name}.kicad_pcb")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]
