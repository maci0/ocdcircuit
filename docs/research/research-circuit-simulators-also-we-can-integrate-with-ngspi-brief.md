# Circuit simulator integrations — research brief (ocdcircuit relevance)

## Summary

ocdcircuit today simulates with a stdlib MNA engine (DC + transient, R/C/L,
V/I/sine sources, Shockley diode — `ocdcircuit/sim.py`, `simulate:mna`
plugin, `sim` constraints). That covers sizing checks; everything with real
silicon needs an external simulator. Ranked integration order:
1. **Pure-stdlib `.cir` exporter** from Board state (zero new deps,
   golden-file testable without ngspice installed).
2. **Subprocess runner** (`ngspice -b -o -r`, ASCII raw parse, graceful
   not-found path) + result back-annotation into board nets.
3. **`.SUBCKT` include path** for analog ICs (the entire IC story is one
   primitive: `.include` + X-line) + behavioral B-sources as fallback.
4. **Tiny built-in gate sim** (~50–100 lines, unit-delay, 74xx glue) — the
   digital answer that needs no JVM, no dep.
5. **XSPICE code models via ngspice** for truly mixed boards (no second
   simulator). Everything else (shared-lib fast path, Xyce, Verilator,
   Renode/QEMU, IBIS) is conditional-or-never. Whole-board simulation =
   block-by-block with an explicit model-less-part policy, never a single
   "simulate PCB" button.

## Background

Current surface: `sim.py` (319 lines, MNA nodal analysis, Backward-Euler
companions, Gaussian elimination), `SimPlugin` (`plugins.py`, `simulate:mna`),
`Board.simulate()` dispatch, `sim vcc/sine/tran/probe/r/c` constraints
(`docs/OCD.md`), MCP exposure. No transistors, no subcircuits, no AC/noise,
no digital, no external models. The `simulate` plugin kind already exists, so
new backends (`simulate:ngspice`, …) slot into the hot-swap architecture with
zero refactoring.

## Key findings (by theme)

### 1. ngspice: subprocess first, shared lib later, server never (v1)

