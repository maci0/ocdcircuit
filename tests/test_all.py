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
EX = os.path.join(HERE, "..", "boards")


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
    ("board t 40x30\npart R1 R0805\npart R1 R0805\n", "duplicate part R1"),
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
proc = subprocess.run([sys.executable, os.path.join(HERE, "..", "apps", "ocd.py"),
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
assert cast(str, bj.render("svg")).startswith("<svg")
assert cast(str, bj.render("stl")).startswith("solid")
assert cast(bytes, bj.render("png"))[:8] == b"\x89PNG\r\n\x1a\n"
assert "<canvas" in cast(str, bj.render("html3d"))

# fab profiles: oshpark is stricter than jlc on drills; jlc-flex is ENIG-only FPC
from ocdcircuit import fab
assert fab.get("oshpark")["min_drill"] == 0.508
assert fab.get("jlc-flex")["layers"] == (1, 2, 4)
assert fab.get("jlc-flex")["finishes"] == ("ENIG",)
# flex: bend/stiffener round-trip, DRC, no maze vias in dynamic bends
_fb = agent.loads("board f 60x20 2L\npart J1 PINHD4\npart U1 SOIC8 X\n"
                  "net A: J1.1 U1.1\nnet B: J1.2 U1.2\n"
                  "bend 30 10 6x20 r5\nstiffener 5 10 10x12 FR4 0.4\n"
                  "fix J1 at 8 10\nfix U1 at 50 10\n", base=EX)
assert agent.dumps(agent.loads(agent.dumps(_fb), base=EX)) == agent.dumps(_fb)
_fb.fab = "jlc-flex"
_fb.place(seeds=1, iters=30)
_fb.route_board("maze")
assert _fb.check("jlc-flex")["errors"] == [], _fb.check("jlc-flex")["errors"]
assert not [t for t in _fb.traces if getattr(t, "via", False) and 27 <= t.x1 <= 33]
_tb = agent.loads("board t 40x30\npart R1 R0805 1k\nbend 20 15 10x10 r1\nfix R1 at 20 15\n", base=EX)
errs = cast(list[str], _tb.check("jlc-flex")["errors"])
assert any(e.startswith("bend-part") for e in errs) and any(e.startswith("bend-radius") for e in errs)
# fiducials + deadzones: round/square, explicit/near-part, maze + DRC + dumps
_fdz = agent.loads("board t 40x30\npart F1 FIDUCIAL\npart R1 R0805 1k\npart C1 C0805 100n\n"
                   "fix F1 at 3 3\nfix R1 at 30 20\nfix C1 at 8 25\n"
                   "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n"
                   "keepout near F1 d4\nkeepout 20 15 d6\nkeepout 30 8 6x4\n", base=EX)
assert agent.dumps(agent.loads(agent.dumps(_fdz), base=EX)) == agent.dumps(_fdz)
_fdz.place(seeds=1, iters=30)
_fdz.route_board("maze")
assert _fdz.check()["errors"] == [], _fdz.check()["errors"]
assert _fdz.check()["warnings"] == [], _fdz.check()["warnings"]
_dzbad = agent.loads("board t 40x30\npart F1 FIDUCIAL\npart R1 R0805 1k\n"
                     "fix F1 at 20 15\nfix R1 at 20 15\nnet N: R1.1 R1.2\n"
                     "keepout near F1 d4\n", base=EX)
_dzbadw = cast(list[str], _dzbad.check()["warnings"])
assert any(str(w).startswith("keepout R1") for w in _dzbadw)
assert not any(str(w).startswith("keepout F1 ") for w in _dzbadw)
bo.fab = "oshpark"
ro = bo.check()
assert ro["fab"] == "oshpark"
bo.fab = "jlc"
assert not cast(list[str], bo.check()["errors"])

# mix-and-match: every placer × every router × every silk resolves + runs
for pl in ["diffusion", "compact", "thermal"]:
    for rt in ["lroute", "maze", "coarse", "wiremask"]:
        bm = agent.loads(ocd, base=EX)
        bm.place(pl, seeds=2, iters=100)
        bm.route_board(rt, **({"pop": 2, "gen": 1} if rt == "wiremask" else {}))
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
    # drill file carries PTH holes (J1=PINHD2), grouped by tool diameter
    drl = open([f for f in files if f.endswith(".TXT")][0]).read()
    assert "M48" in drl and "M30" in drl
    j1xy = sorted((round(float(b.pad_pos("J1", pin)[0]), 3),
                   round(float(b.pad_pos("J1", pin)[1]), 3)) for pin in ("1", "2"))
    for x, y in j1xy:
        assert f"X{x:.3f}Y{y:.3f}" in drl, drl
    assert any(f.endswith("BOM.csv") for f in files)
    assert any(f.endswith(".json") for f in files)
    assert any(f.endswith(".ocd") for f in files)
    kc = open([f for f in files if f.endswith(".kicad_pcb")][0]).read()
    assert kc.startswith("(kicad_pcb") and "(segment" in kc and "(footprint" in kc

# MCP stdio server: initialize → list → load → solve → patch → check
import json as _json
mcp = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "apps", "mcp.py")],
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
assert len(cast(list[object], cast(dict[str, object], _rpc("tools/list")["result"])["tools"])) == 18
assert _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})["parts"] == 10
assert _call("lint", {})["errors"] == []
assert _call("doctor", {})["ok"] is True
solved = _call("solve", {"placer": "compact", "router": "maze"})
assert solved["errors"] == [] and solved["warnings"] == [], solved
assert _call("apply_patch", {"ops": [{"op": "constrain",
        "c": {"t": "near", "a": "U1", "b": "R1", "w": 1}}]})["applied"] == 1
