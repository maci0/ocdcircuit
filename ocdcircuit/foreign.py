"""Import foreign footprints + boards: KiCad .pretty/.kicad_mod + .kicad_pcb,
Eagle .lbr (packages) + .brd (full board: elements/signals/plain),
EasyEDA Std JSON (footprint + PCB docs: PAD/TRACK/VIA/LIB shapes),
tscircuit Circuit-JSON, Altium (ASCII `|RECORD=` export, native binary
.PcbDoc/.SchDoc OLE, P-CAD ASCII `ACCEL_ASCII` .pcb).
Rect/circle/oval SMD pads, PTH holes, courtyard → w/h, 3D model refs
kept as texture hints.

Usage: `fp path/to/part.kicad_mod` in .ocd — same as .fp files.
Also: Board.import_foreign(path) for whole-board netlist import (.kicad_pcb).

Altium notes: binary decode covers param streams (Board/Nets/Components/
Rules) + Tracks/Arcs/Vias/Pads/Fills primitives + Polygons6 pours +
SchDoc wires/netlabels/powerports/components. Regions6/SplitPlane layers
arrive as pour constraints (geometry refills at export); binary layouts
are reconstructed (cf. KiCad altium_parser_pcb.h), so implausible
geometry aborts pointing at the ASCII export — never silently placed.
P-CAD import covers patterns + netlist nodes + netNameRef copper +
copperPour95/pcbPoly pours + arcs/text; plane fills arrive as pours.
P-CAD Y comes in unflipped (same convention as kicad_pcb_netlist).
"""
from __future__ import annotations
import math
import os
import re
from .types import Footprint


def _tokenize(s: str) -> list[str]:
    toks: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(" or c == ")":
            toks.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            while True:
                j = s.find('"', j)
                if j < 0:
                    raise ValueError("unterminated string in s-expr")
                back, nback = j - 1, 0
                while back > i and s[back] == "\\":
                    nback += 1
                    back -= 1
                if nback % 2 == 0:
                    break
                j += 1  # escaped quote (30u\" gold) — keep scanning
            toks.append(s[i:j + 1])
            i = j + 1
        elif c == "'":
            toks.append("'")
            i += 1
        else:
            j = i
            while j < n and s[j] not in " \t\r\n()\"":
                j += 1
            toks.append(s[i:j])
            i = j
    return toks


def _parse(toks: list[str], pos: int = 0) -> tuple[list[object], int]:
    out: list[object] = []
    assert toks[pos] == "("
    pos += 1
    while pos < len(toks) and toks[pos] != ")":
        if toks[pos] == "(":
            node, pos = _parse(toks, pos)
            out.append(node)
        else:
            out.append(toks[pos])
            pos += 1
    return out, pos + 1


def _strip_comment(l: str) -> str:
    """Cut a `;` comment, but never inside a quoted string (TSOPII descr
    "...; 54 leads; ..." is one string, not a comment)."""
    in_str = False
    i, n = 0, len(l)
    while i < n:
        c = l[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == ";":
            return l[:i]
        i += 1
    return l


def sexpr(s: str) -> list[object]:
    """Parse one s-expression (skips ; comments)."""
    lines = [_strip_comment(l) for l in s.splitlines()]
    toks = _tokenize("\n".join(lines))
    if not toks or toks[0] != "(":
        raise ValueError("not an s-expression")
    node, _ = _parse(toks)
    return node


def _unq(s: object) -> str:
    t = str(s)
    return t[1:-1] if len(t) >= 2 and t.startswith('"') and t.endswith('"') else t


def _num(s: object) -> float:
    return float(_unq(s))


def _isnum(s: object) -> bool:
    try:
        float(_unq(s))
        return True
    except (ValueError, TypeError):
        return False


def _kids(node: list[object], tag: str) -> list[list[object]]:
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == tag]


def _at(el: object, key: str, default: str = "") -> str:
    """Untrusted-XML attr read: never trust Element.get typing."""
    get = getattr(el, "get", None)
    v = get(key, default) if callable(get) else default
    return v if isinstance(v, str) else default


def _fl(el: object, key: str, default: float = 0.0) -> float:
    try:
        return float(_at(el, key, ""))
    except ValueError:
        return default


def kicad_mod(text: str) -> tuple[str, Footprint]:
    """Parse .kicad_mod (`footprint`; KiCad 6+ only)."""
    root = sexpr(text)
    assert root and root[0] == "footprint", f"not a footprint: {root[:1]}"
    name = _unq(root[1]) if len(root) > 1 else "unknown"
    name = name.split(":")[-1]
    pads: dict[str, tuple[float, float, float, float]] = {}
    holes: dict[str, tuple[float, float, float]] = {}
    slots: dict[str, tuple[float, float, float, float]] = {}
    models: list[str] = []
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    def box(x: float, y: float, w: float = 0.0, h: float = 0.0) -> None:
        nonlocal minx, miny, maxx, maxy
        minx, miny = min(minx, x - w / 2), min(miny, y - h / 2)
        maxx, maxy = max(maxx, x + w / 2), max(maxy, y + h / 2)

    for pad in _kids(root, "pad"):
        # (pad NUM TYPE SHAPE (at x y [rot]) (size w h) [(drill d)] ...)
        if len(pad) < 4:
            continue
        num, typ, shape = _unq(pad[1]), _unq(pad[2]), _unq(pad[3])
        if typ in ("np_thru_hole",) or num == "":
            continue
        at = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "at"), None)
        sz = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "size"), None)
        dr = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "drill"), None)
        x = _num(at[1]) if at and len(at) > 1 else 0.0
        y = _num(at[2]) if at and len(at) > 2 else 0.0
        # NOTE: pad rotation ignored (needs footprint-level rot support)
        if typ in ("thru_hole",) or dr is not None:
            ds = [a for a in (dr[1:] if dr else []) if _isnum(a)]
            if dr and len(dr) > 1 and str(dr[1]).lower() == "oval" and len(ds) >= 2:
                # true slot: (drill oval w h [offset ...]) — milled, not drilled
                sw, sh = float(ds[0]), float(ds[1])
                slots[num] = (x, y, sw, sh)
                box(x, y, sw + 0.7, sh + 0.7)
            else:
                d = max([float(a) for a in ds] or [0.8])
                holes[num] = (x, y, d)
                box(x, y, d + 0.7, d + 0.7)
        else:
            w = _num(sz[1]) if sz and len(sz) > 1 else 1.0
            h = _num(sz[2]) if sz and len(sz) > 2 else 1.0
            if shape in ("circle",):
                w = h = max(w, h)
            pads[num] = (x, y, w, h)
            box(x, y, w, h)
    for m in _kids(root, "model"):
        if len(m) > 1:
            models.append(_unq(m[1]))
    # F/B.CrtYd rects: real courtyard (pin headers span past their pads).
    # fp_line/fp_rect/fp_circle/fp_poly on a courtyard layer → bbox union.
    crtx: list[float] = []
    crty: list[float] = []
    for tag in ("fp_rect", "fp_line", "fp_circle", "fp_poly"):
        for node in _kids(root, tag):
            lay = next((c for c in node[1:] if isinstance(c, list) and c and c[0] == "layer"), None)
            if not lay or len(lay) < 2 or "CrtYd" not in _unq(lay[1]):
                continue
            pts: list[tuple[float, float]] = []
            for c in node[1:]:
                if not isinstance(c, list) or not c:
                    continue
                if c[0] in ("start", "end", "xy") and len(c) > 2 and _isnum(c[1]) and _isnum(c[2]):
                    pts.append((_num(c[1]), _num(c[2])))
                elif c[0] == "center" and len(c) > 2:
                    pts.append((_num(c[1]), _num(c[2])))
                elif c[0] == "pts":
                    for p in c[1:]:
                        if isinstance(p, list) and p and p[0] == "xy" and len(p) > 2:
                            pts.append((_num(p[1]), _num(p[2])))
            if pts:
                crtx += [p[0] for p in pts]
                crty += [p[1] for p in pts]
    # antenna keepouts: (zone ... (keepout ...) (polygon (pts ...))) with
    # tracks/vias/pads/copperpour/footprints not_allowed → rect bbox.
    zones: list[dict[str, float | list[str]]] = []
    for z in _kids(root, "zone"):
        ko = next((c for c in z[1:] if isinstance(c, list) and c and c[0] == "keepout"), None)
        if ko is None:
            continue
        flags = {_unq(c[0]) for c in ko[1:] if isinstance(c, list) and c
                 and len(c) > 1 and _unq(c[1]) == "not_allowed"}
        if not {"tracks", "vias", "pads", "footprints"} <= flags:
            continue  # partial keepout (e.g. copperpour-only) — not routing
        poly = next((c for c in z[1:] if isinstance(c, list) and c and c[0] == "polygon"), None)
        pl = next((c for c in (poly[1:] if poly else []) if isinstance(c, list) and c and c[0] == "pts"),
                  poly)
        pts = [(_num(p[1]), _num(p[2])) for p in (pl[1:] if pl else [])
               if isinstance(p, list) and p and p[0] == "xy" and len(p) > 2]
        if len(pts) >= 2:
            zx = [p[0] for p in pts]
            zy = [p[1] for p in pts]
            zones.append({"dx": (min(zx) + max(zx)) / 2, "dy": (min(zy) + max(zy)) / 2,
                          "w": max(zx) - min(zx), "h": max(zy) - min(zy), "layers": []})
    if minx == float("inf"):
        minx, miny, maxx, maxy = 0.0, 0.0, 1.0, 1.0
    if crtx:
        # courtyard wins over pad bbox (headers, USB shells, tall bodies)
        minx, miny = min(crtx), min(crty)
        maxx, maxy = max(crtx), max(crty)
    wdt, hgt = max(1.0, maxx - minx + 1.0), max(1.0, maxy - miny + 1.0)
    # recenter pads/holes on centroid
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    pads = {k: (v[0] - cx, v[1] - cy, v[2], v[3]) for k, v in pads.items()}
    holes = {k: (v[0] - cx, v[1] - cy, v[2]) for k, v in holes.items()}
    slots = {k: (v[0] - cx, v[1] - cy, v[2], v[3]) for k, v in slots.items()}
    from typing import cast
    zones = [{"dx": cast(float, z["dx"]) - cx, "dy": cast(float, z["dy"]) - cy,
                "w": cast(float, z.get("w", 0.0)), "h": cast(float, z.get("h", 0.0)),
                "layers": cast(list[str], z.get("layers", []))}
             for z in zones]
    fp: Footprint = {"w": wdt, "h": hgt, "pads": pads, "holes": holes,
                     "bodies": [{"box": (wdt - 1.0, hgt - 1.0, 1.0)}]}
    if slots:
        fp["slots"] = slots
    if models:
        fp["models"] = models  # STEP/WRL refs → texture/model hints
    if zones:
        fp["keepouts"] = zones
    return name, fp


def eagle_lbr(text: str) -> list[tuple[str, Footprint]]:
    """Parse Eagle .lbr XML → [(name, fp)]. SMD pads, PTH pads, holes."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text)
    out: list[tuple[str, Footprint]] = []
    for pkg in root.iter("package"):
        name = _at(pkg, "name", "unknown")
        pads: dict[str, tuple[float, float, float, float]] = {}
        holes: dict[str, tuple[float, float, float]] = {}
        minx = miny = float("inf")
        maxx = maxy = float("-inf")

        def box(x: float, y: float, w: float, h: float) -> None:
            nonlocal minx, miny, maxx, maxy
            minx, miny = min(minx, x - w / 2), min(miny, y - h / 2)
            maxx, maxy = max(maxx, x + w / 2), max(maxy, y + h / 2)

        for smd in pkg.iter("smd"):
            n = _at(smd, "name", "?")
            x, y, w, h = (_fl(smd, "x"), _fl(smd, "y"),
                          _fl(smd, "dx", 1.0), _fl(smd, "dy", 1.0))
            pads[n] = (x, y, w, h)
            box(x, y, w, h)
        for pad in pkg.iter("pad"):
            n = _at(pad, "name", "?")
            x, y, d = _fl(pad, "x"), _fl(pad, "y"), _fl(pad, "drill", 0.8)
            holes[n] = (x, y, d)
            box(x, y, d + 0.7, d + 0.7)
        for hole in pkg.iter("hole"):
            n = f"H{len(holes) + 1}"
            x, y, d = _fl(hole, "x"), _fl(hole, "y"), _fl(hole, "drill", 1.0)
            holes[n] = (x, y, d)
            box(x, y, d + 0.7, d + 0.7)
        if minx == float("inf"):
            minx, miny, maxx, maxy = -0.5, -0.5, 0.5, 0.5
        wdt, hgt = max(1.0, maxx - minx + 1.0), max(1.0, maxy - miny + 1.0)
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        pads = {k: (v[0] - cx, v[1] - cy, v[2], v[3]) for k, v in pads.items()}
        holes = {k: (v[0] - cx, v[1] - cy, v[2]) for k, v in holes.items()}
        out.append((name, {"w": wdt, "h": hgt, "pads": pads, "holes": holes,
                           "bodies": [{"box": (wdt - 1.0, hgt - 1.0, 1.0)}]}))
    if not out:
        raise ValueError("no <package> in Eagle library")
    return out


def eagle_brd(text: str) -> dict[str, object]:
    """Eagle .brd XML → IR dict (agent.from_ir): elements + signals + wires.
    Footprints rebuilt per element from the library packages (relative pads
    recentered on the element); board size from the Dimension layer bbox."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text)
    drawing = root.find("drawing")
    assert drawing is not None, "not an Eagle .brd (no <drawing>)"
    board_el = drawing.find("board")
    assert board_el is not None, "not an Eagle .brd (no <board>)"
    # library packages: {name: (pads, holes)} in package frame
    pkgs: dict[str, tuple[dict[str, tuple[float, float, float, float]],
                          dict[str, tuple[float, float, float]]]] = {}
    for pkg in root.iter("package"):
        name = _at(pkg, "name", "unknown")
        pads: dict[str, tuple[float, float, float, float]] = {}
        holes: dict[str, tuple[float, float, float]] = {}
        for smd in pkg.iter("smd"):
            n = _at(smd, "name", "?")
            pads[n] = (_fl(smd, "x"), _fl(smd, "y"),
                       _fl(smd, "dx", 1.0), _fl(smd, "dy", 1.0))
        for pad in pkg.iter("pad"):
            n = _at(pad, "name", "?")
            holes[n] = (_fl(pad, "x"), _fl(pad, "y"), _fl(pad, "drill", 0.8))
        pkgs[name] = (pads, holes)
    plains = board_el.find("plain")
    wires = list(plains.iter("wire")) if plains is not None else []
    xs: list[float] = []
    ys: list[float] = []
    for w in wires:
        if _at(w, "layer") == "20":
            xs += [_fl(w, "x1"), _fl(w, "x2")]
            ys += [_fl(w, "y1"), _fl(w, "y2")]
    wdt = max(xs) - min(xs) if xs else 40.0
    hgt = max(ys) - min(ys) if ys else 30.0
    parts: list[dict[str, object]] = []
    nets: dict[str, dict[str, object]] = {}
    fps: dict[str, Footprint] = {}
    elements = board_el.find("elements")
    for el in elements.iter("element") if elements is not None else []:
        ref = _at(el, "name") or f"U{len(parts) + 1}"
        pkgname = _at(el, "package", "unknown")
        x, y = _fl(el, "x"), _fl(el, "y")
        pads, holes = pkgs.get(pkgname, ({}, {}))
        if pads or holes:
            xs2 = [v[0] for v in pads.values()] + [v[0] for v in holes.values()]
            ys2 = [v[1] for v in pads.values()] + [v[1] for v in holes.values()]
            fw = max(1.0, (max(xs2) - min(xs2) + 2.0)) if xs2 else 2.0
            fh = max(1.0, (max(ys2) - min(ys2) + 2.0)) if ys2 else 2.0
        else:
            fw, fh = 2.0, 2.0
        fps.setdefault(pkgname, {"w": fw, "h": fh, "pads": pads, "holes": holes,
                             "bodies": []})
        parts.append({"ref": ref, "fp": pkgname,
                      "value": _at(el, "value", pkgname),
                      "x": x, "y": y})
    signals = board_el.find("signals")
    for sig in signals.iter("signal") if signals is not None else []:
        nn = _at(sig, "name") or f"N{len(nets) + 1}"
        entry = nets.setdefault(nn, {"pins": [], "layer": None, "width": 0.3})
        pins = entry["pins"]
        assert isinstance(pins, list)
        for c in sig.iter("contactref"):
            pins.append([_at(c, "element", "?"), _at(c, "pad", "?")])
    return {"board": {"name": "imported", "w": wdt, "h": hgt, "layers": 2},
            "parts": parts, "nets": nets, "constraints": [],
            "_imported_fp": fps}


