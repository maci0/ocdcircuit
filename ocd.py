"""ocd CLI: build any .ocd file with zero Python. Stdlib only."""
from __future__ import annotations
import os
import sys
from typing import cast

USAGE = ("usage: ocd [--fab jlc|pcbway|oshpark|seeed|aisler] "
         "[--placer diffusion|compact|thermal] [--router lroute|maze] <circuit.ocd>")


def main(argv: list[str]) -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ocdcircuit import agent
    fab, placer, router = "jlc", None, None
    args = argv[1:]
    while len(args) >= 2 and args[0] in ("--fab", "--placer", "--router"):
        if args[0] == "--fab":
            fab = args[1]
        elif args[0] == "--placer":
            placer = args[1]
        else:
            router = args[1]
        args = args[2:]
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(USAGE)
        return 1
    src = args[0]
    try:
        b = agent.loads(open(src).read(),
                        base=os.path.dirname(os.path.abspath(src)))
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
