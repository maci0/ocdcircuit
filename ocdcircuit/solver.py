"""Diffusion placement + greedy layers + L-router. Stdlib only, except
an optional numpy fast path for the hot loops (falls back automatically).

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
from typing import TYPE_CHECKING, Any, cast
from .circuit import Part, Seg
from .types import BBox, Frame, XY

_np = None  # lazy: imported on first vectorized run, not at package load


def _numpy() -> Any:
    """numpy handle, imported once on first use (~70ms — not at startup)."""
    global _np
    if _np is None:
        try:
            import numpy as _m
            _np = _m
        except ImportError:
            return None
    return _np

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
    np = _numpy()
    # vector path pays below n=16 (numpy overhead ≈ win) and risks a worse
    # basin on tiny boards (measured: blinky vec 264+airwire vs scalar 258.8
    # clean). Scalar stays exact where it's already instant.
    if np is not None and not thermal and len(board.parts) >= 16:
        _diffuse_np(board, np, iters, seed, frames, every, pull, spread, edge)
        return
    rng = random.Random(seed)
    fx = _fixed(board)
    near = _near(board)
    lib = board._lib()  # hoisted: _lib() merges dicts + resolves plugin per call
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
            is_edge = bool(lib.get(p.fp, {}).get("edge"))
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


def _diffuse_np(board: Board, np: Any, iters: int, seed: int,
                 frames: list[Frame] | None, every: int,
                 pull: float, spread: float, edge: float | None) -> None:
    """Vectorized _diffuse_once: positions/forces as (n,2) arrays, repulsion
    as one broadcast. Jacobi (not Gauss-Seidel) updates — same quality band,
    different trajectories; goldens pin the vector path."""
    import random as _random
    rng = _random.Random(seed)
    fx = _fixed(board)
    near = _near(board)
    lib = board._lib()
    m = edge if edge is not None else edge_margin(board)
    refs = [r for r in board.parts if r not in fx]
    for r, (x, y) in fx.items():
        if r in board.parts:
            board.parts[r].x, board.parts[r].y = x, y
    if not refs:
        return
    n = len(refs)
    idx = {r: i for i, r in enumerate(refs)}
    # fixed parts don't move but still repel (scalar path loops all parts)
    stat = [(board.parts[r].x, board.parts[r].y, board.parts[r].wh())
            for r in board.parts if r in fx]
    spos = np.array([[x, y] for x, y, _ in stat]) if stat else np.zeros((0, 2))
    swh = np.array([wh for _, _, wh in stat]) if stat else np.zeros((0, 2))
    pos = np.array([[board.parts[r].x, board.parts[r].y] for r in refs])
    for r, p in zip(refs, pos):
        pw, ph = board.parts[r].wh()
        p[0] = rng.uniform(pw / 2 + m, board.width - pw / 2 - m)
        p[1] = rng.uniform(ph / 2 + m, board.height - ph / 2 - m)
    for r, p in zip(refs, pos):
        board.parts[r].x, board.parts[r].y = float(p[0]), float(p[1])
    wh = np.array([board.parts[r].wh() for r in refs])
    is_edge = np.array([bool(lib.get(board.parts[r].fp, {}).get("edge"))
                        for r in refs])
    # net springs: per-part list of (centroid fn inputs) — centroids depend
    # on live positions, so store pin refs and recompute per iteration
    mem: dict[str, list[str]] = {r: [] for r in refs}
    for net in board.nets.values():
        for ref, _ in net.pins:
            if ref in mem:
                mem[ref].append(net.name)
    pads = _pad_cache(board)
    near_idx = [(idx[a], idx[b], w) for a, b, w in near
                if a in idx and b in idx]
    if frames is not None:
        frames.append(_snap(board))
    for t in range(iters):
        T = 1 - t / iters
        step = (0.25 + 0.65 * T) * (0.3 + 0.7 * T)
        F = np.zeros((n, 2))
        # springs to net centroids (pad-accurate via cache)
        for r in refs:
            i = idx[r]
            for nname in mem[r]:
                net = board.nets[nname]
                pts = []
                for rr, pn in net.pins:
                    if rr in board.parts:
                        q = board.parts[rr]
                        dx, dy = pads.get((rr, str(pn)), (0.0, 0.0))
                        rx, ry = q.rot_xy(dx, dy)
                        pts.append((q.x + rx, q.y + ry))
                if len(pts) > 1:
                    cx = sum(q[0] for q in pts) / len(pts)
                    cy = sum(q[1] for q in pts) / len(pts)
                    F[i, 0] += pull * (cx - pos[i, 0])
                    F[i, 1] += pull * (cy - pos[i, 1])
        for a, b, w in near_idx:
            F[a] += 0.05 * w * (pos[b] - pos[a])
            F[b] += 0.05 * w * (pos[a] - pos[b])
        # pairwise repulsion, one broadcast: d (n,n), no self-term
        dxy = pos[:, None, :] - pos[None, :, :]
        d = np.sqrt((dxy ** 2).sum(-1))
        np.fill_diagonal(d, 1e9)
        tiny = d < 1e-6
        d = np.where(tiny, 1.0, d)
        need = ((wh[:, None, 0] + wh[None, :, 0]) / 2 + 0.6
                + (wh[:, None, 1] + wh[None, :, 1]) / 2 + 0.6) / 2
        close = d < need * 2.2
        fmag = np.where(close, spread * (3.2 * (1 - d / (need * 2.2))
                                        + np.where(d < need, 1.6, 0.0)), 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            F += ((fmag / d)[:, :, None] * np.where(tiny[:, :, None], 0.0, dxy)).sum(1)
        pen = (wh[:, None, :] + wh[None, :, :]) / 2 + 0.4 - np.abs(dxy)
        both = (pen[:, :, 0] > 0) & (pen[:, :, 1] > 0)
        px, py = pen[:, :, 0], pen[:, :, 1]
        push = np.where(both, spread * (4.0 + 8.0 * np.minimum(px, py)), 0.0)
        xmask = both & (px < py)
        F[:, 0] += (np.where(xmask, np.sign(dxy[:, :, 0]), 0.0) * push).sum(1)
        F[:, 1] += (np.where(~xmask & both, np.sign(dxy[:, :, 1]), 0.0) * push).sum(1)
        if len(spos):
            # fixed parts repel movers (positions static, no back-reaction)
            sxy = pos[:, None, :] - spos[None, :, :]
            sd = np.sqrt((sxy ** 2).sum(-1))
            stiny = sd < 1e-6
            sd = np.where(stiny, 1.0, sd)
            sneed = ((wh[:, None, 0] + swh[None, :, 0]) / 2 + 0.6
                     + (wh[:, None, 1] + swh[None, :, 1]) / 2 + 0.6) / 2
            sclose = sd < sneed * 2.2
            sf = np.where(sclose, spread * (3.2 * (1 - sd / (sneed * 2.2))
                                           + np.where(sd < sneed, 1.6, 0.0)), 0.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                F += ((sf / sd)[:, :, None] * np.where(stiny[:, :, None], 0.0, sxy)).sum(1)
            spen = (wh[:, None, :] + swh[None, :, :]) / 2 + 0.4 - np.abs(sxy)
            sboth = (spen[:, :, 0] > 0) & (spen[:, :, 1] > 0)
            spx, spy = spen[:, :, 0], spen[:, :, 1]
            spush = np.where(sboth, spread * (4.0 + 8.0 * np.minimum(spx, spy)), 0.0)
            sxmask = sboth & (spx < spy)
            F[:, 0] += (np.where(sxmask, np.sign(sxy[:, :, 0]), 0.0) * spush).sum(1)
            F[:, 1] += (np.where(~sxmask & sboth, np.sign(sxy[:, :, 1]), 0.0) * spush).sum(1)
        # edge push
        lox = m + wh[:, 0] / 2 + 1 - pos[:, 0]
        hix = pos[:, 0] - (board.width - m - wh[:, 0] / 2 - 1)
        loy = m + wh[:, 1] / 2 + 1 - pos[:, 1]
        hiy = pos[:, 1] - (board.height - m - wh[:, 1] / 2 - 1)
        F[:, 0] += np.maximum(0, lox) * 2 - np.maximum(0, hix) * 2
        F[:, 1] += np.maximum(0, loy) * 2 - np.maximum(0, hiy) * 2
        F[is_edge] = 0.0
        # noise (same stream shape as scalar path: 2 gausses per part/iter)
        for i in range(n):
            F[i, 0] += rng.gauss(0, 1) * 1.4 * T
            F[i, 1] += rng.gauss(0, 1) * 1.4 * T
        pos += step * F
        # clamp + write back
        for i, r in enumerate(refs):
            pw, ph = wh[i]
            if is_edge[i]:
                nx = min(max(pos[i, 0], pw / 2 + m), board.width - pw / 2 - m)
                ny = min(pos[i, 1], board.height - 1.0)
            else:
                nx = min(max(pos[i, 0], pw / 2 + m), board.width - pw / 2 - m)
                ny = min(max(pos[i, 1], ph / 2 + m), board.height - ph / 2 - m)
            pos[i] = (nx, ny)
            board.parts[r].x, board.parts[r].y = float(nx), float(ny)
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
    _repair(board)
    board.traces = old_traces
    final = {r: (p.x, p.y) for r, p in board.parts.items()}

    def _do() -> None:
        for r, (x, y) in final.items():
            if r in board.parts:  # parts added/removed since still undo
                board.parts[r].x, board.parts[r].y = x, y

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y

    board.ctx.emit(_do, _undo)
    return best


def candidates(board: Board, n: int = 4, key: str | None = None,
                 seed: int = 0, seeds: int = 1, iters: int = 400,
                 **k: object) -> list[dict[str, object]]:
    """N seeded layouts for gallery pick: run the placer N times (distinct
    seeds), snapshot each (cost + positions), restore the board, and leave
    exactly one undoable effect behind (the picked layout goes on top).
    Returns [{seed, cost, pos:{ref:(x,y)}}] sorted by cost. The caller
    applies one via restore_candidate (== one undo step back to here)."""
    from typing import cast
    snap = board.ctx.snapshot()
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    old_traces = list(board.traces)
    out: list[dict[str, object]] = []
    for i in range(max(1, n)):
        board.place(key, seed=seed + i, seeds=seeds, iters=iters, **k)
        out.append({"seed": seed + i,
                    "cost": round(cost(board), 1),
                    "pos": {r: (round(q.x, 2), round(q.y, 2))
                            for r, q in board.parts.items()}})
    board.ctx.rollback(snap)  # inner place() effects discarded; one below
    final = {r: (p.x, p.y) for r, p in board.parts.items()}

    def _do() -> None:
        for r, (x, y) in final.items():
            if r in board.parts:  # parts added/removed since still undo
                board.parts[r].x, board.parts[r].y = x, y

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y

    board.ctx.emit(_do, _undo)
    out.sort(key=lambda c: cast(float, c["cost"]))
    return out


def restore_candidate(board: Board, cand: dict[str, object]) -> None:
    """Apply a picked gallery layout: one undoable effect (positions)."""
    pos = cast(dict[str, tuple[float, float]], cand["pos"])
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    final = {r: (float(xy[0]), float(xy[1])) for r, xy in pos.items()
             if r in board.parts}

    def _do() -> None:
        for r, (x, y) in final.items():
            if r in board.parts:  # parts added/removed since still undo
                board.parts[r].x, board.parts[r].y = x, y

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y

    board.ctx.emit(_do, _undo)


def feasible(board: Board, layers: list[int] | None = None) -> dict[int, dict[str, object]]:
    """Routability hint per layer count: lroute (10x maze speed) on the
    CURRENT placement, snapshot/rollback so the board is untouched.
    Returns {L: {ok, segs, wirelength}} — congestion comparison across
    layer counts, not a maze-clean verdict (lroute is obstacle-blind).
    ok=True here means "routed", not "clean": the real verdict is the
    DRC panel's airwire/jumper warnings at the current layer count."""
    from .solver import route as _route
    out: dict[int, dict[str, object]] = {}
    if layers is None:
        layers = [ll for ll in (1, 2, 4) if ll <= max(2, board.layers)]
    for ll in layers:
        snap = board.ctx.snapshot()
        old_traces = list(board.traces)
        old_layers = board.layers
        try:
            board.layers = ll
            n = _route(board)
            wl = round(sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in board.traces), 1)
            out[ll] = {"ok": True, "segs": n, "wirelength": wl}
        finally:
            board.layers = old_layers
            board.traces = old_traces
            board.ctx.rollback(snap)
    return out


