# .ocd language reference

One fact per line. Keywords case-insensitive, `#` starts a comment,
blank lines ignored. Units are mm. First non-blank line must be `board`.
Build with `python -m apps.ocd <file.ocd>` — errors name the line number.

## Your first board (six lines)

```ocd
board rc 20x10
part R1 R0805 1k x=3 y=5
part C1 C0805 100n
N :: R1.2 <--> C1.2
GND :: R1.1 <--> C1.1
```

```bash
python -m apps.ocd run boards/blinky_555.ocd   # place → route → DRC → out/
python -m apps.studio boards/blinky_555.ocd    # see it, drag it, keep it
```

Coming from KiCad? `Board('m').import_fp('pcb', path='mine.kicad_pcb')`
loads parts+nets; `b.export('kicad')` writes it back. Footprints
(`.kicad_mod`), Eagle (`.lbr`/`.brd`), tscircuit/EasyEDA JSON all import
the same way (`docs/PORTS.md`).

Declarative rule: facts describe the board (`part … x=3`,
`GND pour=0 :: …`); legacy command spellings (`fix`, `route`, `pour`)
still parse and dump in canonical form.

```ocd
board blinky555 40x30 2L       # board NAME WxH [NL] — 1..32 layers (default 2)
board 40x30                  # resize (bare form, no name)
meta title Blinky 555          # meta KEY value... (title/rev/desc/… → KiCad/IR)
```

## Parts

```ocd
part U1 SOIC8 NE555            # part REF FOOTPRINT [value...] [k=v ...]
part C1 C0402 100n lcsc=C1525 rot=90 x=3 y=15
part R9 R0603 0 dnp=1        # do-not-place: DNP BOM row, ERC-exempt,
                             # excluded from CPL + KiCad (attr dnp), X'd on
                             # assembly drawing (pads still export)
part U2 SOIC8 TL072 sym=OPX pin2=VFB  # sym= symbol override; pinN= pin label
```

Part attrs (`k=v`, order-free, kept verbatim into IR/BOM/KiCad):
values with spaces quote (`note="hello world"`, shlex rules) ·
`lcsc=` `mpn=` (orderable keys) · `rot=` 0/90/180/270 (bbox-aware) ·
`x=` `y=` (≡ `fix REF at x y`, dumps the `fix` line) · `dnp=1` ·
`sym=` (symbol override: R C L D Q3 OPAMP IC8 IC14 IC16) ·
`pinN=` (schematic pin label) ·
`alternates=` (comma MPN/LCSC list, parts-libraries brief).

## Nets (mermaid-style flow)

```ocd
VCC :: J1.1 <--> U1.8 <--> R1.1  # NAME [attrs] :: REF.PIN <--> ...
GND L1 w0.5 :: J1.2 <--> U1.1    #   L<n> layer, w<n> width mm
GND pour=0 :: J1.2 <--> U1.1     #   pour=N ≡ `pour NET on N`
HV class=highvolt :: J1.3 <--> U1.2  # k=v net attrs (class= names a class…)
class highvolt width=0.8 clearance=0.5  # …defined once: width floor + DRC gap
```

Legacy `net NAME [attrs]: REF.PIN ...` (colon form) also parses.
Every `REF` must be a declared part; every `PIN` must exist on its
footprint — checked at load, no silent bad pins. Unknown footprints fail:
`line 2: 'unknown footprint NOPE'`. Values may contain spaces
(`part R1 R0805 10k 0805` keeps `10k 0805`).

## Placement constraints

```ocd
fix J1 at 3 15                 # pin a part (or x=/y= on the part line)
keep U1 near C1 3              # pull parts together (weight, default 2)
```

## Routing constraints (fold onto the net line where possible)

```ocd
route GND on 1                 # force net to layer (top/bottom also work)
trace VCC 0.5                  # trace width mm
power VCC GND                # widen nets to 0.5 (power)
match A0 A1                  # length-match nets (placer cost + DRC skew report)
diff DP DN gap 0.3           # diff pair: equal length + 0.3mm coupling gap
```

## Geometry (board features in mm, center x y)

```ocd
pour GND on 0                # copper pour (or pour=0 on the net line)
keepout 11.5 47 15.7x1.9     # rect keepout, center x y WxH [+ on layers]
keepout 20 15 d6           # round keepout, center x y dia [+ on layers]
keepout near F1 d4         # deadzone follows part (fiducial); WxH or dN,
                           # default d4; anchor part exempt, maze + DRC + KiCad
cutout 11.5 47 15x1.2        # board cutout (slot)
hole 15.2 12.9 1.3           # bare mounting hole (x y drill)
bend 10 20 30x5 r2           # flex bend area, center x y WxH radius [static]
stiffener 10 20 30x5 FR4 0.2  # stiffener: center x y WxH material thick
nc J1.A5 J1.A6               # intentionally unconnected pins (ERC-exempt)
route-grid 0.2               # maze cell size (default 0.25); finer closes
                             # dense boards, coarser routes faster
route-penalty bend 3 via 20  # maze cost knobs (defaults 1.5/8.0);
                             # higher bend = straighter, higher via = fewer layers
silk 2                       # silk detail 0=refs 1=+values 2=+outlines 3=+nets
```

