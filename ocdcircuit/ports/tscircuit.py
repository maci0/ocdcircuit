"""tscircuit → .ocd port tool. circuit.json is authoritative (resolved nets,
pin numbers, positions); index.circuit.tsx supplies footprint strings +
values + board size.

Maps: qfn28/pinrow4/dip40/0805/1206/1210 → our lib; tscircuit centered
coords → .ocd origin; author positions kept as fix (re-solve freely).

Usage: python ports/tscircuit.py <project-dir> > out.ocd
"""
from __future__ import annotations
import json
import os
import re
import sys
from typing import cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root (ports/ lives in-package)

FP_MAP = {
    "qfn28": "QFN28", "0805": "C0805", "1206": "R1206", "1210": "C1210",
    "pinrow4": "PINHD4", "dip40_w17.78mm_p2.54mm": "PINHD2X20",
    "0402": "R0402", "0603": "R0603", "soic8": "SOIC8",
}


def map_fp(tsx_fp: str, kind: str) -> str:
    """tscircuit footprint string → our footprint. kind disambiguates 0805."""
    key = tsx_fp.strip().strip('"').lower()
    if key in FP_MAP:
        fp = FP_MAP[key]
        if key == "0805":
            fp = "R0805" if kind == "resistor" else "C0805"
        return fp
    m = re.match(r"(\d{4})$", key)
    if m:
        return ("R" if kind == "resistor" else "C") + m.group(1)
    raise ValueError(f"no footprint mapping for {tsx_fp!r} (add to FP_MAP)")


def tsx_parts(src: str) -> dict[str, dict[str, str]]:
    """{name: {kind, fp, value}} from tsx elements. Handles macro templates:
    a DriverChannel-style block declaring footprints once + instances
    (name="Z1") gets its footprints fanned out by element kind+order."""
    out: dict[str, dict[str, str]] = {}
    # literal parts first
    for kind in ("chip", "capacitor", "resistor", "jumper"):
        for m in re.finditer(rf"<{kind}\s+([^>]*?)/?>", src, re.S):
            attrs = m.group(1)
            name = _attr(attrs, "name")
            if not name or "{" in name:
                continue
            out[name] = {"kind": kind, "fp": _attr(attrs, "footprint"),
                         "value": _attr(attrs, "capacitance") or _attr(attrs, "resistance")}
    # template blocks: name={`C${name}v`} / name={name} + instances <Block name="INST">
    pat_bt = r"name=\{`[^`]*\$\{name\}[^`]*`\}"
    for kind in ("chip", "capacitor", "resistor", "jumper"):
        tmpl = [(_attr(m.group(1), "footprint"),
                 _attr(m.group(1), "capacitance") or _attr(m.group(1), "resistance"),
                 _nametmpl(m.group(1)))
                for m in re.finditer(rf"<{kind}\s+([^>]*{pat_bt}[^>]*)/?>", src, re.S)]
        tmpl += [(_attr(m.group(1), "footprint"),
                  _attr(m.group(1), "capacitance") or _attr(m.group(1), "resistance"),
                  "{name}")
                 for m in re.finditer(rf"<{kind}\s+([^>]*name=\{{name\}}[^>]*)/?>", src, re.S)]
        if not tmpl:
            continue
        # find enclosing component + its instances: const Name = (...) => (
        for bm in re.finditer(r"const\s+(\w+)\s*=\s*\([^)]*\)\s*=>\s*\(", src):
            block = bm.group(1)
            insts = re.findall(rf"<{block}\s+[^>]*name=\"(\w+)\"", src)
            if not insts:
                continue
            # element order within block for this kind
            body = src[bm.start():src.find("/>", bm.start()) + 2000]
            # map by suffix convention: chip→{I}, cap→C{I}v/cp, res→R{I}a/b, jumper→J{I}
            for inst in insts:
                cands: list[str] = []
                if kind == "chip":
                    cands = [inst]
                elif kind == "capacitor":
                    cands = [f"C{inst}v", f"C{inst}cp"]
                elif kind == "resistor":
                    cands = [f"R{inst}a", f"R{inst}b"]
                elif kind == "jumper":
                    cands = [f"J{inst}"]
                for ci, cname in enumerate(cands):
                    if ci < len(tmpl) and cname not in out:
                        fp, val, _nt = tmpl[ci]
                        out[cname] = {"kind": kind, "fp": fp, "value": val}
    return out


def _attr(attrs: str, key: str) -> str:
    m = re.search(rf"{key}=\{{\"([^\"]*)\"\}}", attrs) or re.search(rf'{key}="([^"]*)"', attrs)
    return m.group(1) if m else ""


def _nametmpl(attrs: str) -> str:
    """name={`C${name}v`} → C{name}v template form."""
    m = re.search(r"name=\{`([^`]*)`\}", attrs)
    if not m:
        return "{name}"
    return m.group(1).replace("${name}", "{name}")


