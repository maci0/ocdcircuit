"""Maze (A*) router: grid wavefront with obstacle avoidance + vias.

Grid 0.25mm; blocked cells = part courtyards (+gap) + foreign-net copper.
Cost: step + bend penalty + layer-change (via) penalty. Multi-pin nets route
pin-to-pin (chain), reusing own-net copper as free terrain. One undoable
effect; streams frames like the L-router.
# ponytail: O(cells) per pin pair; grid 0.25 fixed — coarser when boards grow.
"""
from __future__ import annotations
import heapq
from typing import TYPE_CHECKING, cast

from .circuit import Seg
from .types import Frame, XY

if TYPE_CHECKING:
    from .circuit import Board

GRID = 0.25
BEND = 1.5
VIA_COST = 8.0


def _constraints(board: Board) -> dict[str, float]:
    grid, bend, via = GRID, BEND, VIA_COST
    for c in board.constraints:
        if c.get("t") == "route-grid":
            grid = float(cast(float, c.get("grid", GRID)))
        elif c.get("t") == "route-penalty":
            bend = float(cast(float, c.get("bend", BEND)))
            via = float(cast(float, c.get("via", VIA_COST)))
    return {"grid": grid, "bend": bend, "via": via}


def _blocked(board: Board, grid: float) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    gap = 0.35
    for p in board.parts.values():
        x0 = int((p.x - p.w / 2 - gap) / grid)
        x1 = int((p.x + p.w / 2 + gap) / grid)
        y0 = int((p.y - p.h / 2 - gap) / grid)
        y1 = int((p.y + p.h / 2 + gap) / grid)
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                cells.add((gx, gy))
    return cells


def _astar(start: tuple[int, int, int], goal: tuple[int, int],
           blocked: set[tuple[int, int]], soft: set[tuple[int, int]],
           own: set[tuple[int, int, int]],
           nx: int, ny: int, nl: int, bend: float, via: float,
           ) -> list[tuple[int, int, int]] | None:
    """(gx, gy, layer) search. own-net cells are free (copper reuse).
    soft (part courtyard) cells passable at +SOFT per cell — escapes work,
    open field preferred."""
    SOFT = 15.0
    INF = float("inf")
    sx, sy, sl = start
    gx, gy = goal

    def h(x: int, y: int) -> float:
        return abs(x - gx) + abs(y - gy)

    openh: list[tuple[float, float, tuple[int, int, int], int]] = []
    heapq.heappush(openh, (h(sx, sy), 0.0, (sx, sy, sl), -1))
    best: dict[tuple[int, int, int], float] = {(sx, sy, sl): 0.0}
    prev: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    dirs = ((1, 0, 0), (-1, 0, 0), (0, 1, 1), (0, -1, 1))
    while openh:
        _f, g, node, ndir = heapq.heappop(openh)
        if g > best.get(node, INF):
            continue
        x, y, ll = node
        if (x, y) == (gx, gy):
            path = [node]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            path.reverse()
            return path
        for dx, dy, dd in dirs:
            nx2, ny2 = x + dx, y + dy
            if not (0 <= nx2 < nx and 0 <= ny2 < ny):
                continue
            step = 1.0 + (bend if dd != ndir and ndir != -1 else 0.0)
            for l2 in (ll, 1 - ll) if nl == 2 else (ll,):
                if (nx2, ny2) in blocked and (nx2, ny2, l2) not in own and (nx2, ny2) != (gx, gy):
                    continue
                if (nx2, ny2) in soft and (nx2, ny2, l2) not in own:
                    step += 15.0
                ng = g + step + (via if l2 != ll else 0.0)
                key = (nx2, ny2, l2)
                if ng < best.get(key, INF):
                    best[key] = ng
                    prev[key] = node
                    heapq.heappush(openh, (ng + h(nx2, ny2), ng, key, dd))
    return None


