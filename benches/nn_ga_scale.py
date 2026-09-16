"""Reproduce the nn-ga.md synthetic scale table (stdlib only).

Methodology (matches the brief's footnote / docs/nn-ga.md):
  - parts: R0805; board side = 20·n^0.4 mm (n=50 ≈ 96×96)
  - timing columns: sparse-chain nets (i connected to i+1)
  - dense-net overlaps: n nets × 4-random-parts (seed 7)
  - place: forced diffusion seeds=1 iters=50 seed=0
  - routed-DRC via lroute

Run: python -m benches.nn_ga_scale
"""
from __future__ import annotations

import random
import time

from ocdcircuit import agent
from ocdcircuit.circuit import Board


SIZES = (20, 100, 300, 1000)


def _board_side(n: int) -> float:
    # Fixed area growth slower than parts: force contention at large n.
    # Calibrated so n=50 ≈ 100×100 (brief maze note) and n=1000 packs hard.
    return float(max(40.0, 20.0 * (n ** 0.4)))


def _sparse_src(n: int) -> str:
    side = _board_side(n)
    lines = [f"board syn{n} {side:.1f}x{side:.1f} 2L"]
    for i in range(n):
        lines.append(f"part R{i} R0805 10k")
    for i in range(n - 1):
        lines.append(f"net N{i}: R{i}.1 R{i + 1}.1")
    return "\n".join(lines) + "\n"


def _dense_src(n: int, seed: int = 7) -> str:
    side = _board_side(n)
    lines = [f"board den{n} {side:.1f}x{side:.1f} 2L"]
    for i in range(n):
        lines.append(f"part R{i} R0805 10k")
    rng = random.Random(seed)
    # n nets × 4 random pins (seed 7) — contention rises with n.
    for k in range(n):
        picks = rng.sample(range(n), min(4, n))
        pins = " ".join(f"R{i}.1" for i in picks)
        lines.append(f"net D{k}: {pins}")
    return "\n".join(lines) + "\n"


def _overlap_count(board: Board) -> int:
    errs = board.check()["errors"]
    assert isinstance(errs, list)
    return sum(1 for e in errs if str(e).startswith("overlap"))


def main() -> None:
    print("| n | place (seeds=1, iters=50) | DRC (no traces) | DRC (routed) | "
          "dense-net overlaps | sparse overlaps |")
    print("|---|---|---|---|---|---|")
    for n in SIZES:
        b = agent.loads(_sparse_src(n))
        t0 = time.perf_counter()
        # Force diffusion: Board.place auto-selects multilevel at n>=1000.
        b.place(key="diffusion", seeds=1, iters=50, seed=0)
        place_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        ov_sparse = _overlap_count(b)
        b.check()
        drc_s = time.perf_counter() - t0
        routed_s: float | None = None
        if n <= 1000:
            t0 = time.perf_counter()
            b.route_board(key="lroute")
            b.check()
            routed_s = time.perf_counter() - t0
        d = agent.loads(_dense_src(n, seed=7))
        d.place(key="diffusion", seeds=1, iters=50, seed=0)
        ov_dense = _overlap_count(d)
        place_cell = f"{place_s:.2f} s"
        drc_cell = f"{drc_s:.2f} s"
        routed_cell = "—" if routed_s is None else f"{routed_s:.2f} s"
        print(f"| {n} | {place_cell} | {drc_cell} | {routed_cell} | "
              f"{ov_dense} | {ov_sparse} |")


if __name__ == "__main__":
    main()
