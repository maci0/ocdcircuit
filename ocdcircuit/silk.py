"""Auto-silkscreen at detail levels. Pure function of board state.

Levels: 0 = refs only (dense boards) · 1 = refs + values · 2 = + pin-1
dots + courtyard outline · 3 = + net labels at segment midpoints.
`silk <n>` statement sets the level (default 1). Renderers/exporters call
labels() and format geometry themselves.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .circuit import Board

LEVELS = (0, 1, 2, 3)
DEFAULT = 1


class Text(NamedTuple):
    x: float
    y: float
    s: str
    cls: str  # silk-ref | silk-val | silk-net


class Dot(NamedTuple):
    x: float
    y: float


class Box(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float


class Silk(NamedTuple):
    texts: list[Text]
    dots: list[Dot]
    boxes: list[Box]


def level_of(board: Board) -> int:
    for c in board.constraints:
        if c.get("t") == "silk":
            lv = c["level"]
            assert isinstance(lv, int) and lv in LEVELS
            return lv
    return DEFAULT


def labels(board: Board, level: int | None = None) -> Silk:
    """All coordinates in board mm, y-up. SVG caller flips y."""
    lv = DEFAULT if level is None else level
    assert lv in LEVELS, f"silk level {lv} not in {LEVELS}"
    texts: list[Text] = []
    dots: list[Dot] = []
    boxes: list[Box] = []
    for p in board.parts.values():
        texts.append(Text(p.x, p.y + p.h / 2 + 0.8, p.ref, "silk-ref"))
        if lv >= 1 and p.value:
            texts.append(Text(p.x, p.y - p.h / 2 - 1.0, p.value, "silk-val"))
        if lv >= 2:
            from .parts import pads_of
            pads = pads_of(p.fp)
            if "1" in pads:
                dx, dy = pads["1"]
                dots.append(Dot(p.x + dx, p.y + dy))
            boxes.append(Box(p.x - p.w / 2 - 0.2, p.y - p.h / 2 - 0.2,
                             p.x + p.w / 2 + 0.2, p.y + p.h / 2 + 0.2))
    if lv >= 3:
        for t in board.traces:
            texts.append(Text((t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2, t.net, "silk-net"))
    return Silk(texts, dots, boxes)
