"""Circuit simulation: Modified Nodal Analysis (MNA), stdlib only.

Supports: resistors, capacitors (transient, Backward Euler),
independent V/I sources (DC + step + sine). L-prefix footprints map to
R (no inductive transient); no diodes/transistors (use simulate:ngspice).
Nets GND/VSS/0 = ground. Parts map by footprint prefix:
R*→R from value, C*→C, V*? No — sources come from `sim` constraints.

Front door is constraints, not parts:
  sim vcc VCC 5        # 5V source VCC→GND
  sim vcc VCC 0 5      # 0→5V step at t=0 (transient)
  sim sine IN 1.65 1.65 1000   # 1.65+1.65*sin(2π1kHz) source
  sim r R1 10000       # resistor value override (else parse from part value)
  sim c C1 100n        # capacitor override
  sim tran 0.01 1000   # 10ms, 1000 steps
  sim probe N_OUT      # record this net (default: all nets)

Value parser: 10k, 4k7, 100n, 10u, 1m, 1M, 0.11 etc.
"""
from __future__ import annotations
from .util import as_float as _f, as_int as _i, as_str as _s
from .types import GNDS as GNDS
from typing import TYPE_CHECKING
import math

if TYPE_CHECKING:
    from .circuit import Board


def parse_value(s: str) -> float:
    """10k, 4k7, 4R7, 100n, 10u, 1m, 1M, 0.11 → float."""
    t = s.strip().replace(" ", "")
    mult = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3,
            "M": 1e6, "G": 1e9, "R": 1.0}
    if not t:
        raise ValueError("empty value")
    # embedded multiplier: 4k7, 4R7 → decimal point (tail must extend
    # the number — a trailing unit like 2.2k falls through to suffix below)
    for i, c in enumerate(t):
        if c in mult and i > 0 and t[i - 1].isdigit():
            head, tail = t[:i], t[i + 1:]
            if tail and tail[0].isdigit():
                v = float(head + "." + tail) * mult[c]
                if not math.isfinite(v):
                    raise ValueError(f"non-finite value {s!r}")
                return v
    if t[-1] in mult and not t[-1].isdigit():
        v = float(t[:-1] or "1") * mult[t[-1]]
    else:
        v = float(t)
    if not math.isfinite(v):
        raise ValueError(f"non-finite value {s!r}")
    return v


def _sim_constraints(board: Board) -> list[dict[str, object]]:
    return [c for c in board.constraints if c.get("t") == "sim"]


def _is_gnd(net: str) -> bool:
    return net in GNDS


def _net_index(board: Board) -> dict[str, int]:
    idx: dict[str, int] = {}
    for n in board.nets:
        if not _is_gnd(n):
            idx[n] = len(idx)
    return idx


def _part_value(board: Board, ref: str, *kinds: str) -> float | None:
    """Part value, with `sim <kind> REF` overrides matching spice._val.

    `kinds` are the override kinds that apply (e.g. "r" for resistors,
    "r"/"l" for L-prefix parts mapped to R). A `sim c` must not override R.
    """
    assert kinds, "at least one override kind"
    p = board.parts.get(ref)
    if p is None:
        return None
    for c in _sim_constraints(board):
        if c.get("t") == "sim" and c.get("kind") in kinds and c.get("ref") == ref:
            try:
                return parse_value(str(c.get("value", "")))
            except ValueError:
                return None
    try:
        return parse_value(p.value)
    except ValueError:
        return None


def _elements(board: Board) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """(passives, sources) from parts + sim constraints."""
    passives: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    # Invert nets once: O(pins) instead of O(parts × nets × pins).
    pinnets: dict[str, dict[str, str]] = {}
    for nn, net in board.nets.items():
        for r, pin in net.pins:
            if r in board.parts:
                pinnets.setdefault(r, {})[str(pin)] = nn
    for ref, p in board.parts.items():
        pinnet = pinnets.get(ref, {})
        fp = p.fp.upper()
        if fp.startswith("R") or fp.startswith("L0805") or fp.startswith("L1206") \
                or fp.startswith("IND"):
            # L* footprints become R in MNA; accept sim r or sim l overrides
            v = _part_value(board, ref, "r", "l")
            if v is None or len(pinnet) < 2:
                continue
            a, b = pinnet.get("1", ""), pinnet.get("2", "")
            passives.append({"t": "R", "a": a, "b": b, "v": v})
        elif fp.startswith("C") or fp.startswith("LED"):
            v = _part_value(board, ref, "c")
            if v is None or len(pinnet) < 2:
                continue
            a, b = pinnet.get("1", ""), pinnet.get("2", "")
            passives.append({"t": "C", "a": a, "b": b, "v": v})
    for c in _sim_constraints(board):
        k = c.get("kind")
        if k == "vcc":
            vnet = _s(c.get("net", ""))
            v0 = _f(c.get("v0", c.get("value", 0)))
            v1 = c.get("v1")
            sources.append({"t": "V", "net": vnet, "v0": v0,
                            "v1": None if v1 is None else _f(v1)})
        elif k == "sine":
            sources.append({"t": "sine", "net": str(c.get("net", "")),
                            "off": _f(c.get("off", 0)), "amp": _f(c.get("amp", 1)),
                            "freq": _f(c.get("freq", 1000))})
        elif k == "isrc":
            sources.append({"t": "I", "net": str(c.get("net", "")),
                            "v": _f(c.get("value", 0))})
    return passives, sources


