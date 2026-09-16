"""Gerber/Excellon (JLC) + KiCad s-expr export. Hand-rolled, no deps.

KiCad writer emits a loadable .kicad_pcb: general/setup/layers, nets,
footprints with SMD pads + PTH holes, segments, vias at segment joints.
Not bit-identical to KiCad's own output, but parses and round-trips.
"""
from __future__ import annotations
from .util import as_float as _f
import csv
import os
import re
from typing import TYPE_CHECKING, cast


if TYPE_CHECKING:
    from .circuit import Board

Flash = tuple[float, float]
Draw = tuple[float, float, float, float]


def _gerber(flashes: list[Flash], draws: list[Draw], aperture: float,
              negative: str | None = None,
              widths: list[float] | None = None,
              fsizes: list[float] | None = None) -> str:
    """Positive plot, or negative plane (flood minus `negative` cutouts).
    `widths` parallels `draws`: one aperture per distinct width (D10, D11,
    ...), so 0.5 power traces don't render at the 0.4 default. `fsizes`
    parallels `flashes`: flash aperture per pad (mask openings exceed the
    pad; a single circle under-opens every SMD pad)."""
    groups: dict[float, int] = {}
    for w in (widths or []) + (fsizes or []):
        if w not in groups:
            groups[w] = 11 + len(groups)  # D10 is the base aperture
    if negative is None:
        out = ["G04 ocdcircuit*", "%FSLAX46Y46*%", "%MOMM*%",
               f"%ADD10C,{aperture:.3f}*%"]
        for w, code in sorted(groups.items(), key=lambda kv: kv[1]):
            out.append(f"%ADD{code}C,{w:.3f}*%")
        out.append("D10*")
    else:
        # negative plane: clear-polarity draws subtract from the flood.
        # Cutouts render as drawn rects; the flood rect is board outline.
        out = ["G04 ocdcircuit*", "%FSLAX46Y46*%", "%MOMM*%", "%LPC*%",
               f"%ADD10C,{aperture:.3f}*%", "D10*",
               f"G04 plane {negative}*"]
    forder = sorted(range(len(flashes)),
                    key=lambda i: groups[(fsizes or [])[i]] if fsizes else 10)
    cur = -1
    for i in forder:
        code = groups[(fsizes or [])[i]] if fsizes else 10
        x, y = flashes[i]
        if code != cur:
            out.append(f"D{code:02d}*")
            cur = code
        out.append(f"X{x:.4f}Y{y:.4f}D03*")
    # one aperture select per width group (D01 draws with current aperture)
    order = sorted(range(len(draws)),
                   key=lambda i: groups[(widths or [])[i]] if widths else 10)
    cur = -1
    for i in order:
        code = groups[(widths or [])[i]] if widths else 10
        x1, y1, x2, y2 = draws[i]
        if code != cur:
            out.append(f"D{code:02d}*")
            cur = code
        out.append(f"X{x1:.4f}Y{y1:.4f}D02*")
        out.append(f"X{x2:.4f}Y{y2:.4f}D01*")
    out.append("M02*")
    return "\n".join(out)


