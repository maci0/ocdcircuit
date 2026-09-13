"""Import foreign footprints + boards: KiCad .pretty/.kicad_mod + .kicad_pcb,
Eagle .lbr (packages) + .brd (full board: elements/signals/plain),
EasyEDA Std JSON (footprint + PCB docs: PAD/TRACK/VIA/LIB shapes),
tscircuit Circuit-JSON. Rect/circle/oval SMD pads, PTH holes,
courtyard → w/h, 3D model refs kept as texture hints.

Usage: `fp path/to/part.kicad_mod` in .ocd — same as .fp files.
Also: Board.import_foreign(path) for whole-board netlist import (.kicad_pcb).
"""
from __future__ import annotations
import os
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
            j = s.find('"', i + 1)
            if j < 0:
                raise ValueError("unterminated string in s-expr")
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


def sexpr(s: str) -> list[object]:
    """Parse one s-expression (skips ; comments)."""
    lines = [l.split(";")[0] for l in s.splitlines()]
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
            d = _num(dr[1]) if dr and len(dr) > 1 else 0.8
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
    if minx == float("inf"):
        minx, miny, maxx, maxy = 0.0, 0.0, 1.0, 1.0
    wdt, hgt = max(1.0, maxx - minx + 1.0), max(1.0, maxy - miny + 1.0)
    # recenter pads/holes on centroid
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    pads = {k: (v[0] - cx, v[1] - cy, v[2], v[3]) for k, v in pads.items()}
    holes = {k: (v[0] - cx, v[1] - cy, v[2]) for k, v in holes.items()}
    fp: Footprint = {"w": wdt, "h": hgt, "pads": pads, "holes": holes,
                     "bodies": [{"box": (wdt - 1.0, hgt - 1.0, 1.0)}]}
    if models:
        fp["models"] = models  # STEP/WRL refs → texture/model hints
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
            parts.append({"ref": ref, "fp": pkg, "value": meta.get("name", pkg),
                          "x": lx * mm, "y": ly * mm})
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


def load_foreign(path: str) -> list[tuple[str, Footprint]]:
    """Dispatch by extension: .kicad_mod/.pretty, .lbr, .json."""
    ext = os.path.splitext(path)[1].lower()
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
    """Reference designator: property Reference, else our user text."""
    for t in _kids(fpnode, "property"):
        if len(t) > 2 and _unq(t[1]) == "Reference":
            return _unq(t[2])
    for t in _kids(fpnode, "fp_text"):
        if len(t) > 2 and _unq(t[1]) == "user":
            return _unq(t[2])
    return ""


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
        ref = _footprint_ref(fpnode)
        if not ref:
            ref = f"U{len(parts) + 1}"
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
            pat = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "at"), None)
            psz = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "size"), None)
            pdr = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "drill"), None)
            px = _num(pat[1]) - x if pat and len(pat) > 1 else 0.0
            py = _num(pat[2]) - y if pat and len(pat) > 2 else 0.0
            pnet = next((c for c in pad[4:] if isinstance(c, list) and c and c[0] == "net"), None)
            nid = str(pnet[1]) if pnet and len(pnet) > 1 else "0"
            if pdr is not None:
                holes[num] = (px, py, _num(pdr[1]) if len(pdr) > 1 else 0.8)
            else:
                pw = _num(psz[1]) if psz and len(psz) > 1 else 1.0
                ph = _num(psz[2]) if psz and len(psz) > 2 else 1.0
                pads[num] = (px, py, pw, ph)
            if nid != "0":
                nn = netnames.get(nid, f"N{nid}")
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
    # board size from Edge.Cuts bbox
    xs: list[float] = []
    ys: list[float] = []
    for gr in _kids(root, "gr_line"):
        for tag in ("start", "end"):
            pt = next((c for c in gr[1:] if isinstance(c, list) and c and c[0] == tag), None)
            if pt and len(pt) > 2:
                xs.append(_num(pt[1]))
                ys.append(_num(pt[2]))
    wdt = max(xs) - min(xs) if xs else 40.0
    hgt = max(ys) - min(ys) if ys else 30.0
    return {"board": {"name": "imported", "w": wdt, "h": hgt, "layers": 2},
            "parts": parts, "nets": nets, "constraints": [],
            "_imported_fp": fps}
