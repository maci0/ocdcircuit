BOARD ?= boards/blinky_555.ocd

.PHONY: check run lint doctor test snap clean

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
clean:
	rm -rf boards/out/* __pycache__ apps/__pycache__ */__pycache__ .mypy_cache
