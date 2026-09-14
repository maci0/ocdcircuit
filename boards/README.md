# boards/

Real boards, not samples. Each subdir is one board: the `.ocd` source, its
`fp/` footprints, and `out/` build artifacts (gitignored — regenerate with
`python -m apps.ocd run <board>.ocd`).

- `blinky_555.ocd`, `psu.ocd`, `usb_breakout.ocd` — small 2L demos
- `pico_tmc2209/` — Pico + 3×TMC2209 (block/instance demo)
- `mitox/` — 43-part 4L port with harvested LCSC footprints
- `bme690/` — atopile-ported BME690 carrier
- `ne555/` — discrete 555 (atopile port)
- `e2e_driver4/` — E2E sensor driver (atopile port)
- `breath_ketone/` — dense breath sensor (farm-excepted: 81% fill, 12 overlaps)
