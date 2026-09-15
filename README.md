# OCD Circuit

A circuit design tool for people with OCD. Your traces are parallel.
Your silkscreen is aligned. Your DRC is clean. It has to be.

Needs: Python 3.11+, no other deps (`rich` optional for pretty CLI;
`numpy` optional for the SIMD placer). Developing: `pip install -r
requirements-dev.txt` — it pins the checker `make check` runs.

## 10 minutes from KiCad

Already have a schematic? Bring the board, keep the workflow:

```bash
# 1. import your footprints (or a whole .kicad_pcb layout)
python -c "from ocdcircuit.circuit import Board
b = Board('mine')
print(b.import_fp('kicad', path='boards/bme690/fp/PinHeader_1x07_P2.54mm_Vertical.kicad_mod'))
# ...or b.import_fp('pcb', path='mine.kicad_pcb') → parts + nets"
# 2. write it back as .ocd, solve, check against your fab
python -m apps.ocd boards/blinky_555.ocd        # .ocd → DRC → Gerbers + KiCad
# 3. open the cockpit and drag it into shape
python -m apps.studio boards/blinky_555.ocd     # visual editor → localhost:8077
# 4. ship: one zip, or push the .kicad_pcb back to KiCad
```

New board instead? Six lines, then `ocd run` (full spec: `docs/OCD.md`):

```ocd
board rc 20x10
part R1 R0805 1k x=3 y=5
part C1 C0805 100n
N :: R1.2 <--> C1.2
GND :: R1.1 <--> C1.1
```

```bash
python -m apps.ocd --placer compact --router maze boards/blinky_555.ocd  # cleanest
python -m apps.ocd --fab oshpark boards/psu.ocd # same board, stricter fab
python tests/test_all.py                     # one self-check for everything
mypy     # strict, zero errors
```

## The language (full spec: `docs/OCD.md`)
```ocd
board blinky555 40x30 2L     # every board starts exactly like this
use psu.ocd as PSU           # include another board (refs → PSU_*, VCC/GND join)
part U1 SOIC8 NE555          # every part has a ref, a footprint, a place
VCC :: PSU_J1.1 <--> U1.8 <--> R1.1  # every net lists every pin, no exceptions
fix PSU_J1 at 3 15           # dragged in studio? lands here, kept forever
keep U1 near C1 3            # related parts stay together. everything has its place
route GND on 1               # ground goes on the bottom. obviously
power VCC GND                # power traces are 0.5mm. obviously
silk 2                       # refs + values + outlines. level 3 labels nets too
```

`board.toml` next to the `.ocd` sets project defaults (CLI flags win):
`fab` / `placer` / `router` / `drc` (list) / `mask` / `style`.
Values are validated — `ocd plugins [kind]` lists legal picks.
Every surface honors them: CLI, studio dropdowns, and MCP tools.

## Studio

`.ocd` editor with highlighting | PCB (drag parts — they stay where dropped,
everything else re-solves around them) | schematic | live 3D | DRC panel.
Pick placer/router/fab/silk level from dropdowns — parts glide to the new
solution with easing, traces grow net by net. **candidates** generates N
layouts in a filmstrip — click one to pick it, drag parts to nudge+fix,
re-run the same or a different engine, rinse and repeat. Every step shows a
routing-feasibility badge (`2L routable` / `1L unroutable`) per layer count.
The chrome follows the recompile.online design guide: paper ground, white
panel cards, the `.ocd` editor as the one dark terminal, one signal green for
actions and live state, every control carrying a visible word, every status
stated as a word in a pill.

## Under the hood ([architecture](docs/ARCHITECTURE.md), authoring: `docs/PLUGINS.md`)

