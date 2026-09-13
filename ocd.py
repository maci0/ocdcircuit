"""ocd CLI: build any .ocd file with zero Python. Stdlib only."""
import os, sys


def main(argv):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ocdcircuit import agent
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print("usage: ocd <circuit.ocd>  # places, routes, DRCs, exports next to the file")
        return 1
    src = argv[1]
    try:
        b = agent.loads(open(src).read())
        for p in b.parts.values():  # surface typos early, not mid-solve
            if p.fp not in b._lib():
                raise ValueError(f"{p.ref}: unknown footprint {p.fp!r}")
    except (OSError, ValueError, KeyError) as e:
        print(f"ocd: {e}")
        return 1
    c = b.place()
    n = b.route_board()
    r = b.check()
    out = os.path.join(os.path.dirname(os.path.abspath(src)), "out")
    files = b.export("jlc", outdir=out) + b.export("ocd", outdir=out)
    open(os.path.join(out, b.name + ".svg"), "w").write(b.render("svg"))
    print(f"{b.name}: cost={c:.1f} segs={n} errors={r['errors']} warnings={len(r['warnings'])}")
    print(f"{len(files)} fab files + svg in {out}/")
    if r["errors"]:
        return 2
    if r["warnings"]:
        for w in sorted(set(r["warnings"]))[:5]:
            print(f"warn: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
