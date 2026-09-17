# Contributing

Python **3.14** (see `.python-version`). Core board code is stdlib-only;
the full gate (`make check`) also needs the optional extras because
golden snapshots were taken with the numpy placer path.

```bash
make setup                                      # .venv + mypy/ruff + .[optional]
make doctor                                     # names missing tools / env knobs
make check                                      # what CI runs (lint + all tests)
```

`make` with no args prints the command list (`make help`). After
`make setup`, `make check` / `make lint` use `.venv/bin` automatically
(no `source .venv/bin/activate` required). Bare `pip install` on
Arch/Debian/Ubuntu fails with PEP 668 — that is why setup creates a venv.

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

CI (`.github/workflows/check.yml`) runs on **Ubuntu 24.04** only and relies on:

- runner Google Chrome / Chromium — studio screenshot + console-clean gate
- `poppler-utils` (`pdftotext`) — installed in CI for kb/ datasheet PDF search
- `requirements-dev.txt` + `.[optional]` — same stack as `make setup`

The Python package itself uses `os.path` / tempfile (not hardcoded `/`) so
the same tree is expected to run wherever Python 3.14 does; contributor
`make` targets assume a Unix make + shell. Without a browser or
`pdftotext`, those halves skip or degrade; `make doctor` lists the gap.
`make check` still passes after `make setup`.

## PR checklist

1. `make check` green
2. If you changed intended geometry: `make snap` and commit `tests/golden.json`
3. No new unexplained `ruff` ignore — drop ignores only when the tree is clean
