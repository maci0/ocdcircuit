# SaaS — beating Flux.ai (feature map + our two options)

Position: Flux is expensive and the Copilot output disappoints. Our verified
workflow — DeepSeek/Claude + tscircuit + ocdcircuit — already beats it on
price (free/local), iteration (unmetered), and agent-operability
(git-diffable `.ocd` + MCP). This doc maps Flux, prices the field, and shows
where we win and what to build.

Pricing numbers are mid/late-2026 public pages, not gospel — re-check before
quoting. EDA sources:
[Flux EDA](https://www.flux.ai/p/nb/eda-software),
[prompt sim](https://www.flux.ai/p/blog/simulate-circuits-with-a-prompt?trk=public_post_comment-text),
[Copilot upgrade](https://www.flux.ai/p/blog/design-circuits-with-natural-language-copilot-upgrade?utm_source=linktree&utm_medium=organic_social&utm_campaign=New+Copilot),
[Enterprise](https://www.flux.ai/p/enterprise),
[Flux pricing breakdown](https://www.protoflow.ai/blog/flux-ai-pricing),
[Quilter on 2026 PCB pricing models](https://www.quilter.ai/blog/generative-pcb-design-tool-pricing-in-2026-a-guide-to-saas-credits-and-subscriptions#1#1),
[DeepPCB pricing](https://deeppcb.ai/pricing/),
[Quilter vs DeepPCB](https://www.protoflow.ai/compare/ai-pcb-autorouter-comparison),
[online tools compared](https://www.protoflow.ai/compare/online-pcb-design-tools),
[ProtoFlow vs Flux](https://www.protoflow.ai/compare/flux-ai-alternative),
[tsci export](https://docs.tscircuit.com/command-line/tsci-export).

## 1. Flux feature map (everything they sell)

Design flow: plan assist (brief → blocks/BOM/stackup/self-written notes) →
schematic (symbols+footprints, sublayouts, net classes) → multi-layer layout
(DXF/SVG outline, diff-pair/impedance rules) → AI auto-layout → continuous
DRC/ERC + AI Review → prompt-SPICE (AC/transient/Bode, 340k models, custom
upload) → fab outputs (Gerber X2, ODB++, IPC-2581, Excellon, IPC-356A, BOM,
PnP, STEP, DXF, PDFs) → vendor handoff (JLCPCB, PCBWay, NextPCB, OSH, Seeed,
Aisler, Lion). Partners/collab: realtime multiplayer, comments, roles,
version history, browser-native, KiCad import (all plans) / Altium-Cadence-
Eagle (Pro+), JEP30/Allegro/Altium part export + live pricing, public MCP
server + chat/voice. Enterprise: SSO, hidden workspaces, audit logs, SOC 1/2
II, AES-256/TLS, no training on private data. Funnel: community (6.4M
projects), templates, docs/YouTube/Slack, in-app support.
Calculators in-tool: divider, resistor color, trace width, via current,
impedance, Gerber viewer.

## 2. Why Flux loses (the opening)

1. **Export is the paywall.** Per Flux's own FAQ, private-project editing,
   export, and AI all require paid plans
   ([source](https://www.protoflow.ai/blog/flux-ai-pricing)). Your design
   is held hostage: ~$20/editor/mo entry (10 ACU, ~$2.50/ACU overage),
   Pro ~$140+/editor/mo. Free lane = public projects only.
2. **The meter rations iteration.** ACUs burn on every Copilot action, so
   exploration — the thing hardware design *is* — gets budgeted instead of
   done. Two identical teams pay different bills depending on how hard they
   think ([Quilter's model guide](https://www.quilter.ai/blog/generative-pcb-design-tool-pricing-in-2026-a-guide-to-saas-credits-and-subscriptions#1#1)
   calls this out as the industry's core budgeting headache).
3. **Copilot quality doesn't justify the price.** Hands-on verdict: useless
   output at premium price. Market confirms the churn risk — the whole
   "free Flux alternative" category (ProtoFlow's top SEO page is literally
   [Flux.ai Alternative](https://www.protoflow.ai/compare/flux-ai-alternative))
   exists because engineers bounce off exactly these three walls: metered
   AI, per-seat pricing, gated export.
4. **Cloud lock-in, no local files.** Browser-only means no git, no offline,
   no scripting your own flow. Designs live in their workspace or not at all.
5. **Community trust dent.** June 2026: Flux's lawyers sent a demand letter
   that paused Adafruit's blog
   ([Slashdot](https://yro.slashdot.org/story/26/06/02/1647209/adafruit-pauses-blog-after-demand-letter-from-fluxais-lawyers)).
   Suing the maker community's paper of record is not a trust strategy.

## 3. What everyone charges (Sept 2026 shape)

| Tool | Model | Entry price | What you get free |
|------|-------|-------------|-------------------|
| Flux | per-seat + ACU meter | ~$20/editor/mo, Pro ~$140+ | public projects; manual edit unlimited, AI credits limited |
| Quilter | seat-free, pay-per-download (pin-count) | free tier, unlimited iterations | iterate free, pay on download |
| DeepPCB | pay-as-you-go time meter | $30/1hr, $280/10hr, $800/30hr ([pricing](https://deeppcb.ai/pricing/)) | 30-min trial (1 board, ≤4L, 150 airwires) |
| EasyEDA | free, JLCPCB/LCSC-tied | free (Std), Pro adds features | full browser ECAD free |
| Altium | seat license | $7–9k/seat perpetual | nothing |
| KiCad | open source | $0 | everything, local |
| tscircuit | open source (MIT) + registry | $0 | code→board, exports (below), KiCad handoff |
| **ocdcircuit (us)** | **open, local, unmetered** | **$0** | **everything in §5, forever (funnel)** |

Takeaway: every paid tool meters *iteration* (ACUs, minutes, downloads).
Nobody sells unmetered local solve. That's the gap we own.

## 4. Other players — full field map (Sept 2026 shape)

AI maturity runs L1 copilot → L2 assisted execution → L3 autonomous layout
([AtlasPCB landscape](https://www.atlaspcb.com/blog/ai-pcb-design-tools-landscape-2026-copilot-autonomous-layout/#cadence-allegro-x-ai)).
EDA hit $4.2B in Q1 2026, 20th straight growth quarter. Everyone below is
priced or positioned against that spectrum.

| Player | What it is | Price / access | Verdict for us |
|--------|------------|----------------|----------------|
| [Quilter](https://www.quilter.ai/blog/series-b?trk=public_post_comment-text#1) | L3 autonomous place+route, physics/RL, own CAD kernel. $25M Series B Oct 2025 (Index, Benchmark). Project Speedrun: i.MX8 Mini computer, fabbed + booted. ≤8L/~500 parts in 10–30 min | Seat-free, pay-per-download (pin-count), unlimited-iteration free tier | Downstream router, needs a schematic in — complementary. Cloud vs our local/unmetered is the fight |
| [DeepPCB](https://deeppcb.ai/pricing/) (InstaDeep) | L3 RL cloud router | Pay-as-you-go: $30/1hr, $280/10hr, $800/30hr; 30-min trial (1 board, ≤4L, 150 airwires, 100 comps) | Same slot as Quilter, burst-friendly. Benchmark against our maze router |
| [ProtoFlow](https://www.protoflow.ai/compare/flux-ai-alternative) | Free desktop AI capture (prompt → part-backed schem → KiCad), LCSC/DigiKey/Mouser, DRC/ERC | Free, no seats/meter/export gate | Closest philosophy; capture-only (no routing). Validates our model — differentiate on local solvers |
| [CELUS](https://www.celus.io/news/reducing-bom-cost-early-a-practical-walkthrough-with-the-celus-design-platform) | Requirements → schematic/BOM, deterministic (not LLM), digital-twin library, exports to Altium/Cadence/Siemens/Zuken | Credit subscription by complexity, enterprise sales | Pre-layout only, no layout. Enterprise lane we skip |
| [Circuit Mind](https://agentaya.com/ai-review/circuitmind/#1) (ACE) | Block-diagram → schematic, cost/size/power trade sliders, live supply chain | No public pricing, demo-gated, no trial | Same slot as CELUS, pro-teams only. Skip |
| [JITX](https://www.jitx.com/?utm_content=328363650&utm_medium=social&utm_source=linkedin&hss_channel=lis-R66mIFj2pp) | Code-first hardware (Stanza), YC S18, constraint-driven | Enterprise-leaning, no public pricing | Church-adjacent (circuits-as-code). Watch, stay `.ocd`/tsx-compatible |
| atopile | OSS Python HDL, modules/interfaces, KiCad-bound. We already port it (`tools/atopile.py`: bme690, ne555, breath_ketone, e2e_driver4) | Free OSS | Ally, like tscircuit |
| Copperhead | "Cursor for PCBs" — KiCad AI verification agent | Early/prototype, claims disputed | Watch — closest to our MCP-in-KiCad play, unproven |
| [EasyEDA Pro](https://drmachine.tech/en/wiki/easyeda-pro-01-intro) (JLCEDA) | Free browser+desktop ECAD, LCSC catalog + one-click JLCPCB, push-shove/diff pairs/buried vias, NGSpice, 3D | Free (Std + Pro); monetizes via fab, not seats | The free incumbent. Integration target #2; copy the fab-affiliate model |
| KiCad 9 | Free OSS, vendor-neutral, action-plugin API | $0 | Home turf for Option B |
| Altium / Cadence Allegro X AI / Siemens Xpedition | Enterprise EDA + ML routing, sim ecosystems (Sigrity/Clarity), BGA escape 2000+ pins | Altium ~$7–9k/seat perpetual; others quote-based | Skip until a paid pilot demands it |
| Eagle/Fusion 360 | [EAGLE dead June 7, 2026](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Autodesk-EAGLE-Announcement-Next-steps-and-FAQ.html#1) — sales/support ended | — | Migration pool: ex-Eagle users picking KiCad/EasyEDA now. Target them |

Net: L3 routers (Quilter/DeepPCB) need schematics and charge per compute —
we undercut with local unmetered solve. Capture tools (ProtoFlow/CELUS/
Circuit Mind) stop at schematic — we finish the board. Free tools
(KiCad/EasyEDA/tscircuit/atopile) are allies and distribution, not enemies.

## 5. tscircuit — the ally, not the competitor

What tscircuit is (verified in [export docs](https://docs.tscircuit.com/command-line/tsci-export)):
React/TS circuits-as-code, `tsci` CLI (`init/dev/build/check/simulate/
snapshot/export`), package registry (`tsci add/search`), `tsci export`
→ Gerbers, schematic/PCB/assembly SVG, `specctra-dsn`, `spice`,
`kicad_sch`/`kicad_pcb`/`kicad_zip`, `gltf`/`glb`/`step`, plus
[autorouting API](https://github.com/tscircuit/docs/blob/7363f75c3810df0c3a7bf62b906b3ade06103af6/docs/web-apis/autorouting-api.mdx#1),
`<analogsimulation>`, KiCad import guides, JLCPCB footprints.

Why it matters to us:
- **Interop already works.** `tools/tscircuit.py` ports tsx + circuit.json
  → `.ocd` (exact pad geometry → `.fp` per footprint); live boards:
  `boards/pico_tmc2209` (20 parts), `boards/mitox` (43 parts, 4L).
  Contract: `docs/PORTS.md`.
- **Complementary engines, not overlapping.** tscircuit owns capture +
  registry + export breadth; we own local place (diffusion/compact/
  thermal/hierarchical), any-layer maze route + rip-up, fab-profile DRC,
  candidate gallery + feasibility badge. Their router shells to external
  services; ours runs offline, unmetered.
- **Same church.** Open-source, code-first, KiCad-compatible, local files.
  Their users are our users. Alliance posture: stay port-compatible both
  ways (we import tsx today; keep KiCad as the shared interchange so their
  export is our import and vice versa).

## 6. Our stack — why DeepSeek/Claude + ocdcircuit beats Copilot

The winning loop, already working in this repo:
`LLM (any model, your key, your bill) → .ocd text / MCP tools → solve →
DRC → Gerbers`. No seat, no ACU, no cloud.

- **`.ocd` is LLM-native; Flux's canvas is not.** One fact per line,
  git-diffable, byte-round-tripped (goldens enforce). An LLM can read,
  patch, and review a board as text — no screenshots, no clicking.
- **MCP is the Copilot without the rent.** `apps/mcp.py`: 24 tools
  (load/solve/patch/set_state/undo/place/candidates/apply_candidate/
  feasible/route/check/score/diff/export/render/simulate/…). Any MCP
  client (Claude, DeepSeek agents, anything) drives boards today.
- **Undo is total.** Every edit carries its inverse (`core.py`, ADR-0001).
  An agent can experiment recklessly — rollback is free. Flux's
  "reviewable and reversible" is a feature; ours is the architecture.
- **Solvers run local, unmetered.** 5 placers × 4 routers × 3 silk levels,
  hot-swapped per board (`ocdcircuit/plugins.py`, 40+ plugins), multi-seed
  candidates with filmstrip picking in studio. Iterate 200× — cost is $0
  and a warm CPU.
- **Sim + calc without tab-switching.** `simulate:mna` (DC + transient,
  stdlib) + `simulate:ngspice`/`gates` plugins, `calc.py` (IPC-2221, via,
  divider). Flux's prompt-sim needs their cloud + meter; ours needs numpy-less stdlib.
- **Fab honesty.** 5 vendor profiles (JLC/PCBWay/OSH/Seeed/Aisler),
  one-zip bundle, snapshot goldens. DRC errors block fab, warnings don't.
  AtlasPCB's fab-side note applies to us too: manufacturers accept
  AI-generated Gerbers but flag aggressive minimum-feature use, copper
  imbalance, missing notes — our fab profiles + DFM honesty are the answer.
- **Gaps, stated plainly:** no AC-sweep/Bode UI, no 340k model library, no
  live pricing/stock, no ODB++/IPC-2581/STEP, no multiplayer, no datasheet→
  footprint. §8 orders them by revenue impact.

## 7. Options

**A — full SaaS (clone Flux's business, undercut the price).** Hosted studio
+ accounts → private/export gate → per-editor sub with a *generous or flat*
AI allowance (the anti-ACU pitch: "iterate free, pay for seats") → Copilot
v1 as orchestration over our MCP → live sourcing (LCSC/DigiKey/Mouser data
contract — the known blocker) → enterprise row only on paid pilot. Cost:
months for 1–4, real money at 5–6 (data + SOC 2).

**B — API key + billing inside other EDAs (recommended first).** Thin HTTP
solve API over Board dispatch → KiCad action plugin with layout-preserving
write-back (LANDSCAPE's `update_pcb` gap — the one build that matters) →
EasyEDA/JLCEDA extension second → Stripe prepaid solve-credits per
candidate-batch (seeds × rip-up = real compute unit, no ACU psychology) +
fab-affiliate kickback. Skip Altium/Cadence until an enterprise pays.
`ponytail: global job queue to start, per-user concurrency limits when abuse appears`.
B needs no collab infra, no browser EDA rewrite, no community bootstrap, no
compliance to sell job #1 — and it monetizes exactly the engines Flux can't
offer locally.

## 8. Roadmap (build in this order)

1. Solve-API + KiCad round-trip + Stripe credits (B's MVP — funds the rest).
2. Copilot-over-MCP reference config (Claude/DeepSeek system prompt +
   tool policy in-repo; proves the "better than Copilot" claim publicly).
3. Live sourcing: LCSC price/stock on BOM (unblocks fab-affiliate revenue).
4. AC-sweep/Bode + model upload in sim (closes the demo gap vs prompt-sim).
5. ODB++/IPC-2581/STEP export (enterprise handoff unblock).
6. Hosted studio (A) only when solve-credits prove demand.

Skipped: Altium/Cadence import, SSO/audit, community platform — add when a
paying pilot names them.

## 9. Open questions

1. Free-local forever? (Recommended yes — funnel + Flux-differentiator.)
2. First integration: KiCad plugin vs EasyEDA extension — KiCad (open API,
   interchange closest).
3. Credit unit: per-candidate-batch (maps to real compute).