def plane_plots(board: Board) -> dict[int, list[Draw]]:
    """Negative-plot cutouts per pour layer: what the plane must avoid.

    Cutouts = foreign-net pads (pad + 0.3 gap), foreign vias, keepout /
    cutout zones. Own-net pads need no cutout (the plane connects them;
    that is the point); part bodies need none (no traces run under parts
    except at pads). Returns {layer: [cutout rects]}; empty when the
    board declares no pours.
    # ponytail: rect cutouts, not polygon subtraction -- JLC renders the
    # bbox union fine at these clearances (e2e pours export DRC-clean);
    # exact boolean ops if a fab ever rejects a plot (none has).
    """
    from .drc import fp_keepouts, pour_layers, zone_at
    from .parts import hole_drill, pads_of
    poured = pour_layers(board)
    if not poured:
        return {}
    lib = {k: v for k, v in board._lib().items()}
    gap = 0.3
    out: dict[int, list[Draw]] = {}
    for net, layers in poured.items():
        own = {(r, str(q)) for r, q in board.nets[net].pins}
        for ll in layers:
            cuts: list[Draw] = []
            for p in board.parts.values():
                for pin in pads_of(p.fp, lib):
                    if (p.ref, str(pin)) in own:
                        continue  # own net -- plane connects, no cutout
                    x, y = board.pad_pos(p.ref, pin)
                    dr = 0.0
                    try:
                        dr = hole_drill(p.fp, pin, lib)
                    except (KeyError, ValueError):
                        pass
                    r = max(1.0, dr + 0.3) / 2 + gap
                    cuts.append((x - r, y - r, x + r, y + r))
            for t in board.traces:
                if t.via and t.net != net:
                    r = 0.2 + gap
                    cuts.append((t.x1 - r, t.y1 - r, t.x1 + r, t.y1 + r))
            for c in board.constraints:
                if isinstance(c, dict) and c.get("t") in ("keepout", "cutout"):
                    z = zone_at(board, c)
                    cx, cy = _f(z["x"]), _f(z.get("y", 0.0))
                    if z.get("d") is not None:
                        rr = _f(z["d"]) / 2 + gap
                        cuts.append((cx - rr, cy - rr, cx + rr, cy + rr))
                    else:
                        hw = _f(z.get("w", 0.0)) / 2 + gap
                        hh = _f(z.get("h", 0.0)) / 2 + gap
                        cuts.append((cx - hw, cy - hh, cx + hw, cy + hh))
            for ref in board.parts:
                for z in fp_keepouts(board, ref):
                    zz = zone_at(board, z)
                    cx, cy = _f(zz["x"]), _f(zz.get("y", 0.0))
                    hw = _f(zz.get("w", 0.0)) / 2 + gap
                    hh = _f(zz.get("h", 0.0)) / 2 + gap
                    cuts.append((cx - hw, cy - hh, cx + hw, cy + hh))
            out[ll] = cuts
    return out



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
    from .parts import pad_size
    msizes: list[float] = []  # mask opening per top pad (pad + 0.1 each side)
    psizes: list[float] = []  # paste per SMD pad (pad - 0.1, never starved)
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            x, y = board.pad_pos(p.ref, pin)
            flashes[0].append((x, y))  # SMD pads on top
            pw, ph = pad_size(p.fp, pin, lib)
            msizes.append(round(max(pw, ph) + 0.1, 3))
            from .parts import hole_drill
            if not hole_drill(p.fp, pin, lib):
                paste.append((x, y))  # SMD only — PTH gets no paste
                psizes.append(round(max(min(pw, ph) - 0.1, 0.2), 3))
    widths: dict[int, list[float]] = {ll: [] for ll in range(board.layers)}
    for t in board.traces:
        draws[t.layer % board.layers].append((t.x1, t.y1, t.x2, t.y2))
        widths[t.layer % board.layers].append(t.width)
    for ll in draws:
        order = sorted(range(len(draws[ll])), key=lambda i: (draws[ll][i], widths[ll][i]))
        draws[ll] = [draws[ll][i] for i in order]
        widths[ll] = [widths[ll][i] for i in order]
        if ll == 0:
            # keep mask sizes aligned with sorted flashes
            paired = sorted(zip(flashes[ll], msizes))
            flashes[ll] = [p[0] for p in paired]
            msizes = [p[1] for p in paired]
        else:
            flashes[ll] = sorted(flashes[ll])
    # pours: negative plane (flood minus cutouts) replaces trace draws.
    # Flood insets by fab edge clearance (plane to outline shorts the specs
    # DRC enforces on every other copper); cutouts clear foreign copper.
    from .drc import pour_layers as _pours
    from .fab import get as _fab_get
    planes = plane_plots(board)
    poured_nets = {n: sorted(ll) for n, ll in _pours(board).items()}
    edge = float(cast(float, _fab_get(board.fab).get("edge", 0.3)))
    for ll, nm in enumerate(layer_names(board.layers)):
        fn = os.path.join(outdir, f"{board.name}.{nm}.gbr")
        if ll in planes:
            # no flashes: flood connects own-net pads directly; cutouts
            # clear foreign copper (flashes would punch wrong-size voids)
            cuts = planes[ll]
            x0, y0, x1, y1 = edge, edge, board.width - edge, board.height - edge
            flood = [(x0, y0, x1, y0), (x1, y0, x1, y1),
                     (x1, y1, x0, y1), (x0, y1, x0, y0)]
            open(fn, "w").write(_gerber([], flood + cuts, 0.4,
                                        negative=",".join(
                                            f"{n}@L{ll}" for n, lls in poured_nets.items() if ll in lls)))
        else:
            open(fn, "w").write(_gerber(flashes.get(ll, []), draws.get(ll, []), 0.4,
                                        widths=widths.get(ll, [])))
        files.append(fn)
    # paste (top only — single-sided SMT like the mitox board)
    fn = os.path.join(outdir, f"{board.name}.GTP.gbr")
    open(fn, "w").write(_gerber(paste, [], 0.4, fsizes=psizes))
    files.append(fn)
    # mask: openings over pads (empty file = full mask = unsolderable).
    # Bottom is pad-free (single-sided SMT), so empty GBS is correct there.
    fn = os.path.join(outdir, f"{board.name}.GTS.gbr")
    open(fn, "w").write(_gerber(flashes.get(0, []), [], 0.5, fsizes=msizes))
    files.append(fn)
    if board.layers > 1:
        fn = os.path.join(outdir, f"{board.name}.GBS.gbr")
        open(fn, "w").write(_gerber([], [], 0.5))
        files.append(fn)
    # silk: courtyard outlines + pin-1 dots (no stroke font in this
    # writer, so ref text stays in the KiCad export, not Gerber)
    from .silk import labels as _silk_labels, level_of as _silk_level
    sk = _silk_labels(board, max(_silk_level(board), 2))  # fab gets
    # outlines + pin-1 dots even when the screen level shows refs only
    silk_draws: list[Draw] = [(b.x0, b.y0, b.x1, b.y0) for b in sk.boxes]
    silk_draws += [(b.x1, b.y0, b.x1, b.y1) for b in sk.boxes]
    silk_draws += [(b.x1, b.y1, b.x0, b.y1) for b in sk.boxes]
    silk_draws += [(b.x0, b.y1, b.x0, b.y0) for b in sk.boxes]
    silk_fl: list[Flash] = [(d.x, d.y) for d in sk.dots]
    fn = os.path.join(outdir, f"{board.name}.GTO.gbr")
    open(fn, "w").write(_gerber(silk_fl, silk_draws, 0.2))
    files.append(fn)
    if board.layers > 1:
        fn = os.path.join(outdir, f"{board.name}.GBO.gbr")
        open(fn, "w").write(_gerber([], [], 0.2))
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
    # Milled slots ride G85 route blocks (one tool per width).
    from .parts import hole_drill as _hd, slot_of as _so
    drills: dict[float, set[tuple[float, float]]] = {}
    slots: dict[float, list[tuple[float, float, float, float]]] = {}
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            so = _so(p.fp, pin, lib)
            if so is not None:
                x, y = board.pad_pos(p.ref, pin)
                sw, sh = so[2], so[3]
                w = min(sw, sh)  # tool = slot width; length along long axis
                x1, y1, x2, y2 = (x - (max(sw, sh) - w) / 2, y, x + (max(sw, sh) - w) / 2, y) \
                    if sw >= sh else (x, y - (max(sw, sh) - w) / 2, x, y + (max(sw, sh) - w) / 2)
                slots.setdefault(round(w, 3), []).append((round(x1, 3), round(y1, 3),
                                                          round(x2, 3), round(y2, 3)))
                continue
            dr = _hd(p.fp, pin, lib)
            if dr > 0:
                x, y = board.pad_pos(p.ref, pin)
                drills.setdefault(dr, set()).add((round(x, 3), round(y, 3)))
    for t in board.traces:
        if t.via:
            drills.setdefault(0.4, set()).add((round(t.x1, 3), round(t.y1, 3)))
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "hole":
            from .drc import zone_at
            z = zone_at(board, c)
            drills.setdefault(_f(z["d"]), set()).add(
                (round(_f(z["x"]), 3), round(_f(z.get("y", 0.0)), 3)))
    fn = os.path.join(outdir, f"{board.name}.TXT")
    d = ["M48", "METRIC,TZ"]
    tools = sorted(set(drills) | set(slots))
    for i, dr in enumerate(tools, 1):
        d.append(f"T{i}C{dr:.3f}")
    d.append("%")
    for i, dr in enumerate(tools, 1):
        d.append(f"G90\nG05\nT{i}")
        d += [f"X{x:.3f}Y{y:.3f}" for x, y in sorted(drills.get(dr, ()))]
        d += [f"G85X{x1:.3f}Y{y1:.3f}X{x2:.3f}Y{y2:.3f}"
              for x1, y1, x2, y2 in sorted(slots.get(dr, ()))]
    d += ["T0", "M30"]
    open(fn, "w").write("\n".join(d))
    files.append(fn)
    fn = os.path.join(outdir, f"{board.name}.BOM.csv")
    # JLC format: Comment,Designator,Footprint,LCSC — grouped by value,
    # LCSC from `lcsc` part attr. DNP parts get their own rows (JLC's
    # "Do not place" is per-line; never merge placed + DNP). LCSC is part
    # of the key: same value+fp with different LCSC must not merge (JLC
    # orders per row; first-wins would ship the wrong reel).
    groups: dict[tuple[str, str, str, str], list[str]] = {}
    for p in board.parts.values():
        groups.setdefault((p.value, p.fp, str(p.attrs.get("lcsc", "")),
                           "DNP" if p.attrs.get("dnp") else ""), []).append(p.ref)
    byref = {p.ref: p for p in board.parts.values()}
    # csv.writer, not ",".join: a value carrying a comma (`1k,1%`) used to shift
    # every column (JLC read Designator="1%"), and quoting by hand is a bug per
    # field. lineterminator keeps the LF the rest of the bundle uses.
    with open(fn, "w", newline="") as f:
        cw = csv.writer(f, lineterminator="\n")
        cw.writerow(["Comment", "Designator", "Footprint", "LCSC", "Alternates"])
        for (value, fp, lcsc, dnp), refs in sorted(groups.items()):
            comment = f"{value} (DNP)" if dnp else value
            # alternates: curated per-part substitute lists (stock-outs);
            # unioned across the row, empties dropped. JLC ignores the extra
            # column; pinout compatibility stays a human attestation.
            alts = sorted({a.strip() for r in refs
                           for a in str(byref[r].attrs.get("alternates", "")).split(",")
                           if a.strip()})
            cw.writerow([comment, ",".join(sorted(refs)), fp, lcsc, ";".join(alts)])
    files.append(fn)
    fn = os.path.join(outdir, f"{board.name}.CPL.csv")
    # DNP excluded: CPL drives the pick-and-place machine, BOM marks the
    # row do-not-place — listing both would place what must stay empty.
    with open(fn, "w", newline="") as f:
        cw = csv.writer(f, lineterminator="\n")
        cw.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for p in board.parts.values():
            if p.attrs.get("dnp"):
                continue
            cw.writerow([p.ref, f"{p.x:.3f}mm", f"{p.y:.3f}mm", "Top",
                        int(p.attrs.get("rot", 0))])
    files.append(fn)
    return files


