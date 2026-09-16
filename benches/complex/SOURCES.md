# Complex bench: production open-hardware boards as feature tests

discrete6502 wins raw part count (5420 parts). These win feature
coverage: real stackups (4–10L), BGA fanout, DDR length-matching,
RF pours/keepouts, dense multi-sheet hierarchy. Fetched + converted,
never committed (same rule as discrete6502 raw JSON).

## Boards

- `ulx3s.kicad_pcb` — [ULX3S](https://github.com/ulx3s/ulx3s) ECP5 FPGA
  + SDRAM + GPDI/HDMI (CC BY-SA; board file: CERN-OHL spirit, see repo
  LICENSE.md). 238 parts / 348 nets / 6L, 94x51mm. Stresses: BGA
  fanout, `diff` (HDMI), `match` (SDRAM bus), 6-layer route.
- `hackrf-one.kicad_pcb` — [HackRF One](https://github.com/greatscottgadgets/hackrf)
  SDR (GPLv2+ / hardware CC BY-SA — bench use is fine; do not ship
  derived files commercially). 437 parts / 370 nets / 4L, 120x75mm.
  Stresses: RF `pour` + keepouts, dense placement, silk at density.
- `virgo-rpl-uph.kicad_pcb` — [System76 Virgo](https://github.com/system76/virgo)
  laptop motherboard (CC BY-SA 4.0). 591 parts / 1382 nets / 10L,
  358x126mm. Stresses: everything — `diff` (USB/PCIe/HDMI), `match`
  (DDR5), `power` classes, `pour`, hierarchy-shaped blocks.
- `cm4-baseboard.kicad_pcb` — [Antmicro CM4 baseboard](https://github.com/antmicro/cm4-baseboard)
  (Apache-2.0). 516 parts / 506 nets / 6L, 107x68mm. Stresses:
  `diff` + `match` (CSI/DSI, GbE, NVMe, USB-C), edge connectors.

## Files here (generated + fetched, all gitignored)

- `fetch.py` — download the four `.kicad_pcb` sources
  (`python -m benches.complex.fetch [--board ulx3s]`)
- `convert.py` — `.kicad_pcb` → `.ocd` via the shared `pcb` importer,
  plus derived feature tags (`power`/`pour`/`match`/`diff` candidates
  from net shapes; `python -m benches.complex.convert`)
- `bench.py` — every board loads + places + routes, reports load
  fidelity / cost / wirelength / route yield / DRC
  (`python -m benches.complex.bench [--board ulx3s] [seeds] [iters]`)
- `<board>.ocd` — converted output (regenerate, don't hand-edit)

## Importer fixes these boards forced (in `ocdcircuit/foreign.py`)

1. Escaped quotes in s-expr strings (`30u\" gold plating` — ulx3s).
2. `;` inside quoted strings treated as comment start (TSOPII
   `descr "...; 54 leads; ..."` swallowed 228 ulx3s footprints).
3. `fp_text reference` is the designator in `.kicad_pcb` (not
   `property Reference`); sanitize refs/nets to `\w+` at the import
   boundary (`REF**`, `TESTPOINT-30MIL-MASKONLY`, non-breaking spaces).
4. `(drill oval w h)` slot drills (virgo/cm4 USB-C shells).
5. Layer count from the `(layers ...)` decl (virgo is 10L, not 2).
6. Board size from the Edge.Cuts outline incl. `fp_line` + arcs
   (virgo keeps its outline inside a footprint); parts recentered.

## License note

Fetched `.kicad_pcb` files keep their upstream licenses (see links
above). Converted `.ocd` files are mechanical format conversions —
same licenses apply. Bench use only.
