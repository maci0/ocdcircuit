# MOnSter-class benchmark: discrete6502 netlist in .ocd form

## Source (raw JSON committed; regenerate with convert.py)

- Repo: https://github.com/epatel/discrete6502 (CC **BY-NC-SA 4.0** —
  benchmark use is fine; do not ship derived board files commercially)
- Logic ground truth: visual6502 reverse-engineered netlist
- Inspired by the MOnSter 6502 (concept only — independently designed):
  https://www.evilmadscientist.com/2016/6502/ (12×15", 4000+ parts)
- Raw files: `netlist.json` (components + nets), `layout.json`
  (true mm positions); `layout_params.json` (board 290.7×322.0)
- This repo's earlier claim "291x322 6L" for the monster board stands
  corrected: discrete6502 is 290.7×322.0mm, 6-layer.

## Files here

- `convert.py` — netlist.json + layout.json → `discrete6502.ocd`
  (run: `python -m benches.discrete6502.convert`; needs `netlist.json` + `layout.json` beside it)
- `discrete6502.ocd` — generated, 5420 parts / 2593 nets / ~14.9k pins
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
- **WL-model caveat**: star-model wirelength is dominated by power nets
  (vcc 1350 / vss 2502 pins). Ratio numbers are mostly power-span, not
  signal routing quality — no per-net-class split yet. Bench defaults
  (seeds=1, iters=5) ≠ shipped studio defaults (seeds=4, iters=400).

## Known limitations (do not "fix" — they define the bench)

1. **Front/back stacking**: pull-ups/decoupling sit on layer B under front
   FETs at identical x/y. ocd placement is 2D single-side → ~957 residual
   `overlap` errors at golden positions. Placement scoring must use
   wirelength/benchmark metrics, not DRC-zero.
2. **Current published baseline** (flat diffusion, seeds=1 iters=5 —
   `BASE_SEEDS`/`BASE_ITERS` in `bench.py`; numbers from `baseline_r3.txt`
   r4/r5 block; re-pin on solver/WL-model changes):
   531–536 s, cost 4939572307 (deterministic — same cost across r4/r5;
   wall-clock varies by machine), placed WL 1394358 vs golden 1087471
   (ratio **1.28**), mean displacement 88.91 mm (similarity, secondary),
   overlaps 3321 vs golden floor 957 (**above_floor 2364**). Headroom:
   close the WL ratio toward 1.0 and the 2364 avoidable overlaps.
   Historical r3 (pre WL-model/repair drift): ratio 1.39, above_floor 1591,
   cost 3647492514 — superseded; do not quote as current headroom.
3. **Multilevel** (`placer:multilevel`, seeds=1 iters=2, same harness):
   10.6 s, WL ratio 1.64, overlaps 682 vs floor 957. Beating the stacking
   floor on overlaps while trailing on WL is a **different tradeoff**
   (leaves die-true stacking), not a win — read both numbers, not one.
4. **Rotations ignored** (layout.json has rot 0/90/180 + B-side parts);
   ocd parts are axis-aligned.
5. Single-pin TP nets and DNP ballast excluded from strictness.

## License note

`netlist.json`/`layout.json` are CC BY-NC-SA 4.0 (upstream). `discrete6502.ocd`
is a mechanical format conversion — same license applies to it. Raw JSON is
committed alongside (small enough, needed to regenerate).
