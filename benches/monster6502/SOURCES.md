# MOnSter-class benchmark: discrete6502 netlist in .ocd form

## Source (NOT committed — download to reproduce)

- Repo: https://github.com/epatel/discrete6502 (CC **BY-NC-SA 4.0** —
  benchmark use is fine; do not ship derived board files commercially)
- Logic ground truth: visual6502 reverse-engineered netlist
- Inspired by the MOnSter 6502 (concept only — independently designed):
  https://www.evilmadscientist.com/2016/6502/ (12×15", 4000+ parts)
- Raw files: `gen/netlist.json` (components + nets), `gen/layout.json`
  (true mm positions); `layout_params.json` (board 290.7×322.0)
- This repo's earlier claim "291x322 6L" for the monster board stands
  corrected: discrete6502 is 290.7×322.0mm, 6-layer.

## Files here

- `convert.py` — netlist.json + layout.json → `monster6502.ocd`
  (run: `python3 convert.py`; needs `netlist.json` + `layout.json` beside it)
- `monster6502.ocd` — generated, 5420 parts / 2593 nets / ~14.9k pins
  (raw: 5421 comps / 2624 nets; DNP Pico U1 + 31 pico-private/single-pin nets
  dropped; vcc/vss kept)
- `fet_sot323.fp`, `chip0402.fp`, `testpoint.fp` — true-size footprints
  (stdlib SOT23/R0402 carry courtyard margins that false-overlap at
  3.7×2.8mm die-true pitch)
- Footprint map: FET→FET_SOT323, R/C0402→CHIP0402, LED→LED0603 (stdlib),
  C0805→C0805, diode→D_SOD323, testpoints→TP1

## Stats (measured)

- 4051 FETs + 1110 R + 156 C + 55 LED + 12 diode + 36 TP (raw counts);
  2624 raw nets (1282 2-pin; p50 fanout 3, p90 7, p99 20; vss 2502 pins,
  vcc 1350). Generated file: 5420 parts / 2593 nets (see above).
- 5271 parts carry die-true `fix` positions (golden reference for scoring);
  150 back-side decoupling caps unplaced (placer decides)
- `fix` = placement ground truth: ocd only honors `fix` inside `place()`,
  so the harness must apply positions post-load before scoring (see bench.py)

## Known limitations (do not "fix" — they define the bench)

1. **Front/back stacking**: pull-ups/decoupling sit on layer B under front
   FETs at identical x/y. ocd placement is 2D single-side → ~957 residual
   `overlap` errors at golden positions. Placement scoring must use
   wirelength/benchmark metrics, not DRC-zero.
2. **Baseline** (flat diffusion, seeds=1 iters=5, fixed harness):
   320.9 s, cost 3647492514 (deterministic — same cost across runs;
   wall-clock varies by machine), placed WL 1492514 vs golden 1075333
   (ratio 1.39 — flat diffusion *loses* to die-true hierarchy),
   mean displacement 151.90 mm (similarity, secondary),
   overlaps 2548 vs golden floor 957 (above_floor 1591). The headroom
   to beat: close the WL ratio toward 1.0 and the 1591 avoidable overlaps.
3. **Rotations ignored** (layout.json has rot 0/90/180 + B-side parts);
   ocd parts are axis-aligned.
4. Single-pin TP nets and DNP ballast excluded from strictness.

## License note

`netlist.json`/`layout.json` are CC BY-NC-SA 4.0 (upstream). `monster6502.ocd`
is a mechanical format conversion — same license applies to it. Raw JSON is
committed alongside (small enough, needed to regenerate).
