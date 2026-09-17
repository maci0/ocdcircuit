"""Complex-bench converter: .kicad_pcb → .ocd via the shared pcb importer.

Feature tags (derived, not just part count — that's discrete6502's job):
power classes (wide nets), length-match candidates (shared-pin fanout
groups on the same bus), diff-pair candidates (2-pin nets sharing both
endpoints' parts), pour candidates (highest-fanout GND-like net), dense
zones (placement DRC errors at author positions).

Usage: python -m benches.complex.convert [--board ulx3s|hackrf|virgo|cm4]
Writes <board>.ocd next to the fetched .kicad_pcb (gitignored).
"""
from __future__ import annotations
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = {
    "ulx3s": "ulx3s.kicad_pcb",
    "hackrf": "hackrf-one.kicad_pcb",
    "virgo": "virgo-rpl-uph.kicad_pcb",
    "cm4": "cm4-baseboard.kicad_pcb",
}


def _tags(b: object) -> list[str]:
    from ocdcircuit.circuit import Board
    assert isinstance(b, Board)
    out: list[str] = []
    big = sorted(((n, len(v.pins)) for n, v in b.nets.items()),
                 key=lambda t: -t[1])[:5]
    if big and big[0][1] >= 50:
        out.append(f"power {big[0][0]}")
        for n, _ in big[1:3]:
            if re.fullmatch(r"[A-Za-z0-9_+.-]+", n):
                out.append(f"power {n}")
                break
    gnd = [n for n, v in b.nets.items()
           if n.upper() in ("GND", "GNDD", "VSS") and len(v.pins) >= 20]
    if gnd:
        out.append(f"pour {gnd[0]} on 0")
    # bus groups: nets sharing a prefix + digit suffix (SDRAM_D0..D15)
    bus: dict[str, list[str]] = {}
    for n in b.nets:
        m = re.match(r"(.+?)[_]?(\d+)$", n)
        if m and len(b.nets[n].pins) >= 2:
            bus.setdefault(m.group(1), []).append(n)
    for pre, ns in sorted(bus.items(), key=lambda t: -len(t[1])):
        if len(ns) >= 4:
            out.append(f"match {' '.join(sorted(ns)[:4])}")
            break
    # diff candidates: 2-pin nets whose parts also share another 2-pin net
    parts_of = {n: frozenset(r for r, _ in v.pins) for n, v in b.nets.items()}
    twos = [n for n, v in b.nets.items() if len(v.pins) == 2]
    seen: set[frozenset[str]] = set()
    for i, a in enumerate(twos):
        if parts_of[a] in seen:
            continue
        for c in twos[i + 1:]:
            if parts_of[c] == parts_of[a]:
                out.append(f"diff {a} {c} gap 0.3")
                seen.add(parts_of[a])
                break
        if len([l for l in out if l.startswith("diff ")]) >= 2:
            break
    return out


def convert(board: str) -> str:
    from ocdcircuit.circuit import Board
    from ocdcircuit import agent
    from ocdcircuit import footprint as _fp
    src = os.path.join(HERE, FILES[board])
    if not os.path.exists(src):
        raise SystemExit(f"{src} missing — run: python -m benches.complex.fetch --board {board}")
    b = Board(board)
    b.import_fp("pcb", path=src)
    for t in _tags(b):
        try:
            b.constrain(_parse_tag(t))
        except ValueError:
            pass  # candidate nets may not survive sanitize — bench, not proof
    # materialize rebuilt customs as fp/ sidecars so the .ocd reloads
    # standalone (importer geometry is in-memory only, dumps needs fp_src)
    fpdir = os.path.join(HERE, "fp", board)
    os.makedirs(fpdir, exist_ok=True)
    for name, meta in b.custom_fp.items():
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "X"
        with open(os.path.join(fpdir, f"{safe}.fp"), "w") as f:
            f.write(_fp.dumps(name, meta) + "\n")
        b.fp_src[name] = f"fp/{board}/{safe}.fp"
    text = agent.dumps(b)
    dest = os.path.join(HERE, f"{board}.ocd")
    with open(dest, "w") as f:
        f.write(text)
    pins = sum(len(v.pins) for v in b.nets.values())
    print(f"{board}: parts={len(b.parts)} nets={len(b.nets)} pins={pins} "
          f"size={b.width:.0f}x{b.height:.0f} layers={b.layers} → {dest}")
    return dest


def _parse_tag(t: str) -> dict[str, object]:
    w = t.split()
    if w[0] == "power":
        return {"t": "power", "nets": w[1:]}
    if w[0] == "pour":
        return {"t": "pour", "net": w[1], "layer": int(w[3])}
    if w[0] == "match":
        return {"t": "match", "nets": w[1:]}
    if w[0] == "diff":
        return {"t": "diff", "p": w[1], "n": w[2], "gap": float(w[4])}
    raise ValueError(t)


def main() -> None:
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print("usage: python -m benches.complex.convert [--board ulx3s|hackrf|virgo|cm4]\n"
              "  Convert the fetched .kicad_pcb sources to .ocd bench boards\n"
              "  (default: all four). Requires python -m benches.complex.fetch first.")
        return
    want = argv[argv.index("--board") + 1] if "--board" in argv else None
    for board in [want] if want else sorted(FILES):
        if board not in FILES:
            raise SystemExit(f"unknown board {board!r}")
        convert(board)


if __name__ == "__main__":
    main()
