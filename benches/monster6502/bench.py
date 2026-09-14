"""Monster6502-class placement benchmark harness (stdlib only).

Scores placer output against die-true golden positions:
  wirelength placed vs golden (same star-model WL both sides),
  displacement (similarity to golden, secondary — die-true is human
  2-sided hierarchy-aware, NOT wirelength-optimal),
  overlaps above the golden floor (golden itself scores GOLDEN_OV via
  front/back stacking; 0 is NOT the target),
  runtime. Run: python -m benches.monster6502.bench [seeds] [iters]  (defaults reproduce SOURCES baseline)

# ponytail: single-scale harness, no cli framework — argparse when reused.
"""
from __future__ import annotations
import os
import sys
import time

from ocdcircuit import agent
from ocdcircuit import solver
from typing import cast

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_SEEDS, BASE_ITERS = 1, 5  # SOURCES.md baseline config


def golden(b: object) -> dict[str, tuple[float, float]]:
    """Die-true positions. Block boards carry no fixes (placer owns
    instances) — so read layout.json directly, mapping flat refs through
    the converter's rename (I{i}_R/Q, P{i}_A/B)."""
    import json
    from ocdcircuit.circuit import Board
    assert isinstance(b, Board)
    fix = {str(c["ref"]): (float(cast(float, c["x"])), float(cast(float, c["y"])))
           for c in b.constraints if c.get("t") == "fixed"}
    if fix and len(fix) >= len(b.parts):
        return fix
    # block board: fixes cover stragglers only — map members via layout.json
    from benches.monster6502.convert import _find_blocks
    raw = json.load(open(os.path.join(HERE, "netlist.json")))
    lay = json.load(open(os.path.join(HERE, "layout.json")))
    pos = {it["ref"]: (float(it["x"]), float(it["y"])) for it in lay["items"]}
    inv, psg = _find_blocks(raw["components"])
    for i, (r, q) in enumerate(inv):
        if r in pos:
            fix[f"I{i}_R"] = pos[r]
        if q in pos:
            fix[f"I{i}_Q"] = pos[q]
    for i, (a, bb) in enumerate(psg):
        if a in pos:
            fix[f"P{i}_A"] = pos[a]
        if bb in pos:
            fix[f"P{i}_B"] = pos[bb]
    for r, xy in pos.items():
        fix.setdefault(r, xy)
    return fix


def apply_golden(b: object, g: dict[str, tuple[float, float]]) -> None:
    from ocdcircuit.circuit import Board
    assert isinstance(b, Board)
    for r, (x, y) in g.items():
        if r in b.parts:
            b.parts[r].x, b.parts[r].y = x, y


def overlaps(b: object) -> int:
    from ocdcircuit.circuit import Board
    assert isinstance(b, Board)
    errs = cast(list[str], b.check()["errors"])
    return sum(1 for e in errs if str(e).startswith("overlap"))


def main() -> None:
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else BASE_SEEDS
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else BASE_ITERS
    placer = sys.argv[3] if len(sys.argv) > 3 else "diffusion"
    try:
        b = agent.loads(open(os.path.join(HERE, "monster6502.ocd")).read(), base=HERE)
    except OSError:
        raise SystemExit("monster6502.ocd missing (generated, gitignored) — run: python -m benches.monster6502.convert")
    g = golden(b)
    # golden baselines first (same WL model + same overlap counter both sides)
    apply_golden(b, g)
    golden_wl = solver.wirelength(b)
    golden_ov = overlaps(b)
    # release fixed parts so the placer actually works (golden kept for scoring)
    b.constraints = [c for c in b.constraints if c.get("t") != "fixed"]
    t = time.perf_counter()
    cost = b.place(placer, seeds=seeds, iters=iters)
    dt = time.perf_counter() - t
    placed_wl = solver.wirelength(b)
    disp = sum(abs(p.x - g[r][0]) + abs(p.y - g[r][1])
               for r, p in b.parts.items() if r in g) / max(1, len(g))
    ov = overlaps(b)
    # floating nets are file-static (5953 single-pin nets in the netlist),
    # not placement signal — errors counts the placeable rest.
    all_errs = cast(list[str], b.check()["errors"])
    errs = sum(1 for e in all_errs if not str(e).startswith("floating"))
    print(f"seeds={seeds} iters={iters} time={dt:.1f}s cost={cost:.0f}")
    print(f"wirelength placed={placed_wl:.0f} golden={golden_wl:.0f} "
          f"ratio={placed_wl / max(1.0, golden_wl):.2f}")
    print(f"mean_displacement={disp:.2f}mm (similarity, secondary)")
    print(f"overlaps placed={ov} golden_floor={golden_ov} "
          f"above_floor={ov - golden_ov} errors={errs}")


if __name__ == "__main__":
    main()
