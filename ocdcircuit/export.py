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
    # LCSC from `lcsc` part attr. DNP parts get their own rows (JLC's
    # "Do not place" is per-line; never merge placed + DNP).
    groups: dict[tuple[str, str, str], list[str]] = {}
    for p in board.parts.values():
        groups.setdefault((p.value, p.fp, "DNP" if p.attrs.get("dnp") else ""), []).append(p.ref)
    lines = ["Comment,Designator,Footprint,LCSC"]
    for (value, fp, dnp), refs in sorted(groups.items()):
        lcsc = ""
        for r in refs:
            a = board.parts[r].attrs.get("lcsc", "")
            if a:
                lcsc = str(a)
                break
        comment = f"{value} (DNP)" if dnp else value
        lines.append(f"{comment},\"{','.join(sorted(refs))}\",{fp},{lcsc}")
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


def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


def _tech_layers() -> list[tuple[int, str, str]]:
    """Full KiCad tech-layer table (ids + order from pcbnew's own files:
    copper first, then tech — the CLI validates this). Unlisted layers
    break zone fills, DRC, and 3D renders that reference F.Mask etc."""
    return [(0, "F.Cu", "signal"), (2, "B.Cu", "signal"),
            (9, "F.Adhes", "user"), (11, "B.Adhes", "user"),
            (13, "F.Paste", "user"), (15, "B.Paste", "user"),
            (5, "F.SilkS", "user"), (7, "B.SilkS", "user"),
            (1, "F.Mask", "user"), (3, "B.Mask", "user"),
            (17, "Dwgs.User", "user"), (19, "Cmts.User", "user"),
            (21, "Eco1.User", "user"), (23, "Eco2.User", "user"),
            (25, "Edge.Cuts", "user"), (27, "Margin", "user"),
            (31, "F.CrtYd", "user"), (29, "B.CrtYd", "user"),
            (35, "F.Fab", "user"), (33, "B.Fab", "user")]


# soldermask palette for render presets (kicad preset patch at render time)
MASK_COLORS = {"green": (18, 90, 20), "red": (160, 20, 20),
               "blue": (20, 50, 140), "black": (15, 15, 15),
               "white": (225, 225, 225), "purple": (90, 30, 130),
               "yellow": (200, 170, 30)}


