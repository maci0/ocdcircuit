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

### Built since (was "build next", now shipped)
1. **One-zip fab bundle** (tscircuit): DONE — `export:bundle`.
2. **`nc` pins** (atopile/KiCad): DONE — `nc REF.PIN`, ERC-exempt.
3. **Snapshot golden tests** (tscircuit `snapshot`): DONE — seeded
   place+route hashes in CI (`tests/test_snapshot.py`).
4. **Embedded calculators** (flux): DONE — `calc.py` (trace/amps/via/
   divider) + microstrip Z0 + diff-pair (`calc z0|zdiff`, studio row).
5. **Pre-route difficulty gate** (tscircuit `routing-difficulty`): DONE —
   `feasible()` carries a congestion estimate (crossings/pairs ratio +
   pin density, no maze burn), surfaced in the studio badge tooltip.
6. **`assert` constraints** (atopile): DONE — `assert R1.1 connected`,
   `assert N != GND`, `assert parts <= N`, evaluated in ERC
   (voltages stay `sim expect`).

### Later (bigger lifts, tracked not built)
- **Copper pour** (tscircuit): DONE — `pour NET on L` floods negative
  Gerber plots + KiCad zones; routers/DRC honor planes (mitox GND on 0/3).
  Remaining: panel elements.
- **Keepout / fiducial** (tscircuit): DONE — rect/round/part-relative
  keepouts (maze-soft + DRC-flagged), fiducial parts with deadzones.
  Remaining: panel elements.
- **Typed interfaces + `~`/`~>` wiring** (atopile): PARTIAL — RFC-0002
  net roles (`class role=power|signal`, cross-role shorts fail ERC) +
  symbol pin `dir=in|out|pwr` (`out-out` drivers fail ERC). Full `~`
  port wiring still needs direction data on all footprints.
- **Units + tolerances + parametric BOM picker** (atopile): PARTIAL —
  `tol=` validated + own BOM column + row splits, `lcstat=` lifecycle
  with ERC warnings, `alternates=` priced when primary MPN is unpriced
  (source tagged `+alt:`), live JLC stock threads into `low_stock`.
  No hosted distributor default.
- **Package registry** (tscircuit `@tsci/`, atopile packages): PARTIAL —
  `use PATH@SHA` pins includes to content hashes (`ocd pin` rewrites);
  no hosted registry, git files suffice.
- **Layout-preserving KiCad sync** (atopile `update_pcb`): DONE (import
  side) — eagle-brd/kicad-pcb/altium-ascii emit `fix` per part, so
  foreign placement survives dumps + re-solve. Schematic importers
  stay unpinned (drawing coords, not mm).
- **SPICE simulation** (flux prompt-sim, tscircuit `<analogsimulation>`):
  DONE — stdlib MNA + ngspice + `sim expect` (DC + tran-wave
  final|min|max, CLI exit 2, studio flags) + deterministic NL
  prompt-to-testbench (`VO should settle at 5V`, no LLM needed).
- **Candidate gallery + compare view** (quilter): DONE — N-candidate
  gallery with feasibility badge, pick-to-restore, feasibility probe,
  shift-click compare (Δcost + moved parts).
- **Schematic capture** (flux): PARTIAL — sch canvas creates parts
  (dbl-click) + nets (pin→empty click), rewires, renames;
  `disconnect`/`drop_net` patch ops.
- **Panelization**: DONE — `panel 2x3 gap 2.5` tiles Gerber/drill/CPL
  (export-only; DRC/place/route see one board).
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
| SI/PI | DRC skew reports + microstrip Z0/Zdiff closed-form estimates; no impedance/PDN solver |
| Cost / stock | tol/lcstat/alternates attrs + quote with live JLC stock; no hosted distributor default |
| Panelization | `panel` tiles Gerber/drill/CPL export + outer cut frame; doctor `panel-fit` gates fab max; no fab rails/fiducials |
| Interactive route | maze shove + single-net reroute + alt-click/shift-drag UI; no spring-drag copper |
| Enterprise handoff | STEP AP203 + IPC-2581 + ODB++ subsets, honestly labeled; Gerbers stay mfg truth |
| Agent copilot | `agents/` MCP config + system prompt, registry-tested |
| Exact layout proof | CP-SAT optional; stdlib proxy piloted in `benches/exact_overlap_pilot.py` (feasibility cheap ≤20; prove-no/WL-cap time out by n=12–20) |
| Whole-board sim | block-by-block + model-less policy; never one "simulate PCB" button |

