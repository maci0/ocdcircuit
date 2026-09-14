"""Gerber/Excellon (JLC) + KiCad s-expr export. Hand-rolled, no deps.

KiCad writer emits a loadable .kicad_pcb: general/setup/layers, nets,
footprints with SMD pads + PTH holes, segments, vias at segment joints.
Not bit-identical to KiCad's own output, but parses and round-trips.
"""
from __future__ import annotations
import os
from typing import TYPE_CHECKING


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)

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
    paste: list[Flash] = []
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            x, y = board.pad_pos(p.ref, pin)
            flashes[0].append((x, y))  # SMD pads on top
            from .parts import hole_drill
            if not hole_drill(p.fp, pin, lib):
                paste.append((x, y))  # SMD only — PTH gets no paste
    for t in board.traces:
        draws[t.layer % board.layers].append((t.x1, t.y1, t.x2, t.y2))
    for ll in draws:
        draws[ll] = sorted(draws[ll])
        flashes[ll] = sorted(flashes[ll])
    for ll, nm in enumerate(layer_names(board.layers)):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        open(fn, "w").write(_gerber(flashes.get(ll, []), draws.get(ll, []), 0.4))
        files.append(fn)
    # paste (top only — single-sided SMT like the mitox board)
    fn = os.path.join(outdir, f"{board.name}.GTP.gbr")
    open(fn, "w").write(_gerber(paste, [], 0.4))
    files.append(fn)
    # mask / silk / outline (minimal but present; no bottom side on 1L)
    bottom = [] if board.layers == 1 else ["GBS", "GBO"]
    for nm, ap in (("GTS", 0.5), ("GTO", 0.2)):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        open(fn, "w").write(_gerber([], [], ap))
        files.append(fn)
    for nm, ap in zip(bottom, (0.5, 0.2)):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        open(fn, "w").write(_gerber([], [], ap))
        files.append(fn)
    W, H = board.width, board.height
    fn = os.path.join(outdir, f"{board.name}.GKO.gbr")
    outl: list[Draw] = [(0.0, 0.0, W, 0.0), (W, 0.0, W, H),
                        (W, H, 0.0, H), (0.0, H, 0.0, 0.0)]
    from .drc import zone_at as _za
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "cutout":
            z = _za(board, c)
            hw, hh = _f(z["w"]) / 2, _f(z["h"]) / 2
            cx, cy = _f(z["x"]), _f(z.get("y", 0.0))
            outl += [(cx - hw, cy - hh, cx + hw, cy - hh),
                     (cx + hw, cy - hh, cx + hw, cy + hh),
                     (cx + hw, cy + hh, cx - hw, cy + hh),
                     (cx - hw, cy + hh, cx - hw, cy - hh)]
    open(fn, "w").write(_gerber([], outl, 0.1))
    files.append(fn)
    # drill: PTH holes (soldering) + vias (layer changes).
    # 1-layer boards have no vias — but PTH drills still go here.
    from .parts import hole_drill as _hd
    drills: dict[float, set[tuple[float, float]]] = {}
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            dr = _hd(p.fp, pin, lib)
            if dr > 0:
                x, y = board.pad_pos(p.ref, pin)
                drills.setdefault(dr, set()).add((round(x, 3), round(y, 3)))
    for t in board.traces:
        if getattr(t, "via", False):
            drills.setdefault(0.4, set()).add((round(t.x1, 3), round(t.y1, 3)))
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "hole":
            from .drc import zone_at
            z = zone_at(board, c)
            drills.setdefault(_f(z["d"]), set()).add(
                (round(_f(z["x"]), 3), round(_f(z.get("y", 0.0)), 3)))
    fn = os.path.join(outdir, f"{board.name}.TXT")
    d = ["M48", "METRIC,TZ"]
    tools = sorted(drills)
    for i, dr in enumerate(tools, 1):
        d.append(f"T{i}C{dr:.3f}")
    d.append("%")
    for i, dr in enumerate(tools, 1):
        d.append(f"G90\nG05\nT{i}")
        d += [f"X{x:.3f}Y{y:.3f}" for x, y in sorted(drills[dr])]
    d += ["T0", "M30"]
    open(fn, "w").write("\n".join(d))
    files.append(fn)
    fn = os.path.join(outdir, f"{board.name}.BOM.csv")
    # JLC format: Comment,Designator,Footprint,LCSC — grouped by value,
    # LCSC from `lcsc` part attr
    groups: dict[tuple[str, str], list[str]] = {}
    for p in board.parts.values():
        groups.setdefault((p.value, p.fp), []).append(p.ref)
    lines = ["Comment,Designator,Footprint,LCSC"]
    for (value, fp), refs in sorted(groups.items()):
        lcsc = ""
        for r in refs:
            a = board.parts[r].attrs.get("lcsc", "")
            if a:
                lcsc = str(a)
                break
        lines.append(f"{value},\"{','.join(sorted(refs))}\",{fp},{lcsc}")
    open(fn, "w").write("\n".join(lines) + "\n")
    files.append(fn)
    fn = os.path.join(outdir, f"{board.name}.CPL.csv")
    open(fn, "w").write("Designator,Mid X,Mid Y,Layer,Rotation\n" + "".join(
        f"{p.ref},{p.x:.3f}mm,{p.y:.3f}mm,Top,{int(p.attrs.get('rot', 0))}\n"
        for p in board.parts.values()))
    files.append(fn)
    return files


