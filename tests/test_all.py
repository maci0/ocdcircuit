"""One self-check for everything (asserts only, no framework)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit import Board, Loader, Module
from ocdcircuit import agent
from ocdcircuit.core import Context, Plugin


class PSU(Module):
    def build(self, b):
        self.add(b, "J1", "PINHD2", "9V", x=3, y=15)
        b.connect("VCC", "J1", "1")
        b.connect("GND", "J1", "2")


# core: undo + services
ctx = Context()
snap = ctx.snapshot()
ctx.emit(lambda: None, lambda: None)
ctx.undo()
assert len(ctx._undos) == snap
ctx.provide("vcc", 9)
assert ctx.require("vcc") == 9

# temporal composability: module mount/unmount removes exactly its parts
b = Board("t", 40, 30)
ld = Loader(b.ctx)
ld.mount(PSU("psu"), b)
assert "J1" in b.parts
ld.unmount("psu")
assert "J1" not in b.parts and "VCC" in b.nets  # nets persist, pins cleaned
assert all(p[0] != "J1" for net in b.nets.values() for p in net.pins)

# agent patch fully undoes
b = Board("t2", 40, 30)
s = b.ctx.snapshot()
agent.apply_patch(b, [
    {"op": "add_part", "ref": "R1", "fp": "R0805", "value": "1k"},
    {"op": "connect", "net": "N", "ref": "R1", "pin": "1"},
    {"op": "constrain", "c": {"t": "near", "a": "R1", "b": "R1", "w": 1}},
])
assert "R1" in b.parts
b.ctx.rollback(s)
assert "R1" not in b.parts

# NL constraints
assert agent.parse_constraint("keep U1 near C1")["t"] == "near"
assert agent.parse_constraint("fix J1 at 3 10")["t"] == "fixed"
assert agent.parse_constraint("route GND on bottom") == {"t": "layer", "net": "GND", "layer": 1}
assert agent.parse_constraint("trace VCC 0.5")["width"] == 0.5

# hot-swap: mount alt plugin, use(), undo → back to default
class AltPlacer(Plugin):
    kind, key = "placer", "alt"
    def run(self, board):
        return -1.0

b = Board("swap", 40, 30)
assert b.plugins().get("placer").key == "diffusion"
AltPlacer("placer:alt").mount(b.ctx)
s = b.ctx.snapshot()
b.use("placer", "alt")
assert b.place() == -1.0
b.ctx.rollback(s)
assert b.plugins().get("placer").key == "diffusion"
assert b.place(seeds=1, iters=5) is not None  # still works after swap-back

# use() on unmounted key fails loudly
try:
    b.use("placer", "nope")
    raise AssertionError("should have raised")
except KeyError:
    pass

# .ocd doc facts: minimal example parses; error lines carry numbers
mini = agent.loads("board rc 20x10\npart R1 R0805 1k\npart C1 C0805 100n\n"
                   "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\nfix R1 at 3 5\n")
assert set(mini.parts) == {"R1", "C1"}
for bad, frag in [
    ("board t 40x30\npart R1\n", "line 2"),
    ("board t 40x30\npart R1 NOPE\n", "unknown footprint"),
    ("board t 40x30\npart R1 R0805\nnet N: R1.9\n", "no pin"),
    ("board t 40x30\npart R1 R0805\nnet N: R9.1\n", "unknown part"),
    ("board t 40x30\nboard q 10x10\n", "duplicate board"),
    ("board t 40x30\nfrobnicate\n", "unknown statement"),
]:
    try:
        agent.loads(bad)
        raise AssertionError(f"doc example should fail: {bad!r}")
    except ValueError as e:
        assert frag in str(e), f"{frag!r} not in {e}"
here = os.path.dirname(os.path.abspath(__file__))
ocd = open(os.path.join(here, "..", "examples", "blinky_555.ocd")).read()
bo = agent.loads(ocd)
# CLI builds the same file with zero Python (exit 0 = DRC clean)
import subprocess
r = subprocess.run([sys.executable, os.path.join(here, "..", "ocd.py"),
                    os.path.join(here, "..", "examples", "blinky_555.ocd")],
                   capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
assert {p.ref for p in bo.parts.values()} == {"U1", "R1", "R2", "R3", "C1", "C2", "D1", "J1"}
assert len(bo.nets["GND"].pins) == 5
assert any(c == {"t": "layer", "net": "GND", "layer": 1} for c in bo.constraints)
assert "fix J1 at 3 15" in agent.dumps(bo)
b2 = agent.loads(agent.dumps(bo))
assert agent.dumps(b2) == agent.dumps(bo)
try:
    agent.loads("part R1 R0805\n")
    raise AssertionError("should have raised")
except ValueError:
    pass

# JSON is the wire IR: round-trips the committed .ocd board exactly
bj = agent.from_json(agent.to_json(bo))
assert {p.ref for p in bj.parts.values()} == {"U1", "R1", "R2", "R3", "C1", "C2", "D1", "J1"}
assert len(bj.nets["GND"].pins) == 5
assert agent.to_json(bj).startswith("{")
assert bj.render("svg").startswith("<svg")
assert bj.render("stl").startswith("solid")

# full flow on 555-ish mini board, plugin-dispatched
b = Board("mini", 40, 30)
b.add_part("U1", "SOIC8", "NE555")
b.add_part("R1", "R0805", "1k")
b.add_part("C1", "C0805", "10u")
b.add_part("J1", "PINHD2", "9V")
for net, pins in {"VCC": [("J1", "1"), ("U1", "8")], "GND": [("J1", "2"), ("U1", "1"), ("C1", "1")],
                  "N1": [("U1", "3"), ("R1", "1")], "N2": [("R1", "2"), ("C1", "2")]}.items():
    for ref, pin in pins:
        b.connect(net, ref, pin)
b.constrain({"t": "fixed", "ref": "J1", "x": 3.0, "y": 15.0})
b.place(seeds=3, iters=200)
b.route_board()
r = b.check()
assert not r["errors"], r["errors"]
with tempfile.TemporaryDirectory() as d:
    files = b.export("jlc", outdir=d) + b.export("json", outdir=d) + b.export("ocd", outdir=d)
    assert len(files) == 12, files
    assert any(f.endswith(".GTL.gbr") for f in files)
    assert any(f.endswith(".TXT") for f in files)
    assert any(f.endswith("BOM.csv") for f in files)
    assert any(f.endswith(".json") for f in files)
    assert any(f.endswith(".ocd") for f in files)
print("ALL OK")
