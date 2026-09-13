"""Diffusion placement + greedy layers + L-router. Stdlib only.

Placement = Langevin diffusion: parts drift along net-spring forces and
pairwise repulsion with decaying temperature/noise. Run N seeds, keep best
(Quilter-style candidates for free).

Animation: optimize(..., frames=True) records per-seed snapshots
[{cost, pos:{ref:(x,y)}}]; route(..., frames=True) records per-net segment
batches. The studio UI tweens between snapshots (ease-out cubic) — parts
glide, traces grow. Headless callers pay nothing (default off).
# ponytail: O(n^2) forces, L-router only — push-and-shove when warnings annoy.
"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING, cast
from .circuit import Part, Seg
from .types import BBox, Frame, XY

if TYPE_CHECKING:
    from .circuit import Board


def _fixed(board: Board) -> dict[str, XY]:
    out: dict[str, XY] = {}
    for c in board.constraints:
        if c.get("t") == "fixed":
            out[str(c["ref"])] = (float(cast(float, c["x"])), float(cast(float, c["y"])))
    return out


def _near(board: Board) -> list[tuple[str, str, float]]:
    out: list[tuple[str, str, float]] = []
    for c in board.constraints:
        if c.get("t") == "near":
            w = c.get("w", 2.0)
            out.append((str(c["a"]), str(c["b"]), float(cast(float, w))))
        elif c.get("t") == "near-group":  # keep an include's parts together
            refs = [r for r, q in board.parts.items() if q.owner == c["prefix"]]
            out += [(refs[i], refs[i + 1], 1.5) for i in range(len(refs) - 1)]
    return out


def wirelength(board: Board) -> float:
    tot = 0.0
    for net in board.nets.values():
        pts = []
        for ref, pin in net.pins:
            if ref in board.parts:
                pts.append(board.pad_pos(ref, pin))
        for i in range(1, len(pts)):
            tot += abs(pts[i][0] - pts[0][0]) + abs(pts[i][1] - pts[0][1])
    return tot


def cost(board: Board) -> float:
    parts = list(board.parts.values())
    c = wirelength(board)
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            aw, ah, bw, bh = (*a.wh(), *b.wh())
            if (abs(a.x - b.x) < (aw + bw) / 2 + 0.4 and
                    abs(a.y - b.y) < (ah + bh) / 2 + 0.4):
                c += 1e6
    m = edge_margin(board)
    lib = board._lib()
    for p in parts:
        if lib.get(p.fp, {}).get("edge"):
            continue  # edge-mount hangs off-board by design
        pw, ph = p.wh()
        if not (pw / 2 + m <= p.x <= board.width - pw / 2 - m and
                ph / 2 + m <= p.y <= board.height - ph / 2 - m):
            c += 1e5
    for na, nb, wgt in _near(board):
        if na in board.parts and nb in board.parts:
            qa, qb = board.parts[na], board.parts[nb]
            c += wgt * (abs(qa.x - qb.x) + abs(qa.y - qb.y))
    c += _match_cost(board) + _diff_cost(board)
    return c


def _net_length(board: Board, net: str) -> float:
    """Routed length if traces exist, else Manhattan estimate from pads."""
    segs = [s for s in board.traces if s.net == net]
    if segs:
        return sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in segs)
    pts = [board.pad_pos(r, q) for r, q in board.nets[net].pins if r in board.parts]
    if len(pts) < 2:
        return 0.0
    return sum(abs(p[0] - pts[0][0]) + abs(p[1] - pts[0][1]) for p in pts[1:])


def _match_cost(board: Board) -> float:
    """match NET... within TOL: penalize max length deviation × 50."""
    c = 0.0
    for con in board.constraints:
        if con.get("t") != "match":
            continue
        nets = [n for n in cast(list[str], con.get("nets", [])) if n in board.nets]
        if len(nets) < 2:
            continue
        lens = [_net_length(board, n) for n in nets]
        c += 50.0 * (max(lens) - min(lens))
    return c


def _diff_cost(board: Board) -> float:
    """diff P N gap G: penalize pair length mismatch × 100 + gap error × 20."""
    c = 0.0
    for con in board.constraints:
        if con.get("t") != "diff":
            continue
        p, n = str(con.get("p")), str(con.get("n"))
        if p not in board.nets or n not in board.nets:
            continue
        c += 100.0 * abs(_net_length(board, p) - _net_length(board, n))
        gap = float(cast(float, con.get("gap", 0.3)))
        pp = [board.pad_pos(r, q) for r, q in board.nets[p].pins if r in board.parts]
        np_ = [board.pad_pos(r, q) for r, q in board.nets[n].pins if r in board.parts]
        if pp and np_:
            # closest pad-pair distance should equal gap (coupling entry)
            d = min(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in pp for b in np_)
            c += 20.0 * abs(d - gap)
    return c


def edge_margin(board: Board) -> float:
    for c in board.constraints:
        if c.get("t") == "edge":
            return float(cast(float, c.get("margin", 0.5)))
    return 0.5


def _diffuse_once(board: Board, iters: int = 400, seed: int = 0,
                   frames: list[Frame] | None = None, every: int = 10,
                   pull: float = 0.08, spread: float = 1.0,
                   edge: float | None = None, thermal: bool = False) -> None:
    rng = random.Random(seed)
    fx = _fixed(board)
    near = _near(board)
    m = edge if edge is not None else edge_margin(board)
    parts = [p for r, p in board.parts.items() if r not in fx]
    for r, (x, y) in fx.items():
        if r in board.parts:
            board.parts[r].x, board.parts[r].y = x, y
    if not parts:
        return
    if frames is not None:
        frames.append(_snap(board))
    for p in parts:  # random init inside board
        pw, ph = p.wh()
        p.x = rng.uniform(pw / 2 + m, board.width - pw / 2 - m)
        p.y = rng.uniform(ph / 2 + m, board.height - ph / 2 - m)
    # net membership
    mem: dict[str, list[str]] = {p.ref: [] for p in parts}
    for net in board.nets.values():
        for ref, _ in net.pins:
            if ref in mem:
                mem[ref].append(net.name)
    for t in range(iters):
        T = 1 - t / iters  # temperature
        step = (0.25 + 0.65 * T) * (0.3 + 0.7 * T)  # shrink late: settle, don't jitter
        for p in parts:
            Fx = Fy = 0.0
            # springs to net centroids
            for nname in mem[p.ref]:
                net = board.nets[nname]
                pts = [board.pad_pos(r, pn) for r, pn in net.pins if r in board.parts]
                if len(pts) > 1:
                    cx = sum(q[0] for q in pts) / len(pts)
                    cy = sum(q[1] for q in pts) / len(pts)
                    Fx += pull * (cx - p.x)
                    Fy += pull * (cy - p.y)
            for a, b, w in near:
                if p.ref == a and b in board.parts:
                    q = board.parts[b]
                    Fx += 0.05 * w * (q.x - p.x)
                    Fy += 0.05 * w * (q.y - p.y)
                elif p.ref == b and a in board.parts:
                    q = board.parts[a]
                    Fx += 0.05 * w * (q.x - p.x)
                    Fy += 0.05 * w * (q.y - p.y)
            # pairwise repulsion: radial spread + hard box-penetration
            # push (matches cost()'s overlap box, so dynamics feel the cliff).
            # thermal: big bodies repel harder + drift to edges.
            for q in board.parts.values():
                if q is p:
                    continue
                area_k = 1.0
                if thermal:
                    area_k = 1.0 + (q.w * q.h) / 25.0
                    edge_cx, edge_cy = board.width / 2, board.height / 2
                    Fx += 0.02 * (p.x - edge_cx) * (p.w * p.h) / 25.0
                    Fy += 0.02 * (p.y - edge_cy) * (p.w * p.h) / 25.0
                dx, dy = p.x - q.x, p.y - q.y
                d = (dx * dx + dy * dy) ** 0.5
                pw, ph, qw, qh = (*p.wh(), *q.wh())
                need = ((pw + qw) / 2 + 0.6 + (ph + qh) / 2 + 0.6) / 2
                if d < 1e-6:
                    dx, dy, d = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                if d < need * 2.2:
                    f = spread * area_k * (3.2 * (1 - d / (need * 2.2)) + (1.6 if d < need else 0))
                    Fx += f * dx / d
                    Fy += f * dy / d
                pen_x = (pw + qw) / 2 + 0.4 - abs(dx)
                pen_y = (ph + qh) / 2 + 0.4 - abs(dy)
                if pen_x > 0 and pen_y > 0:
                    push = spread * (4.0 + 8.0 * min(pen_x, pen_y))
                    if pen_x < pen_y:
                        Fx += push if dx >= 0 else -push
                    else:
                        Fy += push if dy >= 0 else -push
            # edge push (skipped for edge-mount parts: they live off-board)
            is_edge = bool(board._lib().get(p.fp, {}).get("edge"))
            pw, ph = p.wh()
            if not is_edge:
                Fx += max(0, (m + pw / 2 + 1 - p.x)) * 2 - max(0, (p.x - (board.width - m - pw / 2 - 1))) * 2
                Fy += max(0, (m + ph / 2 + 1 - p.y)) * 2 - max(0, (p.y - (board.height - m - ph / 2 - 1))) * 2
            # Langevin noise
            Fx += rng.gauss(0, 1) * 1.4 * T
            Fy += rng.gauss(0, 1) * 1.4 * T
            if is_edge:
                # clamp x on-board, let y hang off the bottom edge (tongue out)
                p.x = min(max(p.x + step * Fx, pw / 2 + m), board.width - pw / 2 - m)
                p.y = min(p.y + step * Fy, board.height - 1.0)
            else:
                p.x = min(max(p.x + step * Fx, pw / 2 + m), board.width - pw / 2 - m)
                p.y = min(max(p.y + step * Fy, ph / 2 + m), board.height - ph / 2 - m)
        if frames is not None and (t % every == 0 or t == iters - 1):
            frames.append(_snap(board))


def _snap(board: Board) -> Frame:
    return {"cost": round(cost(board), 1),
            "pos": {r: (round(q.x, 2), round(q.y, 2)) for r, q in board.parts.items()}}


def optimize(board: Board, seeds: int = 4, iters: int = 400, seed: int = 0,
             frames: list[Frame] | None = None, every: int = 10,
             pull: float = 0.08, spread: float = 1.0,
             edge: float | None = None, thermal: bool = False) -> float:
    """Multi-seed diffusion; whole run is one undoable effect. Returns cost.
    frames: optional list to append animation snapshots to.
    pull/spread/edge/thermal: objective knobs (see placer plugins)."""
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    old_traces = list(board.traces)
    best: float = 0.0
    best_pos: dict[str, XY] = {}
    first = True
    for s in range(seeds):
        if frames is not None:
            frames.append({"seed": s})
        _diffuse_once(board, iters, seed + s, frames=frames, every=every,
                      pull=pull, spread=spread, edge=edge, thermal=thermal)
        c = cost(board)
        if first or c < best:
            first = False
            best, best_pos = c, {r: (q.x, q.y) for r, q in board.parts.items()}
    for r, (x, y) in best_pos.items():
        board.parts[r].x, board.parts[r].y = x, y
    board.traces = old_traces
    final = {r: (p.x, p.y) for r, p in board.parts.items()}

    def _do() -> None:
        for r, (x, y) in final.items():
            board.parts[r].x, board.parts[r].y = x, y

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            board.parts[r].x, board.parts[r].y = x, y

    board.ctx.emit(_do, _undo)
    return best


def assign_layers(board: Board) -> None:
    """Greedy: constrained nets keep layers; rest pick layer with fewer
    bbox crossings. Power nets default wide; GND goes to the last layer
    (bottom on 2L, first inner plane on 4L+). 1-layer boards: all → 0."""
    for c in board.constraints:
        if c.get("t") == "layer" and c["net"] in board.nets:
            board.nets[str(c["net"])].layer = int(cast(int, c["layer"]))
        if c.get("t") == "width" and c["net"] in board.nets:
            board.nets[str(c["net"])].width = float(cast(float, c["width"]))
    for c in board.constraints:
        if c.get("t") == "power":
            nets = cast(list[str], c.get("nets", []))
            for n in nets:
                if n in board.nets:
                    board.nets[n].width = max(board.nets[n].width, 0.5)
    if board.layers == 1:
        for net in board.nets.values():
            net.layer = 0
        return
    order = sorted(board.nets.values(), key=lambda n: -len(n.pins))
    boxes: dict[int, list[BBox]] = {ll: [] for ll in range(board.layers)}
    for net in order:
        pts = [board.pad_pos(r, q) for r, q in net.pins if r in board.parts]
        if not pts:
            continue
        bx: BBox = (min(q[0] for q in pts), min(q[1] for q in pts),
                    max(q[0] for q in pts), max(q[1] for q in pts))
        if net.layer is None:
            def hits(ll: int) -> int:
                return sum(1 for bb in boxes[ll] if not (
                    bx[2] < bb[0] or bx[0] > bb[2] or bx[3] < bb[1] or bx[1] > bb[3]))
            net.layer = min(boxes, key=hits)
        boxes[net.layer].append(bx)
    if "GND" in board.nets and board.nets["GND"].layer is None:
        board.nets["GND"].layer = board.layers - 1


def route(board: Board, frames: list[Frame] | None = None) -> int:
    """Ordered star L-routes on assigned layers. One undoable effect.
    frames: optional list; one snapshot per routed net for trace animation.
    # ponytail: no obstacle avoidance — maze router (router:maze) does that.
    """
    assign_layers(board)
    old = list(board.traces)
    new: list[Seg] = []
    for net in board.nets.values():
        pts = [(r, board.pad_pos(r, q)) for r, q in net.pins if r in board.parts]
        if len(pts) < 2:
            continue
        layer = net.layer if net.layer is not None else 0
        hub = pts[0][1]
        for _, pt in pts[1:]:
            # L via mid: pick orientation with shorter stub to hub-x first
            mid: XY
            if abs(pt[0] - hub[0]) > abs(pt[1] - hub[1]):
                mid = (pt[0], hub[1])
            else:
                mid = (hub[0], pt[1])
            if mid != hub:
                new.append(Seg(net.name, hub[0], hub[1], mid[0], mid[1], layer, net.width))
            if mid != pt:
                new.append(Seg(net.name, mid[0], mid[1], pt[0], pt[1], layer, net.width))
        if frames is not None:
            frames.append({"net": net.name, "layer": layer,
                           "segs": [(s.x1, s.y1, s.x2, s.y2) for s in new
                                    if s.net == net.name]})

    def _do() -> None:
        board.traces[:] = new

    def _undo() -> None:
        board.traces[:] = old

    board.ctx.emit(_do, _undo)
    return len(new)