- **Three paths** (verified against official pages): (i) netlist + subprocess;
  (ii) shared library (`libngspice.so`/`.dll`, `ngSpice_Init()` with callback
  table, netlist-as-string, threaded sim with per-step data + halt/alter/resume —
  [shared-lib page](https://ngspice.sourceforge.io/shared.html), API in manual
  ch. 19 per that page); (iii) server/IPC mode. Verdict: **(i) first** —
  only needs the binary + stdlib `subprocess`; (ii) later as optional
  accelerator (ctypes bridge, callback/thread discipline, ABI drift, and the
  .so is often NOT shipped by distros — see below); (iii) skip (least
  documented, build-option-sensitive).
- **Headless flags verified** in the man page: `-b/--batch`, `-o` log file,
  `-r` rawfile, `-s/--server` (rawfile to stdout after `@`), `-n`
  skip-spiceinit, `SPICE_ASCIIRAWFILE=1` for ASCII raw (stdlib-parseable) —
  [Arch man page](https://man.archlinux.org/man/ngspice.1.en). Package names:
  `apt install ngspice`, `brew install ngspice` / `libngspice`
  ([PySpice install page](https://pyspice.fabrice-salvaire.fr/releases/v1.3/installation.html)).
- **License verified**: core is new/modified BSD-3-Clause, Debian-compatible —
  [devel page](https://ngspice.sourceforge.io/devel.html). Caveat: bundled
  extras differ (ADMS GPL-2+, numparam LGPL-2, manual CC-BY-SA) — routine for
  distro binaries, but check build options if GPL-taint matters [UNVERIFIED
  which builds link ADMM by default — flagged].
- **Netlist essentials** the exporter must emit: line-1 title, `R/C/L/D/Q/M`
  element lines, `V/I` sources (DC/SIN/PULSE/PWL), `.include`/`.lib`,
  `.op/.dc/.ac/.tran`, `.save/.print`, `.control…​.endc`, `.end`. Manual:
  [v47 PDF + xhtml](https://ngspice.sourceforge.io/docs.html). Chapter numbers
  cited by angle-(a) NOT verified against the ToC — flagged, confirm before
  citing chapters.
- **Existing Python drivers — do not depend, optionally crib**:
  [PySpice](https://github.com/PySpice-org/PySpice) (`pip install PySpice`,
  CFFI shared-lib driver AND `ngspice-subprocess` fallback, but deps
  numpy/matplotlib/cffi + libngspice path pain: Fedora Copr-only, Ubuntu
  binary-only, brew `libngspice` — all per its install page); [ngspyce](https://github.com/endolith/ngspyce)
  (thin ctypes, vendoring-sized); [ngspice-connect](https://github.com/jchabloz/ngspice-connect)
  (maturity UNVERIFIED — flagged). For a zero-dep tool: vendor a ~100-line
  netlist writer, optionally crib ASCII-raw parsing. No new pip deps.
- **What ngspice buys over stdlib MNA** (one line each): BSIM3/4 + HiSIM MOS;
  Gummel-Poon/VBIC BJTs with temp/noise; transmission lines (LTRA/TXL);
  `.ac` + `.noise`; `.pz/.sens/.disto/.tf`/fourier; XSPICE + B-sources;
  scripted sweeps/Monte Carlo via `.control` [no single `.mc` card claim
  UNVERIFIED — flagged].

### 2. Other SPICE backends: Xyce second, LTspice never embedded

- **Xyce** (Sandia): open engine, MPI/Trilinos parallel, largely
  HSPICE-compatible netlists, CLI `Xyce deck.cir`; QUCS-S exposes it as a
  script backend —
  [backend comparison](https://qucs-s-help.readthedocs.io/en/latest/overview/choosing-a-sim-backend.html).
  License widely reported GPLv3 — **NOT verified, confirm at
  [Xyce repo](https://github.com/Xyce/Xyce)**. No official Python API found
  (absence-of-evidence — flagged). Beats ngspice only at HPC/large-circuit
  scale; QUCS-S's own rule: ngspice default, Xyce for special features.
- **LTspice** (ADI): proprietary free-of-charge, non-transferable, no
  redistribution-modified, Windows-native (Linux = Wine + CLI, community
  practice — officially UNVERIFIED). Batch confirmed: `-b` (→ .raw), `-ascii`
  — [switches](https://ltwiki.org/LTspiceHelpXVII/LTspiceHelp/html/Command_Line_Switches.htm).
  Python automation via third-party [PyLTSpice](https://github.com/nunobrum/PyLTSpice).
  Value = vendor model library + speed; cost = proprietary + Wine fragility +
  encrypted models. For ocdcircuit: exported-netlist compatibility only.
- **QUCS-S**: GUI with NO built-in backend — drives ngspice/Xyce/SpiceOpus/
  Icarus/GHDL ([overview](https://qucs-s-help.readthedocs.io/en/latest/welcome/what-is-qucs-s.html)).
  Lesson: copy the multi-backend-frontend *pattern*, not the code. Minimal
  abstraction: one `SimBackend` interface (`write_netlist → run → parse`)
  with per-backend flag deltas — ngspice/Xyce differ in invocation, not
  netlist shape.
- **[SKiDl](https://github.com/devbisme/skidl)** (MIT): closest precedent —
  Python-embedded `Net`/`Part`, `+=` wiring, `generate_netlist()`, built-in
  ERC, SPICE hooks. Steal the `+=` idiom and describe→ERC→emit ordering;
  do NOT depend on it (needs KiCad libraries + dep tree — anti-zero-dep).
- One-liners: **Gnucap** (GNU/GPL, scriptable CLI, smaller model ecosystem);
  **SpiceOpus** (free, 3f5-based, optimization-oriented — license UNVERIFIED).
  TopSPICE unreached — no claim.

### 3. Digital: tiny gate sim first, XSPICE second, Verilator if HDL appears

- **Tiny built-in gate sim: YES, ~50–100 lines stdlib.** Event-driven
  worklist (time, node, value); pop earliest, evaluate fanout, schedule
  changes at t+delay; unit-delay defuses combinational loops; fixpoint cap
  (~100 iterations) catches oscillation. Covers 74xx glue (NAND/NOR/INV/DFF
  on the existing Net/pin graph) with stimulus from `sim`-style constraints.
  Limits: no timing closure, no metastability. No shell-out needed.
- **XSPICE digital inside ngspice is the mixed-signal answer with no second
  simulator**: >60 code models incl. gates, latches, flip-flops, shift
  registers, ADC/DAC bridges, 12-state digital node type, embedded
  event-driven algorithm coordinated with the analog solver; XSPICE sources
  are public domain —
  [XSPICE page](https://ngspice.sourceforge.io/xspice.html) (verified by
  direct fetch). KiCad already exposes these
  ([FOSDEM 2025 slides](https://fosdem.org/2025/events/attachments/fosdem-2025-5619-ngspice-xspice-elemental-devices-made-available-in-kicad/slides/238601/Fosdem202_8rKMx6m.pdf)).
  Netlist pattern: `A`-devices with `d_*` models + bridge models at domain
  crossings — **exact bridge names UNVERIFIED, confirm in the manual before
  coding.** Functional/timing-approximate (settable per-gate delays, not
  extracted parasitics).
- **Verilator vs Icarus** (only if real HDL blocks appear): Verilator
  compiles Verilog→C++ (fastest open HDL sim, needs C++ toolchain +
  testbench harness —
  [guide](https://verilator.org/guide/latest/verilator_first_intro.html)
  via veripool redirect — flagged); Icarus (`iverilog` + `vvp`) is lighter
  for small blocks. Both need a schematic→Verilog exporter first.
- **Logisim-evolution / Digital (hneemann)**: Java GUI-first tools with thin
  headless stories (Logisim `--test-vector` CLI —
  [test-vector doc](https://github.com/logisim-evolution/logisim-evolution/blob/master/docs/manuals/logisim-testvectors.md);
  Digital's CLI test-case evaluation UNVERIFIED whether implemented —
  flagged). Digital's *Verilog export* is the useful direction (design there,
  verify here). Both are GUI companions / test-vector exchange, never
  backends (JVM wall). MyHDL/PyRTL not evaluated — flagged.

### 4. ICs + whole board: `.SUBCKT` path first, block-by-block always

- **Manufacturer models**: nearly all analog ICs ship as `.SUBCKT`
  macromodels (`.lib`/`.mod` per part: [onsemi](https://www.onsemi.com/design/technical-documentation?type=Models),
  [Nexperia](https://www.nexperia.com/documentation-center?categoryLevel1=1550507662316&categoryLevel2=1681460750434)).
  Consumption is uniform and tiny: `.include` + `X<name> <pins> <subckt>`
  (ngspice manual ch. 2.6/2.8/2.10 per angle-(d), xhtml
  [manual](https://ngspice.sourceforge.io/docs/ngspice-html-manual/manual.xhtml) —
  chapter numbers NOT independently verified, flagged). Limits: TYPICAL-only
  (no corners unless shipped), approximate temp scaling, behavioral
  discontinuities kill convergence. Tool takeaway: **one primitive —
  external `.SUBCKT` include + X-line per part**; rest is search/download UX.
- **The 555** (blinky example): NO single canonical vendor model; circulating
  options are BJT-level replicas vs behavioral equivalents, e.g. community
  [NE555 model](https://github.com/MuMashhour/NE555-SPICE-Model) (unverified
  vs silicon — flagged). Astables need kickstart (`.ic`/UIC) + tight
  max-timestep. Whether TI still hosts an official model is UNVERIFIED.
- **IBIS is SI, not function** (buffer I/V + package parasitics, no internals —
  [ibis.org](https://ibis.org/)); serious solvers are commercial (HyperLynx
  tax); forum ships parsers (IBISCHK), not solvers ([tools](https://ibis.org/tools/)).
  **No verified zero-dep open IBIS transient solver found — flagged.**
  Verdict: later-or-never.
- **MCU/firmware-in-the-loop**: [Renode](https://renode.io/) (multi-MCU +
  peripheral co-sim, designed board-ish from the start), QEMU ARM
  (Cortex-M/STM32 machine scripts, partial board coverage), simavr
  (AVR-only, GDB/VCD, CI-scriptable). Only when firmware closes a loop
  through the board — otherwise pure cost.
- **Whole-board honesty**: hierarchical `.subckt`-per-module maps 1:1 onto
  `Module`/`use` includes — the netlist story is clean. What breaks first:
  (i) model availability (most BOM parts ship NO model — "whole board" is
  undefined), (ii) timestep collapse on switching edges. Simulatatable subset:
  power rails (DC + load-step), one analog front-end, one functional block.
  **Model-less policy must be explicit and loud**: idealize / stub / exclude
  with netlist-header warnings — never silent fake coverage.

## Open questions

1. XSPICE ADC/DAC bridge exact model names — confirm in the manual before coding.
2. Xyce license text (GPLv3 widely reported, not verified) — decides whether a
   second backend is even shippable.
3. libngspice availability per target distro — decides shared-lib viability.
4. libngspice vs subprocess performance at ocd board sizes — likely irrelevant
   (netlists are small); measure before building the ctypes bridge.
5. Open IBIS transient solver — none found; revisit if SI scope arrives.
6. 555 official vendor model URL — check TI part page before citing.

## Verification notes

- Directly fetched & confirmed this round: ngspice BSD license (devel page);
  batch/server flags + `SPICE_ASCIIRAWFILE` (Arch man page); shared-lib API
  shape + language bindings incl. PySpice (shared page); XSPICE 60+ blocks +
  public domain (xspice page); PySpice install pain + subprocess fallback +
  `--enable-ndev` caveat (install page); ngspice manual v47 + xhtml index
  (docs page); current sim surface (`sim.py`, `SimPlugin`, `sim` constraints).
- OpenAlex/arXiv not needed (docs/code research); HF/AlphaXiv unset (verified
  at session start) — irrelevant here, no gated calls attempted.
- Blocked, never inferred: ngspice-manual.pdf fetch (unsupported content
  type — xhtml/manual pages used instead); Verilator guide (cross-origin
  redirect, unfollowed); hneemann.de.PowerDNS fail (Digital details via GitHub
  mirrors); paywalled primaries (none encountered).
- Subagent [U]/UNVERIFIED flags preserved above: manual chapter numbers,
  `-a` flag semantics, ADMS-default linkage, ngspice-connect maturity,
 TI/ADI model-page specifics, NE555-vs-silicon, open IBIS solver, Digital CLI
  status, MyHDL/PyRTL, TopSPICE, Xyce license + Python-API absence.

## References

- ngspice manual + xhtml — https://ngspice.sourceforge.io/docs.html · https://ngspice.sourceforge.io/docs/ngspice-html-manual/manual.xhtml
- ngspice shared lib — https://ngspice.sourceforge.io/shared.html
- ngspice XSPICE — https://ngspice.sourceforge.io/xspice.html
- ngspice devel (license) — https://ngspice.sourceforge.io/devel.html
- ngspice man page — https://man.archlinux.org/man/ngspice.1.en / https://manpages.debian.org/trixie/ngspice/ngspice.1.en.html
- PySpice repo + install — https://github.com/PySpice-org/PySpice · https://pyspice.fabrice-salvaire.fr/releases/v1.3/installation.html
- ngspyce — https://github.com/endolith/ngspyce
- ngspice-connect — https://github.com/jchabloz/ngspice-connect
- Xyce — https://github.com/Xyce/Xyce · https://qucs-s-help.readthedocs.io/en/latest/overview/choosing-a-sim-backend.html
- LTspice switches/license — https://ltwiki.org/LTspiceHelpXVII/LTspiceHelp/html/Command_Line_Switches.htm · https://www.ltwiki.org/LTspiceHelpXVII/LTspiceHelp/html/License_Agreement_Disclaimer.htm
- PyLTSpice — https://github.com/nunobrum/PyLTSpice
- QUCS-S — https://qucs-s-help.readthedocs.io/en/latest/welcome/what-is-qucs-s.html
- SKiDl — https://github.com/devbisme/skidl
- Gnucap — http://www.gnucap.org/dokuwiki/doku.php?id=gnucap:about
- Logisim-evolution + test vectors — https://github.com/logisim-evolution/logisim-evolution · https://github.com/logisim-evolution/logisim-evolution/blob/master/docs/manuals/logisim-testvectors.md
- Digital — https://github.com/hneemann/Digital
- Verilator — https://github.com/verilator/verilator
- Icarus — http://iverilog.icarus.com/
- FOSDEM XSPICE-in-KiCad — https://fosdem.org/2025/events/attachments/fosdem-2025-5619-ngspice-xspice-elemental-devices-made-available-in-kicad/slides/238601/Fosdem202_8rKMx6m.pdf
- onsemi models — https://www.onsemi.com/design/technical-documentation?type=Models
- Nexperia docs — https://www.nexperia.com/documentation-center?categoryLevel1=1550507662316&categoryLevel2=1681460750434
- NE555 model — https://github.com/MuMashhour/NE555-SPICE-Model
- IBIS — https://ibis.org/ · https://ibis.org/tools/ · https://ibis.org/home/articles/ed_ibis1.htm
- free-IBIS status — https://ee-training.dk/simulation/free-ibis-simulator-kicad-8/
- Renode — https://renode.io/
- QEMU STM32 — https://qemu.googlesource.com/qemu/+/9cf3bc65afdb63f6fc28560274600b4e6e0c91ca/docs/system/arm/stm32.rst
- simavr CI example — https://github.com/GeoffWilliams/pio-simavr-unit-test-example