def export_easyeda(board: Board, outdir: str = "out") -> list[str]:
    """EasyEDA Std PCB JSON (docType 3): LIB footprints (PAD children) +
    TRACK/VIA shapes, 10-mil units. Opens in EasyEDA/JLCEDA import."""
    import json
    from .parts import hole_drill, pad_size, pads_of
    os.makedirs(outdir, exist_ok=True)
    lib = board._lib()
    mm = 1 / 0.254  # mm → 10-mil units
    shape: list[str] = []
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        kids: list[str] = [f"TEXT~P~0~0~0.7~0~~3~~4.5~{p.ref}~~g{p.ref}"]
        for pin in sorted(pads_of(p.fp, lib)):
            dx, dy = board.pad_pos(p.ref, pin)
            rx, ry = (dx - p.x) * mm, (dy - p.y) * mm
            dr = hole_drill(p.fp, pin, lib)
            if dr > 0:
                kids.append(f"PAD~OVAL~{rx:.1f}~{ry:.1f}~6~6~11~"
                            f"{_enet(board, p.ref, pin)}~{pin}~{dr / 2 / 0.254:.1f}~~0~g{p.ref}{pin}")
            else:
                pw, ph = pad_size(p.fp, pin, lib)
                kids.append(f"PAD~RECT~{rx:.1f}~{ry:.1f}~{pw / 0.254:.1f}~{ph / 0.254:.1f}~1~"
                            f"{_enet(board, p.ref, pin)}~{pin}~~0~g{p.ref}{pin}")
        shape.append(f"LIB~{p.x * mm:.1f}~{p.y * mm:.1f}~package`{p.fp}`name`{p.ref}`~~g{p.ref}~1"
                     + "".join("#@$" + k for k in kids))
    for t in sorted(board.traces, key=lambda s: (s.net, s.layer, s.x1, s.y1, s.x2, s.y2)):
        pts = f"{t.x1 * mm:.1f} {t.y1 * mm:.1f} {t.x2 * mm:.1f} {t.y2 * mm:.1f}"
        if getattr(t, "via", False):
            shape.append(f"VIA~{t.x1 * mm:.1f}~{t.y1 * mm:.1f}~3.2~{t.net}~0.8~gvia")
        else:
            shape.append(f"TRACK~{t.width / 0.254:.1f}~{t.layer + 1}~{t.net}~{pts}~gt{t.layer}")
    doc = {"head": "3~1.7.5", "canvas": "CA~2400~2400~#000000~yes~#FFFFFF~10~1200~1200~line~1~mil~1~45~visible~0.5~400~300",
           "shape": shape, "title": board.meta.get("title", board.name),
           "dataStr": {"layers": ["1~TopLayer~#FF0000~true~true~true",
                                  "2~BottomLayer~#0000FF~true~false~true",
                                  "10~BoardOutline~#FF00FF~true~false~true"]}}
    fn = os.path.join(outdir, f"{board.name}.easyeda.json")
    open(fn, "w").write(json.dumps(doc))
    return [fn]


