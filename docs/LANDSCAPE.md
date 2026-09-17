# LANDSCAPE — what we steal from whom

- **tscircuit**: circuits-as-code, footprint registry convention, `check`
  pipeline (netlist → placement → routing → DRC) mirrored in our demo/tests.
- **atopile**: hierarchical modules + interfaces → our `Module` components and
  `requires/provides` services; declarative config ≈ `.ato` intent.
- **flux.ai**: copilot-side structured access → our IR + patch ops; sim hooks
  reserved as v1 `simulate` op.
- **Quilter**: generate N candidate layouts, keep best → multi-seed `optimize`.
- **Paper (Cordis)**: revertible effects, reactive coeffects, declarative
  loader + HMR → `core.py` / `Loader` verbatim as architecture.

## Scrape round (Sep 2026) — steal list, ranked by cost × value

Stolen already: MCP server (flux), fab profiles for 5 fabs (flux mfg list),
KiCad as interchange (quilter/atopile), multi-seed candidates (quilter),
check pipeline (tscircuit).

### Build next (cheap, high value)
1. **One-zip fab bundle** (tscircuit): Gerbers + drill + BOM w/ MPN + PnP in
   one zip. The conversion step is "download → upload → boards".
2. **`nc` pins** (atopile/KiCad): `nc REF.PIN` marks intentionally-unused pins
   so ERC stops flaggingjasmine them. One parser line + ERC exemption.
3. **Snapshot golden tests** (tscircuit `snapshot`): seeded place+route →
   hash positions/traces, compare in CI. Stops silent solver regressions.
4. **Embedded calculators** (flux): trace-width/IPC-2221, via current,
   divider — `calc.py`, no tab-switching.
5. **Pre-route difficulty gate** (tscircuit `routing-difficulty`): congestion
   score before burning maze time; warn early on dense boards.
6. **`assert` constraints** (atopile): `assert VCC within 3.0 3.6` checked at
   build — design intent verified before layout.

### Later (bigger lifts, tracked not built)
- **Copper pour** (tscircuit): DONE — `pour NET on L` floods negative
  Gerber plots + KiCad zones; routers/DRC honor planes (mitox GND on 0/3).
  Remaining: panel elements.
- **Keepout / fiducial** (tscircuit): DONE — rect/round/part-relative
  keepouts (maze-soft + DRC-flagged), fiducial parts with deadzones.
  Remaining: panel elements.
- **Typed interfaces + `~`/`~>` wiring** (atopile): illegal connections as
  compile errors. Needs a type layer on nets — real design work, RFC first.
- **Units + tolerances + parametric BOM picker** (atopile): value±tol →
  orderable MPN via LCSC/JLCPCB. Needs distributor data source.
- **Package registry** (tscircuit `@tsci/`, atopile packages): versioned
  reusable modules. Needs hosting story; git includes suffice for now.
- **Layout-preserving KiCad sync** (atopile `update_pcb`): merge code changes
  without wiping manual placement. Needs KiCad parsing, not just export.
- **SPICE simulation** (flux prompt-sim, tscircuit `<analogsimulation>`):
  DONE (stdlib MNA + ngspice bridge + `sim expect` assertions with CLI
  exit codes, STATUS.md verdicts, MCP problems). Remaining: prompt-to-
  testbench NL layer; tran-wave assertions.
- **Candidate gallery + compare view** (quilter): DONE — N-candidate
  gallery with feasibility badge, pick-to-restore, feasibility probe.
  Remaining: side-by-side visual diff of candidates.
- **Schematic sections + dual placement** (tscircuit schX/schY): schematic
  stays legible while PCB moves. Needs sch layout engine.

### Explicitly not copying
- Quilter scope: no RF-dominant/HV/HDI promises — honest envelope instead.
- Flux pricing/ACUs, multiplayer, enterprise SSO — not our problem yet.
- Cloud autorouter, push-to-URL sharing — local-first beats accounts.

## Envelope (what we do / don't claim)

Corpus-wide honesty map — tracked here so briefs don't silently overclaim:

| Domain | Status in tree |
|--------|----------------|
| Thermal | placer `thermal` knob only (big bodies → edges); no FEA |
| SI/PI | DRC skew reports only; no impedance/PDN solver |
| Cost / stock | attrs + optional quote; no live distributor default |
| Panelization | zero code (panel elements still "Remaining" above) |
| Interactive route | no push-shove story (KiCad PNS is the reference, not a port) |
| Exact layout proof | CP-SAT optional; stdlib proxy piloted in `benches/exact_overlap_pilot.py` (feasibility cheap ≤20; prove-no/WL-cap time out by n=12–20) |
| Whole-board sim | block-by-block + model-less policy; never one "simulate PCB" button |