def convert(projdir: str) -> str:
    """Project dir → .ocd text. Raises on unmapped footprints."""
    tsx_f = os.path.join(projdir, "index.circuit.tsx")
    cj_f = os.path.join(projdir, "dist", "index", "circuit.json")
    src = open(tsx_f).read()
    cjson = cast(list[dict[str, object]], json.load(open(cj_f)))
    by_id = {str(e["source_component_id"]): e for e in cjson
             if e.get("type") == "source_component"}

    def comp_name(cid: str) -> str:
        e = by_id.get(cid, {})
        n = e.get("name", cid)
        return str(n)

    # board size from tsx + pcb_board
    bw, bh = 100.0, 80.0
    m = re.search(r"<board\s+width=\"([\d.]+)mm\"\s+height=\"([\d.]+)mm\"", src)
    if m:
        bw, bh = float(m.group(1)), float(m.group(2))
    for e in cjson:
        if e.get("type") == "pcb_board":
            _w, _h = e.get("width", bw), e.get("height", bh)
            assert isinstance(_w, (int, float)) and isinstance(_h, (int, float))
            bw, bh = float(_w), float(_h)

    # tsx intent: footprint/value per part (match real names, else template)
    tmpl = tsx_parts(src)

    def intent(name: str) -> dict[str, str]:
        if name in tmpl:
            return tmpl[name]
        for suffix in ("Z1", "Z2", "Z3"):
            if name.startswith(suffix) or name.endswith(("v", "cp", "a", "b")) and name[1:] in ("",):
                pass
        # template keys look like "Cv", "RZa", "J" (name=${name} stripped)
        core = name
        for pre in ("Z1", "Z2", "Z3"):
            if core.startswith(pre):
                core = core[len(pre):]
                break
        return tmpl.get(core, tmpl.get("C" + core, tmpl.get("R" + core, tmpl.get("J" + core, {}))))

    # nets via union-find over traces (ports ∪ nets)
    ports: dict[str, tuple[str, str]] = {}
    for e in cjson:
        if e.get("type") == "source_port":
            ports[str(e["source_port_id"])] = (
                comp_name(str(e.get("source_component_id", ""))), str(e.get("pin_number")))
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
    # label each set: prefer a real net name touched by its traces
    set_label: dict[str, str] = {}
    for e in traces:
        pids = cast(list[str], e.get("connected_source_port_ids", []))
        nids = cast(list[str], e.get("connected_source_net_ids", []))
        labels = [netname[n] for n in nids if n in netname]
        if not labels or not pids:
            continue
        r = find(f"P:{pids[0]}")
        set_label.setdefault(r, labels[0])
    # label each set: prefer a real net name; else derive from display_name
    # (".Z1 > .STEP to .PICO .Z1S" → Z1_STEP); keep unnamed only if ≥2 pins
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
            m = re.match(r"\.(\w+)\s*>\s*\.(\w+)", d)
            label = f"{m.group(1)}_{m.group(2)}" if m else f"X_{r[-6:]}"
        nets.setdefault(label, []).append((comp, pin))
    nets = {k: v for k, v in nets.items() if len(v) >= 2}
    # ports in no trace at all → nc (intentionally unconnected, ERC-exempt)
    intraces = {p for e in traces for p in cast(list[str], e.get("connected_source_port_ids", []))}
    ncs = sorted(f"{comp}.{pin}" for pid, (comp, pin) in ports.items() if pid not in intraces)

    # positions from pcb_component
    pos: dict[str, tuple[float, float]] = {}
    for e in cjson:
        if e.get("type") == "pcb_component":
            c = by_id.get(str(e.get("source_component_id", "")), {})
            cen = cast(dict[str, object], e.get("center", {"x": 0, "y": 0}))
            _x, _y = cen.get("x", 0), cen.get("y", 0)
            assert isinstance(_x, (int, float)) and isinstance(_y, (int, float))
            pos[str(c.get("name", ""))] = (float(_x), float(_y))

    name = os.path.basename(os.path.normpath(projdir)).replace("-tscircuit", "").replace("-", "_")
    cx, cy = bw / 2, bh / 2
    L = [f"board {name} {bw:g}x{bh:g} 2L"]
    L.append(f"# ported from {tsx_f} — positions kept as fix, re-solve freely")
    comps = sorted({c for pins in nets.values() for c, _ in pins} | set(pos))
    fp_of: dict[str, str] = {}
    for cname in comps:
        it = intent(cname)
        if not it.get("fp"):
            raise ValueError(f"no footprint intent for {cname!r} (tsx templates: {sorted(tmpl)})")
        fp = map_fp(it["fp"], it.get("kind", "chip"))
        fp_of[cname] = fp
        val = f" {it['value']}" if it.get("value") else ""
        L.append(f"part {cname} {fp}{val}")
    for net, pins in sorted(nets.items()):
        L.append(f"net {net}: " + " ".join(f"{c}.{pin}" for c, pin in pins))
    # QFN exposed pads → own GND net (near-universal; edit .ocd if exotic)
    from ocdcircuit.parts import pads_of
    def _has_ep(c: str) -> bool:
        try:
            return "EP" in pads_of(fp_of.get(c, ""))
        except KeyError:
            return False
    eps = [c for c in comps if _has_ep(c)]
    if eps:
        L.append("net GND_EP: " + " ".join(f"{c}.EP" for c in sorted(eps)))
    for cname in comps:
        if cname in pos:
            x, y = pos[cname]
            L.append(f"fix {cname} at {x + cx:g} {y + cy:g}")
    for i in range(0, len(ncs), 12):
        L.append("nc " + " ".join(ncs[i:i + 12]))
    if any(n in nets for n in ("VM", "V3V3", "GND", "5V")):
        L.append("power " + " ".join(n for n in ("VM", "V3V3", "GND", "5V", "3V3") if n in nets))
    return "\n".join(L) + "\n"


def _guess_fp(cname: str) -> str:
    """Fallback by naming convention when tsx intent is missing."""
    if cname.startswith("R"):
        return "R0805"
    if cname.startswith("C"):
        return "C0805"
    if cname.startswith("J"):
        return "PINHD4"
    raise ValueError(f"no footprint intent for {cname!r}")


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python ports/tscircuit.py <project-dir>", file=sys.stderr)
        raise SystemExit(1)
    try:
        print(convert(sys.argv[1]), end="")
    except (OSError, ValueError) as e:
        print(f"tscircuit-port: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
