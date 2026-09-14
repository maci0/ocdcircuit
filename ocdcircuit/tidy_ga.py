"""Tidy-GA placer: evolve placements for neatness (docs/tidy-metrics.md).

Genome = full position vector; uniform per-part crossover (coordinate-blend
crossover on raw (x,y) stitches two bad halves — docs/nn-ga.md); mutation =
short diffusion burst (evolution proposes, decoder disposes). Fitness = tidy
components + DRC errors at veto scale. One undoable effect; evals run under
snapshot/rollback like candidates()/wiremask.
"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING, cast

from .types import XY

if TYPE_CHECKING:
    from .circuit import Board

Pos = dict[str, XY]


def fitness(board: Board, weights: dict[str, float] | None = None) -> float:
    """Lower = tidier. Only placement-owned terms: T1 (same-layer
    crossings), T7/T8/T9 (regularity), wirelength cost, DRC errors at veto
    scale. Router-owned T2 (bends)/T4 (vias), schematic T13 and silk T14
    stay in the report — optimizing them from placement is noise. Default
    weights are the doc's illustrative vector, stated here, used only
    inside one board (never compare across boards)."""
    from . import score as _score
    from .solver import cost as _cost
    w = weights or {"T1": 3.0, "T7": -20.0, "T8": 30.0, "T9": -10.0}
    t = _score.tidy(board)
    f = 0.0
    t1 = t["T1_crossings"]
    f += w["T1"] * (t1 if isinstance(t1, (int, float)) else 0)
    t7 = t["T7_alignment"]
    f += w["T7"] * (t7 if isinstance(t7, float) else 0)
    t8 = t["T8_gridsnap_mm"]
    f += w["T8"] * (t8 if isinstance(t8, float) else 0)
    t9 = t["T9_spacing"]
    f += w["T9"] * (t9 if isinstance(t9, float) else 0)
    errs = cast(list[object], board.check()["errors"])
    f += 1e6 * len(errs) + _cost(board) * 0.01
    return f


def _decode(board: Board, pos: Pos, iters: int, seed: int) -> None:
    """Greedy decoder: stamp positions, refine in place (no re-init —
    the genome is inherited, not re-rolled), min-conflicts repair."""
    from .solver import _diffuse_once, _repair
    for r, (x, y) in pos.items():
        if r in board.parts:
            board.parts[r].x, board.parts[r].y = x, y
    _diffuse_once(board, iters, seed, init=False)
    _repair(board)


def tidy_ga(board: Board, pop: int = 8, gen: int = 6, seed: int = 0,
            iters: int = 60, route: bool = True,
            weights: dict[str, float] | None = None) -> float:
    """Evolve pop position vectors × gen generations, keep best. Whole run
    is one undoable effect; returns best fitness. Route=True maze-routes
    each eval so trace metrics (T1/T2) actually discriminate."""
    from . import maze as _maze
    rng = random.Random(seed)
    refs = list(board.parts)
    snap_pos = {r: (p.x, p.y) for r, p in board.parts.items()}
    old_traces = list(board.traces)
    snap = board.ctx.snapshot()
    best: tuple[float, Pos] = (float("inf"), dict(snap_pos))

    def _eval(pos: Pos, s: int) -> tuple[float, Pos]:
        board.ctx.rollback(snap)
        board.traces = list(old_traces)
        _decode(board, pos, iters, s)
        if route:
            _maze.maze(board)
        f = fitness(board, weights)
        return f, {r: (board.parts[r].x, board.parts[r].y) for r in refs
                   if r in board.parts}

    try:
        from .solver import optimize
        pop_pos: list[Pos] = []
        for i in range(pop):  # seed from diffusion winners, not raw noise
            board.ctx.rollback(snap)
            optimize(board, seeds=1, iters=max(50, iters), seed=seed + i)
            pop_pos.append({r: (board.parts[r].x, board.parts[r].y) for r in refs})
        for g in range(gen):
            scored = sorted((_eval(p, seed + 1000 * (g + 1) + i)
                             for i, p in enumerate(pop_pos)),
                            key=lambda t: t[0])
            if scored[0][0] < best[0]:
                best = scored[0]
            elite = [p for _, p in scored[: max(2, pop // 3)]]
            nxt = list(elite)
            while len(nxt) < pop:
                a, b = rng.choice(elite), rng.choice(elite)
                # ponytail: uniform per-part inheritance; blend crossover
                # destroys placements (two good halves = two bad halves)
                child = {r: (a[r] if rng.random() < 0.5 else b[r])
                         for r in refs if r in a and r in b}
                for r in rng.sample(refs, max(1, len(refs) // 4)):
                    if r in child:
                        child[r] = (rng.uniform(0, board.width), rng.uniform(0, board.height))
                nxt.append(child)
            pop_pos = nxt
    finally:
        board.ctx.rollback(snap)
    # rebuild the winner for real (decode + route), then fold its own
    # emits into the single effect below — same shape as candidates().
    _decode(board, dict(best[1]), iters, seed)
    if route:
        _maze.maze(board)
    final = {r: (p.x, p.y) for r, p in board.parts.items()}
    final_traces = list(board.traces)
    board.ctx.rollback(snap)
    board.traces = list(old_traces)
    for r, (x, y) in snap_pos.items():
        if r in board.parts:
            board.parts[r].x, board.parts[r].y = x, y

    def _do() -> None:
        for r, (x, y) in final.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y
        board.traces[:] = final_traces

    def _undo() -> None:
        for r, (x, y) in snap_pos.items():
            if r in board.parts:
                board.parts[r].x, board.parts[r].y = x, y
        board.traces[:] = old_traces

    board.emit(_do, _undo)
    return best[0]


if __name__ == "__main__":  # python -m ocdcircuit.tidy_ga
    from ocdcircuit import agent
    b = agent.loads(open("boards/blinky_555.ocd").read(), base="boards")
    b.place(seeds=2, iters=100)
    b.route_board()
    f0 = fitness(b)
    snap = b.ctx.snapshot()
    fb = tidy_ga(b, pop=6, gen=4, iters=40)
    assert b.check()["errors"] == [], b.check()["errors"]
    assert fb <= f0, f"GA regressed: {f0:.1f} -> {fb:.1f}"
    assert b.ctx.snapshot() - snap == 1, "GA must leave exactly one effect"
    b.ctx.undo()
    assert abs(fitness(b) - f0) < 1e-6, "GA undo must restore fitness"
    print(f"blinky tidy-GA: {f0:.1f} -> {fb:.1f}")
    print("TIDY-GA OK")
