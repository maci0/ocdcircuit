"""Diffusion placement + greedy layers + L-router. Stdlib only, except
an optional numpy fast path for the hot loops (falls back automatically).

Placement = Langevin diffusion: parts drift along net-spring forces and
pairwise repulsion with decaying temperature/noise. Run N seeds, keep best
(Quilter-style candidates for free).

Animation: optimize(..., frames=True) records per-seed snapshots
[{cost, pos:{ref:(x,y)}}]; route(..., frames=True) records per-net segment
batches. The studio UI tweens between snapshots (ease-out cubic) — parts
glide, traces grow. Headless callers pay nothing (default off).
# ponytail: scalar O(n²) repulsion is exact and instant at shipped scales
# (all boards place in <1s; diffusion is the default path). Vector path
# above covers n≥16 when numpy exists; push-and-shove when warnings annoy.
"""
from __future__ import annotations
from .util import numpy as _numpy
import random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from .circuit import Part, Seg
from .types import BIG_RAILS as BIG_RAILS, BBox, Frame, GNDS as GNDS, XY

if TYPE_CHECKING:
    from .circuit import Board


def _fixed(board: Board) -> dict[str, XY]:
    out: dict[str, XY] = {}
    for c in board.constraints:
        if c.get("t") == "fixed":
            out[str(c["ref"])] = (float(cast(float, c["x"])), float(cast(float, c["y"])))
    return out


def _near(board: Board) -> list[tuple[str, str, float]]:
    """Keep-together pairs. `near-group` members come from one owner index
    built lazily on the first group, not from a fresh parts scan per group:
    cost() calls this ~105x per placement, and the old scan was
    len(constraints) x len(parts) owner compares — 1725 x 5420 = 9.4M per
    call, 31s of a 47s discrete6502 run. Same refs, same order, so the cost
    sum accumulates identically."""
    out: list[tuple[str, str, float]] = []
    by_owner: dict[str | None, list[str]] | None = None
    for c in board.constraints:
        if c.get("t") == "near":
            w = c.get("w", 2.0)
            out.append((str(c["a"]), str(c["b"]), float(cast(float, w))))
        elif c.get("t") == "near-group":  # keep an include's parts together
            if by_owner is None:
                by_owner = {}
                for r, q in board.parts.items():
                    by_owner.setdefault(q.owner, []).append(r)
            refs = by_owner.get(cast("str | None", c["prefix"]), [])
            out += [(refs[i], refs[i + 1], 1.5) for i in range(len(refs) - 1)]
    return out


def wirelength(board: Board, pads: dict[tuple[str, str], XY] | None = None) -> float:
    """Star-model wirelength. `pads` is an optional (ref, pin) → rotated
    offset map (see _pad_cache): cost() is called ~5x per repair round and
    walks every pin of every net, and resolving the offset per pin is the
    single hottest call in a placement run. Offsets are static while parts
    only move, so the caller may hoist them; without one this resolves per
    pin exactly as before."""
    tot = 0.0
    parts = board.parts
    for net in board.nets.values():
        pts = []
        for ref, pin in net.pins:
            p = parts.get(ref)
            if p is not None:
                if pads is not None:
                    off = pads.get((ref, str(pin)))
                    if off is None:
                        pts.append(board.pad_pos(ref, pin))
                        continue
                    pts.append((p.x + off[0], p.y + off[1]))
                else:
                    pts.append(board.pad_pos(ref, pin))
        x0, y0 = pts[0] if pts else (0.0, 0.0)
        for i in range(1, len(pts)):
            tot += abs(pts[i][0] - x0) + abs(pts[i][1] - y0)
    return tot


def _keepouts(board: Board) -> list[dict[str, object]]:
    """Live board-frame keepout zones: constraints + footprint keepouts
    (antenna zones ride their part). One funnel for cost/diffusion."""
    from .drc import fp_keepouts, zone_at
    zones = [zone_at(board, c) for c in board.constraints
             if isinstance(c, dict) and c.get("t") == "keepout"]
    lib = board._lib()
    for ref in board.parts:
        zones.extend(fp_keepouts(board, ref, lib))
    return zones


def _keepout_cost(board: Board) -> float:
    """Part center in a keepout (padded by part half-size) × 1e5."""
    from .drc import in_zone
    c = 0.0
    zones = _keepouts(board)
    if not zones:
        return 0.0
    for p in board.parts.values():
        pw, ph = p.wh()
        if any(in_zone(z, p.x, p.y, (pw / 2, ph / 2)) for z in zones):
            c += 1e5
    return c


