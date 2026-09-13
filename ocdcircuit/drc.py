"""DRC against a fab profile (see fab.py). Errors block fab; clearance-only
hits are warnings (naive L-router, see ADR-0002)."""
from __future__ import annotations
from typing import TYPE_CHECKING, cast
from .fab import DEFAULT, get

if TYPE_CHECKING:
    from .circuit import Board


def _seg_dist(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> float:
    (x1, y1, x2, y2), (x3, y3, x4, y4) = a, b
    if x1 == x2 == x3 == x4 or y1 == y2 == y3 == y4:
        return 0.0  # collinear handled conservatively elsewhere

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
            if (abs(a.x - b.x) < (a.w + b.w) / 2 + 0.1 and
                    abs(a.y - b.y) < (a.h + b.h) / 2 + 0.1):
                errors.append(f"overlap {a.ref}-{b.ref}")
    for p in parts:
        if not (p.w / 2 + edge <= p.x <= board.width - p.w / 2 - edge and
                p.h / 2 + edge <= p.y <= board.height - p.h / 2 - edge):
            errors.append(f"edge {p.ref}")
    from .parts import hole_drill
    for p in parts:
        for pin in p.pins_of():
            dr = hole_drill(p.fp, pin)
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