def export_easyeda(board: Board, outdir: str = "out") -> list[str]:
    """EasyEDA Std PCB JSON (docType 3): LIB footprints (PAD children,
    rotation + Fitted=N) + TRACK/VIA (inner layers 21+, real drill) +
    COPPERAREA pours, BOARDOUTLINE, HOLEs, TEXT silk. 10-mil units.
    Opens in EasyEDA/JLCEDA import; mirrors foreign.easyeda_doc."""
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
        # DNP has no native Std field: emit the community Fitted=N
        # parameter (importer honors it back — round-trips losslessly).
        # Format continues the key`value`key`value` chain (trailing `).
        fitted = "Fitted`N`" if p.attrs.get("dnp") else ""
        rot = int(p.attrs.get("rot", 0) or 0) % 360
        rotp = f"`rotation`{rot}" if rot else ""
        shape.append(f"LIB~{p.x * mm:.1f}~{p.y * mm:.1f}~package`{p.fp}`name`{p.ref}`"
                     f"{fitted}{rotp}~~g{p.ref}~1"
                     + "".join("#@$" + k for k in kids))

    def _ezlay(ll: int) -> int:
        return 1 if ll == 0 else 2 if ll == 1 else ll + 19  # 2→21…

    for t in sorted(board.traces, key=lambda s: (s.net, s.layer, s.x1, s.y1, s.x2, s.y2)):
        if t.via:
            dr = getattr(t, "drill", 0.4)
            shape.append(f"VIA~{t.x1 * mm:.1f}~{t.y1 * mm:.1f}~{(dr + 0.4) / 0.254:.1f}"
                         f"~{t.net}~{dr / 2 / 0.254:.1f}~gvia")
        else:
            pts = f"{t.x1 * mm:.1f} {t.y1 * mm:.1f} {t.x2 * mm:.1f} {t.y2 * mm:.1f}"
            shape.append(f"TRACK~{t.width / 0.254:.1f}~{_ezlay(t.layer)}~{t.net}~{pts}~gt{t.layer}")
    from .drc import pour_layers as _ezpours
    for pname, lls in sorted(_ezpours(board).items()):
        for ll in lls:
            w, h = board.width * mm, board.height * mm
            shape.append(f"COPPERAREA~2px~{_ezlay(ll)}~{pname}~0 0 {w:.1f} 0 {w:.1f} {h:.1f} 0 {h:.1f}"
                         f"~1~solid~gpour{ll}~spoke~none~[]")
    W, H = board.width * mm, board.height * mm
    shape.append(f"BOARDOUTLINE~0 0 {W:.1f} 0 {W:.1f} {H:.1f} 0 {H:.1f}~goutline")
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "hole":
            shape.append(f"HOLE~{float(cast(float, c['x'])) * mm:.1f}"
                         f"~{float(cast(float, c['y'])) * mm:.1f}"
                         f"~{float(cast(float, c.get('d', 3.0))) / 2 / 0.254:.1f}~ghole")
    for i, cmt in enumerate(board.comments):
        txt = str(cmt).split("@")[0].strip().replace("~", " ") or str(cmt).strip()
        shape.append(f"TEXT~L~{board.width * mm / 2:.1f}~{(board.height + 2 + i * 2) * mm:.1f}"
                     f"~0.8~0~none~3~~8~{txt}~~gtxt{i}")
    layers = ["1~TopLayer~#FF0000~true~true~true",
              "2~BottomLayer~#0000FF~true~false~true",
              "10~BoardOutline~#FF00FF~true~false~true"]
    for ll in range(2, min(board.layers, 6)):
        layers.append(f"{ll + 19}~Inner{ll - 1}~#808000~true~false~true")
    doc = {"head": "3~1.7.5", "canvas": "CA~2400~2400~#000000~yes~#FFFFFF~10~1200~1200~line~1~mil~1~45~visible~0.5~400~300",
           "shape": shape, "title": board.meta.get("title", board.name),
           "dataStr": {"layers": layers}}
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
    files += export_kicad_sch(board, outdir)
    files += export_eagle(board, outdir)
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
    A('(kicad_pcb (version 20260206) (generator "ocdcircuit") (generator_version "10.0")')
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
    for c in board.constraints:
        if not isinstance(c, dict) or c.get("t") != "class":
            continue
        members = sorted(n for n, net in board.nets.items()
                         if net.attrs.get("class") == c.get("name"))
        if not members:
            continue
        A(f'  (net_class {_sexp_str(str(c.get("name")))} ""'
          f' (clearance {float(cast(float, c.get("clearance", 0.2))):.4f})'
          f' (trace_width {float(cast(float, c.get("width", 0.3))):.4f})'
          + "".join(f" (add_net {_sexp_str(m)})" for m in members) + ")")
    pin_net: dict[tuple[str, str], str] = {}
    for n, net in board.nets.items():
        for r, q in net.pins:
            pin_net[(r, str(q))] = n
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        uuid = _uuid()
        A(f'  (footprint {_sexp_str(p.fp)} (layer "F.Cu") (uuid "{uuid}")')
        if p.attrs.get("dnp"):
            A('    (attr dnp)')  # KiCad excludes from BOM/PnP, like our CPL
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
        if t.via:
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
    # pours: copper zones (KiCad refills geometry on load; hatch marks intent)
    from .drc import pour_layers as _pours
    from .fab import get as _fab_get2
    zedge = float(cast(float, _fab_get2(board.fab).get("edge", 0.3)))
    zx0, zy0, zx1, zy1 = zedge, zedge, W - zedge, H - zedge
    for pname, lls in _pours(board).items():
        zid = net_ids.get(pname, 0)
        for ll in lls:
            zln = layers[ll] if ll < len(layers) else layers[0]
            A(f'  (zone (net {zid}) (net_name {_sexp_str(pname)}) (layer {_sexp_str(zln)})'
              f' (uuid "{_uuid()}") (hatch edge 0.5)')
            A(f'    (polygon (pts (xy {zx0:.4f} {zy0:.4f}) (xy {zx1:.4f} {zy0:.4f})'
              f' (xy {zx1:.4f} {zy1:.4f}) (xy {zx0:.4f} {zy1:.4f})))')
            A('    (fill (thermal_gap 0.5) (thermal_bridge_width 0.5)))')
    A(")")
    fn = os.path.join(outdir, f"{board.name}.kicad_pcb")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]


