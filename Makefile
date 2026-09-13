BOARD ?= boards/blinky_555.ocd

.PHONY: check run lint doctor test snap bench clean

check: lint test			# everything green before commit
lint:				# types + source lint (no place/route)
	mypy
	python apps/ocd.py lint $(BOARD)
doctor:				# tooling self-check
	python apps/ocd.py doctor
test:				# unit suite + golden snapshots
	python tests/test_all.py
	python tests/test_snapshot.py
run:				# webui → http://localhost:8077
	python apps/studio.py $(BOARD)
snap:				# re-pin goldens after intended geometry change
	SNAP=1 python tests/test_snapshot.py
bench:				# 5420-part stress (~5 min, not in check)
	python benches/monster6502/bench.py 1 5
clean:
	rm -rf boards/out/* __pycache__ apps/__pycache__ */__pycache__ .mypy_cache