# declarative set_state: idempotent, order-independent, atomic
_ss1 = _call("set_state", {"parts": {"QX": {"fp": "R0805", "value": "1k"}},
                           "nets": {"QN": ["QX.1", "QX.2"]}, "constraints": []})
assert cast(dict[str, object], _ss1["applied"])["added"] == 1, _ss1
_ss2 = _call("set_state", {"parts": {"QX": {"fp": "R0805", "value": "1k"}},
                           "nets": {"QN": ["QX.2", "QX.1"]}, "constraints": []})
assert _ss2["applied"] == {"added": 0, "removed": 0, "updated": 0, "nets": 0}, _ss2
_bad = _call("set_state", {"parts": {"QY": {"fp": "NOPE"}}})
assert "error" in _bad, _bad  # atomic: nothing applied
_st = _call("get_state", {})
assert any(p["ref"] == "QX" for p in cast(list[dict[str, object]],
           cast(dict[str, object], _st["ir"])["parts"]))  # QX survived rollback
assert _call("parse_constraint", {"text": "keep U1 near C1"})["constraint"] == {
    "t": "near", "a": "U1", "b": "C1", "w": 2.0}
assert _call("check", {})["errors"] == []
assert "placer:diffusion" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "importer:fp" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "simulate:mna" in cast(list[str], _call("list_plugins", {})["plugins"])
# failure memory: raising plugin is marked failed, previous entry serves,
# explicit use() re-arms (harness-loader style rollback)
from ocdcircuit.core import Plugin as _Pl
_bo = agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.1 R1.2\n")


class _Boom(_Pl[dict[str, object]]):
    kind, key = "drc", "boom"

    def run(self, board: object, *a: object, **k: object) -> dict[str, object]:
        raise RuntimeError("kaput")


_Boom("drc:boom").mount(_bo.ctx)
_bo.use("drc", "boom")
try:
    _bo.check("boom")
    raise AssertionError("should have raised")
except RuntimeError:
    pass
assert _bo.plugins().active.get("drc") != "boom"
try:
    _bo.check("boom")
    raise AssertionError("should have refused")
except KeyError:
    pass
