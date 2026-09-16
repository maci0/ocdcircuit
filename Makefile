BOARD ?= boards/blinky_555.ocd

# Hermetic-ish local/CI gate: C locale + UTC so sort/date never leak host
# settings into check output; SOURCE_DATE_EPOCH stabilizes fab.zip mtimes.
export LC_ALL := C
export TZ := UTC
export SOURCE_DATE_EPOCH ?= 0

.PHONY: check run lint doctor test snap bench farm fabsweep clean

check: lint test			# everything green before commit
lint:				# types + source lint (no place/route)
	mypy
	ruff check
	python -m apps.ocd lint $(BOARD)
doctor:				# tooling self-check
	python -m apps.ocd doctor
test:				# unit suite + golden snapshots + studio smoke gate
	python tests/test_all.py
	python tests/test_snapshot.py
	python tests/test_paper.py
	python tests/test_studio.py
run:				# webui → http://localhost:8077
	python -m apps.studio $(BOARD)
snap:				# re-pin goldens after intended geometry change
	SNAP=1 python tests/test_snapshot.py
bench:				# 5420-part stress (~5 min, not in check)
	python -m benches.discrete6502.bench 1 5
farm:				# every board loads+solves (breath-ketone density excepted)
	python -c "import sys, glob; sys.path.insert(0, '.'); \
	from ocdcircuit import agent; \
	[(_b := agent.loads(open(f).read(), base=f.rsplit('/', 1)[0]), \
	_b.place(seeds=2, iters=100), _b.route_board(), \
	print(f, len(_b.check()['errors']), 'errors')) \
	for f in sorted(glob.glob('boards/*.ocd') + glob.glob('boards/*/*.ocd')) \
	if '/out/' not in f]"
fabsweep:			# every board x every fab (profile discrimination check)
	python -c "import sys, glob; sys.path.insert(0, '.'); \
	from ocdcircuit import agent, fab; \
	[(_b := agent.loads(open(f).read(), base=f.rsplit('/', 1)[0]), \
	_b.place(seeds=2, iters=100), _b.route_board(), \
	print(f.split('/')[-1], ' '.join(f'{g}={len(_b.check(\"fab\", fab=g)[\"errors\"])}' \
	for g in fab.list_fabs()))) \
	for f in sorted(glob.glob('boards/*.ocd') + glob.glob('boards/*/*.ocd')) \
	if '/out/' not in f]"
clean:
	rm -rf boards/out boards/*/out *-erc.rpt *-drc.rpt __pycache__ apps/__pycache__ */__pycache__ .mypy_cache
