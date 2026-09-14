# Parts libraries — research brief (ocdcircuit relevance)

## Disposition (round 200–201)
- (1) Alias table: SHIPPED — `KICAD_ALIASES` + `resolve_fp()` in
  `ocdcircuit/parts.py`, wired into `Board.add_part` (all surfaces).
- (2) Pin-map table: SKIPPED — MNA/ngspice map by net, not pin order;
  no consumer needs it.
- (4) Courtyard audit: DEFERRED to an automated pass (unchanged).
- (5) Live distributor APIs: never by default (unchanged policy).

## Summary

ocdcircuit already covers the import side well (101 stdlib footprints,
custom `.fp`, KiCad/Eagle/tscircuit importers). The landscape research says:
**vendor-or-fetch, your call** (KiCad libs are CC-BY-SA WITH a design-use
exception — using data in a design doesn't trigger share-alike; only
redistributed collections must stay CC-BY-SA with attribution);
**alias, don't rename** (KiCad `R_0603_1608Metric` ↔ ocd
`R0603` via a static dict, reusing the monster bench's mapping); **attrs,
not APIs** for orderable parts (`lcsc=`/`mpn=` validated locally, live lookup
only on explicit user action — with tscircuit's jlcsearch as the scriptable
exception); and the single missing link for simulation is
a **pin-map table** (footprint pin → SPICE node order, copied from tscircuit's
`spicePinMapping`). Ranked cheapest-first: (1) KiCad↔ocd alias table (one
static dict); (2) pin-map attr for the simulators brief; (3) IPC-7351
silkscreen rules (already drafted in tidy brief, now second-sourced);
(4) scripted courtyard audit of the 101 stdlib footprints vs IPC Level N
(defer to an automated pass); (5) live distributor APIs — never by default
(except jlcsearch, no-auth JSON).
Symbols: native `.sym` format + 9 stdlib symbols (`ocdcircuit/symbol.py`),
`sym=` per-part override, footprint→symbol default map; SchRenderer draws
bodies + pin stubs. 3D bodies stay
generated with an optional model-URL attr.

## Background

Current state: `ocdcircuit/parts.py` (101 stdlib footprints, parametric
generators — `chip()`, `sot23()`, `soic()`, `qfp()`, `qfn()`, `bga()` —
courtyard w/h + pads + 3D bodies), `footprint.py` (custom `.fp` format:
`footprint`/`pad`/`hole`/`body`), `foreign.py` (KiCad/Eagle/tscircuit
importers), `part … k=v attrs` (`lcsc=`, `mpn`, `rot=`), BOM exporter.
The monster bench already maps KiCad names → ocd names. Open questions from
prior briefs this closes: IPC courtyard numbers (tidy audit M-tier), KiCad
interop (methods brief), MPN→SPICE registry (simulators brief OQ).

## Key findings (by theme)

### 1. Open footprint + symbol libraries (angle a)

