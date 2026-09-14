# Fab capability inventory

`ocdcircuit/fab.py` is the machine-readable copy. This is the human copy.
Standard tiers only, checked Sep 2026 — **re-verify before ordering**,
fabs update specs without telling you.

| | JLCPCB (`jlc`) | JLC flex (`jlc-flex`) | PCBWay (`pcbway`) | OSH Park (`oshpark`) | Seeed (`seeed`) | Aisler (`aisler`) |
|---|---|---|---|---|---|---|
| layers | 1–32 even | 1,2,4 | 1,2,4,6,8,10 | 2,4 | 1,2,4 | 2,4 |
| min trace / space | 0.09 / 0.09 | 0.1 / 0.1 | 0.09 / 0.09 | 0.1524 / 0.1524 | 0.1 / 0.1 | 0.1 / 0.1 |
| min drill (PTH) | 0.2 | 0.1 | 0.2 | 0.508 | 0.3 | 0.3 |
| annular / edge | 0.15 / 0.3 | 0.18 / 0.3 | 0.15 / 0.3 | 0.18 / 0.5 | 0.15 / 0.3 | 0.15 / 0.3 |
| max size | 400×500 | 234×490 | 500×1100 | 200×200 | 400×400 | 300×400 |
| thickness | 0.4–2.0 | 0.07–0.45 | 0.4–2.4 | 1.6 | 0.6–2.0 | 1.0–1.6 |
| finishes | HASL LF ENIG OSP | ENIG | HASL LF ENIG OSP | ENIG | HASL LF ENIG | ENIG |

| | Eurocircuits (`eurocircuits`) | NextPCB (`nextpcb`) | ALLPCB (`allpcb`) | Sierra (`sierra`) | Advanced (`advanced`) |
|---|---|---|---|---|---|
| layers | 2,4,6,8 | 1,2,4,6,8 | 1,2,4,6,8 | 2,4,6,8 | 1,2,4,6,8 |
| min trace / space | 0.15 / 0.15 | 0.09 / 0.09 | 0.1 / 0.1 | 0.09 / 0.09 | 0.09 / 0.09 |
| min drill (PTH) | 0.3 | 0.2 | 0.2 | 0.15 | 0.25 |
| annular / edge | 0.15 / 0.5 | 0.15 / 0.3 | 0.15 / 0.3 | 0.1 / 0.25 | 0.15 / 0.25 |
| max size | 400×500 | 500×1100 | 500×1100 | 457×610 | 533×914 |
| thickness | 0.5–2.4 | 0.4–3.0 | 0.4–3.0 | 0.5–3.2 | 0.8–2.4 |
| finishes | ENIG HASL-LF | HASL LF ENIG OSP | HASL LF ENIG | ENIG HASL-LF | HASL LF ENIG |

Flex extras (`bend`/`stiffener` constraints, `jlc-flex` only): no vias,
parts, or copper pours in dynamic bend areas (`bend-via`/`bend-part`/
`bend-pour` errors — planes crack); bend radius ≥10× finished thickness
dynamic, ≥6× static; stiffeners PI/FR4/steel annotated to Cmts.User on
KiCad export. No rigid-flex (JLC doesn't offer it). Source:
[JLCPCB flex capabilities](https://jlcpcb.com/capabilities/flex-pcb-capabilities).

Units: mm. `min_trace`/`min_space` are 1oz copper; heavier copper needs
wider geometry (bump `trace` widths or expect DRC errors — that's the
checker doing its job).

Sources: [JLCPCB capabilities](https://jlcpcb.com/capabilities/Capabilities),
[PCBWay capabilities](https://www.pcbway.com/capabilities.html),
[OSH Park](https://oshpark.com), [Seeed Fusion](https://www.seeedstudio.com/fusion.html),
[Aisler](https://aisler.net),
[Eurocircuits classification](https://www.eurocircuits.com/technical-guidelines/pcb-design-guidelines/classification/),
[NextPCB](https://www.nextpcb.com/capabilities), [ALLPCB](https://www.allpcb.com),
[Sierra](https://www.protoexpress.com/products/),
[Advanced Circuits](https://www.4pcb.com/pcb-capabilities.html).

Use: `python -m apps.ocd --fab oshpark board.ocd`, `fab` dropdown in studio,
or `Board.fab = "pcbway"` in Python. DRC reports which fab it checked.
