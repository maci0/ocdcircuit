"""Digital gate simulation: event-driven, unit-delay, stdlib only.

Parts declare logic via the `logic=` attr: NAND/NOR/AND/OR/XOR/INV/BUF
(combinational) or DFF/JK (clocked, positive-edge). Pin order follows the
footprint: inputs first in pin-number order, output = highest pin
(DFF: D=1, CLK=2, Q=3, QN=4, CLR=5, PRE=6 when present; JK: J=1, K=2,
CLK=3, Q=4, QN=5, CLR=6, PRE=7).

Stimulus comes from `sim` constraints: `sim vcc NET 0|1` (logic level),
`sim clk NET period [duty]` (square wave). Nets GND/VSS/0 = 0; AUTO_JOIN
rails that are not grounds (VCC/VDD/5V/3V3) default to 1 unless driven.
Returns {"nets": {net: 0|1}} final state + {"waves": ...}
per-step levels when `sim tran` present (steps = ticks).

Oscillation guard: combinational loops settle by fixpoint cap (100 iters);
ring oscillators report X (None) on the loop nets.
# ponytail: tick delays via `delay=N` part attrs (0 = instant fixpoint,
# the old behavior); sub-tick/float annotated delays when a board needs them.
"""
from __future__ import annotations
from .util import as_float as _f
from .types import GNDS as GNDS, LOGIC_HI as LOGIC_HI
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit import Board

# gate: (n_inputs_including_clock, is_clocked)
GATES = {"NAND": (2, False), "NOR": (2, False), "AND": (2, False),
         "OR": (2, False), "XOR": (2, False), "INV": (1, False),
         "BUF": (1, False), "DFF": (2, True), "JK": (3, True)}


def _gate_fn(kind: str, ins: list[int]) -> int:
    if kind == "NAND":
        return int(not all(ins))
    if kind == "NOR":
        return int(not any(ins))
    if kind == "AND":
        return int(all(ins))
    if kind == "OR":
        return int(any(ins))
    if kind == "XOR":
        s = 0
        for b in ins:
            s ^= b
        return s
    if kind == "INV":
        return int(not ins[0])
    return int(bool(ins[0]))  # BUF


def _delay(p_attrs: dict[str, str]) -> int:
    """Timing annotation: `delay=N` part attr (ticks). Default 0 = today's
    instant combinational fixpoint; N>0 schedules the output N ticks out."""
    try:
        return max(0, int(p_attrs.get("delay", "0")))
    except (TypeError, ValueError):
        return 0


def _logic_parts(board: Board) -> list[tuple[str, str, list[str], str]]:
    """(ref, kind, [input nets...], output net) for parts with logic= attr."""
    out: list[tuple[str, str, list[str], str]] = []
    pinnets: dict[str, dict[str, str]] = {}
    for nn, net in board.nets.items():
        for r, pin in net.pins:
            if r in board.parts:
                pinnets.setdefault(r, {})[str(pin)] = nn
    for ref, p in board.parts.items():
        kind = p.attrs.get("logic", "").upper()
        if kind not in GATES:
            continue
        pinnet = pinnets.get(ref, {})
        nums = sorted(pinnet, key=lambda q: (len(q), q))
        if kind in ("DFF", "JK"):
            n_in, clk_idx = (2, 1) if kind == "DFF" else (3, 2)
            if len(nums) < n_in + 1:
                continue
            ins = [pinnet[nums[i]] for i in range(n_in)]
            out.append((ref, kind, ins, pinnet[nums[n_in]]))
        else:
            n_in = 2 if kind in ("NAND", "NOR", "AND", "OR", "XOR") else 1
            ins = [pinnet[n] for n in nums[:n_in] if n in pinnet]
            if not ins or len(nums) < 2:
                continue
            out.append((ref, kind, ins, pinnet[nums[len(ins)]]))
    return out


