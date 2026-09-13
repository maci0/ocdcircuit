# Audit: the research here (`docs/constraint-solvers.md`, `constraint-methods.md`, `nn-ga.md`, `tidy-metrics.md`)

## Workflow adaptation (read first)

No arXiv ID or repo URL was given, so "the research here" was interpreted as
the four research briefs produced in this workspace this session. Mapping to
the template: **paper → each brief; implementation → (a) the ocdcircuit
codebase** (`ocdcircuit/`, `tests/`, `examples/`, exact file:line) for code
claims, **(b) the cited source URL** (re-fetched) for literature claims;
benchmark numbers → re-run. Method: 4-way subagent fan-out (one per brief,
Pass 1 claim extraction + Pass 2 verification) plus lead spot-checks
(docs/outputs diff, code constants, env keys, OpenAlex records).

Credentials: `HF_TOKEN` and `ALPHAXIV_API_KEY` both **unset** in env (verified
via shell) — correctly treated as blocked throughout; all paper metadata came
from arXiv abs pages + OpenAlex API (sanctioned fallback). No paywalls
bypassed. Docs/outputs copies verified **byte-identical** (`diff -q`, all four
pairs) — findings apply to both.

## Match Summary

~153 distinct claims extracted. **80 confirmed (52%), 20 mismatched (13%),
46 blocked-but-pre-disclosed (30%), 7 judgment estimates (5%).**
Excluding items the briefs themselves flagged as unverified: **80/100
checkable claims match (80%)**. The briefs' self-flags are ~90% accurate
(independently re-verified) — the audit's real findings are (a) three
code-coverage overstatements, (b) scorecard cost-tier errors, (c) a benchmark
table missing its methodology.

| Brief | Claims | Confirmed | Mismatched | Blocked (disclosed) |
|---|---|---|---|---|
| constraint-solvers | ~25 | 15 (60%) | 3 (12%) | ~7 (28%) |
| constraint-methods | ~46 | 15 (33%) | 3 (7%) | ~21 + 7 judgment |
| nn-ga | ~37 | 25 (68%) | 6 (16%) | ~6 (16%) |
| tidy-metrics | ~45 | 25 (56%) | 8 (18%) | ~12 (27%) |

## Confirmed Claims (headlines; full evidence in auditor reports)

- **Solver description**: Langevin dynamics (springs `solver.py:163-170`,
  repulsion `183-208`, noise `215-216`, schedule `157-159`), multi-seed
  best-of (`245-253`), greedy bbox layers (`271-305`), L-router hub
  (`308-344`), maze GRID=0.25/BEND=1.5/VIA=8.0/SOFT=15 (`maze.py:19-21,57`),
  own-net reuse, cost terms 1e6/1e5 (`solver.py:55-77`). All match.
- **Literature core re-fetched**: CMU CSP notes (complexities verbatim);
  CP-SAT docs (SAT+CP, integers-only, 5 statuses); HiGHS (LP/MIP/QP, MIT);
  OpenROAD gpl (density 0.7, RUDY); Song & Ermon abstract wording; Quilter
  "thousands of candidate boards"; KiCad AR_AUTOPLACER fields; Freerouting
  router-only (by absence); tscircuit presets; **Purchase 1997 abstract
  verbatim + OpenAlex 493 cites (W1521554751, re-fetched 2026-09-13)**;
  B\*-tree 2000/548 cites (W2100740271); FLUTE TCAD/354 cites (W2125831674);
  Dunnart 2009/51 cites; NSGA-II record; libvpsc QP definition; kiwisolver
  "10x–500x" verbatim; Boyd ADMM 2011 monograph page; MiniZinc `diffn` docs;
  Z3-reachable items; JLCPCB copper (40–60%, Δ15–20%, warpage 0.75%);
  AtlasPCB silk (1.0 mm/0.15 mm); IPC-2221B 0.10 mm floor (two independent
  summaries); diff-pair skew table values; cola constraints; Dong & Nakatake
  abstract; Altium DFA orientation; Altium 45° ES-mirror (17 ps test, Johnson
  link, ≥10 GHz exceptions); MOnSter quote verbatim; 305×381 mm arithmetic.
- **Benchmarks**: place-time absolutes within ~25% on re-run; **scaling ratios
  match (300→1000: 10.0× vs 9.9× — quadratic confirmed)**; all Monster
  extrapolation arithmetic checks (5 min, 2.5 h, 3.7 M states, 34 s, ~30×);
  WireMask-BBO paper claims confirmed (with note on its internal 5-vs-6/7
  inconsistency); ChiPBench findings confirmed (20 designs, 6 placers,
  end-to-end PPA loss).
- **Correctly-absent proposals**: VPSC, min-conflicts, LNS, skyline, Steiner,
  Cassowary, Sugiyama, symmetry — proposed in briefs, absent in code
  (grep-clean). Consistent.

## Mismatches (brief location vs actual; fix recommended)

1. **`keepout` listed as a current constraint** — solvers Background + §1/§5,
   methods §1/§2. `grep keepout ocdcircuit/*.py tests/` → **zero hits**; no
   `.ocd` syntax (`agent.py:93-146`), no handler. Only RFC-0001/ADR-0002/briefs
   mention it. Only real keepout-like behavior: maze part-courtyard blockage
   (`maze.py:35`). **Fix: remove from "current" lists or mark aspirational.**
2. **Edge described as "one-sided penalty"** (solvers §5). Code is penalty
   (`solver.py:64-71`) + repulsive push (`209-213`) + hard clamp (`219-223`).
   Understated. **Fix wording.**
