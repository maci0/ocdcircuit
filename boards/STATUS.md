# STATUS — blinky555

OCD score: 83.6/100 (B)

## tidy (14/15 metrics defined)

| metric | value |
|---|---|
| T1_crossings | 6 |
| T2_bends_per_mm | 0.112 |
| T3_orthogonality | 1.000 |
| T4_vias | per_net={}, total=0 |
| T5_headroom | 0.000 |
| T6_skew |  |
| T7_alignment | 0.200 |
| T8_gridsnap_mm | 0.111 |
| T9_spacing | 0.834 |
| T10_orientation | cardinal=1.0, entropy=0.0 |
| T11_copper_balance | layer_delta=0.0, tile_sigma=8.527 |
| T12_acid_traps | 16 |
| T13_schematic | crossings=51, jogs=0 |
| T14_silk_overlap | text_copper=4, text_text=1 |
| T15_silk_consistency | 1.000 |

| check | errors | warnings |
|---|---|---|
| erc | 0 | 0 |
| fab | 0 | 14 |
| jlc-flex | 0 | 14 |

- fab: clearance VCC-GND
- fab: clearance VCC-GND
- fab: clearance VCC-GND
- fab: clearance VCC-GND
- fab: clearance VCC-GND
- fab: clearance VCC-GND
- fab: clearance VCC-GND
- fab: clearance GND-N_LED
- fab: clearance GND-N_LED
- fab: clearance N_CV-N_TH
parts: 10, nets: 7, traces: 38, layers: 2
solved: diffusion/lroute @ jlc
extent: 24.54x27.26mm (56% of 40x30 board, shrink → 25.5x28)
