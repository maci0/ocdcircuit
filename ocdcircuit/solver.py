"""Diffusion placement + greedy layers + L-router. Stdlib only.

Placement = Langevin diffusion: parts drift along net-spring forces and
pairwise repulsion with decaying temperature/noise. Run N seeds, keep best
(Quilter-style candidates for free).
# ponytail: O(n^2) forces, L-router only — push-and-shove when warnings annoy.
"""
from __future__ import annotations
import random
from .circuit import Seg


def _fixed(board):
    return {c["ref"]: (c["x"], c["y"]) for c in board.constraints if c.get("t") == "fixed"}


def _near(board):
    return [(c["a"], c["b"], c.get("w", 2.0)) for c in board.constraints if c.get("t") == "near"]


def wirelength(board) -> float:
    tot = 0.0
    for net in board.nets.values():
        pts = []
        for ref, pin in net.pins:
            if ref in board.parts:
                pts.append(board.pad_pos(ref, pin))
        for i in range(1, len(pts)):
            tot += abs(pts[i][0] - pts[0][0]) + abs(pts[i][1] - pts[0][1])
    return tot


def cost(board) -> float:
    parts = list(board.parts.values())
    c = wirelength(board)
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            if (abs(a.x - b.x) < (a.w + b.w) / 2 + 0.4 and
                    abs(a.y - b.y) < (a.h + b.h) / 2 + 0.4):
                c += 1e6
    m = edge_margin(board)
    for p in parts:
        if not (p.w / 2 + m <= p.x <= board.width - p.w / 2 - m and
                p.h / 2 + m <= p.y <= board.height - p.h / 2 - m):
            c += 1e5
    for a, b, w in _near(board):
        if a in board.parts and b in board.parts:
            pa, pb = board.parts[a], board.parts[b]
            c += w * (abs(pa.x - pb.x) + abs(pa.y - pb.y))
    return c


def edge_margin(board):
    for c in board.constraints:
        if c.get("t") == "edge":
            return c.get("margin", 0.5)
    return 0.5


def _diffuse_once(board, iters=400, seed=0):
    rng = random.Random(seed)
    fx = _fixed(board)
    near = _near(board)
    m = edge_margin(board)
    parts = [p for r, p in board.parts.items() if r not in fx]
    for r, (x, y) in fx.items():
        if r in board.parts:
            board.parts[r].x, board.parts[r].y = x, y
    if not parts:
        return
    for p in parts:  # random init inside board
        p.x = rng.uniform(p.w / 2 + m, board.width - p.w / 2 - m)
        p.y = rng.uniform(p.h / 2 + m, board.height - p.h / 2 - m)
    # net membership
    mem = {p.ref: [] for p in parts}
    for net in board.nets.values():
        for ref, _ in net.pins:
            if ref in mem:
                mem[ref].append(net.name)
    for t in range(iters):
        T = 1 - t / iters  # temperature
        step = 0.25 + 0.65 * T
        for p in parts:
            Fx = Fy = 0.0
            # springs to net centroids
            for nname in mem[p.ref]:
                net = board.nets[nname]
                pts = [board.pad_pos(r, pn) for r, pn in net.pins if r in board.parts]
                if len(pts) > 1:
                    cx = sum(q[0] for q in pts) / len(pts)
                    cy = sum(q[1] for q in pts) / len(pts)
                    Fx += 0.08 * (cx - p.x)
                    Fy += 0.08 * (cy - p.y)
            for a, b, w in near:
                if p.ref == a and b in board.parts:
                    q = board.parts[b]
                    Fx += 0.05 * w * (q.x - p.x)
                    Fy += 0.05 * w * (q.y - p.y)
                elif p.ref == b and a in board.parts:
                    q = board.parts[a]
                    Fx += 0.05 * w * (q.x - p.x)
                    Fy += 0.05 * w * (q.y - p.y)
            # pairwise repulsion (diffusion spread)
            for q in board.parts.values():
                if q is p:
                    continue
                dx, dy = p.x - q.x, p.y - q.y
                d = (dx * dx + dy * dy) ** 0.5
                need = ((p.w + q.w) / 2 + 0.6 + (p.h + q.h) / 2 + 0.6) / 2
                if d < 1e-6:
                    dx, dy, d = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                if d < need * 2.2:
                    f = 1.6 * (1 - d / (need * 2.2)) + (0.8 if d < need else 0)
                    Fx += f * dx / d
                    Fy += f * dy / d
            # edge push
            Fx += max(0, (m + p.w / 2 + 1 - p.x)) * 2 - max(0, (p.x - (board.width - m - p.w / 2 - 1))) * 2
            Fy += max(0, (m + p.h / 2 + 1 - p.y)) * 2 - max(0, (p.y - (board.height - m - p.h / 2 - 1))) * 2
            # Langevin noise
            Fx += rng.gauss(0, 1) * 1.4 * T
            Fy += rng.gauss(0, 1) * 1.4 * T
            p.x = min(max(p.x + step * Fx, p.w / 2 + m), board.width - p.w / 2 - m)
            p.y = min(max(p.y + step * Fy, p.h / 2 + m), board.height - p.h / 2 - m)


