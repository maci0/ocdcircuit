"""Mitox port: tscircuit → .ocd + harvested .fp files.

Unlike the pico port (footprint-STRING mapping), mitox uses FP_* vars with
exact EasyEDA geometry. Strategy: harvest pad/hole geometry per component
from circuit.json → emit one .fp per unique footprint (named by LCSC from
tsx supplierPartNumbers) → .ocd references them via `fp` lines.

Also ports: testpoints (→ plated-hole footprints), keepouts, cutout, holes,
pours, pre-route <trace> (→ keep constraints), LCSC attrs, rotations.

Usage: python -m tools.mitox <mitox-projdir> <outdir>
"""
from __future__ import annotations
import json
import os
import re
import sys
from typing import cast



def _f(v: object, default: float = 0.0) -> float:
    if v is None:
        return default
    assert isinstance(v, (int, float, str))
    return float(v)


def tsx_elements(src: str) -> dict[str, dict[str, str]]:
    """{partname: {fpvar, lcsc, value, kind, x, y, rot}} from tsx elements."""
    out: dict[str, dict[str, str]] = {}
    for kind in ("chip", "capacitor", "resistor", "inductor", "jumper", "testpoint"):
        for m in re.finditer(rf"<{kind}\s+([^>]*?)/?>", src, re.S):
            attrs = m.group(1)
            name = _attr(attrs, "name")
            if not name or "{" in name:
                continue
            fp = _attr(attrs, "footprint")
            lcsc = ""
            sm = re.search(r'supplierPartNumbers=\{\{\s*jlcpcb:\s*\["([^"]+)"\]', attrs)
            if sm:
                lcsc = sm.group(1)
            val = (_attr(attrs, "capacitance") or _attr(attrs, "resistance")
                   or _attr(attrs, "inductance"))
            xm = re.search(r'pcbX="(-?[\d.]+)mm"', attrs)
            ym = re.search(r'pcbY="(-?[\d.]+)mm"', attrs)
            rm = re.search(r'pcbRotation="(\d+)deg"', attrs)
            out[name] = {"kind": kind, "fpvar": fp, "lcsc": lcsc, "value": val,
                         "x": xm.group(1) if xm else "", "y": ym.group(1) if ym else "",
                         "rot": rm.group(1) if rm else "0"}
    return out


def _attr(attrs: str, key: str) -> str:
    m = re.search(rf"{key}=\{{\"([^\"]*)\"\}}", attrs) or re.search(rf'{key}="([^"]*)"', attrs)
    return m.group(1) if m else ""


