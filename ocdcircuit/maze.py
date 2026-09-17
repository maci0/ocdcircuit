"""Maze (A*) router: grid wavefront with obstacle avoidance + vias.

Grid 0.25mm (route-grid constraint overrides; coarse router uses 2mm);
blocked cells = part courtyards (+gap) + foreign-net copper.
Cost: step + bend penalty + layer-change (via) penalty. Multi-pin nets route
pin-to-pin (chain), reusing own-net copper as free terrain. One undoable
effect; streams frames like the L-router.
"""
from __future__ import annotations
from .util import as_float as _f
import heapq
from typing import TYPE_CHECKING, cast

from .circuit import Net, Seg
from .types import Frame, XY


if TYPE_CHECKING:
    from .circuit import Board

GRID = 0.25
BEND = 1.5
VIA_COST = 8.0


def _constraints(board: Board) -> dict[str, float]:
    import math
    grid, bend, via = GRID, BEND, VIA_COST
    for c in board.constraints:
        if c.get("t") == "route-grid":
            g = float(cast(float, c.get("grid", GRID)))
            # file parser accepts junk for lint; zero/NaN would ZeroDivision
            # every cell index below — fall back to the default grid.
            if math.isfinite(g) and g > 0:
                grid = g
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
           hist: dict[tuple[int, int, int], float] | None = None,
           ) -> list[tuple[int, int, int]] | None:
    """(gx, gy, layer) search. own-net cells are free (copper reuse).
    soft (part courtyard) cells passable at +SOFT per cell — escapes work,
    open field preferred. blocked is per-layer: copper on L0 never walls
    L1 (FR4 between); PTH pads/vias arrive expanded on every layer.
    hist: negotiated-congestion history — cells used by ripped/failed
    routes cost +HIST each, steering retries around past congestion."""
    SOFT = 15.0
    HIST = 2.0
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
                    st += SOFT
                ng = g + st + (via if l2 != ll else 0.0)
                key = (nx2, ny2, l2)
                if hist and key not in own:
                    ng += HIST * hist.get(key, 0.0)
                if ng < best.get(key, INF):
                    best[key] = ng
                    prev[key] = node
                    heapq.heappush(openh, (ng + h(nx2, ny2), ng, key, dd))
    return None


def _net_span(board: Board, net: Net) -> float:
    """BBox diagonal of a net's pads (big nets route later)."""
    pts = [board.pad_pos(r, q) for r, q in net.pins if r in board.parts]
    if len(pts) < 2:
        return 0.0
    dx: float = max(p[0] for p in pts) - min(p[0] for p in pts)
    dy: float = max(p[1] for p in pts) - min(p[1] for p in pts)
    return float((dx * dx + dy * dy) ** 0.5)


