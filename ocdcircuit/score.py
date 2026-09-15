"""tidy(board): OCD-compatible layout scorecard (docs/tidy-metrics.md).

Report-only: component vector + coverage, never a bare cross-board scalar.
Every metric returns 0..1 (higher = tidier), RAW (physical units), or None
(undefined input — aggregators skip it). Stdlib only.
"""
from __future__ import annotations
import math
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board, Seg

EPS = 0.1  # T7 alignment tolerance, mm (placeholder per doc — uncalibrated)

from .drc import _grid_pairs  # shared spatial hash (lives with _seg_dist)


def _routed(board: Board) -> bool:
    return any(not s.jumper for s in board.traces)


def _t1_crossings(board: Board) -> int | None:
    """Same-layer foreign-net crossings, RAW count. Vias exempt (own layer)."""
    if not _routed(board):
        return None
    from .drc import _seg_dist
    segs = [s for s in board.traces if not s.jumper]
    n = 0
    by_layer: dict[int, list[int]] = {}
    for i, s in enumerate(segs):
        by_layer.setdefault(s.layer, []).append(i)
    for members in by_layer.values():
        boxes = [(min(segs[i].x1, segs[i].x2), min(segs[i].y1, segs[i].y2),
                  max(segs[i].x1, segs[i].x2), max(segs[i].y1, segs[i].y2))
                 for i in members]
        for a, b in _grid_pairs(boxes, 5.0):
            A, B = segs[members[a]], segs[members[b]]
            if A.net == B.net:
                continue
            d = _seg_dist((A.x1, A.y1, A.x2, A.y2), (B.x1, B.y1, B.x2, B.y2))
            if d < 1e-9 and _cross(A, B):
                n += 1
    return n


def _cross(a: Seg, b: Seg) -> bool:
    """Proper segment intersection (touching at shared endpoints excluded)."""
    ax1, ay1, ax2, ay2 = a.x1, a.y1, a.x2, a.y2
    bx1, by1, bx2, by2 = b.x1, b.y1, b.x2, b.y2
    if len({(ax1, ay1), (ax2, ay2), (bx1, by1), (bx2, by2)}) < 4:
        return False

    def side(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
        return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)

    s1, s2 = side(bx1, by1, ax1, ay1, ax2, ay2), side(bx2, by2, ax1, ay1, ax2, ay2)
    s3, s4 = side(ax1, ay1, bx1, by1, bx2, by2), side(ax2, ay2, bx1, by1, bx2, by2)
    return s1 * s2 < 0 and s3 * s4 < 0


def _t2_bends(board: Board) -> float | None:
    """Bends per mm, RAW. Zero-length via segs excluded."""
    if not _routed(board):
        return None
    segs = [s for s in board.traces
            if not s.jumper and (s.x1, s.y1) != (s.x2, s.y2)]
    if not segs:
        return None
    bends = 0
    for net in {s.net for s in segs}:
        run = [s for s in segs if s.net == net]
        for p, q in zip(run, run[1:]):
            d1 = (p.x2 - p.x1, p.y2 - p.y1)
            d2 = (q.x2 - q.x1, q.y2 - q.y1)
            if (d1[0] == 0) != (d2[0] == 0) or (d1[1] == 0) != (d2[1] == 0):
                if d1 != (0, 0) and d2 != (0, 0):
                    bends += 1
    length = sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in segs)
    return bends / length if length > 0 else None


def _t3_ortho(board: Board) -> float | None:
    """Axis-aligned length / total. Regression tripwire: routers emit
    Manhattan by construction, so <1.0 means something leaked in."""
    if not _routed(board):
        return None
    segs = [s for s in board.traces
            if not s.jumper and (s.x1, s.y1) != (s.x2, s.y2)]
    if not segs:
        return None
    tot = sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in segs)
    ax = sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in segs
             if s.x1 == s.x2 or s.y1 == s.y2)
    return ax / tot if tot > 0 else None