- **KiCad official libs**: footprints at
  [kicad-footprints (GitLab)](https://gitlab.com/kicad/libraries/kicad-footprints),
  symbols at kicad-symbols; license **CC-BY-SA 4.0 confirmed on the repo
  page**, WITH a design-use exception: using library data in a design (and
  generated files) does NOT trigger share-alike; only redistributed
  collections must stay CC-BY-SA with attribution
  ([license](https://www.kicad.org/libraries/license/)) — so vendoring into
  ocdcircuit is allowed with attribution + same license on the collection,
  and fetch-on-demand via git clone is explicitly supported
  ([download](https://www.kicad.org/libraries/download/)). Contribution =
  fork → MR → librarian review per KLC ([contribute](https://www.kicad.org/libraries/contribute/)).
  Exact footprint count UNVERIFIED (repo shows 5,626 commits, not part
  count) — do not quote a number.
- **SnapEDA / UltraLibrarian / SamacSys**: all three are free-downloads-behind-account
  with per-download redistribution restrictions and CAD-plugin (not API)
  integrations. No sanctioned headless/scriptable API found at any of them
  (absence-of-evidence — flagged). Distributor "download ECAD model" buttons
  (DigiKey/Mouser/TME) are backed by these two. Verdict: human-download
  companions, not backends. SamacSys terms VERIFIED free ("no fees, downloads
  are all FREE", 24+ CAD systems, GUI Library Loader app — not a headless
  API; redistribution rights still UNVERIFIED). SnapEDA ToS text BLOCKED
  (truncated fetch, never invented). cdsol/Otto mirrors: targeted search
  found nothing identifiable — no claim.
- **tscircuit registry**: parts as code — npm `@tsci/` packages with props
  (footprint strings like `"0402"`, values)
  ([configuring-chips](https://docs.tscircuit.com/guides/tscircuit-essentials/configuring-chips)),
  plus a scriptable HTTP layer: [Registry API](https://docs.tscircuit.com/web-apis/the-registry-api)
  (search/files/download/releases/builds/autoroute), parametric
  [footprinter strings](https://docs.tscircuit.com/footprints/footprinter-strings)
  (`0402`, `dip16_p1.27_…`, `jlcpcb:C2040` direct JLC refs), and
  [jlcsearch](https://docs.tscircuit.com/web-apis/jlcsearch-api) — no-auth
  JSON search over JLC parts (stock/price/package/lcsc) via `.json`-suffixed
  GETs, directly usable for MPN lookup. Closest to ocd's code-first stance;
  steal the *props-shape* idea (footprint+value+mpn as data) and use
  jlcsearch where scriptable lookup is needed. Ultra Librarian import path
  documented
  ([guide](https://github.com/tscircuit/docs/blob/main/docs/guides/importing-modules-and-chips/importing-from-ultra-librarian.md));
  community BXL decoders exist ([BxlSharp](https://github.com/issus/BxlSharp));
  UL license/API/pricing UNVERIFIED.
- **EasyEDA/LCSC**: JLCPCB assembly keys off LCSC `C...` numbers and ocd
  attrs already carry `lcsc=`. Bridges: [easyeda2kicad](https://github.com/enduity/easyeda2kicad)
  converts any LCSC/EasyEDA component to KiCad libs; EasyEDA Pro API exposes
  part lookup (e.g. `LIB_Device.getByLcscIds`); jlcsearch (above) covers
  scriptable search. Official open/scriptable LCSC search API UNVERIFIED;
  JLC catalog size UNVERIFIED (often cited 200k+ SKUs — do not quote). Lookup
  stays manual-or-jlcsearch; attrs stay ready.

### 2. Footprint + land-pattern standards (angle b)

- **IPC-7351B** ("Generic Requirements for Surface Mount Design and Land
  Pattern Standard", successor to IPC-SM-782): pads computed from component
  geometry + toe/heel/side extensions (Jt/Jh/Js) + RSS tolerance stack.
  Three density levels, safely citable from summaries: **M (Most,
  hand-solder) / N (Nominal, standard reflow) / L (Least, high-density)** —
  toe extension 0.55/0.35/0.15 mm, courtyard excess 0.5/0.25/0.1 mm per side
  (M/N/L), courtyard rounded outward to 0.05 mm grid, same-side courtyard gap
  0.25 mm at N, BGA NSMD pad ≈ 0.75–0.80× ball Ø —
  [AtlasPCB summary](https://www.atlaspcb.com/blog/pcb-land-pattern-design-ipc-7351b-footprint-standards/)
  (fetched in full, lead-verified). IPC naming `FAMILY_PINS_BODY_PITCH_DENSITY`
  (e.g. `SOIC127P600X175-8N`, `QFP50P1200X1200X160-64N`). IPC full text
  paywalled — no bypass attempted; Table 3-2 per-family values beyond the
  above are summary-level only.
- **IPC-7352** existence confirmed (CSK deck) but clause numbers paywalled —
  UNVERIFIED, do not cite specifics.
- **JEDEC vs IPC split**: JEDEC defines package *outlines* only
  (MS-012 = SOIC, MO-220 = QFN — corroborated via Microchip spec citing
  "JEDEC Equivalent: MS-012"); IPC consumes JEDEC max body/lead numbers and
  outputs the land pattern. Rule: never copy lands from JEDEC outlines.
- **KiCad naming**: `R_0603_1608Metric`, `SOT-23`,
  `QFP-48_7x7mm_P0.5mm`, `SOIC-8_3.9x4.9mm_P1.27mm` (KLC F2.1/F2.2/F3.1;
  pages 403 to fetcher, confirmed via search + mirror doc — partially
  verified). ocd short names need an **alias map, not a rename** — reuse the
  bench's KiCad→ocd table.
- **DFM re-check** (second source): courtyard 0.25 mm beyond pads (= IPC
  Level N excess — consistent), silk-to-pad 0.1–0.2 mm (prior 0.15 mm sits
  inside → CONFIRMED compatible), 1.0 mm text = KiCad default convention
  (partially verified).

### 3. Parametric part data + distributor APIs (angle c — full findings)

- **Nexar (Octopart) API**: GraphQL over supply data — search, stock,
  pricing, lifecycle, datasheets, specs, ECAD modules —
  [API](https://nexar.com/api). Free portal signup → app; Evaluation tier
  returns up to 100 matched parts; Standard (2,000) / Pro (15,000) /
  Enterprise paid ([plans](https://nexar.com/compare-plans)). Dollar prices
  unpublished (portal/contact only — flagged). Eval tier suffices for
  prototyping/explicit single-BOM lookups; per-CI automation needs paid.
- **DigiKey API confirmed + specified**: Product Information V4, Quote,
  Ordering, Order Status, MyLists ([portal](https://developer.digikey.com/)).
  OAuth 2.0 mandatory (no permanent tokens); 3-legged flow needs browser
  consent, auth code 1 min, access token 30 min, refresh 90 days
  ([FAQ](https://developer.digikey.com/faq),
  [3-legged](https://developer.digikey.com/tutorials-and-resources/oauth-20-3-legged-flow)).
  Limits: 429 with daily + burst headers (~120/min per FAQ); one MPN per
  search; keyword search cached up to 24 h stale, ProductDetails realtime.
  Headless enrichment is realistic but fiddly (consent bootstrap + refresh
  husbandry) — never default-on.
- **Mouser API**: Search V1/V2 with simple `?apiKey=<uuid>` (no OAuth); key
  via My Mouser (may need approval); up to 10 MPNs pipe-separated per
  PartNumber call, 50 records/keyword, V2 adds manufacturer filter; free
  tier community-reported ~30 calls/min. Claims via secondary skill doc
  ([mouser SKILL](https://raw.githubusercontent.com/aklofas/kicad-happy/main/skills/mouser/SKILL.md))
  — mouser.com fetch failed, flagged UNVERIFIED. Cheapest scripted
  Western-distributor path if confirmed.
- **LCSC/JLCPCB**: assembly matches BOM lines against its Parts Library
  ([FAQs](https://jlcpcb.com/help/article/pcb-assembly-faqs)); library counts
  quoted inconsistently on-page ("40k+ kinds… 698 basic… 300k+ extended" —
  verbatim, flagged); Basic = no loading fee, Extended = $3/line fee.
  Sanctioned scriptable path: [JLCAPI Components API](https://api.jlcpcb.com/)
  (free access application). Undocumented `wmsc.lcsc.com` JSON endpoints in
  community docs are NOT sanctioned — no product on them, no scraping.
  LCSC codes validate locally as `C\d+`.
- **Open-hardware conventions**: Kitspace 1clickBOM (archived) set the TSV
  convention (References/Qty/Description/MPN + per-distributor columns);
  "Complete" filled blanks from CPL + Octopart/Findchips
  ([README](https://raw.githubusercontent.com/kitspace/1clickBOM/master/README.md)).
  CPL = Octopart Common Parts Library (Altium
  [intro](https://resources.altium.com/p/introducing-the-common-parts-library));
  Octopart page blocked 403 — details flagged. OpenBOM not researched.
- Judgment: (i) local `lcsc=` format/allowlist validation (~0 cost, no keys,
  catches typos — do first); (ii) BOM CSV with MPN + LCSC (+ manufacturer)
  as canonical orderable keys, matching JLCPCB sample BOM/CPL + Kitspace TSV
  (do second); (iii) live lookup (JLCAPI → Mouser → Nexar eval → DigiKey)
  only on explicit user command with user keys, cached locally (do
  last/optional). Never default-on.

### 4. Part-model ecosystems beyond footprints (angle d)

- **3D**: KiCad's official
  [kicad-packages3D](https://github.com/KiCad/kicad-packages3D) ships
  STEP+WRL referenced per-footprint — ocd can reference, not bundle.
  UltraLibrarian bundles `.kicad_mod` + `.kicad_sym` + `.stp` per part
  (account-gated). tscircuit's answer is URL-referenced GLB/STEP via
  `<cadmodel>` with generated jscad bodies on CDN as default
  ([cadmodel](https://docs.tscircuit.com/elements/cadmodel)) — direct
  precedent: keep `geom3d` generated boxes/cylinders, add optional model-URL
  attr. CadQuery is the standard Python parametric CAD framework
  ([repo](https://github.com/KRS-Projects/cadquery/)). SnapEDA-3D specifics,
  Kitspace-generator claims UNVERIFIED — flagged.
- **Symbols — verdict: built.** Native `.sym` format (`symbol`/`pin`/`notch`/
  `zigzag`, mirrors `.fp`), 9 stdlib symbols (R/C/L/D/Q3/OPAMP/IC8/14/16),
  `sym=` per-part override with footprint→symbol default map, SchRenderer
  draws bodies + pin stubs + labels. KiCad `.kicad_sym` import deferred (no
  consumer pressure yet); studio canvas reuse comes free via `sch_layout`.
- **SPICE pin-mapping: minimal path shipped, table deferred** (review round 1):
  `sim op REF MODEL PINS...` carries positional pins per part today; the
  `spicepin=` attr name is reserved in OCD.md but unconsumed — wire it or
  drop the doc line. tscircuit's `spicePinMapping` remains the model for a
  full table. TI (TLV9052/OPA4383 pages)
  and ADI/LTspice model hosting confirmed this round; Nexperia/onsemi URLs
  NOT re-verified — flagged. **No open MPN→SPICE-URL registry found**
  (JitPCB open-components-database adjacent, SPICE coverage unverified).
- **Co-packages**: UltraLibrarian/SamacSys ship symbol+footprint+3D per part
  (SPICE inside UNVERIFIED); closest true co-package is tscircuit `<chip>`
  (footprint+symbol+cadModel+spiceModel, versioned). Fit for ocd: **`.fp` +
  model-URL attrs + pin-map table = one versioned include unit** — no new
  packaging format.

## Open questions

1. KLC clause-level numbers (F5.1/F5.3 silk/courtyard) — 403-blocked; confirm
   before hard-coding anything beyond the AtlasPCB summary.
2. SnapEDA/SamacSys exact redistribution terms — needed only if ocd ever
   redistributes converted vendor data (recommended: never).
3. Mouser/Octopart key + rate-limit specifics — needed only for live lookup.
4. Open MPN→SPICE registry — none found; build-or-crowdsource only if the
   simulators brief's `.SUBCKT` path demands it.
5. 101-footprint courtyard audit vs IPC-N — scripted pass, deferred.

## Equivalents / replacements / pin-compatible alternatives

Rule: **alternates are curated strings, never inferred** — the tool carries
engineer verdicts; no distributor offers a scriptable "is X a safe substitute
for Y" boolean, so ocdcircuit models verdicts instead of computing them.

- **Second-source vs drop-in**: industry "second source" = independently
  manufactured, form-fit-function compatible (same footprint + pinout +
  parametric superset in your corner) — Toshiba frames it as supply-resilience
  strategy ([article](https://toshiba.semicon-storage.com/eu/semiconductor/design-development/innovationcentre/articles/tcm0706_2ndSourceMCDs.html),
  verified). Altium codifies two tiers, worth copying as vocabulary:
  **Part Choice** = identical specs, procured differently (supplier/packaging,
  ranked Primary/Secondary); **Alternate Part** = specs vary (voltage,
  tolerance) but functionally equivalent, workspace-managed only
  ([KB](https://www.altium.com/ru/documentation/knowledge-base/altium-designer/add-a-substitute-component-in-activebom),
  verified). Classic cases: LM358→LM358A (tighter offset, safe superset),
  NE555 across TI/ST/ON (same DIP-8/SO-8 pinout, differing Iq/speed grades).
- **Distributor reality**: JLCPCB never auto-substitutes — unmatched/shortfall
  parts get a human magnifier-icon "pick an in-stock replacement" step or go
  unpopulated ([guidelines](https://jlcpcb.com/help/article/component-matching-guidelines-for-pcba-orders),
  verified). So ocd alternates directly reduce matching friction. TI runs a
  parametric cross-reference page (JS-gated, scoring unverified); Octopart
  "similar parts" guide is captcha-blocked (title-only, do not cite contents);
  SnapEDA shortage-era alternates UNVERIFIED (429). Scriptable paths remain
  jlcsearch + Nexar API only.
- **EDA precedent**: KiCad database libraries map one internal part number →
  symbol + footprint + N MPNs (multi-manufacturer rows behind one internal PN
  — the alternates mechanism; KiCad has footprint-override only, no true
  alternates model). tscircuit/SKiDl: no alternates concept found
  (absence-of-evidence — flagged); ocd would be novel here, so keep it trivial.
  Kitspace 1clickBOM archived; JitPCB schema unverified — don't design against
  either.
- **Data model for ocd (ranked, judgment)**: (i) `alternates` attr on the
  part — `part U1 SOIC8 NE555 mpn=NE555P alternates=LM555CN,TLC555CP` —
  PROPOSED (BOM exporter does not yet emit the column; OCD.md documents the
  attr); ERC/placer/sim ignore (same footprint+value by construction); footprint equality checked at load,
  **pinout compatibility stays a human attestation**. Covers ~90% of real
  need (stock-outs) in ~10 lines. (ii) Passive equivalence needs no syntax:
  BOM already groups by (value, footprint) — "any 10k 0603" is procurement
  tolerance notes, not a feature. (iii) Separate equivalence table only when
  alternates need rank/notes metadata. (iv) Live stock-ranking via
  jlcsearch/Nexar only on explicit command, never auto-substitute (JLCPCB
  precedent: designer decides).

## Verification notes

- Lead-verified by direct fetch: KiCad footprints repo (CC-BY-SA 4.0 on
  page); AtlasPCB IPC-7351B summary (density levels, courtyard, naming);
  DigiKey portal product list; ngspice-adjacent model pages NOT re-fetched
  (angle-d URLs taken as reported, flagged where noted).
- Blocked, never inferred: Octopart (captcha 403); Mouser API page (fetch
  TypeError); KLC sub-pages (403 bot-check); IPC full text (paywall);
  IPC-7352 clauses; hneemann-adjacent pages (n/a here).
- Angle-c subagent report was not received; §3 above is lead-synthesized from
  direct fetches and is thinner than the other sections — treat its API
  specifics as provisional.
- HF/AlphaXiv unset (verified at session start) — no gated calls attempted.
- Local inventory re-verified: 101 stdlib footprints (`parts.py`), `.fp`
  format (`footprint.py`), KiCad/Eagle/tscircuit importers (`foreign.py`).

## References

- KiCad footprints — https://gitlab.com/kicad/libraries/kicad-footprints
- KLC — https://klc.kicad.org/ · F3.1 — https://klc.kicad.org/footprint/f3/f3.1/
- SnapEDA — https://www.snapeda.com/
- UltraLibrarian — https://www.ultralibrarian.com/
- SamacSys + terms — https://www.samacsys.com/ · https://www.samacsys.com/epw-terms/
- tscircuit chips/cadmodel/spicemodel — https://docs.tscircuit.com/guides/tscircuit-essentials/configuring-chips · https://docs.tscircuit.com/elements/cadmodel · https://docs.tscircuit.com/elements/spicemodel
- IPC-7351B summary — https://www.atlaspcb.com/blog/pcb-land-pattern-design-ipc-7351b-footprint-standards/
- PCBSync IPC-7351 — https://pcbsync.com/ipc-7351-land-pattern/
- DigiKey portal — https://developer.digikey.com/node?page=16
- Mouser API — https://www.mouser.de/api-search/
- CPL intro — https://resources.altium.com/p/introducing-the-common-parts-library
- KiCad packages3D — https://github.com/KiCad/kicad-packages3D
- CadQuery — https://github.com/KRS-Projects/cadquery/
- TI TLV9052/OPA4383 — https://www.ti.com/product/TLV9052 · https://www.ti.com/product/zh-tw/OPA4383
- ADI LTspice — https://www.analog.com/cn/lp/002/tools/ltspice-simulator-tw.html
- JitPCB open-components-database — https://github.com/d-haldane/open-components-database
# Parts library matrix

Legend: ✅ yes · ⚠️ partial/unverified · ❌ no. Details in `parts-libraries.md`.

| Source | Footprints | Symbols | SPICE | 3D open | Textures | Formats | License-open | Headless API | LCSC/MPN | Pin-map |
|---|---|---|---|---|---|---|---|---|---|---|
| ocdcircuit stdlib (101) | ✅ | ❌ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ❌ |
| KiCad official | ✅ | ✅ | ⚠️ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ | ⚠️ |
| SnapEDA | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ⚠️ | ❌ |
| UltraLibrarian | ✅ | ✅ | ⚠️ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| SamacSys | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ⚠️ | ❌ | ❌ | ❌ |
| tscircuit registry | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ |
| EasyEDA / LCSC / JLCPCB | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ⚠️ | ✅ | ❌ |

Key: ocd-3D = generated boxes/cyl + procedural PBR; SPICE ⚠️ = stdlib MNA + `.cir` export, no per-part `.SUBCKT` yet; KiCad-SPICE ⚠️ = fields/alt-node-seq only; UL-SPICE ⚠️ = unverified bundles; SamacSys license ⚠️ = free download, redistribution unverified; tscircuit-3D/textures ⚠️ = URL-dependent; ocd-LCSC ⚠️ = attrs ready, lookup manual; SnapEDA-LCSC ⚠️ = MPN links only; EasyEDA-headless ⚠️ = Pro API + jlcsearch, no sanctioned open search.
