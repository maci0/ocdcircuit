"""Monster6502-class placement benchmark harness (stdlib only).

Compares placer output against die-true golden positions:
  wirelength (placed vs golden), displacement (mean mm from golden),
  overlap count, runtime. Run: python3 bench.py [seeds] [iters]

# ponytail: single-scale harness, no cli framework — argparse when reused.
"""
from __future__ import annotations
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from ocdcircuit import agent
from ocdcircuit import solver

HERE = os.path.dirname(os.path.abspath(__file__))


def golden(b: object) -> dict[str, tuple[float, float]]:
    from ocdcircuit.circuit import Board
    assert isinstance(b, Board)
    return {str(c["ref"]): (float(c["x"]), float(c["y"]))
            for c in b.constraints if c.get("t") == "fixed"}


def main() -> None:
    seeds, iters = int(sys.argv[1]) if len(sys.argv) > 1 else 1, \
        int(sys.argv[2]) if len(sys.argv) > 2 else 50
    b = agent.loads(open(os.path.join(HERE, "monster6502.ocd")).read(), base=HERE)
    g = golden(b)
    # release fixed parts so the placer actually works (golden kept for scoring)
    b.constraints = [c for c in b.constraints if c.get("t") != "fixed"]
    t = time.perf_counter()
    cost = b.place(seeds=seeds, iters=iters)
    dt = time.perf_counter() - t
    placed_wl = solver.wirelength(b)
    disp = sum(abs(p.x - g[r][0]) + abs(p.y - g[r][1])
               for r, p in b.parts.items() if r in g) / max(1, len(g))
    errs = b.check()["errors"]
    ov = sum(1 for e in errs if str(e).startswith("overlap"))
    print(f"seeds={seeds} iters={iters} time={dt:.1f}s cost={cost:.0f}")
    print(f"placed_wirelength={placed_wl:.0f} mean_displacement={disp:.2f}mm "
          f"overlaps={ov} errors={len(errs)}")


if __name__ == "__main__":
    main()
