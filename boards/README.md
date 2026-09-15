# boards/

Real boards, not samples. Each subdir is one board: the `.ocd` source, its
`fp/` footprints, and `out/` build artifacts (gitignored — regenerate with
`python -m apps.ocd run <board>.ocd`).

- `blinky_555.ocd`, `psu.ocd`, `usb_breakout.ocd` — small 2L demos
- `pico_tmc2209/` — Pico + 3×TMC2209 (block/instance demo)
- `mitox/` — 43-part 4L port with harvested LCSC footprints
- `bme690/` — atopile-ported BME690 carrier
- `ne555/` — discrete 555 (atopile port)
- `e2e_driver4/` — E2E sensor driver (atopile port)
- `breath_ketone/` — dense breath sensor (farm-excepted: 81% fill, 12 overlaps)

## kb/ — the board's own documents

Documentation lives beside the board file, in `kb/`, because that is where a
human drops a datasheet anyway:

```
boards/mitox/kb/NOTES.md          notes/errata/pinouts (.md/.txt/.csv/…)
boards/mitox/kb/datasheets/       one datasheet per part
boards/mitox/kb/sources.tsv       name<TAB>url — where a file came from
boards/mitox/kb/.cache/           pdftotext output (derived, gitignored)
```

Three ways in — drop files into `kb/` with the file manager, `ocd kb add
<board.ocd|dir> <path|url>`, or `ocd kb fetch <board.ocd>` which downloads
datasheets for every `datasheet=<url>` / `lcsc=C1234` part attr.

Read it back with `ocd kb list|search|read`, or let an agent traverse it over
MCP (`kb` tool: list/search/read/add/fetch/index/ask) — search returns
`doc:line` hits and `list` maps each doc back to the refs it covers, so "what
does U3's datasheet say about VIN" is a lookup, not a guess.

For questions rather than terms, `ocd kb ask <board.ocd|dir> "<question>"`
recalls the passages that answer it — embeddings (`ocd kb index`, cached in
`kb/.cache/vec__*.json`, model from `OCD_LLM_EMBED`, default
`nomic-embed-text` on the `OCD_LLM_BASE` endpoint) with automatic fallback to
term matching when no model is reachable, so it answers on a bare machine too.
Add `--answer` (CLI) or `answer: true` (MCP) to have the local model write the
answer from those passages only. Studio has the same panel: ask/search,
`add` a URL or path, `fetch datasheets`, click a doc to read it.

One rule, no exceptions: `kb/` sits next to the `.ocd`. So `boards/mitox/`
keeps its own `kb/`, while the loose demos (`boards/blinky_555.ocd`,
`psu.ocd`, …) share `boards/kb/`.