def tscircuit_json(doc: object) -> list[tuple[str, Footprint]]:
    """tscircuit Circuit-JSON / snippet soup: [{type:'pcb_smtpad'...}] or
    {footprints:[...]}. Best-effort pad extraction."""
    out: list[tuple[str, Footprint]] = []
    elems: list[dict[str, object]] = []
    if isinstance(doc, dict):
        for key in ("footprints", "elements"):
            v = doc.get(key)
            if isinstance(v, list):
                elems = [e for e in v if isinstance(e, dict)]
                break
    elif isinstance(doc, list):
        elems = [e for e in doc if isinstance(e, dict)]
    by_fp: dict[str, dict[str, list[tuple[str, float, float, float, float]]]] = {}
    by_hole: dict[str, list[tuple[str, float, float, float]]] = {}

    def fnum(e: dict[str, object], *keys: str, default: float = 0.0) -> float:
        for k in keys:
            v = e.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return default

    for e in elems:
        t = str(e.get("type", ""))
        fp = str(e.get("footprint", e.get("name", "tsc")))
        if t in ("pcb_smtpad", "smtpad"):
            ph = e.get("port_hints")
            num = str(ph[0]) if isinstance(ph, list) and ph else str(
                e.get("pin", len(by_fp.get(fp, {"pads": []})["pads"]) + 1))
            by_fp.setdefault(fp, {"pads": []})["pads"].append(
                (num, fnum(e, "x"), fnum(e, "y"),
                 fnum(e, "width", "w", default=1.0), fnum(e, "height", "h", default=1.0)))
        elif t in ("pcb_platedhole", "platedhole", "hole"):
            num = str(e.get("pin", len(by_hole.get(fp, [])) + 1))
            by_hole.setdefault(fp, []).append(
                (num, fnum(e, "x"), fnum(e, "y"), fnum(e, "outer_diameter", "drill", default=0.8)))
    for fp, d in by_fp.items():
        pads = {p[0]: (p[1], p[2], p[3], p[4]) for p in d["pads"]}
        holes = {p[0]: (p[1], p[2], p[3]) for p in by_hole.get(fp, [])}
        xs = [v[0] for v in pads.values()] + [v[0] for v in holes.values()]
        ys = [v[1] for v in pads.values()] + [v[1] for v in holes.values()]
        wdt = max(1.0, (max(xs) - min(xs) + 2.0)) if xs else 2.0
        hgt = max(1.0, (max(ys) - min(ys) + 2.0)) if ys else 2.0
        out.append((fp, {"w": wdt, "h": hgt, "pads": pads, "holes": holes,
                         "bodies": [{"box": (wdt - 1.0, hgt - 1.0, 1.0)}]}))
    for fp, hs in by_hole.items():
        if fp in by_fp:
            continue
        holes = {p[0]: (p[1], p[2], p[3]) for p in hs}
        xs = [v[0] for v in holes.values()]
        ys = [v[1] for v in holes.values()]
        wdt = max(1.0, (max(xs) - min(xs) + 2.0)) if xs else 2.0
        hgt = max(1.0, (max(ys) - min(ys) + 2.0)) if ys else 2.0
        out.append((fp, {"w": wdt, "h": hgt, "pads": {}, "holes": holes,
                         "bodies": [{"box": (wdt - 1.0, hgt - 1.0, 1.0)}]}))
    if not out:
        raise ValueError("no pads found in Circuit-JSON")
    return out


def easyeda_doc(doc: dict[str, object]) -> object:
    """EasyEDA Std JSON → footprints (docType 4) or board IR (docType 3).
    Shape strings are `~`-delimited; PCB units are 10-mil (×0.254 = mm)."""
    shape = doc.get("shape", [])
    assert isinstance(shape, list)
    head = str(doc.get("head", ""))
    dtype = head.split("~")[0] if head else ""
    # LIB compounds join children with #@$ — split those first, then ~.
    lines: list[list[str]] = []
    for s in shape:
        if not isinstance(s, str):
            continue
        for seg in s.split("#@$"):
            lines.append(seg.split("~"))
    mm = 0.254
    if dtype == "4":  # footprint: PAD shapes in package frame
        pads: dict[str, tuple[float, float, float, float]] = {}
        holes: dict[str, tuple[float, float, float]] = {}
        xs: list[float] = []
        ys: list[float] = []
        for f in lines:
            if f[0] != "PAD" or len(f) < 11:
                continue
            try:
                _sh, px, py, pw, ph = f[1], float(f[2]), float(f[3]), float(f[4]), float(f[5])
                num = f[8] or str(len(pads) + len(holes) + 1)
                hole = float(f[9]) if len(f) > 9 and f[9] else 0.0
            except ValueError:
                continue
            if f[1] == "OVAL" and abs(pw - ph) < 1e-9 and hole > 0:
                holes[num] = (px * mm, py * mm, hole * 2 * mm)
            elif hole > 0 or (len(f) > 6 and f[6] == "11"):
                holes[num] = (px * mm, py * mm, max(hole * 2, 0.8) * mm)
            else:
                pads[num] = (px * mm, py * mm, pw * mm, ph * mm)
            xs.append(px * mm)
            ys.append(py * mm)
        cx = (max(xs) + min(xs)) / 2 if xs else 0.0
        cy = (max(ys) + min(ys)) / 2 if ys else 0.0
        pads = {k: (v[0] - cx, v[1] - cy, v[2], v[3]) for k, v in pads.items()}
        holes = {k: (v[0] - cx, v[1] - cy, v[2]) for k, v in holes.items()}
        wdt = max(2.0, (max(xs) - min(xs) + 2.0)) if xs else 2.0
        hgt = max(2.0, (max(ys) - min(ys) + 2.0)) if ys else 2.0
        title = str(doc.get("title", "easyeda"))
        return [(title, {"w": wdt, "h": hgt, "pads": pads, "holes": holes,
                         "bodies": []})]
    # PCB doc: LIB footprints (children joined with #@$) + TRACK/VIA
    parts: list[dict[str, object]] = []
    nets: dict[str, dict[str, object]] = {}
    fps: dict[str, Footprint] = {}
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    def _pin(net: str, ref: str, num: str) -> None:
        if not net:
            return
        entry = nets.setdefault(net, {"pins": [], "layer": None, "width": 0.3})
        pins = entry["pins"]
        assert isinstance(pins, list)
        pins.append([ref, num])

    # PAD lines following a LIB belong to it (children were joined with #@$)
    cur: tuple[str, str, float, float,
               dict[str, tuple[float, float, float, float]],
               dict[str, tuple[float, float, float]]] | None = None

    def _flush() -> None:
        if cur is None:
            return
        _, pkg, _, _, pads, holes = cur
        if pkg not in fps:
            xs = [v[0] for v in list(pads.values()) + list(holes.values())]
            ys = [v[1] for v in list(pads.values()) + list(holes.values())]
            fw = max(2.0, (max(xs) - min(xs) + 2.0)) if xs else 2.0
            fh = max(2.0, (max(ys) - min(ys) + 2.0)) if ys else 2.0
            fps[pkg] = {"w": fw, "h": fh, "pads": dict(pads),
                        "holes": dict(holes), "bodies": []}
        else:
            fp = fps[pkg]
            assert isinstance(fp, dict)
            fp_pads = fp.setdefault("pads", {})
            fp_holes = fp.setdefault("holes", {})
            assert isinstance(fp_pads, dict) and isinstance(fp_holes, dict)
            fp_pads.update(pads)
            fp_holes.update(holes)

    for f in lines:
        if f[0] == "LIB" and len(f) >= 4:
            _flush()
            try:
                lx, ly = float(f[1]), float(f[2])
            except ValueError:
                cur = None
                continue
            params = f[3] if len(f) > 3 else ""
            kv = params.split("`")
            meta = dict(zip(kv[::2], kv[1::2])) if len(kv) >= 2 else {}
            ref = meta.get("name", f"U{len(parts) + 1}")
            pkg = meta.get("package", ref)
            cur = (ref, pkg, lx, ly, {}, {})
            # DNP: EasyEDA Std has no native field; the community
            # convention is a Fitted=Y/N parameter (forum). Honor it.
            pattrs: dict[str, str] = {}
            if str(meta.get("Fitted", "Y")).upper() == "N":
                pattrs["dnp"] = "1"
            parts.append({"ref": ref, "fp": pkg, "value": meta.get("name", pkg),
                          "x": lx * mm, "y": ly * mm, "attrs": pattrs})
            minx, miny = min(minx, lx * mm), min(miny, ly * mm)
            maxx, maxy = max(maxx, lx * mm), max(maxy, ly * mm)
            continue
        if cur is None or f[0] != "PAD" or len(f) < 11:
            continue  # TRACK copper re-routes; nets come from PAD assigns
        ref, _, lx, ly, pads, holes = cur
        try:
            px, py, pw, ph = float(f[2]), float(f[3]), float(f[4]), float(f[5])
            num = f[8] or str(len(pads) + len(holes) + 1)
            hole = float(f[9]) if len(f) > 9 and f[9] else 0.0
        except ValueError:
            continue
        if hole > 0 or (len(f) > 6 and f[6] == "11"):
            holes[num] = (px * mm - lx * mm, py * mm - ly * mm,
                          max(hole * 2, 0.8) * mm)
        else:
            pads[num] = (px * mm - lx * mm, py * mm - ly * mm, pw * mm, ph * mm)
        _pin(f[7] if len(f) > 7 else "", ref, num)
    _flush()
    wdt = max(10.0, maxx - minx + 5.0) if parts else 40.0
    hgt = max(10.0, maxy - miny + 5.0) if parts else 30.0
    return {"board": {"name": str(doc.get("title", "imported")),
                      "w": wdt, "h": hgt, "layers": 2},
            "parts": parts, "nets": nets, "constraints": [],
            "_imported_fp": fps}


def _sch_pin(p: bytes) -> tuple[str | None, str | None]:
    """Binary SchLib pin payload → (designator, name). Tail holds
    [nlen][name][01][desig]; scan for the 01 marker near the end
    (names are leading-alpha alnum). (None, None) when absent —
    caller skips, never invents pins."""
    for i in range(len(p) - 3, max(0, len(p) - 40), -1):
        if p[i] == 0x01 and 32 < p[i + 1] < 127:
            des = chr(p[i + 1])
            for k in range(max(0, i - 34), i):
                n = p[k]
                if (1 <= n <= 16 and k + 1 + n == i and chr(p[k + 1]).isalpha()
                        and all(chr(c).isalnum() or chr(c) in "_/-"
                                for c in p[k + 1:k + 1 + n])):
                    return des, p[k + 1:k + 1 + n].decode("latin-1")
    return None, None


