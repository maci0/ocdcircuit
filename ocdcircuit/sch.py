"""Shared schematic layout geometry (renderer, export, studio, score).

Leaf engine: depends only on Board. Plugins and other engines import this —
never the reverse — so schematic consumers stay out of plugins.py.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .circuit import Board


class SchLayout(NamedTuple):
    """Shared schematic geometry. Typed, so consumers stop re-asserting the
    shape of an `object`-valued dict on every field."""
    order: list[str]
    nets: list[str]
    px: dict[str, float]
    rail_y: dict[str, float]
    col_w: int
    top: int
    W: int
    H: int


def sch_layout(board: Board) -> SchLayout:
    """Shared schematic geometry (renderer + studio canvas draw the same
    picture): barycenter-ordered part columns, one rail row per net."""
    refs = sorted(board.parts)
    nets = sorted(board.nets)
    pin_nets: dict[str, set[str]] = {r: set() for r in refs}
    for n, net in board.nets.items():
        for r, _ in net.pins:
            if r in pin_nets:
                pin_nets[r].add(n)
    # Adjacency once, from the net→parts inversion: the sweep below used to
    # rebuild each part's neighbour list by scanning every OTHER part and
    # intersecting net sets — O(n²) set intersections per sweep, six sweeps
    # (2.1M pair tests on virgo vs 143k to invert). Neighbours never change
    # during the sweeps; only the positions do.
    nb_of: dict[str, set[str]] = {r: set() for r in refs}
    for net in board.nets.values():
        ms = {r for r, _ in net.pins if r in pin_nets}
        if len(ms) > 1:
            for r in ms:
                nb_of[r] |= ms
    for r, s in nb_of.items():
        s.discard(r)
    # barycenter sweeps: order parts so shared-net neighbors sit close
    order = list(refs)
    pos = {r: float(i) for i, r in enumerate(order)}
    for _ in range(6):
        for r in order:
            nb = nb_of[r]
            if nb:
                pos[r] = sum(pos[q] for q in nb) / len(nb)
        order.sort(key=lambda r: pos[r])
    col_w, top = 120, 70
    return SchLayout(
        order=order, nets=nets,
        px={r: 10 + i * col_w + col_w / 2 for i, r in enumerate(order)},
        rail_y={n: top + 20 + i * 26 for i, n in enumerate(nets)},
        col_w=col_w, top=top,
        W=max(1, len(order)) * col_w + 20,
        H=top + len(nets) * 26 + 30 + 40)