def export_altium(board: Board, outdir: str = "out") -> list[str]:
    """Write <name>.PcbDocAscii (|RECORD= lines — Altium ASCII + P-CAD
    interchange): Board verts/thickness, Nets, Components (ROTATION), Pads
    (absolute, bottom-mirrored back by the importer), Tracks (TOP/MID/
    BOTTOMLAYER), Vias (drill kept: our kicad importer drops it, this one
    doesn't). Mirrors what foreign.altium_ascii parses, so export→import
    round-trips."""
    from .parts import hole_drill, pad_size, pads_of
    os.makedirs(outdir, exist_ok=True)
    lib = board._lib()
    L: list[str] = []
    A = L.append
    W, H = board.width, board.height
    A(f"|RECORD=Board|FILENAME={board.name}.PcbDoc|BOARDTHICKNESS=1.6mm"
      f"|VX0=0mm|VY0=0mm|VX1={W}mm|VY1=0mm|VX2={W}mm|VY2={H}mm|VX3=0mm|VY3={H}mm|")
    names = sorted(board.nets)
    for n in names:
        A(f"|RECORD=Net|NAME={n}|")
    nets = {n: i for i, n in enumerate(names)}
    layers = ["TOPLAYER", "BOTTOMLAYER"] + [f"MIDLAYER{i}" for i in range(1, 31)]
    comps = sorted(board.parts.values(), key=lambda q: q.ref)
    for i, p in enumerate(comps):
        rot = p.attrs.get("rot", "0")
        A(f"|RECORD=Component|SOURCEDESIGNATOR={p.ref}|PATTERN={p.fp}"
          f"|COMMENT={p.value or p.fp}|LAYER=TOPLAYER|X={p.x}mm|Y={p.y}mm|ROTATION={rot}|")
        for pin in sorted(pads_of(p.fp, lib)):
            dx, dy = board.pad_pos(p.ref, pin)
            dr = hole_drill(p.fp, pin, lib)
            pw, ph = pad_size(p.fp, pin, lib)
            net = next((n for n, net in board.nets.items()
                        if (p.ref, str(pin)) in net.pins), "")
            ni = nets.get(net, -1)
            shape = "ROUND" if dr > 0 else "RECTANGLE"
            lay = "MULTILAYER" if dr > 0 else "TOPLAYER"
            A(f"|RECORD=Pad|NAME={pin}|COMPONENT={i}|LAYER={lay}|NET={ni}"
              f"|X={dx}mm|Y={dy}mm|XSIZE={max(pw, dr)}mm|YSIZE={max(ph, dr)}mm"
              f"|SHAPE={shape}|HOLESIZE={dr}mm|ROTATION={rot}|")
    for t in sorted(board.traces, key=lambda s: (s.net, s.layer, s.x1, s.y1, s.x2, s.y2)):
        ni = nets.get(t.net, -1)
        lay = layers[t.layer] if 0 <= t.layer < len(layers) else "TOPLAYER"
        if t.via:
            dr = getattr(t, "drill", 0.4)
            A(f"|RECORD=Via|X={t.x1}mm|Y={t.y1}mm|DIAMETER={dr + 0.4}mm"
              f"|HOLESIZE={dr}mm|STARTLAYER=TOPLAYER|ENDLAYER=BOTTOMLAYER|NET={ni}|")
        else:
            A(f"|RECORD=Track|LAYER={lay}|NET={ni}|X1={t.x1}mm|Y1={t.y1}mm"
              f"|X2={t.x2}mm|Y2={t.y2}mm|WIDTH={t.width}mm|")
    from .drc import pour_layers as _pours
    for pname, lls in sorted(_pours(board).items()):
        ni = nets.get(pname, -1)
        for ll in lls:
            lay = layers[ll] if 0 <= ll < len(layers) else "TOPLAYER"
            A(f"|RECORD=Polygon|NET={ni}|LAYER={lay}|HATCHSTYLE=Solid"
              f"|VX0=0mm|VY0=0mm|VX1={W}mm|VY1=0mm"
              f"|VX2={W}mm|VY2={H}mm|VX3=0mm|VY3={H}mm|")
    fn = os.path.join(outdir, f"{board.name}.PcbDocAscii")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]