def _novia_cells(board: Board, grid: float) -> set[tuple[int, int]]:
    """Dynamic bend rects: traces pass, layer jumps forbidden. Static for a
    maze() pass — hoist once rather than rebuild per net."""
    novia: set[tuple[int, int]] = set()
    for c in board.constraints:
        if c.get("t") == "bend" and c.get("dynamic", True):
            cx, cy = _f(c["x"]), _f(c["y"])
            hw, hh = _f(c["w"]) / 2, _f(c["h"]) / 2
            for gx in range(int((cx - hw) / grid), int((cx + hw) / grid) + 1):
                for gy in range(int((cy - hh) / grid), int((cy + hh) / grid) + 1):
                    novia.add((gx, gy))
    return novia


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
    lib = board._lib()
    for ref in board.parts:
        zones.extend(fp_keepouts(board, ref, lib))
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
    # Expand pads onto every layer once; per-net route subtracts own pads
    # instead of walking pad_cells × layers for every net.
    nl = board.layers
    all_pads_3d: set[tuple[int, int, int]] = set()
    own_pads_3d: dict[str, set[tuple[int, int, int]]] = {}
    for cell, owner in pad_cells.items():
        for ll in range(nl):
            t = (cell[0], cell[1], ll)
            all_pads_3d.add(t)
            own_pads_3d.setdefault(owner, set()).add(t)
    novia = _novia_cells(board, grid)
    old = list(board.traces)
    new: list[Seg] = []
    copper: set[tuple[int, int, int]] = set()  # per-layer routed cells
    halo: set[tuple[int, int, int]] = set()  # per-layer 1-ring spacing
    cells_of: dict[str, set[tuple[int, int, int]]] = {}

    # Big nets first: multi-pin power busses claim trunks while the board
    # is open; small point-to-point wires thread the gaps after. Small-first
    # walls big nets off (breath_ketone: 190 → 46 jumpers). Within a size
    # class, wide spans still go last (short paths grab direct routes).
    spans = {n.name: _net_span(board, n) for n in board.nets.values()}
    order = sorted(board.nets.values(),
                   key=lambda n: (len(n.pins), -spans[n.name]),
                   reverse=True)
    from .drc import pour_layers
    poured = {n: set(ls) for n, ls in pour_layers(board).items()}
    failed: list[str] = []
    # negotiated-congestion history: cells used by ripped/failed routes
    # cost extra on retries, steering around past congestion (doc §order-a).
    hist: dict[tuple[int, int, int], float] = {}
    for net in order:
        if net.layer is not None and net.layer in poured.get(net.name, ()):
            continue  # plane covers this layer — nothing to route
        if not _route_one(board, net, grid, bend, via, nx, ny, base_blocked,
                          pad_cells, copper, halo, cells_of, new, frames, hist,
                          novia, all_pads_3d, own_pads_3d):
            failed.append(net.name)
            for hcell in cells_of.get(net.name, ()):
                hist[hcell] = hist.get(hcell, 0.0) + 1.0
    # rip-up retry: victim = blocker with most cells inside the failed net's
    # corridor (pads bbox grown 4mm), not nearest endpoints — big blockers
    # wall off whole regions. A 2nd round runs only if the 1st strictly
    # shrank the failed set (on jumper-structural boards like 1L blinky,
    # extra churn converts routed nets to jumpers).
    n_failed = len(failed)
    for _round in range(2):
        if not failed:
            break
        if _round == 1 and len(failed) >= n_failed:
            for fname in failed:
                fnet = board.nets[fname]
                fpts = [(r, board.pad_pos(r, q)) for r, q in fnet.pins if r in board.parts]
                if len(fpts) >= 2:
                    _fallback(fnet, fpts, new)
            break
        still: list[str] = []
        for fname in failed:
            fnet = board.nets[fname]
            fpts = [(r, board.pad_pos(r, q)) for r, q in fnet.pins if r in board.parts]
            if len(fpts) < 2:
                continue
            xs = [p[0] for _, p in fpts]
            ys = [p[1] for _, p in fpts]
            x0, x1 = min(xs) - 4.0, max(xs) + 4.0
            y0, y1 = min(ys) - 4.0, max(ys) + 4.0
            best, best_hit = "", -1
            for oname, cells in cells_of.items():
                if oname == fname:
                    continue
                hit = sum(1 for (gx, gy, _ll) in cells
                          if x0 <= gx * grid <= x1 and y0 <= gy * grid <= y1)
                if hit > best_hit:
                    best, best_hit = oname, hit
            if best_hit <= 0:
                if _round == 1:
                    _fallback(fnet, fpts, new)
                else:
                    still.append(fname)
                continue
            # shove first: nudge the blocker's in-corridor segs aside
            # and retry. Cheaper than ripping the whole net — and when it
            # works the victim keeps its (moved) copper.
            if _shove(board, best, x0, x1, y0, y1, grid, copper, halo,
                      base_blocked, cells_of, new) and _route_one(
                      board, fnet, grid, bend, via, nx, ny, base_blocked,
                      pad_cells, copper, halo, cells_of, new, frames, hist,
                      novia, all_pads_3d, own_pads_3d):
                continue
            ripped = [s for s in new if s.net == best]
            new[:] = [s for s in new if s.net != best]
            victim_cells = set(cells_of.get(best, ()))
            del cells_of[best]
            _rebuild_blocked(copper, halo, cells_of)
            if _route_one(board, fnet, grid, bend, via, nx, ny, base_blocked,
                          pad_cells, copper, halo, cells_of, new, frames, hist,
                          novia, all_pads_3d, own_pads_3d):
                bnet = board.nets[best]
                bpts = [(r, board.pad_pos(r, q)) for r, q in bnet.pins if r in board.parts]
                if len(bpts) >= 2 and not _route_one(
                        board, bnet, grid, bend, via, nx, ny, base_blocked,
                        pad_cells, copper, halo, cells_of, new, frames, hist,
                        novia, all_pads_3d, own_pads_3d):
                    if _round == 1:
                        _fallback(bnet, bpts, new)
                    else:
                        still.append(best)
            else:
                for _cell in victim_cells:  # victim's cells congested this retry
                    hist[_cell] = hist.get(_cell, 0.0) + 1.0
                if _round == 1:
                    _fallback(fnet, fpts, new)
                    for s in ripped:  # restore ripped net as flagged fallback
                        j = Seg(s.net, s.x1, s.y1, s.x2, s.y2, s.layer, s.width)
                        j.jumper = True
                        new.append(j)
                else:
                    new.extend(ripped)  # victim back untouched, retry later
                    _rebuild_blocked(copper, halo, cells_of)
                    still.append(fname)
        failed = still
    _meander(board, new, grid, copper)
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
               new: list[Seg], frames: list[Frame] | None,
               hist: dict[tuple[int, int, int], float] | None = None,
               novia: set[tuple[int, int]] | None = None,
               all_pads_3d: set[tuple[int, int, int]] | None = None,
               own_pads_3d: dict[str, set[tuple[int, int, int]]] | None = None,
               ) -> bool:
    """Route one net with current blockage. Returns True if maze-succeeded.
    copper/halo are per-layer (FR4 isolates); pads expand onto all layers.
    Legs follow a rectilinear MST over pads (research §5), not pin order —
    one shared trunk instead of N-1 competing maze paths."""
    pts = [(r, board.pad_pos(r, q)) for r, q in net.pins if r in board.parts]
    if len(pts) < 2:
        return True
    legs = _mst_pairs(pts)
    layer = net.layer if net.layer is not None else 0
    if novia is None:
        novia = _novia_cells(board, grid)
    nl = board.layers
    if all_pads_3d is None or own_pads_3d is None:
        blocked = set(copper) | halo
        for cell, owner in pad_cells.items():
            if owner != net.name:
                for ll in range(nl):
                    blocked.add((cell[0], cell[1], ll))
    else:
        # Foreign pads only — do not subtract own pads from copper|halo
        # (a pad cell can still be blocked by another net's copper/halo).
        foreign = all_pads_3d - own_pads_3d.get(net.name, set())
        blocked = copper | halo | foreign
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
    before = len(new)
    for (_, a), (_, b) in legs:
        s = (min(nx - 1, max(0, int(a[0] / grid))),
             min(ny - 1, max(0, int(a[1] / grid))), layer)
        g = (min(nx - 1, max(0, int(b[0] / grid))),
             min(ny - 1, max(0, int(b[1] / grid))))
        path = _astar(s, g, blocked, soft, own, nx, ny, board.layers, bend, via, novia, hist)
        if path is None:
            return False
        new.extend(_path_segs(net.name, path, grid, net.width))
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
                       "segs": [(s.x1, s.y1, s.x2, s.y2)
                                for s in new[before:]]})
    return True


