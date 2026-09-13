"""Shared static types."""
from __future__ import annotations
from collections.abc import Callable
from typing import Union

XY = tuple[float, float]
BBox = tuple[float, float, float, float]
# effect closures stored on the undo stack
Undo = Callable[[], None]
# constraint dicts: {"t": "near"|"fixed"|"layer"|"width"|"power"|..., ...}
Constraint = dict[str, object]
# patch ops for the agent API
Op = dict[str, object]
# animation frames streamed by placer/router
Frame = dict[str, object]
# footprint metadata
Footprint = dict[str, object]
PadSpec = tuple[float, float, float, float]  # dx, dy, w, h
HoleSpec = tuple[float, float, float]  # dx, dy, drill
PinName = str
NetName = str
RefName = str
# allow int pins at boundaries (normalized to str internally)
PinLike = Union[str, int]
