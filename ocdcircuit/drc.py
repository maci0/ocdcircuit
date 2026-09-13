"""DRC against a fab profile (see fab.py). Errors block fab; clearance-only
hits are warnings (naive L-router, see ADR-0002)."""
from __future__ import annotations
from typing import TYPE_CHECKING, cast
from .fab import DEFAULT, get


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)


if TYPE_CHECKING:
    from .circuit import Board


def _seg_dist(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> float:
    (x1, y1, x2, y2), (x3, y3, x4, y4) = a, b
    if x1 == x2 == x3 == x4:
        # shared vertical: gap between y-ranges (0 if overlapping)
        lo1, hi1 = min(y1, y2), max(y1, y2)
        lo2, hi2 = min(y3, y4), max(y3, y4)
        return max(0.0, max(lo1, lo2) - min(hi1, hi2))
    if y1 == y2 == y3 == y4:
        lo1, hi1 = min(x1, x2), max(x1, x2)
        lo2, hi2 = min(x3, x4), max(x3, x4)
        return max(0.0, max(lo1, lo2) - min(hi1, hi2))

    def d(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        dx, dy = bx - ax, by - ay
        denom: float = dx * dx + dy * dy or 1e-9
        t: float = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
        return float(((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5)

    return min(d(x1, y1, x3, y3, x4, y4), d(x2, y2, x3, y3, x4, y4),
               d(x3, y3, x1, y1, x2, y2), d(x4, y4, x1, y1, x2, y2))


def check(board: Board, fab: str | None = None) -> dict[str, object]:
    key = fab or board.fab or DEFAULT
    P = get(key)
    min_trace = float(cast(float, P["min_trace"]))
    min_space = float(cast(float, P["min_space"]))
    min_drill = float(cast(float, P["min_drill"]))
    edge = float(cast(float, P["edge"]))
    errors: list[str] = []
    warnings: list[str] = []
    layers = [int(v) for v in cast(tuple[int, ...], P["layers"])]
    if board.layers not in layers:
        errors.append(f"layers {board.layers} not in {P['name']} {layers}")
    max_w = float(cast(float, P["max_w"]))
    max_h = float(cast(float, P["max_h"]))
    if board.width > max_w or board.height > max_h:
        errors.append(f"size {board.width:g}x{board.height:g} exceeds {P['name']}")
    parts = list(board.parts.values())
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            aw, ah = a.wh()
            bw, bh = b.wh()
            if (abs(a.x - b.x) < (aw + bw) / 2 + 0.1 and
                    abs(a.y - b.y) < (ah + bh) / 2 + 0.1):
                errors.append(f"overlap {a.ref}-{b.ref}")
    lib = board._lib()
    for p in parts:
        if lib.get(p.fp, {}).get("edge"):
            continue  # edge-mount: overhang is the point (USB-C plug etc.)
        pw, ph = p.wh()
        if not (pw / 2 + edge <= p.x <= board.width - pw / 2 - edge and
                ph / 2 + edge <= p.y <= board.height - ph / 2 - edge):
            errors.append(f"edge {p.ref}")
    from .parts import hole_drill
    lib = board._lib()
    for p in parts:
        for pin in p.pins_of(lib):
            dr = hole_drill(p.fp, pin, lib)
            if dr and dr < min_drill:
                errors.append(f"drill {p.ref}.{pin}={dr} < {min_drill}")
    for net in board.nets.values():
        if len([1 for r, _ in net.pins if r in board.parts]) == 1:
            errors.append(f"floating {net.name}")
        if net.width < min_trace:
            errors.append(f"width {net.name}={net.width}")
    for t in board.traces:
        if t.width < min_trace:
            errors.append(f"trace-width {t.net}")
        if getattr(t, "jumper", False):
            if board.layers == 1:
                warnings.append(f"jumper {t.net} (wire bridge needed)")
            else:
                warnings.append(f"airwire {t.net} (maze fallback — re-route?)")
    for c in board.constraints:
        if c.get("t") == "keepout":
            cx, cy = _f(c["x"]), _f(c["y"])
            hw, hh = _f(c["w"]) / 2, _f(c["h"]) / 2
            for p in parts:
                pw, ph = p.wh()
                if abs(p.x - cx) < hw + pw / 2 and abs(p.y - cy) < hh + ph / 2:
                    # warning, not error: modules sit in antenna keepouts by
                    # design (mitox U4); review, don't block
                    warnings.append(f"keepout {p.ref}")
            for t in board.traces:
                mx, my = (t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2
                if abs(mx - cx) < hw and abs(my - cy) < hh:
                    warnings.append(f"keepout-trace {t.net}")
        elif c.get("t") == "hole":
            if _f(c["d"]) < min_drill:
                errors.append(f"hole-drill {c['d']} < {min_drill}")
    from .solver import _diff_cost, _match_cost
    mc = _match_cost(board)
    if mc > 5.0:
        warnings.append(f"length-mismatch skew~{mc / 50.0:.1f}mm")
    dc = _diff_cost(board)
    if dc > 10.0:
        warnings.append(f"diff-pair skew/gap dev~{dc / 100.0:.1f}mm")
    tr = board.traces
    for i in range(len(tr)):
        for j in range(i + 1, len(tr)):
            sa, sb = tr[i], tr[j]
            if sa.layer != sb.layer or sa.net == sb.net:
                continue
            if _seg_dist((sa.x1, sa.y1, sa.x2, sa.y2),
                         (sb.x1, sb.y1, sb.x2, sb.y2)) < min_space:
                warnings.append(f"clearance {sa.net}-{sb.net}")
                break
    return {"errors": errors, "warnings": warnings, "fab": key}


def erc(board: Board) -> dict[str, object]:
    """Electrical rule check: netlist sanity before any copper.
    Errors: unconnected pins, single-pin nets, same-pin-twice, power nets
    shorted together (VCC/GND/VDD/VSS/5V/3V3 sharing a pin), empty nets.
    Warnings: pins sharing a footprint pad name across parts is fine —
    reported only when a net has >12 pins (smell: accidental global)."""
    from .agent import AUTO_JOIN
    errors: list[str] = []
    warnings: list[str] = []
    lib = board._lib()
    ncs: set[str] = set()
    for c in board.constraints:
        if c.get("t") == "nc":
            ncs.update(cast(list[str], c.get("pins", [])))
    connected: set[tuple[str, str]] = set()
    for n, net in board.nets.items():
        if not net.pins:
            errors.append(f"empty {n}")
            continue
        seen: set[tuple[str, str]] = set()
        for ref, pin in net.pins:
            if ref not in board.parts:
                errors.append(f"unknown {ref} on {n}")
                continue
            key = (ref, str(pin))
            if key in seen:
                errors.append(f"duplicate {ref}.{pin} on {n}")
            seen.add(key)
            connected.add(key)
    for ref, p in board.parts.items():
        for pin in p.pins_of(lib):
            if (ref, pin) not in connected and f"{ref}.{pin}" not in ncs \
                    and not pin.startswith("NC"):
                errors.append(f"unconnected {ref}.{pin}")
    # power nets sharing pins = shorted rails
    owners: dict[tuple[str, str], str] = {}
    for n, net in board.nets.items():
        if n in AUTO_JOIN:
            for ref, pin in net.pins:
                key = (ref, str(pin))
                if key in owners:
                    errors.append(f"power-short {n}/{owners[key]} at {ref}.{pin}")
                owners[key] = n
    for n, net in board.nets.items():
        if len(net.pins) > 12:
            warnings.append(f"big-net {n} ({len(net.pins)} pins — intentional?)")
    return {"errors": errors, "warnings": warnings}