_bo.use("drc", "boom")  # re-arm
assert _bo.plugins().active.get("drc") == "boom"
assert "importer:eagle-brd" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "importer:easyeda" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "exporter:easyeda" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "simulate:ngspice" in cast(list[str], _call("list_plugins", {})["plugins"])
assert cast(float, _call("calc", {"what": "divider", "vin": 9, "rtop": 10000,
                                  "rbot": 4700})["vout"]) > 2.8
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
# calculators (IPC-2221 etc.): rule-of-thumb values
from ocdcircuit import calc
assert abs(calc.trace_width(1.0) - 0.3) < 0.05
assert abs(calc.divider(9, 10000, 4700) - 2.88) < 0.05
assert abs(calc.divider_pick(9, 5) - 8000) < 1

# foreign: kicad_mod + eagle lbr/brd + tscircuit JSON + easyeda Std
from ocdcircuit import foreign
_kmod = '''(footprint "T1" (layer "F.Cu") (at 0 0)
  (pad "1" smd rect (at -1 0) (size 1 1.5) (layers "F.Cu"))
  (pad "2" smd rect (at 1 0) (size 1 1.5) (layers "F.Cu"))
  (model "x.stp"))'''
_fn, _fm = foreign.kicad_mod(_kmod)
assert _fn == "T1" and set(cast(dict[str, object], _fm["pads"])) == {"1", "2"}
assert _fm["models"] == ["x.stp"]
_lbr = '''<eagle><drawing><library><packages><package name="P1">
<smd name="1" x="0" y="0" dx="1" dy="1"/><pad name="2" x="2" y="0" drill="0.8"/>
</package></packages></library></drawing></eagle>'''
assert foreign.eagle_lbr(_lbr)[0][0] == "P1"
_brd = '''<eagle><drawing><board>
<plain><wire x1="0" y1="0" x2="20" y2="0" layer="20"/><wire x1="20" y1="0" x2="20" y2="15" layer="20"/><wire x1="20" y1="15" x2="0" y2="15" layer="20"/><wire x1="0" y1="15" x2="0" y2="0" layer="20"/></plain>
<libraries><library><packages><package name="P1">
<smd name="1" x="-0.95" y="0" dx="1" dy="1.2"/><smd name="2" x="0.95" y="0" dx="1" dy="1.2"/>
</package></packages></library></libraries><elements/><signals/></board></drawing></eagle>'''
assert foreign.eagle_lbr(_brd)[0][0] == "P1"
_ebr = cast(dict[str, object], foreign.eagle_brd(_brd)["board"])
assert _ebr["w"] == 20.0
_tj = [{"type": "pcb_smtpad", "footprint": "C1", "port_hints": ["1"], "x": 0, "y": 0}]
assert foreign.tscircuit_json(_tj)[0][0] == "C1"
_ezfp: dict[str, object] = {"head": "4~1.7.5", "title": "EZ1",
                            "shape": ["PAD~RECT~0~0~9~5~1~~1~~0~g1",
                                      "PAD~RECT~20~0~9~5~1~~2~~0~g2"]}
assert cast(list[tuple[str, object]], foreign.easyeda_doc(_ezfp))[0][0] == "EZ1"
_ebb = agent.loads("board t 20x20\npart R1 R0805 1k\npart R2 R0805 1k\n"
                   "net N: R1.2 R2.1\nnet GND: R1.1 R2.2\n")
_ebb.place()
_ebb.route_board()
_ezf = _ebb.export("easyeda", outdir=tempfile.mkdtemp())[0]
assert _ezf.endswith(".easyeda.json")
import json as _jj
_ezrt = foreign.easyeda_doc(_jj.loads(open(_ezf).read()))
assert isinstance(_ezrt, dict)
assert {p["ref"] for p in cast(list[dict[str, object]], _ezrt["parts"])} == {"R1", "R2"}
assert set(cast(dict[str, object], _ezrt["nets"])) == {"N", "GND"}

# textured 3D: glTF materials + shared mesh builder
import json as _jj
_g = _jj.loads(bo.render("gltf"))
assert {m["name"] for m in _g["materials"]} >= {"mask", "copper", "chip"}
assert len(_g["meshes"]) == len(_g["materials"])
for _m in _g["meshes"]:
    _at = _m["primitives"][0]["attributes"]
    assert set(_at) >= {"POSITION", "NORMAL", "TEXCOORD_0"}, _at
