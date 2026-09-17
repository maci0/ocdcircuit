"""Exact non-overlap placement pilot (stdlib) — bounds CP-SAT scope.

OR-Tools is not a project dependency (zero-dep axiom). This answers Open Q1
("at what n does multi-start diffusion lose to exact on a linearized model?")
with three regimes a CP-SAT AddNoOverlap2D pilot would also hit:

  1. feasibility on a loose board (any non-overlap packing)
  2. prove-infeasible on an undersized board
  3. feasibility + star-model wirelength upper bound (exact search with cost)

Run: python -m benches.exact_overlap_pilot
"""
from __future__ import annotations

import math
import time


def _feas(n: int, board: float, part: float, step: float, deadline: float,
          nets: list[tuple[int, int]] | None = None,
          wl_cap: float | None = None) -> tuple[str, float, int]:
    """Return (yes|no|timeout, seconds, nodes)."""
    cells = math.floor((board - part) / step) + 1
    if cells < 1:
        return "no", 0.0, 0
    half = part / 2.0
    xs: list[float] = []
    ys: list[float] = []
    nodes = 0
    t0 = time.perf_counter()

    def wl() -> float:
        if not nets:
            return 0.0
        s = 0.0
        for a, b in nets:
            s += abs(xs[a] - xs[b]) + abs(ys[a] - ys[b])
        return s

    def overlap(i: int, x: float, y: float) -> bool:
        for j in range(i):
            if abs(x - xs[j]) < part and abs(y - ys[j]) < part:
                return True
        return False

    def rec(i: int) -> bool:
        nonlocal nodes
        if time.perf_counter() - t0 >= deadline:
            raise TimeoutError
        if i == n:
            return wl_cap is None or wl() <= wl_cap
        for cx in range(cells):
            for cy in range(cells):
                if time.perf_counter() - t0 >= deadline:
                    raise TimeoutError
                nodes += 1
                x = half + cx * step
                y = half + cy * step
                if overlap(i, x, y):
                    continue
                xs.append(x)
                ys.append(y)
                if rec(i + 1):
                    return True
                xs.pop()
                ys.pop()
        return False

    ok = False
    try:
        ok = rec(0)
    except TimeoutError:
        pass
    secs = time.perf_counter() - t0
    if ok:
        return "yes", secs, nodes
    if secs >= deadline:
        return "timeout", secs, nodes
    return "no", secs, nodes


def main() -> None:
    part, step, deadline = 2.0, 1.0, 3.0
    print("## Feasibility (loose vs undersized)")
    print("| n | regime | board mm | result | time s | nodes |")
    print("|---|---|---|---|---|---|")
    for n in (5, 10, 15, 20):
        loose = math.ceil(math.sqrt(n)) * part * 1.6
        tiny = math.ceil(math.sqrt(n)) * part * 0.7  # cannot fit
        for label, side in (("loose", loose), ("undersized", tiny)):
            flag, secs, nodes = _feas(n, side, part, step, deadline)
            print(f"| {n} | {label} | {side:.1f} | {flag} | {secs:.3f} | {nodes} |")

    print()
    print("## Feasibility + chain-WL cap (exact cost search)")
    print("| n | board mm | wl_cap | result | time s | nodes |")
    print("|---|---|---|---|---|---|")
    for n in (5, 8, 10, 12):
        side = math.ceil(math.sqrt(n)) * part * 2.0
        nets = [(i, i + 1) for i in range(n - 1)]
        # cap = Manhattan length of a straight row (optimistic)
        row = (n - 1) * part
        for cap in (row * 1.5, row * 3.0, None):
            cap_s = "none" if cap is None else f"{cap:.1f}"
            flag, secs, nodes = _feas(n, side, part, step, deadline, nets, cap)
            print(f"| {n} | {side:.1f} | {cap_s} | {flag} | {secs:.3f} | {nodes} |")


if __name__ == "__main__":
    main()