def export_pcad(board: Board, outdir: str = "out") -> list[str]:
    """Write <name>.pcb (P-CAD ASCII, ACCEL_ASCII — Altium's own interchange:
    File > Save As > P-CAD in Altium opens it). Patterns carry pad stacks,
    compDefs bind refs, netlist nodes join pins, layerContents carries
    tracks/pours. Mirrors what foreign.pcad_ascii parses, so export→import
    round-trips."""
    from .parts import hole_drill, pad_size, pads_of
    os.makedirs(outdir, exist_ok=True)
    lib = board._lib()
    L: list[str] = []
    A = L.append
    A(f'ACCEL_ASCII "{board.name}"')
    A("(asciiHeader (asciiVersion 3 0) (fileUnits mm))")
    A('(library "ocd"')
    seen: dict[str, str] = {}  # fp -> style prefix
    for p in sorted(board.parts.values(), key=lambda q: q.fp):
        if p.fp in seen:
            continue
        seen[p.fp] = f"s{len(seen)}"
        for pin in sorted(pads_of(p.fp, lib)):
            dr = hole_drill(p.fp, pin, lib)
            pw, ph = pad_size(p.fp, pin, lib)
            st = f"{seen[p.fp]}p{pin}"
            shape = "Ellipse" if dr > 0 else "Rect"
            A(f'  (padStyleDef "{st}" (holeDiam {dr:.4f})')
            A(f'    (padShape (layerNumRef 1) (padShapeType {shape})'
              f' (shapeWidth {max(pw, dr):.4f}) (shapeHeight {max(ph, dr):.4f})))')
    for fp, pre in sorted(seen.items(), key=lambda kv: kv[1]):
        A(f'  (patternDef "{fp}" (originalName "{fp}")')
        A("    (multiLayer")
        for pin in sorted(pads_of(fp, lib)):
            dx, dy = pads_of(fp, lib)[pin][:2]
            A(f'      (pad (padNum {pin}) (padStyleRef "{pre}p{pin}") (pt {dx:.4f} {dy:.4f}))')
        A("    ))")
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        A(f'  (compDef "{p.ref}" (attachedPattern "{p.fp}"))')
    A(")")
    A('(netlist "ocd"')
    for n, net in sorted(board.nets.items()):
        A(f'  (net "{n}"')
        for r, q in net.pins:
            A(f'    (node "{r} {q}")')
        A("  )")
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        A(f'  (compInst "{p.ref}" (compRef "{p.ref}") (compValue "{p.value or p.fp}"))')
    A(")")
    A('(pcbDesign "ocd" (pcbDesignHeader (workspaceSize 200.0 150.0))')
    for i, ln in enumerate(["Top", "Bottom"][:max(board.layers, 1)], 1):
        A(f'  (layerDef "{ln}" (layerNum {i}) (layerType Signal))')
    A("  (multiLayer")
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        A(f'    (pattern "{p.ref}" (patternRef "{p.fp}") (refDesRef "{p.ref}")'
          f' (pt {p.x:.4f} {p.y:.4f}))')
    A("  )")
    for li in range(min(board.layers, 10)):
        A(f"  (layerContents (layerNumRef {li + 1})")
        for t in sorted(board.traces, key=lambda s: (s.net, s.x1, s.y1, s.x2, s.y2)):
            if t.layer != li or t.via:
                continue
            A(f'    (line (pt {t.x1:.4f} {t.y1:.4f}) (pt {t.x2:.4f} {t.y2:.4f})'
              f' (width {t.width:.4f}) (netNameRef "{t.net}"))')
        A("  )")
    A(")")
    fn = os.path.join(outdir, f"{board.name}.pcb")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]