def _bin_schlib(data: bytes) -> list[tuple[str, dict[str, object]]]:
    """Native binary .SchLib → [(name, symbol)]. Each top-level storage is
    one symbol: text records (component/params/rect) + binary pin records
    (type byte 1: payload byte 15 low 2 bits = TRotateBy90 orientation —
    0 right, 1 up, 2 left, 3 down; tail [len][chars] pairs give name,
    designator). Vertical pins land top/bottom by y-sign; horizontal by
    orientation. Verified on TSOP-8 (4L/4R) + Cyclone-V (225L/671R)."""
    import struct
    paths, _, _, _, _, _ = _ole_dir(data)
    libs: dict[str, list[str]] = {}
    for p in paths:
        if "/" in p:
            libs.setdefault(p.split("/")[0], []).append(p)
    out: list[tuple[str, dict[str, object]]] = []
    for lib, members in libs.items():
        dpath = lib + "/Data"
        if dpath not in paths:
            continue
        raw = _ole_stream(paths, dpath)
        if not raw:
            continue
        buf = bytes(raw)
        pins: list[tuple[str, str, str]] = []  # (designator, name, side)
        name = lib
        j = 0
        while j + 4 <= len(buf):
            ln = struct.unpack("<H", buf[j:j + 2])[0]
            if ln == 0 or ln > len(buf) - j - 4:
                break
            if buf[j + 2] == 0 and buf[j + 3] == 0:
                t = buf[j + 4:j + 4 + ln].decode("latin-1", "replace")
                r = _arec(t)
                if r.get("RECORD") == "1" and r.get("LIBREFERENCE"):
                    name = r["LIBREFERENCE"]
            elif buf[j + 2] == 0 and buf[j + 3] == 1:
                prec = bytes(buf[j + 4:j + 4 + ln])
                des, nm = _sch_pin(prec)
                if nm is not None and des is not None:
                    ori = prec[15] & 3 if len(prec) > 15 else 0
                    y = struct.unpack("<h", prec[20:22])[0] if len(prec) > 22 else 0
                    side = ("left" if ori == 2 else "right" if ori == 0
                            else "top" if (ori == 1) == (y >= 0) else "bottom")
                    pins.append((des, nm, side))
            j += 4 + ln
        if not pins:
            continue
        sympins: dict[str, tuple[str, int, str]] = {}
        counts = {"left": 0, "right": 0, "top": 0, "bottom": 0}
        for des, nm, side in pins:
            sympins[des] = (side, counts[side], nm)
            counts[side] += 1
        lr = max(counts["left"], counts["right"], 1)
        tb = max(counts["top"], counts["bottom"], 0)
        sym: dict[str, object] = {"w": max(12.0, tb * 2.0 + 4.0),
                                  "h": max(4.0, lr * 2.0 + 2.0),
                                  "pins": sympins, "notch": False,
                                  "zigzag": False, "label": "{ref} {value}"}
        out.append((name, sym))
    if not out:
        raise ValueError("altium binary: no symbols with pins found")
    return out


def _bin_pcblib(data: bytes) -> list[tuple[str, Footprint]]:
    """Native binary .PcbLib → [(name, footprint)]. Each top-level storage
    is one footprint: its Data stream opens with [u8 namelen][name], then
    PcbDoc-framed primitives (tracks/arcs/texts + six-block pads, located
    by scanning for 0x02 starts that decode to sane geometry — the lib
    pad framing differs from PcbDoc's, but the geometry block is shared).
    Pads arrive footprint-relative already (no component transform)."""
    import struct
    paths, _, _, _, _, _ = _ole_dir(data)
    libs: dict[str, list[str]] = {}
    for p in paths:
        if "/" in p:
            libs.setdefault(p.split("/")[0], []).append(p)
    out: list[tuple[str, Footprint]] = []
    for lib, members in libs.items():
        dpath = lib + "/Data"
        if dpath not in paths:
            continue
        buf = _ole_stream(paths, dpath)
        if not buf or len(buf) < 6:
            continue
        nl = buf[4]
        if 5 + nl > len(buf):
            continue
        name = buf[5:5 + nl].decode("latin-1", "replace").strip() or lib
        pads: dict[str, tuple[float, float, float, float]] = {}
        holes: dict[str, tuple[float, float, float]] = {}
        for j in [i for i in range(len(buf)) if buf[i] == 2]:
            try:
                k = j + 1
                blocks = []
                for _ in range(6):
                    ln = struct.unpack("<I", buf[k:k + 4])[0]
                    if ln > 2000 or k + 4 + ln > len(buf):
                        raise ValueError
                    blocks.append(buf[k + 4:k + 4 + ln])
                    k += 4 + ln
                g = blocks[4]
                h = _bin_head(g)
                pos = _bin_pt(g, 13)
                w = struct.unpack("<i", g[21:25])[0] * _IU
                hgt = struct.unpack("<i", g[25:29])[0] * _IU
                hole = struct.unpack("<i", g[45:49])[0] * _IU if len(g) >= 49 else 0.0
                if (h is None or pos is None or not 0 < w <= 100
                        or not 0 < hgt <= 100
                        or not all(-500 <= v <= 500 for v in pos)):
                    continue
                n = blocks[0][0] if blocks[0] else 0
                num = blocks[0][1:1 + n].decode("latin-1", "replace").rstrip("\x00") \
                    or str(len(pads) + len(holes) + 1)
                if hole > 0:
                    holes[num] = (pos[0], pos[1], hole)
                else:
                    pads[num] = (pos[0], pos[1], w, hgt)
            except (ValueError, struct.error, IndexError):
                continue
        if not pads and not holes:
            continue
        xs = [v[0] for v in list(pads.values()) + list(holes.values())]
        ys = [v[1] for v in list(pads.values()) + list(holes.values())]
        fp: Footprint = {"w": max(1.0, max(xs) - min(xs) + 2.0),
                         "h": max(1.0, max(ys) - min(ys) + 2.0),
                         "pads": pads, "holes": holes, "bodies": []}
        from .parts import FOOTPRINTS as _STD
        if name in _STD:
            name = "altium:" + name  # std lib wins; lib version kept reachable
        out.append((name, fp))
    if not out:
        raise ValueError("altium binary: no footprint patterns with pads found")
    return out


