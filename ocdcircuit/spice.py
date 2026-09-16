"""Board → SPICE netlist + ngspice runner (stdlib only, subprocess).

Maps footprints like sim.py does (R*/C*/L*/D*/Q* by prefix, value from part
or `sim r/c/l` override). Diodes default 1N4148, BJTs need `sim q REF MODEL`
(or default 2N3904/2N3906 by ref prefix Q/N... default NPN 2N3904).
Opamps: `sim op REF MODEL PINS...` → X-card with .lib include if `sim lib`
given, else ideal single-pole model.

Analyses: dc (op), tran, ac. ngspice batch mode (-b), wrdata ASCII parse.
Missing binary → RuntimeError naming the apt package (hot-swap back to mna).
"""
from __future__ import annotations
from .util import as_float as _num, as_int as _int
from typing import TYPE_CHECKING
import math
import os
import re
import shutil
import subprocess
import tempfile

if TYPE_CHECKING:
    from .circuit import Board

NGSPICE = shutil.which("ngspice")

GNDS = ("GND", "VSS", "0")


def _norm(net: str) -> str:
    return "0" if net in GNDS else re.sub(r"[^A-Za-z0-9_+-]", "_", net) or "N"


def _val(board: Board, ref: str, kind: str) -> float | None:
    from .sim import parse_value
    p = board.parts.get(ref)
    if p is None:
        return None
    for c in board.constraints:
        if c.get("t") == "sim" and c.get("kind") == kind and c.get("ref") == ref:
            try:
                return parse_value(str(c.get("value", "")))
            except ValueError:
                return None
    try:
        return parse_value(p.value)
    except ValueError:
        return None