assert len(_g["images"]) == len(_g["materials"])
assert all(_t["sampler"] == 0 for _t in _g["textures"])

# blocks: repeatable units — stamp 3x, join, round-trip exactly
_bb = agent.loads("board t 60x40\nblock ch\npart U QFN28\npart C C0805 100n\n"
                  "net N: U.3 C.2\nnet GND: U.1 C.1\nend\n"
                  "instance ch as A\ninstance ch as B join GND\n")
assert sorted(_bb.parts) == ["A_C", "A_U", "B_C", "B_U"]
assert ("B_C", "1") in _bb.nets["GND"].pins and ("B_U", "1") in _bb.nets["GND"].pins
assert ("A_C", "1") in _bb.nets["GND"].pins  # GND auto-joins even unlisted
assert "block ch" in agent.dumps(_bb) and "instance ch as B join GND" in agent.dumps(_bb)
assert agent.dumps(agent.loads(agent.dumps(_bb))) == agent.dumps(_bb)
for _bbad, _bfrag in [
    ("board t 10x10\nblock a\npart R1 R0805\nblock b\n", "nested blocks"),
    ("board t 10x10\nend\n", "end without block"),
    ("board t 10x10\nblock a\npart R1 R0805\nend\nblock a\npart R2 R0805\nend\n", "duplicate block"),
    ("board t 10x10\ninstance nope as X\n", "unknown block"),
    ("board t 10x10\nblock a\nuse x.ocd\nend\n", "not allowed inside block"),
]:
    try:
        agent.loads(_bbad)
        raise AssertionError(f"should have raised: {_bbad!r}")
    except ValueError as e:
        assert _bfrag in str(e), f"{_bfrag!r} not in {e}"
# workspace: score + diff go through the plugin registry like prod code
_sb = agent.loads("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\nfix R1 at 3 5\nfix C1 at 8 5\n")
_ss = _sb.score()
_stot = _ss["total"]
assert isinstance(_stot, (int, float)) and 0 <= _stot <= 100
assert _ss["grade"] in ("A", "B", "C", "D", "F")
assert set(cast(dict[str, float], _ss["parts"])) == {"grid", "orientation", "spacing", "edge", "compact"}
# tidy scorecard: components + coverage, None for undefined inputs
_sb.place(seeds=1, iters=50)
_sb.route_board()
_tt = _sb.score(tidy=True)
assert _tt["T1_crossings"] == 0 and _tt["T3_orthogonality"] == 1.0
assert _tt["T4_vias"] == {"total": 0, "per_net": {}}
assert _tt["T7_alignment"] == 1.0
assert cast(dict[str, object], _tt["T10_orientation"])["cardinal"] == 1.0
assert _tt["T11_copper_balance"] is None and _tt["T12_acid_traps"] is None
assert _tt["T13_schematic"] is None and _tt["T15_silk_consistency"] == 1.0
assert isinstance(_tt["coverage"], str)
_tu = agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.2\n").score(tidy=True)
assert _tu["T1_crossings"] is None and _tu["T3_orthogonality"] is None
assert _tu["T4_vias"] is None and _tu["T5_headroom"] is None
assert _sb.diff(_sb) == ""
_drep = _sb.diff(agent.loads("board t 40x30\npart R1 R0805 10k\n"
                             "net N: R1.2\nnet GND: R1.1\n"))
assert "- part C1" in _drep
# hierarchical placer: rigid instances, falls back cleanly without them
assert _bb.place("hierarchical", seeds=1, iters=50) is not None
_offs: dict[str, tuple[float, float]] = {}
for _pre in ("A_", "B_"):
    _ux, _uy = _bb.parts[_pre + "U"].x, _bb.parts[_pre + "U"].y
    _offs[_pre] = (round(_bb.parts[_pre + "C"].x - _ux, 2),
                   round(_bb.parts[_pre + "C"].y - _uy, 2))