def load_foreign(path: str) -> list[tuple[str, Footprint]]:
    """Dispatch by extension: .kicad_mod/.pretty, .lbr, .json, .PcbLib."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pcblib":
        with open(path, "rb") as f:
            return _bin_pcblib(f.read())
    with open(path) as f:
        text = f.read()
    if ext in (".kicad_mod", ".pretty"):
        return [kicad_mod(text)]
    if ext == ".lbr":
        return eagle_lbr(text)
    if ext == ".json":
        import json
        doc = json.loads(text)
        if isinstance(doc, dict) and "shape" in doc:
            out = easyeda_doc(doc)
            assert isinstance(out, list)
            return out
        return tscircuit_json(doc)
    # fall back to native .fp
    from .footprint import loads
    name, fp = loads(text)
    return [(name, fp)]


def _footprint_ref(fpnode: list[object]) -> str:
    """Reference designator: fp_text reference, else property Reference,
    else our user text."""
    for t in _kids(fpnode, "fp_text"):
        if len(t) > 2 and _unq(t[1]) == "reference":
            return _unq(t[2])
    for t in _kids(fpnode, "property"):
        if len(t) > 2 and _unq(t[1]) == "Reference":
            return _unq(t[2])
    for t in _kids(fpnode, "fp_text"):
        if len(t) > 2 and _unq(t[1]) == "user":
            return _unq(t[2])
    return ""


def _safe_id(s: str, fallback: str) -> str:
    """Foreign ref/net → .ocd-safe id (dumps split on whitespace; refs are
    \\w+): sanitize at the import boundary so core stays strict."""
    out = re.sub(r"\s+", "_", s.strip())
    if re.fullmatch(r"\w+", out or ""):
        return out
    return re.sub(r"\W", "_", out) or fallback


def kicad_pcb_netlist(text: str) -> dict[str, object]:
    """Import netlist from .kicad_pcb s-expr: footprints + pads→nets.
    Returns IR dict loadable via agent.from_ir (positions preserved)."""
    root = sexpr(text)
    assert root and root[0] == "kicad_pcb", "not a .kicad_pcb"
    netnames: dict[str, str] = {}
    for net in _kids(root, "net"):
        if len(net) >= 3:
            netnames[str(net[1])] = _unq(net[2])
    parts: list[dict[str, object]] = []
    nets: dict[str, dict[str, object]] = {}
    fps: dict[str, Footprint] = {}
    for fpnode in _kids(root, "footprint"):
        ref = _safe_id(_footprint_ref(fpnode), f"U{len(parts) + 1}")
        if any(str(p["ref"]) == ref for p in parts):
            ref = f"{ref}_{len(parts) + 1}"
        at = next((c for c in fpnode[1:] if isinstance(c, list) and c and c[0] == "at"), None)
        x = _num(at[1]) if at and len(at) > 1 else 0.0
        y = _num(at[2]) if at and len(at) > 2 else 0.0
        fpname = _unq(fpnode[1]) if len(fpnode) > 1 else "unknown"
        # rebuild a footprint from its pads
        pads: dict[str, tuple[float, float, float, float]] = {}
        holes: dict[str, tuple[float, float, float]] = {}
        for pad in _kids(fpnode, "pad"):
            if len(pad) < 4:
                continue
            num = _unq(pad[1])
            typ = _unq(pad[2]) if len(pad) > 2 else ""
            if typ in ("np_thru_hole",) or num == "":
                continue  # unplated/unnumbered: no pin to connect
            pat = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "at"), None)
            psz = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "size"), None)
            pdr = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "drill"), None)
            px = _num(pat[1]) - x if pat and len(pat) > 1 else 0.0
            py = _num(pat[2]) - y if pat and len(pat) > 2 else 0.0
            pnet = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "net"), None)
            nid = str(pnet[1]) if pnet and len(pnet) > 1 else "0"
            if pdr is not None:
                nums = [a for a in pdr[1:] if _isnum(a)]
                holes[num] = (px, py, max([float(_unq(a)) for a in nums] or [0.8]))
            else:
                pw = _num(psz[1]) if psz and len(psz) > 1 else 1.0
                ph = _num(psz[2]) if psz and len(psz) > 2 else 1.0
                pads[num] = (px, py, pw, ph)
            if nid != "0":
                nn = _safe_id(netnames.get(nid, f"N{nid}"), f"N{nid}")
                entry = nets.setdefault(nn, {"pins": [], "layer": None, "width": 0.3})
                pins = entry["pins"]
                assert isinstance(pins, list)
                pins.append([ref, num])
        wdt = max([abs(v[0]) + v[2] for v in pads.values()] + [0.0]) * 2 + 1.0
        hgt = max([abs(v[1]) + v[3] for v in pads.values()] + [0.0]) * 2 + 1.0
        fps.setdefault(fpname, {"w": wdt, "h": hgt, "pads": pads,
                                "holes": holes, "bodies": []})
        parts.append({"ref": ref, "fp": fpname, "value": fpname,
                      "x": x, "y": y})
    # layer count from the (layers ...) decl (copper only, capped at 32)
    ncu = 2
    for lay in _kids(root, "layers"):
        cu = [c for c in lay[1:] if isinstance(c, list) and c
              and str(_unq(c[1]) if len(c) > 1 else "").endswith(".Cu")]
        if cu:
            ncu = min(max(len(cu), 1), 32)
    # board size from the Edge.Cuts outline (gr_line/gr_arc/gr_circle plus
    # footprint-relative fp_line/fp_arc/fp_circle); parts recentered onto it
    xs: list[float] = []
    ys: list[float] = []

    def _edge_pts(node: list[object], ox: float = 0.0, oy: float = 0.0) -> None:
        nonlocal xs, ys
        for tag in ("gr_line", "fp_line"):
            for gr in _kids(node, tag):
                lay = next((c for c in gr[1:] if isinstance(c, list) and c and c[0] == "layer"), None)
                if lay is None or "Edge.Cuts" not in str(lay[1] if len(lay) > 1 else ""):
                    continue
                for end in ("start", "end"):
                    pt = next((c for c in gr[1:] if isinstance(c, list) and c and c[0] == end), None)
                    if pt and len(pt) > 2 and _isnum(pt[1]) and _isnum(pt[2]):
                        xs.append(_num(pt[1]) + ox)
                        ys.append(_num(pt[2]) + oy)
        for tag in ("gr_arc", "fp_arc"):
            for gr in _kids(node, tag):
                lay = next((c for c in gr[1:] if isinstance(c, list) and c and c[0] == "layer"), None)
                if lay is None or "Edge.Cuts" not in str(lay[1] if len(lay) > 1 else ""):
                    continue
                for end in ("start", "mid", "end"):
                    pt = next((c for c in gr[1:] if isinstance(c, list) and c and c[0] == end), None)
                    if pt and len(pt) > 2 and _isnum(pt[1]) and _isnum(pt[2]):
                        xs.append(_num(pt[1]) + ox)
                        ys.append(_num(pt[2]) + oy)
        for tag in ("gr_circle", "fp_circle"):
            for gr in _kids(node, tag):
                lay = next((c for c in gr[1:] if isinstance(c, list) and c and c[0] == "layer"), None)
                if lay is None or "Edge.Cuts" not in str(lay[1] if len(lay) > 1 else ""):
                    continue
                c = next((x for x in gr[1:] if isinstance(x, list) and x and x[0] == "center"), None)
                e = next((x for x in gr[1:] if isinstance(x, list) and x and x[0] == "end"), None)
                if c and e and len(c) > 2 and len(e) > 2:
                    r = abs(_num(e[1]) - _num(c[1]))
                    xs += [_num(c[1]) - r + ox, _num(c[1]) + r + ox]
                    ys += [_num(c[2]) - r + oy, _num(c[2]) + r + oy]

    _edge_pts(root)
    for fpnode in _kids(root, "footprint"):
        at = next((c for c in fpnode[1:] if isinstance(c, list) and c and c[0] == "at"), None)
        _edge_pts(fpnode, _num(at[1]) if at and len(at) > 1 else 0.0,
                  _num(at[2]) if at and len(at) > 2 else 0.0)
    if not xs:  # no outline: fall back to part extents
        for p in parts:
            px, py = float(str(p["x"])), float(str(p["y"]))
            xs += [px - 1.0, px + 1.0]
            ys += [py - 1.0, py + 1.0]
    wdt = max(xs) - min(xs) if xs else 40.0
    hgt = max(ys) - min(ys) if ys else 30.0
    if xs:  # parts carry absolute KiCad coords — recenter onto the outline
        ox, oy = min(xs), min(ys)
        for p in parts:
            p["x"], p["y"] = float(str(p["x"])) - ox, float(str(p["y"])) - oy
    return {"board": {"name": "imported", "w": wdt, "h": hgt, "layers": ncu},
            "parts": parts, "nets": nets, "constraints": [],
            "_imported_fp": fps}


# -- Altium ASCII (.PcbDoc text export) + P-CAD ASCII (.pcb) --

def _alen(v: str) -> float:
    """Altium length field → mm (10mm / 250mil / 10000nm / 2.54cm / 25.4in)."""
    v = v.strip().lower()
    for suf, mul in (("mm", 1.0), ("mil", 0.0254), ("nm", 1e-6),
                     ("cm", 10.0), ("in", 25.4)):
        if v.endswith(suf):
            try:
                return float(v[:-len(suf)]) * mul
            except ValueError:
                return 0.0
    try:
        return float(v)  # bare number: mm (matches the ASCII writer)
    except ValueError:
        return 0.0


def _arec(line: str) -> dict[str, str]:
    """One `|RECORD=X|K=V|…` line → {KEY: value} (keys uppercased)."""
    out: dict[str, str] = {}
    for part in line.split("|"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().upper()
        if k:
            out[k] = v.strip()
    return out


def _alayers() -> list[str]:
    """ASCII copper layer table: TOP/BOTTOM + MIDn + INTERNALPLANEn.
    One table for import (_lyr), export, and pour mapping — a layer that
    misses it lands on TOP (0) instead of vanishing."""
    return (["TOPLAYER", "BOTTOMLAYER"] + [f"MIDLAYER{i}" for i in range(1, 31)]
            + [f"INTERNALPLANE{i}" for i in range(1, 17)])


def _askip() -> dict[str, object]:
    return {"_skipped": []}  # pours/planes/arcs/text now import; nothing skipped


def _alyr_idx(lay: str) -> int | None:
    try:
        return _alayers().index(lay.upper())
    except ValueError:
        short = {"TOP": 0, "BOTTOM": 1}.get(lay.upper())
        return short


_OLE_END, _OLE_FREE, _OLE_SAT = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD
_OLE_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"


def _ole_dir(data: bytes) -> tuple[dict[str, tuple[int, int]], bytes, list[int], int, int, int]:
    """OLE compound file → ({path: (start, size)}, ministream, minifat,
    minisec, cutoff). Stdlib only (struct). Raises ValueError, never garbage."""
    import struct
    if len(data) < 512:
        raise ValueError("not an OLE compound file (truncated)")
    if data[:8] != _OLE_MAGIC:
        raise ValueError("not an OLE compound file")
    ssz = 1 << struct.unpack("<H", data[30:32])[0]
    msz = 1 << struct.unpack("<H", data[32:34])[0]
    nfat = struct.unpack("<I", data[44:48])[0]
    seen = list(struct.unpack("<109I", data[76:76 + 436]))
    ds, dn = struct.unpack("<II", data[68:76])
    s = ds
    while s not in (_OLE_END, _OLE_FREE) and dn > 0:
        base = 512 + s * ssz
        arr = struct.unpack("<%dI" % (ssz // 4), data[base:base + ssz])
        # ponytail: one DIFAT sector per lap (last slot is the next pointer,
        # not a FAT entry — slicing dn*127 at once eats it and derails big
        # files like the 22MB LimeSDR PcbLib)
        seen += list(arr[:ssz // 4 - 1])
        s = arr[-1]
        dn -= 1
    fat: list[int] = []
    for sec in seen[:nfat]:
        base = 512 + sec * ssz
        fat += list(struct.unpack("<%dI" % (ssz // 4), data[base:base + ssz]))

    def _chain(st: int, tab: list[int]) -> list[int]:
        out: list[int] = []
        while st not in (_OLE_END, _OLE_FREE, _OLE_SAT) and len(out) < 1000000:
            out.append(st)
            st = tab[st]
        return out

    def _read(st: int) -> bytes:
        return b"".join(data[512 + x * ssz:512 + (x + 1) * ssz]
                         for x in _chain(st, fat))
    mfs, mfn = struct.unpack("<II", data[60:68])
    mf: list[int] = []
    sc = mfs
    while sc not in (_OLE_END, _OLE_FREE) and len(mf) < mfn + 1:
        mf.append(sc)
        sc = fat[sc]
    minifat: list[int] = []
    for sec in mf:
        base = 512 + sec * ssz
        minifat += list(struct.unpack("<%dI" % (ssz // 4), data[base:base + ssz]))
    cutoff = struct.unpack("<I", data[56:60])[0]
    raw = _read(struct.unpack("<I", data[48:52])[0])
    n = len(raw) // 128
    nodes: list[tuple[str, int, int, int, int, int, int]] = []
    for i in range(n):
        e = raw[i * 128:(i + 1) * 128]
        nlen = struct.unpack("<H", e[64:66])[0]
        nm = e[:nlen - 2].decode("utf-16-le", "replace") if 2 <= nlen <= 128 else ""
        typ = e[66]
        left, right, child = struct.unpack("<iii", e[68:80])
        st, sz = struct.unpack("<II", e[116:124])
        nodes.append((nm, typ, left, right, child, st, sz))
    import sys
    sys.setrecursionlimit(100000)
    paths: dict[str, tuple[int, int]] = {}

    def _walk(idx: int, path: str, depth: int = 0) -> None:
        if idx < 0 or idx >= n or depth > 80:
            return
        nm, typ, left, right, child, st, sz = nodes[idx]
        if left >= 0:
            _walk(left, path, depth + 1)
        full = (path + "/" + nm) if path else nm
        if typ == 2:
            paths[full] = (st, sz)
        if typ in (1, 5) and child >= 0:
            _walk(child, full if typ == 1 else path, depth + 1)
        if right >= 0:
            _walk(right, path, depth + 1)
    _walk(0, "")
    ms = _read(nodes[0][5])

    def _mchain(st: int) -> list[int]:
        out: list[int] = []
        while st not in (_OLE_END, _OLE_FREE, _OLE_SAT) and len(out) < 1000000:
            out.append(st)
            st = minifat[st]
        return out
    mini = ms  # closure capture for _stream
    _ = mini, _mchain  # (kept local; _stream rebuilt per call below)
    # stash tables on the dict for _ole_stream
    paths["_fat_"] = (0, 0)  # marker, never read as a stream
    _ole_dir._fat = fat  # type: ignore[attr-defined]
    _ole_dir._minifat = minifat  # type: ignore[attr-defined]
    _ole_dir._ms = ms  # type: ignore[attr-defined]
    _ole_dir._msz = msz  # type: ignore[attr-defined]
    _ole_dir._ssz = ssz  # type: ignore[attr-defined]
    _ole_dir._data = data  # type: ignore[attr-defined]
    _ole_dir._cutoff = cutoff  # type: ignore[attr-defined]
    return paths, ms, minifat, msz, cutoff, ssz


def _ole_stream(paths: dict[str, tuple[int, int]], path: str) -> bytes | None:
    """Read one OLE stream by path (ministream-aware). None = absent."""
    import struct
    if path not in paths:
        return None
    st, sz = paths[path]
    fat = _ole_dir._fat  # type: ignore[attr-defined]
    minifat = _ole_dir._minifat  # type: ignore[attr-defined]
    ms = _ole_dir._ms  # type: ignore[attr-defined]
    msz = _ole_dir._msz  # type: ignore[attr-defined]
    ssz = _ole_dir._ssz  # type: ignore[attr-defined]
    data = _ole_dir._data  # type: ignore[attr-defined]
    cutoff = _ole_dir._cutoff  # type: ignore[attr-defined]
    if sz < cutoff:
        out = bytearray()
        s = st
        guard = 0
        while s not in (_OLE_END, _OLE_FREE, _OLE_SAT) and guard < 1000000:
            out += ms[s * msz:(s + 1) * msz]
            s = minifat[s]
            guard += 1
        return bytes(out[:sz])
    out2 = bytearray()
    s = st
    guard = 0
    while s not in (_OLE_END, _OLE_FREE, _OLE_SAT) and guard < 1000000:
        out2 += data[512 + s * ssz:512 + (s + 1) * ssz]
        s = fat[s]
        guard += 1
    _ = struct  # (struct used by _ole_dir; keeps imports local)
    return bytes(out2[:sz])


_IU = 2.54e-6  # Altium internal unit (1/10000 mil) → mm


def _bin_param(buf: bytes, kind: str) -> list[dict[str, str]]:
    """Binary param stream: [u32 len][ASCII] blocks → |RECORD=| dicts.
    Records carry no RECORD= key (the stream implies it); Board6 lead-in
    (outline verts) + |RECORD=Board| sub-record both parse."""
    import struct
    out: list[dict[str, str]] = []
    i = 0
    while i + 4 <= len(buf):
        ln = struct.unpack("<I", buf[i:i + 4])[0]
        if ln == 0 or ln > len(buf) - i - 4:
            break
        text = buf[i + 4:i + 4 + ln].decode("latin-1").rstrip("\x00")
        i += 4 + ln
        j = text.find("|RECORD=")
        lead, rest = (text[:j], text[j:]) if j >= 0 else (text, "")
        if "=" in lead:
            r = _arec(lead)
            r.setdefault("RECORD", kind.upper())
            out.append(r)
        for line in rest.splitlines():
            if "RECORD" in line.upper():
                r = _arec(line)
                if r.get("RECORD"):
                    r["RECORD"] = r["RECORD"].upper()
                    out.append(r)
    return out


def _bin_split(buf: bytes) -> list[tuple[int, bytes]]:
    """Primitive framing: [u8 type][u32 len][payload]. Short tail → error."""
    import struct
    out: list[tuple[int, bytes]] = []
    i = 0
    while i + 5 <= len(buf):
        ty = buf[i]
        ln = struct.unpack("<I", buf[i + 1:i + 5])[0]
        if ln > len(buf) - i - 5:
            raise ValueError("altium binary: record length past end of stream "
                             "(re-export via File > Save As > PCB ASCII)")
        out.append((ty, buf[i + 5:i + 5 + ln]))
        i += 5 + ln
    return out


def _bin_head(p: bytes) -> tuple[int, int, int] | None:
    """13-byte primitive header → (altium layer id, net, component); -1 = none."""
    import struct
    if len(p) < 13:
        return None
    layer = p[0]
    net = struct.unpack("<H", p[3:5])[0]
    comp = struct.unpack("<H", p[7:9])[0]
    return (layer, -1 if net == 0xFFFF else net, -1 if comp == 0xFFFF else comp)


def _bin_pt(p: bytes, o: int) -> tuple[float, float] | None:
    import struct
    if len(p) < o + 8:
        return None
    x, y = struct.unpack("<2i", p[o:o + 8])
    return (x * _IU, y * _IU)


def _lid(lid: int) -> str:
    """Binary layer id → ASCII layer name (1=Top … 32=Bottom, 39-54 planes)."""
    if lid == 1:
        return "TOPLAYER"
    if lid == 32:
        return "BOTTOMLAYER"
    if 2 <= lid <= 31:
        return f"MIDLAYER{lid - 1}"
    if 39 <= lid <= 54:
        return f"INTERNALPLANE{lid - 38}"
    if lid in (33, 34):
        return "TOPOVERLAY" if lid == 33 else "BOTTOMOVERLAY"
    if lid in (35, 36):
        return "TOPPASTE" if lid == 35 else "BOTTOMPASTE"
    if lid in (37, 38):
        return "TOPSOLDER" if lid == 37 else "BOTTOMSOLDER"
    if lid == 74:
        return "MULTILAYER"
    if lid == 56:
        return "KEEPOUTLAYER"
    if 57 <= lid <= 72:
        return f"MECHANICAL{lid - 56}"
    return "UNKNOWN"


def _bin_prims(buf: bytes, want: int) -> list[dict[str, str]]:
    """Decode one primitive stream → ASCII-shape dicts (skips other types).
    Implausible geometry raises (fail closed, cf. module docstring)."""
    import struct
    out: list[dict[str, str]] = []
    for ty, p in _bin_split(buf):
        if ty != want:
            continue
        h = _bin_head(p)
        if h is None:
            raise ValueError("altium binary: short primitive header")
        lid, net, comp = h
        if want == 4:  # TRACK
            a, b = _bin_pt(p, 13), _bin_pt(p, 21)
            w = struct.unpack("<i", p[29:33])[0] * _IU if len(p) >= 33 else 0.0
            if a is None or b is None or not (-2540 <= a[0] <= 2540 and -2540 <= a[1] <= 2540
                                              and -2540 <= b[0] <= 2540 and -2540 <= b[1] <= 2540):
                raise ValueError("altium binary: implausible track coords")
            if not 0.005 <= w <= 50:
                raise ValueError("altium binary: implausible track width")
            out.append({"RECORD": "TRACK", "LAYER": _lid(lid), "NET": str(net),
                        "COMPONENT": str(comp), "X1": f"{a[0]}mm", "Y1": f"{a[1]}mm",
                        "X2": f"{b[0]}mm", "Y2": f"{b[1]}mm", "WIDTH": f"{w}mm"})
        elif want == 1:  # ARC
            c = _bin_pt(p, 13)
            r = struct.unpack("<i", p[21:25])[0] * _IU if len(p) >= 25 else 0.0
            sa = struct.unpack("<d", p[25:33])[0] if len(p) >= 33 else 0.0
            ea = struct.unpack("<d", p[33:41])[0] if len(p) >= 41 else 360.0
            w = struct.unpack("<i", p[41:45])[0] * _IU if len(p) >= 45 else 0.0
            if c is None or not (-2540 <= c[0] <= 2540 and -2540 <= c[1] <= 2540):
                raise ValueError("altium binary: implausible arc center")
            if not 0 < r <= 2540 or not 0.005 <= w <= 50:
                raise ValueError("altium binary: implausible arc radius/width")
            out.append({"RECORD": "ARC", "LAYER": _lid(lid), "NET": str(net),
                        "COMPONENT": str(comp), "LOCATION.X": f"{c[0]}mm",
                        "LOCATION.Y": f"{c[1]}mm", "RADIUS": f"{r}mm",
                        "STARTANGLE": str(sa), "ENDANGLE": str(ea),
                        "WIDTH": f"{w}mm"})
        elif want == 3:  # VIA
            a = _bin_pt(p, 13)
            dia = struct.unpack("<i", p[21:25])[0] * _IU if len(p) >= 25 else 0.0
            hole = struct.unpack("<i", p[25:29])[0] * _IU if len(p) >= 29 else 0.0
            sl = _lid(p[29]) if len(p) >= 30 else "TOPLAYER"
            el = _lid(p[30]) if len(p) >= 31 else "BOTTOMLAYER"
            if a is None or not (-2540 <= a[0] <= 2540 and -2540 <= a[1] <= 2540):
                raise ValueError("altium binary: implausible via coords")
            if not 0.02 <= hole <= 20 or hole > dia * 4.0:
                raise ValueError("altium binary: implausible via drill")
            out.append({"RECORD": "VIA", "NET": str(net), "X": f"{a[0]}mm",
                        "Y": f"{a[1]}mm", "DIAMETER": f"{dia}mm",
                        "HOLESIZE": f"{hole}mm", "STARTLAYER": sl,
                        "ENDLAYER": el})
        elif want == 6:  # FILL → boundary tracks (no board-level fill prim)
            a, b = _bin_pt(p, 13), _bin_pt(p, 21)
            if a is None or b is None:
                raise ValueError("altium binary: short fill record")
            for x1, y1, x2, y2 in ((a[0], a[1], b[0], a[1]), (b[0], a[1], b[0], b[1]),
                                   (b[0], b[1], a[0], b[1]), (a[0], b[1], a[0], a[1])):
                out.append({"RECORD": "TRACK", "LAYER": _lid(lid), "NET": str(net),
                            "COMPONENT": str(comp), "X1": f"{x1}mm", "Y1": f"{y1}mm",
                            "X2": f"{x2}mm", "Y2": f"{y2}mm", "WIDTH": "0.05mm"})
    return out


def _bin_pads(buf: bytes) -> list[dict[str, str]]:
    """Pads6: 0x02 + six [u32 len][block] (name in block 0, geometry in
    block 4: header + pos + top/mid/bot sizes + hole + shapes + rot + plate).
    Layout cf. KiCad altium_parser_pcb.h APAD6."""
    import struct
    out: list[dict[str, str]] = []
    i = 0
    while i + 5 <= len(buf):
        if buf[i] != 2:
            raise ValueError("altium binary: pad record without 0x02 type byte")
        i += 1
        blocks = []
        for _ in range(6):
            if i + 4 > len(buf):
                raise ValueError("altium binary: truncated pad sub-block")
            ln = struct.unpack("<I", buf[i:i + 4])[0]
            if ln > len(buf) - i - 4:
                raise ValueError("altium binary: pad block past end of stream")
            blocks.append(buf[i + 4:i + 4 + ln])
            i += 4 + ln
        b0, g = blocks[0], blocks[4]
        n = b0[0] if b0 else 0
        name = b0[1:1 + n].decode("latin-1", "replace").rstrip("\x00")
        h = _bin_head(g)
        pos = _bin_pt(g, 13)
        w = struct.unpack("<i", g[21:25])[0] * _IU if len(g) >= 25 else 0.0
        hgt = struct.unpack("<i", g[25:29])[0] * _IU if len(g) >= 29 else 0.0
        hole = struct.unpack("<i", g[45:49])[0] * _IU if len(g) >= 49 else 0.0
        shape = {2: "RECTANGLE", 3: "OCTAGONAL", 9: "ROUNDEDRECTANGLE"}.get(
            g[49] if len(g) >= 50 else 0, "ROUND")
        rot = struct.unpack("<d", g[52:60])[0] if len(g) >= 60 else 0.0
        plated = (g[60] if len(g) >= 61 else 1) != 0
        if h is None or pos is None or not (-2540 <= pos[0] <= 2540
                                            and -2540 <= pos[1] <= 2540):
            raise ValueError("altium binary: implausible pad coords")
        if not 0 < w <= 100 or not 0 < hgt <= 100:
            raise ValueError("altium binary: implausible pad size")
        lid, net, comp = h
        out.append({"RECORD": "PAD", "NAME": name, "LAYER": _lid(lid),
                    "NET": str(net), "COMPONENT": str(comp),
                    "X": f"{pos[0]}mm", "Y": f"{pos[1]}mm",
                    "XSIZE": f"{w}mm", "YSIZE": f"{hgt}mm", "SHAPE": shape,
                    "HOLESIZE": f"{hole}mm", "ROTATION": str(rot),
                    "PLATED": "TRUE" if plated else "FALSE"})
    return out


def _bin_schdoc(data: bytes) -> dict[str, object]:
    """Native binary .SchDoc → IR (parts + nets). Records: [u16 len][00][type]
    + |K=V| text; units 10mil. Components (1) + Designator (34) → refs,
    Pins (2) + netlabels (25)/powerports (17) at wire (27) endpoints → nets.
    Junctions/dots implicit (shared endpoints join)."""
    import struct
    paths, _, _, _, _, _ = _ole_dir(data)
    fh = _ole_stream(paths, "FileHeader") or _ole_stream(paths, "Root Entry/FileHeader")
    if fh is None:
        raise ValueError("altium binary: .SchDoc has no FileHeader stream")
    recs: list[dict[str, str]] = []
    i = 0
    while i + 4 <= len(fh):
        ln = struct.unpack("<H", fh[i:i + 2])[0]
        if ln > len(fh) - i - 4:
            break
        t = fh[i + 4:i + 4 + ln].decode("latin-1", "replace")
        r = _arec(t)
        if r.get("RECORD"):
            recs.append(r)
        i += 4 + ln
    if not recs:
        raise ValueError("altium binary: no records in .SchDoc FileHeader")

    def _sp(v: str) -> float:
        try:
            return float(v) * 0.254  # 10mil units → mm
        except ValueError:
            return 0.0
    comps: dict[int, dict[str, str]] = {}
    for idx, r in enumerate(recs):
        if r.get("RECORD") == "1":
            comps[idx] = r
    owners: dict[int, int] = {}  # OwnerIndex → comp record idx
    for idx in comps:
        owners[idx] = idx  # children follow their component in file order
    refs: dict[int, str] = {}
    for idx, r in comps.items():
        des = next((q.get("TEXT", f"U{idx}") for q in recs
                    if q.get("RECORD") == "34" and q.get("OWNERINDEX") == str(idx)), f"U{idx}")
        refs[idx] = des
    pins: list[tuple[float, float, str, str]] = []  # x, y, ref, num
    for r in recs:
        if r.get("RECORD") != "2" or "DESIGNATOR" not in r:
            continue
        try:
            ci = owners.get(int(float(r.get("OWNERINDEX", "-1"))), -1)
        except ValueError:
            continue
        if ci not in refs:
            continue
        pins.append((_sp(r.get("LOCATION.X", "0")), _sp(r.get("LOCATION.Y", "0")),
                     refs[ci], r["DESIGNATOR"].rstrip("\x00").strip() or "?"))
    wires: list[tuple[float, float, float, float]] = []
    for r in recs:
        if r.get("RECORD") != "27" or r.get("LOCATIONCOUNT") != "2":
            continue
        try:
            wires.append((_sp(r["X1"]), _sp(r["Y1"]), _sp(r["X2"]), _sp(r["Y2"])))
        except KeyError:
            continue
    labels: list[tuple[float, float, str]] = []
    for r in recs:
        if r.get("RECORD") in ("25", "17") and r.get("TEXT"):
            labels.append((_sp(r.get("LOCATION.X", "0")),
                           _sp(r.get("LOCATION.Y", "0")),
                           r["TEXT"].rstrip("\x00").strip()))
    # union-find over touched points (pin ends + wire ends + labels)
    parent: dict[int, int] = {}

    def _find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def _union(a: int, b: int) -> None:
        parent[_find(a)] = _find(b)
    pts: list[tuple[float, float]] = []
    pin_at: list[int] = []
    for x, y, _rf, _pn in pins:
        parent[len(pts)] = len(pts)
        pts.append((x, y))
        pin_at.append(len(pins) and len(pts) - 1)
    for x1, y1, x2, y2 in wires:
        for x, y in ((x1, y1), (x2, y2)):
            parent[len(pts)] = len(pts)
            pts.append((x, y))

    def _near(p: tuple[float, float], tol: float = 0.3) -> list[int]:
        return [j for j, q in enumerate(pts)
                if abs(q[0] - p[0]) < tol and abs(q[1] - p[1]) < tol]
    for x1, y1, x2, y2 in wires:
        for a in _near((x1, y1)):
            for b in _near((x1, y1)):
                _union(a, b)
        for a in _near((x2, y2)):
            for b in _near((x2, y2)):
                _union(a, b)
    # every pin joins every wire-end/pin within tolerance (shared dots)
    for j, p in enumerate(pts):
        for k in _near(p):
            _union(j, k)
    groups: dict[int, list[int]] = {}
    for j in range(len(pts)):
        groups.setdefault(_find(j), []).append(j)
    npin = len(pins)
    nets: dict[str, dict[str, object]] = {}
    auto = 0

    def _pin(net: str, ref: str, num: str) -> None:
        entry = nets.setdefault(net, {"pins": [], "layer": None, "width": 0.3})
        pins_l = entry["pins"]
        assert isinstance(pins_l, list)
        pins_l.append([ref, num])
    for g in groups.values():
        members = [pins[j] for j in g if j < npin]
        if not members:
            continue
        name = next((t for x, y, t in labels
                     if any(abs(pts[j][0] - x) < 5.0 and abs(pts[j][1] - y) < 5.0 for j in g)),
                    None)
        if name is None:
            auto += 1
            name = f"N{auto}"
        for _x, _y, ref, num in members:
            _pin(name, ref, num)
    parts: list[dict[str, object]] = []
    fps: dict[str, Footprint] = {}
    for idx, r in comps.items():
        ref = refs[idx]
        lib = r.get("LIBREFERENCE", "unknown")
        mypins = [(num) for _x, _y, rf, num in pins if rf == ref]
        wdt = max(2.0, len(mypins) * 1.27 + 2.0)
        fp = fps.setdefault(lib, {"w": wdt, "h": 5.0,
                                  "pads": {str(n): (0.0, 0.0, 1.0, 1.0)
                                           for n in mypins},
                                  "holes": {}, "bodies": []})
        assert isinstance(fp, dict)
        cmt = next((q.get("TEXT", lib) for q in recs
                    if q.get("RECORD") == "41" and q.get("OWNERINDEX") == str(idx)
                    and q.get("NAME") == "Comment"), lib)
        parts.append({"ref": ref, "fp": lib, "value": cmt, "x": 0.0, "y": 0.0})
    from typing import cast as _cast3
    have = {str(p["ref"]) for p in parts}
    nets = {n: v for n, v in nets.items()
            if _cast3(list[list[str]], v["pins"]) and
            all(pin[0] in have for pin in _cast3(list[list[str]], v["pins"]))}
    xs = [x for x, _y in pts]
    wdt = max(10.0, (max(xs) - min(xs) + 5.0)) if xs else 40.0
    return {"board": {"name": "imported", "w": wdt, "h": 30.0, "layers": 2},
            "parts": parts, "nets": nets, "constraints": [],
            "_imported_fp": fps}


def _bin_region(buf: bytes) -> list[dict[str, str]]:
    """Regions6/ShapeBasedRegions6: [u8 0x0b][u32 len][13B header][u32 0]
    [u8 0][u32 tlen][text NUL][u32 npt][4 pad][verts…] where each vert is
    [f32 x][u32 flags][f32 y][u32 flags] in mm, origin-relative (+ORIGIN
    for absolute). KIND=1 → cutout, else keepout-ish outline. Component-
    attached regions (courtyards etc.) carry comp ≠ 0xFFFF — skipped, the
    footprint library owns those shapes, not the board."""
    import struct
    out: list[dict[str, str]] = []
    i = 0
    while i + 5 <= len(buf):
        if buf[i] != 0x0B:
            break
        ln = struct.unpack("<I", buf[i + 1:i + 5])[0]
        if ln > len(buf) - i - 5 or ln < 30:
            break
        p = buf[i + 5:i + 5 + ln]
        i += 5 + ln
        lid, net = p[0], struct.unpack("<H", p[3:5])[0]
        comp = struct.unpack("<H", p[7:9])[0]
        if len(p) < 22:
            continue
        tlen = struct.unpack("<I", p[18:22])[0]
        if tlen > len(p) - 22:
            continue
        d = _arec(p[22:22 + tlen].decode("latin-1", "replace").rstrip("\x00"))
        rest = p[22 + tlen:]
        if len(rest) < 8:
            continue
        npt = struct.unpack("<I", rest[:4])[0]
        raw = rest[8:]
        if npt > 100000 or len(raw) < 16 * npt - 4:
            continue
        try:
            xs = [struct.unpack("<f", raw[16 * k:16 * k + 4])[0]
                  for k in range(npt)]
            ys = [struct.unpack("<f", raw[16 * k + 8:16 * k + 12])[0]
                  for k in range(npt)]
        except struct.error:
            continue
        if not xs or not all(-500 <= v <= 500 for v in xs + ys):
            continue  # fail closed: misaligned layout, not a big board
        if comp != 0xFFFF:
            continue  # component-attached (courtyard/3D); footprint owns it
        d["RECORD"] = "REGION"
        d["LAYER"] = _lid(lid)
        # mask/paste/overlay regions are fab-art, not copper zones: mark so
        # the builder emits outline copper only for real zone layers
        if d["LAYER"] in ("TOPSOLDER", "BOTTOMSOLDER", "TOPPASTE",
                          "BOTTOMPASTE", "TOPOVERLAY", "BOTTOMOVERLAY"):
            d["FABART"] = "1"
        d["NET"] = str(-1 if net == 0xFFFF else net)
        for k, (x, y) in enumerate(zip(xs, ys)):
            d[f"X{k}"], d[f"Y{k}"] = f"{x}mm", f"{y}mm"
        d["NPT"] = str(npt)
        out.append(d)
    return out


def _bin_texts(buf: bytes) -> list[dict[str, str]]:
    """Texts6: [u8 0x05][u32 len][payload][u32 strlen][string]. Payload
    coords are u32 internal units at +13/+17 (origin-inclusive, like
    tracks); layer id at +0. Returns ASCII-shape TEXT dicts."""
    import struct
    out: list[dict[str, str]] = []
    i = 0
    while i + 5 <= len(buf):
        if buf[i] != 0x05:
            break
        ln = struct.unpack("<I", buf[i + 1:i + 5])[0]
        if ln > len(buf) - i - 5 or ln < 25:
            break
        p = buf[i + 5:i + 5 + ln]
        i += 5 + ln
        ln2 = struct.unpack("<I", buf[i:i + 4])[0] if i + 4 <= len(buf) else 0
        if ln2 > len(buf) - i - 4:
            break
        s = buf[i + 4:i + 4 + ln2].decode("latin-1", "replace")
        i += 4 + ln2
        x = struct.unpack("<I", p[13:17])[0] * _IU
        y = struct.unpack("<I", p[17:21])[0] * _IU
        if not (-2540 <= x <= 2540 and -2540 <= y <= 2540):
            continue
        t = s.strip().strip("\x00").strip()
        if not t or t == ".Designator":
            continue  # refdes echoes; real silk comes via footprints
        out.append({"RECORD": "TEXT", "LAYER": _lid(p[0]),
                    "LOCATION.X": f"{x}mm", "LOCATION.Y": f"{y}mm",
                    "TEXT": " ".join(t.split())})
    return out


def _bin_pcbdoc(data: bytes) -> dict[str, object]:
    """Native binary .PcbDoc → IR via the ASCII-shape path (same builder as
    the text export): param streams + decoded primitives + Polygons6 pours."""
    paths, _, _, _, _, _ = _ole_dir(data)
    if _ole_stream(paths, "Root Entry/FileHeaderSix") is None and \
            _ole_stream(paths, "FileHeaderSix") is None:
        raise ValueError("not an Altium PCB compound file (no FileHeaderSix); "
                         "a .SchDoc goes to importer:altium-sch")
    param: list[dict[str, str]] = []
    for stor, kind in (("Board6", "Board"), ("Nets6", "Net"),
                       ("Components6", "Component"), ("Rules6", "Rule")):
        for pre in ("", "Root Entry/"):
            buf = _ole_stream(paths, pre + stor + "/Data")
            if buf is not None:
                param += _bin_param(buf, kind)
                break
    if not [r for r in param if r.get("RECORD") == "NET"]:
        raise ValueError("altium binary: no Nets6 stream (not a .PcbDoc?)")

    def _prim(stor: str, want: int) -> list[dict[str, str]]:
        for pre in ("", "Root Entry/"):
            buf = _ole_stream(paths, pre + stor + "/Data")
            if buf is not None:
                return _bin_prims(buf, want) if want != 2 else [
                    {**r, "RECORD": "PAD"} for r in _bin_pads(buf)]
        return []
    prims: list[dict[str, str]] = []
    for stor, want in (("Tracks6", 4), ("Arcs6", 1), ("Vias6", 3),
                       ("Pads6", 2), ("Fills6", 6)):
        prims += _prim(stor, want)
    # Polygons6 pours → pour constraints (+ keep the outline verts as tracks)
    bpours: list[tuple[str, str]] = []  # (net-index-or-name, layer)
    regions: list[dict[str, str]] = []
    btexts: list[dict[str, str]] = []
    for pre in ("", "Root Entry/"):
        buf = _ole_stream(paths, pre + "Polygons6/Data")
        if buf is None:
            continue
        for r in _bin_param(buf, "Polygon"):
            num = sum(1 for k in r if k.startswith("VX"))
            verts = [(r.get(f"VX{i}", ""), r.get(f"VY{i}", "")) for i in range(num)]
            if len(verts) >= 3:
                for i in range(len(verts)):
                    x1, y1 = verts[i]
                    x2, y2 = verts[(i + 1) % len(verts)]
                    try:
                        prims.append({"RECORD": "TRACK", "LAYER": r.get("LAYER", "TOPLAYER"),
                                      "NET": r.get("NET", "-1"), "COMPONENT": "-1",
                                      "X1": x1, "Y1": y1, "X2": x2, "Y2": y2,
                                      "WIDTH": "0.05mm"})
                    except (ValueError, TypeError):
                        continue
            bpours.append((r.get("NET", "-1"), r.get("LAYER", "TOPLAYER")))
        break
    for r in _bin_param(_ole_stream(paths, "DifferentialPairs6/Data") or b"", "DiffPair"):
        if r.get("POSITIVENETNAME") and r.get("NEGATIVENETNAME"):
            prims.append({"RECORD": "DIFFPAIR",
                          "P": r["POSITIVENETNAME"], "N": r["NEGATIVENETNAME"]})
    for r in _bin_param(_ole_stream(paths, "Classes6/Data") or b"", "Class"):
        if r.get("KIND") == "0" and r.get("NAME"):
            members = [r[k] for k in sorted(r) if k.startswith("M")
                       and r[k] and not r[k].startswith("|")]
            prims.append({"RECORD": "NETCLASS", "NAME": r["NAME"],
                          "MEMBERS": " ".join(members)})
    for stor in ("Regions6", "ShapeBasedRegions6"):
        for pre in ("", "Root Entry/"):
            buf = _ole_stream(paths, pre + stor + "/Data")
            if buf is not None:
                regions += _bin_region(buf)
                break
    for pre in ("", "Root Entry/"):
        buf = _ole_stream(paths, pre + "Texts6/Data")
        if buf is not None:
            btexts = _bin_texts(buf)
            break
    text = "\n".join("|" + "|".join(f"{k}={v}" for k, v in r.items()
                                     if k != "RECORD") + f"|RECORD={r['RECORD']}|"
                     for r in param + prims + regions + btexts)
    ir = altium_ascii(text + "\n|RECORD=Net|NAME=__end__|\n")
    # Board6 outline verts (absolute, origin-inclusive) → origin-relative:
    # shift by the outline min so (0,0) is the board corner, matching tracks.
    from typing import cast as _cast6
    overts = _cast6(list[tuple[float, float]], ir.get("_outline", []))
    if len(overts) >= 3:
        ox, oy = min(v[0] for v in overts), min(v[1] for v in overts)
        ir["_outline"] = [(v[0] - ox, v[1] - oy) for v in overts]
        bb = ir["board"]
        assert isinstance(bb, dict)
        ol = _cast6(list[tuple[float, float]], ir["_outline"])
        bb["w"] = max(v[0] for v in ol)
        bb["h"] = max(v[1] for v in ol)
    names = [r.get("NAME", "") for r in param if r.get("RECORD") == "NET"]

    def _li(net: str, lay: str) -> tuple[str | None, int | None]:
        nn = names[int(net)] if net.lstrip("-").isdigit() and 0 <= int(net) < len(names) else None
        return nn, _alyr_idx(lay)
    from typing import cast as _cast5
    cons = [c for c in _cast5(list[object], ir.get("constraints", []))
            if isinstance(c, dict)]
    for net, lay in bpours:
        nn, ll = _li(net, lay)
        if nn is not None and ll is not None and ll < 10:
            cons.append({"t": "pour", "net": nn, "layer": ll})
    ir["constraints"] = cons
    from typing import cast as _cast4
    skip = [s for s in _cast4(list[str], ir.get("_skipped", [])) if s != "pours"]
    ir["_skipped"] = skip
    return ir


def altium_ascii(text: str) -> dict[str, object]:
    """Altium File > Save As > PCB ASCII (`|RECORD=` lines) → IR dict.
    Nets by index (out-of-range → unnetted copper); components in file
    order (pads reference them by index); pads absolute→footprint frame
    (mirrored on the bottom side, like kicad_pcb_netlist's relative pads);
    tracks on TOPLAYER/BOTTOMLAYER/MIDLAYERn, vias, board-outline verts or
    MECHANICAL1 chaining or the copper bbox. Binary .PcbDoc (OLE magic)
    raises pointing at the ASCII export."""
    if text[:8] == "\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        raise ValueError("binary .PcbDoc (OLE): re-export from Altium via "
                         "File > Save As > PCB ASCII, then import the text")
    recs = [_arec(l) for l in text.splitlines() if "RECORD" in l.upper()]
    recs = [r for r in recs if r.get("RECORD")]
    for r in recs:
        r["RECORD"] = r["RECORD"].upper()
    if not recs:
        raise ValueError("no |RECORD=| lines (not an Altium ASCII export)")
    names = [r.get("NAME", "") for r in recs if r["RECORD"] == "NET"]

    def _lyr(r: dict[str, str]) -> int:
        return _alyr_idx(r.get("LAYER", "TOPLAYER")) or 0

    def _net(r: dict[str, str]) -> str | None:
        raw = r.get("NET", "").strip()
        if not raw:
            return None
        if raw.lstrip("-").isdigit():
            i = int(raw)
            return names[i] if 0 <= i < len(names) else None
        return raw if raw in names else None  # third-party exporters name nets
    comps = [r for r in recs if r["RECORD"] == "COMPONENT"]
    fps: dict[str, Footprint] = {}
    parts: list[dict[str, object]] = []
    nets: dict[str, dict[str, object]] = {}
    refs: list[str] = []

    def _pin(net: str | None, ref: str, num: str) -> None:
        if not net:
            return
        entry = nets.setdefault(net, {"pins": [], "layer": None, "width": 0.3})
        pins = entry["pins"]
        assert isinstance(pins, list)
        pins.append([ref, num])

    for i, c in enumerate(comps):
        ref = (c.get("SOURCEDESIGNATOR") or c.get("DESIGNATOR")
               or c.get("NAME") or f"U{i + 1}")
        refs.append(ref)
        fpname = c.get("PATTERN") or "unknown"
        if c.get("SOURCEFOOTPRINTLIBRARY"):
            fpname = f"{c['SOURCEFOOTPRINTLIBRARY']}:{fpname}"
        rot = round(float(c.get("ROTATION", "0") or "0")) % 360
        x, y = _alen(c.get("X", "0")), _alen(c.get("Y", "0"))
        fps.setdefault(fpname, {"w": 2.0, "h": 2.0, "pads": {},
                                "holes": {}, "bodies": []})
        attrs = {"rot": str(rot)} if rot not in (0,) else None
        parts.append({"ref": ref, "fp": fpname,
                      "value": c.get("COMMENT") or c.get("SOURCEVALUE") or fpname,
                      "x": x, "y": y, **({"attrs": attrs} if attrs else {})})
    loose: list[tuple[float, float, float, float, float, str | None, str]] = []
    for r in recs:
        if r["RECORD"] != "PAD":
            continue
        try:
            ci = int(float(r.get("COMPONENT", "-1")))
        except ValueError:
            continue
        if not (0 <= ci < len(comps)):
            continue  # free pads handled below (IR has no loose pads)
        c, ref = comps[ci], refs[ci]
        fx, fy = _alen(c.get("X", "0")), _alen(c.get("Y", "0"))
        fro = round(float(c.get("ROTATION", "0") or "0")) % 360
        mir = c.get("LAYER", "TOPLAYER").upper() == "BOTTOMLAYER"
        hole = _alen(r.get("HOLESIZE", "0"))
        w = _alen(r.get("XSIZE", r.get("TOPXSIZE", "0")))
        h = _alen(r.get("YSIZE", r.get("TOPYSIZE", "0")))
        if w <= 0 or h <= 0:
            continue
        dx, dy = _alen(r.get("X", "0")) - fx, _alen(r.get("Y", "0")) - fy
        if mir:
            dx = -dx
        a = -math.radians(fro)
        ca, sa = math.cos(a), math.sin(a)
        dx, dy = dx * ca - dy * sa, dx * sa + dy * ca
        num = r.get("NAME", str(len(loose) + 1))
        fp = fps[str(parts[ci]["fp"])]
        assert isinstance(fp, dict)
        if hole > 0 or r.get("LAYER", "").upper() == "MULTILAYER":
            fp_h = fp.setdefault("holes", {})
            assert isinstance(fp_h, dict)
            fp_h[num] = (dx, dy, hole)
        else:
            fp_p = fp.setdefault("pads", {})
            assert isinstance(fp_p, dict)
            fp_p[num] = (dx, dy, w, h)
        _pin(_net(r), ref, num)
    for r in recs:  # free pads: no owning component → one single-pad fp each
        if r["RECORD"] != "PAD":
            continue
        try:
            ci = int(float(r.get("COMPONENT", "-1")))
        except ValueError:
            continue
        if 0 <= ci < len(comps):
            continue
        hole = _alen(r.get("HOLESIZE", "0"))
        w = _alen(r.get("XSIZE", r.get("TOPXSIZE", "0")))
        h = _alen(r.get("YSIZE", r.get("TOPYSIZE", "0")))
        if w <= 0 or h <= 0:
            continue
        loose.append((_alen(r.get("X", "0")), _alen(r.get("Y", "0")),
                      w, h, hole, _net(r), r.get("NAME", "")))
    for i, (x, y, w, h, hole, net, num) in enumerate(loose):
        ref, fpname = f"FREEPAD{i + 1}", "altium:free_pad"
        num = num or "1"
        fp = fps.setdefault(fpname, {"w": 2.0, "h": 2.0, "pads": {},
                                     "holes": {}, "bodies": []})
        assert isinstance(fp, dict)
        if hole > 0:
            fh = fp.setdefault("holes", {})
            assert isinstance(fh, dict)
            fh[num] = (0.0, 0.0, hole)
        else:
            fpp = fp.setdefault("pads", {})
            assert isinstance(fpp, dict)
            fpp[num] = (0.0, 0.0, w, h)
        parts.append({"ref": ref, "fp": fpname, "value": fpname, "x": x, "y": y})
        _pin(net, ref, num)
    # footprint bodies: pad bbox (empty customs place at 2x2 but DRC walls
    # their pads — same rule as kicad_pcb_netlist's rebuilt footprints)
    for fp in fps.values():
        assert isinstance(fp, dict)
        pads = fp.get("pads", {})
        holes = fp.get("holes", {})
        assert isinstance(pads, dict) and isinstance(holes, dict)
        xs = [v[0] for v in list(pads.values()) + list(holes.values())]
        ys = [v[1] for v in list(pads.values()) + list(holes.values())]
        if xs:
            fp["w"] = max(1.0, max(xs) - min(xs) + 2.0)
            fp["h"] = max(1.0, max(ys) - min(ys) + 2.0)
    traces: list[dict[str, object]] = []
    segs: list[tuple[float, float, float, float]] = []
    texts: list[dict[str, object]] = []
    pours: list[tuple[str, int]] = []
    rcons: list[dict[str, object]] = []
    dcons: list[dict[str, object]] = []
    ccons: list[dict[str, object]] = []
    boards = [r for r in recs if r["RECORD"] == "BOARD"]
    verts: list[tuple[float, float]] = []
    for b in boards:
        n = sum(1 for k in b if k.startswith("VX"))
        v = [(_alen(b.get(f"VX{i}", "")), _alen(b.get(f"VY{i}", "")))
             for i in range(max(n, 8)) if b.get(f"VX{i}") and b.get(f"VY{i}")]
        if len(v) >= 3:
            verts = v
            break
    for r in recs:
        if r["RECORD"] == "TRACK":
            p = (_alen(r.get("X1", "0")), _alen(r.get("Y1", "0")),
                 _alen(r.get("X2", "0")), _alen(r.get("Y2", "0")))
            wdt = max(0.01, _alen(r.get("WIDTH", "0.15")))
            lay = r.get("LAYER", "").upper()
            if _alyr_idx(lay) is not None:
                net = _net(r)
                traces.append({"net": net or "", "x1": p[0], "y1": p[1],
                               "x2": p[2], "y2": p[3], "layer": _lyr(r),
                               "width": wdt, **({} if net else {"_skip": True})})
            elif lay in ("MECHANICAL1", "KEEPOUTLAYER", "KEEPOUT"):
                segs.append(p)  # outline candidates (chained below)
        elif r["RECORD"] == "VIA":
            net = _net(r)
            traces.append({"net": net or "", "x": _alen(r.get("X", "0")),
                           "y": _alen(r.get("Y", "0")),
                           "drill": max(0.05, _alen(r.get("HOLESIZE", "0.3"))),
                           "via": True, **({} if net else {"_skip": True})})
        elif r["RECORD"] == "ARC":
            arc_c = (_alen(r.get("LOCATION.X", r.get("X", "0"))),
                     _alen(r.get("LOCATION.Y", r.get("Y", "0"))))
            arc_rad = _alen(r.get("RADIUS", "0"))
            try:
                arc_sa = float(r.get("STARTANGLE", "0") or "0")
                arc_ea = float(r.get("ENDANGLE", "360") or "360")
            except ValueError:
                arc_sa, arc_ea = 0.0, 360.0
            arc_wdt = max(0.01, _alen(r.get("WIDTH", "0.15")))
            arc_lay = r.get("LAYER", "").upper()
            if _alyr_idx(arc_lay) is not None and arc_rad > 0:
                arc_net = _net(r)
                # ponytail: chord-approximated (1 chord per 5°); exact arcs
                # return if a consumer needs them
                import math as _m
                arc_sweep = (arc_ea - arc_sa) % 360.0 or 360.0
                arc_nseg = max(1, int(arc_sweep / 5) + 1)
                arc_angs = [_m.radians(arc_sa + arc_sweep * k / arc_nseg)
                            for k in range(arc_nseg + 1)]
                arc_xy = [(arc_c[0] + arc_rad * _m.cos(a),
                           arc_c[1] + arc_rad * _m.sin(a)) for a in arc_angs]
                for k in range(arc_nseg):
                    traces.append({"net": arc_net or "",
                                   "x1": arc_xy[k][0], "y1": arc_xy[k][1],
                                   "x2": arc_xy[k + 1][0], "y2": arc_xy[k + 1][1],
                                   "layer": _lyr(r), "width": arc_wdt,
                                   **({} if arc_net else {"_skip": True})})
        elif r["RECORD"] == "TEXT":
            tx = _alen(r.get("LOCATION.X", r.get("X", "0")))
            ty = _alen(r.get("LOCATION.Y", r.get("Y", "0")))
            tstr = r.get("TEXT", r.get("STRING", "")).strip()
            if tstr:
                texts.append({"x": tx, "y": ty, "text": tstr})
        elif r["RECORD"] == "REGION":
            if r.get("FABART"):
                continue  # mask/paste/overlay art, not a copper zone
            kind = r.get("KIND", "0")
            npt = int(r.get("NPT", "0") or "0")
            rpts = [(_alen(r.get(f"X{k}", "")), _alen(r.get(f"Y{k}", "")))
                    for k in range(npt)]
            lay = r.get("LAYER", "").upper()
            if len(rpts) >= 3:
                xs = [p[0] for p in rpts]
                ys = [p[1] for p in rpts]
                cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                wdt, hgt = max(xs) - min(xs), max(ys) - min(ys)
                if kind == "1":
                    rcons.append({"t": "cutout", "x": cx, "y": cy,
                                  "w": max(wdt, 0.1), "h": max(hgt, 0.1)})
                elif lay in ("KEEPOUTLAYER", "KEEPOUT"):
                    rcons.append({"t": "keepout", "x": cx, "y": cy,
                                  "w": max(wdt, 0.1), "h": max(hgt, 0.1),
                                  "layers": []})
                for k in range(len(rpts)):
                    traces.append({"net": "", "x1": rpts[k][0], "y1": rpts[k][1],
                                   "x2": rpts[(k + 1) % len(rpts)][0],
                                   "y2": rpts[(k + 1) % len(rpts)][1],
                                   "layer": _lyr(r), "width": 0.05,
                                   "_skip": True})
        elif r["RECORD"] == "DIFFPAIR":
            if _net({"NET": r.get("P", "")}) and _net({"NET": r.get("N", "")}):
                dcons.append({"t": "diff", "p": r["P"], "n": r["N"], "gap": 0.2})
        elif r["RECORD"] == "NETCLASS":
            members = [m for m in r.get("MEMBERS", "").split() if m in nets]
            if members:
                ccons.append({"t": "class", "name": r.get("NAME", ""),
                              "nets": members})
        elif r["RECORD"] in ("POLYGON", "POLYGONPOUR", "SPLITPLANE"):
            pnet = _net(r)
            play = _alyr_idx(r.get("LAYER", ""))
            if pnet is not None and play is not None and play < 10:
                pours.append((pnet, play))
            pnum = sum(1 for k in r if k.startswith("VX"))
            ppts = [(_alen(r.get(f"VX{i}", "")), _alen(r.get(f"VY{i}", "")))
                    for i in range(pnum)]
            if len(ppts) >= 3 and pnet:
                for k in range(len(ppts)):
                    qx1, qy1 = ppts[k]
                    qx2, qy2 = ppts[(k + 1) % len(ppts)]
                    traces.append({"net": pnet, "x1": qx1, "y1": qy1,
                                   "x2": qx2, "y2": qy2,
                                   "layer": play if play is not None else 0,
                                   "width": 0.05})
    if len(verts) < 3 and segs:  # chain outline segments end-to-end
        pts = [(x1, y1, x2, y2) for x1, y1, x2, y2 in segs]
        chain = [(pts[0][0], pts[0][1]), (pts[0][2], pts[0][3])]
        rest = pts[1:]
        while rest:
            ex, ey = chain[-1]
            j = min(range(len(rest)),
                    key=lambda k: min((rest[k][0] - ex) ** 2 + (rest[k][1] - ey) ** 2,
                                      (rest[k][2] - ex) ** 2 + (rest[k][3] - ey) ** 2))
            x1, y1, x2, y2 = rest.pop(j)
            chain.append((x2, y2) if (x1 - ex) ** 2 + (y1 - ey) ** 2
                         < (x2 - ex) ** 2 + (y2 - ey) ** 2 else (x1, y1))
        verts = chain
    from typing import cast as _cast
    bxs: list[float] = ([v[0] for v in verts]
                       + [float(_cast(float, p["x1"])) for p in traces if "x1" in p]
                       + [float(_cast(float, p["x"])) for p in traces if "x" in p]
                       + [float(_cast(float, p["x"])) for p in parts])
    bys: list[float] = ([v[1] for v in verts]
                        + [float(_cast(float, p["y1"])) for p in traces if "y1" in p]
                        + [float(_cast(float, p["y"])) for p in traces if "y" in p]
                        + [float(_cast(float, p["y"])) for p in parts])
    wdt = max(10.0, (max(bxs) - min(bxs) + 5.0)) if bxs else 40.0
    hgt = max(10.0, (max(bys) - min(bys) + 5.0)) if bys else 30.0
    ncu = 2
    for t in traces:
        if "layer" in t and isinstance(t["layer"], int):
            ncu = max(ncu, int(t["layer"]) + 1)
    ncu = min(ncu, 10)
    have = {str(p["ref"]) for p in parts}
    nets = {n: {"pins": [pin for pin in _cast(list[list[str]], v["pins"])
                         if pin[0] in have],
                "layer": v["layer"], "width": v["width"]}
            for n, v in nets.items()}
    nets = {n: v for n, v in nets.items() if v["pins"]}
    cons: list[dict[str, object]] = [{"t": "pour", "net": n, "layer": ll}
                                          for n, ll in dict(pours).items()] + rcons + dcons + ccons
    ir: dict[str, object] = {"board": {"name": "imported", "w": wdt, "h": hgt,
                                       "layers": ncu},
                             "parts": parts, "nets": nets, "constraints": cons,
                             "_imported_fp": fps,
                             "_imported_traces": [t for t in traces if not t.pop("_skip", False)],
                             **_askip()}
    if texts:
        ir["_imported_texts"] = texts
    if len(verts) >= 3:
        ir["_outline"] = verts
    return ir


def pcad_ascii(text: str) -> dict[str, object]:
    """P-CAD ASCII (`ACCEL_ASCII`, Altium's own interchange) → IR dict.
    Patterns → footprints (padStyle sizes, `Rect`=rect / else circle-ish),
    netlist nodes (`compRef pinRef`) + `netNameRef` copper → nets,
    patterns + free pads → parts, outline or copper bbox → size.
    Real files are often flattened (pads/lines, no patterns): those pads
    become one single-pad part each, like Altium free pads."""
    head = text.lstrip()
    if not head.startswith("(ACCEL_ASCII") and not head.startswith("ACCEL_ASCII"):
        raise ValueError("not a P-CAD ASCII file (want ACCEL_ASCII …)")
    toks = sexpr("(" + head[head.find("("):] if head.startswith("ACCEL_ASCII")
                 else head)
    kids = toks[1:]
    # synthetic root: the first section lands in toks[0], not kids
    top = ([toks[0]] if toks and isinstance(toks[0], list) else []) + kids
    lib = next((c for c in top if isinstance(c, list) and c and c[0] == "library"), [])
    netsec = next((c for c in top if isinstance(c, list) and c and c[0] == "netlist"), [])
    pcb = next((c for c in top if isinstance(c, list) and c and c[0] == "pcbDesign"), [])
    units = "mil"
    for c in top:
        if isinstance(c, list) and c and c[0] == "asciiHeader":
            for f in c[1:]:
                if isinstance(f, list) and f and f[0] == "fileUnits" and len(f) > 1:
                    units = _unq(f[1]).lower()
    mul = {"mm": 1.0, "mil": 0.0254, "inch": 25.4, "in": 25.4,
           "um": 0.001, "cm": 10.0}.get(units, 0.0254)

    def _xy(node: list[object]) -> tuple[float, float]:
        pt = next((c for c in node[1:] if isinstance(c, list) and c and c[0] == "pt"), None)
        if pt is None or len(pt) < 3 or not _isnum(pt[1]) or not _isnum(pt[2]):
            return (0.0, 0.0)
        return (_num(pt[1]) * mul, _num(pt[2]) * mul)

    def _ref(node: list[object], tag: str) -> str:
        n = next((c for c in node[1:] if isinstance(c, list) and c and c[0] == tag), None)
        return _unq(n[1]) if n is not None and len(n) > 1 else ""

    styles: dict[str, tuple[float, float, float, str]] = {}  # w,h,hole,shape
    for s in _kids(lib, "padStyleDef"):
        nm = _unq(s[1]) if len(s) > 1 else ""
        hole = 0.0
        hd = next((c for c in s[1:] if isinstance(c, list) and c and c[0] == "holeDiam"), None)
        if hd is not None and len(hd) > 1 and _isnum(hd[1]):
            hole = _num(hd[1]) * mul
        w = h = 0.0
        shape = "Ellipse"
        for p in _kids(s, "padShape"):
            ln = next((c for c in p[1:] if isinstance(c, list) and c and c[0] == "layerNumRef"), None)
            if ln is None or len(ln) < 2 or str(ln[1]) != "1":
                continue  # top-layer shape sizes the pad (as KiCad's importer)
            for c in p[1:]:
                if not isinstance(c, list) or not c:
                    continue
                if c[0] == "padShapeType" and len(c) > 1:
                    shape = _unq(c[1])
                elif c[0] == "shapeWidth" and len(c) > 1 and _isnum(c[1]):
                    w = _num(c[1]) * mul
                elif c[0] == "shapeHeight" and len(c) > 1 and _isnum(c[1]):
                    h = _num(c[1]) * mul
        styles[nm] = (w or 1.0, h or 1.0, hole, shape)
    # patterns (old `patternDef`, new `patternDefExtended` via graphics defs)
    pats: dict[str, list[tuple[str, float, float, float, float, float]]] = {}
    for tag in ("patternDef", "patternDefExtended"):
        for p in _kids(lib, tag):
            nm = _unq(p[1]) if len(p) > 1 else ""
            mls = [p] if tag == "patternDef" else _kids(p, "patternGraphicsDef")
            pads: list[tuple[str, float, float, float, float, float]] = []
            scopes: list[list[object]] = []
            if tag == "patternDef":
                scopes = [m for m in p[1:] if isinstance(m, list) and m and m[0] == "multiLayer"]
            else:
                for g in _kids(p, "patternGraphicsDef"):
                    scopes += [m for m in g[1:] if isinstance(m, list) and m and m[0] == "multiLayer"]
            for m in scopes:
                for pad in _kids(m, "pad"):
                    num = _ref(pad, "padNum") or str(len(pads) + 1)
                    st = styles.get(_ref(pad, "padStyleRef"), (1.0, 1.0, 0.0, "Ellipse"))
                    x, y = _xy(pad)
                    pads.append((num, x, y, st[0], st[1], st[2]))
            pats[nm] = pads
    comp_pat: dict[str, str] = {}  # compDef name → pattern
    for c in _kids(lib, "compDef"):
        nm = _unq(c[1]) if len(c) > 1 else ""
        for f in c[1:]:
            if isinstance(f, list) and f and f[0] in ("attachedPattern", "patternName") and len(f) > 1:
                comp_pat[nm] = _unq(f[1])
            elif isinstance(f, list) and f and f[0] == "pattern" and len(f) > 1:
                comp_pat[nm] = _unq(f[1])
    nets: dict[str, dict[str, object]] = {}
    inst: dict[str, tuple[str, str]] = {}  # ref → (comp, value)

    def _pin(net: str, ref: str, num: str) -> None:
        if not net:
            return
        entry = nets.setdefault(net, {"pins": [], "layer": None, "width": 0.3})
        pins = entry["pins"]
        assert isinstance(pins, list)
        pins.append([ref, num])

    for n in _kids(netsec, "net"):
        nn = _unq(n[1]) if len(n) > 1 else ""
        for node in _kids(n, "node"):
            raw = _unq(node[1]) if len(node) > 1 else ""
            rs = raw.split()
            if len(rs) >= 2:
                _pin(nn, rs[0], rs[1])
    for c in _kids(netsec, "compInst"):
        ref = _unq(c[1]) if len(c) > 1 else ""
        comp = val = ""
        for f in c[1:]:
            if not isinstance(f, list) or not f:
                continue
            if f[0] in ("compRef", "compValue", "refDesRef") and len(f) > 1:
                if f[0] == "compValue":
                    val = _unq(f[1])
                else:
                    comp = _unq(f[1])
        inst[ref] = (comp, val or comp)
    fps: dict[str, Footprint] = {}
    parts: list[dict[str, object]] = []
    multi = next((c for c in pcb[1:] if isinstance(c, list) and c and c[0] == "multiLayer"), [])
    # layer numbers: Top=1 Bottom=2 (defaults), refined by layerDef names
    lmap = {1: 0, 2: 1}

    def _stack() -> int:
        sig = 0
        for ld in _kids(pcb, "layerDef"):
            lt = next((c for c in ld[1:] if isinstance(c, list) and c and c[0] == "layerType"), None)
            if lt is not None and len(lt) > 1 and _unq(lt[1]).lower() == "signal":
                sig += 1
        return min(10, max(2 if sig != 1 else 1, sig))

    for ld in _kids(pcb, "layerDef"):
        nm = (_unq(ld[1]) if len(ld) > 1 else "").upper()
        lnode = next((c for c in ld[1:] if isinstance(c, list) and c and c[0] == "layerNum"), None)
        if lnode is None or len(lnode) < 2 or not _isnum(lnode[1]):
            continue
        lnum = int(float(_unq(lnode[1])))
        if nm in ("TOP",):
            lmap[lnum] = 0
        elif nm in ("BOTTOM",):
            lmap[lnum] = 1
    seen_pat = [c for c in multi[1:] if isinstance(c, list) and c and c[0] == "pattern"] \
        if multi else []
    for i, c in enumerate(seen_pat):
        ref = _ref(c, "refDesRef") or f"U{i + 1}"
        pat = _ref(c, "patternRef")
        comp, val = inst.get(ref, ("", ""))
        fpname = comp_pat.get(comp, pat) or pat or "unknown"
        pads = pats.get(fpname, pats.get(pat, []))
        x, y = _xy(c)
        fpd: Footprint = {"w": 2.0, "h": 2.0, "pads": {}, "holes": {},
                          "bodies": []}
        xs, ys = [0.0], [0.0]
        for num, px, py, w, h, hole in pads:
            xs += [px, -px]
            ys += [py, -py]
            if hole > 0:
                fh = fpd.setdefault("holes", {})
                assert isinstance(fh, dict)
                fh[num] = (px, py, hole)
            else:
                fp_ = fpd.setdefault("pads", {})
                assert isinstance(fp_, dict)
                fp_[num] = (px, py, w, h)
        fpd["w"], fpd["h"] = max(1.0, max(xs) - min(xs) + 1.0), max(1.0, max(ys) - min(ys) + 1.0)
        fps.setdefault(fpname or f"pcad{i}", fpd)
        parts.append({"ref": ref, "fp": fpname or f"pcad{i}",
                      "value": val or comp or fpname, "x": x, "y": y})
    # free pads (flattened files): one single-pad part each
    freep = [c for c in multi[1:] if isinstance(c, list) and c and c[0] == "pad"] \
        if multi else []
    for i, pad in enumerate(freep):
        st = styles.get(_ref(pad, "padStyleRef"), (1.0, 1.0, 0.0, "Ellipse"))
        x, y = _xy(pad)
        ref = f"FREEPAD{i + 1}"
        fpname = "pcad:free_pad"
        fp = fps.setdefault(fpname, {"w": 2.0, "h": 2.0, "pads": {},
                                     "holes": {}, "bodies": []})
        assert isinstance(fp, dict)
        if st[2] > 0:
            fh = fp.setdefault("holes", {})
            assert isinstance(fh, dict)
            fh[str(i + 1)] = (0.0, 0.0, st[2])
        else:
            fp_ = fp.setdefault("pads", {})
            assert isinstance(fp_, dict)
            fp_[str(i + 1)] = (0.0, 0.0, st[0], st[1])
        parts.append({"ref": ref, "fp": fpname, "value": fpname, "x": x, "y": y})
        _pin(_ref(pad, "netNameRef"), ref, str(i + 1))
    traces: list[dict[str, object]] = []
    texts: list[dict[str, object]] = []
    pours: list[tuple[str, int]] = []
    for lc in _kids(pcb, "layerContents"):
        ln = next((c for c in lc[1:] if isinstance(c, list) and c and c[0] == "layerNumRef"), None)
        lay = lmap.get(int(float(_unq(ln[1]))), 0) if ln is not None and len(ln) > 1 else 0
        for ln2 in _kids(lc, "line"):
            pts = [c for c in ln2[1:] if isinstance(c, list) and c and c[0] == "pt"]
            if len(pts) < 2:
                continue
            x1, y1 = _num(pts[0][1]) * mul, _num(pts[0][2]) * mul
            x2, y2 = _num(pts[1][1]) * mul, _num(pts[1][2]) * mul
            wnode = next((c for c in ln2[1:] if isinstance(c, list) and c and c[0] == "width"), None)
            wdt = _num(wnode[1]) * mul if wnode is not None and len(wnode) > 1 else 0.3
            net = _ref(ln2, "netNameRef")
            traces.append({"net": net, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                           "layer": lay, "width": max(0.01, wdt),
                           **({} if net else {"_skip": True})})
        for a in _kids(lc, "arc") + _kids(lc, "triplePointArc"):
            # ponytail: chord-approximated; exact arcs return if needed
            import math as _m2
            wnode = next((c for c in a[1:] if isinstance(c, list) and c and c[0] == "width"), None)
            wdt = _num(wnode[1]) * mul if wnode is not None and len(wnode) > 1 else 0.3
            net = _ref(a, "netNameRef")
            pts = [c for c in a[1:] if isinstance(c, list) and c and c[0] == "pt"]
            rnode = next((c for c in a[1:] if isinstance(c, list) and c and c[0] == "radius"), None)
            if a[0] == "arc" and rnode is not None and len(rnode) > 1 and pts:
                try:
                    cx, cy = _num(pts[0][1]) * mul, _num(pts[0][2]) * mul
                    rad = _num(rnode[1]) * mul
                    sa = float(_unq(next((c for c in a[1:] if isinstance(c, list) and c
                                          and c[0] == "startAngle"), ["", "0"])[1] or "0"))
                    ea = float(_unq(next((c for c in a[1:] if isinstance(c, list) and c
                                          and c[0] == "sweepAngle"), ["", "360"])[1] or "360"))
                except (ValueError, TypeError):
                    continue
                if rad <= 0:
                    continue
                sweep = ea % 360.0 or 360.0
                nseg = max(1, int(sweep / 5) + 1)
                xy = [(cx + rad * _m2.cos(_m2.radians(sa + sweep * k / nseg)),
                       cy + rad * _m2.sin(_m2.radians(sa + sweep * k / nseg)))
                      for k in range(nseg + 1)]
                for k in range(nseg):
                    traces.append({"net": net, "x1": xy[k][0], "y1": xy[k][1],
                                   "x2": xy[k + 1][0], "y2": xy[k + 1][1],
                                   "layer": lay, "width": max(0.01, wdt),
                                   **({} if net else {"_skip": True})})
            elif len(pts) >= 3:  # triplePointArc: center/start/end
                (cx, cy), (sx, sy), (ex, ey) = ((pts[0][1], pts[0][2]), (pts[1][1], pts[1][2]),
                                                (pts[2][1], pts[2][2]))
                try:
                    cx, cy, sx, sy, ex, ey = (float(cx) * mul, float(cy) * mul,
                                              float(sx) * mul, float(sy) * mul,
                                              float(ex) * mul, float(ey) * mul)
                except (ValueError, TypeError):
                    continue
                import math as _m3
                rad = _m3.hypot(sx - cx, sy - cy)
                if rad <= 0:
                    continue
                sa = _m3.degrees(_m3.atan2(sy - cy, sx - cx))
                sweep = (_m3.degrees(_m3.atan2(ey - cy, ex - cx)) - sa) % 360.0 or 360.0
                nseg = max(1, int(sweep / 5) + 1)
                txy = [(cx + rad * _m3.cos(_m3.radians(sa + sweep * k / nseg)),
                       cy + rad * _m3.sin(_m3.radians(sa + sweep * k / nseg)))
                      for k in range(nseg + 1)]
                tnet, tlay = net, lay
                for k in range(nseg):
                    traces.append({"net": tnet, "x1": txy[k][0], "y1": txy[k][1],
                                   "x2": txy[k + 1][0], "y2": txy[k + 1][1],
                                   "layer": tlay, "width": max(0.01, wdt),
                                   **({} if tnet else {"_skip": True})})
        for tx in _kids(lc, "text"):
            tstr = _unq(tx[1]) if len(tx) > 1 else ""
            txp, typ = _xy(tx)
            if tstr:
                texts.append({"x": txp, "y": typ, "text": tstr})
        for pp in _kids(lc, "pcbPoly") + _kids(lc, "copperPour95"):
            poly = next((c for c in pp[1:] if isinstance(c, list) and c and c[0] == "pcbPoly"),
                        pp if pp[0] == "pcbPoly" else None)
            if poly is None:
                continue
            net = _ref(poly, "netNameRef") or _ref(pp, "netNameRef")
            if net and lay < 10:
                pours.append((net, lay))
            pts = [c for c in poly[1:] if isinstance(c, list) and c and c[0] == "pt"]
            if len(pts) >= 3 and net:
                xy = [(_num(p[1]) * mul, _num(p[2]) * mul) for p in pts]
                for k in range(len(xy)):
                    traces.append({"net": net, "x1": xy[k][0], "y1": xy[k][1],
                                   "x2": xy[(k + 1) % len(xy)][0],
                                   "y2": xy[(k + 1) % len(xy)][1],
                                   "layer": lay, "width": 0.05})
    from typing import cast as _cast2
    xs2: list[float] = ([float(_cast2(float, p["x"])) for p in parts]
                        + [float(_cast2(float, t["x1"])) for t in traces if "x1" in t])
    ys2: list[float] = ([float(_cast2(float, p["y"])) for p in parts]
                        + [float(_cast2(float, t["y1"])) for t in traces if "y1" in t])
    wdt = max(10.0, (max(xs2) - min(xs2) + 5.0)) if xs2 else 40.0
    hgt = max(10.0, (max(ys2) - min(ys2) + 5.0)) if ys2 else 30.0
    have = {str(p["ref"]) for p in parts}
    nets = {n: {"pins": [pin for pin in _cast2(list[list[str]], v["pins"])
                         if pin[0] in have],
                "layer": v["layer"], "width": v["width"]}
            for n, v in nets.items()}
    nets = {n: v for n, v in nets.items() if v["pins"]}
    cons: list[dict[str, object]] = [{"t": "pour", "net": n, "layer": ll}
                                     for n, ll in dict(pours).items()]
    ir: dict[str, object] = {"board": {"name": "imported", "w": wdt, "h": hgt,
                                       "layers": _stack()},
                             "parts": parts, "nets": nets, "constraints": cons,
                             "_imported_fp": fps,
                             "_imported_traces": [t for t in traces if not t.pop("_skip", False)],
                             **_askip()}
    if texts:
        ir["_imported_texts"] = texts
    return ir