def _overlap_hits(np: Any, box: list[tuple[float, float, float, float]]) -> int:
    """Count overlapping part pairs (i<j) — the O(n²) half of cost().

    Chunked over rows like _repel_block: the full broadcast would
    materialise an n×n bool pair (14.7M pairs / 29MB at n=5,420). Returns
    the pair count only; the caller does the float accumulation, because
    summing 1e6 k times is not k*1e6 in float64 and cost() picks seeds.
    """
    a = np.asarray(box, dtype=np.float64)
    n = a.shape[0]
    x, y, w, h = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    total = 0
    step = max(64, CHUNK_PAIRS // max(1, n))
    for i0 in range(0, n, step):
        i1 = min(n, i0 + step)
        ox = np.abs(x[i0:i1, None] - x[None, :]) < (w[i0:i1, None] + w[None, :])
        oy = np.abs(y[i0:i1, None] - y[None, :]) < (h[i0:i1, None] + h[None, :])
        hit = ox & oy
        # upper triangle only (j > i), matching the scalar loop's pairs
        rows = np.arange(i0, i1)
        hit &= rows[:, None] < np.arange(n)[None, :]
        total += int(hit.sum())
    return total


def cost(board: Board, pads: dict[tuple[str, str], XY] | None = None) -> float:
    """Placement cost. `pads` is an optional hoisted pad-offset map passed
    straight to wirelength(); omit it and nothing changes."""
    parts = list(board.parts.values())
    c = wirelength(board, pads)
    # Overlap scan is O(n^2) — 14.7M pairs on discrete6502, each asking for two
    # boxes four times. Precompute (x, y, w/2+0.2, h/2+0.2) per part once;
    # aw+bw then equals (aw+bw)/2+0.4 exactly, so placements do not move.
    box = [(p.x, p.y, p.wh()[0] / 2 + 0.2, p.wh()[1] / 2 + 0.2)
           for p in parts]
    np = _numpy()
    hits = -1
    if np is not None and len(box) >= 64:
        hits = _overlap_hits(np, box)
    if hits >= 0:
        # Detection is vectorized; the accumulation is deliberately NOT.
        # c += 1e6 k times differs from c + k*1e6 in float64 at realistic
        # (wirelength, overlap-count) pairs — measured 665/4000 random draws
        # over c0∈[1e2,1e7], k∈[0,200k] — and cost() picks seeds, so a drift
        # here silently changes placements. sum(repeat(1e6, k), c) is also
        # NOT equivalent (501/3000 mismatches: different accumulation
        # strategy). The adds stay one at a time, in this order.
        # ponytail: ~0.4s per cost() at 14.7M overlaps on discrete6502; the
        # upgrade path is math.fsum-style exact accumulation with a proof,
        # not a multiply.
        for _ in range(hits):
            c += 1e6
    else:
        for i in range(len(box)):
            ax, ay, aw, ah = box[i]
            for j in range(i + 1, len(box)):
                bx, by, bw, bh = box[j]
                if abs(ax - bx) < aw + bw and abs(ay - by) < ah + bh:
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
    by_net: dict[str, list[object]] | None = None
    if board.traces and any(
            isinstance(con, dict) and con.get("t") in ("match", "diff")
            for con in board.constraints):
        by_net = {}
        for s in board.traces:
            by_net.setdefault(s.net, []).append(s)
    c += _match_cost(board, by_net) + _diff_cost(board, by_net) + _keepout_cost(board)
    return c


def _net_length(board: Board, net: str,
                by_net: dict[str, list[object]] | None = None) -> float:
    """Routed length if traces exist, else Manhattan estimate from pads."""
    if by_net is not None:
        segs = by_net.get(net, [])
    else:
        segs = [s for s in board.traces if s.net == net]
    if segs:
        return sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in segs)  # type: ignore[attr-defined]
    pts = [board.pad_pos(r, q) for r, q in board.nets[net].pins if r in board.parts]
    if len(pts) < 2:
        return 0.0
    return sum(abs(p[0] - pts[0][0]) + abs(p[1] - pts[0][1]) for p in pts[1:])


def _match_cost(board: Board,
                by_net: dict[str, list[object]] | None = None) -> float:
    """match NET... within TOL: penalize max length deviation × 50."""
    c = 0.0
    for con in board.constraints:
        if con.get("t") != "match":
            continue
        nets = [n for n in cast(list[str], con.get("nets", [])) if n in board.nets]
        if len(nets) < 2:
            continue
        lens = [_net_length(board, n, by_net) for n in nets]
        c += 50.0 * (max(lens) - min(lens))
    return c


def _diff_cost(board: Board,
               by_net: dict[str, list[object]] | None = None) -> float:
    """diff P N gap G: penalize pair length mismatch × 100 + gap error × 20."""
    c = 0.0
    for con in board.constraints:
        if con.get("t") != "diff":
            continue
        p, n = str(con.get("p")), str(con.get("n"))
        if p not in board.nets or n not in board.nets:
            continue
        c += 100.0 * abs(_net_length(board, p, by_net) - _net_length(board, n, by_net))
        gap = float(cast(float, con.get("gap", 0.3)))
        pp = [board.pad_pos(r, q) for r, q in board.nets[p].pins if r in board.parts]
        np_ = [board.pad_pos(r, q) for r, q in board.nets[n].pins if r in board.parts]
        if pp and np_:
            # closest pad-pair distance should equal gap (coupling entry)
            d = min(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in pp for b in np_)
            c += 20.0 * abs(d - gap)
    return c


# In-tree public leaf math for sibling engines (drc warnings, score).
# Apps use Board.feasible/candidates — not these cost terms.
net_length = _net_length
match_cost = _match_cost
diff_cost = _diff_cost