def export_schlib(board: Board, outdir: str = "out") -> list[str]:
    """Write <name>.SchLib (native binary OLE: one storage per symbol).
    Each symbol packs its pins as binary type-1 records (y = i16 @20 in
    10mil units, orientation in payload byte 15, [nlen][name][01][desig]
    tail) plus a RECORD=1 text record — the exact shape _bin_schlib
    parses, so export→import round-trips."""
    import struct
    from .foreign import _ole_write
    os.makedirs(outdir, exist_ok=True)
    lib = dict(board.custom_sym)
    if not lib:
        raise ValueError("export_schlib: board has no symbols")
    from typing import cast
    streams: dict[str, bytes] = {}
    for name, sym in sorted(lib.items()):
        pins = cast(dict[str, tuple[str, int, str]], sym.get("pins", {}))
        recs = bytearray()
        head = f"|RECORD=1|LibReference={name}|PartCount=2|".encode("latin-1")
        recs += struct.pack("<H", len(head)) + b"\x00\x00" + head
        by_side: dict[str, list[str]] = {}
        for num, (side, order, _label) in pins.items():
            by_side.setdefault(side, []).append(num)
        order_of: dict[str, int] = {}
        for side, lst in by_side.items():
            for i, num in enumerate(sorted(lst, key=lambda q: pins[q][1])):
                order_of[num] = i
        nside = max((len(v) for v in by_side.values()), default=1)
        for num in sorted(pins, key=lambda q: (pins[q][0], pins[q][1])):
            side, _o, label = pins[num]
            slot = order_of[num]
            y = (nside - 1 - slot * 2) * 5 if side in ("left", "right") else 0
            ori = {"right": 0, "top": 1, "left": 2, "bottom": 3,
                     "up": 1, "down": 3}.get(side, 0)
            nm = (label or num).encode("latin-1", "replace")[:16]
            des = num.encode("latin-1", "replace")[:8]
            tail = bytes([len(nm)]) + nm + b"\x01" + des
            pay = bytearray(30)
            pay[15] = (pay[15] & ~3) | ori
            struct.pack_into("<h", pay, 20, max(-30000, min(30000, y)))
            pay += tail
            recs += struct.pack("<H", len(pay)) + b"\x00\x01" + bytes(pay)
        streams[f"{name}/Data"] = bytes(recs)
    fn = os.path.join(outdir, f"{board.name}.SchLib")
    open(fn, "wb").write(_ole_write(streams))
    return [fn]