def _stimulus(board: Board) -> tuple[dict[str, int], dict[str, tuple[float, float]]]:
    """levels from `sim vcc NET 0|1`; clocks from `sim clk NET period [duty]`."""
    levels: dict[str, int] = {}
    clocks: dict[str, tuple[float, float]] = {}
    for c in board.constraints:
        if c.get("t") != "sim":
            continue
        if c.get("kind") == "vcc":
            v0 = _f(c.get("v0", c.get("value", 0)))
            levels[str(c.get("net", ""))] = 1 if v0 > 0.5 else 0
        elif c.get("kind") == "clk":
            clocks[str(c.get("net", ""))] = (
                _f(c.get("period", c.get("value", 1.0)), 1.0),
                _f(c.get("duty", 0.5), 0.5))
    return levels, clocks


def run(board: Board, ticks: int | None = None, **k: object) -> dict[str, object]:
    """Event-driven unit-delay sim. Returns {"nets", "waves"?} like mna."""
    parts = _logic_parts(board)
    levels, clocks = _stimulus(board)
    state: dict[str, int | None] = {}
    for n in board.nets:
        if n in GNDS:
            state[n] = 0
        elif n in LOGIC_HI and n not in levels:
            state[n] = 1
    state.update(levels)
    # clock schedule: square wave per tick (tick = half period unit)
    clk_nets = set(clocks)
    waves: dict[str, list[int]] = {n: [] for n in board.nets}
    # DFF/JK state: ref -> Q
    ff: dict[str, int] = {r: 0 for r, kd, _i, _o in parts if GATES[kd][1]}
    prev_clk: dict[str, int] = {}
    n_ticks = ticks if ticks is not None else 20
    assert isinstance(n_ticks, int)
    delays = {ref: _delay(board.parts[ref].attrs) for ref, kd, _i, _o in parts}
    pending: list[tuple[int, str, int]] = []  # (fire_tick, net, value)
    for t in range(n_ticks):
        for cn, (per, duty) in clocks.items():
            phase = (t % max(1, int(round(per)))) / max(1, int(round(per)))
            state[cn] = 1 if phase < duty else 0
        # due events first: outputs scheduled by delay=N fire now
        for fire_t, onet, v in [e for e in pending if e[0] == t]:
            state[onet] = v
        pending = [e for e in pending if e[0] != t]
        # combinational fixpoint (cap 100: oscillation → None)
        for _ in range(100):
            changed = False
            for _r, kd, ins, onet in parts:
                if GATES[kd][1]:
                    continue
                vals = [state.get(i) for i in ins]
                if any(v is None for v in vals):
                    continue
                v = _gate_fn(kd, [int(x) for x in vals if x is not None])
                if state.get(onet) != v:
                    delay = delays.get(_r, 0)
                    if delay and t + delay < n_ticks:
                        pending.append((t + delay, onet, v))
                        continue  # output lands later; not settled now
                    state[onet] = v
                    changed = True
            if not changed:
                break
        else:
            for _r, kd, _i, onet in parts:
                if not GATES[kd][1]:
                    state[onet] = None
        # clocked: rising edge latches
        for r, kd, ins, onet in parts:
            if not GATES[kd][1]:
                continue
            clk = state.get(ins[-1], 0)
            rising = clk == 1 and prev_clk.get(r, 0) == 0
            prev_clk[r] = int(clk or 0)
            if not rising:
                continue
            if kd == "DFF":
                ff[r] = int(state.get(ins[0]) or 0)
            else:
                j, kk = int(state.get(ins[0]) or 0), int(state.get(ins[1]) or 0)
                if j and not kk:
                    ff[r] = 1
                elif kk and not j:
                    ff[r] = 0
                elif j and kk:
                    ff[r] = 1 - ff[r]
            state[onet] = ff[r]
        for n in waves:
            wv = state.get(n)
            waves[n].append(int(wv) if wv is not None else 0)
    nets = {n: (v if v is not None else 0) for n, v in state.items()
            if n in board.nets}
    res: dict[str, object] = {"nets": nets}
    if any(c.get("t") == "sim" and c.get("kind") == "tran"
           for c in board.constraints):
        res["waves"] = waves
    return res


if __name__ == "__main__":
    assert _gate_fn("NAND", [1, 1]) == 0
    assert _gate_fn("NAND", [1, 0]) == 1
    assert _gate_fn("XOR", [1, 1, 0]) == 0
    print("GATES OK")
