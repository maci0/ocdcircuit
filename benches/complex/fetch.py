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
    if "--help" in argv or "-h" in argv:
        print("usage: python -m benches.complex.fetch [--board ulx3s|hackrf|virgo|cm4]\n"
              "  Download the .kicad_pcb sources for the selected board(s) into\n"
              "  benches/complex/ (default: all four). Cached files are kept.\n"
              "  URLs and licenses: see SOURCES.md in the same directory.")
        return
    want = argv[argv.index("--board") + 1] if "--board" in argv else None
    if want and want not in BOARDS:
        raise SystemExit(f"unknown board {want!r} (pick one of {sorted(BOARDS)})")
    items = [(want, BOARDS[want][0], BOARDS[want][1])] if want else \
        [(n, u, f) for n, (u, f) in BOARDS.items()]
    for name, url, fn in items:
        dest = os.path.join(HERE, fn)
        if os.path.exists(dest):
            print(f"{name}: cached ({os.path.getsize(dest) // 1024}KB)")
            continue
        print(f"{name}: downloading {url} ...")
        # urlretrieve has no timeout — a hung mirror would stall the bench forever.
        # Write via temp+replace so a timeout cannot leave a truncated dest that
        # the next run would treat as a finished cache.
        req = urllib.request.Request(url)
        tmp = dest + ".part"
        try:
            with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        print(f"{name}: saved {os.path.getsize(dest) // 1024}KB")


if __name__ == "__main__":
    main()