def export_eagle(board: Board, outdir: str = "out") -> list[str]:
    """Write <name>.brd (Eagle XML): libraries/packages from footprints,
    elements, signals with contactrefs, Dimension wires. Mirrors what
    foreign.eagle_brd parses, so export→import round-trips."""
    from xml.sax.saxutils import escape as _esc
    from .parts import hole_drill, pad_size, pads_of
    os.makedirs(outdir, exist_ok=True)
    lib = board._lib()
    L: list[str] = []
    A = L.append
    A('<?xml version="1.0" encoding="utf-8"?>')
    A('<!DOCTYPE eagle SYSTEM "eagle.dtd">')
    A(f'<eagle version="9.6.2" generator="ocdcircuit">')
    A("<drawing><board>")
    A("<plain>")
    W, H = board.width, board.height
    for x1, y1, x2, y2 in [(0, 0, W, 0), (W, 0, W, H),
                           (W, H, 0, H), (0, H, 0, 0)]:
        A(f'<wire x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" '
          f'width="0" layer="20"/>')
    A("</plain>")
    A("<libraries><library>")
    A("<packages>")
    seen: set[str] = set()
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        if p.fp in seen:
            continue
        seen.add(p.fp)
        A(f'<package name="{_esc(p.fp)}">')
        for pin, (dx, dy) in sorted(pads_of(p.fp, lib).items()):
            dr = hole_drill(p.fp, pin, lib)
            if dr > 0:
                A(f'<pad name="{_esc(str(pin))}" x="{dx:.4f}" y="{dy:.4f}" '
                  f'drill="{dr:.4f}"/>')
            else:
                pw, ph = pad_size(p.fp, pin, lib)
                A(f'<smd name="{_esc(str(pin))}" x="{dx:.4f}" y="{dy:.4f}" '
                  f'dx="{pw:.4f}" dy="{ph:.4f}"/>')
        A("</package>")
    A("</packages></library></libraries>")
    A("<elements>")
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        A(f'<element name="{_esc(p.ref)}" package="{_esc(p.fp)}" '
          f'value="{_esc(p.value or p.fp)}" x="{p.x:.4f}" y="{p.y:.4f}"/>')
    A("</elements>")
    A("<signals>")
    from .drc import pour_layers as _eagle_pours
    from .fab import get as _eagle_fab
    _epoured = _eagle_pours(board)
    _eedge = float(cast(float, _eagle_fab(board.fab).get("edge", 0.3)))
    _eiso = float(cast(float, _eagle_fab(board.fab).get("min_space", 0.09)))
    for n in sorted(board.nets):
        net = board.nets[n]
        A(f'<signal name="{_esc(n)}">')
        for r, q in net.pins:
            A(f'<contactref element="{_esc(r)}" pad="{_esc(str(q))}"/>')
        for t in board.traces:
            if t.net != n or t.via:
                continue
            A(f'<wire x1="{t.x1:.4f}" y1="{t.y1:.4f}" x2="{t.x2:.4f}" y2="{t.y2:.4f}" '
              f'width="{t.width:.4f}" layer="{t.layer + 1}"/>')
        for ll in _epoured.get(n, []):
            x0, y0, x1, y1 = _eedge, _eedge, W - _eedge, H - _eedge
            A(f'<polygon width="0.2" layer="{ll + 1}" rank="1" pour="solid" '
              f'isolate="{_eiso:.4f}">'
              f'<vertex x="{x0:.4f}" y="{y0:.4f}"/>'
              f'<vertex x="{x1:.4f}" y="{y0:.4f}"/>'
              f'<vertex x="{x1:.4f}" y="{y1:.4f}"/>'
              f'<vertex x="{x0:.4f}" y="{y1:.4f}"/></polygon>')
        A("</signal>")
    A("</signals>")
    A("</board></drawing></eagle>")
    fn = os.path.join(outdir, f"{board.name}.brd")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]


