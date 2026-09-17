"""Shared static types."""
from __future__ import annotations
from collections.abc import Callable
from typing import NotRequired, TypedDict

XY = tuple[float, float]
BBox = tuple[float, float, float, float]
# effect closures stored on the undo stack
Undo = Callable[[], None]
# constraint dicts: {"t": "near"|"fixed"|"edge"|"layer"|"width"|"power"|..., ...}
Constraint = dict[str, object]
# animation frames streamed by placer/router
Frame = dict[str, object]
# footprint metadata
Footprint = dict[str, object]
PadSpec = tuple[float, float, float, float]  # dx, dy, w, h
HoleSpec = tuple[float, float, float]  # dx, dy, drill
SlotSpec = tuple[float, float, float, float]  # dx, dy, w, h (milled slot)
# allow int pins at boundaries (normalized to str internally)
PinLike = str | int
# Power rails that stay unprefixed across module include joins, and that ERC
# treats as power nets (shorted when they share a pin). Owned here so agent
# (language), drc, placer, and routers share one vocabulary.
AUTO_JOIN = ("VCC", "GND", "VDD", "VSS", "5V", "3V3")
# Sim/SPICE ground aliases (includes SPICE node "0"). Not the same as
# AUTO_JOIN: VCC is power, "0" is never an auto-join rail name.
GNDS = ("GND", "VSS", "0")
# Placement/routing "no signal" rails. AUTO_JOIN plus casefold aliases so
# imported/bench netlists (vcc/vss) skip the same nets clustering and
# wiremask do — one set, not three hand-maintained copies that drift.
BIG_RAILS = frozenset(AUTO_JOIN) | frozenset(n.casefold() for n in AUTO_JOIN)
# Digital-gate default-high rails (AUTO_JOIN minus grounds).
LOGIC_HI = frozenset(n for n in AUTO_JOIN if n not in GNDS)


class DrcReport(TypedDict):
    """Return shape of ``Board.check()`` / DRC plugins.

    ``errors`` and ``warnings`` are always present (possibly empty).
    ``fab`` is set when a fab profile was applied; ``ran`` lists sibling
    keys when ``check("all")`` merges multiple DRC plugins.
    ``overlap_count`` is the full part-overlap total when overlaps were
    found (``errors`` may only keep the first few detail rows).
    """

    errors: list[str]
    warnings: list[str]
    fab: NotRequired[str]
    ran: NotRequired[list[str]]
    overlap_count: NotRequired[int]
