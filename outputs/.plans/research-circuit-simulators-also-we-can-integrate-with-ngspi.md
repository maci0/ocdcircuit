# Plan: circuit simulator integrations (ngspice + others, digital, IC/board-level)

## Key questions
1. ngspice: how to integrate (subprocess + netlist export vs shared-lib `ngspice.so` API)? Netlist format needed (`.cir`/`.cki`)? What analyses (DC/AC/tran/noise)? License fit (BSD-ish, redistributable)? What does ocd need to emit (parts → SPICE elements, `sim` constraints → sources/analyses)?
2. Other analog/mixed SPICE: Xyce (Sandia, parallel), LTspice (Windows/Wine, proprietary), QUCS-S/ngspice backends, SKiDl as netlist-gen precedent. Which are scriptable headless?
3. Digital/logic: Logisim-evolution, Digital (hneemann), Verilator/Icarus for HDL blocks, event-driven vs SPICE for digital sections. How to co-simulate digital + analog (mixed-signal boundary)?
4. IC-level: what if a part IS an IC (opamp/555/MCU)? Model sources: manufacturer SPICE models (.lib/.mod), IBIS for SI, behavioral B-sources, MCU firmware-in-the-loop (simavr, QEMU)? Whole-board: hierarchical netlist, multi-board, performance at thousands of nets.
5. Cheapest integration order for ocd: export `.cir` first (zero dep, ngspice-agnostic), then subprocess runner, then back-annotation. What stays stdlib (current MNA) vs what shells out.

## Source strategy
- Official docs first: ngspice manual (ngspice.sourceforge.io/docs), Xyce docs (sandia), Verilator docs, Logisim-evolution GitHub, Digital (hneemann.de), QUCS-S, SKiDl.
- Papers/metadata via OpenAlex + arXiv only if simulator-adjacent claims arise; this is mostly docs/code research.
- Local: `ocdcircuit/sim.py` (current MNA), `plugins.py` simulate plugin, `circuit.py` simulate dispatch, `agent.py` sim constraints, `export.py` (netlist export patterns), tests.
- HF/AlphaXiv unset → blocked for gated calls (not expected to matter). No paywalls.

## Scale decision
Lead-owned with 4-way fan-out (one prompt per angle below), then lead synthesizes. No workflow tool.

## Task ledger
- [x] Map local sim surface (sim.py, plugin, constraints)
- [x] Write this plan
- [x] Fan out 4 subagent angles (a–d)
- [x] Direct-verify anchor claims (ngspice API, licenses, tool repos)
- [x] Write `outputs/research-circuit-simulators-also-we-can-integrate-with-ngspi-brief.md`
- [x] Copy to `docs/simulators.md` + cross-link