def _stamp_resistors(A: list[list[float]], idx: dict[str, int],
                     resistors: list[dict[str, object]]) -> None:
    """Add G = 1/R stamps for each resistor into conductance matrix A."""
    for p in resistors:
        rv = _f(p["v"], 1.0)
        if not math.isfinite(rv) or rv <= 0:
            raise ValueError(f"resistor value must be positive (got {rv})")
        g = 1.0 / rv
        a = idx.get(str(p["a"]), -1)
        b = idx.get(str(p["b"]), -1)
        if a >= 0:
            A[a][a] += g
        if b >= 0:
            A[b][b] += g
        if a >= 0 and b >= 0:
            A[a][b] -= g
            A[b][a] -= g


def _stamp_sources(A: list[list[float]], idx: dict[str, int],
                   sources: list[dict[str, object]], n: int, n_cols: int) -> None:
    """Stamp V/sine (extra MNA rows) and I sources into A | rhs."""
    vi = 0
    for s in sources:
        kind = s["t"]
        if kind in ("V", "sine"):
            # DC OP uses sine offset; transient converts sine→V with v0 set
            v = _f(s["v0"]) if kind == "V" else _f(s["off"])
            row = n + vi
            vi += 1
            net = idx.get(str(s["net"]), -1)
            if net >= 0:
                A[net][row] += 1.0
                A[row][net] += 1.0
            A[row][n_cols] = v
        elif kind == "I":
            net = idx.get(str(s["net"]), -1)
            if net >= 0:
                A[net][n_cols] += _f(s["v"])


def _mna_size(idx: dict[str, int], sources: list[dict[str, object]]) -> tuple[int, int]:
    """(n_nodes, N_total) for the MNA matrix including voltage-source rows."""
    n = len(idx)
    nv = sum(1 for s in sources if s["t"] in ("V", "sine"))
    return n, n + nv


def _solve_dc(passives: list[dict[str, object]], sources: list[dict[str, object]],
              idx: dict[str, int]) -> dict[str, float]:
    """Nodal analysis, R + V sources (conductance matrix, Gaussian elim)."""
    n, n_total = _mna_size(idx, sources)
    A = [[0.0] * (n_total + 1) for _ in range(n_total)]
    _stamp_resistors(A, idx, [p for p in passives if p["t"] == "R"])
    _stamp_sources(A, idx, sources, n, n_total)
    x = _gauss(A, n_total)
    return {net: x[i] for net, i in idx.items()}


