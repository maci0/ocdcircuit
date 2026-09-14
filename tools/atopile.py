"""Atopile → .ocd port. Parses main.ato (signals, `new` instances, `~`
wiring) + parts/*.ato (footprint .kicad_mod refs, LCSC) + layout positions
from the built .kicad_pcb.

`~` union-find over (instance.pin ∪ signal) gives nets directly — the .ato
IS the netlist, no JSON needed. Footprints import via our kicad importer.

Usage: python -m tools.atopile <atopile-projdir> <outdir>
"""
from __future__ import annotations
import os
import re
import sys
from typing import cast



def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('"""'):
            continue
        out.append(re.sub(r"#.*$", "", line))
    return "\n".join(out)


def _wire_stmts(line: str) -> list[tuple[str, str, str]]:
    """All `a ~ b` statements on a line (`;`-separated atopile style)."""
    out: list[tuple[str, str, str]] = []
    for stmt in line.split(";"):
        m = re.match(r"^\s*([\w.]+)\s*(~|>)\s*([\w.\[\]]+)", stmt)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def parse_main(text: str) -> tuple[list[str], dict[str, str], list[tuple[str, str, str]]]:
    """→ (signals, instances {var: component}, wires [(lhs, op, rhs)]).
    lhs/rhs are dotted (j1.p1) or bare signals. Hierarchical `module M:`
    blocks elaborate inline: `x = new M` stamps prefixed copies
    (x_var, x_port signals), so the rest of the pipeline stays flat."""
    return _parse_with(text, {})[0:3]


_ModTab = dict[str, tuple[list[str], dict[str, str], list[tuple[str, str, str]]]]


def _parse_with(text: str, modules: _ModTab
                ) -> tuple[list[str], dict[str, str], list[tuple[str, str, str]],
                           _ModTab, dict[str, tuple[str, str]]]:
    """parse_main + module table + elaboration scope (var → (module, local)).
    Sibling files merge their modules here."""
    src = _strip_comments(text)
    for m in re.finditer(r"^module\s+(\w+)\s*:(.*?)(?=^module\s+\w+\s*:|\Z)",
                         src, re.M | re.S):
        msig: list[str] = []
        minst: dict[str, str] = {}
        mwires: list[tuple[str, str, str]] = []
        for line in m.group(2).splitlines():
            sm = re.match(r"^\s*signal\s+(\w+)", line)
            if sm:
                msig.append(sm.group(1))
                continue
            im = re.match(r"^\s*(\w+)\s*=\s*new\s+(\w+)", line)
            if im:
                minst[im.group(1)] = im.group(2)
                continue
            mwires += _wire_stmts(line)
        modules[m.group(1)] = (msig, minst, mwires)
    pre = re.split(r"^module\s+\w+\s*:", src, flags=re.M)[0]
    signals = re.findall(r"^\s*signal\s+(\w+)", pre, re.M)
    insts: dict[str, str] = {}
    for m in re.finditer(r"^\s*(\w+)\s*=\s*new\s+(\w+)", pre, re.M):
        insts[m.group(1)] = m.group(2)
    wires: list[tuple[str, str, str]] = []
    for line in pre.splitlines():
        wires += _wire_stmts(line)
    # `module App:` is the root when present (flat files have no modules)
    if "App" in modules:
        _s, _i, _w = modules.pop("App")
        signals += [s for s in _s if s not in signals]
        insts.update(_i)
        wires += _w
    # elaborate: module instances become flat prefixed copies.
    # - internal wires: bare signals qualify NOW (once): s → var_s
    # - existing wires: only var.rest endpoints rewrite (ports); bare
    #   App signals are a different namespace — never touch them.
    def _local(tok: str, var: str, sigs: set[str],
               ivars: set[str]) -> str:
        if "." in tok:
            v, rest = tok.split(".", 1)
            if v in ivars:
                return f"{var}_{v}.{rest}"
            return tok
        return f"{var}_{tok}" if tok in sigs else tok

    def _port(tok: str, var: str) -> str:
        if "." in tok:
            v, rest = tok.split(".", 1)
            return f"{var}_{rest}" if v == var else tok
        return tok

    # loop: nested modules (core instantiates Indicator) expand bottom-up.
    # scope tracks each elaborated var's (module, localname) for .package
    # lookup; each expansion overwrites (innermost wins, children re-expand).
    scope: dict[str, tuple[str, str]] = {}
    for _ in range(8):  # ponytail: depth cap, atopile allows recursion;
        # raise (or loop to fixpoint) if a real project nests deeper than 8
        todo = [(v, c) for v, c in insts.items() if c in modules]
        if not todo:
            break
        for var, comp in todo:
            del insts[var]
            scope.pop(var, None)
            msig, minst, mwires = modules[comp]
            sigs = set(msig)
            for v, c in minst.items():
                insts[f"{var}_{v}"] = c
                scope[f"{var}_{v}"] = (comp, v)
            ivars = set(minst)
            for lhs, op, rhs in mwires:
                wires.append((_local(lhs, var, sigs, ivars), op,
                              _local(rhs, var, sigs, ivars)))
            wires = [(_port(a, var), op, _port(b, var))
                     for a, op, b in wires]
    return signals, insts, wires, modules, scope


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
                                           float, float,
                                           dict[str, str]]:
    """{ref: (x, y)} in mm + board w/h + {ref: footprint} from a .kicad_pcb."""
    from ocdcircuit.foreign import sexpr, _kids, _unq, _num, _footprint_ref
    root = sexpr(open(path).read())
    refs: dict[str, tuple[float, float]] = {}
    fps: dict[str, str] = {}
    for fp in _kids(root, "footprint"):
        ref = _footprint_ref(fp)
        at = next((c for c in fp[1:] if isinstance(c, list) and c and c[0] == "at"), None)
        x = _num(at[1]) if at and len(at) > 1 else 0.0
        y = _num(at[2]) if at and len(at) > 2 else 0.0
        if ref:
            refs[ref] = (x, y)
            fps[ref] = _unq(fp[1]).split(":")[-1] if len(fp) > 1 else ""
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
    return ({r: (x - ox, h - (y - oy)) for r, (x, y) in refs.items()}, w, h, fps)