assert _offs["A_"] == _offs["B_"], _offs  # rigid: identical offsets
_nb = Board("plain", 20, 10)
_nb.add_part("R1", "R0805", "1k")
assert _nb.place("hierarchical", seeds=1, iters=10) is not None  # no-instance fallback
# sim transient (setup above)
_simb2 = agent.loads("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
                     "net VIN: R1.1\nnet VO: R1.2 C1.1\nnet GND: C1.2\n"
                     "sim vcc VIN 0 5\nsim tran 0.005 500\nsim probe VO\n")
_w = cast(list[float], cast(dict[str, object], _simb2.simulate(what="tran")["waves"])["VO"])
assert abs(_w[-1] - 5.0) < 0.05 and all(a <= c + 1e-9 for a, c in zip(_w, _w[1:]))
# ngspice plugin: same shape as mna + analog mna cannot do (skip if no binary)
import shutil as _sh
if _sh.which("ngspice") is not None:
    _ng0 = _simb2.simulate("ngspice", what="tran")
    _ngw = cast(list[float], cast(dict[str, object], _ng0["waves"])["VO"])
    assert abs(_ngw[-1] - 5.0) < 0.05, _ngw[-5:]
    _ngd = agent.loads("board t 40x30\npart R1 R0805 10k\npart R2 R0805 4k7\n"
                       "net VIN: R1.1\nnet VO: R1.2 R2.1\nnet GND: R2.2\nsim vcc VIN 9\n")
    assert abs(cast(dict[str, float], _ngd.simulate("ngspice")["nets"])["VO"] - 2.878) < 0.02
    # diode clipper (no mna equivalent): clamps ±0.7
    _ngc = agent.loads("board t 40x30\npart R1 R0805 1k\npart D1 D_SOD323\npart D2 D_SOD323\n"
                       "net IN: R1.1\nnet VO: R1.2 D1.2 D2.1\nnet GND: D1.1 D2.2\n"
                       "sim sine IN 0 5 1000\nsim tran 0.002 400\nsim probe VO\n")
    _cw = cast(list[float], cast(dict[str, object], _ngc.simulate("ngspice", what="tran")["waves"])["VO"])
    assert max(_cw) < 1.5 and min(_cw) > -1.5, (max(_cw), min(_cw))
    # BJT saturation (no mna equivalent): Vce < 0.5
    _ngq = agent.loads("board t 40x30\npart RB R0805 10k\npart RC R0805 1k\npart Q1 SOT23\n"
                       "net IN: RB.1\nnet B: RB.2 Q1.1\nnet VCC: RC.1\nnet OUT: RC.2 Q1.3\nnet GND: Q1.2\n"
                       "sim vcc VCC 5\nsim vcc IN 3\n")
    assert cast(dict[str, float], _ngq.simulate("ngspice")["nets"])["OUT"] < 0.5
    # RC lowpass ac: unity at LF, rolled off at HF
    _nga = agent.loads("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
                       "net IN: R1.1\nnet VO: R1.2 C1.1\nnet GND: C1.2\n"
                       "sim sine IN 0 1 1000\nsim ac 10 100000 20\nsim probe VO\n")
    _am = cast(list[float], cast(dict[str, object], _nga.simulate("ngspice", what="ac")["ac"])["VO"])
    assert abs(_am[0] - 1.0) < 0.05 and _am[-1] < 0.1
    # opamp x11 (no mna equivalent)
    _ngo = agent.loads("board t 40x30\npart U1 SOIC8 X\npart R1 R0805 1k\npart Rf R0805 10k\n"
                       "net IN: U1.3\nnet FB: U1.2 R1.2 Rf.1\nnet GND: R1.1\nnet OUT: U1.1 Rf.2\n"
                       "net VCC: U1.7\nnet VEE: U1.4\n"
                       "sim vcc VCC 15\nsim vcc VEE -15\nsim sine IN 0 1 100\n"
                       "sim op U1 OPIDEAL 1 3 2 7 4\nsim tran 0.02 200\nsim probe OUT\n")
    _ow = cast(list[float], cast(dict[str, object], _ngo.simulate("ngspice", what="tran")["waves"])["OUT"])
    assert 10.0 < max(_ow) < 12.0, _ow[-5:]
    # grammar round-trips
    _ngg = agent.loads("board t 40x30\npart D1 D_SOD323\nnet A: D1.1\nnet B: D1.2\n"
                       "sim d D1 BAT54\nsim ac 10 1e6 20\n")
    assert agent.dumps(agent.loads(agent.dumps(_ngg))) == agent.dumps(_ngg)