def optimize(board, seeds=4, iters=400, seed=0) -> float:
    """Multi-seed diffusion; whole run is one undoable effect. Returns cost."""
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    old_traces = list(board.traces)
    best, best_pos = None, None
    for s in range(seeds):
        _diffuse_once(board, iters, seed + s)
        c = cost(board)
        if best is None or c < best:
            best, best_pos = c, {r: (p.x, p.y) for r, p in board.parts.items()}
    for r, (x, y) in best_pos.items():
        board.parts[r].x, board.parts[r].y = x, y
    board.traces = old_traces
    final = {r: (p.x, p.y) for r, p in board.parts.items()}

    def _do():
        for r, (x, y) in final.items():
            board.parts[r].x, board.parts[r].y = x, y

    def _undo():
        for r, (x, y) in snap_pos.items():
            board.parts[r].x, board.parts[r].y = x, y

    board.ctx.emit(_do, _undo)
    return best


def assign_layers(board):
    """Greedy: constrained nets keep layers; rest pick layer with fewer
    bbox crossings. Power nets default wide + bottom for GND."""
    for c in board.constraints:
        if c.get("t") == "layer" and c["net"] in board.nets:
            board.nets[c["net"]].layer = c["layer"]
        if c.get("t") == "width" and c["net"] in board.nets:
            board.nets[c["net"]].width = c["width"]
    for c in board.constraints:
        if c.get("t") == "power":
            for n in c.get("nets", []):
                if n in board.nets:
                    board.nets[n].width = max(board.nets[n].width, 0.5)
    order = sorted(board.nets.values(), key=lambda n: -len(n.pins))
    boxes: dict[int, list] = {l: [] for l in range(board.layers)}
    for net in order:
        pts = [board.pad_pos(r, p) for r, p in net.pins if r in board.parts]
        if not pts:
            continue
        box = (min(q[0] for q in pts), min(q[1] for q in pts),
               max(q[0] for q in pts), max(q[1] for q in pts))
        if net.layer is None:
            def hits(l):
                return sum(1 for b in boxes[l] if not (
                    box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]))
            net.layer = min(boxes, key=hits)
        boxes[net.layer].append(box)
    if "GND" in board.nets and board.nets["GND"].layer is None:
        board.nets["GND"].layer = board.layers - 1


def route(board):
    """Ordered star L-routes on assigned layers. One undoable effect."""
    assign_layers(board)
    old = list(board.traces)
    new: list[Seg] = []
    for net in board.nets.values():
        pts = [(r, board.pad_pos(r, p)) for r, p in net.pins if r in board.parts]
        if len(pts) < 2:
            continue
        layer = net.layer if net.layer is not None else 0
        hub = pts[0][1]
        for _, pt in pts[1:]:
            # L via mid: pick orientation with shorter stub to hub-x first
            if abs(pt[0] - hub[0]) > abs(pt[1] - hub[1]):
                mid = (pt[0], hub[1])
            else:
                mid = (hub[0], pt[1])
            if mid != hub:
                new.append(Seg(net.name, hub[0], hub[1], mid[0], mid[1], layer, net.width))
            if mid != pt:
                new.append(Seg(net.name, mid[0], mid[1], pt[0], pt[1], layer, net.width))
    board.ctx.emit(lambda: board.traces.__setitem__(slice(None), new),
                   lambda: board.traces.__setitem__(slice(None), old))
    return len(new)
