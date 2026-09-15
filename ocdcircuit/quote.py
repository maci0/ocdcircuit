"""Fab price comparison: bare PCB + assembled, per fab. Stdlib only.

Two halves, matching how fabs actually bill:
- bare PCB: parametric model per fab (base + area + layer steps), fit to
  published prototype pricing (JLC $2/5pcs 2L, OSH Park $5/in², …).
- assembly (with parts): JLC fee schedule (setup + joints + extended-part
  loading) + per-part unit prices from knoll's live JLC lookup or a manual
  `price=` part attr. Other fabs: bare + "no model" for assembly (their
  assembly is quote-per-order; the bare column still compares).

Estimates, not quotes — fabs change prices without telling you, and every
number here carries its source + date so staleness is visible. Sizes in mm,
money in USD. qty = boards ordered; per-board = total/qty.
"""
from __future__ import annotations
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit import Board

STAMP = "2026-09"  # when the model numbers were fit — re-verify before ordering

# Bare-PCB model per fab: total USD for `qty` boards of `area` cm², `layers`.
# JLC/PCBWay/NextPCB/ALLPCB: Chinese proto flat-rate + area slope; PCBWay's
# base runs ~$3 over JLC. OSH Park: $5/in² 2L, $10/in² 4L, 3 copies, free ship.
# Aisler: €-zone HDK pricing ≈ $18 + area; Eurocircuits: pooled service ≈
# $35 + area + layer steps; Sierra/Advanced: US proto shops, setup-heavy.
BARE = {
    # fab: (base, per_cm2, per_layer_step_over_2, min_qty, name)
    "jlc": (2.0, 0.06, 3.0, 5, "JLCPCB standard"),
    "pcbway": (5.0, 0.06, 3.5, 5, "PCBWay standard"),
    "jlc-flex": (8.0, 0.15, 4.0, 5, "JLCPCB flex (FPC)"),
    "oshpark": (0.0, 0.775, 0.775, 3, "OSH Park"),  # $5/in² 2L; 4L doubles
    "seeed": (4.0, 0.08, 3.0, 5, "Seeed Fusion"),
    "aisler": (18.0, 0.25, 6.0, 1, "Aisler HDSK"),
    "eurocircuits": (35.0, 0.30, 12.0, 1, "Eurocircuits standard pool"),
    "nextpcb": (3.0, 0.06, 3.0, 5, "NextPCB standard"),
    "allpcb": (3.0, 0.06, 3.0, 5, "ALLPCB prototype"),
    "sierra": (45.0, 0.40, 15.0, 1, "Sierra Circuits proto"),
    "advanced": (40.0, 0.35, 12.0, 1, "Advanced Circuits standard"),
}

# JLC assembly fee schedule (Economic PCBA, single-side), fit to their
# published table 2026-09: https://jlcpcb.com/help/article/pcb-assembly-price
JLC_SETUP = 8.18
JLC_STENCIL = 1.53
JLC_SMT_JOINT = 0.0016
JLC_EXTENDED_FEE = 3.07  # per unique extended part, one-time
JLC_CONFIRM = 0.45


def _pads(fp: str, lib: object) -> int:
    from typing import cast
    from .parts import pads_of
    from .types import Footprint
    try:
        assert isinstance(lib, dict)
        return len(pads_of(fp, cast(dict[str, Footprint], lib)))
    except KeyError:
        return 2


def assembly_parts(board: Board) -> list[dict[str, object]]:
    """Placed (non-DNP) parts with pins: what assembly bills on."""
    lib = board._lib()
    rows: list[dict[str, object]] = []
    for p in board.parts.values():
        if p.attrs.get("dnp"):
            continue
        rows.append({"ref": p.ref, "fp": p.fp, "value": p.value,
                     "lcsc": str(p.attrs.get("lcsc", "")),
                     "mpn": str(p.attrs.get("mpn", "")),
                     "pins": _pads(p.fp, lib)})
    return rows


def unit_price(board: Board, ref: str) -> tuple[float | None, str]:
    """One part's unit USD: the board's `price` provider first (std: `price=`
    attr, then offline JLC DB), then knoll live JLC; else unpriced. Provider
    calls go through Board.price → _run, so a crashing provider is fenced
    in failure memory like every other plugin (not silently swallowed)."""
    from .core import Plugin
    p = board.parts.get(ref)
    if p is None:  # KeyError on unknown ref (fixable input)
        raise KeyError(f"no part {ref!r}")
    lcsc, mpn = str(p.attrs.get("lcsc", "")), str(p.attrs.get("mpn", ""))
    for key in ("std", "knoll"):
        try:
            out = board.price(ref, key, lcsc=lcsc, mpn=mpn)
        except (ValueError, KeyError, OSError, AssertionError):
            continue  # unmounted / bad input: next source, quote still prices
        except Exception:
            continue  # fenced provider (failure memory): next source
        pv = out.get("price")
        if pv is None:
            continue
        src = out.get("source", "price")
        assert isinstance(pv, (int, float)) and isinstance(src, str)
        import math
        if math.isfinite(pv) and pv >= 0:
            return float(pv), src
    return None, "unpriced"


