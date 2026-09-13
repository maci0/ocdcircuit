"""JLCPCB export: RS-274X, Excellon, BOM, CPL. Hand-rolled, no deps."""
from __future__ import annotations
import os


def _gerber(shapes, aperture):
    out = ["G04 ocdcircuit*", "%FSLAX46Y46*%", "%MOMM*%", f"%ADD10C,{aperture:.3f}*%"]
    out.append("D10*")
    for kind, *g in shapes:
        if kind == "flash":
            x, y = g
            out.append(f"X{x:.4f}Y{y:.4f}D03*")
        elif kind == "draw":
            x1, y1, x2, y2 = g
            out.append(f"X{x1:.4f}Y{y1:.4f}D02*")
            out.append(f"X{x2:.4f}Y{y2:.4f}D01*")
    out.append("M02*")
    return "\n".join(out)


def export_jlc(board, outdir="out") -> list[str]:
    from .parts import pin_offset
    os.makedirs(outdir, exist_ok=True)
    files = []
    layers = {l: [] for l in range(board.layers)}
    for p in board.parts.values():
        for pin in pin_offset_fp(p.fp):
            x, y = board.pad_pos(p.ref, pin)
            layers[0].append(("flash", x, y))  # SMD pads on top
    for t in board.traces:
        layers[t.layer].append(("draw", t.x1, t.y1, t.x2, t.y2))
    names = ["GTL", "GBL"] if board.layers == 2 else [f"G{i}" for i in range(board.layers)]
    for l, nm in enumerate(names):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        open(fn, "w").write(_gerber(layers.get(l, []), 0.4))
        files.append(fn)
    # mask / silk / outline (minimal but present)
    for nm, ap in (("GTS", 0.5), ("GBS", 0.5), ("GTO", 0.2), ("GBO", 0.2)):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        open(fn, "w").write(_gerber([], ap))
        files.append(fn)
    W, H = board.width, board.height
    fn = os.path.join(outdir, f"{board.name}.GKO.gbr")
    open(fn, "w").write(_gerber([("draw", 0, 0, W, 0), ("draw", W, 0, W, H),
                                         ("draw", W, H, 0, H), ("draw", 0, H, 0, 0)], 0.1))
    files.append(fn)
    # drill: one via per layer-change-free net is enough v0 → drill at net hubs
    drills = set()
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


def pin_offset_fp(fp):
    from .parts import FOOTPRINTS
    return list(FOOTPRINTS[fp]["pins"].keys())
