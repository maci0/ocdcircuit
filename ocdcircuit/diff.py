"""Board diff: what changed between two .ocd files (knoll diff style)."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit import Board


def diff(a: Board, b: Board) -> str:
    out: list[str] = []
    if (a.width, a.height, a.layers) != (b.width, b.height, b.layers):
        out.append(f"size: {a.width:g}x{a.height:g} {a.layers}L"
                   f" → {b.width:g}x{b.height:g} {b.layers}L")
    for ref in sorted(set(a.parts) - set(b.parts)):
        out.append(f"- part {ref} {a.parts[ref].fp}")
    for ref in sorted(set(b.parts) - set(a.parts)):
        out.append(f"+ part {ref} {b.parts[ref].fp}")
    for ref in sorted(set(a.parts) & set(b.parts)):
        pa, pb = a.parts[ref], b.parts[ref]
        if (pa.fp, pa.value) != (pb.fp, pb.value):
            out.append(f"~ part {ref}: {pa.fp} {pa.value} → {pb.fp} {pb.value}")
        if abs(pa.x - pb.x) > 0.05 or abs(pa.y - pb.y) > 0.05:
            out.append(f"~ move {ref}: ({pa.x:g},{pa.y:g}) → ({pb.x:g},{pb.y:g})")
    an = {n: sorted(f"{r}.{p}" for r, p in net.pins) for n, net in a.nets.items()}
    bn = {n: sorted(f"{r}.{p}" for r, p in net.pins) for n, net in b.nets.items()}
    for n in sorted(set(an) - set(bn)):
        out.append(f"- net {n}")
    for n in sorted(set(bn) - set(an)):
        out.append(f"+ net {n}")
    for n in sorted(set(an) & set(bn)):
        if an[n] != bn[n]:
            out.append(f"~ net {n}: {' '.join(an[n])} → {' '.join(bn[n])}")
    return "\n".join(out)