# lint: clean board passes, dirty board reports (no place/route needed)
_lb = agent.loads("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n")
assert _lb.lint() == {"errors": [], "warnings": []}, _lb.lint()
_ld = agent.loads("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.2\nfix ZZ at 5 5\nkeep R1 near ZZ\n"
                  "trace NONET 0.5\n")
_lr = _ld.lint()
assert any("ZZ" in e for e in cast(list[str], _lr["errors"])), _lr
assert any("single-pin net N" in w for w in cast(list[str], _lr["warnings"])), _lr
assert any("C1" in w for w in cast(list[str], _lr["warnings"])), _lr
assert any("NONET" in w for w in cast(list[str], _lr["warnings"])), _lr
# lint covers the whole constraint grammar, dedupes, never crashes on junk
_l2 = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n"
                  "match NZZZ\ndiff A B gap 0.01\npour NONET on 0\nsim probe GHOST\n"
                  "trace TINY 0.01\nroute N on 9\nkeep R1 near R1\n", base=EX)
_l2r = _l2.lint()
assert any("layer 9" in e for e in cast(list[str], _l2r["errors"])), _l2r
for frag in ("match on unknown net NZZZ", "pour on unknown net NONET",
             "sim probe on unknown net GHOST", "width on unknown net TINY",
             "outside sane range"):
    assert any(frag in w for w in cast(list[str], _l2r["warnings"])), (_l2r, frag)
assert len(cast(list[str], _l2r["warnings"])) == len(set(cast(list[str], _l2r["warnings"])))
# meta lines: title/rev/desc round-trip, flow into IR + KiCad title
_mb = agent.loads("board t 40x30\nmeta title Blinky 555\nmeta rev A\n"
                  "part R1 R0805 10k\nnet N: R1.1 R1.2\n")
assert _mb.meta == {"title": "Blinky 555", "rev": "A"}, _mb.meta
assert agent.dumps(agent.loads(agent.dumps(_mb))) == agent.dumps(_mb)
import json as _jm
assert _jm.loads(agent.to_json(_mb))["board"]["meta"] == {"title": "Blinky 555", "rev": "A"}
_kd = _mb.export("kicad", outdir=tempfile.mkdtemp())[0]
assert '(title "Blinky 555")' in open(_kd).read()
# placer auto-select: diffusion below 1000 parts, multilevel at/above
_seen: dict[str, object] = {}
_orig_run = Board._run


def _spy_run(self: object, kind: str, key: object, **k: object) -> object:
    _seen.update(kind=kind, key=key)
    raise RuntimeError("stop")


Board._run = _spy_run  # type: ignore[method-assign]
try:
    _lb.place()
except RuntimeError:
    pass
assert _seen == {"kind": "placer", "key": None}, _seen
_big = agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.1 R1.2\n")
for _i in range(1000):
    _big.parts[f"D{_i}"] = _big.parts["R1"]
try:
    _big.place()
except RuntimeError:
    pass
assert _seen == {"kind": "placer", "key": "multilevel"}, _seen
Board._run = _orig_run  # type: ignore[method-assign]
# doctor: registry healthy on a live board
_doc = _lb.plugins().get("doctor", "std")
assert isinstance(_doc, Plugin)
_docr = cast(dict[str, object], _doc.run(_lb))
assert _docr["ok"] is True, _docr
assert any(str(c.get("name")) == "plugin:lint"
           and c.get("ok") for c in cast(list[dict[str, object]], _docr["checks"]))
print("ALL OK")
