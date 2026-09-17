from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board, Seg


def net_length(board: Board, net: str,
               by_net: dict[str, list[Seg]] | None = None) -> float:
    if by_net is not None:
        segs = by_net.get(net, [])
    else:
        segs = [s for s in board.traces if s.net == net]
    if segs:
        return sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1) for s in segs)
    pts = [board.pad_pos(r, q) for r, q in board.nets[net].pins if r in board.parts]
    if len(pts) < 2:
        return 0.0
    return sum(abs(p[0] - pts[0][0]) + abs(p[1] - pts[0][1]) for p in pts[1:])


def match_cost(board: Board,
               by_net: dict[str, list[Seg]] | None = None) -> float:
    c = 0.0
    for con in board.constraints:
        if con.get("t") != "match":
            continue
        nets = [n for n in cast(list[str], con.get("nets", [])) if n in board.nets]
        if len(nets) < 2:
            continue
        lens = [net_length(board, n, by_net) for n in nets]
        c += 50.0 * (max(lens) - min(lens))
    return c


def diff_cost(board: Board,
              by_net: dict[str, list[Seg]] | None = None) -> float:
    c = 0.0
    for con in board.constraints:
        if con.get("t") != "diff":
            continue
        p, n = str(con.get("p")), str(con.get("n"))
        if p not in board.nets or n not in board.nets:
            continue
        c += 100.0 * abs(net_length(board, p, by_net) - net_length(board, n, by_net))
        gap = float(cast(float, con.get("gap", 0.3)))
        pp = [board.pad_pos(r, q) for r, q in board.nets[p].pins if r in board.parts]
        np_ = [board.pad_pos(r, q) for r, q in board.nets[n].pins if r in board.parts]
        if pp and np_:
            d = min(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in pp for b in np_)
            c += 20.0 * abs(d - gap)
    return c
