# Contributing

Python **3.14** (see `.python-version`). No package install beyond the
dev checkers is required for the core path.

```bash
python -m pip install -r requirements-dev.txt   # pins mypy + ruff for `make check`
python -m pip install '.[optional]'             # optional: numpy, rich, pillow
make doctor                                     # names missing optionals
make check                                      # what CI runs (lint + all tests)
```

`make` with no args prints the command list (`make help`).

## Edit-test loop

Run the suite you touched, not the whole gate:

| change area | command |
|---|---|
| core / paper / context | `python tests/test_paper.py` |
| library / CLI / MCP | `python tests/test_all.py` |
| placement / routing geometry | `python tests/test_snapshot.py` (`SNAP=1` to re-pin) |
| studio / HTTP / UI | `python tests/test_studio.py` |

Full gate before push: `make check` (~2 min here; CI already has Chrome
and installs poppler so the studio browser half and PDF kb path run).

Process env knobs (`OCD_PORT`, `OCD_LLM_*`, `JLCPCB_*`, …) are listed in
`.env.example` and shown redacted by `make doctor` / `ocd doctor`.

## CI parity (optional locally)

CI (`.github/workflows/check.yml`) relies on:

- runner Google Chrome / Chromium — studio screenshot + console-clean gate
- `poppler-utils` (`pdftotext`) — installed in CI for kb/ datasheet PDF search

Without a browser or `pdftotext`, those halves skip or degrade; `make doctor`
lists the gap. `make check` still passes on a correct Python +
`requirements-dev.txt` setup.

## PR checklist

1. `make check` green
2. If you changed intended geometry: `make snap` and commit `tests/golden.json`
3. No new unexplained `ruff` ignore — drop ignores only when the tree is clean