def _repair(board: Board, rounds: int = 8) -> None:
    """Min-conflicts repair (research §4): greedy place leaves overlaps;
    repeatedly move the most-conflicted part to its min-cost spot.
    Runs inside optimize's undoable effect (positions restored by _undo)."""
    import random
    rng = random.Random(0)
    fx = _fixed(board)
    lib = board._lib()
    parts = [p for p in board.parts.values() if p.ref not in fx]

    def _bad(p: object) -> int:
        assert isinstance(p, Part)
        pw, ph = p.wh()
        n = 0
        for q in board.parts.values():
            if q is p:
                continue
            qw, qh = q.wh()
            if (abs(p.x - q.x) < (pw + qw) / 2 + 0.4 and
                    abs(p.y - q.y) < (ph + qh) / 2 + 0.4):
                n += 1
        if not lib.get(p.fp, {}).get("edge"):
            m = edge_margin(board)
            if not (pw / 2 + m <= p.x <= board.width - pw / 2 - m and
                    ph / 2 + m <= p.y <= board.height - ph / 2 - m):
                n += 1
        return n

    for _ in range(rounds):
        if not parts:
            return
        p = max(parts, key=_bad)
        if _bad(p) == 0:
            return
        m = edge_margin(board)
        pw, ph = p.wh()
        bx, by, bc = p.x, p.y, cost(board)
        for _ in range(12):
            p.x = min(max(rng.uniform(bx - 8, bx + 8), pw / 2 + m), board.width - pw / 2 - m)
            p.y = min(max(rng.uniform(by - 8, by + 8), ph / 2 + m), board.height - ph / 2 - m)
            c = cost(board)
            if c < bc:
                bx, by, bc = p.x, p.y, c
        p.x, p.y = bx, by


