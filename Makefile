BOARD ?= boards/blinky_555.ocd

# SOURCE_DATE_EPOCH stabilizes fab.zip mtimes for every target.
# LC_ALL/TZ only on hermetic gate targets — `make run` must keep the host
# zone so interactive tooling is not forced into UTC.
export SOURCE_DATE_EPOCH ?= 0
HERMETIC := LC_ALL=C TZ=UTC

.PHONY: help check run lint doctor test snap bench farm fabsweep clean

help:				# list contributor commands (default)
	@printf '%s\n' \
	  'Contributor commands (see CONTRIBUTING.md):' \
	  '  make doctor     tooling self-check (Python, optionals, plugins)' \
	  '  make lint       mypy + ruff + ocd lint on $$(BOARD)' \
	  '  make test       all four test scripts (what CI runs after lint)' \
	  '  make check      lint + test — full gate before push' \
	  '  make run        studio webui on :8077 (BOARD=$(BOARD))' \
	  '  make snap       re-pin golden snapshots after intentional change' \
	  '  make bench      5420-part stress (~5 min, not in check)' \
	  '  make farm       load+solve every board' \
	  '  make fabsweep   every board × every fab profile' \
	  '  make clean      wipe out/ caches and report leftovers' \
	  '' \
	  'Single suite (edit-test loop):' \
	  '  python tests/test_paper.py      # core paper (~0.1s)' \
	  '  python tests/test_all.py        # unit + MCP (~1–2 min)' \
	  '  python tests/test_snapshot.py   # golden geometry' \
	  '  python tests/test_studio.py     # studio smoke (+ chromium if present)' \
	  '' \
	  'Setup: python -m pip install -r requirements-dev.txt' \
	  'CI: runner Chrome + apt poppler-utils (pdftotext).'

check: lint test			# everything green before commit
lint:				# types + source lint (no place/route)
	$(HERMETIC) mypy
	$(HERMETIC) ruff check
	$(HERMETIC) python -m apps.ocd lint $(BOARD)
doctor:				# tooling self-check
	$(HERMETIC) python -m apps.ocd doctor
test:				# unit suite + golden snapshots + studio smoke gate
	$(HERMETIC) python tests/test_all.py
	$(HERMETIC) python tests/test_snapshot.py
	$(HERMETIC) python tests/test_paper.py
	$(HERMETIC) python tests/test_studio.py
run:				# webui → http://localhost:8077
	python -m apps.studio $(BOARD)
snap:				# re-pin goldens after intended geometry change
	$(HERMETIC) env SNAP=1 python tests/test_snapshot.py
bench:				# 5420-part stress (~5 min, not in check)
	$(HERMETIC) python -m benches.discrete6502.bench 1 5
farm:				# every board loads+solves (breath-ketone density excepted)
	$(HERMETIC) python -c "import sys, glob, os; sys.path.insert(0, '.'); \
	from ocdcircuit import agent; \
	[(_b := agent.loads(open(f, encoding="utf-8").read(), base=os.path.dirname(f)), \
	_b.place(seeds=2, iters=100), _b.route_board(), \
	print(f, len(_b.check()['errors']), 'errors')) \
	for f in sorted(glob.glob(os.path.join('boards', '*.ocd')) \
	              + glob.glob(os.path.join('boards', '*', '*.ocd'))) \
	if 'out' not in f.split(os.sep) and 'lib' not in f.split(os.sep)]"
fabsweep:			# every board x every fab (profile discrimination check)
	$(HERMETIC) python -c "import sys, glob, os; sys.path.insert(0, '.'); \
	from ocdcircuit import agent, fab; \
	[(_b := agent.loads(open(f, encoding="utf-8").read(), base=os.path.dirname(f)), \
	_b.place(seeds=2, iters=100), _b.route_board(), \
	print(os.path.basename(f), ' '.join(f'{g}={len(_b.check(\"fab\", fab=g)[\"errors\"])}' \
	for g in fab.list_fabs()))) \
	for f in sorted(glob.glob(os.path.join('boards', '*.ocd')) \
	              + glob.glob(os.path.join('boards', '*', '*.ocd'))) \
	if 'out' not in f.split(os.sep) and 'lib' not in f.split(os.sep)]"
clean:
	rm -rf boards/out boards/*/out *-erc.rpt *-drc.rpt __pycache__ apps/__pycache__ */__pycache__ .mypy_cache
