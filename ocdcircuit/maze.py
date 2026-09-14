"""Maze (A*) router: grid wavefront with obstacle avoidance + vias.

Grid 0.25mm (route-grid constraint overrides; coarse router uses 2mm);
blocked cells = part courtyards (+gap) + foreign-net copper.
Cost: step + bend penalty + layer-change (via) penalty. Multi-pin nets route
pin-to-pin (chain), reusing own-net copper as free terrain. One undoable
effect; streams frames like the L-router.
"""
from __future__ import annotations
import heapq
from typing import TYPE_CHECKING, cast

from .circuit import Net, Seg
from .types import Frame, XY


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)

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
        pw, ph = p.wh()
        x0 = int((p.x - pw / 2 - gap) / grid)
        x1 = int((p.x + pw / 2 + gap) / grid)
        y0 = int((p.y - ph / 2 - gap) / grid)
        y1 = int((p.y + ph / 2 + gap) / grid)
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                cells.add((gx, gy))
    return cells




def _astar(start: tuple[int, int, int], goal: tuple[int, int],
           blocked: set[tuple[int, int, int]], soft: set[tuple[int, int]],
           own: set[tuple[int, int, int]],
           nx: int, ny: int, nl: int, bend: float, via: float,
           novia: set[tuple[int, int]] | None = None,
           ) -> list[tuple[int, int, int]] | None:
    """(gx, gy, layer) search. own-net cells are free (copper reuse).
    soft (part courtyard) cells passable at +SOFT per cell — escapes work,
    open field preferred. blocked is per-layer: copper on L0 never walls
    L1 (FR4 between); PTH pads/vias arrive expanded on every layer."""
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
            # any-layer vias: stay, or jump to an adjacent layer (stacked).
            # bend regions forbid layer jumps (flex: no vias in bend area)
            alts = [ll] if nl == 1 else [ll, ll - 1, ll + 1]
            if novia and (nx2, ny2) in novia:
                alts = [ll]
            for l2 in alts:
                if not (0 <= l2 < nl):
                    continue
                if ((nx2, ny2, l2) in blocked and (nx2, ny2, l2) not in own
                        and (nx2, ny2) != (gx, gy)):
                    continue
                st = step
                if (nx2, ny2) in soft and (nx2, ny2, l2) not in own:
                    st += 15.0
                ng = g + st + (via if l2 != ll else 0.0)
                key = (nx2, ny2, l2)
                if ng < best.get(key, INF):
                    best[key] = ng
                    prev[key] = node
                    heapq.heappush(openh, (ng + h(nx2, ny2), ng, key, dd))
    return None


from .circuit import Net, Seg


def _net_span(board: Board, net: Net) -> float:
    """BBox diagonal of a net's pads (big nets route later)."""
    pts = [board.pad_pos(r, q) for r, q in net.pins if r in board.parts]
    if len(pts) < 2:
        return 0.0
    dx: float = max(p[0] for p in pts) - min(p[0] for p in pts)
    dy: float = max(p[1] for p in pts) - min(p[1] for p in pts)
    return float((dx * dx + dy * dy) ** 0.5)


