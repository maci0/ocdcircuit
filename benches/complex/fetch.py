"""Fetch the complex-bench .kicad_pcb sources (gitignored, like discrete6502
raw JSON). URLs + licenses: see SOURCES.md.

Usage: python -m benches.complex.fetch [--board ulx3s|hackrf|virgo|cm4]
"""
from __future__ import annotations
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

BOARDS = {
    "ulx3s": ("https://raw.githubusercontent.com/ulx3s/ulx3s/master/ulx3s.kicad_pcb",
              "ulx3s.kicad_pcb"),
    "hackrf": ("https://raw.githubusercontent.com/greatscottgadgets/hackrf/master/"
               "hardware/hackrf-one/hackrf-one.kicad_pcb",
               "hackrf-one.kicad_pcb"),
    "virgo": ("https://raw.githubusercontent.com/system76/virgo/main/"
              "pcb-rpl-uph/virgo-rpl-uph.kicad_pcb",
              "virgo-rpl-uph.kicad_pcb"),
    "cm4": ("https://raw.githubusercontent.com/antmicro/cm4-baseboard/main/"
            "cm4-baseboard.kicad_pcb",
            "cm4-baseboard.kicad_pcb"),
}


def main() -> None:
    argv = sys.argv[1:]
    want = argv[argv.index("--board") + 1] if "--board" in argv else None
    items = [(want, BOARDS[want][0], BOARDS[want][1])] if want else \
        [(n, u, f) for n, (u, f) in BOARDS.items()]
    if want and want not in BOARDS:
        raise SystemExit(f"unknown board {want!r} (pick one of {sorted(BOARDS)})")
    for name, url, fn in items:
        dest = os.path.join(HERE, fn)
        if os.path.exists(dest):
            print(f"{name}: cached ({os.path.getsize(dest) // 1024}KB)")
            continue
        print(f"{name}: downloading {url} ...")
        urllib.request.urlretrieve(url, dest)
        print(f"{name}: saved {os.path.getsize(dest) // 1024}KB")


if __name__ == "__main__":
    main()