def _model_for(fp: str) -> str | None:
    """KiCad 3D model path for std footprints (env-var form so
    KICAD10_3DMODEL_DIR resolves on the viewer's machine). Candidates
    are probed against the local model dir; first hit wins, else a
    static fallback (pads + silk still render)."""
    import os
    import re
    M = "${KICAD10_3DMODEL_DIR}"
    local = os.environ.get("KICAD10_3DMODEL_DIR", "/usr/share/kicad/3dmodels")

    def pick(d: str, *cands: str) -> str | None:
        for c in cands:
            if os.path.exists(os.path.join(local, d, c)):
                return f"{M}/{d}/{c}"
        return None  # no such STEP shipped: pads + silk still render

    _METRIC = {"0201": "0603Metric", "0402": "1005Metric",
               "0603": "1608Metric", "0805": "2012Metric",
               "1206": "3216Metric", "1210": "3225Metric",
               "1218": "3246Metric", "2010": "5025Metric",
               "2512": "6332Metric"}
    m = re.fullmatch(r"(R|C|LED|L)(\d{4})", fp)
    if m and m.group(2) in _METRIC:
        fam = {"R": "Resistor_SMD.3dshapes", "C": "Capacitor_SMD.3dshapes",
               "LED": "LED_SMD.3dshapes",
               "L": "Inductor_SMD.3dshapes"}[m.group(1)]
        s = f"{m.group(1)}_{m.group(2)}_{_METRIC[m.group(2)]}.step"
        return pick(fam, s)
    m = re.fullmatch(r"IND_SM(\d{4})", fp)
    if m and m.group(1) in _METRIC:
        return pick("Inductor_SMD.3dshapes",
                    f"L_{m.group(1)}_{_METRIC[m.group(1)]}.step")
    if fp in ("D_SOD123", "D_SOD323", "D_SMA", "D_SMB", "D_SMC"):
        return pick("Diode_SMD.3dshapes", f"D_{fp[2:].replace('SOD', 'SOD-')}.step")
    if fp in ("SOT23", "SOT-23", "SOT-23-3", "SOT23-3", "SOT-23-5",
              "SOT223", "SOT-223", "SOT89", "SOT-89"):
        stem = {"SOT-23-5": "SOT-23-5", "SOT223": "SOT-223",
                "SOT-223": "SOT-223", "SOT89": "SOT-89-3",
                "SOT-89": "SOT-89-3"}.get(fp, "SOT-23")
        return pick("Package_TO_SOT_SMD.3dshapes", f"{stem}.step")
    if fp in ("DPAK", "TO-252"):
        return pick("Package_TO_SOT_SMD.3dshapes", "TO-252-2.step")
    if fp in ("D2PAK", "TO-263"):
        return pick("Package_TO_SOT_SMD.3dshapes", "TO-263-2.step")
    if fp in ("MSOP8", "MSOP10"):
        n = fp[4:]
        return pick("Package_SO.3dshapes", f"MSOP-{n}_3x3mm_P0.65mm.step",
                    f"MSOP-{n}_3x3mm_P0.5mm.step")
    m = re.fullmatch(r"SOIC(\d+)", fp)
    if m:
        n = m.group(1)
        wide = "3.9x8.7mm" if n in ("14", "16") else "3.9x9.9mm" if n in ("20", "28") else "3.9x4.9mm"
        return pick("Package_SO.3dshapes", f"SOIC-{n}_{wide}_P1.27mm.step")
    for fam_pre, dims in (("TSSOP", ("4.4x5mm_P0.65mm", "4.4x6.5mm_P0.65mm")),
                          ("SSOP", ("5.3x6.2mm_P0.65mm", "5.3x10.2mm_P0.65mm",
                                   "3.9x9.9mm_P0.635mm"))):
        if fp.startswith(fam_pre):
            n = fp[len(fam_pre):]
            return pick("Package_SO.3dshapes",
                        *(f"{fam_pre}-{n}_{d}.step" for d in dims))
    m = re.fullmatch(r"QFN(\d+)", fp)
    if m:
        n = m.group(1)
        return pick("Package_DFN_QFN.3dshapes",
                    f"QFN-{n}-1EP_4x4mm_P0.4mm_EP2.65x2.65mm.step",
                    f"QFN-{n}-1EP_5x5mm_P0.5mm_EP3.3x3.3mm.step",
                    f"MPS_QFN-{n}_3x3mm_P0.5mm.step")
    m = re.fullmatch(r"QFP(\d+)", fp)
    if m:
        n = m.group(1)
        return pick("Package_QFP.3dshapes",
                    f"LQFP-{n}_7x7mm_P0.5mm.step",
                    f"TQFP-{n}_7x7mm_P0.8mm.step",
                    f"LQFP-{n}_10x10mm_P0.5mm.step",
                    f"LQFP-{n}_14x14mm_P0.5mm.step")
    if fp in ("XTAL_3225", "XTAL_5032"):
        return pick("Crystal.3dshapes",
                    "Crystal_SMD_3225-4Pin_3.2x2.5mm.step",
                    "Crystal_SMD_5032-2Pin_5.0x3.2mm.step")
    if fp == "OSC4":
        return pick("Crystal.3dshapes",
                    "Crystal_SMD_5032-2Pin_5.0x3.2mm.step",
                    "Crystal_SMD_3225-4Pin_3.2x2.5mm.step")
    if fp in ("ELEC_5MM", "ELEC_6MM", "ELEC_8MM", "ELEC_10MM"):
        d = fp.split("_")[1].replace("MM", "")
        dia = "6.3" if d == "6" else d
        pitch = "2.50mm" if d in ("6", "8", "10") else "2.00mm"
        return pick("Capacitor_THT.3dshapes",
                    f"CP_Radial_D{dia}mm_P{pitch}.step")
    if fp in ("USB_C", "USB_C_EDGE"):
        return pick("Connector_USB.3dshapes",
                    "USB_C_Receptacle_GCT_USB4085.step")
    if fp in ("USB_MICRO", "USB_MINI"):
        return pick("Connector_USB.3dshapes",
                    "USB_Micro-B_Molex_47346-0001.step",
                    "USB_Mini-B_Lumberg_2486_01_Horizontal.step")
    m = re.fullmatch(r"PINHD(\d+)", fp)
    if m:
        return pick("Connector_PinHeader_2.54mm.3dshapes",
                    f"PinHeader_1x{int(m.group(1)):02d}_P2.54mm_Vertical.step")
    m = re.fullmatch(r"PINHD2X(\d+)", fp)
    if m:
        return pick("Connector_PinHeader_2.54mm.3dshapes",
                    f"PinHeader_2x{int(m.group(1)):02d}_P2.54mm_Vertical.step")
    m = re.fullmatch(r"JST(\d+)", fp)
    if m:
        jn = int(str(m.group(1)))
        return pick("Connector_JST.3dshapes",
                    f"JST_XH_B{jn}B-XH-A_1x{jn:02d}_P2.50mm_Vertical.step")
    if fp in ("TERMINAL2", "TERMINAL3"):
        return pick("TerminalBlock_Phoenix.3dshapes",
                    f"TerminalBlock_Phoenix_MKDS-1,5-{fp[-1]}-5.08_1x{fp[-1]:0>2}_P5.08mm_Horizontal.step")
    if fp == "BARREL":
        return pick("Connector_BarrelJack.3dshapes",
                    "BarrelJack_Horizontal.step")
    if fp == "SDCARD":
        return pick("Connector_Card.3dshapes",
                    "microSD_HC_Hirose_DM3D-SF.step")
    return None


