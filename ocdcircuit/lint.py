"""Static lint for .ocd sources: fast, no place/route. Pure checks over the
parsed board (parts/nets/constraints as loaded) — style, hygiene, and
likely-silly before the solvers ever run. DRC/ERC own geometry/electrics.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board


def lint(board: Board) -> dict[str, object]:
    """{"errors": [...], "warnings": [...]}. Errors = will fail downstream
    (bad refs, dupes); warnings = smell (unused, shadowing, waste)."""
    errors: list[str] = []
    warnings: list[str] = []
    lib = board._lib()

    connected: set[tuple[str, str]] = set()
    for net in board.nets.values():
        for r, q in net.pins:
            connected.add((r, str(q)))
    ncs: set[str] = set()
    for c in board.constraints:
        if c.get("t") == "nc":
            ncs.update(cast(list[str], c.get("pins", [])))
    for ref, p in board.parts.items():
        if p.fp not in lib:
            errors.append(f"unknown footprint {p.fp} on {ref}")
            continue
        try:
            pins = set(p.pins_of(lib))
        except KeyError:
            errors.append(f"unreadable footprint {p.fp} on {ref}")
            continue
        for pin in pins:
            if ((ref, pin) not in connected and f"{ref}.{pin}" not in ncs
                    and not pin.startswith("NC")):
                warnings.append(f"unconnected {ref}.{pin}")

    for name, net in board.nets.items():
        if len([1 for r, _ in net.pins if r in board.parts]) == 1:
            warnings.append(f"single-pin net {name}")
        for ref, pin in net.pins:
            if ref not in board.parts:
                errors.append(f"unknown part {ref} on net {name}")
                continue
            try:
                pins = set(board.parts[ref].pins_of(lib))
            except KeyError:
                continue
            if str(pin) not in pins:
                errors.append(f"unknown pin {ref}.{pin} on net {name}")


    for c in board.constraints:
        t = c.get("t")
        if t == "fixed":
            ref = str(c.get("ref", ""))
            if ref not in board.parts:
                errors.append(f"fix on unknown part {ref}")
            else:
                p = board.parts[ref]
                pw, ph = p.wh()
                x = float(cast(float, c.get("x", 0)))
                y = float(cast(float, c.get("y", 0)))
                if not (pw / 2 <= x <= board.width - pw / 2
                        and ph / 2 <= y <= board.height - ph / 2):
                    warnings.append(f"fix {ref} off-board")
        elif t == "near":
            for k in ("a", "b"):
                if str(c.get(k, "")) not in board.parts:
                    errors.append(f"near on unknown part {c.get(k)}")
        elif t == "width":
            if str(c.get("net", "")) not in board.nets:
                warnings.append(f"width on unknown net {c.get('net')}")
        elif t == "layer":
            if str(c.get("net", "")) not in board.nets:
                warnings.append(f"route on unknown net {c.get('net')}")
        elif t == "power":
            for n in cast(list[str], c.get("nets", [])):
                if n not in board.nets:
                    warnings.append(f"power on unknown net {n}")

    if not board.parts:
        warnings.append("no parts")
    return {"errors": errors, "warnings": warnings}
