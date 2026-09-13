"""OCD neatness score 0-100 (knoll-style). Read-only: never mutates the board."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit import Board


def score(board: Board) -> dict[str, object]:
    parts = list(board.parts.values())
    sub: dict[str, float] = {}
    if not parts:
        return {"total": 0, "grade": "F", "parts": sub}
    # grid alignment: share x/y with another part (±0.25)
    xs = [p.x for p in parts]
    ys = [p.y for p in parts]
    aligned = sum(1 for i, p in enumerate(parts)
                  if any(abs(p.x - q) < 0.25 for j, q in enumerate(xs) if j != i)
                  or any(abs(p.y - q) < 0.25 for j, q in enumerate(ys) if j != i))
    sub["grid"] = round(100 * aligned / len(parts), 1)
    # orientation: share rot with the majority
    rots = [p.rot for p in parts]
    maj = max(set(rots), key=rots.count)
    sub["orientation"] = round(100 * sum(r == maj for r in rots) / len(rots), 1)
    # spacing: no overlap pairs (uses DRC boxes)
    bad = 0
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            aw, ah = a.wh()
            bw, bh = b.wh()
            if (abs(a.x - b.x) < (aw + bw) / 2 and
                    abs(a.y - b.y) < (ah + bh) / 2):
                bad += 1
    sub["spacing"] = round(max(0, 100 - 25 * bad), 1)
    # edge discipline: all inside with 0.3 margin
    out = sum(1 for p in parts
              if not (p.wh()[0] / 2 + 0.3 <= p.x <= board.width - p.wh()[0] / 2 - 0.3
                      and p.wh()[1] / 2 + 0.3 <= p.y <= board.height - p.wh()[1] / 2 - 0.3))
    sub["edge"] = round(max(0, 100 - 25 * out), 1)
    # compactness: courtyard fill vs board area (target 15-60% → 100)
    fill = sum(p.wh()[0] * p.wh()[1] for p in parts) / max(1, board.width * board.height)
    sub["compact"] = round(100 * min(fill / 0.15, (0.9 - fill) / 0.3) if fill < 0.9 else 0, 1)
    sub["compact"] = max(0, min(100, sub["compact"]))
    total = round(sum(sub.values()) / len(sub), 1)
    grade = "A" if total >= 90 else "B" if total >= 75 else "C" if total >= 60 else "D" if total >= 40 else "F"
    return {"total": total, "grade": grade, "parts": sub}
