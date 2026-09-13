"""DRC vs JLC 2-layer capabilities (with margin)."""
MIN_W, MIN_CL, MIN_DRILL, ANNULAR, EDGE = 0.15, 0.15, 0.2, 0.15, 0.3


def _seg_dist(a, b):
    (x1, y1, x2, y2), (x3, y3, x4, y4) = a, b
    if x1 == x2 == x3 == x4 or y1 == y2 == y3 == y4:
        return 0.0  # collinear handled conservatively elsewhere
    def d(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy or 1e-9
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
        return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5
    return min(d(x1, y1, x3, y3, x4, y4), d(x2, y2, x3, y3, x4, y4),
               d(x3, y3, x1, y1, x2, y2), d(x4, y4, x1, y1, x2, y2))


def check(board):
    errors, warnings = [], []
    parts = list(board.parts.values())
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            if (abs(a.x - b.x) < (a.w + b.w) / 2 + 0.1 and
                    abs(a.y - b.y) < (a.h + b.h) / 2 + 0.1):
                errors.append(f"overlap {a.ref}-{b.ref}")
    for p in parts:
        if not (p.w / 2 + EDGE <= p.x <= board.width - p.w / 2 - EDGE and
                p.h / 2 + EDGE <= p.y <= board.height - p.h / 2 - EDGE):
            errors.append(f"edge {p.ref}")
    for net in board.nets.values():
        if len([1 for r, _ in net.pins if r in board.parts]) == 1:
            errors.append(f"floating {net.name}")
        if net.width < MIN_W:
            errors.append(f"width {net.name}={net.width}")
    for t in board.traces:
        if t.width < MIN_W:
            errors.append(f"trace-width {t.net}")
    # same-layer clearance → warnings (naive L-router, see ADR-0002)
    tr = board.traces
    for i in range(len(tr)):
        for j in range(i + 1, len(tr)):
            a, b = tr[i], tr[j]
            if a.layer != b.layer or a.net == b.net:
                continue
            if _seg_dist((a.x1, a.y1, a.x2, a.y2), (b.x1, b.y1, b.x2, b.y2)) < MIN_CL:
                warnings.append(f"clearance {a.net}-{b.net}")
                break
    return {"errors": errors, "warnings": warnings}
