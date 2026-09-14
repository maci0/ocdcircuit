# ADR-0003 — DRC + JLCPCB export thresholds

## Decision
DRC (`ocdcircuit/drc.py`) enforces JLC 2-layer capabilities with margin:
min trace/space 0.15mm, min drill 0.2mm, annular 0.15mm, edge 0.3mm.
Errors: shorts, overlaps, width, drill, floating pins, edge.
Clearance-only hits are warnings (naive router, ADR-0002).
Export (`ocdcircuit/export.py`) writes minimal RS-274X (copper/mask/silk/
outline), Excellon drill, BOM + CPL CSVs — hand-rolled, no Gerber lib.

## Consequences
Demo must reach zero *errors*. Warnings are the visible router-debt meter.

## Update (fab profiles, Eagle/EasyEDA, lint breadth)
- Six fab profiles (`fab.py`: jlc/jlc-flex/pcbway/oshpark/seeed/aisler);
  `Board.fab` selects, DRC reports which fab it checked.
- Import: Eagle `.brd` boards, EasyEDA Std JSON (footprint + PCB docs);
  export adds EasyEDA Std PCB JSON. All as `importer`/`exporter` plugins.

## Update (schematic + Eagle export, bundle)
- `exporter:kicad-sch` writes `.kicad_sch` from the shared `sch_layout`
  grid (per-pin-count box symbols, segmented rails, grid-exact pins) —
  `kicad-cli sch erc` reports 0 errors on the demo board.
- `exporter:eagle` writes `.brd` XML; verified by export→import round-trip
  (refs + nets identical through `importer:eagle-brd`).
- `exporter:bundle` zips Gerbers + drill + BOM + CPL + `.kicad_pcb` +
  `.kicad_sch` + `.brd`: one file to fab.
- Lint (`lint.py`) covers the full constraint grammar (net/part refs,
  numeric ranges, layer bounds), dedupes, never crashes on junk input.

## Update (pours, apertures, DRC hardening)
- `pour NET on L` renders real copper: negative Gerber planes (flood inset
  by fab edge rule + rect cutouts) + KiCad refillable zones; routers skip
  poured nets, DRC exempts plane copper, `pour-isolated` errors on
  keepout-stranded pads. Visible in PNG/canvas/STATUS.md.
- Gerber apertures per width/size (traces, mask, paste) — the single-blind-
  aperture era (0.4/0.5/0.4 for everything) under-built power, mask, paste.
- DRC: segment-intersection catches X-crossing shorts (degenerate-safe);
  ERC shorts custom `power`-constraint rails; lint validates `nc` refs.
- BOM groups by LCSC (no wrong-reel merges); CPL excludes DNP rows.
- `Board.check_all` merges fab+erc+flex profiles for ocd/MCP/studio.