def reroute(board: Board, name: str) -> bool:
    """Rip one net and re-route it against live copper (other nets' segs
    rasterized to blockage). One undoable effect. Returns maze-success.
    The interactive primitive: retry a failed net without full-board churn."""
    net = board.nets.get(name)
    if net is None:
        raise ValueError(f"unknown net {name!r}")
    P = _constraints(board)
    grid, bend, via = P["grid"], P["bend"], P["via"]
    nx = max(1, int(board.width / grid) + 1)
    ny = max(1, int(board.height / grid) + 1)
    old = list(board.traces)
    # rasterize surviving nets' segs to cells
    cells_of: dict[str, set[tuple[int, int, int]]] = {}
    for s in old:
        if s.net == name:
            continue
        cells = cells_of.setdefault(s.net, set())
        x0, x1 = sorted((s.x1, s.x2))
        y0, y1 = sorted((s.y1, s.y2))
        for gx in range(int(x0 / grid), int(x1 / grid) + 1):
            for gy in range(int(y0 / grid), int(y1 / grid) + 1):
                cells.add((gx, gy, s.layer))
    copper: set[tuple[int, int, int]] = set()
    halo: set[tuple[int, int, int]] = set()
    _rebuild_blocked(copper, halo, cells_of)
    base_blocked = _blocked(board, grid)
    from .parts import pads_of
    lib = board._lib()
    pad_cells: dict[tuple[int, int], str] = {}
    for n, nt in board.nets.items():
        for ref, pin in nt.pins:
            if ref in board.parts and pin in pads_of(board.parts[ref].fp, lib):
                px, py = board.pad_pos(ref, pin)
                for gx in (int(px / grid) - 1, int(px / grid), int(px / grid) + 1):
                    for gy in (int(py / grid) - 1, int(py / grid), int(py / grid) + 1):
                        pad_cells.setdefault((gx, gy), n)
    new = [s for s in old if s.net != name]
    novia = _novia_cells(board, grid)
    ok = _route_one(board, net, grid, bend, via, nx, ny, base_blocked,
                    pad_cells, copper, halo, cells_of, new, None, {},
                    novia)
    if not ok:
        # shove pass: nudge each blocker's in-corridor segs aside, retry.
        # Then one rip-up of the biggest blocker. Then flagged jumper.
        pts = [(r, board.pad_pos(r, q)) for r, q in net.pins if r in board.parts]
        if len(pts) >= 2:
            xs = [p[0] for _, p in pts]
            ys = [p[1] for _, p in pts]
            x0, x1 = min(xs) - 4.0, max(xs) + 4.0
            y0, y1 = min(ys) - 4.0, max(ys) + 4.0
            for oname in sorted(cells_of,
                                key=lambda o: -sum(
                                    1 for (gx, gy, _ll) in cells_of[o]
                                    if x0 <= gx * grid <= x1
                                    and y0 <= gy * grid <= y1)):
                if oname == name:
                    continue
                if _shove(board, oname, x0, x1, y0, y1, grid, copper,
                          halo, base_blocked, cells_of, new) and _route_one(
                          board, net, grid, bend, via, nx, ny,
                          base_blocked, pad_cells, copper, halo, cells_of,
                          new, None, {}, novia):
                    ok = True
                    break
        if not ok:
            # rip the biggest surviving blocker once, then retry
            pre_rip = list(new)
            pre_cells = {k: set(v) for k, v in cells_of.items()}
            best, best_n = "", -1
            for oname, cells in cells_of.items():
                if oname != name and len(cells) > best_n:
                    best, best_n = oname, len(cells)
            if best:
                new[:] = [s for s in new if s.net != best]
                del cells_of[best]
                _rebuild_blocked(copper, halo, cells_of)
                ok = _route_one(board, net, grid, bend, via, nx, ny,
                                base_blocked, pad_cells, copper, halo,
                                cells_of, new, None, {}, novia)
                if not ok:
                    new[:] = pre_rip
                    cells_of.clear()
                    cells_of.update(pre_cells)
                    _rebuild_blocked(copper, halo, cells_of)
            if not ok and len(pts) >= 2:
                _fallback(net, pts, new)
    kept = list(new)
    board.emit(lambda: board.traces.__setitem__(slice(None), kept),
               lambda: board.traces.__setitem__(slice(None), old))
    return ok