def edge_margin(board: Board) -> float:
    for c in board.constraints:
        if c.get("t") == "edge":
            return float(cast(float, c.get("margin", 0.5)))
    return 0.5


def _diffuse_once(board: Board, iters: int = 400, seed: int = 0,
                   frames: list[Frame] | None = None, every: int = 10,
                   pull: float = 0.08, spread: float = 1.0,
                   edge: float | None = None, thermal: bool = False,
                   init: bool = True) -> None:
    np = _numpy()
    # vector path pays below n=16 (numpy overhead ≈ win) and risks a worse
    # basin on tiny boards (measured: blinky vec 264+airwire vs scalar 258.8
    # clean). Scalar stays exact where it's already instant.
    if np is not None and not thermal and len(board.parts) >= 16:
        _diffuse_np(board, np, iters, seed, frames, every, pull, spread, edge, init)
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
    if init:
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
            # keepout escape lives in cost()'s 1e5 cliff (seed selection),
            # not here: a dynamics push fights packing on dense boards
            # (measured +4..6 overlaps on breath_ketone) and loses.
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


# Pair budget per repulsion chunk. Small chunks are much faster than
# large ones (5,400 parts, 1 iteration: 50k -> 1.29s, 4M -> 1.99s): the
# chunk has to fit in cache, and the loop overhead is negligible next to it.
CHUNK_PAIRS = 50_000