def export_kicad_sch(board: Board, outdir: str = "out") -> list[str]:
    """Write <name>.kicad_sch: generic box symbols on the shared sch_layout
    grid (same picture as the SVG canvas), one wire per pin-to-rail drop,
    one global_label per net. Validated with `kicad-cli sch erc`."""
    import uuid as _uuid_mod
    from .plugins import sch_layout
    os.makedirs(outdir, exist_ok=True)
    lay = sch_layout(board)
    order = lay["order"]
    assert isinstance(order, list)
    px = lay["px"]
    assert isinstance(px, dict)
    rail_y = lay["rail_y"]
    assert isinstance(rail_y, dict)
    from typing import cast
    top = float(cast(float, lay["top"]))
    # KiCad schematic units are mm; our layout is ~px — scale down
    S = 0.25
    L: list[str] = []
    A = L.append
    def lib_pin(i: int, n: int) -> tuple[float, float]:
        # i-th of n pins: split across two columns; shared by lib emission
        # + wire targets (one rounding — 0.01 mismatch breaks connectivity).
        # 2.54 pitch keeps every pin on KiCad's 1.27 grid, any row count.
        rows = max(1, (n + 1) // 2)
        side = -1.0 if i < rows else 1.0
        j = i if i < rows else i - rows
        return (round(side * 7.62, 2), round(1.27 * (rows - 1 - 2 * j), 2))

    def part_pins(r: str) -> list[str]:
        return sorted({str(q) for _n, _nn in board.nets.items()
                       for rr, q in _nn.pins if rr == r})

    counts = sorted({len(part_pins(r)) for r in order})
    A('(kicad_sch (version 20250114) (generator "ocdcircuit") (generator_version "10.0")')
    A(f'  (uuid "{_uuid_mod.uuid4()}")')
    A('  (paper "A4")')
    A("  (lib_symbols")
    for n in counts:
        rows = max(1, (n + 1) // 2)
        hh = round(max(2.54, 1.27 * (rows - 1) + 1.27), 2)
        # ponytail: (pin_numbers show) with no parent pins breaks kicad-cli load
        A(f'    (symbol "ocd:box{n}" (pin_numbers hide) (in_bom yes) (on_board yes)')
        for prop, at in (("Reference", "0 2.54 0"), ("Value", "0 -2.54 0"),
                         ("Footprint", "0 -5.08 0")):
            # ponytail: hide lives INSIDE effects — trailing hide breaks load
            hide = "" if prop == "Reference" else " hide"
            A(f'      (property "{prop}" "{prop[0]}" (at {at})'
              f' (effects (font (size 1.27 1.27)){hide}))')
        A(f'      (symbol "box{n}_0_1"')
        A(f'        (rectangle (start -5.08 {-hh:.2f}) (end 5.08 {hh:.2f})')
        A('          (stroke (width 0.254) (type default)) (fill (type none)))')
        A("      )")
        if n:  # empty pin units break the loader — pinless boxes are rect-only
            A(f'      (symbol "box{n}_1_1"')
            for i in range(n):
                dx, dy = lib_pin(i, n)
                ang = 0 if dx < 0 else 180
                A(f'        (pin passive line (at {dx:.2f} {dy:.2f} {ang}) (length 2.54)'
                  f' (name "P{i + 1}" (effects (font (size 1.27 1.27))))'
                  f' (number "{i + 1}" (effects (font (size 1.27 1.27)))))')
            A("      )")
        A("    )")
    A("  )")
    def g(v: float) -> float:
        return round(v / 1.27) * 1.27  # KiCad schematic grid

    def pin_xy(i: int, n: int, cx: float, cy: float) -> tuple[float, float]:
        dx, dy = lib_pin(i, n)
        return (round(cx + dx, 2), round(cy + dy, 2))

    nets = lay["nets"]
    assert isinstance(nets, list)
    pin_pos: dict[tuple[str, str], tuple[float, float]] = {}
    for r in order:
        assert isinstance(r, str)
        p = board.parts[r]
        pins = part_pins(r)
        n = len(pins)
        sym = f"ocd:box{n}" if n else "ocd:box0"
        x, y = g(float(px[r]) * S), g(float(top - 20) * S)
        A(f'  (symbol (lib_id "{sym}") (at {x:.2f} {y:.2f} 0) (unit 1)')
        A(f'    (uuid "{_uuid_mod.uuid4()}")')
        A(f'    (property "Reference" "{r}" (at {x:.2f} {y - 5.08:.2f} 0)'
          ' (effects (font (size 1.27 1.27))))')
        A(f'    (property "Value" "{p.value or p.fp}" (at {x:.2f} {y + 5.08:.2f} 0)'
          ' (effects (font (size 1.27 1.27))))')
        A(f'    (property "Footprint" "{p.fp}" (at {x:.2f} {y + 7.62:.2f} 0)'
          ' (effects (font (size 1.27 1.27)) hide))')
        # LCSC/MPN ride as hidden properties so KiCad→JLC flows keep
        # ordering data (our CSV BOM is the primary path; this is backup).
        for _prop in ("LCSC", "MPN"):
            _v = p.attrs.get(_prop.lower(), "")
            if _v:
                A(f'    (property "{_prop}" "{_v}" (at {x:.2f} {y + 10.16:.2f} 0)'
                  ' (effects (font (size 1.27 1.27)) hide))')
        for i, q in enumerate(pins):
            A(f'    (pin "{i + 1}" (uuid "{_uuid_mod.uuid4()}"))')
            pin_pos[(r, q)] = pin_xy(i, n, x, y)
        A("  )")
    for i, n in enumerate(nets):
        y = g(float(rail_y[str(n)]) * S)
        xs = sorted(pin_pos.get((r, str(q)), (g(float(px[r]) * S), y))[0]
                    for r, q in board.nets[str(n)].pins if r in px)
        if not xs:
            continue
        # ponytail: rail as chained segments — KiCad ERC does not
        # auto-junction mid-wire T-taps, every drop lands on an endpoint
        for xa, xb in zip(xs, xs[1:]):
            A(f'  (wire (pts (xy {xa:.2f} {y:.2f}) (xy {xb:.2f} {y:.2f}))'
              ' (stroke (width 0.254) (type default))'
              f' (uuid "{_uuid_mod.uuid4()}"))')
        A(f'  (global_label "{n}" (shape input) (at {xs[0]:.2f} {y:.2f} 180)'
          ' (effects (font (size 1.27 1.27)))'
          f' (uuid "{_uuid_mod.uuid4()}"))')
        for r, q in board.nets[str(n)].pins:
            if (r, str(q)) not in pin_pos:
                continue
            ex, ey = pin_pos[(r, str(q))]
            # lib pin `at` IS the wire attach point — drop straight to rail
            A(f'  (wire (pts (xy {ex:.2f} {ey:.2f}) (xy {ex:.2f} {y:.2f}))'
              ' (stroke (width 0.254) (type default))'
              f' (uuid "{_uuid_mod.uuid4()}"))')
    A('  (sheet_instances (path "/" (page "1")))')
    A(")")
    fn = os.path.join(outdir, f"{board.name}.kicad_sch")
    open(fn, "w").write("\n".join(L) + "\n")
    return [fn]