def maze(board: Board, frames: list[Frame] | None = None) -> int:
    """Route every net on the grid, avoiding parts + foreign copper."""
    from .core import Plugin
    try:
        plug = board.plugins().get("layers", None)
        assert isinstance(plug, Plugin)
        plug.run(board)
    except (KeyError, AssertionError):
        from .solver import assign_layers
        assign_layers(board)
    P = _constraints(board)
    grid, bend, via = P["grid"], P["bend"], P["via"]
    nx, ny = max(1, int(board.width / grid) + 1), max(1, int(board.height / grid) + 1)
    base_blocked = _blocked(board, grid)
    # keepout/cutout zones join the soft set (+15/cell, all layers):
    # the maze prefers around but crosses when walled in, and DRC flags
    # every crossing as keepout-trace (flagged, never silent). Hard walls
    # would force jumpers where a warned crossing is the right call.
    # Rect fill is the inclusive bounding box (bit-stable); round
    # zones filter by radius.
    from .drc import fp_keepouts, in_zone, zone_at
    zones: list[dict[str, object]] = [
        c for c in board.constraints
        if isinstance(c, dict) and c.get("t") in ("keepout", "cutout")]
    for ref in board.parts:
        zones.extend(fp_keepouts(board, ref))
    for _c in zones:
        c = zone_at(board, _c)
        cx, cy = _f(c["x"]), _f(c["y"])
        if c.get("d") is not None:
            r = _f(c["d"]) / 2
            for gx in range(int((cx - r) / grid), int((cx + r) / grid) + 1):
                for gy in range(int((cy - r) / grid), int((cy + r) / grid) + 1):
                    if in_zone(c, gx * grid, gy * grid):
                        base_blocked.add((gx, gy))
            continue
        hw, hh = _f(c["w"]) / 2, _f(c["h"]) / 2
        for gx in range(int((cx - hw) / grid), int((cx + hw) / grid) + 1):
            for gy in range(int((cy - hh) / grid), int((cy + hh) / grid) + 1):
                base_blocked.add((gx, gy))
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
    copper: set[tuple[int, int, int]] = set()  # per-layer routed cells
    halo: set[tuple[int, int, int]] = set()  # per-layer 1-ring spacing
    cells_of: dict[str, set[tuple[int, int, int]]] = {}

    # small nets first: short point-to-point wires grab direct paths before
    # wide power busses wall off regions (completion beats convention here)
    order = sorted(board.nets.values(), key=lambda n: (len(n.pins), -_net_span(board, n)))
    from .drc import pour_layers
    poured = pour_layers(board)  # poured nets need no traces on pour layers
    failed: list[str] = []
    for net in order:
        if net.layer is not None and net.layer in poured.get(net.name, []):
            continue  # plane covers this layer — nothing to route
        if not _route_one(board, net, grid, bend, via, nx, ny, base_blocked,
                          pad_cells, copper, halo, cells_of, new, frames):
            failed.append(net.name)
    # rip-up retry: drop the blocker crowding each failed net's corridor,
    # re-route failed-first, then re-route the ripped net. A 2nd round runs
    # only if the 1st strictly shrank the failed set (on jumper-structural
    # boards like 1L blinky, extra churn converts routed nets to jumpers).
    n_failed = len(failed)
    for _round in range(2):
        if not failed:
            break
        if _round == 1 and len(failed) >= n_failed:
            for fname in failed:
                fnet = board.nets[fname]
                fpts = [(r, board.pad_pos(r, q)) for r, q in fnet.pins if r in board.parts]
                if len(fpts) >= 2:
                    _fallback(board, fnet, fpts, new)
            break
        still: list[str] = []
        for fname in failed:
            fnet = board.nets[fname]
            fpts = [(r, board.pad_pos(r, q)) for r, q in fnet.pins if r in board.parts]
            if len(fpts) < 2:
                continue
            best, best_hit = "", -1
            for oname, cells in cells_of.items():
                if oname == fname:
                    continue
                hit = sum(1 for (gx, gy, _ll) in cells
                          for (px, py) in (fpts[0][1], fpts[-1][1])
                          if abs(gx * grid - px) + abs(gy * grid - py) < 4.0)
                if hit > best_hit:
                    best, best_hit = oname, hit
            if best_hit <= 0:
                if _round == 1:
                    _fallback(board, fnet, fpts, new)
                else:
                    still.append(fname)
                continue
            ripped = [s for s in new if s.net == best]
            new[:] = [s for s in new if s.net != best]
            del cells_of[best]
            _rebuild_blocked(copper, halo, cells_of)
            if _route_one(board, fnet, grid, bend, via, nx, ny, base_blocked,
                          pad_cells, copper, halo, cells_of, new, frames):
                bnet = board.nets[best]
                bpts = [(r, board.pad_pos(r, q)) for r, q in bnet.pins if r in board.parts]
                if len(bpts) >= 2 and not _route_one(
                        board, bnet, grid, bend, via, nx, ny, base_blocked,
                        pad_cells, copper, halo, cells_of, new, frames):
                    if _round == 1:
                        _fallback(board, bnet, bpts, new)
                    else:
                        still.append(best)
            else:
                if _round == 1:
                    _fallback(board, fnet, fpts, new)
                    for s in ripped:  # restore ripped net as flagged fallback
                        j = Seg(s.net, s.x1, s.y1, s.x2, s.y2, s.layer, s.width)
                        j.jumper = True  # type: ignore[attr-defined]
                        new.append(j)
                else:
                    new.extend(ripped)  # victim back untouched, retry later
                    _rebuild_blocked(copper, halo, cells_of)
                    still.append(fname)
        failed = still
    board.emit(lambda: board.traces.__setitem__(slice(None), new),
                   lambda: board.traces.__setitem__(slice(None), old))
    return len(new)


def _mst_pairs(pts: list[tuple[str, XY]]) -> list[tuple[tuple[str, XY], tuple[str, XY]]]:
    """Prim's tree edges over Manhattan pad distance (research §5).
    O(pins²) — nets are small. Shared trunk beats pin-order chaining."""
    if len(pts) < 3:
        return list(zip(pts, pts[1:]))
    done = [pts[0]]
    rest = pts[1:]
    legs: list[tuple[tuple[str, XY], tuple[str, XY]]] = []
    while rest:
        bi, bj, bd = 0, 0, float("inf")
        for i, (_, a) in enumerate(done):
            for j, (_, b) in enumerate(rest):
                d = abs(a[0] - b[0]) + abs(a[1] - b[1])
                if d < bd:
                    bi, bj, bd = i, j, d
        legs.append((done[bi], rest[bj]))
        done.append(rest.pop(bj))
    return legs


