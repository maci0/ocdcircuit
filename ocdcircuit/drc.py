"""DRC against a fab profile (see fab.py). Errors block fab; clearance-only
hits are warnings (naive L-router, see ADR-0002)."""
from __future__ import annotations
from .util import as_float as _f
from typing import TYPE_CHECKING, cast
from .fab import DEFAULT, get


if TYPE_CHECKING:
    from .circuit import Board


def zone_at(board: Board, c: object) -> dict[str, object]:
    """Resolve a keepout/cutout center: explicit x/y, or live part position
    via `ref` (deadzone follows the part through place iterations)."""
    assert isinstance(c, dict)
    out = dict(c)
    if c.get("ref") is not None and str(c["ref"]) in board.parts:
        p = board.parts[str(c["ref"])]
        out["x"], out["y"] = p.x, p.y
    return out


def pour_layers(board: Board) -> dict[str, list[int]]:
    """{net: sorted layers} with `pour` constraints. One funnel for maze
    (skip poured-net legs), DRC (skip poured traces), Gerber/KiCad (plots)."""
    out: dict[str, list[int]] = {}
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "pour":
            n = str(c.get("net", ""))
            try:
                ll = int(cast(int, c.get("layer", 0)))
            except (TypeError, ValueError):
                continue
            if n in board.nets and 0 <= ll < board.layers:
                out.setdefault(n, [])
                if ll not in out[n]:
                    out[n].append(ll)
    for n in out:
        out[n].sort()
    return out


def in_zone(c: object, x: float, y: float,
            pad: float | tuple[float, float] = 0.0) -> bool:
    """Shape-aware zone hit: rect (`w/h`) or round (`d`) keepout/cutout.
    One predicate for maze walls, DRC warnings, export. `pad` grows the
    shape — scalar, or (px, py) pair for exact box-vs-box (part half-size,
    trace width/2, fiducial deadzone)."""
    assert isinstance(c, dict)
    cx, cy = _f(c["x"]), _f(c["y"])
    dx, dy = abs(x - cx), abs(y - cy)
    px, py = pad if isinstance(pad, tuple) else (pad, pad)
    if c.get("d") is not None:
        r: float = _f(c["d"]) / 2 + max(px, py)
        return dx * dx + dy * dy < r * r
    return dx < _f(c["w"]) / 2 + px and dy < _f(c["h"]) / 2 + py


def fp_keepouts(board: Board, ref: str) -> list[dict[str, object]]:
    """Footprint keepouts as live board-frame zones: footprint-frame
    (dx, dy, w/h or d) rotated into the part frame (rot-aware), centered
    on the part. Follows placement like `keepout near` — synthesized at
    consumption (maze/DRC/export), never materialized as constraints."""
    p = board.parts[ref]
    lib = board._lib()
    meta = lib.get(p.fp, {})
    out: list[dict[str, object]] = []
    ko = meta.get("keepouts")
    if not isinstance(ko, list):
        return out
    for z in ko:
        if not isinstance(z, dict):
            continue
        rx, ry = p.rot_xy(_f(z.get("dx", 0.0)), _f(z.get("dy", 0.0)))
        c: dict[str, object] = {"t": "keepout", "ref": ref,
                                "x": p.x + rx, "y": p.y + ry,
                                "layers": list(z.get("layers", []))}
        if z.get("d") is not None:
            c["d"] = _f(z["d"])
        else:
            w, h = _f(z.get("w", 0.0)), _f(z.get("h", 0.0))
            if p.rot in (90, 270):
                w, h = h, w
            c["w"], c["h"] = w, h
        out.append(c)
    return out


