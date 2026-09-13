"""ocd CLI: dispatcher for board projects. Stdlib only.

    ocd new <dir>            scaffold board.ocd + README + STATUS
    ocd run <circuit.ocd>    full pipeline: place → route → DRC → fab
    ocd status <circuit.ocd> refresh STATUS.md (score, DRC, ERC, sim)
    ocd diff <a.ocd> <b.ocd> what changed: parts, nets, size, constraints
    ocd score <circuit.ocd>  OCD neatness 0-100 + breakdown (no mutation)

Global flags (run/score): --fab --placer --router --sim. `ocd <file>` = run.
"""
from __future__ import annotations
import os
import sys
from typing import Callable, cast

from ocdcircuit.circuit import Board

USAGE = """usage:
  ocd new <dir>                 scaffold a board project
  ocd run [--fab F] [--placer P] [--router R] [--sim dc|tran] <circuit.ocd>
  ocd status <circuit.ocd>      refresh STATUS.md next to the file
  ocd diff <a.ocd> <b.ocd>       parts/nets/size/constraints delta
  ocd score <circuit.ocd>        OCD neatness 0-100 (read-only)
  ocd <circuit.ocd>              shorthand for run"""


def _boot() -> object:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ocdcircuit import agent as _a
    return _a


def _load(agent: object, src: str) -> Board:
    loads = cast(object, getattr(agent, "loads"))
    from typing import Callable, Any
    fn = cast(Callable[..., Board], loads)
    return fn(open(src).read(), base=os.path.dirname(os.path.abspath(src)))