def harvest_footprints(cjson: list[dict[str, object]],
                       ) -> dict[str, dict[str, object]]:
    """{pcb_component_id: {pads, holes}} in COMPONENT frame (minus center).

    Pins keyed by pin NUMBER (via pcb_port→source_port), matching what nets
    reference — never by port_hints labels."""
    comp_center: dict[str, tuple[float, float]] = {}
    comp_rot: dict[str, int] = {}
    for e in cjson:
        if e.get("type") == "pcb_component":
            cen = cast(dict[str, object], e.get("center", {}))
            cid = str(e.get("pcb_component_id"))
            comp_center[cid] = (_f(cen.get("x")), _f(cen.get("y")))
            comp_rot[cid] = int(_f(e.get("rotation", 0))) % 360
    src_pin: dict[str, str] = {}  # source_port_id → pin_number
    for e in cjson:
        if e.get("type") == "source_port":
            src_pin[str(e.get("source_port_id"))] = str(e.get("pin_number"))
    pcb_pin: dict[str, str] = {}  # pcb_port_id → pin_number
    for e in cjson:
        if e.get("type") == "pcb_port":
            pcb_pin[str(e.get("pcb_port_id"))] = src_pin.get(
                str(e.get("source_port_id", "")), "?")
    pads: dict[str, list[dict[str, object]]] = {}
    holes: dict[str, list[dict[str, object]]] = {}
    for e in cjson:
        t = e.get("type")
        if t == "pcb_smtpad":
            cid = str(e.get("pcb_component_id", ""))
            pads.setdefault(cid, []).append({
                "pin": pcb_pin.get(str(e.get("pcb_port_id", "")), "?"),
                "x": _f(e.get("x")), "y": _f(e.get("y")),
                "w": _f(e.get("width"), 1.0), "h": _f(e.get("height"), 1.0),
                "shape": str(e.get("shape", "rect"))})
        elif t == "pcb_plated_hole":
            cid = str(e.get("pcb_component_id", ""))
            holes.setdefault(cid, []).append({
                "pin": pcb_pin.get(str(e.get("pcb_port_id", "")), "?"),
                "x": _f(e.get("x")), "y": _f(e.get("y")),
                "drill": _f(e.get("hole_width", e.get("hole_diameter", 0.8)))})
    out: dict[str, dict[str, object]] = {}
    for cid, (cx, cy) in comp_center.items():
        rot = comp_rot.get(cid, 0)

        def unrot(dx: float, dy: float) -> tuple[float, float]:
            if rot == 90:
                return (dy, -dx)
            if rot == 180:
                return (-dx, -dy)
            if rot == 270:
                return (-dy, dx)
            return (dx, dy)

        def unrot_wh(w: float, h: float) -> tuple[float, float]:
            return (h, w) if rot in (90, 270) else (w, h)

        pl = []
        for p in pads.get(cid, []):
            dx, dy = unrot(_f(p["x"]) - cx, _f(p["y"]) - cy)
            w, h = unrot_wh(_f(p["w"]), _f(p["h"]))
            pl.append({"pin": p["pin"], "dx": dx, "dy": dy, "w": w, "h": h})
        hl = []
        for hh in holes.get(cid, []):
            dx, dy = unrot(_f(hh["x"]) - cx, _f(hh["y"]) - cy)
            hl.append({"pin": hh["pin"], "dx": dx, "dy": dy, "drill": _f(hh["drill"])})
        if pl or hl:
            out[cid] = {"pads": pl, "holes": hl}
    return out


def fp_text(name: str, pads: list[dict[str, object]],
            holes: list[dict[str, object]],
            wh: tuple[float, float] | None = None) -> str:
    """Render our .fp format. wh: exact courtyard (tscircuit's own) —
    falls back to tight pad bbox + 0.3."""
    if wh is None:
        x0 = min([_f(p["dx"]) - _f(p["w"]) / 2 for p in pads] +
                 [_f(h["dx"]) - 0.5 for h in holes] + [0.0])
        x1 = max([_f(p["dx"]) + _f(p["w"]) / 2 for p in pads] +
                 [_f(h["dx"]) + 0.5 for h in holes] + [0.0])
        y0 = min([_f(p["dy"]) - _f(p["h"]) / 2 for p in pads] +
                 [_f(h["dy"]) - 0.5 for h in holes] + [0.0])
        y1 = max([_f(p["dy"]) + _f(p["h"]) / 2 for p in pads] +
                 [_f(h["dy"]) + 0.5 for h in holes] + [0.0])
        w, h = max(1.0, x1 - x0 + 0.3), max(1.0, y1 - y0 + 0.3)
    else:
        w, h = wh
    L = [f"footprint {name} {w:.2f}x{h:.2f}"]
    for p in pads:
        L.append(f"pad {p['pin']} {_f(p['dx']):.3f} {_f(p['dy']):.3f} "
                 f"{_f(p['w']):.3f} {_f(p['h']):.3f}")
    for hh in holes:
        L.append(f"hole {hh['pin']} {_f(hh['dx']):.3f} {_f(hh['dy']):.3f} {_f(hh['drill']):.3f}")
    L.append(f"body box {w - 1:.2f} {h - 1:.2f} 1.0")
    return "\n".join(L) + "\n"