def _fallback(net: Net, pts: list[tuple[str, XY]], new: list[Seg]) -> None:
    """Straight-L fallback (never fail a build). Flagged jumper for DRC."""
    from .circuit import Seg as S
    layer = net.layer if net.layer is not None else 0
    hub = pts[0][1]
    for _, pt in pts[1:]:
        mid: XY = (pt[0], hub[1]) if abs(pt[0] - hub[0]) > abs(pt[1] - hub[1]) else (hub[0], pt[1])
        for aa, bb in ((hub, mid), (mid, pt)):
            if aa != bb:
                j = S(net.name, aa[0], aa[1], bb[0], bb[1], layer, net.width)
                j.jumper = True
                new.append(j)


def _shove(board: Board, victim: str,
           x0: float, x1: float, y0: float, y1: float,
           grid: float, copper: set[tuple[int, int, int]],
           halo: set[tuple[int, int, int]],
           base_blocked: set[tuple[int, int]],
           cells_of: dict[str, set[tuple[int, int, int]]],
           new: list[Seg]) -> bool:
    """Push-and-shove lite: shift the victim's in-corridor segs ±1 cell
    perpendicular instead of ripping the whole net. Returns True when at
    least one seg moved into free cells (not copper/halo/courtyard).
    The failed net retries against the shoved geometry; rip-up stays
    the fallback when nothing can move."""
    from .circuit import Seg as S
    moved = False
    for s in list(new):
        if s.net != victim:
            continue
        mx, my = (s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2
        if not (x0 <= mx <= x1 and y0 <= my <= y1):
            continue
        horiz = abs(s.x2 - s.x1) >= abs(s.y2 - s.y1)
        for sign in (1.0, -1.0):
            dx, dy = (0.0, sign * grid) if horiz else (sign * grid, 0.0)
            cells: set[tuple[int, int, int]] = set()
            x1, x2 = sorted((s.x1 + dx, s.x2 + dx))
            y1, y2 = sorted((s.y1 + dy, s.y2 + dy))
            for gx in range(int(x1 / grid), int(x2 / grid) + 1):
                for gy in range(int(y1 / grid), int(y2 / grid) + 1):
                    cells.add((gx, gy, s.layer))
            if not cells:
                continue
            if any(c in copper or c in halo or (c[0], c[1]) in base_blocked
                   for c in cells):
                continue
            # move it: replace seg, refresh victim cells
            new.remove(s)
            ns = S(s.net, s.x1 + dx, s.y1 + dy, s.x2 + dx, s.y2 + dy,
                   s.layer, s.width)
            new.append(ns)
            vcells = cells_of.get(victim, set())
            vcells = {c for c in vcells
                      if not (x0 <= c[0] * grid <= x1 and y0 <= c[1] * grid <= y1)}
            vcells |= cells
            cells_of[victim] = vcells
            moved = True
            break
    if moved:
        _rebuild_blocked(copper, halo, cells_of)
    return moved


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


def _path_segs(net: str, path: list[tuple[int, int, int]],
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
                v.via = True
                out.append(v)
            ax, ay, al = bx, by, ll
            dx, dy = gx - bx, gy - by
        bx, by, bl = gx, gy, ll
    if (ax, ay) != (bx, by):
        out.append(S(net, ax * grid, ay * grid, bx * grid, by * grid, bl, width))
    return out


def _meander(board: Board, new: list[Seg], grid: float,
             copper: set[tuple[int, int, int]]) -> None:
    """Skew-driven length match: for each `match` group, grow shorter nets
    toward the longest with rectangular bumps (up to 6 per net, ≤2mm each)
    on the longest straight run. A bump is skipped when it leaves the
    board or hits copper; DRC still reports residual skew — this narrows
    it, not zeroes it.
    # ponytail: match groups only (diff pairs need coupled bumps that
    # hold the gap — build when a diff board needs it)."""
    from typing import cast
    from .circuit import Seg as S

    by_net: dict[str, list[S]] = {}
    for s in new:
        by_net.setdefault(s.net, []).append(s)

    def _len(nm: str) -> float:
        return sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1)
                   for s in by_net.get(nm, ()))

    def _bump(nm: str, short: float) -> bool:
        runs = sorted(
            (s for s in by_net.get(nm, ()) if (s.x1 == s.x2 or s.y1 == s.y2)),
            key=lambda s: abs(s.x2 - s.x1) + abs(s.y2 - s.y1), reverse=True)
        if not runs:
            return False
        s = runs[0]
        horiz = s.y1 == s.y2
        h = min(short / 2.0, 2.0)
        for _try in range(2):
            nh = h if _try == 0 else h / 2.0
            if nh < grid:
                return False
            if horiz:
                ny = s.y1 + nh
                if not (0 <= ny <= board.height):
                    continue
                cells = [(int((s.x1 + s.x2) / 2 / grid), int(ny / grid), s.layer)]
            else:
                nx = s.x1 + nh
                if not (0 <= nx <= board.width):
                    continue
                cells = [(int(nx / grid), int((s.y1 + s.y2) / 2 / grid), s.layer)]
            if any(cc in copper for cc in cells):
                continue
            new.remove(s)
            by_net[nm].remove(s)
            if horiz:
                added = [
                    S(nm, s.x1, s.y1, s.x1, ny, s.layer, s.width),
                    S(nm, s.x1, ny, s.x2, ny, s.layer, s.width),
                    S(nm, s.x2, ny, s.x2, s.y2, s.layer, s.width),
                ]
            else:
                added = [
                    S(nm, s.x1, s.y1, nx, s.y1, s.layer, s.width),
                    S(nm, nx, s.y1, nx, s.y2, s.layer, s.width),
                    S(nm, nx, s.y2, s.x2, s.y2, s.layer, s.width),
                ]
            new.extend(added)
            by_net[nm].extend(added)
            return True
        return False

    for c in board.constraints:
        if not isinstance(c, dict) or c.get("t") != "match":
            continue
        nets = [n for n in cast(list[str], c.get("nets", []))
                if isinstance(n, str) and n in board.nets]
        if len(nets) < 2:
            continue
        for _round in range(6):
            target = max(_len(n) for n in nets)
            grew = False
            for nm in nets:
                if target - _len(nm) >= 1.0 and _bump(nm, target - _len(nm)):
                    grew = True
            if not grew:
                break
