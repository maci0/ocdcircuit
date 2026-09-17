BOARD ?= boards/blinky_555.ocd
VENV ?= .venv

# Prefer project-local venv binaries so `make check` works after `make setup`
# without a prior `source .venv/bin/activate`. Fall back to PATH for CI and
# machines that already have the gate tools installed.
ifeq ($(wildcard $(VENV)/bin/python),)
  PYTHON := python
  MYPY := mypy
  RUFF := ruff
else
  PYTHON := $(VENV)/bin/python
  MYPY := $(VENV)/bin/mypy
  RUFF := $(VENV)/bin/ruff
endif

# SOURCE_DATE_EPOCH stabilizes fab.zip mtimes for every target.
# LC_ALL/TZ only on hermetic gate targets — `make run` must keep the host
# zone so interactive tooling is not forced into UTC.
export SOURCE_DATE_EPOCH ?= 0
HERMETIC := LC_ALL=C TZ=UTC

.PHONY: help setup check run lint doctor test snap bench farm fabsweep sbom clean

help:				# list contributor commands (default)
	@printf '%s\n' \
	  'Contributor commands (see CONTRIBUTING.md):' \
	  '  make setup      create .venv + install gate tools and optionals' \
	  '  make doctor     tooling self-check (Python, optionals, plugins)' \
	  '  make lint       mypy + ruff + ocd lint on $$(BOARD)' \
	  '  make test       all test scripts (what CI runs after lint)' \
	  '  make check      lint + test — full gate before push' \
	  '  make run        studio webui on :8077 (BOARD=$(BOARD))' \
	  '  make snap       re-pin golden snapshots after intentional change' \
	  '  make bench      5420-part stress (~5 min, not in check)' \
	  '  make farm       load+solve every board (skips breath_ketone)' \
	  '  make fabsweep   every board × every fab profile' \
	  '  make sbom       CycloneDX inventory from pinned manifests' \
	  '  make clean      wipe out/ caches and report leftovers' \
	  '' \
	  'Single suite (edit-test loop):' \
	  '  $(PYTHON) tests/test_paper.py      # core paper (~0.1s)' \
	  '  $(PYTHON) tests/test_sdk.py        # SDK integration' \
	  '  $(PYTHON) tests/test_all.py        # unit + MCP (~1–2 min)' \
	  '  $(PYTHON) tests/test_exact_overlap.py  # exact-pilot timeout/feasibility' \
	  '  $(PYTHON) tests/test_snapshot.py   # golden geometry' \
	  '  $(PYTHON) tests/test_studio.py     # studio smoke (+ chromium if present)' \
	  '' \
	  'Setup: make setup   # PEP 668-safe; then make check uses $(VENV)/bin' \
	  'CI: runner Chrome + apt poppler-utils (pdftotext).'

setup:				# project-local venv + gate tools + optionals
	@test -x $(VENV)/bin/python || python -m venv $(VENV)
	$(VENV)/bin/python -m pip install -r requirements-dev.txt
	# numpy (and friends) change placer basins — goldens expect the vector path.
	# editable so edits under ocdcircuit/ are what `make test` imports.
	$(VENV)/bin/python -m pip install -e '.[optional]'
	@printf 'Dev env ready at %s — run `make check` (no activate needed).\n' '$(VENV)'

check: lint test			# everything green before commit
lint:				# types + source lint (no place/route)
	$(HERMETIC) $(MYPY)
	$(HERMETIC) $(RUFF) check
	$(HERMETIC) $(PYTHON) -m apps.ocd lint $(BOARD)
doctor:				# tooling self-check
	$(HERMETIC) $(PYTHON) -m apps.ocd doctor
test:				# unit suite + golden snapshots + studio smoke gate
	$(HERMETIC) $(PYTHON) tests/test_cli.py
	$(HERMETIC) $(PYTHON) tests/test_sdk.py
	$(HERMETIC) $(PYTHON) tests/test_all.py
	$(HERMETIC) $(PYTHON) tests/test_exact_overlap.py
	$(HERMETIC) $(PYTHON) tests/test_snapshot.py
	$(HERMETIC) $(PYTHON) tests/test_paper.py
	$(HERMETIC) $(PYTHON) tests/test_studio.py
run:				# webui → http://localhost:8077
	$(PYTHON) -m apps.studio $(BOARD)
snap:				# re-pin goldens after intended geometry change
	$(HERMETIC) env SNAP=1 $(PYTHON) tests/test_snapshot.py
bench:				# 5420-part stress (~5 min, not in check)
	$(HERMETIC) $(PYTHON) -m benches.discrete6502.bench 1 5
farm:				# every board loads+solves (breath_ketone density excepted)
	$(HERMETIC) $(PYTHON) -c "import sys, glob, os; sys.path.insert(0, '.'); \
	from ocdcircuit import agent; \
	[(_b := agent.loads(open(f, encoding='utf-8').read(), base=os.path.dirname(f)), \
	_b.place(seeds=2, iters=100), _b.route_board(), \
	print(f, len(_b.check()['errors']), 'errors')) \
	for f in sorted(glob.glob(os.path.join('boards', '*.ocd')) \
	              + glob.glob(os.path.join('boards', '*', '*.ocd'))) \
	if 'out' not in f.split(os.sep) and 'lib' not in f.split(os.sep) \
	and 'breath_ketone' not in f.split(os.sep)]"
fabsweep:			# every board x every fab (profile discrimination check)
	$(HERMETIC) $(PYTHON) -c "import sys, glob, os; sys.path.insert(0, '.'); \
	from ocdcircuit import agent, fab; \
	[(_b := agent.loads(open(f, encoding='utf-8').read(), base=os.path.dirname(f)), \
	_b.place(seeds=2, iters=100), _b.route_board(), \
	print(os.path.basename(f), ' '.join(f'{g}={len(_b.check(\"fab\", fab=g)[\"errors\"])}' \
	for g in fab.list_fabs()))) \
	for f in sorted(glob.glob(os.path.join('boards', '*.ocd')) \
	              + glob.glob(os.path.join('boards', '*', '*.ocd'))) \
	if 'out' not in f.split(os.sep) and 'lib' not in f.split(os.sep)]"
sbom:				# CycloneDX JSON from pinned manifests (stdout)
	@$(HERMETIC) $(PYTHON) -m tools.sbom
clean:
	rm -rf boards/out boards/*/out *-erc.rpt *-drc.rpt __pycache__ apps/__pycache__ */__pycache__ .mypy_cache