- **Context paradigm** ([the paper](https://github.com/cordiverse/paper)):
  every edit carries its inverse, every module declares its deps. Undo is
  total. ([ADR-0001](docs/ADR-0001-context-core.md))
- **Diffusion placer**: parts drift along net-springs with Langevin noise,
  hard box-penetration repulsion, multi-seed best-of. Streams animation
  frames. ([ADR-0002](docs/ADR-0002-solver.md))
- **Everything is a hot-swappable plugin**: placers, routers, DRC, exporters
  (Gerbers, KiCad, EasyEDA, `.ocd`, JSON), parts library (all with 3D
  bodies), renderers (PCB/SCH SVG, PNG, STL, textured glTF, 3D HTML).
- **Fab profiles**: JLCPCB (+flex), PCBWay, OSH Park, Seeed, Aisler,
  Eurocircuits, NextPCB, ALLPCB, Sierra, Advanced Circuits — DRC checks
  your board against the factory you actually ordered from
  ([inventory](docs/FAB.md)). `make fabsweep` runs every board × every fab.
- **Copper pours**: `pour GND on 0` renders negative Gerber planes + KiCad
  zones; routers skip poured nets, DRC exempts plane copper and flags
  keepout-stranded pads (`pour-isolated`).
- **mypy strict**, zero `Any`, zero errors. The code is aligned too.
- **`block`/`instance` + hierarchical placer**: repeat a channel 3×, solve
  it once, stamp rigidly (`placer:hierarchical`). Pico demo does exactly this.
- **1–32 layers**: placer, maze router (any-layer vias + rip-up retry),
  DRC, Gerber (`GTL/G1..Gn/GBL`) and KiCad (`F.Cu/In1..Bn`) exports all
  handle 1–32 layers. 1-layer boards report `jumper` wire bridges.
- **Mix-and-match plugins**: placers `diffusion` (wirelength) / `compact`
  (area) / `thermal` (heat); routers `lroute` (fast estimate) / `maze`
  (DRC-clean A* with vias); silk `ref` / `full` / `fab`. Swap live via
  `b.use()`, CLI flags, studio dropdowns, or MCP.
- **`nc` + ERC**: `drc:erc` flags unconnected pins, power shorts, dupes;
  `nc` marks intentional no-connects (USB-C demo has 14).
- **One-zip fab bundle** (`export:bundle`), **snapshot golden tests**,
  **embedded calculators** (IPC-2221 trace width, via current, divider).
- **Board knowledgebase** (`kb/` beside the board): notes you drop in, plus
  datasheets — `ocd kb fetch` pulls them per part from a `datasheet=` URL or
  an `lcsc=` code, `ocd kb add` takes a path/url, `ocd kb search <term>`
  greps notes *and* PDF text (`pdftotext`, cached), and `ocd kb ask "<q>"`
  recalls the passages that answer it (embeddings, falling back to terms; no
  index to maintain). Agents traverse the same thing over MCP, and studio has
  the same panel — so "what does U3's datasheet say about VIN" is a lookup.
- **Foreign footprints**: `fp` loads KiCad `.kicad_mod`, Eagle `.lbr`,
  tscircuit/EasyEDA JSON, or native `.fp` — plus Eagle `.brd` and
  `.kicad_pcb` board import, and EasyEDA Std JSON export.
- **Textured 3D**: `render("gltf")` with PBR materials (mask/copper/silk/
  chip/tantalum/electrolytic/LED/steel); studio canvas shades faces live.
- **Simulators are plugins** (`simulate:mna`): DC operating point +
  transient (trapezoidal→Euler MNA, stdlib) via `sim` constraints;
  `ocd --sim dc|tran`, studio ⚡ readout, MCP `simulate` tool.
- **Importers/exporters are plugins**
  (`importer:fp/kicad/eagle/eagle-brd/tscircuit/pcb/easyeda`,
  `exporter:jlc/kicad/easyeda/…`): `b.import_fp("easyeda", path=…)`.
- **Agents are first-class**: `apps/mcp.py` is an MCP stdio server (30 tools:
  load/solve/patch/set_state/undo/context/place/candidates/apply_candidate/feasible/route/check/score/diff/export/render/xray/quote/footprints/fabs/…)
  — any MCP client can drive boards, gallery-pick layouts, probe routability,
  browse footprints + fab profiles, search the board's kb/ notes + datasheets,
  and inspect live fiber/coeffect state.
- **X-ray check** (`renderer:xray` + `xray:std`): stacked-copper reference
  view of the board; upload the fab's x-ray PNG and get a score plus boxed
  divergences (`ocd xray board.ocd fab.png`, studio x-ray panel, MCP `xray`).
- **PCB photo scan** (`scan:photo`): reverse-engineer a physical board from
  a pile of phone photos. Registers every shot against the sharpest one
  (pyramid search on illumination-invariant structure, so glare and exposure
  cannot win), median-stitches per side, writes a contrast/sharpen/edge/silk/
  copper stack for reading markings, recovers component standoff from
  residual parallax, and bakes a 3D gaussian splat (`.ply`). A vision model
  then reads the stack and drafts a new `.ocd`
  (`ocd scan --mm 100 photos/*.jpg`, `--no-llm` for artifacts only).
  Tell it what you know and it uses it: `--note "scope PSU"`, `--doc
  manual.pdf` (PDFs via pdftotext). It asks back what the photos cannot
  settle — answer with `--answer "question=reply"` and re-run for a
  better-informed pass. Context is evidence, not authority: where a manual
  and the board disagree, the model is told to believe the board.
  Measured on a real board with a published schematic
  (`python -m tools.scanbench`): 83% of handheld frames lock to 0.04°/0.03%,
  the gated stitch lands 2.1x closer to the true board than the sharpest
  single photo, and enhancement lifts local contrast up to 2.7x.
- **Fab price comparison** (`quote:std`): bare PCB per fab + JLC assembly
  with parts (`ocd quote board.ocd 5`, studio quote dropdown, MCP `quote`).
  Estimates from published pricing — parts via knoll's live JLC lookup or
  `price=` attrs.
  `match`/`diff` constraints cover length + diff pairs.
- **Context paradigm** ([the paper](https://arxiv.org/abs/2608.25512)):
  every edit carries its inverse (`Context.effect`, fires once), every module
  declares its deps (`Fiber` LOADING→ACTIVE→UNLOADING→INACTIVE,
  dependents drain before recovery). Loader reconciles declarative entries
  per-field; HMR classifies + reloads transactionally. Undo is total.

Ports: `tools/tscircuit.py` converts tscircuit projects (tsx + circuit.json)
to `.ocd` — see `boards/pico_tmc2209/` (Pico + 3×TMC2209, 20 parts).
`tools/mitox.py` ports LCSC-footprint boards, harvesting exact pad geometry
into `.fp` files — see `boards/mitox/` (43 parts, 4L, full fab output).
`tools/atopile.py` ports atopile projects (`~` wiring, modules, kicad_mod
footprints, LCSC) — see `boards/bme690/`, `boards/ne555/`,
`boards/breath_ketone/`, `boards/e2e_driver4/`. Porter contracts: `docs/PORTS.md`.

Layout: `ocdcircuit/` (core, circuit, parts, solver, maze, drc, fab, silk,
export, agent, score, diff, plugins), `tools/` (importers),
`apps/` (ocd, studio, mcp), `docs/` (ADRs, research), `boards/` (one dir per board),
`benches/`, `tests/`.