def _repel_block(np: Any, F: Any, pos: Any, wh: Any, opos: Any, owh: Any,
                  spread: float, self_pairs: bool) -> None:
    """Repulsion of `pos` (n,2) against `opos` (m,2), accumulated into F.

    Chunked over rows: the full broadcast would materialise ~13 float64 n×n
    arrays per iteration (2.6GB at n=5,400, measured). Each chunk is a
    contiguous slab, so it stays in cache; the j-sum is unchanged per row.
    `self_pairs` masks the diagonal (a part does not repel itself).
    """
    n = pos.shape[0]
    m = opos.shape[0]
    if not m:
        return
    step = max(64, CHUNK_PAIRS // max(1, m))
    for i0 in range(0, n, step):
        i1 = min(n, i0 + step)
        px = pos[i0:i1]
        pw = wh[i0:i1]
        dxy = px[:, None, :] - opos[None, :, :]
        d = np.sqrt((dxy ** 2).sum(-1))
        if self_pairs:
            idx = np.arange(i0, i1)
            d[idx - i0, idx] = 1e9  # self-distance, same as fill_diagonal
        tiny = d < 1e-6
        d = np.where(tiny, 1.0, d)
        need = ((pw[:, None, 0] + owh[None, :, 0]) / 2 + 0.6
                + (pw[:, None, 1] + owh[None, :, 1]) / 2 + 0.6) / 2
        close = d < need * 2.2
        fmag = np.where(close, spread * (3.2 * (1 - d / (need * 2.2))
                                        + np.where(d < need, 1.6, 0.0)), 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            F[i0:i1] += ((fmag / d)[:, :, None]
                         * np.where(tiny[:, :, None], 0.0, dxy)).sum(1)
        pen = (pw[:, None, :] + owh[None, :, :]) / 2 + 0.4 - np.abs(dxy)
        both = (pen[:, :, 0] > 0) & (pen[:, :, 1] > 0)
        ax, ay = pen[:, :, 0], pen[:, :, 1]
        push = np.where(both, spread * (4.0 + 8.0 * np.minimum(ax, ay)), 0.0)
        xmask = both & (ax < ay)
        F[i0:i1, 0] += (np.where(xmask, np.sign(dxy[:, :, 0]), 0.0) * push).sum(1)
        F[i0:i1, 1] += (np.where(~xmask & both, np.sign(dxy[:, :, 1]), 0.0)
                        * push).sum(1)


def _diffuse_np(board: Board, np: Any, iters: int, seed: int,
                 frames: list[Frame] | None, every: int,
                 pull: float, spread: float, edge: float | None,
                 init: bool = True) -> None:
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
    if init:
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
    # A net's centroid is one value per iteration, but the old loop rebuilt
    # it once per member part: sum(len(net.pins)) over (part, net) pairs
    # instead of sum(len(net.pins)) over nets — 409x the work on virgo
    # (1.81M point-builds per iteration vs 4.4k). Resolve each net's pin
    # list once here, then compute each centroid once below and reuse it for
    # every member. Same pins, same order, same arithmetic.
    net_pins: dict[str, list[tuple[Part, XY]]] = {}
    for nname in {nn for r in refs for nn in mem[r]}:
        pl = [(board.parts[rr], pads.get((rr, str(pn)), (0.0, 0.0)))
              for rr, pn in board.nets[nname].pins if rr in board.parts]
        if len(pl) > 1:
            net_pins[nname] = pl
    if frames is not None:
        frames.append(_snap(board))
    for t in range(iters):
        T = 1 - t / iters
        step = (0.25 + 0.65 * T) * (0.3 + 0.7 * T)
        F = np.zeros((n, 2))
        # springs to net centroids (pad-accurate via cache), one per net
        cent: dict[str, XY] = {}
        for nname, pl in net_pins.items():
            k = len(pl)
            cent[nname] = (sum(q.x + off[0] for q, off in pl) / k,
                           sum(q.y + off[1] for q, off in pl) / k)
        for r in refs:
            i = idx[r]
            for nname in mem[r]:
                c = cent.get(nname)
                if c is not None:
                    F[i, 0] += pull * (c[0] - pos[i, 0])
                    F[i, 1] += pull * (c[1] - pos[i, 1])
        for a, b, w in near_idx:
            F[a] += 0.05 * w * (pos[b] - pos[a])
            F[b] += 0.05 * w * (pos[a] - pos[b])
        # pairwise repulsion, chunked (was one n*n broadcast)
        _repel_block(np, F, pos, wh, pos, wh, spread, True)
        if len(spos):
            # fixed parts repel movers (positions static, no back-reaction)
            _repel_block(np, F, pos, wh, spos, swh, spread, False)
        # edge push
        lox = m + wh[:, 0] / 2 + 1 - pos[:, 0]
        hix = pos[:, 0] - (board.width - m - wh[:, 0] / 2 - 1)
        loy = m + wh[:, 1] / 2 + 1 - pos[:, 1]
        hiy = pos[:, 1] - (board.height - m - wh[:, 1] / 2 - 1)
        F[:, 0] += np.maximum(0, lox) * 2 - np.maximum(0, hix) * 2
        F[:, 1] += np.maximum(0, loy) * 2 - np.maximum(0, hiy) * 2
        # no keepout force here: cost()'s 1e5 cliff steers seed selection;
        # a dynamics push fights packing on dense boards (measured +4..6
        # overlaps on breath_ketone) and loses. See scalar path comment.
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


def _emit_positions(board: Board, final: dict[str, XY],
                    snap_pos: dict[str, XY]) -> None:
    """One undoable effect that applies `final` and restores `snap_pos`.
    Skips refs removed since the snapshot (parts added/removed still undo)."""

    def _do() -> None:
        for r, (x, y) in final.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y

    board.emit(_do, _undo)


def _best_of_seeds(board: Board, seeds: int, frames: list[Frame] | None,
                   run_once: Callable[[int], None],
                   repair: bool = False) -> float:
    """Run `run_once(s)` per seed, keep the cheapest layout, and leave
    exactly one undoable effect behind. Returns the winning cost.

    optimize/hierarchical/multilevel differed only in the per-seed body;
    the keep-best, restore-best, reset-traces and emit(do, undo) scaffold
    around it was copied three times."""
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    old_traces = list(board.traces)
    best: float = 0.0
    best_pos: dict[str, XY] = {}
    first = True
    for s in range(seeds):
        if frames is not None:
            frames.append({"seed": s})
        run_once(s)
        c = cost(board)
        if first or c < best:
            first = False
            best, best_pos = c, {r: (q.x, q.y) for r, q in board.parts.items()}
    for r, (x, y) in best_pos.items():
        board.parts[r].x, board.parts[r].y = x, y
    if repair:
        _repair(board)
    board.traces = old_traces
    _emit_positions(board, {r: (p.x, p.y) for r, p in board.parts.items()},
                    snap_pos)
    return best

def optimize(board: Board, seeds: int = 4, iters: int = 400, seed: int = 0,
             frames: list[Frame] | None = None, every: int = 10,
             pull: float = 0.08, spread: float = 1.0,
             edge: float | None = None, thermal: bool = False) -> float:
    """Multi-seed diffusion; whole run is one undoable effect. Returns cost.
    frames: optional list to append animation snapshots to.
    pull/spread/edge/thermal: objective knobs (see placer plugins)."""

    def _once(s: int) -> None:
        _diffuse_once(board, iters, seed + s, frames=frames, every=every,
                      pull=pull, spread=spread, edge=edge, thermal=thermal)

    return _best_of_seeds(board, seeds, frames, _once, repair=True)


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
    out: list[dict[str, object]] = []
    for i in range(max(1, n)):
        board.place(key, seed=seed + i, seeds=seeds, iters=iters, **k)
        out.append({"seed": seed + i,
                    "cost": round(cost(board), 1),
                    "pos": {r: (round(q.x, 2), round(q.y, 2))
                            for r, q in board.parts.items()}})
    board.ctx.rollback(snap)  # inner place() effects discarded; one below
    _emit_positions(board, {r: (p.x, p.y) for r, p in board.parts.items()},
                    snap_pos)
    out.sort(key=lambda c: cast(float, c["cost"]))
    return out


def restore_candidate(board: Board, cand: dict[str, object]) -> None:
    """Apply a picked gallery layout: one undoable effect (positions)."""
    pos = cast(dict[str, tuple[float, float]], cand["pos"])
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    final = {r: (float(xy[0]), float(xy[1])) for r, xy in pos.items()
             if r in board.parts}
    _emit_positions(board, final, snap_pos)


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
            out[ll] = {"ok": True, "segs": n, "wirelength": wl,
                       "congestion": _congestion(board)}
        finally:
            board.layers = old_layers
            board.traces = old_traces
            board.ctx.rollback(snap)
    return out


def _congestion(board: Board) -> dict[str, object]:
    """Cheap pre-maze difficulty: crossings among the lroute estimate +
    pin density. No maze burn — the traces are already in hand.
    crossings/total pairs in [0,1]; dense when pins crowd the board."""
    segs = [(s.x1, s.y1, s.x2, s.y2, s.layer) for s in board.traces]
    cross = 0
    pairs = 0
    for i in range(len(segs)):
        x1, y1, x2, y2, la = segs[i]
        for j in range(i + 1, len(segs)):
            x3, y3, x4, y4, lb = segs[j]
            if la != lb:
                continue
            pairs += 1
            # proper intersection of open segments (shared endpoints excluded)
            d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
            if d == 0:
                continue
            ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / d
            ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / d
            if 0.0 < ua < 1.0 and 0.0 < ub < 1.0:
                cross += 1
    pins = sum(len(net.pins) for net in board.nets.values())
    area = max(1.0, board.width * board.height)
    return {"crossings": cross,
            "pairs": pairs,
            "ratio": round(cross / pairs, 3) if pairs else 0.0,
            "pins_per_mm2": round(pins / area, 3)}


def _repair(board: Board, rounds: int = 8) -> None:
    """Min-conflicts repair (research §4): greedy place leaves overlaps;
    repeatedly move the most-conflicted part to its min-cost spot.
    Runs inside optimize's undoable effect (positions restored by _undo)."""
    import random
    rng = random.Random(0)
    fx = _fixed(board)
    lib = board._lib()
    parts = [p for p in board.parts.values() if p.ref not in fx]

    np = _numpy()
    marg = edge_margin(board)  # hoisted: rescans every constraint per call

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
            if not (pw / 2 + marg <= p.x <= board.width - pw / 2 - marg and
                    ph / 2 + marg <= p.y <= board.height - ph / 2 - marg):
                n += 1
        return n

    def _worst() -> Part:
        """The most-conflicted movable part.

        max(parts, key=_bad) ran an O(n) python scan per part — O(n·m) per
        round (2.9M wh() calls on virgo). Same counts, same first-wins tie
        break as max/argmax; falls back to the scalar key when numpy is
        absent or the board is small enough that setup dominates.
        """
        if np is None or len(parts) < 64:
            return max(parts, key=_bad)
        allp = list(board.parts.values())
        ax = np.fromiter((q.x for q in allp), float, len(allp))
        ay = np.fromiter((q.y for q in allp), float, len(allp))
        aw = np.fromiter((q.size[0] for q in allp), float, len(allp))
        ah = np.fromiter((q.size[1] for q in allp), float, len(allp))
        mi = np.fromiter((i for i, q in enumerate(allp) if q.ref not in fx),
                         int, len(parts))
        px, py, pw, ph = ax[mi], ay[mi], aw[mi], ah[mi]
        counts = np.zeros(len(parts), dtype=np.int64)
        step = max(64, CHUNK_PAIRS // max(1, len(allp)))
        for i0 in range(0, len(parts), step):
            i1 = min(len(parts), i0 + step)
            ox = (np.abs(px[i0:i1, None] - ax[None, :])
                  < (pw[i0:i1, None] + aw[None, :]) / 2 + 0.4)
            oy = (np.abs(py[i0:i1, None] - ay[None, :])
                  < (ph[i0:i1, None] + ah[None, :]) / 2 + 0.4)
            hit = ox & oy
            hit[np.arange(i1 - i0), mi[i0:i1]] = False  # q is p
            counts[i0:i1] = hit.sum(1)
        off = ~((pw / 2 + marg <= px) & (px <= board.width - pw / 2 - marg)
                & (ph / 2 + marg <= py) & (py <= board.height - ph / 2 - marg))
        edge_fp = np.fromiter(
            (bool(lib.get(q.fp, {}).get("edge")) for q in parts),
            bool, len(parts))
        counts += (off & ~edge_fp).astype(np.int64)
        return parts[int(counts.argmax())]

    # repair only translates parts — no rotation, no footprint change — so
    # the rotated pad offsets are constant for the whole run. Resolving them
    # per pin inside cost() was the hottest call left (467k pad_pos per
    # 20-iter virgo placement); hoist once, hand to every cost() below.
    pads = _pad_cache(board)
    for _ in range(rounds):
        if not parts:
            return
        p = _worst()
        if _bad(p) == 0:
            return
        m = marg
        pw, ph = p.wh()
        bx, by, bc = p.x, p.y, cost(board, pads)
        for _ in range(12):
            p.x = min(max(rng.uniform(bx - 8, bx + 8), pw / 2 + m), board.width - pw / 2 - m)
            p.y = min(max(rng.uniform(by - 8, by + 8), ph / 2 + m), board.height - ph / 2 - m)
            c = cost(board, pads)
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
    def _once(s: int) -> None:
        _hier_once(board, groups, iters, seed + s, frames, every,
                   pull, spread, edge, thermal)

    return _best_of_seeds(board, seeds, frames, _once)


def _hier_once(board: Board, groups: dict[str, list[str]], iters: int, seed: int,
               frames: list[Frame] | None, every: int,
               pull: float, spread: float, edge: float | None, thermal: bool) -> None:
    """One seed: solve prototype internals, stamp, rigid-body global."""
    import random
    from .circuit import Board as _Board
    rng = random.Random(seed)
    # Per-owner pin lists, one pass over the netlist. The level-1 loop used to
    # rescan every net for every owner (1725 owners x 9493 nets on
    # discrete6502 = ~20 min a seed before a single part moved); multilevel
    # already indexes this way. Net order is board.nets order per owner and
    # pins stay in net order, so each proto is built exactly as before.
    net_pins: dict[str, dict[str, list[tuple[str, str]]]] = {o: {} for o in groups}
    for n, net in board.nets.items():
        for r, q in net.pins:
            p = board.parts.get(r)
            if p is None or p.owner not in net_pins:
                continue
            net_pins[p.owner].setdefault(n, []).append((r, q))
    # --- level 1: prototype = first instance of each owner, solved alone ---
    offsets: dict[str, dict[str, XY]] = {}  # owner → {ref: (dx, dy)}
    anchors: dict[str, XY] = {}  # owner → prototype centroid after solve
    lib = board._lib()
    for owner, refs in groups.items():
        proto = _Board("proto", board.width, board.height, board.layers)
        proto.custom_fp.update(board.custom_fp)  # blocks may use custom `fp`
        for ref in refs:
            p = board.parts[ref]
            meta = lib[p.fp]
            w = meta["w"]
            h = meta["h"]
            assert isinstance(w, float) and isinstance(h, float)
            from .circuit import Part as _Part
            proto.parts[ref] = _Part(ref=ref, fp=p.fp, value=p.value,
                                     x=p.x, y=p.y, w=w, h=h)
        # internal nets only (both ends inside the group)
        for n, pins in net_pins[owner].items():
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
    # Same core as multilevel: spatial-hash repulsion + net membership index.
    # The old inline O(parts×nets) + O(parts²) loop was the slow twin of
    # `_rigid_diffuse` kept around for free-part interleaving; free parts
    # still move each iter when `free=True`.
    fx = _fixed(board)
    for r, (x, y) in fx.items():
        if r in board.parts:
            board.parts[r].x, board.parts[r].y = x, y
    _rigid_diffuse(board, groups, iters, rng, m, pull, spread, frames, every,
                   free=True)
    # Free parts can still land on a stamped instance (group repulsion is
    # one-sided during the Gauss-Seidel scan). Nudge only owner-less parts
    # so instance rigidity stays intact.
    _repair_free(board, rng)


def _repair_free(board: Board, rng: random.Random, rounds: int = 24) -> None:
    """Min-conflict moves for owner-less parts only (hierarchical post-pass)."""
    fx = _fixed(board)
    lib = board._lib()
    m = edge_margin(board)
    free = [p for r, p in board.parts.items() if not p.owner and r not in fx]
    if not free:
        return
    pads = _pad_cache(board)

    def overlapping(p: Part) -> bool:
        pw, ph = p.wh()
        for q in board.parts.values():
            if q is p:
                continue
            qw, qh = q.wh()
            if (abs(p.x - q.x) < (pw + qw) / 2 + 0.4 and
                    abs(p.y - q.y) < (ph + qh) / 2 + 0.4):
                return True
        return False

    for _ in range(rounds):
        moved = False
        for p in free:
            if not overlapping(p):
                continue
            pw, ph = p.wh()
            bx, by = p.x, p.y
            best: tuple[float, float, float] | None = None  # cost, x, y
            lo_x, hi_x = pw / 2 + m, board.width - pw / 2 - m
            lo_y, hi_y = ph / 2 + m, board.height - ph / 2 - m
            if lo_x > hi_x or lo_y > hi_y:
                continue
            for _try in range(64):
                p.x = rng.uniform(lo_x, hi_x)
                p.y = rng.uniform(lo_y, hi_y)
                if overlapping(p):
                    continue
                c = cost(board, pads)
                if best is None or c < best[0]:
                    best = (c, p.x, p.y)
                    moved = True
            if best is not None:
                p.x, p.y = best[1], best[2]
            else:
                p.x, p.y = bx, by
        if not moved:
            return


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
        if 1 < len(owners_set) <= 6 and net.name not in BIG_RAILS:
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
    """(ref, pin) → board-frame offset, rotation already applied.

    pad_pos() re-resolves the plugin and merges the lib dict per call (~13µs),
    and the diffusion loops read an offset per pin per net per iteration —
    millions of `rot_xy` calls as well. Both are static for a placement run,
    so resolve and rotate once here; callers add the part position."""
    from .parts import pads_of
    lib = board._lib()
    out: dict[tuple[str, str], XY] = {}
    for ref, p in board.parts.items():
        try:
            pads = pads_of(p.fp, lib)
        except KeyError:
            continue
        for pin, (dx, dy) in pads.items():
            # pads_of already IS the offset — pin_offset() would rebuild the
            # same dict per pin (O(pins²) per part).
            out[(ref, str(pin))] = p.rot_xy(dx, dy)
    return out


def _rigid_diffuse(board: Board, groups: dict[str, list[str]], iters: int,
                   rng: random.Random, m: float,
                   pull: float, spread: float,
                   frames: list[Frame] | None, every: int,
                   free: bool = False) -> None:
    """Rigid-body diffusion over arbitrary groups (level-2 core, reused by
    multilevel). Translates whole groups, deforms nothing.
    free=True also Langevin-steps owner-less parts each iter (hierarchical).
    # ponytail: spatial hash (CELL=4mm) keeps repulsion ~O(n); net index
    # avoids the per-part × per-net scan."""
    fx = _fixed(board)
    # net membership index (built once — board topology is fixed)
    mem_all: dict[str, list[str]] = {}
    for net in board.nets.values():
        for ref, _ in net.pins:
            if ref in board.parts:
                mem_all.setdefault(ref, []).append(net.name)
    CELL = 4.0
    pads = _pad_cache(board)
    # giant rails carry no placement signal (centroid ≈ board center) —
    # skip them instead of rebuilding thousand-pin ext lists per member
    small_nets = {n for n, net in board.nets.items()
                  if n not in BIG_RAILS and len(net.pins) <= 32}
    mem = {r: [n for n in ns if n in small_nets]
           for r, ns in mem_all.items()}
    # free-part springs keep every net (matches the old hierarchical loop)
    free_parts = ([p for r, p in board.parts.items() if not p.owner and r not in fx]
                  if free else [])

    def wpos(ref: str, pin: str) -> XY:
        p = board.parts[ref]
        rx, ry = pads.get((ref, str(pin)), (0.0, 0.0))
        return (p.x + rx, p.y + ry)

    for t in range(iters):
        T = 1 - t / iters
        step = (0.25 + 0.65 * T) * (0.3 + 0.7 * T)
        # This loop is Gauss-Seidel: each group reads LIVE positions, so a
        # group updated earlier in the scan has already moved when a later
        # group looks at it. Anything read from outside the scan is therefore
        # stale. Three attempts to exploit that failed the fingerprint gate —
        # snapshotting positions into the spatial hash, hoisting per-part
        # geometry, and memoising group centroids for the pair loop. Caching
        # here is only valid for values that do not move (hw/hh, pad offsets).
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
        # Packing geometry for this iteration. `wh()` is a call per part-pair
        # (32M of them on discrete6502): the box is fixed within an iteration
        # and the boundary clamp re-reads it, so read each part's once here.
        # Kept as (w, h) and summed exactly as before — folding it into one
        # "radius" is cheaper but not algebraically identical, which moves
        # every placement (the snapshot gate caught the drift).
        hw: dict[str, float] = {}
        hh: dict[str, float] = {}
        for _r, _q in board.parts.items():
            _w, _h = _q.wh()
            hw[_r] = _w
            hh[_r] = _h
        cent: dict[str, XY] = {}
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
                            need = ((hw[ref] + hw[oref]) / 2 + 0.6
                                    + (hh[ref] + hh[oref]) / 2 + 0.6) / 2
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
                        # Centroids are memoised per iteration and dropped
                        # the moment their group moves (below), so a hit
                        # always returns the same live positions the sum
                        # would: hoisting them for the whole iteration is
                        # what the fingerprint gate rejected (Gauss-Seidel —
                        # an earlier group has already moved). ~72k requests
                        # against 1725 groups per iteration on discrete6502.
                        oc = cent.get(other)
                        if oc is None:
                            oc = (sum(board.parts[r].x for r in orefs) / len(orefs),
                                  sum(board.parts[r].y for r in orefs) / len(orefs))
                            cent[other] = oc
                        ox, oy = oc
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
            cent.pop(owner, None)  # this group moved: cached centroid is stale
        # free parts: net springs + nearby repulsion + noise (hierarchical
        # used to spring-only; without a push, free connectors sit on top
        # of stamped instances after the spatial-hash group pass).
        for p in free_parts:
            Fx = Fy = 0.0
            for nname in mem_all.get(p.ref, []):
                net = board.nets[nname]
                pts = [wpos(r, q) for r, q in net.pins if r in board.parts]
                if len(pts) > 1:
                    Fx += pull * (sum(q[0] for q in pts) / len(pts) - p.x)
                    Fy += pull * (sum(q[1] for q in pts) / len(pts) - p.y)
            gx, gy = int(p.x / CELL), int(p.y / CELL)
            for ix in (gx - 1, gx, gx + 1):
                for iy in (gy - 1, gy, gy + 1):
                    for oref in grid.get((ix, iy), []):
                        if oref == p.ref:
                            continue
                        o = board.parts[oref]
                        dx, dy = p.x - o.x, p.y - o.y
                        d = (dx * dx + dy * dy) ** 0.5
                        need = ((hw[p.ref] + hw[oref]) / 2 + 0.6
                                + (hh[p.ref] + hh[oref]) / 2 + 0.6) / 2
                        if d < 1e-6:
                            dx, dy, d = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                        if d < need * 2.2:
                            f = spread * (3.2 * (1 - d / (need * 2.2))
                                          + (1.6 if d < need else 0))
                            Fx += f * dx / d
                            Fy += f * dy / d
            Fx += rng.gauss(0, 1) * 1.4 * T
            Fy += rng.gauss(0, 1) * 1.4 * T
            pw, ph = hw[p.ref], hh[p.ref]
            p.x = min(max(p.x + step * Fx, pw / 2 + m), board.width - pw / 2 - m)
            p.y = min(max(p.y + step * Fy, ph / 2 + m), board.height - ph / 2 - m)
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
    m = edge if edge is not None else edge_margin(board)

    def _once(s: int) -> None:
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
        # One pass over the netlist (same as hierarchical): avoids
        # owners × nets rescans on boards like discrete6502.
        net_pins: dict[str, dict[str, list[tuple[str, str]]]] = {
            o: {} for o in groups}
        for n, net in board.nets.items():
            for r, q in net.pins:
                p = board.parts.get(r)
                if p is None or p.owner not in net_pins:
                    continue
                net_pins[p.owner].setdefault(n, []).append((r, q))
        lib = board._lib()
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
                meta = lib[p.fp]
                w, h = meta["w"], meta["h"]
                assert isinstance(w, float) and isinstance(h, float)
                from .circuit import Part as _Part
                proto.parts[ref] = _Part(ref=ref, fp=p.fp, value=p.value,
                                     x=p.x, y=p.y, w=w, h=h)
            for n, pins in net_pins[owner].items():
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
        _rigid_diffuse(board, super_groups, max(20, iters // 4),
                       rng, m, pull, spread, frames, every)
        # level 3: per-instance rigid refine
        _rigid_diffuse(board, groups, max(20, iters // 2),
                       rng, m, pull, spread, frames, every)
        # level 4: short per-part relax (few iters, keeps instances ~rigid
        # via near-group springs already on the board). Skipped at scale:
        # full O(n^2) relax costs minutes past ~1500 parts, rigid levels
        # already placed everything.
        if len(board.parts) <= 1500:
            _diffuse_once(board, max(10, iters // 10), seed + s, frames=None,
                          every=every, pull=pull * 0.5, spread=spread,
                          edge=edge, thermal=thermal)
    return _best_of_seeds(board, seeds, frames, _once)


def assign_layers(board: Board) -> None:
    """Greedy: constrained nets keep layers; rest pick layer with fewer
    bbox crossings. Power nets default wide; ground aliases (GND/VSS/0)
    go to the last layer (bottom on 2L, first inner plane on 4L+). 1-layer
    boards: all → 0. One undoable effect — but only when something actually
    changes, so routers keep their undo accounting (wiremask emits exactly 1).
    (Layer/width assignment used to leak through place/route undo —
    caught by the undo fuzzer.)"""
    snap = {n: (net.layer, net.width) for n, net in board.nets.items()}
    # reset first: layer/width are runtime caches, not state. Without this
    # a removed constraint leaves its last assignment behind forever.
    for net in board.nets.values():
        net.layer, net.width = None, 0.3
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
    else:
        order = sorted(board.nets.values(), key=lambda n: -len(n.pins))
        boxes: dict[int, list[BBox]] = {ll: [] for ll in range(board.layers)}
        for net in order:
            pts = [board.pad_pos(r, q) for r, q in net.pins if r in board.parts]
            if not pts:
                continue
            bx: BBox = (min(q[0] for q in pts), min(q[1] for q in pts),
                        max(q[0] for q in pts), max(q[1] for q in pts))
            if net.name in GNDS and net.layer is None:
                # Doc intent: grounds on the last layer. The old post-loop
                # `if layer is None` park never ran for connected GND — the
                # greedy pass above had already assigned it.
                net.layer = board.layers - 1
            elif net.layer is None:
                def hits(ll: int) -> int:
                    return sum(1 for bb in boxes[ll] if not (
                        bx[2] < bb[0] or bx[0] > bb[2] or bx[3] < bb[1] or bx[1] > bb[3]))
                net.layer = min(boxes, key=hits)
            elif not 0 <= net.layer < board.layers:
                # `route N on 9` on a 2-layer board: lint reports it as an error
                # and that is where the user is told, but the router must not
                # die on it (it did, with a bare `KeyError: 9` from this dict).
                # Clamp into the stackup and route the net somewhere real.
                net.layer = max(0, min(board.layers - 1, net.layer))
            boxes[net.layer].append(bx)
    if any((net.layer, net.width) != snap[n]
           for n, net in board.nets.items() if n in snap):
        def _undo() -> None:
            for n, (layer, width) in snap.items():
                if n in board.nets:
                    board.nets[n].layer, board.nets[n].width = layer, width

        def _do() -> None:
            pass  # already applied; redo is re-run, not replay

        board.emit(_do, _undo)


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
        mark = len(new)
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
                           "segs": [(s.x1, s.y1, s.x2, s.y2)
                                    for s in new[mark:]]})

    def _do() -> None:
        board.traces[:] = new

    def _undo() -> None:
        board.traces[:] = old

    board.emit(_do, _undo)
    return len(new)
