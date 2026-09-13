"""Atopile → .ocd port. Parses main.ato (signals, `new` instances, `~`
wiring) + parts/*.ato (footprint .kicad_mod refs, LCSC) + layout positions
from the built .kicad_pcb.

`~` union-find over (instance.pin ∪ signal) gives nets directly — the .ato
IS the netlist, no JSON needed. Footprints import via our kicad importer.

Usage: python tools/atopile.py <atopile-projdir> <outdir>
"""
from __future__ import annotations
import os
import re
import sys
from typing import cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('"""'):
            continue
        out.append(re.sub(r"#.*$", "", line))
    return "\n".join(out)


def parse_main(text: str) -> tuple[list[str], dict[str, str], list[tuple[str, str, str]]]:
    """→ (signals, instances {var: component}, wires [(lhs, op, rhs)]).
    lhs/rhs are dotted (j1.p1) or bare signals."""
    src = _strip_comments(text)
    signals = re.findall(r"^\s*signal\s+(\w+)", src, re.M)
    insts: dict[str, str] = {}
    for m in re.finditer(r"^\s*(\w+)\s*=\s*new\s+(\w+)", src, re.M):
        insts[m.group(1)] = m.group(2)
    wires: list[tuple[str, str, str]] = []
    for m in re.finditer(r"^\s*([\w.]+)\s*(~|>)\s*([\w.]+)", src, re.M):
        wires.append((m.group(1), m.group(2), m.group(3)))
    return signals, insts, wires


def parse_parts(text: str) -> dict[str, dict[str, object]]:
    """{component: {fp (kicad_mod file), lcsc, pins {signal: pinnum}}}."""
    src = _strip_comments(text)
    out: dict[str, dict[str, object]] = {}
    for m in re.finditer(r"component\s+(\w+)\s*:(.*?)(?=component\s+\w+\s*:|\Z)", src, re.S):
        name, body = m.group(1), m.group(2)
        fp = ""
        fm = re.search(r'footprint="([^"]+)"', body)
        if fm:
            fp = fm.group(1)
        lcsc = ""
        lm = re.search(r'supplier_partno="([^"]+)"', body)
        if lm:
            lcsc = lm.group(1)
        pins: dict[str, str] = {}
        for pm in re.finditer(r"^\s*signal\s+(\w+)\s*~\s*pin\s+(\w+)", body, re.M):
            pins[pm.group(1)] = pm.group(2)
        out[name] = {"fp": fp, "lcsc": lcsc, "pins": pins}
    return out


def positions_from_pcb(path: str) -> tuple[dict[str, tuple[float, float]],
                                           float, float]:
    """{ref: (x, y)} in mm + board w/h from Edge.Cuts bbox of a .kicad_pcb."""
    from ocdcircuit.foreign import sexpr, _kids, _unq, _num, _footprint_ref
    root = sexpr(open(path).read())
    refs: dict[str, tuple[float, float]] = {}
    for fp in _kids(root, "footprint"):
        ref = _footprint_ref(fp)
        at = next((c for c in fp[1:] if isinstance(c, list) and c and c[0] == "at"), None)
        x = _num(at[1]) if at and len(at) > 1 else 0.0
        y = _num(at[2]) if at and len(at) > 2 else 0.0
        if ref:
            refs[ref] = (x, y)
    xs: list[float] = []
    ys: list[float] = []
    for gr in _kids(root, "gr_line"):
        for tag in ("start", "end"):
            pt = next((c for c in gr[1:] if isinstance(c, list) and c and c[0] == tag), None)
            if pt and len(pt) > 2:
                xs.append(_num(pt[1]))
                ys.append(_num(pt[2]))
    w = max(xs) - min(xs) if xs else 40.0
    h = max(ys) - min(ys) if ys else 30.0
    ox, oy = (min(xs), min(ys)) if xs else (0.0, 0.0)
    # KiCad Y grows down, ours grows up → flip
    return ({r: (x - ox, h - (y - oy)) for r, (x, y) in refs.items()}, w, h)