def _route_one(board: Board, net: Net, grid: float, bend: float, via: float,
               nx: int, ny: int, base_blocked: set[tuple[int, int]],
               pad_cells: dict[tuple[int, int], str],
               copper: set[tuple[int, int, int]], halo: set[tuple[int, int, int]],
               cells_of: dict[str, set[tuple[int, int, int]]],
               new: list[Seg], frames: list[Frame] | None) -> bool:
    """Route one net with current blockage. Returns True if maze-succeeded.
    copper/halo are per-layer (FR4 isolates); pads expand onto all layers.
    Legs follow a rectilinear MST over pads (research §5), not pin order —
    one shared trunk instead of N-1 competing maze paths."""
    pts = [(r, board.pad_pos(r, q)) for r, q in net.pins if r in board.parts]
    if len(pts) < 2:
        return True
    legs = _mst_pairs(pts)
    layer = net.layer if net.layer is not None else 0
    # dynamic bend rects: traces pass, layer jumps forbidden
    novia: set[tuple[int, int]] = set()
    for c in board.constraints:
        if c.get("t") == "bend" and c.get("dynamic", True):
            cx, cy = _f(c["x"]), _f(c["y"])
            hw, hh = _f(c["w"]) / 2, _f(c["h"]) / 2
            for gx in range(int((cx - hw) / grid), int((cx + hw) / grid) + 1):
                for gy in range(int((cy - hh) / grid), int((cy + hh) / grid) + 1):
                    novia.add((gx, gy))
    nl = board.layers
    blocked = set(copper) | halo
    for cell, owner in pad_cells.items():
        if owner != net.name:
            for ll in range(nl):  # PTH pads/vias span every layer
                blocked.add((cell[0], cell[1], ll))
    soft = set(base_blocked)
    for _, (px, py) in pts:
        r = 1.0
        for gx in range(int((px - r) / grid), int((px + r) / grid) + 1):
            for gy in range(int((py - r) / grid), int((py + r) / grid) + 1):
                # escape frees own pads + courtyard on every layer, never
                # foreign copper or spacing halo (squeezing there shorts —
                # dense pile-ups must rip-up instead).
                if (pad_cells.get((gx, gy), net.name) == net.name
                        and all((gx, gy, ll) not in copper
                                and (gx, gy, ll) not in halo
                                for ll in range(nl))):
                    soft.discard((gx, gy))
                    for ll in range(nl):
                        blocked.discard((gx, gy, ll))
    own: set[tuple[int, int, int]] = set()
    for (_, a), (_, b) in legs:
        s = (min(nx - 1, max(0, int(a[0] / grid))),
             min(ny - 1, max(0, int(a[1] / grid))), layer)
        g = (min(nx - 1, max(0, int(b[0] / grid))),
             min(ny - 1, max(0, int(b[1] / grid))))
        path = _astar(s, g, blocked, soft, own, nx, ny, board.layers, bend, via, novia)
        if path is None:
            return False
        new.extend(_path_segs(board, net.name, path, grid, net.width))
        own.update(path)
    cells_of[net.name] = set(own)
    for (gx, gy, ll) in own:
        copper.add((gx, gy, ll))
    for (gx, gy, ll) in own:
        for hx in (gx - 1, gx, gx + 1):
            for hy in (gy - 1, gy, gy + 1):
                # same-layer copper suppresses halo (blocked anyway);
                # other-layer copper must NOT suppress it (FR4 isolates,
                # spacing still needed on this layer).
                if (hx, hy, ll) not in copper:
                    halo.add((hx, hy, ll))
    if frames is not None:
        frames.append({"net": net.name, "layer": layer,
                       "segs": [(s.x1, s.y1, s.x2, s.y2) for s in new
                                if s.net == net.name]})
    return True


def _fallback(board: Board, net: Net, pts: list[tuple[str, XY]], new: list[Seg]) -> None:
    """Straight-L fallback (never fail a build). Flagged jumper for DRC."""
    from .circuit import Seg as S
    layer = net.layer if net.layer is not None else 0
    hub = pts[0][1]
    for _, pt in pts[1:]:
        mid: XY = (pt[0], hub[1]) if abs(pt[0] - hub[0]) > abs(pt[1] - hub[1]) else (hub[0], pt[1])
        for aa, bb in ((hub, mid), (mid, pt)):
            if aa != bb:
                j = S(net.name, aa[0], aa[1], bb[0], bb[1], layer, net.width)
                j.jumper = True  # type: ignore[attr-defined]
                new.append(j)


def _rebuild_blocked(copper: set[tuple[int, int, int]],
                     halo: set[tuple[int, int, int]],
                     cells_of: dict[str, set[tuple[int, int, int]]]) -> None:
    """After ripping a net, rebuild copper+halo from surviving cells."""
    copper.clear()
    halo.clear()
    for cells in cells_of.values():
        for (gx, gy, ll) in cells:
            copper.add((gx, gy, ll))
    for cells in cells_of.values():
        for (gx, gy, ll) in cells:
            for hx in (gx - 1, gx, gx + 1):
                for hy in (gy - 1, gy, gy + 1):
                    if (hx, hy, ll) not in copper:
                        halo.add((hx, hy, ll))


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