def maze(board: Board, frames: list[Frame] | None = None) -> int:
    """Route every net on the grid, avoiding parts + foreign copper."""
    from .solver import assign_layers
    assign_layers(board)
    P = _constraints(board)
    grid, bend, via = P["grid"], P["bend"], P["via"]
    nx, ny = max(1, int(board.width / grid) + 1), max(1, int(board.height / grid) + 1)
    base_blocked = _blocked(board, grid)
    # foreign pads are obstacles too (routing over them shorts the net)
    pad_cells: dict[tuple[int, int], str] = {}
    for n, net in board.nets.items():
        for ref, pin in net.pins:
            if ref in board.parts:
                px, py = board.pad_pos(ref, pin)
                for gx in (int(px / grid) - 1, int(px / grid), int(px / grid) + 1):
                    for gy in (int(py / grid) - 1, int(py / grid), int(py / grid) + 1):
                        pad_cells.setdefault((gx, gy), n)
    old = list(board.traces)
    new: list[Seg] = []
    copper: set[tuple[int, int]] = set()  # foreign-net routed cells (never freed)
    for net in board.nets.values():
        pts = [(r, board.pad_pos(r, q)) for r, q in net.pins if r in board.parts]
        if len(pts) < 2:
            continue
        layer = net.layer if net.layer is not None else 0
        # pads need exits: part courtyards are soft (costly) terrain, but
        # foreign copper + foreign pads stay hard (else nets short)
        blocked = set(copper)
        blocked.update(c for c, owner in pad_cells.items() if owner != net.name)
        soft = set(base_blocked)
        for _, (px, py) in pts:
            r = 1.0
            for gx in range(int((px - r) / grid), int((px + r) / grid) + 1):
                for gy in range(int((py - r) / grid), int((py + r) / grid) + 1):
                    if (gx, gy) not in copper and pad_cells.get((gx, gy), net.name) == net.name:
                        soft.discard((gx, gy))
        own: set[tuple[int, int, int]] = set()
        ok = True
        for i in range(1, len(pts)):
            a, b = pts[i - 1][1], pts[i][1]
            s = (min(nx - 1, max(0, int(a[0] / grid))),
                 min(ny - 1, max(0, int(a[1] / grid))), layer)
            g = (min(nx - 1, max(0, int(b[0] / grid))),
                 min(ny - 1, max(0, int(b[1] / grid))))
            path = _astar(s, g, blocked, soft, own, nx, ny, board.layers, bend, via)
            if path is None:
                ok = False
                break
            new.extend(_path_segs(board, net.name, path, grid, net.width))
            own.update(path)
        if not ok:
            # fall back to straight L for this net (never fail a build)
            hub = pts[0][1]
            for _, pt in pts[1:]:
                mid: XY = (pt[0], hub[1]) if abs(pt[0] - hub[0]) > abs(pt[1] - hub[1]) else (hub[0], pt[1])
                if mid != hub:
                    new.append(Seg(net.name, hub[0], hub[1], mid[0], mid[1], layer, net.width))
                if mid != pt:
                    new.append(Seg(net.name, mid[0], mid[1], pt[0], pt[1], layer, net.width))
        # foreign copper: block routed cells + 1-ring halo for later nets
        # (cell grid can't resolve sub-cell clearance, so exclusion wins)
        for (gx, gy, _ll) in own:
            for hx in (gx - 1, gx, gx + 1):
                for hy in (gy - 1, gy, gy + 1):
                    blocked.add((hx, hy))
                    copper.add((hx, hy))
        if frames is not None:
            frames.append({"net": net.name, "layer": layer,
                           "segs": [(s.x1, s.y1, s.x2, s.y2) for s in new
                                    if s.net == net.name]})
    board.ctx.emit(lambda: board.traces.__setitem__(slice(None), new),
                   lambda: board.traces.__setitem__(slice(None), old))
    return len(new)


def _path_segs(board: Board, net: str, path: list[tuple[int, int, int]],
               grid: float, width: float) -> list[Seg]:
    """Collapse grid path into Manhattan segs; split on direction/layer change."""
    from .circuit import Seg as S
    out: list[S] = []
    if len(path) < 2:
        return out
    ax, ay, al = path[0]
    bx, by, bl = path[1]
    dx, dy = bx - ax, by - ay  # last-step direction (unit)
    for gx, gy, ll in path[2:]:
        if ll != bl or (gx - bx, gy - by) != (dx, dy):
            if (ax, ay) != (bx, by):
                out.append(S(net, ax * grid, ay * grid, bx * grid, by * grid, bl, width))
            if ll != bl:
                v = S(net, bx * grid, by * grid, bx * grid, by * grid, bl, width)
                v.via = True  # type: ignore[attr-defined]
                out.append(v)
            ax, ay, al = bx, by, ll
            dx, dy = gx - bx, gy - by
        bx, by, bl = gx, gy, ll
    if (ax, ay) != (bx, by):
        out.append(S(net, ax * grid, ay * grid, bx * grid, by * grid, bl, width))
    return out


def _dir(px: int, py: int, sx: int, sy: int, gx: int, gy: int) -> tuple[int, int]:
    dx, dy = (1 if gx > px else -1 if gx < px else 0), (1 if gy > py else -1 if gy < py else 0)
    if sx == px and sy == py:
        return (dx, dy)
    return (1 if px > sx else -1 if px < sx else 0,
            1 if py > sy else -1 if py < sy else 0)