def convert(projdir: str, outdir: str) -> str:
    """Atopile project → outdir/{name.ocd, fp/*.fp}. Returns .ocd path."""
    main_f = os.path.join(projdir, "atopile", "main.ato")
    src = open(main_f).read()
    signals, insts, wires = parse_main(src)
    parts_info: dict[str, dict[str, object]] = {}
    parts_dir = os.path.join(projdir, "atopile", "parts")
    for root, _ds, fs in os.walk(parts_dir):
        for fn in fs:
            if fn.endswith(".ato"):
                parts_info.update(parse_parts(open(os.path.join(root, fn)).read()))
    # instance refs: J-designator order of declaration
    refs: dict[str, str] = {}
    counters: dict[str, int] = {}
    for var in insts:
        counters["J"] = counters.get("J", 0) + 1
        refs[var] = f"J{counters['J']}"
    # union-find wires
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        while parent.get(a, a) != a:
            a = parent[a]
        return parent.setdefault(a, a)

    # pin map per instance: ato signal (p8) → physical pin (8)
    pinmap: dict[str, dict[str, str]] = {}
    for var, comp in insts.items():
        info = parts_info.get(comp, {})
        pins = info.get("pins", {})
        assert isinstance(pins, dict)
        pinmap[var] = {str(k): str(v) for k, v in pins.items()}

    def phys(var: str, pin: str) -> str:
        return f"{refs.get(var, var)}.{pinmap.get(var, {}).get(pin, pin)}"

    for lhs, _op, rhs in wires:
        if "." in lhs:
            v, p = lhs.split(".")
            a = phys(v, p)
        else:
            a = lhs
        b = rhs
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    nets: dict[str, list[str]] = {}
    for var in insts:
        ref = refs[var]
        pinset = {w[0].split(".")[1] for w in wires if w[0].startswith(var + ".")}
        pinset |= {w[2].split(".")[1] for w in wires if w[2].startswith(var + ".")}
        for pin in pinset:
            pp = pinmap.get(var, {}).get(pin, pin)
            r = find(f"{ref}.{pp}")
            nets.setdefault(r, []).append(f"{ref}.{pp}")
    # name each set after its signal member (or first pin)
    named: dict[str, list[str]] = {}
    sigset = set(signals)
    for r, pinlist in nets.items():
        signame = ""
        for s in sigset:
            if find(s) == r:
                signame = s
                break
        named.setdefault(signame or f"X_{r[-6:]}", []).extend(pinlist)
    # positions from layout pcb (match by order if refs differ)
    pos: dict[str, tuple[float, float]] = {}
    bw, bh = 40.0, 30.0
    laydir = os.path.join(projdir, "atopile", "layouts")
    if os.path.isdir(laydir):
        for root, _ds, fs in os.walk(laydir):
            for fn in fs:
                if fn.endswith(".kicad_pcb"):
                    pos, bw, bh = positions_from_pcb(os.path.join(root, fn))
                    break
            if pos:
                break
    os.makedirs(os.path.join(outdir, "fp"), exist_ok=True)
    name = os.path.basename(os.path.normpath(projdir)).replace("-", "_")
    L = [f"board {name} {bw:g}x{bh:g} 2L",
         "# ported from atopile main.ato — signals + ~ wiring preserved"]
    # footprints: resolve .kicad_mod files next to parts
    fps_emitted: list[str] = []
    modemap: dict[str, str] = {}  # component → fp name in our lib
    for var, comp in insts.items():
        info = parts_info.get(comp, {})
        fpfile = info.get("fp", "")
        assert isinstance(fpfile, str)
        ref = refs[var]
        fpname = os.path.splitext(os.path.basename(fpfile))[0] if fpfile else f"FP_{ref}"
        src_fp = ""
        for root, _ds, fs in os.walk(parts_dir):
            if os.path.basename(fpfile) in fs:
                src_fp = os.path.join(root, os.path.basename(fpfile))
                break
        if src_fp:
            dst = os.path.join(outdir, "fp", os.path.basename(fpfile))
            import shutil
            shutil.copy(src_fp, dst)  # verbatim; our kicad importer parses it
            if f"fp fp/{os.path.basename(fpfile)}" not in fps_emitted:
                fps_emitted.append(f"fp fp/{os.path.basename(fpfile)}")
            modemap[ref] = fpname
        else:
            modemap[ref] = f"FP_{ref}"
    L += fps_emitted
    for var, comp in insts.items():
        ref = refs[var]
        info = parts_info.get(comp, {})
        lcsc = info.get("lcsc", "")
        assert isinstance(lcsc, str)
        attrs = f" lcsc={lcsc}" if lcsc else ""
        L.append(f"part {ref} {modemap[ref]}{attrs}")
    for net, pinlist in sorted(named.items()):
        if len(pinlist) >= 2 or net in ("GND",):
            L.append(f"net {net}: " + " ".join(sorted(set(pinlist))))
    # author positions: keep only ones that pass OUR overlap/edge DRC —
    # stale layouts pin known-bad coordinates (bme690 J1/J3 overlap in the
    # committed .kicad_pcb). Verify-then-pin, never blind-pin.
    from ocdcircuit import agent as _agent
    probe = _agent.loads("\n".join(L) + "\n", base=outdir)
    for ref, (x, y) in sorted(pos.items()):
        if ref in modemap and ref in probe.parts:
            part = probe.parts[ref]
            pw, ph = part.wh()
            # compare against fellow fixed candidates (probe coords are center)
            clash = any(
                o != ref and abs(x - ox) < (pw + probe.parts[o].wh()[0]) / 2 + 0.1
                and abs(y - oy) < (ph + probe.parts[o].wh()[1]) / 2 + 0.1
                for o, (ox, oy) in pos.items() if o in probe.parts)
            inside = (pw / 2 + 0.3 <= x <= bw - pw / 2 - 0.3 and
                      ph / 2 + 0.3 <= y <= bh - ph / 2 - 0.3)
            if not clash and inside:
                L.append(f"fix {ref} at {x:g} {y:g}")
            else:
                L.append(f"# fix {ref} at {x:g} {y:g} — dropped: stale layout "
                         f"({'overlap' if clash else 'off-board'}), solver places freely")
    if "GND" in named:
        L.append("power GND")
    fn = os.path.join(outdir, f"{name}.ocd")
    open(fn, "w").write("\n".join(L) + "\n")
    return fn


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python tools/atopile.py <atopile-projdir> <outdir>", file=sys.stderr)
        raise SystemExit(1)
    try:
        print(convert(sys.argv[1], sys.argv[2]))
    except (OSError, ValueError) as e:
        print(f"atopile-port: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