def _gauss(A: list[list[float]], n_total: int) -> list[float]:
    M = [row[:] for row in A]
    for col in range(n_total):
        piv = max(range(col, n_total), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            continue
        M[col], M[piv] = M[piv], M[col]
        for r in range(n_total):
            if r != col and M[r][col] != 0.0:
                f = M[r][col] / M[col][col]
                for c in range(col, n_total + 1):
                    M[r][c] -= f * M[col][c]
    return [(M[i][n_total] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0)
            for i in range(n_total)]


def dc(board: Board) -> dict[str, float]:
    """DC operating point {net: volts}."""
    idx = _net_index(board)
    if not idx:
        return {}
    passives, sources = _elements(board)
    return _solve_dc(passives, sources, idx)


def tran(board: Board, t_end: float | None = None,
         steps: int | None = None) -> dict[str, list[float]]:
    """Transient: Backward-Euler C, sine/step sources. Returns {net: [v...]}."""
    t_end_v = 0.01
    steps_v = 1000
    for c in _sim_constraints(board):
        if c.get("kind") == "tran":
            t_end_v = _f(c.get("t_end", c.get("value", t_end_v)), t_end_v)
            steps_v = _i(c.get("steps"), steps_v)
    if t_end is not None:
        t_end_v = t_end
    if steps is not None:
        steps_v = steps
    if not math.isfinite(t_end_v) or t_end_v <= 0:
        raise ValueError(f"t_end must be positive (got {t_end_v!r})")
    if steps_v < 1:
        raise ValueError(f"steps must be ≥1 (got {steps_v!r})")
    idx = _net_index(board)
    if not idx:
        return {}
    passives, sources = _elements(board)
    # floor division of time: exact steps of equal width (caller-chosen count)
    dt = t_end_v / steps_v
    # Backward Euler companions: Geq = C/dt in parallel with a current
    # source Geq*vc_prev flowing b→a. First-order, stable, plenty for
    # sizing checks (use small dt for stiff circuits).
    caps = [p for p in passives if p["t"] == "C"]
    vc = {id(p): 0.0 for p in caps}
    probes = [str(c.get("net")) for c in _sim_constraints(board) if c.get("kind") == "probe"]
    if not probes:
        probes = list(idx)
    out: dict[str, list[float]] = {p: [] for p in probes if p in idx}
    t = 0.0
    for _ in range(steps_v):
        t += dt
        eff: list[dict[str, object]] = [p for p in passives if p["t"] == "R"]
        ihist: dict[int, float] = {}
        for p in caps:
            C = _f(p["v"], 1e-9)
            if not math.isfinite(C) or C <= 0:
                raise ValueError(f"capacitor value must be positive (got {C})")
            Geq = C / dt
            eff.append({"t": "R", "a": p["a"], "b": p["b"], "v": 1.0 / Geq})
            ihist[id(p)] = Geq * vc[id(p)]
        # time-varying sources (sine/step become plain V with v0 set)
        tsrc: list[dict[str, object]] = []
        for s in sources:
            if s["t"] == "V" and s["v1"] is not None:
                tsrc.append({"t": "V", "net": s["net"], "v0": _f(s["v1"])})
            elif s["t"] == "sine":
                v = (_f(s["off"])
                     + _f(s["amp"], 1.0)
                     * math.sin(2 * math.pi * _f(s["freq"], 1000.0) * t))
                tsrc.append({"t": "V", "net": s["net"], "v0": v})
            else:
                tsrc.append(s)
        n, n_total = _mna_size(idx, tsrc)
        A = [[0.0] * (n_total + 1) for _ in range(n_total)]
        _stamp_resistors(A, idx, eff)
        for p in caps:
            Ih = ihist[id(p)]
            a = idx.get(str(p["a"]), -1)
            b = idx.get(str(p["b"]), -1)
            if a >= 0:
                A[a][n_total] += Ih
            if b >= 0:
                A[b][n_total] -= Ih
        _stamp_sources(A, idx, tsrc, n, n_total)
        x = _gauss(A, n_total)
        sol = {net: x[i] for net, i in idx.items()}
        for p in caps:
            a = idx.get(str(p["a"]), -1)
            b = idx.get(str(p["b"]), -1)
            va = sol.get(str(p["a"]), 0.0) if a >= 0 else 0.0
            vb = sol.get(str(p["b"]), 0.0) if b >= 0 else 0.0
            vc[id(p)] = va - vb
        for pr in out:
            out[pr].append(sol.get(pr, 0.0))
    return out


def run(board: Board, what: str = "dc", **k: object) -> dict[str, object]:
    """what: dc | tran. Returns {"nets"/"waves", ...} for plugins/MCP."""
    if what == "tran":
        t = k.get("t_end")
        s = k.get("steps")
        assert t is None or isinstance(t, (int, float))
        assert s is None or isinstance(s, int)
        waves = tran(board, float(t) if t is not None else None,
                     int(s) if s is not None else None)
        return {"waves": waves, "t_end": t, "steps": s}
    return {"nets": dc(board)}


def intent(board: Board, text: str) -> list[dict[str, object]] | None:
    """Deterministic NL sim-intent → expect constraints (no LLM needed).
    Returns None when the text is not a sim assertion request.
    Shapes: "VO should settle at 5V" / "check N stays under 3.6V" /
    "assert VCC == 5" / "VO must reach at least 4.5V" /
    "ensure OUT settles within 4.9 5.1". Unknown nets/values → None."""
    import re
    t = text.strip()
    m = re.match(r"(?:(?:please\s+)?(?:check|assert|ensure|verify|make sure)\s+)?"
                 r"(\w+)\s+(?:should\s+|must\s+|needs?\s+to\s+)?"
                 r"(settle(?:s|d)?(?:\s+at)?|stay(?:s)?(?:\s+under|below|above)?|"
                 r"reach(?:es)?(?:\s+at\s+least)?|be|equal(?:s|s\s+to)?|within)\s+"
                 r"(.+)$", t, re.I)
    if not m:
        return None
    net, rest = m.group(1), m.group(3).strip()
    if net.lower() in ("it", "this", "that", "board", "circuit"):
        return None
    if net not in board.nets:
        return None
    kind = m.group(0)
    stat: str | None = None
    if re.search(r"settle|final|steady", t, re.I):
        stat = "final"
    vm = re.match(r"(?:at\s+|to\s+|of\s+)?(-?[\d.]+[kMuunp%]?)\s*V?$", rest, re.I)
    if vm:
        op = "=="
        if re.search(r"under|below|<\s", kind + " " + rest, re.I):
            op = "<"
        elif re.search(r"above|over|at\s+least|>\s|min", kind + " " + rest, re.I):
            op = ">"
        c: dict[str, object] = {"t": "sim", "kind": "expect", "net": net,
                                "op": op, "value": vm.group(1)}
        if stat is not None:
            c["stat"] = stat
        return [c]
    wm = re.match(r"(?:within\s+|between\s+)?(-?[\d.]+[kMuunp%]?)\s*(?:V?\s+|V?\s+and\s+|V?\s*-\s*|V?\s+to\s+)(-?[\d.]+[kMuunp%]?)\s*V?$", rest, re.I)
    if wm:
        lo, hi = wm.group(1), wm.group(2)
        return [{"t": "sim", "kind": "expect", "net": net, "op": ">=",
                 "value": lo},
                {"t": "sim", "kind": "expect", "net": net, "op": "<=",
                 "value": hi}]
    return None


def expect(board: Board) -> list[str]:
    """Evaluate `sim expect NET <op> VALUE` against the DC solve.
    Returns problem strings (`N_OUT=0.02V, want == 2.5V`); empty = all
    pass. Ops: ==, !=, <, >, <=, >=, ~ (within ±tol, tol default 5%)."""
    wants = [c for c in _sim_constraints(board) if c.get("kind") == "expect"]
    if not wants:
        return []
    try:
        sol = dc(board)
    except (ValueError, KeyError, AssertionError):
        return ["sim failed — cannot evaluate expectations"]
    out: list[str] = []
    for c in wants:
        net = str(c.get("net", ""))
        op = str(c.get("op", "=="))
        try:
            want = parse_value(str(c.get("value", "0")))
            ts = str(c.get("tol", "5%"))
            tol = float(ts[:-1]) / 100.0 if ts.endswith("%") else parse_value(ts)
        except ValueError:
            out.append(f"sim expect {net}: bad value")
            continue
        if net not in sol:
            out.append(f"sim expect {net}: unknown net")
            continue
        got = sol[net]
        ok = (abs(got - want) <= abs(want) * tol + 1e-9 if op == "~" else
              got == want if op == "==" else
              got != want if op == "!=" else
              got < want if op == "<" else
              got > want if op == ">" else
              got <= want if op == "<=" else
              got >= want if op == ">=" else None)
        if ok is None:
            out.append(f"sim expect {net}: bad op {op!r}")
        elif not ok:
            out.append(f"sim {net}={got:.3f}V, want {op} {want:g}V")
    return out


def expect_tran(board: Board) -> list[str]:
    """Evaluate `sim expect NET final|min|max <op> VALUE` over the tran wave.
    Same op/tol semantics as expect(); empty = all pass. No stat = DC path."""
    wants = [c for c in _sim_constraints(board)
             if c.get("kind") == "expect" and c.get("stat") is not None]
    if not wants:
        return []
    try:
        waves = tran(board)
    except (ValueError, KeyError, AssertionError):
        return ["sim tran failed — cannot evaluate expectations"]
    out: list[str] = []
    for c in wants:
        net = str(c.get("net", ""))
        stat = str(c.get("stat", "final"))
        op = str(c.get("op", "=="))
        try:
            want = parse_value(str(c.get("value", "0")))
            ts = str(c.get("tol", "5%"))
            tol = float(ts[:-1]) / 100.0 if ts.endswith("%") else parse_value(ts)
        except ValueError:
            out.append(f"sim expect {net} {stat}: bad value")
            continue
        wave = waves.get(net)
        if not wave:
            out.append(f"sim expect {net} {stat}: unknown net")
            continue
        got = wave[-1] if stat == "final" else (
            min(wave) if stat == "min" else max(wave))
        ok = (abs(got - want) <= abs(want) * tol + 1e-9 if op == "~" else
              got == want if op == "==" else
              got != want if op == "!=" else
              got < want if op == "<" else
              got > want if op == ">" else
              got <= want if op == "<=" else
              got >= want if op == ">=" else None)
        if ok is None:
            out.append(f"sim expect {net} {stat}: bad op {op!r}")
        elif not ok:
            out.append(f"sim {net} {stat}={got:.3f}V, want {op} {want:g}V")
    return out