def hierarchical(board: Board, seeds: int = 4, iters: int = 400, seed: int = 0,
                 frames: list[Frame] | None = None, every: int = 10,
                 pull: float = 0.08, spread: float = 1.0,
                 edge: float | None = None, thermal: bool = False) -> float:
    """Two-level placement for repeated blocks (instances).
    Level 1: solve ONE prototype per block (relative part offsets) with the
    normal engine on a scratch board. Level 2: stamp offsets to every
    instance, then rigid-body diffusion (translate whole instances, never
    deform them). Boards without instances == plain optimize()."""
    groups: dict[str, list[str]] = {}
    for ref, p in board.parts.items():
        if p.owner:
            groups.setdefault(p.owner, []).append(ref)
    if not groups:
        return optimize(board, seeds=seeds, iters=iters, seed=seed,
                        frames=frames, every=every, pull=pull, spread=spread,
                        edge=edge, thermal=thermal)
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    old_traces = list(board.traces)
    best: float = 0.0
    best_pos: dict[str, XY] = {}
    first = True
    for s in range(seeds):
        if frames is not None:
            frames.append({"seed": s})
        _hier_once(board, groups, iters, seed + s, frames, every,
                   pull, spread, edge, thermal)
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
            if r in board.parts:  # parts added/removed since still undo
                board.parts[r].x, board.parts[r].y = x, y

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y

    board.ctx.emit(_do, _undo)
    return best


