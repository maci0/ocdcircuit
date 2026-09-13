# Cool links

Stuff worth stealing, learning, or just admiring. One line each: what it
is, why it's here.

## Exotic parts (custom .fp + edge-mount)
- [GCT USB-C edge-mount plug](https://www.digikey.com/en/product-highlight/g/gct/usb-type-c-edge-mount-pcb-plug) —
  PCB tongue as USB-C plug. Shipped as `boards/usb_c_edge.fp` +
  `boards/usb_breakout.ocd`; `edge` flag exempts overhang in DRC/placer.

## Edge connectors & clever footprints
- [pcb-edge-usb-c](https://github.com/AnasMalas/pcb-edge-usb-c) — use the PCB
  itself as a USB-C plug (10/14/24-pin). Zero-cost connector; footprint idea
  for our lib.
- [KiCad footprint libs](https://gitlab.com/kicad/libraries/kicad-footprints) —
  thousands of .pretty footprints, directly importable via `fp`.
- [pico_tmc2209-tscircuit](../boards/pico_tmc2209/pico_tmc2209.ocd) —
  our first port: Pico + 3×TMC2209 via `ocdcircuit/ports/tscircuit.py`.
- [atopile](https://atopile.io) — Python HDL for PCBs; our `ocdcircuit/ports/atopile.py`
  converts `.ato` + `.kicad_mod` to `.ocd`.
