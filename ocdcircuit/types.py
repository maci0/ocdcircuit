"""Shared static types."""
from __future__ import annotations
from collections.abc import Callable
from typing import NotRequired, TypedDict, Union

XY = tuple[float, float]
BBox = tuple[float, float, float, float]
# effect closures stored on the undo stack
Undo = Callable[[], None]
# constraint dicts: {"t": "near"|"fixed"|"edge"|"layer"|"width"|"power"|..., ...}
Constraint = dict[str, object]
# patch ops for the agent API
Op = dict[str, object]
# animation frames streamed by placer/router
Frame = dict[str, object]
# footprint metadata
Footprint = dict[str, object]
PadSpec = tuple[float, float, float, float]  # dx, dy, w, h
HoleSpec = tuple[float, float, float]  # dx, dy, drill
SlotSpec = tuple[float, float, float, float]  # dx, dy, w, h (milled slot)
PinName = str
NetName = str
RefName = str
# allow int pins at boundaries (normalized to str internally)
PinLike = Union[str, int]


class DrcReport(TypedDict):
    """Return shape of ``Board.check()`` / DRC plugins.

    ``errors`` and ``warnings`` are always present (possibly empty).
    ``fab`` is set when a fab profile was applied; ``ran`` lists sibling
    keys when ``check("all")`` merges multiple DRC plugins.
    """

    errors: list[str]
    warnings: list[str]
    fab: NotRequired[str]
    ran: NotRequired[list[str]]