def _grid_pairs(bbox: list[tuple[float, float, float, float]],
                cell: float) -> list[tuple[int, int]]:
    """Index pairs sharing a grid cell (uniform spatial hash). Every pair
    whose boxes overlap shares ≥1 cell, so callers testing a box-overlap
    precondition lose nothing.
    # ponytail: O(n·k) not O(n²); k = items per cell. cell ≈ typical size."""
    # A few oversized boxes (a board outline, a big connector) span thousands
    # of cells each and meet in every one of them, so the naive cell-product
    # generated 30.2M candidate pairs to yield 73k unique ones on virgo —
    # 413x waste, and the `seen` set still paid a tuple hash per duplicate.
    # Split the two populations: boxes covering more cells than they are
    # worth are tested against everything once (they nearly always overlap
    # something anyway), and the rest keep the cheap uniform hash.
    grid: dict[tuple[int, int], list[int]] = {}
    big: list[int] = []
    spans: list[int] = []
    for i, (x0, y0, x1, y1) in enumerate(bbox):
        gx0, gx1 = int(x0 // cell), int(x1 // cell)
        gy0, gy1 = int(y0 // cell), int(y1 // cell)
        spans.append((gx1 - gx0 + 1) * (gy1 - gy0 + 1))
    # a box wider than this many cells is cheaper to pair by brute force
    # than to insert (and then dedupe) cell by cell
    limit = max(64, 4 * len(bbox))
    for i, (x0, y0, x1, y1) in enumerate(bbox):
        if spans[i] > limit:
            big.append(i)
            continue
        for gx in range(int(x0 // cell), int(x1 // cell) + 1):
            for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for members in grid.values():
        n = len(members)
        for ai in range(n):
            a = members[ai]
            for b in members[ai + 1:]:
                pair = (a, b) if a < b else (b, a)
                if pair not in seen:
                    seen.add(pair)
                    out.append(pair)
    for i in big:  # oversized: pair against every other box, once
        for j in range(len(bbox)):
            if j == i:
                continue
            pair = (i, j) if i < j else (j, i)
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    # Sorted, not insertion-ordered: callers turn these into user-visible
    # messages ("overlap A-B"), and grid iteration order is an artifact of
    # which cell happened to be visited first. Sorting costs ~1% of the
    # duplicate scan it replaces and keeps the report stable.
    out.sort()
    return out


def _seg_dist(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> float:
    (x1, y1, x2, y2), (x3, y3, x4, y4) = a, b
    if x1 == x2 == x3 == x4:
        # shared vertical: gap between y-ranges (0 if overlapping)
        lo1, hi1 = min(y1, y2), max(y1, y2)
        lo2, hi2 = min(y3, y4), max(y3, y4)
        return max(0.0, max(lo1, lo2) - min(hi1, hi2))
    if y1 == y2 == y3 == y4:
        lo1, hi1 = min(x1, x2), max(x1, x2)
        lo2, hi2 = min(x3, x4), max(x3, x4)
        return max(0.0, max(lo1, lo2) - min(hi1, hi2))

    def d(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        dx, dy = bx - ax, by - ay
        denom: float = dx * dx + dy * dy or 1e-9
        t: float = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
        return float(((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5)

    def cross() -> bool:
        # proper intersection of two NON-DEGENERATE segments (points fall
        # through to endpoint distances below — two distant vias must not
        # read as crossing). Endpoint touches count: shared pads route
        # through the same point only when same-net, checked by callers.
        if (x1 == x2 and y1 == y2) or (x3 == x4 and y3 == y4):
            return False

        def side(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
            return (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        s1, s2 = side(x3, y3, x1, y1, x2, y2), side(x4, y4, x1, y1, x2, y2)
        s3, s4 = side(x1, y1, x3, y3, x4, y4), side(x2, y2, x3, y3, x4, y4)
        return s1 * s2 <= 0 and s3 * s4 <= 0

    if cross():
        return 0.0
    return min(d(x1, y1, x3, y3, x4, y4), d(x2, y2, x3, y3, x4, y4),
               d(x3, y3, x1, y1, x2, y2), d(x4, y4, x1, y1, x2, y2))


# In-tree public leaf math for sibling engines (score). Underscore names
# remain for local call sites; apps still go through Board, not these.
grid_pairs = _grid_pairs
seg_dist = _seg_dist


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
    sizes = [p.wh() for p in parts]
    boxes = [(p.x - w / 2 - 0.1, p.y - h / 2 - 0.1, p.x + w / 2 + 0.1, p.y + h / 2 + 0.1)
             for p, (w, h) in zip(parts, sizes)]
    # Bound formatted overlap rows; full count is returned as overlap_count.
    OVERLAP_DETAIL = 64
    overlap_n = 0
    overlap_detail: list[str] = []
    for i, j in _grid_pairs(boxes, 5.0):
        a, b = parts[i], parts[j]
        aw, ah = sizes[i]
        bw, bh = sizes[j]
        if (abs(a.x - b.x) < (aw + bw) / 2 + 0.1 and
                abs(a.y - b.y) < (ah + bh) / 2 + 0.1):
            # Cap formatted rows: 14.7M unique strings / ~291MB on a scrambled
            # 5,420-part board, and every caller only counts them or shows the
            # first few. Full count lives on `overlap_count`; detail stays
            # the first OVERLAP_DETAIL pairs (stable _grid_pairs order).
            overlap_n += 1
            if len(overlap_detail) < OVERLAP_DETAIL:
                overlap_detail.append(f"overlap {a.ref}-{b.ref}")
    errors.extend(overlap_detail)
    if overlap_n > OVERLAP_DETAIL:
        errors.append(f"overlap … ({overlap_n} total)")
    lib = board._lib()
    for p in parts:
        if lib.get(p.fp, {}).get("edge"):
            continue  # edge-mount: overhang is the point (USB-C plug etc.)
        pw, ph = p.wh()
        if not (pw / 2 + edge <= p.x <= board.width - pw / 2 - edge and
                ph / 2 + edge <= p.y <= board.height - ph / 2 - edge):
            errors.append(f"edge {p.ref}")
    from .parts import hole_drill, slot_of
    lib = board._lib()
    for p in parts:
        for pin in p.pins_of(lib):
            dr = hole_drill(p.fp, pin, lib)
            if dr and dr < min_drill:
                errors.append(f"drill {p.ref}.{pin}={dr} < {min_drill}")
            so = slot_of(p.fp, pin, lib)
            if so is not None and min(so[2], so[3]) < min_drill:
                errors.append(f"slot {p.ref}.{pin}={min(so[2], so[3]):g} < {min_drill}")
    classes: dict[str, float] = {}
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "class":
            classes[str(c.get("name", ""))] = float(cast(float, c.get("clearance", 0.0)))
    for net in board.nets.values():
        if len([1 for r, _ in net.pins if r in board.parts]) == 1:
            errors.append(f"floating {net.name}")
        if net.width < min_trace:
            errors.append(f"width {net.name}={net.width}")
    for t in board.traces:
        if t.width < min_trace:
            errors.append(f"trace-width {t.net}")
        if t.jumper:
            if board.layers == 1:
                warnings.append(f"jumper {t.net} (wire bridge needed)")
            else:
                warnings.append(f"airwire {t.net} (maze fallback — re-route?)")
    poured = pour_layers(board)

    def _zone_warns(c: dict[str, object]) -> None:
        z = zone_at(board, c)
        for p in parts:
            if str(c.get("ref", "")) == p.ref:
                continue  # own deadzone never flags its anchor part
            pw, ph = p.wh()
            if in_zone(z, p.x, p.y, (pw / 2, ph / 2)):
                # warning, not error: modules sit in antenna keepouts by
                # design (mitox U4); review, don't block
                warnings.append(f"keepout {p.ref}")
        for t in board.traces:
            if t.layer in poured.get(t.net, []):
                continue  # plane copper, not a trace — keepouts don't apply
            mx, my = (t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2
            if in_zone(z, mx, my, t.width / 2):
                warnings.append(f"keepout-trace {t.net}")

    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "keepout":
            _zone_warns(c)
    # footprint keepouts (antenna zones etc.): same warnings, synthesized
    for ref in board.parts:
        for c in fp_keepouts(board, ref):
            _zone_warns(c)
    # stranded pour pads: plane is cut inside keepouts AND routers skip
    # poured nets, so a poured pad in a keepout floats electrically. Error:
    # fab-correct, circuit-broken.
    if poured:
        zones = [zone_at(board, c) for c in board.constraints
                 if isinstance(c, dict) and c.get("t") in ("keepout", "cutout")]
        for ref in board.parts:
            for c in fp_keepouts(board, ref):
                zones.append(zone_at(board, c))
        for n, net in board.nets.items():
            if n not in poured:
                continue
            for r, q in net.pins:
                if r not in board.parts:
                    continue
                x, y = board.pad_pos(r, q)
                if any(in_zone(z, x, y) for z in zones):
                    errors.append(f"pour-isolated {n} {r}.{q} (keepout cuts plane, no trace)")
    for c in board.constraints:
        if not (isinstance(c, dict) and c.get("t") in ("hole", "bend")):
            continue
        if c.get("t") == "hole":
            if _f(c["d"]) < min_drill:
                errors.append(f"hole-drill {c['d']} < {min_drill}")
        elif c.get("t") == "bend":
            cx, cy = _f(c["x"]), _f(c["y"])
            hw, hh = _f(c["w"]) / 2, _f(c["h"]) / 2
            for t in board.traces:
                # traces must cross bends (that's the point); vias must not
                # (dynamic bends: strict error; static: covered vias tolerated)
                if t.via and c.get("dynamic", True):
                    if abs(t.x1 - cx) < hw and abs(t.y1 - cy) < hh:
                        errors.append(f"bend-via {t.net}")
            for p in parts:
                pw, ph = p.wh()
                if abs(p.x - cx) < hw + pw / 2 and abs(p.y - cy) < hh + ph / 2:
                    errors.append(f"bend-part {p.ref}")
            if c.get("dynamic", True):
                # pours flood the board: any plane crosses the bend and cracks
                for n in sorted(pour_layers(board)):
                    errors.append(f"bend-pour {n} (plane crosses dynamic bend)")
            # radius vs finished thickness: 6x static, 10x dynamic (JLC FPC)
            th = float(cast(tuple[float, float], P["thickness"])[1])
            need = (10 if c.get("dynamic", True) else 6) * th
            if _f(c["r"]) < need:
                errors.append(f"bend-radius {c['r']} < {need:g} (dynamic={c.get('dynamic', True)})")
    from .solver import diff_cost, match_cost
    mc = match_cost(board)
    if mc > 5.0:
        warnings.append(f"length-mismatch skew~{mc / 50.0:.1f}mm")
    dc = diff_cost(board)
    if dc > 10.0:
        warnings.append(f"diff-pair skew/gap dev~{dc / 100.0:.1f}mm")
    def _need(a: str, b: str) -> float:
        need = min_space
        for n in (a, b):
            net = board.nets.get(n)
            cl = net.attrs.get("class") if net is not None else None
            if cl in classes:
                need = max(need, classes[cl])
        return need

    tr = board.traces
    by_layer: dict[int, list[int]] = {}
    for i, s in enumerate(tr):
        by_layer.setdefault(s.layer, []).append(i)
    # pad: a clearance hit needs dist < need, so boxes grown by max need
    # share a cell with every true hit (same argument as _grid_pairs docs)
    max_need = min_space
    if classes:
        max_need = max([min_space] + list(classes.values()))
    for members in by_layer.values():
        boxes = [(min(tr[i].x1, tr[i].x2) - max_need, min(tr[i].y1, tr[i].y2) - max_need,
                  max(tr[i].x1, tr[i].x2) + max_need, max(tr[i].y1, tr[i].y2) + max_need)
                 for i in members]
        cand: dict[int, list[int]] = {}
        for pa, pb in _grid_pairs(boxes, 5.0):
            ia, ib = members[pa], members[pb]
            cand.setdefault(min(ia, ib), []).append(max(ia, ib))
        for i in sorted(cand):
            sa = tr[i]
            for j in sorted(cand[i]):
                sb = tr[j]
                if sa.net == sb.net:
                    continue
                if sa.layer in poured.get(sa.net, []) or sb.layer in poured.get(sb.net, []):
                    continue  # plane copper never clearances against traces
                if _seg_dist((sa.x1, sa.y1, sa.x2, sa.y2),
                             (sb.x1, sb.y1, sb.x2, sb.y2)) < _need(sa.net, sb.net):
                    warnings.append(f"clearance {sa.net}-{sb.net}")
                    break
    out: dict[str, object] = {"errors": errors, "warnings": warnings, "fab": key}
    if overlap_n:
        out["overlap_count"] = overlap_n
    return out


def erc(board: Board) -> dict[str, object]:
    """Electrical rule check: netlist sanity before any copper.
    Errors: unconnected pins, single-pin nets, same-pin-twice, power nets
    shorted together (AUTO_JOIN rails plus `power`-constraint nets sharing
    a pin), empty nets.
    Warnings: pins sharing a footprint pad name across parts is fine —
    reported only when a net has >12 pins (smell: accidental global)."""
    from .types import AUTO_JOIN
    errors: list[str] = []
    warnings: list[str] = []
    lib = board._lib()
    ncs: set[str] = set()
    for c in board.constraints:
        if c.get("t") == "nc":
            ncs.update(cast(list[str], c.get("pins", [])))
    connected: set[tuple[str, str]] = set()
    for n, net in board.nets.items():
        if not net.pins:
            errors.append(f"empty {n}")
            continue
        seen: set[tuple[str, str]] = set()
        for ref, pin in net.pins:
            if ref not in board.parts:
                errors.append(f"unknown {ref} on {n}")
                continue
            key = (ref, str(pin))
            if key in seen:
                errors.append(f"duplicate {ref}.{pin} on {n}")
            seen.add(key)
            connected.add(key)
    for ref, p in board.parts.items():
        if p.attrs.get("dnp"):
            continue  # unpopulated: pins float by design, still placed
        for pin in p.pins_of(lib):
            if (ref, pin) not in connected and f"{ref}.{pin}" not in ncs \
                    and not pin.startswith("NC"):
                errors.append(f"unconnected {ref}.{pin}")
    # power nets sharing pins = shorted rails (AUTO_JOIN rails plus
    # any net a `power` constraint marks as power — custom rails short too)
    power_nets = set(AUTO_JOIN)
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "power":
            for n in cast(list[str], c.get("nets", [])):
                power_nets.add(str(n))
    owners: dict[tuple[str, str], str] = {}
    for n, net in board.nets.items():
        if n in power_nets:
            for ref, pin in net.pins:
                key = (ref, str(pin))
                if key in owners:
                    errors.append(f"power-short {n}/{owners[key]} at {ref}.{pin}")
                owners[key] = n
    for n, net in board.nets.items():
        if len(net.pins) > 12:
            warnings.append(f"big-net {n} ({len(net.pins)} pins — intentional?)")
    return {"errors": errors, "warnings": warnings}