3. **Rip-up & reroute framed as future work** (solvers §4, "~20 lines").
   `maze.py:142` small-nets-first ordering + `150-184` one bounded rip-up
   retry + `237-248` jumper fallback **already exist**. "1–2 passes" overstates
   (one round), "~20 lines" understates existing (~35 + helpers).
   **Fix: reframe upgrade as 2nd pass / negotiated-congestion.**
4. **Tidy Summary "6 computable" vs table's seven ✅** (T1–T6 + T10). Count
   contradicts own table. **Fix count.**
5. **T10 orientation ✅→🏗️**: no rotation state exists (`Part`=x,y,w,h only).
   Metric vacuously 1.0. **Must-fix before any implementation (drop or add
   rotation field).**
6. **T5 ✅→🔧**: DRC stores no continuous clearances (`check()` boolean-only,
   breaks after first hit `drc.py:99-100`); no per-net current state.
   **Retier.**
7. **T6 ✅→split**: ΔL derivable via `_net_length` (~0–10 lines); gap-σ is new
   code (`_diff_cost` only checks pad-pair distance `solver.py:114-122`).
   **Retier; move "F-table" flag next to the row.**
8. **T1/T2 ✅→🔧** (~10–15 lines new helpers); **T4 ✅→🔧** (via-count only for
   maze routes via monkey-patched `v.via` `maze.py:280`; L-router never emits
   vias). **Retier with router-dependence note.**
9. **T13 🔧→🏗️/N-A**: SchRenderer is one-column-per-net parallel lines
   (`plugins.py:341-362`) — crossings/jogs trivially 0. Needs a real
   schematic placer first. **Retier.**
10. **nn-ga dense-overlap column irreproducible**: no density × seed-7
    combination yields 7 @300 / 396 @1000; 300's "7" exactly equals the
    sparse-chain value — likely mislabeled. DRC absolutes 6–12× lower in
    re-run (board-size dependent). **Fix: republish table with methodology
    footnote or mark machine-specific.**
11. **Tidy refs inconsistency**: §2 cites `/es/` Altium URL, References lists
    `.com.cn` URL for the same article. **Unify.**
12. **Stale flag**: "Johnson original, unfetched" — now fetched HTTP 200 and
    corroborates. **Update flag to confirmed.**

## Missing Implementations (claimed-as-current or needed-for-repro)

- `keepout` constraint: no syntax, no handler, no tests (see M1).
- Greedy legalization / min-penetration push: absent (correctly described as
  upgrade; `solver.py` ponytail notes admit O(n²)/L-only).
- Negotiated-congestion history (`bn+hn`): absent (single rip-up round only).
- Hadlock, length meanders: absent.
- Benchmark methodology for nn-ga table: board sizes, seed, net construction
  per column, router used for "DRC (routed)", fab profile, hardware — none
  stated; table not reproducible from the brief alone.
- Tidy thresholds uncalibrated: ε=0.1 mm, headroom ≥1.2 (underived), weights
  0.35/0.25/0.25/0.15, T14 veto — flagged (J) but read as plug-and-play;
  IPC 0.10 mm is voltage-banded (0.6 mm at 31–150 V), not a bare floor;
  copper Δ≤20% silently takes the loose end of the 15–20% band.
- Version pins: MiniZinc (2.8.5 docs only), OR-Tools, Z3, kiwisolver versions;
  no install/verify commands; cite counts lack date snapshot (493 verified
  2026-09-13, will drift).

## Reproducibility Risks

1. **Machine-dependent timings, zero hardware info** (nn-ga table) — only
   scaling ratios are checkable (and they verify); absolutes will drift.
2. **Unverifiable-by-construction estimates**: all "~N lines" adoption costs
   (~20/~50/15/25/30–40/80–150/500+/10) — no measurement method, pure
   judgment placed adjacent to fetched facts.
3. **Monster extrapolations** assume pure-quadratic to n=4000 (ignore
   memory/cache effects) and DRC-34 s assumes traces ∝ n (trace-pair check is
   O(t²), t router-dependent).
4. **Blocked sources** (IEEE/ACM paywalled primaries, rate-limited arXiv IDs,
   JS-shell pages, 403s, MiniSAT DOI Sage-redirect): correctly marked blocked,
   never inferred — but ~30% of claims rest on metadata-only support.
   Highest-value fetches if exact numbers are ever quoted: TimberWolf,
   Pathfinder DOI, ForceAtlas2/FR DOIs, DREAMPlace PDF, Walshaw/Harel–Koren
   PDFs, MaskPlace/ChiPFormer primaries.
5. **Marketing-as-evidence**: Quilter "thousands of candidates" (verified as
   *stated*, not as benchmark); fab-blog skew/copper numbers are practitioner
   grey-lit, not interface specs (brief flags this — keep flags attached).
6. **Vacuity traps**: T10/T13 naive implementations return constant 1.0/0 and
   look "neat" (see M5/M9).
7. **Search-result claims** ("OpenAlex false positives confirm thinness") are
   non-reproducible without query string/date/dump.

## Verdict

The research is **honest but tier-sloppy**: literature core verified,
self-flags ~90% accurate, zero invented sources — but code-coverage claims
overstate `keepout`/R&R status, the tidy scorecard tiers don't survive contact
with the codebase (T10/T5/T6/T13), and the benchmark table can't be
reproduced from the brief alone. Recommended: apply the 12 mismatch fixes
above (all doc-only, ~30 lines total), then treat the briefs as verified.