def convert(projdir: str, outdir: str) -> str:
    """Atopile project → outdir/{name.ocd, fp/*.fp}. Returns .ocd path."""
    main_f = os.path.join(projdir, "atopile", "main.ato")
    src = open(main_f).read()
    # sibling-file modules first: follow `from "x.ato" import Y` (SSSdriver)
    _mods: dict[str, tuple[list[str], dict[str, str], list[tuple[str, str, str]]]] = {}
    _seen_files = {os.path.abspath(main_f)}
    for m in re.finditer(r'from\s+"([^"]+\.ato)"\s+import\s+(\w+)', src):
        _fp = os.path.normpath(os.path.join(os.path.dirname(main_f), m.group(1)))
        if _fp not in _seen_files and os.path.isfile(_fp):
            _seen_files.add(_fp)
            _, _, _, _mods, _ = _parse_with(open(_fp).read(), _mods)
    signals, insts, wires, _, _scope = _parse_with(src, _mods)
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

    def _side(tok: str) -> str:
        if "." in tok:
            v, _, p = tok.partition(".")
            return phys(v, re.sub(r"\[\d+\]", "", p))
        return tok

    for lhs, _op, rhs in wires:
        ra, rb = find(_side(lhs)), find(_side(rhs))
        if ra != rb:
            parent[rb] = ra
    nets: dict[str, list[str]] = {}
    for var in insts:
        ref = refs[var]

        def _pin_of(w: str) -> str:
            return re.sub(r"\[\d+\]", "", w.split(".", 1)[1])

        pinset = {_pin_of(w[0]) for w in wires if w[0].startswith(var + ".")}
        pinset |= {_pin_of(w[2]) for w in wires if w[2].startswith(var + ".")}
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
    # knoll autoplace emit: {fixed: {dotted.ref: [x, y, rot?]}} —
    # dotted refs match elaboration inputs (core.rx2 → core_rx2 → ref).
    # Loaded here so rotations land on part lines; positions join pinning.
    import json as _json
    _placed: dict[str, tuple[float, float, float]] = {}
    _pj = os.path.join(projdir, "placement.json")
    if os.path.isfile(_pj):
        try:
            _fix = _json.load(open(_pj)).get("fixed", {})
            assert isinstance(_fix, dict)
            for _vr, _xy in _fix.items():
                assert isinstance(_xy, list) and len(_xy) >= 2
                _placed[_vr.replace(".", "_")] = (float(_xy[0]), float(_xy[1]),
                                                  float(_xy[2]) if len(_xy) > 2 else 0.0)
        except (ValueError, AssertionError, IndexError):
            _placed = {}
    # positions from layout pcb (match by order if refs differ)
    pos: dict[str, tuple[float, float]] = {}
    lay_fp: dict[str, str] = {}
    bw, bh = 40.0, 30.0
    laydir = os.path.join(projdir, "atopile", "layouts")
    if os.path.isdir(laydir):
        for root, _ds, fs in os.walk(laydir):
            for fn in fs:
                if fn.endswith(".kicad_pcb"):
                    pos, bw, bh, lay_fp = positions_from_pcb(os.path.join(root, fn))
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
    # generics (Resistor/Capacitor/LED) take packages from `var.package`
    # assignments, scoped per module then mapped onto elaborated var names
    # (z1_r16 descends from instance z1 of a module declaring r16.package)
    _varpkg: dict[str, str] = {}
    for _f, _txt in [(main_f, src)] + [(_fp, open(_fp).read()) for _fp in _seen_files
                                       if _fp != os.path.abspath(main_f)]:
        _sc = _strip_comments(_txt)
        for _m in re.finditer(r"^module\s+(\w+)\s*:(.*?)(?=^module\s+\w+\s*:|\Z)",
                              _sc, re.M | re.S):
            for _pm in re.finditer(r"^\s*(\w+)\.package\s*=\s*\"([^\"]+)\"",
                                   _m.group(2), re.M):
                _varpkg[f"{_m.group(1)}.{_pm.group(1)}"] = _pm.group(2)
                if _m.group(1) == "App":
                    _varpkg[_pm.group(1)] = _pm.group(2)
        for _pm in re.finditer(r"^\s*(\w+)\.package\s*=\s*\"([^\"]+)\"",
                               re.split(r"^module\s+\w+\s*:", _sc, flags=re.M)[0], re.M):
            _varpkg[_pm.group(1)] = _pm.group(2)

    def _pkg_of(var: str) -> str:
        # z1_r_uart → SSSDriver.r_uart via elaboration scope; c19 → App.c19.
        if var in _scope:
            mod, local = _scope[var]
            if f"{mod}.{local}" in _varpkg:
                return _varpkg[f"{mod}.{local}"]
        for key in (f"App.{var}", var):
            if key in _varpkg:
                return _varpkg[key]
        return ""

    for var, comp in insts.items():
        info = parts_info.get(comp, {})
        fpfile = info.get("fp", "")
        assert isinstance(fpfile, str)
        ref = refs[var]
        if not fpfile:
            fpfile = _pkg_of(var)
        fpname = os.path.splitext(os.path.basename(fpfile))[0] if fpfile else f"FP_{ref}"
        src_fp = ""
        for root, _ds, fs in os.walk(parts_dir):
            if os.path.basename(fpfile) in fs:
                src_fp = os.path.join(root, os.path.basename(fpfile))
                break
        from ocdcircuit.parts import FOOTPRINTS, resolve_fp
        if src_fp:
            if fpname in FOOTPRINTS:
                modemap[ref] = fpname  # std land pattern — no import needed
                continue
            dst = os.path.join(outdir, "fp", os.path.basename(fpfile))
            import shutil
            shutil.copy(src_fp, dst)  # verbatim; our kicad importer parses it
            if f"fp fp/{os.path.basename(fpfile)}" not in fps_emitted:
                fps_emitted.append(f"fp fp/{os.path.basename(fpfile)}")
            modemap[ref] = fpname
        elif fpname in FOOTPRINTS:
            modemap[ref] = fpname  # package names a std footprint directly
        elif resolve_fp(fpname) in FOOTPRINTS:
            modemap[ref] = resolve_fp(fpname)  # KiCad alias — no import needed
        else:
            hint = {"Resistor": "R0805", "Capacitor": "C0805",
                    "LED": "LED0805"}.get(comp, "R0805")
            raise ValueError(
                f"part {ref} ({var} = new {comp}): no footprint — add "
                f"`{var}.package = \"{hint}\"` to the .ato")
    L += fps_emitted
    # placement.json rotations apply here (knoll autoplace emit: dotted
    # hierarchical refs + optional [x, y, rot]); positions join the
    # verify-then-pin pass below.
    _prot: dict[str, float] = {}
    for _vr, _xy in _placed.items():
        if _vr in refs and len(_xy) > 2 and _xy[2] not in (0.0, 0):
            _prot[refs[_vr]] = _xy[2]
    for var, comp in insts.items():
        ref = refs[var]
        info = parts_info.get(comp, {})
        lcsc = info.get("lcsc", "")
        assert isinstance(lcsc, str)
        attrs = f" lcsc={lcsc}" if lcsc else ""
        if ref in _prot:
            attrs += f" rot={_prot[ref]:g}"
        L.append(f"part {ref} {modemap[ref]}{attrs}")
    for net, pinlist in sorted(named.items()):
        if len(pinlist) >= 2 or net in ("GND",):
            L.append(f"net {net}: " + " ".join(sorted(set(pinlist))))
    # author positions: keep only ones that pass OUR overlap/edge DRC —
    # stale layouts pin known-bad coordinates (bme690 J1/J3 overlap in the
    # committed .kicad_pcb). Verify-then-pin, never blind-pin.
    from ocdcircuit import agent as _agent
    probe = _agent.loads("\n".join(L) + "\n", base=outdir)
    # layout refs rarely match porter refs: remap by footprint+order
    by_fp: dict[str, list[str]] = {}
    for ref in probe.parts:
        by_fp.setdefault(modemap.get(ref, ""), []).append(ref)
    lay_by_fp: dict[str, list[str]] = {}
    for ref, fp in lay_fp.items():
        lay_by_fp.setdefault(fp, []).append(ref)
    remap: dict[str, tuple[float, float]] = {}
    for fp, layrefs in lay_by_fp.items():
        cands = by_fp.get(fp, [])
        for lr, pr in zip(sorted(layrefs), sorted(cands)):
            remap[pr] = pos[lr]
    # placement.json (dotted hierarchical refs) overlays the pcb remap:
    # elaborated var names already use _ for . (core_rx2 == core.rx2).
    # Rotations already landed on part lines above; fix takes x y only.
    for vr, xy in _placed.items():
        if vr in refs and refs[vr] in probe.parts:
            remap[refs[vr]] = (xy[0], xy[1])
    for ref, (x, y) in sorted(remap.items()):
        if ref in probe.parts:
            part = probe.parts[ref]
            pw, ph = part.wh()
            # compare against fellow fixed candidates (probe coords are center)
            clash = any(
                o != ref and abs(x - ox) < (pw + probe.parts[o].wh()[0]) / 2 + 0.1
                and abs(y - oy) < (ph + probe.parts[o].wh()[1]) / 2 + 0.1
                for o, (ox, oy) in remap.items() if o in probe.parts)
            inside = (pw / 2 + 0.3 <= x <= bw - pw / 2 - 0.3 and
                      ph / 2 + 0.3 <= y <= bh - ph / 2 - 0.3)
            if not clash and inside:
                L.append(f"fix {ref} at {x:g} {y:g}")
            else:
                L.append(f"# fix {ref} at {x:g} {y:g} — dropped: stale layout "
                         f"({'overlap' if clash else 'off-board'}), solver places freely")
    if "GND" in named:
        L.append("power GND")
    # footprint pads with no .ato signal at all are mechanical NCs
    # (USB-C shells, mounting holes) — exempt them so lint stays quiet.
    # Ato-declared-but-unwired pins keep their unconnected warning.
    # Singleton nets never emit (see net loop above) so they don't count.
    from ocdcircuit.parts import pads_of
    _touched = {pp for net, pinlist in named.items()
                if len(pinlist) >= 2 or net in ("GND",) for pp in pinlist}
    _declared = {f"{refs[v]}.{p}" for v, m in pinmap.items() for p in m.values()
                 if v in refs}
    _ncs = sorted({f"{ref}.{pin}" for ref in probe.parts
                   for pin in pads_of(probe.parts[ref].fp, probe._lib())
                   if f"{ref}.{pin}" not in _touched and f"{ref}.{pin}" not in _declared
                   and not str(pin).startswith("NC")})
    if _ncs:
        L.append("nc " + " ".join(_ncs))
    fn = os.path.join(outdir, f"{name}.ocd")
    open(fn, "w").write("\n".join(L) + "\n")
    return fn


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m tools.atopile <atopile-projdir> <outdir>", file=sys.stderr)
        raise SystemExit(1)
    try:
        print(convert(sys.argv[1], sys.argv[2]))
    except (OSError, ValueError, KeyError, AssertionError) as e:
        print(f"atopile-port: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
