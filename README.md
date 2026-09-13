# ocdcircuit

Circuits as code, agents as first-class users, every edit reversible.
Built on [spatiotemporal composability](https://github.com/cordiverse/paper).

One source of truth per board: the `.ocd` file (commit it; build outputs
are gitignored). Full syntax: `docs/OCD.md`.

```bash
python ocd.py examples/blinky_555.ocd  # .ocd → DRC → JLC files in examples/out/
python tests/test_all.py               # one self-check for everything
```

Humans write `.ocd` (one fact per line). Python (`Board` API) and JSON
(`agent.from_json`) build the same model; agents drive it via patch ops.
Everything else (placer/router/drc/exporter/parts/renderer) is a
hot-swappable plugin.

Layout: `ocdcircuit/` (core, circuit, parts, solver, drc, export, agent),
`docs/` (PRD, ADRs, RFC, landscape, OCD), `examples/`, `tests/`.