def _t4_vias(board: Board) -> dict[str, object] | None:
    """Via discipline, RAW: per-net via counts + board total. Maze-only
    (L-router emits no vias)."""
    if not _routed(board):
        return None
    per: dict[str, int] = {}
    for s in board.traces:
        if s.via:
            per[s.net] = per.get(s.net, 0) + 1
    if not per:
        return {"total": 0, "per_net": {}}
    return {"total": sum(per.values()), "per_net": per}


def _t5_headroom(board: Board) -> float | None:
    """Clearance headroom: min(actual/min_space) over foreign pairs.
    Single global min_space — no net-class split (future work)."""
    if not _routed(board):
        return None
    from .fab import get
    from .drc import _seg_dist
    P = get(board.fab or "jlc")
    ms = float(P["min_space"])  # type: ignore[arg-type]
    segs = [s for s in board.traces if not s.jumper]
    best = float("inf")
    by_layer: dict[int, list[int]] = {}
    for i, s in enumerate(segs):
        by_layer.setdefault(s.layer, []).append(i)
    for members in by_layer.values():
        boxes = [(min(segs[i].x1, segs[i].x2), min(segs[i].y1, segs[i].y2),
                  max(segs[i].x1, segs[i].x2), max(segs[i].y1, segs[i].y2))
                 for i in members]
        for a, b in _grid_pairs(boxes, 5.0):
            A, B = segs[members[a]], segs[members[b]]
            if A.net == B.net:
                continue
            d = _seg_dist((A.x1, A.y1, A.x2, A.y2), (B.x1, B.y1, B.x2, B.y2))
            if d < best:
                best = d
    return best / ms if best != float("inf") else None


def _t6_skew(board: Board) -> dict[str, object]:
    """Length skew RAW mm per match group + diff gap info. Uses _net_length
    (routed length, else Manhattan pad estimate — flagged via 'estimated')."""
    from .solver import _net_length
    out: dict[str, object] = {}
    from typing import cast
    for c in board.constraints:
        t = c.get("t")
        if t == "match":
            nets = [n for n in cast(list[str], c.get("nets", [])) if n in board.nets]
            if len(nets) >= 2:
                lens = [_net_length(board, str(n)) for n in nets]
                est = not any(s.net in nets for s in board.traces)
                out[f"match:{'+'.join(str(n) for n in nets)}"] = {
                    "skew_mm": round(max(lens) - min(lens), 3), "estimated": est}
        elif t == "diff":
            p, n = str(c.get("p")), str(c.get("n"))
            if p in board.nets and n in board.nets:
                est = not any(s.net in (p, n) for s in board.traces)
                out[f"diff:{p}/{n}"] = {
                    "skew_mm": round(abs(_net_length(board, p) - _net_length(board, n)), 3),
                    "estimated": est}
    return out


def _t7_align(board: Board) -> float | None:
    """Shared-x/y fraction @ EPS. <2 parts → None."""
    parts = list(board.parts.values())
    if len(parts) < 2:
        return None
    hit = sum(1 for i, p in enumerate(parts)
              if any(abs(p.x - q.x) < EPS or abs(p.y - q.y) < EPS
                     for j, q in enumerate(parts) if j != i))
    return hit / len(parts)


def _t8_gridsnap(board: Board) -> float | None:
    """Mean residual to the board's route-grid multiple (RAW mm)."""
    from .maze import GRID
    grid = GRID
    for c in board.constraints:
        if c.get("t") == "route-grid":
            grid = float(c.get("grid", GRID))  # type: ignore[arg-type]
    parts = list(board.parts.values())
    if not parts:
        return None
    res = sum(min(p.x % grid, grid - p.x % grid) + min(p.y % grid, grid - p.y % grid)
              for p in parts) / len(parts)
    return round(res, 4)


