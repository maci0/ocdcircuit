"""Complex-bench harness: every bench board loads + places + routes.

Unlike discrete6502 (golden WL comparison), these boards have author
routing, not golden placement — so the bench reports load fidelity
(parts/nets/pins surviving import), place cost + wirelength, route
segments, and DRC errors. Run:
python -m benches.complex.bench [--board ulx3s|...] [seeds] [iters]
"""
from __future__ import annotations
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run(board: str, seeds: int, iters: int) -> None:
    from ocdcircuit import agent
    from ocdcircuit import solver
    from benches.complex.convert import FILES
    src = os.path.join(HERE, f"{board}.ocd")
    if not os.path.exists(src):
        if os.path.exists(os.path.join(HERE, FILES[board])):
            from benches.complex.convert import convert
            src = convert(board)
        else:
            raise SystemExit(f"{board}.ocd missing — run: python -m benches.complex.fetch --board {board}")
    b = agent.loads(open(src).read(), base=HERE)
    pins = sum(len(v.pins) for v in b.nets.values())
    print(f"{board}: load parts={len(b.parts)} nets={len(b.nets)} pins={pins} "
          f"size={b.width:.0f}x{b.height:.0f} layers={b.layers}")
    t = time.perf_counter()
    cost = b.place("diffusion", seeds=seeds, iters=iters)
    dt = time.perf_counter() - t
    print(f"{board}: place seeds={seeds} iters={iters} time={dt:.1f}s "
          f"cost={cost:.0f} wl={solver.wirelength(b):.0f}")
    t = time.perf_counter()
    nseg = b.route_board()
    rdt = time.perf_counter() - t
    errs = b.check()["errors"]
    assert isinstance(errs, list)
    print(f"{board}: route time={rdt:.1f}s segments={nseg} "
          f"drc_errors={len(errs)}")


def main() -> None:
    from benches.complex.convert import FILES
    argv = sys.argv[1:]
    board = argv[argv.index("--board") + 1] if "--board" in argv else None
    args = [a for i, a in enumerate(argv)
            if not (a == "--board" or (i > 0 and argv[i - 1] == "--board"))]
    seeds = int(args[0]) if len(args) > 0 else 1
    iters = int(args[1]) if len(args) > 1 else 2
    for b in [board] if board else sorted(FILES):
        if b not in FILES:
            raise SystemExit(f"unknown board {b!r}")
        run(b, seeds, iters)


if __name__ == "__main__":
    main()
