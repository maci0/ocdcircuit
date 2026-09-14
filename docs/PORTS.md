# Ports: foreign projects → .ocd

One-shot converters in `tools/`. Each emits a board dir (`.ocd` + `fp/`)
that then lives on its own — re-port only when upstream changes.

## atopile (`tools/atopile.py`)

Input: `<proj>/atopile/main.ato` + `parts/**/*.ato` + optional
`layouts/**/*.kicad_pcb`. Structural subset: `signal`, `new`, `~`/`>`
wiring (`;`-separated statements all parse), `module M:` blocks elaborated
inline (nested to depth 8, `x = new M` stamps `x_*` copies), sibling files
via `from "x.ato" import Y`, `module App:` as root.

Footprints: `.kicad_mod` refs harvest verbatim; names matching the std lib
use std lands. Generics (`Resistor/Capacitor/LED`) resolve through
`var.package = "PKG"` assignments (per-module scope, mapped onto elaborated
names). A generic with no package is a hard error naming the `.ato` line —
geometry is never guessed.

Positions: layout `.kicad_pcb` refs rarely match porter refs, so matching
is by footprint+order through verify-then-pin (overlap/off-board fixes are
emitted commented, solver places freely).

Boards: `bme690` (flat), `ne555` (nested modules), `breath_ketone`
(dense: 12mm USB-C on 16mm width, ~7 residual overlaps), `e2e_driver4`.

## tscircuit (`tools/tscircuit.py`)

Input: `index.circuit.tsx` + `index.circuit.circuit.json`. TSX gives
structure (elements, LCSC, `pcbX/pcbY`), circuit.json gives exact
pad/hole geometry per component → one `.fp` per footprint (named by LCSC).
Boards: `pico_tmc2209` (20 parts), `mitox` (43 parts, 4L).

## mitox (`tools/mitox.py`)

Specialization of the tscircuit path for LCSC-footprint boards; same
contract, exact-pad harvesting. See `boards/mitox/`.