def export_kicad(board: Board, outdir: str = "out") -> list[str]:
    """Write <name>.kicad_pcb (s-expression). Pads from pad_size, holes
    from hole_drill; segments per trace; silk refs via fp_text user."""
    from .parts import hole_drill, pad_size, pads_of
    os.makedirs(outdir, exist_ok=True)
    lib = board._lib()
    L: list[str] = []
    A = L.append
    A("(kicad_pcb (version 20221018) (generator ocdcircuit)")
    if board.meta.get("title") or board.meta.get("rev"):
        A(f'  (title_block (title {_sexp_str(board.meta.get("title", board.name))})'
          + (f' (rev {_sexp_str(board.meta["rev"])})' if board.meta.get("rev") else "")
          + (f' (comment 1 {_sexp_str(board.meta["desc"])})' if board.meta.get("desc") else "")
          + ')')
    A('  (general (thickness 1.6))')
    A('  (paper "A4")')
    layers = kicad_layers(board.layers)
    A("  (layers")
    A('    (0 "F.Cu" signal)')
    for i in range(1, board.layers - 1):
        A(f'    ({2 * i + 2} "In{i}.Cu" signal)')
    if board.layers > 1:
        A('    (2 "B.Cu" signal)')
    for i, name, typ in _tech_layers():
        if name in ("F.Cu", "B.Cu"):
            continue
        A(f'    ({i} {_sexp_str(name)} {typ})')
    A("  )")
    mask = MASK_COLORS.get(str(board.meta.get("mask", "green")).lower(),
                           MASK_COLORS["green"])
    _ = mask  # soldermask tint applies at render time (kicad preset), not in file
    A('  (setup (pad_to_mask_clearance 0.05))')
    net_ids: dict[str, int] = {}
    A('  (net 0 "")')  # KiCad requires the unconnected net declared first
    for i, n in enumerate(sorted(board.nets), 1):
        net_ids[n] = i
        A(f'  (net {i} {_sexp_str(n)})')
    pin_net: dict[tuple[str, str], str] = {}
    for n, net in board.nets.items():
        for r, q in net.pins:
            pin_net[(r, str(q))] = n
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        uuid = _uuid()
        A(f'  (footprint {_sexp_str(p.fp)} (layer "F.Cu") (uuid "{uuid}")')
        A(f"    (at {p.x:.4f} {p.y:.4f})")
        A(f'    (descr {_sexp_str(p.value or p.fp)})')
        _pw, _ph = p.wh()
        puuid = _uuid()
        A(f'    (fp_text user {_sexp_str(p.ref)} (at 0 {-_ph / 2 - 1:.4f}) (layer "F.SilkS") (uuid "{puuid}"))')
        if p.value:
            A(f'    (fp_text value {_sexp_str(p.value)} (at 0 {_ph / 2 + 1:.4f}) '
              f'(layer "F.Fab") (uuid "{_uuid()}"))')
        for pin in sorted(pads_of(p.fp, lib)):
            dx, dy = board.pad_pos(p.ref, pin)
            dr = hole_drill(p.fp, pin, lib)
            nn = _sexp_str(pin_net.get((p.ref, str(pin)), ""))
            q = _uuid()
            if dr > 0:
                A(f'    (pad {_sexp_str(pin)} thru_hole circle (at {dx - p.x:.4f} {dy - p.y:.4f}) '
                  f'(size {dr + 0.7:.4f} {dr + 0.7:.4f}) (drill {dr:.4f}) '
                  f'(layers "*.Cu" "*.Mask") (net {nn}) (uuid "{q}"))')
            else:
                pw, ph = pad_size(p.fp, pin, lib)
                A(f'    (pad {_sexp_str(pin)} smd rect (at {dx - p.x:.4f} {dy - p.y:.4f}) '
                  f'(size {pw:.4f} {ph:.4f}) (layers "F.Cu" "F.Paste" "F.Mask") '
                  f'(net {nn}) (uuid "{q}"))')
        model = _model_for(p.fp)
        if model is not None:
            A(f'    (model "{model}" (offset (xyz 0 0 0)) '
              f'(scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))')
        A("  )")
    for t in sorted(board.traces, key=lambda s: (s.net, s.layer, s.x1, s.y1, s.x2, s.y2)):
        ln = layers[t.layer] if t.layer < len(layers) else layers[0]
        nid = _sexp_str(t.net)
        if getattr(t, "via", False):
            A(f'  (via (at {t.x1:.4f} {t.y1:.4f}) (size 0.8) (drill 0.4) '
              f'(layers {_sexp_str(layers[0])} {_sexp_str(layers[-1])}) (net {nid}) (uuid "{_uuid()}"))')
            continue
        A(f'  (segment (start {t.x1:.4f} {t.y1:.4f}) (end {t.x2:.4f} {t.y2:.4f}) '
          f'(width {t.width:.4f}) (layer {_sexp_str(ln)}) (net {nid}) (uuid "{_uuid()}"))')
    W, H = board.width, board.height
    for x1, y1, x2, y2 in [(0, 0, W, 0), (W, 0, W, H), (W, H, 0, H), (0, H, 0, 0)]:
        A(f'  (gr_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) '
          f'(layer "Edge.Cuts") (width 0.1))')
    from .drc import fp_keepouts, zone_at

    def _cmts(z: dict[str, object]) -> None:
        cx, cy = _f(z["x"]), _f(z.get("y", 0.0))
        if z.get("d") is not None:
            rr = _f(z["d"]) / 2
            A(f'  (gr_circle (center {cx:.4f} {cy:.4f}) (end {cx + rr:.4f} {cy:.4f}) '
              f'(layer "Cmts.User") (width 0.05))')
            return
        hw, hh = _f(z.get("w", 0.0)) / 2, _f(z.get("h", 0.0)) / 2
        for x1, y1, x2, y2 in [(cx - hw, cy - hh, cx + hw, cy - hh),
                               (cx + hw, cy - hh, cx + hw, cy + hh),
                               (cx + hw, cy + hh, cx - hw, cy + hh),
                               (cx - hw, cy + hh, cx - hw, cy - hh)]:
            A(f'  (gr_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) '
              f'(layer "Cmts.User") (width 0.05))')

    for con in board.constraints:
        kind = con.get("t")
        if kind == "keepout":
            _cmts(zone_at(board, con))
        elif kind == "hole":
            hx, hy, hd = _f(con["x"]), _f(con["y"]), _f(con["d"])
            A(f'  (footprint "MOUNT_HOLE" (layer "F.Cu") (uuid "{_uuid()}")')
            A(f"    (at {hx:.4f} {hy:.4f})")
            A(f'    (pad "1" thru_hole circle (at 0 0) '
              f'(size {hd + 0.6:.4f} {hd + 0.6:.4f}) '
              f'(drill {hd:.4f}) (layers "*.Cu" "*.Mask") (net "") (uuid "{_uuid()}"))')
            A("  )")
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
    # footprint keepouts (antenna zones etc.) ride along as Cmts.User art
    for ref in board.parts:
        for c in fp_keepouts(board, ref):
            _cmts(zone_at(board, c))
    A(")")
    fn = os.path.join(outdir, f"{board.name}.kicad_pcb")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]
