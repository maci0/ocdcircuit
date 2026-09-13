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
