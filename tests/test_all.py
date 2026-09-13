"""One self-check for everything (asserts only, no framework). Typed strict."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from typing import cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit import Board, Loader, Module
from ocdcircuit import agent
from ocdcircuit.core import Context, Plugin
from ocdcircuit.types import Constraint

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")


class PSU(Module):
    def build(self, b: Board) -> None:
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
c0 = agent.parse_constraint("keep U1 near C1")
assert c0 is not None and c0["t"] == "near"
c1 = agent.parse_constraint("fix J1 at 3 10")
assert c1 is not None and c1["t"] == "fixed"
assert agent.parse_constraint("route GND on bottom") == {"t": "layer", "net": "GND", "layer": 1}
wc = agent.parse_constraint("trace VCC 0.5")
assert wc is not None and wc["width"] == 0.5

# hot-swap: mount alt plugin, use(), undo → back to default
class AltPlacer(Plugin[float]):
    kind, key = "placer", "alt"

    def run(self, board: Board, *a: object, **k: object) -> float:
        return -1.0

b = Board("swap", 40, 30)
aplug = b.plugins().get("placer")
assert isinstance(aplug, Plugin) and aplug.key == "diffusion"
AltPlacer("placer:alt").mount(b.ctx)
s = b.ctx.snapshot()
b.use("placer", "alt")
assert b.place() == -1.0
b.ctx.rollback(s)
dplug = b.plugins().get("placer")
assert isinstance(dplug, Plugin) and dplug.key == "diffusion"
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

# committed board (with psu include) loads, merges, round-trips
ocd = open(os.path.join(EX, "blinky_555.ocd")).read()
bo = agent.loads(ocd, base=EX)
assert {p.ref for p in bo.parts.values()} == {"U1", "R1", "R2", "R3", "C1", "C2", "D1",
                                              "PSU_J1", "PSU_C1", "PSU_C2"}
assert len(bo.nets["GND"].pins) == 7  # 4 local + J1.2/C1.2/C2.- via auto-join
assert any(c == {"t": "layer", "net": "GND", "layer": 1} for c in bo.constraints)
assert "fix PSU_J1 at 3 15" in agent.dumps(bo)
assert "use psu.ocd as PSU" in agent.dumps(bo)
b2 = agent.loads(agent.dumps(bo), base=EX)
assert agent.dumps(b2) == agent.dumps(bo)
try:
    agent.loads("part R1 R0805\n")
    raise AssertionError("should have raised")
except ValueError:
    pass

# include errors: cycle + missing file
os.makedirs(os.path.join(EX, "tmp_inc"), exist_ok=True)
open(os.path.join(EX, "tmp_inc", "a.ocd"), "w").write(
    "board a 10x10\nuse b.ocd\npart R1 R0805\nnet N: R1.1\n")
open(os.path.join(EX, "tmp_inc", "b.ocd"), "w").write(
    "board b 10x10\nuse a.ocd\npart R2 R0805\nnet N: R2.1\n")
try:
    agent.loads(open(os.path.join(EX, "tmp_inc", "a.ocd")).read(),
                base=os.path.join(EX, "tmp_inc"))
    raise AssertionError("cycle should fail")
except ValueError as e:
    assert "cycle" in str(e)
try:
    agent.loads("board t 10x10\nuse nope.ocd\npart R1 R0805\nnet N: R1.1\n", base=EX)
    raise AssertionError("missing should fail")
except ValueError as e:
    assert "no such file" in str(e)
import shutil
shutil.rmtree(os.path.join(EX, "tmp_inc"))

# CLI builds the committed file (exit 0 = DRC clean)
proc = subprocess.run([sys.executable, os.path.join(HERE, "..", "ocd.py"),
                       os.path.join(EX, "blinky_555.ocd")],
                      capture_output=True, text=True)
assert proc.returncode == 0, proc.stdout + proc.stderr

# solver frames stream (animation API)
pf: list[dict[str, object]] = []
rf: list[dict[str, object]] = []
bo.place(seeds=1, iters=30, frames=pf, every=10)
bo.route_board(frames=rf)
assert len(pf) >= 2 and "pos" in pf[-1]
assert len(rf) >= 1 and "segs" in rf[0]

# JSON wire IR round-trips
bj = agent.from_json(agent.to_json(bo))
assert {p.ref for p in bj.parts.values()} == set(bo.parts)
assert agent.to_json(bj).startswith("{")
assert bj.render("svg").startswith("<svg")
assert bj.render("stl").startswith("solid")

# fab profiles: oshpark is stricter than jlc on drills
from ocdcircuit import fab
assert fab.get("oshpark")["min_drill"] == 0.508
bo.fab = "oshpark"
ro = bo.check()
assert ro["fab"] == "oshpark"
bo.fab = "jlc"
assert not cast(list[str], bo.check()["errors"])

# mix-and-match: every placer × every router × every silk resolves + runs
for pl in ["diffusion", "compact", "thermal"]:
    for rt in ["lroute", "maze"]:
        bm = agent.loads(ocd, base=EX)
        bm.place(pl, seeds=2, iters=100)
        bm.route_board(rt)
        assert not cast(list[str], bm.check()["errors"]), (pl, rt)
for sk in ["ref", "full", "fab"]:
    silks = bo.silk(sk)
    assert isinstance(silks["texts"], list) and isinstance(silks["dots"], list)
assert len(cast(list[object], bo.silk("ref")["texts"])) == len(bo.parts)
assert len(cast(list[object], bo.silk("full")["texts"])) >= len(bo.parts)
try:
    bo.silk("nope")
    raise AssertionError("should have raised")
except KeyError:
    pass

# full flow on 555-ish mini board, plugin-dispatched
b = Board("mini", 40, 30)
b.add_part("U1", "SOIC8", "NE555")
b.add_part("R1", "R0805", "1k")
b.add_part("C1", "C0805", "10u")
b.add_part("J1", "PINHD2", "9V")
nets: dict[str, list[tuple[str, str]]] = {
    "VCC": [("J1", "1"), ("U1", "8")], "GND": [("J1", "2"), ("U1", "1"), ("C1", "1")],
    "N1": [("U1", "3"), ("R1", "1")], "N2": [("R1", "2"), ("C1", "2")]}
for net, pins in nets.items():
    for ref, pin in pins:
        b.connect(net, ref, pin)
b.constrain(cast(Constraint, {"t": "fixed", "ref": "J1", "x": 3.0, "y": 15.0}))
b.place(seeds=3, iters=200)
b.route_board()
chk = b.check()
assert not cast(list[str], chk["errors"]), chk["errors"]
with tempfile.TemporaryDirectory() as d:
    files = (b.export("jlc", outdir=d) + b.export("kicad", outdir=d)
             + b.export("json", outdir=d) + b.export("ocd", outdir=d))
    assert any(f.endswith(".GTL.gbr") for f in files)
    assert any(f.endswith(".kicad_pcb") for f in files)
    assert any(f.endswith(".TXT") for f in files)
    assert any(f.endswith("BOM.csv") for f in files)
    assert any(f.endswith(".json") for f in files)
    assert any(f.endswith(".ocd") for f in files)
    kc = open([f for f in files if f.endswith(".kicad_pcb")][0]).read()
    assert kc.startswith("(kicad_pcb") and "(segment" in kc and "(footprint" in kc

# MCP stdio server: initialize → list → load → solve → patch → check
import json as _json
mcp = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "mcp.py")],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE)
_mid = [0]

def _rpc(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
    assert mcp.stdin is not None and mcp.stdout is not None
    _mid[0] += 1
    body = _json.dumps({"jsonrpc": "2.0", "id": _mid[0], "method": method,
                        "params": params or {}}).encode()
    mcp.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    mcp.stdin.flush()
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        head += mcp.stdout.read(1)
    ln = [l for l in head.decode().split("\r\n") if "content-length" in l.lower()][0]
    return cast(dict[str, object], _json.loads(mcp.stdout.read(int(ln.split(":")[1].strip()))))

def _call(name: str, args: dict[str, object]) -> dict[str, object]:
    r = _rpc("tools/call", {"name": name, "arguments": args})
    assert "error" not in r, r
    content = cast(list[dict[str, object]], cast(dict[str, object], r["result"])["content"])
    return cast(dict[str, object], _json.loads(str(content[0]["text"])))

assert cast(dict[str, object], _rpc("initialize")["result"])["serverInfo"] == {
    "name": "ocd-circuit", "version": "0.2"}
assert len(cast(list[object], cast(dict[str, object], _rpc("tools/list")["result"])["tools"])) == 12
assert _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})["parts"] == 10
solved = _call("solve", {"placer": "compact", "router": "maze"})
assert solved["errors"] == [] and solved["warnings"] == [], solved
assert _call("apply_patch", {"ops": [{"op": "constrain",
        "c": {"t": "near", "a": "U1", "b": "R1", "w": 1}}]})["applied"] == 1
assert _call("parse_constraint", {"text": "keep U1 near C1"})["constraint"] == {
    "t": "near", "a": "U1", "b": "C1", "w": 2.0}
assert _call("check", {})["errors"] == []
assert "placer:diffusion" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "error" in _rpc("tools/call", {"name": "nope", "arguments": {}})
mcp.kill()
print("MCP OK")

# custom .fp footprints + edge-mount: exotic parts without Python
from ocdcircuit import footprint as _fp
name, meta = _fp.load_file(os.path.join(EX, "usb_c_edge.fp"))
assert name == "USB_C_EDGE_GCT" and meta.get("edge") is True
assert len(cast(dict[str, object], meta["pads"])) == 26  # 2x12 + 2 shell
try:
    _fp.loads("pad A1 0 0\n")
    raise AssertionError("should have raised")
except ValueError:
    pass
bu = agent.loads(open(os.path.join(EX, "usb_breakout.ocd")).read(), base=EX)
assert "J1" in bu.parts and bu.parts["J1"].fp == "USB_C_EDGE_GCT"
assert "A5" in bu.parts["J1"].pins_of(bu._lib())
bu.place(seeds=2, iters=100)
bu.route_board()
assert bu.check()["errors"] == [], bu.check()["errors"]  # edge overhang exempt
assert any(f.endswith(".kicad_pcb") for f in bu.export("kicad", outdir=tempfile.mkdtemp()))
print("ALL OK")