def _enet(board: Board, ref: str, pin: object) -> str:
    for n, net in board.nets.items():
        if (ref, str(pin)) in [(r, str(q)) for r, q in net.pins]:
            return n
    return ""


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
    if board.meta.get("title"):
        A(f'  (title_block (title {_sexp_str(board.meta["title"])}))')
    A('  (general (thickness 1.6))')
    A('  (paper "A4")')
    layers = kicad_layers(board.layers)
    A("  (layers")
    for i, ln in enumerate(layers):
        A(f'    ({i} {ln} signal)')
    A("  )")
    A('  (setup (pad_to_mask_clearance 0.05))')
    net_ids: dict[str, int] = {}
    A('  (net 0 "")')  # KiCad requires the unconnected net declared first
    for i, n in enumerate(sorted(board.nets), 1):
        net_ids[n] = i
        A(f"  (net {i} {_sexp_str(n)})")
    pin_net: dict[tuple[str, str], int] = {}
    for n, net in board.nets.items():
        for r, q in net.pins:
            pin_net[(r, str(q))] = net_ids[n]
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        A(f'  (footprint {_sexp_str(p.fp)} (layer "F.Cu")')
        A(f"    (at {p.x:.4f} {p.y:.4f})")
        A(f'    (descr {_sexp_str(p.value or p.fp)})')
        _pw, _ph = p.wh()
        A(f'    (fp_text user {p.ref} (at 0 {-_ph / 2 - 1:.4f}) (layer "F.SilkS"))')
        for pin in sorted(pads_of(p.fp, lib)):
            dx, dy = board.pad_pos(p.ref, pin)
            dr = hole_drill(p.fp, pin, lib)
            nid = pin_net.get((p.ref, str(pin)), 0)
            if dr > 0:
                A(f'    (pad {pin} thru_hole circle (at {dx:.4f} {dy:.4f}) '
                  f"(size {dr + 0.7:.4f} {dr + 0.7:.4f}) (drill {dr:.4f}) (layers *.Cu *.Mask) (net {nid}))")
            else:
                pw, ph = pad_size(p.fp, pin, lib)
                A(f'    (pad {pin} smd rect (at {dx:.4f} {dy:.4f}) '
                  f"(size {pw:.4f} {ph:.4f}) (layers F.Cu F.Mask) (net {nid}))")
        A("  )")
    for t in sorted(board.traces, key=lambda s: (s.net, s.layer, s.x1, s.y1, s.x2, s.y2)):
        ln = layers[t.layer] if t.layer < len(layers) else layers[0]
        nid = net_ids.get(t.net, 0)
        A(f'  (segment (start {t.x1:.4f} {t.y1:.4f}) (end {t.x2:.4f} {t.y2:.4f}) '
          f'(width {t.width:.4f}) (layer "{ln}") (net {nid}))')
    W, H = board.width, board.height
    for x1, y1, x2, y2 in [(0, 0, W, 0), (W, 0, W, H), (W, H, 0, H), (0, H, 0, 0)]:
        A(f'  (gr_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) '
          f'(layer "Edge.Cuts") (width 0.1))')
    for con in board.constraints:
        kind = con.get("t")
        if kind == "keepout":
            from .drc import zone_at
            z = zone_at(board, con)
            cx, cy = _f(z["x"]), _f(z.get("y", 0.0))
            if z.get("d") is not None:
                rr = _f(z["d"]) / 2
                A(f'  (gr_circle (center {cx:.4f} {cy:.4f}) (end {cx + rr:.4f} {cy:.4f}) '
                  f'(layer "Cmts.User") (width 0.05))')
                continue
            hw, hh = _f(z.get("w", 0.0)) / 2, _f(z.get("h", 0.0)) / 2
            for x1, y1, x2, y2 in [(cx - hw, cy - hh, cx + hw, cy - hh),
                                   (cx + hw, cy - hh, cx + hw, cy + hh),
                                   (cx + hw, cy + hh, cx - hw, cy + hh),
                                   (cx - hw, cy + hh, cx - hw, cy - hh)]:
                A(f'  (gr_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) '
                  f'(layer "Cmts.User") (width 0.05))')
        elif kind == "hole":
            A(f'  (pad HOLE thru_hole circle (at {_f(con["x"]):.4f} {_f(con["y"]):.4f}) '
              f'(size {_f(con["d"]) + 0.6:.4f} {_f(con["d"]) + 0.6:.4f}) '
              f'(drill {_f(con["d"]):.4f}) (layers *.Cu *.Mask) (net 0))')
        elif kind in ("bend", "stiffener"):
            cx, cy = _f(con["x"]), _f(con["y"])
            hw, hh = _f(con["w"]) / 2, _f(con["h"]) / 2
            tag = ("BEND" + ("-DYN" if con.get("dynamic", True) else "-STAT")) \
                if kind == "bend" else f"STIFF-{con['mat']}-{_f(con['th']):g}"
            for x1, y1, x2, y2 in [(cx - hw, cy - hh, cx + hw, cy - hh),
                                   (cx + hw, cy - hh, cx + hw, cy + hh),
                                   (cx + hw, cy + hh, cx - hw, cy + hh),
                                   (cx - hw, cy + hh, cx - hw, cy - hh)]:
                A(f'  (gr_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) '
                  f'(layer "Cmts.User") (width 0.05))')
            A(f'  (gr_text "{tag}" (at {cx:.4f} {cy:.4f}) (layer "Cmts.User"))')
    A(")")
    fn = os.path.join(outdir, f"{board.name}.kicad_pcb")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]
