# SaaS — beating Flux.ai (feature map + our two options)

Position (opinion, not measurement — no controlled Copilot bake-off exists;
proof path: §8.2 Copilot-over-MCP config): Flux is expensive and the Copilot
disappoints in hands-on use. Our verified workflow — DeepSeek/Claude +
tscircuit + ocdcircuit — wins on price (free/local), iteration (unmetered),
and agent-operability (git-diffable `.ocd` + MCP). "Already beats Flux" here
means that price/iteration/agent triad, not a controlled quality bake-off.
This doc maps Flux, prices the field, and shows where we win and what to build.

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
   done. Teams plan is $158/editor/mo with 100 pooled ACUs and $2 overage,
   so a 5-editor team iterating hard can double its bill in overage alone.
   ([Quilter's model guide](https://www.quilter.ai/blog/generative-pcb-design-tool-pricing-in-2026-a-guide-to-saas-credits-and-subscriptions#1#1)
   calls this out as the industry's core budgeting headache).
3. **Copilot quality vs price (Position, not bake-off).** Hands-on opinion:
   output quality does not justify the meter. No controlled side-by-side
   exists — proof path is §8.2 (Copilot-over-MCP). Market confirms churn
   risk — the whole "free Flux alternative" category (ProtoFlow's top SEO
   page is literally
   [Flux.ai Alternative](https://www.protoflow.ai/compare/flux-ai-alternative))
   exists because engineers bounce off exactly these three walls: metered
   AI, per-seat pricing, gated export.
4. **Cloud lock-in, no local files.** Browser-only means no git, no offline,
   no scripting your own flow. Designs live in their workspace or not at all.
5. **Community trust dent.** June 2026: Flux's lawyers sent a demand letter
   that paused Adafruit's blog
   ([Slashdot](https://yro.slashdot.org/story/26/06/02/1647209/adafruit-pauses-blog-after-demand-letter-from-fluxais-lawyers)).
   Suing the maker community's paper of record is not a trust strategy.

## 3. What everyone charges (Sept 2026, verified this round)

Sources: [Flux pricing page](https://www.flux.ai/p/pricing) (JS-rendered; hard
numbers via [Wayback 2026-08-23](https://web.archive.org/web/20260823143038/https://www.flux.ai/p/pricing)),
[ProtoFlow Flux breakdown](https://www.protoflow.ai/blog/flux-ai-pricing),
[DeepPCB pricing](https://deeppcb.ai/pricing/),
[Quilter pricing](https://www.quilter.ai/pricing) + [free tier](https://www.quilter.ai/free-ai-pcb-design) + [startups](https://www.quilter.ai/ai-pcb-design-for-startups),
[Vendr Altium data](https://www.vendr.com/marketplace/altium),
[GoEngineer Cadence guide](https://www.goengineer.com/guide-to-buying-cadence-pcb-design),
[Siemens Xpedition Standard](https://blogs.sw.siemens.com/electronic-systems-design/2025/05/22/introducing-xpedition-standard-scalable-pcb-design-power-for-growing-teams/),
[CELUS](https://www.celus.io/), [Circuit Mind](https://www.circuitmind.io/product),
[JITX plans](https://www.jitx.com/plans), [EasyEDA std-vs-pro](https://prodocs.easyeda.com/en/introduction/std-vs-pro/),
[KiCad 9 notes](https://www.kicad.org/blog/2025/02/Version-9.0.0-Released/),
[tscircuit registry API](https://docs.tscircuit.com/web-apis/the-registry-api).

| Tool | Model | Entry price | What you get free |
|------|-------|-------------|-------------------|
| Flux | per-editor + ACU meter | Explore $20/mo → Build $60/mo → Pro $200/mo → Teams $158/editor/mo (100 ACUs, $2 overage, pooled) → Enterprise custom. 14-day trial, then private/edit/export/AI all paywalled | public projects only |
| Quilter | seat-free, pay-per-project by **unrouted pin count** | quote-only, no public $ (talk to sales). Unlimited iterations + parallel jobs + guided onboarding per project; BOM drift >10% = new project. Cloud (SOC 2) or self-hosted K8s+GPU (higher cost) | $0 tier: unlimited iterations, all features — but academic/personal/<10 ppl/<$50K only, public cloud, and **board metadata trains their models** |
| DeepPCB | pay-as-you-go time meter | $30/1hr, $280/10hr, $800/30hr | 30-min trial (1 board, ≤4L, 150 airwires) |
| CELUS | credit sub by complexity, enterprise sales | no public pricing page (/pricing 404s); self-serve signup + sales motion | trial-ish signup only |
| Circuit Mind ACE | demo-gated, unpublished | "Individual/Professional/Enterprise" named, zero numbers; arrange-a-demo funnel only | none (existing-user login only) |
| JITX | hybrid: free self-serve + enterprise sales | Free tier = open-source (CERN OHL-P v2) designs only, free indefinitely; proprietary/PLM/air-gapped = talk to sales | free tier above |
| ProtoFlow | free desktop AI capture | $0, no seats/meter/export gate | everything (capture only, no routing) |
| EasyEDA Std + Pro | free, JLCPCB/LCSC-tied | **both free** — monetizes via fab orders, not licenses | full browser+desktop ECAD, LCSC live stock/price, one-click JLC order |
| Altium Designer | seat license | sub $4.5–7.5k/seat/yr; perpetual $7–9k + 17–22%/yr maint; median buyer $17,955/yr. CircuitStudio $1.5–3k; Altium 365 +$1–2.5k bundled ($2.5–5k standalone) | CircuitMaker (free community), 15-day trial, Launchpad startups, academic ~70–90% off |
| Cadence OrCAD X / Allegro X | seat lease | OrCAD X from $2,088/yr; Allegro X from $5,707/yr lease (~$18,390 perpetual). ~10–20% under Altium | OrCAD X 30-day commercial trial + 6-mo academic |
| Siemens Xpedition / PADS | seat + quote | Xpedition Standard $2,999/seat/yr (SMB tier, May 2025). Enterprise + PADS Pro = quote-only | PADS Pro Premium+DFM free cloud trial; Xcelerator for Startups |
| KiCad 9 | open source (GPL) | $0 | everything, local. Jobsets, ODB++, ngspice45, IPC API for plugins |
| tscircuit | open source (MIT) + registry | $0, pre-monetization (sponsorships/bounties; backers Konvoy, f4) | web + registry + docs free |
| atopile | open source, bootstrapped | $0 compiler, no SaaS page | everything (pre-1.0, PyPI 0.11.x) |
| **ocdcircuit (us)** | **open, local, unmetered** | **$0** | **everything in §6, forever (funnel)** |

Takeaway: every paid tool meters *iteration* (ACUs, minutes, pins,
downloads) or hides price behind sales. Nobody sells unmetered local solve.
That's the gap we own. Flux repriced upward by Aug 2026 (ProtoFlow's
~$20/10-ACU/$2.50 numbers now read stale vs official $60–200 tiers) —
verify live in a browser before quoting anyone.

## 4. Other players — full field map (Sept 2026 shape)

AI maturity runs L1 copilot → L2 assisted execution → L3 autonomous layout
([AtlasPCB landscape](https://www.atlaspcb.com/blog/ai-pcb-design-tools-landscape-2026-copilot-autonomous-layout/#cadence-allegro-x-ai)).
EDA hit $4.2B in Q1 2026, 20th straight growth quarter. Everyone below is
priced or positioned against that spectrum.

| Player | What it is | Price / access | Verdict for us |
|--------|------------|----------------|----------------|
| [Quilter](https://www.quilter.ai/blog/series-b?trk=public_post_comment-text#1) | L3 autonomous place+route, physics/RL, own CAD kernel. $25M Series B Oct 2025 (Index, Benchmark; earlier $10M Feb 2024). Speedrun (vendor-run, no independent replication): NXP i.MX8 Mini, 8L HDI, 843 parts/5,141 pins, 27h runtime, 98% completion, booted Linux, no respins | Quote-only per-project by unrouted pin count; no public $. Free tier trains on your metadata (see §3) | Downstream router, needs a schematic in — complementary. Cloud vs our local/unmetered is the fight |
| [DeepPCB](https://deeppcb.ai/pricing/) (InstaDeep) | L3 RL cloud router. Vendor USB-hub demo (vendor-run, unverified): published as marketing evidence only | Pay-as-you-go: $30/1hr, $280/10hr, $800/30hr; 30-min trial (1 board, ≤4L, 150 airwires, 100 comps) | Same slot as Quilter, burst-friendly. Benchmark against our maze router |
| [ProtoFlow](https://www.protoflow.ai/compare/flux-ai-alternative) | Free desktop AI capture (prompt → part-backed schem → KiCad + ProtoRoute autoroute), LCSC/DigiKey/Mouser, DRC/ERC | Free, no seats/meter/export gate | Closest philosophy; capture + basic route. Validates our model — differentiate on local solvers |
| [CELUS](https://www.celus.io/) | AI platform: specs → schematics + PCB layout + BOM ("~90% faster"), Renesas Winning Combos, distributor integration (AGS). €25M Series A Jul 2022 (Earlybird; ~$27.4M total). Founded 2018 Munich | No public pricing (/pricing 404s); signup + sales | Capture+layout but enterprise-motion. Verify ECAD export list before claiming overlap |
| [Circuit Mind](https://www.circuitmind.io/product) (ACE) | Arch/block-diagram → candidate schematics + BOM + verification, cost/size/power sliders, live availability. Customers incl. BAE, Legrand, NI, LANL case study. London | Demo-gated, zero public numbers | Capture only (no layout claim on product page). Skip |
| [JITX](https://www.jitx.com/plans) | Code-first "software-defined electronics" in Python → AI edits code → schem + routing + HFSS-in-the-loop (56 GHz PCIe Gen7 demo). Local on customer infra. $12M Series A Sep 2022 (Sequoia). Customers: Honeywell, Lockheed, Northrop, OpenAI | Free tier (open-source designs only) + enterprise sales | Church-adjacent. Exports KiCad + Altium (both tiers), Siemens Enterprise-only. Watch |
| atopile | OSS Python HDL, modules/interfaces, KiCad-bound. We already port it (`tools/atopile.py`: bme690, ne555, breath_ketone, e2e_driver4) | Free OSS, bootstrapped, pre-1.0 (PyPI 0.11.x) | Ally, like tscircuit |
| Copperhead | "Cursor for PCBs" — open-source KiCad AI verification/design agent | Early/prototype, claims disputed | Watch — closest to our MCP-in-KiCad play, unproven |
| [EasyEDA Pro](https://prodocs.easyeda.com/en/introduction/std-vs-pro/) (JLCEDA) | Free browser+desktop ECAD. Pro adds hierarchy/design-blocks, stronger DRC, diff-pair/length routing, DXF, panelizing, 3D+enclosure, collab/versions, Altium/KiCad/Eagle import, ODB++/IPC-356A export, API scripting. LCSC live stock/price + one-click JLC order (deepest fab tie of any tool) | Std + Pro both free; pays via fab | The free incumbent. Integration target #2; copy the fab-affiliate model |
| KiCad 9 (Feb 2025) | Free OSS. Jobsets (CLI+GUI output pipelines), ODB++, ngspice 45, pad stacks, multi-track push-shove, creepage DRC, component classes, reusable design blocks, schematic tables | $0 | Home turf for Option B. Plugin route: legacy SWIG `pcbnew` action plugins + **new v9 IPC API (protobuf)** + PCM distribution (commercial fab plugins need a kicad.org contract). AI ecosystem: community MCP servers only, none blessed |
| Altium Designer + 365 | Flagship EDA. AI: cloud DFM (acid traps/slivers/annular ring), generative placement, ML supply-chain predictions, ValiAssistant requirements agent | §3 prices; median $17,955/yr | Skip until a paid pilot demands it |
| Cadence OrCAD X / Allegro X AI | ML predictive routing (12L/2400-net demo 23 min, 97.2%), constraint inference, thermal-aware routing, generative placement. Sigrity/Clarity sim ecosystem | OrCAD X from $2,088/yr; Allegro X from $5,707/yr | Same verdict |
| Siemens Xpedition / PADS | Xpedition Standard (SMB tier) has AI task automation; 2026 push with **Quilter partnership** toward AI layout; Enterprise 2504/2510 + HyperLynx SI/thermal | Standard $2,999/seat/yr; Enterprise/PADS quote-only | Enterprise lane we skip; note Siemens↔Quilter alliance |
| Eagle/Fusion 360 | [EAGLE dead June 7, 2026](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Autodesk-EAGLE-Announcement-Next-steps-and-FAQ.html#1) — sales/support ended | — | Migration pool: ex-Eagle users picking KiCad/EasyEDA now. Target them |

Net: L3 routers (Quilter/DeepPCB) need schematics and charge per compute —
we undercut with local unmetered solve. Capture tools (ProtoFlow/CELUS/
Circuit Mind) stop at schematic — we finish the board. Free tools
(KiCad/EasyEDA/tscircuit/atopile) are allies and distribution, not enemies.

## 5. tscircuit — the ally, not the competitor

What tscircuit is (verified in [export docs](https://docs.tscircuit.com/command-line/tsci-export)):
React/TS circuits-as-code, `tsci` CLI (`init/dev/build/check/simulate/
snapshot/export`), npm-compatible registry (`@tsci/<author>.<pkg>`,
[registry API](https://docs.tscircuit.com/web-apis/the-registry-api)),
`tsci export` → Gerbers, schematic/PCB/assembly SVG, `specctra-dsn`,
`spice`, `kicad_sch`/`kicad_pcb`/`kicad_zip`, `gltf`/`glb`/`step`, plus
[autorouting API](https://github.com/tscircuit/docs/blob/7363f75c3810df0c3a7bf62b906b3ade06103af6/docs/web-apis/autorouting-api.mdx#1)
(per-board `autorouter={{serverUrl, serverMode, inputFormat}}`,
`SimpleRouteJson` in/out, custom in-process routers),
`<analogsimulation>`, KiCad import guides, JLCPCB footprints.
Company: tscircuit Inc (founder Seve Ibáñez; backers Konvoy, f4) —
$0, pre-monetization via sponsorships/bounties.

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
  export is our import and vice versa). Bonus: their `SimpleRouteJson`
  autorouter API is a ready-made contract — our maze router could serve it
  as a local/cloud endpoint later.

## 6. Our stack — why DeepSeek/Claude + ocdcircuit beats Copilot

The winning loop, already working in this repo:
`LLM (any model, your key, your bill) → .ocd text / MCP tools → solve →
DRC → Gerbers`. No seat, no ACU, no cloud.

- **`.ocd` is LLM-native; Flux's canvas is not.** One fact per line,
  git-diffable, byte-round-tripped (goldens enforce). An LLM can read,
  patch, and review a board as text — no screenshots, no clicking.
- **MCP is the Copilot without the rent.** `apps/mcp.py`: 27 tools
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
- **Gaps, stated plainly:** no AC-sweep/Bode *UI* (ngspice `sim ac` exists
  headless), no 340k model library, no live pricing/stock, no ODB++/IPC-2581/
  STEP, no multiplayer, no datasheet→footprint, no panelization, no SI/PI
  beyond skew reports, no interactive push-shove. §8 orders them by revenue
  impact.

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