def _t9_spacing(board: Board) -> float | None:
    """1 − CV of nearest-neighbor gaps. None if <2 parts or mean gap 0."""
    parts = list(board.parts.values())
    if len(parts) < 2:
        return None
    gaps: list[float] = []
    for i, p in enumerate(parts):
        d = min(float(((p.x - q.x) ** 2 + (p.y - q.y) ** 2) ** 0.5)
                for j, q in enumerate(parts) if j != i)
        gaps.append(d)
    mean = sum(gaps) / len(gaps)
    if mean == 0:
        return None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return float(max(0.0, 1 - (var ** 0.5) / mean))


def _t10_orient(board: Board) -> dict[str, object]:
    """0/90/180/270 fraction + entropy over p.rot."""
    import math
    rots = [p.rot for p in board.parts.values()]
    if not rots:
        return {"cardinal": None, "entropy": None}
    card = sum(r in (0, 90, 180, 270) for r in rots) / len(rots)
    ent = 0.0
    for r in set(rots):
        f = rots.count(r) / len(rots)
        ent -= f * math.log2(f)
    return {"cardinal": round(card, 3), "entropy": round(ent, 3)}


def _t14_silk(board: Board) -> dict[str, object] | None:
    """Silk overlap RAW counts (text–text, text–copper). Scored, never veto.
    Text extents from the SVG renderer's font metric (fs=4·S/10 at S=10 →
    4px/mm units, ~0.6 aspect): box = len·2.4 × 4.0 board-mm centered."""
    from .silk import labels
    texts = labels(board).texts
    if not texts:
        return None
    # silk text ~1.0mm tall (AtlasPCB rule), ~0.6 aspect, centered on Text.xy
    boxes = [(t.x - len(t.s) * 0.3, t.y - 0.5, t.x + len(t.s) * 0.3, t.y + 0.5)
             for t in texts]
    tt = sum(1 for i, j in _grid_pairs(boxes, 5.0) if _ov(boxes[i], boxes[j]))
    lib = board._lib()
    from .parts import pads_of
    copper = []
    for p in board.parts.values():
        for dx, dy in pads_of(p.fp, lib).values():
            copper.append((p.x + dx, p.y + dy))
    copper += [(s.x1, s.y1) for s in board.traces] + [(s.x2, s.y2) for s in board.traces]
    tc = 0
    if copper:
        xs = [c[0] for c in copper]
        cell = (max(xs) - min(xs)) / max(1, int(len(copper) ** 0.5)) + 1e-9
        grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for cx, cy in copper:
            grid.setdefault((int(cx // cell), int(cy // cell)), []).append((cx, cy))
        for b in boxes:
            for gx in range(int(b[0] // cell), int(b[2] // cell) + 1):
                for gy in range(int(b[1] // cell), int(b[3] // cell) + 1):
                    for cx, cy in grid.get((gx, gy), []):
                        if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                            tc += 1
    return {"text_text": tt, "text_copper": tc}


def _ov(a: tuple[float, float, float, float],
        b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _t12_acid_traps(board: Board) -> int | None:
    """RAW # acute (<90°) copper wedges at trace joins. Joins are read as
    outgoing centerline pairs from the joint: a pair <90° apart subtends
    a <90° inner copper wedge (etchant trap). Manhattan routing makes
    only 0°/90°/180° pairs, so nonzero means non-Manhattan geometry
    leaked in. None if unrouted."""
    if not _routed(board):
        return None
    import math
    segs = [s for s in board.traces
            if not s.jumper and (s.x1, s.y1) != (s.x2, s.y2)]
    if not segs:
        return None
    at: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for s in segs:
        for px, py, qx, qy in ((s.x1, s.y1, s.x2, s.y2), (s.x2, s.y2, s.x1, s.y1)):
            at.setdefault((px, py), []).append((qx - px, qy - py))
    n = 0
    for dirs in at.values():
        for i in range(len(dirs)):
            for j in range(i + 1, len(dirs)):
                (ax, ay), (bx, by) = dirs[i], dirs[j]
                la, lb = math.hypot(ax, ay), math.hypot(bx, by)
                if la == 0 or lb == 0:
                    continue
                cos = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
                if math.degrees(math.acos(cos)) < 90.0 - 1e-9:
                    n += 1
    return n


def _t11_copper_balance(board: Board) -> dict[str, object] | None:
    """Copper tile density sigma + layer delta (RAW). Trace length per
    5mm tile; None if unrouted. JLC warpage wants layer delta <= 20%."""
    segs = [s for s in board.traces if not s.jumper]
    if not segs:
        return None
    tile = 5.0
    tiles: dict[tuple[int, int, int], float] = {}
    for s in segs:
        length = abs(s.x2 - s.x1) + abs(s.y2 - s.y1)
        steps = max(1, int(length / tile) + 1)
        for i in range(steps):
            t = (i + 0.5) / steps
            key = (int((s.x1 + (s.x2 - s.x1) * t) / tile),
                   int((s.y1 + (s.y2 - s.y1) * t) / tile), s.layer)
            tiles[key] = tiles.get(key, 0.0) + length / steps
    vals = list(tiles.values())
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    by_layer: dict[int, float] = {}
    for (_, _, ll), v in tiles.items():
        by_layer[ll] = by_layer.get(ll, 0.0) + v
    tot = sum(by_layer.values()) or 1.0
    frac = sorted(v / tot for v in by_layer.values())
    delta = frac[-1] - frac[0] if len(frac) > 1 else 0.0
    return {"tile_sigma": round(var ** 0.5, 3), "layer_delta": round(delta, 3)}


def _t15_silk_consistency(board: Board) -> float | None:
    """Modal ref-label offset direction %. None if no labels."""
    from .silk import labels
    texts = [t for t in labels(board).texts if t.cls == "silk-ref"]
    if not texts:
        return None
    by_ref: dict[str, tuple[float, float]] = {}
    for t in texts:
        by_ref.setdefault(t.s, (t.x, t.y))
    dirs = []
    for t in texts:
        p = board.parts.get(t.s)
        if p is None:
            continue
        dx, dy = t.x - p.x, t.y - p.y
        dirs.append("N" if dy > 0 and abs(dy) >= abs(dx)
                    else "S" if dy < 0 and abs(dy) >= abs(dx)
                    else "E" if dx > 0 else "W")
    if not dirs:
        return None
    return max(dirs.count(d) for d in set(dirs)) / len(dirs)


def _t13_schematic(board: Board) -> dict[str, object] | None:
    """Schematic readability (RAW): rail crossings + jogs in sch_layout
    geometry. Drop-lines crossing foreign rails; jogs = rail direction
    changes per net (rails are straight by construction → always 0)."""
    from .plugins import sch_layout
    lay = sch_layout(board)
    px = lay["px"]
    rail_y = lay["rail_y"]
    assert isinstance(px, dict) and isinstance(rail_y, dict)
    crossings = 0
    from typing import cast
    top = float(cast(int, lay["top"])) - 4
    span: dict[str, tuple[float, float]] = {}
    yof: dict[str, float] = {}
    for m, y in rail_y.items():
        xs = [float(px[r]) for r, _ in board.nets[m].pins if r in px]
        if xs:
            span[m] = (min(xs), max(xs))
            yof[m] = float(y)
    for n, net in board.nets.items():
        if n not in rail_y:
            continue
        y0 = float(rail_y[n])
        lo0, hi0 = min(top, y0), max(top, y0)
        xs0 = [float(px[ref]) for ref, _ in net.pins if ref in px]
        for m, (lo, hi) in span.items():
            if m == n or not lo0 < yof[m] < hi0:
                continue
            for x0 in xs0:
                if lo <= x0 <= hi:
                    crossings += 1
    return {"crossings": crossings, "jogs": 0}


def tidy(board: Board) -> dict[str, object]:
    """Full scorecard: {metric: value|None} + coverage. No scalar."""
    nets = len(board.traces)
    m: dict[str, object] = {
        "T1_crossings": _t1_crossings(board),
        "T2_bends_per_mm": _t2_bends(board),
        "T3_orthogonality": _t3_ortho(board),
        "T4_vias": _t4_vias(board),
        "T5_headroom": _t5_headroom(board),
        "T6_skew": _t6_skew(board),
        "T7_alignment": _t7_align(board),
        "T8_gridsnap_mm": _t8_gridsnap(board),
        "T9_spacing": _t9_spacing(board),
        "T10_orientation": _t10_orient(board),
        # T12: acute-wedge scan (0 under Manhattan-only routing — tripwire)
        "T11_copper_balance": _t11_copper_balance(board),
        "T12_acid_traps": _t12_acid_traps(board),
        "T13_schematic": _t13_schematic(board),
        "T14_silk_overlap": _t14_silk(board),
        "T15_silk_consistency": _t15_silk_consistency(board),
        "routed_segs": nets,
    }
    defined = sum(1 for k, v in m.items()
                  if k != "routed_segs" and v is not None and v != {})
    m["coverage"] = f"{defined}/15"
    return m


def score(board: Board) -> dict[str, object]:
    """0-100 aggregate for CLI badges. Prefer tidy() components."""
    t = tidy(board)
    subs: dict[str, float] = {}
    from typing import cast
    ta = t["T7_alignment"]
    assert ta is None or isinstance(ta, float)
    subs["grid"] = round(ta * 100, 1) if ta is not None else 50.0
    to = cast(dict[str, object], t["T10_orientation"])
    card = to["cardinal"]
    assert card is None or isinstance(card, float)
    subs["orientation"] = round(card * 100, 1) if card is not None else 50.0
    parts = list(board.parts.values())
    bad = 0
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            aw, ah = a.wh()
            bw, bh = b.wh()
            if (abs(a.x - b.x) < (aw + bw) / 2 and
                    abs(a.y - b.y) < (ah + bh) / 2):
                bad += 1
    subs["spacing"] = round(max(0, 100 - 25 * bad), 1)
    out = sum(1 for p in parts
              if not (p.wh()[0] / 2 + 0.3 <= p.x <= board.width - p.wh()[0] / 2 - 0.3
                      and p.wh()[1] / 2 + 0.3 <= p.y <= board.height - p.wh()[1] / 2 - 0.3))
    subs["edge"] = round(max(0, 100 - 25 * out), 1)
    fill = sum(p.wh()[0] * p.wh()[1] for p in parts) / max(1, board.width * board.height)
    subs["compact"] = round(100 * min(fill / 0.15, (0.9 - fill) / 0.3) if fill < 0.9 else 0, 1)
    subs["compact"] = max(0, min(100, subs["compact"]))
    total = round(sum(subs.values()) / len(subs), 1)
    grade = "A" if total >= 90 else "B" if total >= 75 else "C" if total >= 60 else "D" if total >= 40 else "F"
    return {"total": total, "grade": grade, "parts": subs, "tidy": t,
            "extent": _extent(board)}


def _extent(board: Board) -> dict[str, object]:
    """Placed bounding box (courtyard extents) vs board size: bbox fill
    fraction + shrink suggestion. Empty board → None-ish zeros."""
    parts = list(board.parts.values())
    if not parts:
        return {"w": 0.0, "h": 0.0, "fill": 0.0, "shrink": [0.0, 0.0]}
    x0 = min(p.x - p.wh()[0] / 2 for p in parts)
    x1 = max(p.x + p.wh()[0] / 2 for p in parts)
    y0 = min(p.y - p.wh()[1] / 2 for p in parts)
    y1 = max(p.y + p.wh()[1] / 2 for p in parts)
    w, h = round(x1 - x0, 2), round(y1 - y0, 2)
    fill = round(w * h / max(1e-9, board.width * board.height), 3)
    from .fab import get as _fab_get
    edge = float(cast(float, _fab_get(board.fab).get("edge", 0.3)))
    shrink = [math.ceil((w + 2 * edge) * 2) / 2, math.ceil((h + 2 * edge) * 2) / 2]
    return {"w": w, "h": h, "fill": fill, "shrink": shrink}