def cmd_new(args: list[str]) -> int:
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print("usage: ocd new <dir>")
        return 1
    d = args[0]
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(os.path.abspath(d)).replace("-", "_")
    board = os.path.join(d, f"{name}.ocd")
    if not os.path.exists(board):
        with open(board, "w") as f:
            f.write(f"board {name} 40x30 2L\n"
                    f"part R1 R0805 10k\npart C1 C0805 100n\n"
                    f"net N: R1.2 C1.2\nnet GND: R1.1 C1.1\nfix R1 at 3 5\n")
    readme = os.path.join(d, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w") as f:
            f.write(f"# {name}\n\n`ocd run {name}.ocd` → `out/` fab package.\n"
                    f"`ocd status {name}.ocd` refreshes STATUS.md.\n")
    print(f"new: {board}")
    return 0


def _flags(args: list[str]) -> tuple[str, str | None, str | None, str | None, list[str]]:
    fab: str = "jlc"
    placer: str | None = None
    router: str | None = None
    simwhat: str | None = None
    rest = list(args)
    while len(rest) >= 2 and rest[0] in ("--fab", "--placer", "--router", "--sim"):
        if rest[0] == "--fab":
            fab = rest[1]
        elif rest[0] == "--placer":
            placer = rest[1]
        elif rest[0] == "--sim":
            simwhat = rest[1]
        else:
            router = rest[1]
        rest = rest[2:]
    return fab, placer, router, simwhat, rest


def cmd_run(agent: object, args: list[str]) -> int:  # agent: ocdcircuit.agent
    fab, placer, router, simwhat, rest = _flags(args)
    if len(rest) != 1 or rest[0] in ("-h", "--help"):
        print(USAGE)
        return 1
    src = rest[0]
    try:
        b = _load(agent, src)
        b.fab = fab
    except (OSError, ValueError, KeyError) as e:
        print(f"ocd: {e}")
        return 1
    try:
        c = b.place(placer) if placer else b.place()
        n = b.route_board(router) if router else b.route_board()
    except KeyError as e:
        print(f"ocd: {e}")
        return 1
    r = b.check()
    out = os.path.join(os.path.dirname(os.path.abspath(src)), "out")
    files = (b.export("jlc", outdir=out) + b.export("kicad", outdir=out)
             + b.export("ocd", outdir=out))
    open(os.path.join(out, b.name + ".svg"), "w").write(b.render("svg"))
    open(os.path.join(out, b.name + ".stl"), "w").write(b.render("stl"))
    errors = cast(list[object], r["errors"])
    warnings = cast(list[object], r["warnings"])
    print(f"{b.name}: cost={c:.1f} segs={n} errors={errors} warnings={len(warnings)}")
    print(f"{len(files)} fab files + svg + stl in {out}/")
    if simwhat:
        try:
            res = b.simulate(what=simwhat)
        except (ValueError, KeyError) as e:
            print(f"ocd: sim: {e}")
            return 1
        if "nets" in res:
            nets = cast(dict[str, object], res["nets"])
            cells = []
            for k, v in sorted(nets.items()):
                assert isinstance(v, (int, float))
                cells.append(f"{k}={float(v):.3f}V")
            print("sim dc: " + " ".join(cells))
        else:
            waves = cast(dict[str, list[float]], res["waves"])
            for k, v in sorted(waves.items()):
                print(f"sim {k}: final={v[-1]:.3f}V min={min(v):.3f} max={max(v):.3f} ({len(v)} pts)")
    if errors:
        return 2
    seen: set[str] = set()
    for w in warnings:
        ws = str(w)
        if ws not in seen:
            seen.add(ws)
        if len(seen) <= 5:
            print(f"warn: {ws}")
    return 0


def cmd_status(agent: object, args: list[str]) -> int:
    fab, _placer, _router, _sim, rest = _flags(args)
    if len(rest) != 1:
        print("usage: ocd status [--fab F] <circuit.ocd>")
        return 1
    src = rest[0]
    try:
        b = _load(agent, src)
        b.fab = fab
    except (OSError, ValueError, KeyError) as e:
        print(f"ocd: {e}")
        return 1
    from ocdcircuit.score import score as _score
    s = _score(b)
    drc = b.check()
    erc = b.check("erc")
    simline = ""
    if any(c.get("t") == "sim" for c in b.constraints):
        try:
            nets = b.simulate()["nets"]
            assert isinstance(nets, dict)
            simline = "sim: " + " ".join(
                f"{k}={float(v):.2f}V" for k, v in sorted(nets.items())
                if isinstance(v, (int, float))) + "\n"
        except (ValueError, KeyError):
            simline = "sim: error\n"
    derr = cast(list[object], drc["errors"])
    dwarn = cast(list[object], drc["warnings"])
    eerr = cast(list[object], erc["errors"])
    ewarn = cast(list[object], erc["warnings"])
    doc = (f"# STATUS — {b.name}\n\n"
           f"OCD score: {s['total']}/100 ({s['grade']})\n\n"
           f"| check | errors | warnings |\n|---|---|---|\n"
           f"| DRC ({drc.get('fab')}) | {len(derr)} | {len(dwarn)} |\n"
           f"| ERC | {len(eerr)} | {len(ewarn)} |\n\n"
           + ("".join(f"- DRC: {e}\n" for e in derr[:10]))
           + ("".join(f"- ERC: {e}\n" for e in eerr[:10]))
           + (f"{simline}\n" if simline else "")
           + f"parts: {len(b.parts)}, nets: {len(b.nets)}, "
           + f"traces: {len(b.traces)}, layers: {b.layers}\n")
    proj = os.path.dirname(os.path.abspath(src))
    with open(os.path.join(proj, "STATUS.md"), "w") as f:
        f.write(doc)
    print(doc, end="")
    return 0 if not derr and not eerr else 2


def cmd_diff(agent: object, args: list[str]) -> int:
    if len(args) != 2:
        print("usage: ocd diff <a.ocd> <b.ocd>")
        return 1
    try:
        a = _load(agent, args[0])
        b = _load(agent, args[1])
    except (OSError, ValueError, KeyError) as e:
        print(f"ocd: {e}")
        return 1
    from ocdcircuit import diff as _diff
    report = _diff.diff(a, b)
    print(report or "identical")
    return 0


def cmd_score(agent: object, args: list[str]) -> int:
    fab, _placer, _router, _sim, rest = _flags(args)
    if len(rest) != 1:
        print("usage: ocd score [--fab F] <circuit.ocd>")
        return 1
    try:
        b = _load(agent, rest[0])
        b.fab = fab
    except (OSError, ValueError, KeyError) as e:
        print(f"ocd: {e}")
        return 1
    from ocdcircuit.score import score as _score
    s = _score(b)
    sparts = cast(dict[str, float], s["parts"])
    print(f"OCD {s['total']}/100 ({s['grade']})")
    for k, v in sparts.items():
        print(f"  {k}: {v}")
    return 0


def main(argv: list[str]) -> int:
    agent = _boot()
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 1
    if args[0] == "new":
        return cmd_new(args[1:])
    if args[0] == "run":
        return cmd_run(agent, args[1:])
    if args[0] == "status":
        return cmd_status(agent, args[1:])
    if args[0] == "diff":
        return cmd_diff(agent, args[1:])
    if args[0] == "score":
        return cmd_score(agent, args[1:])
    if args[0].startswith("-"):
        print(USAGE)
        return 1
    return cmd_run(agent, args)  # `ocd <file>` = run


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
