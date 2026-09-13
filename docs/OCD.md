# .ocd language reference

One fact per line. Keywords case-insensitive, `#` starts a comment,
blank lines ignored. Units are mm. First non-blank line must be `board`.
Build with `python ocd.py <file.ocd>` — errors name the line number.

```ocd
board blinky555 40x30 2L     # board NAME WxH [NL]   (default 2 layers)
part U1 SOIC8 NE555          # part REF FOOTPRINT [value...]
net VCC: J1.1 U1.8 R1.1      # net NAME [attrs]: REF.PIN ...
net GND L1 w0.5: J1.2 U1.1   #   L<n> = layer, w<n> = width mm
fix J1 at 3 15               # pin a part at x y
keep U1 near C1 3            # pull parts together (weight, default 2)
route GND on 1               # force net to layer (top/bottom also work: 0/1)
trace VCC 0.5                # trace width mm
power VCC GND                # widen nets to 0.5 (power)
match A0 A1                  # length-match nets (placer cost + DRC skew report)
diff DP DN gap 0.3           # diff pair: equal length + 0.3mm coupling gap
silk 2                       # silk detail 0=refs 1=+values 2=+outlines 3=+nets
use psu.ocd as PSU            # include board (child size/layers/fix ignored)
use sub.ocd join VCC GND      # merge nets into parent (VCC/GND auto-join)
board 40x30                  # resize (bare form, no name)
```

## Includes (`use`)

- `use PATH [as PREFIX] [join NET ...]` — PATH relative to the file.
- Child refs/nets gain `PREFIX_` (default: child board name). Joined nets
  (`join`, plus `VCC GND VDD VSS 5V 3V3` automatically) merge into the parent.
- Ignored from child: board size, layer count, `fix` lines. The parent
  places everything; the include's parts stay grouped (`near-group`).
- Cycles and ref clashes are errors. `dumps()` writes `use` + local-only
  content, so committed files stay the single source of truth.

## Rules

- One board per file; a second `board NAME …` header is an error.
- Every `REF` in a net must be a declared part; every `PIN` must exist on
  that footprint (`ocd` checks this at load — no silent bad pins).
- Unknown footprints fail at load: `line 2: 'unknown footprint NOPE'`.
- Net attributes are only `L<n>` / `w<n>` — anything else is an error.
- Values may contain spaces (`part R1 R0805 10k 0805` keeps `10k 0805`).
- `dumps()` output is canonical: constraints come after nets; layer/width
  constraints set by `net` attrs print as `route`/`trace` lines.

## Minimal example

```ocd
board rc 20x10
part R1 R0805 1k
part C1 C0805 100n
net N: R1.2 C1.2        # ocd: floating-net DRC will flag single-pin nets
net GND: R1.1 C1.1
fix R1 at 3 5
```

## Errors (exit 1) vs DRC fail (exit 2)

Parse/validate problems → `ocd: line <n>: <what>: '<line>'`, exit 1.
A file that parses but violates design rules builds, then exits 2 with
`errors=[...]` (see `docs/ADR-0003-drc-export.md`).
