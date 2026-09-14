# ORDER.md — mitox 4L, ready to send (not yet ordered)

Generated package: `out/` (regenerate: `python -m apps.ocd run ../mitox.ocd`).
DRC: clean, 3 keepout warnings (documented placement notes, not blockers).

## Upload to JLCPCB (PCB + assembly)

| File | Purpose | Check |
|---|---|---|
| `mitox.GTL/G1/G2/GBL.gbr` | 4 copper layers (extents 24×56 + 0.3 edge) | ✓ |
| `mitox.GTS/GBS.gbr` | solder mask top/bottom | ✓ |
| `mitox.GTO/GBO.gbr` | silkscreen top/bottom | ✓ |
| `mitox.GTP.gbr` | paste (assembly) | ✓ |
| `mitox.GKO.gbr` | board outline | ✓ |
| `mitox.TXT` | Excellon drill (35 lines) | ✓ |
| `mitox.BOM.csv` | 31 rows, 28 with LCSC | 3 gaps ↓ |
| `mitox.CPL.csv` | 43 placements, rotations 0/90/180/270 | ✓ |

Board: 24×56mm, 4L, lead-free, 1oz. Paste + assembly on top side.

## Hand-solder (no LCSC — customer-supplied)

- **BT1** — battery holder (`FP_BT1`)
- **DS1** — display, 9.62mm wide (`FP_DS1`)
- **TP1** — testpoint (`FP_TP1`)

## After it arrives

1. Photo bare board next to `mitox.png` render.
2. Assemble, power, check nets from `mitox.sch.svg`.
3. Photo assembled board → closes the fab-verified loop in the goal.