def bare(board: Board, fab: str, qty: int = 5) -> dict[str, object]:
    """Bare-PCB estimate for one fab: {total, per_board, note}."""
    from typing import cast
    from . import fab as _fab
    prof = _fab.get(fab)  # ValueError on unknown fab (fixable input)
    if qty < 1:
        raise ValueError(f"qty {qty!r} must be ≥1")
    layers_supported = cast(tuple[int, ...], prof["layers"])
    if board.layers not in layers_supported:
        raise ValueError(f"{fab} doesn't do {board.layers}L "
                         f"(has {sorted(layers_supported)})")
    base, slope, step, minq, _name = BARE[fab]
    area = board.width * board.height / 100.0  # mm² → cm²
    layers = board.layers
    if fab == "oshpark":
        # per-in² pricing, 4L doubles, qty snaps to 3-packs
        total = area / 6.4516 * (5.0 if layers <= 2 else 10.0)
        packs = math.ceil(qty / 3)
        return {"total": round(total * packs, 2),
                "per_board": round(total * packs / (packs * 3), 2),
                "boards": packs * 3, "note": "3-packs, free US ship, no assembly"}
    n = max(qty, minq)
    total = base + slope * area + step * max(0, (layers - 2) // 2)
    if fab in ("sierra", "advanced", "eurocircuits", "aisler"):
        note = "US/EU proto shop: setup-heavy, no assembly model"
    elif fab == "jlc-flex":
        note = "flex surcharge baked into base"
    else:
        note = "proto flat-rate; ENIG/tariffs/shipping extra"
    return {"total": round(total, 2), "per_board": round(total / n, 2),
            "boards": n, "note": note}


def assembled(board: Board, qty: int = 5) -> dict[str, object]:
    """JLC assembly adders over bare: fees + parts. Only JLC has a published
    fee schedule; every other fab gets bare-only (their assembly is per-quote)."""
    from typing import cast
    rows = assembly_parts(board)
    joints = sum(cast(int, r["pins"]) for r in rows)
    fees = JLC_SETUP + JLC_STENCIL + JLC_CONFIRM + joints * JLC_SMT_JOINT
    parts_total = 0.0
    unpriced: list[str] = []
    sources: dict[str, int] = {}
    for r in rows:
        v, src = unit_price(board, str(r["ref"]))
        sources[src] = sources.get(src, 0) + 1
        if v is None:
            unpriced.append(str(r["ref"]))
        else:
            parts_total += v
    ext = sum(1 for r in rows if not str(r.get("lcsc", "")).startswith("C"))
    ext_fee = ext * JLC_EXTENDED_FEE if rows else 0.0
    total = fees + ext_fee + parts_total * qty
    return {"fees": round(fees + ext_fee, 2), "parts_per_board": round(parts_total, 2),
            "total": round(total, 2), "per_board": round(total / qty, 2) if qty else 0.0,
            "joints": joints, "parts": len(rows), "unpriced": unpriced,
            "sources": sources, "extended_parts": ext,
            "note": "JLC Economic PCBA single-side; parts from live JLC or price= attr"}


def compare(board: Board, qty: int = 5, fabs: list[str] | None = None,
            with_parts: bool = True) -> dict[str, object]:
    """Every fab, bare + (JLC only) assembled. Cheapest-first order."""
    from typing import cast
    from . import fab as _fab
    if qty < 1:
        raise ValueError(f"qty {qty!r} must be ≥1")
    want = list(fabs) if fabs else _fab.list_fabs()
    for f in want:
        if f not in _fab.PROFILES:
            raise ValueError(f"unknown fab {f!r} (have {_fab.list_fabs()})")
    rows: list[dict[str, object]] = []
    asm: dict[str, object] | None = None
    if with_parts:
        asm = assembled(board, qty)
        assert isinstance(asm["total"], float)
    for f in want:
        try:
            b = bare(board, f, qty)
        except ValueError as e:
            rows.append({"fab": f, "error": str(e)})
            continue
        row: dict[str, object] = {"fab": f, "bare_total": b["total"],
                                  "bare_per_board": b["per_board"],
                                  "boards": b["boards"], "note": b["note"]}
        if f == "jlc" and asm is not None:
            atotal = float(cast(float, asm["total"]))
            row["asm_total"] = round(float(cast(float, b["total"])) + atotal, 2)
            row["asm_per_board"] = round(float(cast(float, row["asm_total"])) / qty, 2)
            row["asm"] = asm
        rows.append(row)
    priced = sorted((r for r in rows if "bare_total" in r),
                    key=lambda r: float(cast(float, r["bare_total"])))
    errored = [r for r in rows if "bare_total" not in r]
    return {"qty": qty, "board": f"{board.width:g}x{board.height:g}mm {board.layers}L",
            "rows": priced + errored, "stamp": STAMP,
            "note": "estimates from published proto pricing; re-verify before ordering"}


if __name__ == "__main__":
    import os
    import sys
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: python -m ocdcircuit.quote <board.ocd>  # bare-PCB table, qty 5")
        raise SystemExit(1)
    from ocdcircuit import agent
    b = agent.loads(open(sys.argv[1]).read(),
                    base=os.path.dirname(os.path.abspath(sys.argv[1])))
    rows = compare(b)["rows"]
    assert isinstance(rows, list)
    for r in rows:
        assert isinstance(r, dict)
        print(f"{r['fab']:12} bare ${r.get('bare_total', '?')}"
              + (f"  asm ${r['asm_total']}" if "asm_total" in r else ""))