def convert(projdir: str, outdir: str) -> str:
    """Mitox project → outdir/{mitox.ocd, fp/*.fp}. Returns .ocd path."""
    tsx_f = os.path.join(projdir, "index.circuit.tsx")
    cj_f = os.path.join(projdir, "index.circuit.circuit.json")
    src = open(tsx_f).read()
    cjson = cast(list[dict[str, object]], json.load(open(cj_f)))
    os.makedirs(os.path.join(outdir, "fp"), exist_ok=True)

    by_comp = {str(e["source_component_id"]): str(e.get("name", ""))
               for e in cjson if e.get("type") == "source_component"}
    pcb_of = {str(e.get("source_component_id")): str(e.get("pcb_component_id", ""))
              for e in cjson if e.get("type") == "pcb_component"}
    # invert: pcb_component_id → name
    pcb_name: dict[str, str] = {}
    for e in cjson:
        if e.get("type") == "pcb_component":
            pcb_name[str(e.get("pcb_component_id"))] = by_comp.get(
                str(e.get("source_component_id", "")), "")
    harvested = harvest_footprints(cjson)
    intent = tsx_elements(src)
    # tscircuit's own courtyard sizes (BOARD frame) — un-rotate to footprint
    # frame so our rot= model re-applies them identically
    tsci_wh: dict[str, tuple[float, float]] = {}
    for e in cjson:
        if e.get("type") == "pcb_component":
            nm = pcb_name.get(str(e.get("pcb_component_id", "")), "")
            if nm:
                w, h = (_f(e.get("width"), 2.0), _f(e.get("height"), 2.0))
                rot = int(_f(e.get("rotation", 0))) % 360
                tsci_wh[nm] = (h, w) if rot in (90, 270) else (w, h)

    # board: 24x56mm 4L from tsx
    bw, bh, layers = 24.0, 56.0, 4
    m = re.search(r"<board\s+width=\"([\d.]+)mm\"\s+height=\"([\d.]+)mm\"", src)
    if m:
        bw, bh = float(m.group(1)), float(m.group(2))
    m = re.search(r"layers=\{(\d+)\}", src)
    if m:
        layers = int(m.group(1))

    # nets via union-find (same as pico converter)
    ports: dict[str, tuple[str, str]] = {}
    for e in cjson:
        if e.get("type") == "source_port":
            ports[str(e["source_port_id"])] = (
                by_comp.get(str(e.get("source_component_id", "")), "?"),
                str(e.get("pin_number")))
    netname = {str(e["source_net_id"]): str(e.get("name", "?")) for e in cjson
               if e.get("type") == "source_net"}
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        while parent.get(a, a) != a:
            a = parent[a]
        return parent.setdefault(a, a)

    traces = [e for e in cjson if e.get("type") == "source_trace"]
    for e in traces:
        pids = cast(list[str], e.get("connected_source_port_ids", []))
        nids = cast(list[str], e.get("connected_source_net_ids", []))
        nodes = [f"P:{p}" for p in pids] + [f"N:{n}" for n in nids]
        for a, b in zip(nodes, nodes[1:]):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    set_label: dict[str, str] = {}
    for e in traces:
        pids = cast(list[str], e.get("connected_source_port_ids", []))
        nids = cast(list[str], e.get("connected_source_net_ids", []))
        labels = [netname[n] for n in nids if n in netname]
        if labels and pids:
            set_label.setdefault(find(f"P:{pids[0]}"), labels[0])
    disp: dict[str, str] = {}
    for e in traces:
        pids = cast(list[str], e.get("connected_source_port_ids", []))
        if pids:
            disp[find(f"P:{pids[0]}")] = str(e.get("display_name", ""))
    nets: dict[str, list[tuple[str, str]]] = {}
    for pid, (comp, pin) in ports.items():
        r = find(f"P:{pid}")
        label = set_label.get(r)
        if label is None:
            d = disp.get(r, "")
            mm = re.match(r"\.(\w+)\s*>\s*\.(\w+)", d)
            label = f"{mm.group(1)}_{mm.group(2)}" if mm else None
        if label is not None:
            nets.setdefault(label, []).append((comp, pin))
    # unconnected ports → nc
    intraces = {p for e in traces for p in cast(list[str], e.get("connected_source_port_ids", []))}
    ncs = sorted(f"{comp}.{pin}" for pid, (comp, pin) in ports.items() if pid not in intraces)

    # positions from tsx pcbX/pcbY (mm, centered) → .ocd origin
    cx, cy = bw / 2, bh / 2
    L = [f"board mitox {bw:g}x{bh:g} {layers}L",
         "# ported from mitox-prototype — footprints harvested exact, see fp/"]
    fp_files: dict[str, str] = {}  # fpname → filename
    rows: list[tuple[str, str, str]] = []  # (name, fpname, fn)
    for name in sorted({c for pins in nets.values() for c, _ in pins} | set(intent)):
        it = intent.get(name, {})
        lcsc = it.get("lcsc", "")
        fpname = f"FP_{lcsc}" if lcsc else f"FP_{name}"
        # find harvested geometry: match by component name
        cid = next((c for c, n in pcb_name.items() if n == name), "")
        geo = harvested.get(cid, {})
        pads = cast(list[dict[str, object]], geo.get("pads", []))
        holes = cast(list[dict[str, object]], geo.get("holes", []))
        if pads or holes:
            fn = f"fp/{fpname}.fp"
            if fpname not in fp_files:
                open(os.path.join(outdir, fn), "w").write(
                    fp_text(fpname, pads, holes, tsci_wh.get(name)))
                fp_files[fpname] = fn
        else:
            fpname = _std_fallback(name)
            fn = ""
        rows.append((name, fpname, fn))
    # rewrite part lines with fp includes up front
    parts: list[str] = []
    fps_emitted: list[str] = []
    for name, fpname, fn in rows:
        it = intent.get(name, {})
        if fn and f"fp {fn}" not in fps_emitted:
            fps_emitted.append(f"fp {fn}")
        val = it.get("value", "")
        attrs = f" lcsc={it['lcsc']}" if it.get("lcsc") else ""
        if it.get("rot", "0") != "0":
            attrs += f" rot={it['rot']}"
        parts.append(f"part {name} {fpname}{(' ' + val) if val else ''}{attrs}")
    L = L[:2] + fps_emitted + parts
    for net, pins in sorted(nets.items()):
        if len(pins) >= 2 or net in ("GND", "VBUS", "V3V3", "VBAT", "VSYS"):
            L.append(f"net {net}: " + " ".join(f"{c}.{pin}" for c, pin in pins))
    # positions as fix
    for name, it in sorted(intent.items()):
        if it.get("x") and it.get("y"):
            L.append(f"fix {name} at {float(it['x']) + cx:g} {float(it['y']) + cy:g}")
    # pours / keepouts / cutout / holes from tsx + circuit.json
    L.append("pour GND on 0")
    L.append("pour GND on 3")
    for e in cjson:
        if e.get("type") == "pcb_keepout":
            cen = cast(dict[str, object], e.get("center", {}))
            L.append(f"keepout {_f(cen.get('x')) + cx:g} {_f(cen.get('y')) + cy:g} "
                     f"{_f(e.get('width')):g}x{_f(e.get('height')):g}")
        elif e.get("type") == "pcb_cutout":
            cen = cast(dict[str, object], e.get("center", {}))
            L.append(f"cutout {_f(cen.get('x')) + cx:g} {_f(cen.get('y')) + cy:g} "
                     f"{_f(e.get('width')):g}x{_f(e.get('height')):g}")
    for m in re.finditer(r"<hole\s+diameter=\"([\d.]+)mm\"\s+pcbX=\"(-?[\d.]+)mm\"\s+pcbY=\"(-?[\d.]+)mm\"", src):
        L.append(f"hole {float(m.group(2)) + cx:g} {float(m.group(3)) + cy:g} {m.group(1)}")
    for i in range(0, len(ncs), 12):
        L.append("nc " + " ".join(ncs[i:i + 12]))
    if "GND" in nets or True:
        L.append("power VBUS VBAT VSYS V3V3 GND")
    fn = os.path.join(outdir, "mitox.ocd")
    open(fn, "w").write("\n".join(L) + "\n")
    return fn


def _std_fallback(name: str) -> str:
    if name.startswith("R"):
        return "R0402"
    if name.startswith("C"):
        return "C0402"
    raise ValueError(f"no harvested geometry and no fallback for {name!r}")


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m tools.mitox <mitox-projdir> <outdir>", file=sys.stderr)
        raise SystemExit(1)
    try:
        print(convert(sys.argv[1], sys.argv[2]))
    except (OSError, ValueError, KeyError, AssertionError) as e:
        print(f"mitox-port: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