def _hier_once(board: Board, groups: dict[str, list[str]], iters: int, seed: int,
               frames: list[Frame] | None, every: int,
               pull: float, spread: float, edge: float | None, thermal: bool) -> None:
    """One seed: solve prototype internals, stamp, rigid-body global."""
    import random
    from .circuit import Board as _Board
    rng = random.Random(seed)
    # --- level 1: prototype = first instance of each owner, solved alone ---
    offsets: dict[str, dict[str, XY]] = {}  # owner → {ref: (dx, dy)}
    anchors: dict[str, XY] = {}  # owner → prototype centroid after solve
    for owner, refs in groups.items():
        proto = _Board("proto", board.width, board.height, board.layers)
        proto.custom_fp.update(board.custom_fp)  # blocks may use custom `fp`
        for ref in refs:
            p = board.parts[ref]
            lib = board._lib()
            meta = lib[p.fp]
            w = meta["w"]
            h = meta["h"]
            assert isinstance(w, float) and isinstance(h, float)
            from .circuit import Part as _Part
            proto.parts[ref] = _Part(ref, p.fp, p.value, p.x, p.y, w, h)
        # internal nets only (both ends inside the group)
        for n, net in board.nets.items():
            pins = [(r, q) for r, q in net.pins if r in board.parts and board.parts[r].owner == owner]
            if len(pins) >= 2:
                for r, q in pins:
                    proto.net(n).pins.append((r, q))
        for c in board.constraints:
            if c.get("owner") == owner and c.get("t") == "near":
                proto.constraints.append(dict(c))
        _diffuse_once(proto, max(50, iters // 2), seed, frames=None, every=every,
                      pull=pull, spread=spread, edge=edge, thermal=thermal)
        cx = sum(proto.parts[r].x for r in refs) / len(refs)
        cy = sum(proto.parts[r].y for r in refs) / len(refs)
        anchors[owner] = (cx, cy)
        offsets[owner] = {r: (proto.parts[r].x - cx, proto.parts[r].y - cy) for r in refs}
    # --- stamp: every instance gets prototype offsets around a random center ---
    m = edge if edge is not None else edge_margin(board)
    centers: dict[str, XY] = {}
    for owner, refs in groups.items():
        if owner not in centers:
            # spread instance centers across the board
            centers[owner] = (rng.uniform(10, board.width - 10),
                              rng.uniform(10, board.height - 10))
        ox, oy = centers[owner]
        for ref in refs:
            dx, dy = offsets[owner][ref]
            board.parts[ref].x, board.parts[ref].y = ox + dx, oy + dy
    # free parts random-init like normal
    for ref, p in board.parts.items():
        if not p.owner:
            pw, ph = p.wh()
            p.x = rng.uniform(pw / 2 + m, board.width - pw / 2 - m)
            p.y = rng.uniform(ph / 2 + m, board.height - ph / 2 - m)
    # --- level 2: rigid-body diffusion (translate instances, deform nothing) ---
    fx = _fixed(board)
    for r, (x, y) in fx.items():
        if r in board.parts:
            board.parts[r].x, board.parts[r].y = x, y
    for t in range(iters):
        T = 1 - t / iters
        step = (0.25 + 0.65 * T) * (0.3 + 0.7 * T)
        # move whole owners by centroid force
        for owner, refs in groups.items():
            if any(r in fx for r in refs):
                continue  # pinned instance stays
            cx = sum(board.parts[r].x for r in refs) / len(refs)
            cy = sum(board.parts[r].y for r in refs) / len(refs)
            Fx = Fy = 0.0
            for ref in refs:
                p = board.parts[ref]
                # net springs toward external (non-group) pins
                for n, net in board.nets.items():
                    mypins = [(r, q) for r, q in net.pins if r == ref]
                    if not mypins:
                        continue
                    ext = [board.pad_pos(r, q) for r, q in net.pins
                           if r in board.parts and board.parts[r].owner != owner]
                    if ext:
                        ex = sum(q[0] for q in ext) / len(ext)
                        ey = sum(q[1] for q in ext) / len(ext)
                        Fx += pull * (ex - p.x) / max(1, len(refs))
                        Fy += pull * (ey - p.y) / max(1, len(refs))
                # repulsion vs everything outside the instance
                for o in board.parts.values():
                    if o.owner == owner:
                        continue
                    dx, dy = p.x - o.x, p.y - o.y
                    d = (dx * dx + dy * dy) ** 0.5
                    pw, ph = p.wh()
                    qw, qh = o.wh()
                    need = ((pw + qw) / 2 + 0.6 + (ph + qh) / 2 + 0.6) / 2
                    if d < 1e-6:
                        dx, dy, d = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                    if d < need * 2.2:
                        f = spread * (3.2 * (1 - d / (need * 2.2)) + (1.6 if d < need else 0))
                        Fx += f * dx / d
                        Fy += f * dy / d
            # instance-vs-instance centroid push (joined power nets attract
            # all instances to one spot; this keeps them apart as units).
            # small-N path (hierarchical): plain O(G^2) is fine.
            cx = sum(board.parts[r].x for r in refs) / len(refs)
            cy = sum(board.parts[r].y for r in refs) / len(refs)
            for other, orefs in groups.items():
                if other == owner:
                    continue
                ox = sum(board.parts[r].x for r in orefs) / len(orefs)
                oy = sum(board.parts[r].y for r in orefs) / len(orefs)
                dx, dy = cx - ox, cy - oy
                d = (dx * dx + dy * dy) ** 0.5 or 1.0
                if d < 25.0:
                    push = spread * 6.0 * (1 - d / 25.0)
                    Fx += push * dx / d
                    Fy += push * dy / d
            Fx += rng.gauss(0, 1) * 1.4 * T
            Fy += rng.gauss(0, 1) * 1.4 * T
            # rigid translate with INSTANCE-level clamp (per-part clamp would
            # deform the block when one part touches the edge first)
            dx0, dy0 = step * Fx, step * Fy
            lo_x = max(-(board.parts[r].x - board.parts[r].wh()[0] / 2 - m) for r in refs)
            hi_x = min((board.width - board.parts[r].wh()[0] / 2 - m) - board.parts[r].x for r in refs)
            lo_y = max(-(board.parts[r].y - board.parts[r].wh()[1] / 2 - m) for r in refs)
            hi_y = min((board.height - board.parts[r].wh()[1] / 2 - m) - board.parts[r].y for r in refs)
            dx0 = min(max(dx0, lo_x), hi_x) if lo_x <= hi_x else 0.0
            dy0 = min(max(dy0, lo_y), hi_y) if lo_y <= hi_y else 0.0
            for ref in refs:
                p = board.parts[ref]
                p.x += dx0
                p.y += dy0
        # free parts move normally (single diffusion step each)
        for ref, p in board.parts.items():
            if p.owner or ref in fx:
                continue
            Fx = Fy = 0.0
            for n, net in board.nets.items():
                if not any(r == ref for r, _ in net.pins):
                    continue
                pts = [board.pad_pos(r, q) for r, q in net.pins if r in board.parts]
                if len(pts) > 1:
                    Fx += pull * (sum(q[0] for q in pts) / len(pts) - p.x)
                    Fy += pull * (sum(q[1] for q in pts) / len(pts) - p.y)
            Fx += rng.gauss(0, 1) * 1.4 * T
            Fy += rng.gauss(0, 1) * 1.4 * T
            pw, ph = p.wh()
            p.x = min(max(p.x + step * Fx, pw / 2 + m), board.width - pw / 2 - m)
            p.y = min(max(p.y + step * Fy, ph / 2 + m), board.height - ph / 2 - m)
        if frames is not None and (t % every == 0 or t == iters - 1):
            frames.append(_snap(board))


def _coarsen(board: Board, groups: dict[str, list[str]],
             cap: int = 12) -> dict[str, list[str]]:
    """Cluster instances into super-groups by shared-net connectivity (BFS,
    capped size). Same-block instances prefer each other (repeated layout
    tiles together). Returns super-group → flat ref list."""
    inst_block: dict[str, str] = {}
    for ins in board.instances:
        inst_block[str(ins["prefix"]) + "_"] = str(ins["block"])
    owners = list(groups)
    # adjacency: shared non-power nets
    adj: dict[str, set[str]] = {o: set() for o in owners}
    for net in board.nets.values():
        own = {board.parts[r].owner for r, _ in net.pins
               if r in board.parts and board.parts[r].owner}
        own.discard(None)
        owners_set = cast(set[str], own)
        if 1 < len(owners_set) <= 6 and net.name not in ("vcc", "vss", "GND"):
            for a in owners_set:
                adj[a].update(o for o in owners_set if o != a)
    seen: set[str] = set()
    super_groups: dict[str, list[str]] = {}
    si = 0
    # same-block owners first (tiling), then stragglers by adjacency
    by_block: dict[str, list[str]] = {}
    for o in owners:
        by_block.setdefault(inst_block.get(o, "?"), []).append(o)
    order = [o for bl in sorted(by_block) for o in by_block[bl]]
    for o in order:
        if o in seen:
            continue
        # BFS from o, same block preferred, cap size
        cluster = [o]
        seen.add(o)
        queue = [o]
        while queue and len(cluster) < cap:
            cur = queue.pop(0)
            cands = sorted(adj[cur] - seen,
                           key=lambda x: (inst_block.get(x) != inst_block.get(o), x))
            for x in cands:
                if len(cluster) >= cap:
                    break
                seen.add(x)
                cluster.append(x)
                queue.append(x)
        refs: list[str] = []
        for oo in cluster:
            refs.extend(groups[oo])
        super_groups[f"S{si}_"] = refs
        si += 1
    return super_groups


def _pad_cache(board: Board) -> dict[tuple[str, str], XY]:
    """(ref, pin) → footprint-frame offset. pad_pos() re-resolves the plugin
    + merges the lib dict per call (~13µs); the rigid loop calls it millions
    of times. Offsets are static (rot applied by caller via rot_xy)."""
    from .parts import pads_of, pin_offset
    lib = board._lib()
    out: dict[tuple[str, str], XY] = {}
    for ref, p in board.parts.items():
        try:
            pads = pads_of(p.fp, lib)
        except KeyError:
            continue
        for pin in pads:
            try:
                out[(ref, str(pin))] = pin_offset(p.fp, pin, lib)
            except KeyError:
                continue
    return out


def _rigid_diffuse(board: Board, groups: dict[str, list[str]], iters: int,
                   seed: int, rng: random.Random, m: float,
                   pull: float, spread: float,
                   frames: list[Frame] | None, every: int) -> None:
    """Rigid-body diffusion over arbitrary groups (level-2 core, reused by
    multilevel). Translates whole groups, deforms nothing.
    # ponytail: spatial hash (CELL=4mm) keeps repulsion ~O(n); net index
    # avoids the per-part × per-net scan."""
    fx = _fixed(board)
    # net membership index (built once — board topology is fixed)
    mem: dict[str, list[str]] = {}
    for net in board.nets.values():
        for ref, _ in net.pins:
            if ref in board.parts:
                mem.setdefault(ref, []).append(net.name)
    CELL = 4.0
    pads = _pad_cache(board)
    # giant rails carry no placement signal (centroid ≈ board center) —
    # skip them instead of rebuilding thousand-pin ext lists per member
    BIG = {"vcc", "vss", "GND", "VCC", "VDD", "VSS"}
    small_nets = {n for n, net in board.nets.items()
                  if n not in BIG and len(net.pins) <= 32}
    mem = {r: [n for n in ns if n in small_nets]
           for r, ns in mem.items()}

    def wpos(ref: str, pin: str) -> XY:
        p = board.parts[ref]
        dx, dy = pads.get((ref, str(pin)), (0.0, 0.0))
        rx, ry = p.rot_xy(dx, dy)
        return (p.x + rx, p.y + ry)

    for t in range(iters):
        T = 1 - t / iters
        step = (0.25 + 0.65 * T) * (0.3 + 0.7 * T)
        # spatial hash of all parts (rebuilt per iter — positions move)
        grid: dict[tuple[int, int], list[str]] = {}
        for r, q in board.parts.items():
            grid.setdefault((int(q.x / CELL), int(q.y / CELL)), []).append(r)
        # centroid hash of groups (CELL=4 → ±7 cells covers the 25mm push radius)
        cgrid: dict[tuple[int, int], list[str]] = {}
        for go, gorefs in groups.items():
            gox = sum(board.parts[r].x for r in gorefs) / len(gorefs)
            goy = sum(board.parts[r].y for r in gorefs) / len(gorefs)
            cgrid.setdefault((int(gox / CELL), int(goy / CELL)), []).append(go)
        for owner, refs in groups.items():
            if any(r in fx for r in refs):
                continue
            Fx = Fy = 0.0
            for ref in refs:
                p = board.parts[ref]
                for nname in mem.get(ref, []):
                    net = board.nets[nname]
                    if not any(r == ref for r, _ in net.pins):
                        continue
                    ext = [wpos(r, q) for r, q in net.pins
                           if r in board.parts and board.parts[r].owner != owner]
                    if ext:
                        ex = sum(q[0] for q in ext) / len(ext)
                        ey = sum(q[1] for q in ext) / len(ext)
                        Fx += pull * (ex - p.x) / max(1, len(refs))
                        Fy += pull * (ey - p.y) / max(1, len(refs))
                gx, gy = int(p.x / CELL), int(p.y / CELL)
                for ix in (gx - 1, gx, gx + 1):
                    for iy in (gy - 1, gy, gy + 1):
                        for oref in grid.get((ix, iy), []):
                            o = board.parts[oref]
                            if o is p or o.owner == owner:
                                continue
                            dx, dy = p.x - o.x, p.y - o.y
                            d = (dx * dx + dy * dy) ** 0.5
                            pw, ph = p.wh()
                            qw, qh = o.wh()
                            need = ((pw + qw) / 2 + 0.6 + (ph + qh) / 2 + 0.6) / 2
                            if d < 1e-6:
                                dx, dy, d = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                            if d < need * 2.2:
                                f = spread * (3.2 * (1 - d / (need * 2.2)) + (1.6 if d < need else 0))
                                Fx += f * dx / d
                                Fy += f * dy / d
            cx = sum(board.parts[r].x for r in refs) / len(refs)
            cy = sum(board.parts[r].y for r in refs) / len(refs)
            # group-vs-group push via centroid hash (O(nearby), not O(G^2))
            gx, gy = int(cx / CELL), int(cy / CELL)
            seen_c: set[str] = set()
            for ix in range(gx - 7, gx + 8):
                for iy in range(gy - 7, gy + 8):
                    for other in cgrid.get((ix, iy), []):
                        if other == owner or other in seen_c:
                            continue
                        seen_c.add(other)
                        orefs = groups[other]
                        ox = sum(board.parts[r].x for r in orefs) / len(orefs)
                        oy = sum(board.parts[r].y for r in orefs) / len(orefs)
                        dx, dy = cx - ox, cy - oy
                        d = (dx * dx + dy * dy) ** 0.5 or 1.0
                        if d < 25.0:
                            push = spread * 6.0 * (1 - d / 25.0)
                            Fx += push * dx / d
                            Fy += push * dy / d
            Fx += rng.gauss(0, 1) * 1.4 * T
            Fy += rng.gauss(0, 1) * 1.4 * T
            dx0, dy0 = step * Fx, step * Fy
            lo_x = max(-(board.parts[r].x - board.parts[r].wh()[0] / 2 - m) for r in refs)
            hi_x = min((board.width - board.parts[r].wh()[0] / 2 - m) - board.parts[r].x for r in refs)
            lo_y = max(-(board.parts[r].y - board.parts[r].wh()[1] / 2 - m) for r in refs)
            hi_y = min((board.height - board.parts[r].wh()[1] / 2 - m) - board.parts[r].y for r in refs)
            dx0 = min(max(dx0, lo_x), hi_x) if lo_x <= hi_x else 0.0
            dy0 = min(max(dy0, lo_y), hi_y) if lo_y <= hi_y else 0.0
            for ref in refs:
                p = board.parts[ref]
                p.x += dx0
                p.y += dy0
        if frames is not None and (t % every == 0 or t == iters - 1):
            frames.append(_snap(board))


def multilevel(board: Board, seeds: int = 2, iters: int = 200, seed: int = 0,
               frames: list[Frame] | None = None, every: int = 10,
               pull: float = 0.08, spread: float = 1.0,
               edge: float | None = None, thermal: bool = False) -> float:
    """Three-level placement: prototype internals (level 1, like
    hierarchical) → super-group rigid diffuse (level 2, ~40 bodies) →
    per-instance rigid refine (level 3) → short per-part relax.
    Boards without instances == hierarchical()."""
    import random
    from .circuit import Board as _Board
    groups: dict[str, list[str]] = {}
    for ref, p in board.parts.items():
        if p.owner:
            groups.setdefault(p.owner, []).append(ref)
    if not groups:
        return hierarchical(board, seeds=seeds, iters=iters, seed=seed,
                            frames=frames, every=every, pull=pull,
                            spread=spread, edge=edge, thermal=thermal)
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    old_traces = list(board.traces)
    best: float = 0.0
    best_pos: dict[str, XY] = {}
    first = True
    m = edge if edge is not None else edge_margin(board)
    for s in range(seeds):
        if frames is not None:
            frames.append({"seed": s})
        rng = random.Random(seed + s)
        # level 1: prototype offsets per BLOCK TYPE (identical instances
        # share one solve — 1725 owners but only a handful of blocks).
        # local refs (post-prefix) identify the type signature.
        def _sig(refs: list[str]) -> tuple[tuple[str, str], ...]:
            return tuple(sorted((r.split("_", 1)[1],
                                 board.parts[r].fp) for r in refs))

        sig_of: dict[str, tuple[tuple[str, str], ...]] = {
            o: _sig(refs) for o, refs in groups.items()}
        proto_done: dict[tuple[tuple[str, str], ...], dict[str, XY]] = {}
        offsets: dict[str, dict[str, XY]] = {}
        for owner, refs in groups.items():
            sig = sig_of[owner]
            if sig in proto_done:
                # remap cached local offsets onto this owner's refs
                cached = proto_done[sig]
                loc = {r.split("_", 1)[1]: r for r in refs}
                offsets[owner] = {loc[lr]: dxy for lr, dxy in cached.items()}
                continue
            proto = _Board("proto", board.width, board.height, board.layers)
            proto.custom_fp.update(board.custom_fp)  # blocks may use custom `fp`
            for ref in refs:
                p = board.parts[ref]
                meta = board._lib()[p.fp]
                w, h = meta["w"], meta["h"]
                assert isinstance(w, float) and isinstance(h, float)
                from .circuit import Part as _Part
                proto.parts[ref] = _Part(ref, p.fp, p.value, p.x, p.y, w, h)
            for n, net in board.nets.items():
                pins = [(r, q) for r, q in net.pins
                        if r in board.parts and board.parts[r].owner == owner]
                if len(pins) >= 2:
                    for r, q in pins:
                        proto.net(n).pins.append((r, q))
            _diffuse_once(proto, 50, seed + s, frames=None, every=every,
                          pull=pull, spread=spread, edge=edge, thermal=thermal)
            cx = sum(proto.parts[r].x for r in refs) / len(refs)
            cy = sum(proto.parts[r].y for r in refs) / len(refs)
            local = {r.split("_", 1)[1]: (proto.parts[r].x - cx, proto.parts[r].y - cy)
                     for r in refs}
            proto_done[sig] = local
            offsets[owner] = {r: local[r.split("_", 1)[1]] for r in refs}
        # stamp instances at random centers
        for owner, refs in groups.items():
            ox, oy = rng.uniform(10, board.width - 10), rng.uniform(10, board.height - 10)
            for ref in refs:
                dx, dy = offsets[owner][ref]
                board.parts[ref].x, board.parts[ref].y = ox + dx, oy + dy
        for ref, p in board.parts.items():
            if not p.owner:
                pw, ph = p.wh()
                p.x = rng.uniform(pw / 2 + m, board.width - pw / 2 - m)
                p.y = rng.uniform(ph / 2 + m, board.height - ph / 2 - m)
        # level 2: super-group rigid diffuse (coarse — ~40 bodies, not 4000)
        super_groups = _coarsen(board, groups)
        _rigid_diffuse(board, super_groups, max(20, iters // 4), seed + s,
                       rng, m, pull, spread, frames, every)
        # level 3: per-instance rigid refine
        _rigid_diffuse(board, groups, max(20, iters // 2), seed + s,
                       rng, m, pull, spread, frames, every)
        # level 4: short per-part relax (few iters, keeps instances ~rigid
        # via near-group springs already on the board). Skipped at scale:
        # full O(n^2) relax costs minutes past ~1500 parts, rigid levels
        # already placed everything.
        if len(board.parts) <= 1500:
            _diffuse_once(board, max(10, iters // 10), seed + s, frames=None,
                          every=every, pull=pull * 0.5, spread=spread,
                          edge=edge, thermal=thermal)
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
            if r in board.parts:  # parts added/removed since still undo
                board.parts[r].x, board.parts[r].y = x, y

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y

    board.ctx.emit(_do, _undo)
    return best


def assign_layers(board: Board) -> None:
    """Greedy: constrained nets keep layers; rest pick layer with fewer
    bbox crossings. Power nets default wide; GND goes to the last layer
    (bottom on 2L, first inner plane on 4L+). 1-layer boards: all → 0.
    One undoable effect — but only when something actually changes, so
    routers keep their undo accounting (wiremask emits exactly 1).
    (Layer/width assignment used to leak through place/route undo —
    caught by the undo fuzzer.)"""
    snap = {n: (net.layer, net.width) for n, net in board.nets.items()}
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
    for c in board.constraints:
        if c.get("t") == "class":
            w = float(cast(float, c.get("width", 0.3)))
            for n, net in board.nets.items():
                if net.attrs.get("class") == c.get("name"):
                    net.width = max(net.width, w)
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
    if any((net.layer, net.width) != snap[n]
           for n, net in board.nets.items() if n in snap):
        def _undo() -> None:
            for n, (layer, width) in snap.items():
                if n in board.nets:
                    board.nets[n].layer, board.nets[n].width = layer, width

        def _do() -> None:
            pass  # already applied; redo is re-run, not replay

        board.ctx.emit(_do, _undo)


def route(board: Board, frames: list[Frame] | None = None) -> int:
    """Ordered star L-routes on assigned layers. One undoable effect.
    frames: optional list; one snapshot per routed net for trace animation.
    # ponytail: no obstacle avoidance — maze router (router:maze) does that.
    """
    assign_layers(board)
    old = list(board.traces)
    new: list[Seg] = []
    from .drc import pour_layers
    poured = pour_layers(board)  # same skip as maze: planes replace traces
    for net in board.nets.values():
        if net.layer is not None and net.layer in poured.get(net.name, []):
            continue
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