def _pinnet(board: Board, ref: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for nn, net in board.nets.items():
        for r, pin in net.pins:
            if r == ref:
                out[str(pin)] = nn
    return out


def netlist(board: Board) -> str:
    """SPICE text for ngspice batch. Raises ValueError on unmappable parts."""
    L = [f"* ocdcircuit: {board.name}", ".options noinit"]
    for c in board.constraints:
        if c.get("t") == "sim" and c.get("kind") == "lib":
            L.append(f".lib {c.get('path', '')}")
    n_v = 0
    for ref, p in board.parts.items():
        pn = _pinnet(board, ref)
        fp = p.fp.upper()
        if fp.startswith("R") or fp.startswith("L0805") or fp.startswith("L1206") \
                or fp.startswith("IND"):
            v = _val(board, ref, "r")
            if v is None or len(pn) < 2:
                continue
            L.append(f"R{ref} {_norm(pn.get('1', ''))} {_norm(pn.get('2', ''))} {v:g}")
        elif fp.startswith("C") or fp.startswith("LED"):
            v = _val(board, ref, "c")
            if v is None or len(pn) < 2:
                continue
            L.append(f"C{ref} {_norm(pn.get('1', ''))} {_norm(pn.get('2', ''))} {v:g} ic=0")
        elif fp.startswith("L") and not fp.startswith("LED"):
            v = _val(board, ref, "l")
            if v is None or len(pn) < 2:
                continue
            L.append(f"L{ref} {_norm(pn.get('1', ''))} {_norm(pn.get('2', ''))} {v:g} ic=0")
        elif fp.startswith("D") and not fp.startswith("DI"):
            model = "1N4148"
            for c in board.constraints:
                if c.get("t") == "sim" and c.get("kind") == "d" and c.get("ref") == ref:
                    model = str(c.get("value", model))
            if len(pn) < 2:
                continue
            L.append(f"D{ref} {_norm(pn.get('1', ''))} {_norm(pn.get('2', ''))} {model}")
            if model == "1N4148":
                L.append(".model 1N4148 D(is=2.52n rs=0.568 n=1.752 bv=100 ibv=100u)")
        elif fp.startswith("Q") or fp.startswith("SOT") or fp.startswith("TO"):
            model = "2N3904"
            for c in board.constraints:
                if c.get("t") == "sim" and c.get("kind") == "q" and c.get("ref") == ref:
                    model = str(c.get("value", model))
            if len(pn) < 3:
                continue
            # SOT23: 1=base 2=emitter 3=collector
            L.append(f"Q{ref} {_norm(pn.get('3', ''))} {_norm(pn.get('1', ''))} "
                     f"{_norm(pn.get('2', ''))} {model}")
            if model == "2N3904":
                L.append(".model 2N3904 NPN(bf=300 br=7.5 is=14f va=100)")
    for c in board.constraints:
        if c.get("t") != "sim":
            continue
        k = c.get("kind")
        if k == "vcc":
            n_v += 1
            net = _norm(str(c.get("net", "")))
            v0 = _num(c.get("v0", c.get("value", 0)))
            v1 = c.get("v1")
            if v1 is None:
                L.append(f"V{n_v} {net} 0 dc {v0:g}")
            else:
                L.append(f"V{n_v} {net} 0 dc 0 pulse(0 {_num(v1):g} 0 1n 1n 1 10)")
        elif k == "sine":
            n_v += 1
            net = _norm(str(c.get("net", "")))
            L.append(f"V{n_v} {net} 0 dc {_num(c.get('off', 0)):g} ac 1 "
                     f"sin(0 {_num(c.get('amp', 1)):g} {_num(c.get('freq', 1000)):g})")
        elif k == "isrc":
            n_v += 1
            net = _norm(str(c.get("net", "")))
            L.append(f"I{n_v} 0 {net} dc {_num(c.get('value', 0)):g}")
        elif k == "op":
            # sim op U1 LM358 3 2 8 4 1 → XU1 out in+ in- vcc vee MODEL
            toks = str(c.get("value", "")).split()
            ref = str(c.get("ref", ""))
            model = toks[0] if toks else "OPIDEAL"
            pins = toks[1:]
            pn = _pinnet(board, ref)
            nodes = " ".join(_norm(pn.get(p, "")) for p in pins)
            L.append(f"X{ref} {nodes} {model}")
            if model == "OPIDEAL":
                # Miller-compensated 2-stage (this ngspice rejects Laplace
                # braces): gm=1e-3, 16p comp, unity buffer. GBW~10MHz.
                L.append(".subckt OPIDEAL out in+ in- vcc vee\n"
                         "R1 in+ in- 1e12\n"
                         "G1 vcc mid in+ in- 1e-3\n"
                         "R3 mid 0 1e6\n"
                         "C3 mid out 16p\n"
                         "E2 out 0 mid 0 1\n"
                         "Rout out 0 100\n"
                         ".ends")
    # default diode model if D cards reference it without explicit .model
    if any(l.startswith("D") and "1N4148" in l for l in L) \
            and not any(l.startswith(".model 1N4148") for l in L):
        L.append(".model 1N4148 D(is=2.52n rs=0.568 n=1.752 bv=100 ibv=100u)")
    if any("2N3904" in l and l.startswith("Q") for l in L) \
            and not any(l.startswith(".model 2N3904") for l in L):
        L.append(".model 2N3904 NPN(bf=300 br=7.5 is=14f va=100)")
    L.append(".end")
    return "\n".join(L) + "\n"


def _probes(board: Board) -> list[str]:
    out = [str(c.get("net")) for c in board.constraints
           if c.get("t") == "sim" and c.get("kind") == "probe"]
    if not out:
        out = [n for n in board.nets if n not in GNDS]
    return [_norm(n) for n in out]


def _run_ngspice(workdir: str, spice: str, cmds: list[str], dat: str,
                 probes: list[str]) -> dict[str, list[float]]:
    """Batch run in workdir, wrdata ASCII parse → {probe: [samples]}.
    wrdata rows are (index, v1, v2, ...) in command order — positional.
    Caller owns workdir + dat path (one temp dir per analysis)."""
    if NGSPICE is None:
        raise RuntimeError("ngspice not found (apt install ngspice); "
                           "use simulate:mna instead")
    cir = os.path.join(workdir, "c.cir")
    body = spice.rsplit(".end", 1)[0]  # commands go BEFORE .end,
    # wrapped in .control (bare commands error in batch mode)
    with open(cir, "w", encoding="utf-8") as f:
        f.write(body + ".control\n" + "\n".join(cmds) + "\n.endc\n.end\n")
    r = subprocess.run([NGSPICE, "-b", cir], capture_output=True, text=True,
                       timeout=60, cwd=workdir)
    if r.returncode != 0 or not os.path.isfile(dat):
        err = (r.stderr or r.stdout)[-1500:]
        raise RuntimeError(f"ngspice failed: {err}")
    # wrdata writes each vector as a (scale, value) column pair
    cols: dict[str, list[float]] = {p: [] for p in probes}
    for line in open(dat, encoding="utf-8"):
        parts = line.split()
        if len(parts) < 2 * len(probes):
            continue
        try:
            vals = [float(parts[2 * i + 1]) for i in range(len(probes))]
        except ValueError:
            continue
        for p, v in zip(probes, vals):
            cols[p].append(v)
    if not any(cols.values()):
        raise RuntimeError(f"ngspice: no data parsed from wrdata output")
    return cols


def run(board: Board, what: str = "dc", **k: object) -> dict[str, object]:
    """what: dc | tran | ac. Same return shape as simulate:mna."""
    spice = netlist(board)
    probes = _probes(board)
    if what == "dc":
        # wrdata names columns v(net); fall back to bare net
        with tempfile.TemporaryDirectory() as d:
            dat = os.path.join(d, "out.txt")
            cmds = ["op", f"wrdata {dat} " + " ".join("v(" + p + ")" for p in probes)]
            waves = _run_ngspice(d, spice, cmds, dat, probes)
        out: dict[str, float] = {}
        for p in probes:
            col = waves.get(f"v({p})", waves.get(p, [0.0]))
            out[p] = col[-1] if col else 0.0
        # map back to board net names
        back = {(_norm(n) if n not in GNDS else n): n for n in
                ([str(c.get("net")) for c in board.constraints
                  if c.get("t") == "sim" and c.get("kind") == "probe"]
                 or [n for n in board.nets if n not in GNDS])}
        return {"nets": {back.get(p, p): v for p, v in out.items()}}
    if what == "tran":
        t_end, steps = 0.01, 1000
        for c in board.constraints:
            if c.get("t") == "sim" and c.get("kind") == "tran":
                t_end = _num(c.get("t_end", c.get("value", t_end)))
                steps = _int(c.get("steps", steps))
        if k.get("t_end") is not None:
            t_end = _num(k["t_end"])
        if k.get("steps") is not None:
            steps = _int(k["steps"])
        if not math.isfinite(t_end) or t_end <= 0:
            raise ValueError(f"t_end must be positive (got {t_end!r})")
        if steps < 1:
            raise ValueError(f"steps must be ≥1 (got {steps!r})")
        with tempfile.TemporaryDirectory() as d:
            dat = os.path.join(d, "out.txt")
            cmds = [f"tran {t_end / steps:g} {t_end:g}",
                    f"wrdata {dat} " + " ".join("v(" + p + ")" for p in probes)]
            waves = _run_ngspice(d, spice, cmds, dat, probes)
        out_w: dict[str, list[float]] = {}
        for p in probes:
            out_w[p] = waves.get(f"v({p})", waves.get(p, []))
        back_w = {(_norm(n) if n not in GNDS else n): n for n in
                  ([str(c.get("net")) for c in board.constraints
                    if c.get("t") == "sim" and c.get("kind") == "probe"]
                   or [n for n in board.nets if n not in GNDS])}
        return {"waves": {back_w.get(p, p): v for p, v in out_w.items()}}
    if what == "ac":
        f0, f1, npts = 1.0, 1e6, 50
        if k.get("f0") is not None:
            f0 = _num(k["f0"])
        if k.get("f1") is not None:
            f1 = _num(k["f1"])
        with tempfile.TemporaryDirectory() as d:
            dat = os.path.join(d, "out.txt")
            cmds = [f"ac dec {npts} {f0:g} {f1:g}",
                    f"wrdata {dat} " + " ".join("v(" + p + ")" for p in probes)]
            waves = _run_ngspice(d, spice, cmds, dat, probes)
        return {"ac": {p: waves.get(f"v({p})", waves.get(p, [])) for p in probes},
                "f0": f0, "f1": f1, "npts": npts}
    raise ValueError(f"unknown analysis {what!r} (dc|tran|ac)")


if __name__ == "__main__":
    # self-check: divider dc + RC step vs analytic
    from ocdcircuit import agent
    b = agent.loads("board t 40x30\npart R1 R0805 10k\npart R2 R0805 4k7\n"
                    "net VIN: R1.1\nnet VO: R1.2 R2.1\nnet GND: R2.2\nsim vcc VIN 9\n")
    from typing import cast
    r = run(b, "dc")
    assert isinstance(r, dict)
    vo = cast(dict[str, float], r["nets"])["VO"]
    assert abs(vo - 2.878) < 0.02, vo
    b2 = agent.loads("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
                     "net VIN: R1.1\nnet VO: R1.2 C1.1\nnet GND: C1.2\n"
                     "sim vcc VIN 0 5\nsim tran 0.005 500\nsim probe VO\n")
    w = cast(dict[str, list[float]], run(b2, "tran")["waves"])["VO"]
    assert abs(w[-1] - 5.0) < 0.05, w[-5:]
    assert all(a <= c + 1e-9 for a, c in zip(w, w[1:]))
    print("ngspice self-check OK")