`pour NET on L` floods layer L with NET copper (negative Gerber plot +
KiCad zone; routers skip poured nets on pour layers, DRC exempts plane
copper from clearance/keepout). EasyEDA/Eagle carry the net as ratsnest
(pads keep net assignments; no plane polygon — those formats have no
consumer here yet). Flex `bend`/`stiffener` enforced by
`jlc-flex` DRC only (see `docs/FAB.md`).

## Simulation (`sim`, one per line)

```ocd
sim vcc VIN 9                # 5V-style source net→GND (0 5 = step for tran)
sim sine IN 1.65 1.65 1000   # sine source: offset amplitude freq-Hz
sim isrc N 0.01              # current source into net (A)
sim r R1 10k                 # value override (r/c/l/d/q + part REF + value)
sim tran 0.01 1000           # transient: t_end steps
sim probe N_OUT              # record net (default: all)
sim op N_OUT V 0 5           # operating-point sweep (net, source, lo hi)
sim ac 10 100000 20          # AC sweep f0 f1 npts (ngspice dec sweep)
sim lib models.lib           # extra SPICE include for simulate:ngspice
sim expect VO == 5 tol 0.1   # assertion: VO==5 ±0.1 (red in studio/MCP/CLI)
sim clk CLK 4                # square-wave stimulus, period [duty] (gates)
```

Digital: `part U1 SOIC14 NAND logic=NAND` (NAND/NOR/AND/OR/XOR/INV/BUF,
DFF/JK; inputs in pin order, output = highest pin), then
`b.simulate("gates")`. GND/VSS/0 = 0, VCC = 1 unless driven.

## Reuse: files (`use`) and in-file units (`block`)

```ocd
use psu.ocd as PSU            # include board (child size/layers/fix ignored)
use sub.ocd join VCC GND      # merge nets into parent (VCC/GND auto-join)
block driver               # reusable unit: local refs, stamped per instance
  part U QFN28             #   allowed inside: part/net/constraints only —
end                        #   board/use/fp/instance/nested blocks rejected
instance driver as Z1      # stamp with PREFIX_; repeat as needed
instance driver as Z2 join VCC GND  # joined nets merge, rest stay local
```

`use`: child refs/nets gain `PREFIX_` (default: child board name). Joined
nets (`join`, plus `VCC GND VDD VSS 5V 3V3` automatically) merge into the
parent. Ignored from child: board size, layer count, `fix` lines. Cycles
and ref clashes are errors. `dumps()` writes `use` + local-only content.

## Libraries (footprints + symbols)

```ocd
fp exotic.fp                 # custom footprint (pads/holes/3D/keepouts)
sym opamp.sym                # custom symbol (body + pin stubs + label)
                               # also: .kicad_mod/.pretty, .lbr (Eagle), .json (tscircuit)
```

`.fp` format: `footprint NAME WxH [edge]` · `pad PIN dx dy w h` ·
`hole PIN dx dy drill` · `body box|cyl …` · `keepout …`.
`.sym` format: `symbol NAME [WxH]` · `pin NUM side [LABEL]` ·
`label TEXT` (`{ref} {value} {fp}` interpolate) · `notch` · `zigzag`.
Footprint shadowing of stdlib is an error (rename it).

## Rules

- One board per file; a second `board NAME …` header is an error.
- `dumps()` output is canonical: constraints come after nets; layer/width
  constraints set by `net` attrs print as `route`/`trace` lines;
  `x=/y=` parts print no `fix` line.
- A fact that parses but violates design rules builds, then exits 2.

Duplicate sources resolve last-wins, uniformly: a later `fix R1 at …`
overrides the `x=/y=` on the part line; a later net line's `L0 w0.5`
overrides an earlier `route`/`trace` (and vice versa). All statements
persist in dumps; resolution is by file order per target.

## Errors (exit 1) vs DRC fail (exit 2)

Parse/validate problems → `ocd: line <n>: <what>: '<line>'`, exit 1.
A file that parses but violates design rules builds, then exits 2 with
`errors=[...]` (see `docs/ADR-0003-drc-export.md`).
With `--sim dc`, failed `sim expect` assertions also exit 2 (`sim: …`
rows join the error list — CI must not ship a board that simulates wrong).
