"""One self-check for everything (asserts only, no framework). Typed strict."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from typing import cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit import Board, Module
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
ctx.set("vcc", 9)
assert ctx.require("vcc") == 9

# temporal composability: module mount/unmount removes exactly its parts
b = Board("t", 40, 30)
_psu = PSU("psu")
_psu.mount(b.ctx, b)
assert "J1" in b.parts
_psu.unmount(b.ctx)
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
# patch + declare are self-atomic: mid-list failure leaves no residue
_ba = Board("ta", 40, 30)
_sa0 = _ba.ctx.snapshot()
try:
    agent.apply_patch(_ba, [
        {"op": "add_part", "ref": "R1", "fp": "R0805", "value": "1k"},
        {"op": "frobnicate"},
    ])
    raise AssertionError("should have raised")
except ValueError:
    pass
assert "R1" not in _ba.parts and _ba.ctx.snapshot() == _sa0
# non-finite API positions rejected (nan/inf poison geometry silently)
_bnan = Board("tn", 40, 30)
_bnan.add_part("R1", "R0805", "1k")
_bops: list[dict[str, object]] = [
    {"op": "move_part", "ref": "R1", "x": "nan", "y": 5},
    {"op": "add_part", "ref": "R2", "fp": "R0805", "x": "inf"}]
for _bop in _bops:
    try:
        agent.apply_patch(_bnan, [_bop])
        raise AssertionError(f"should have raised: {_bop}")
    except ValueError:
        pass
assert (_bnan.parts["R1"].x, _bnan.parts["R1"].y) == (20.0, 15.0)
assert "R2" not in _bnan.parts
# add_part direct floats guarded too (from_ir + programmatic API funnel here)
try:
    _bnan.add_part("R3", "R0805", x=float("nan"))
    raise AssertionError("should have raised")
except ValueError as e:
    assert "non-finite" in str(e), str(e)
assert "R3" not in _bnan.parts
# from_ir rejects non-\w+ refs (dumps must always reload)
try:
    agent.from_ir({"board": {"name": "t", "w": 40, "h": 30},
                   "parts": [{"ref": "../../x", "fp": "R0805"}],
                   "nets": {}})
    raise AssertionError("should have raised")
except ValueError as e:
    assert "bad part ref" in str(e), str(e)
_bd = Board("td", 40, 30)
_bd.add_part("R9", "R0805", "1k")
_s0 = _bd.ctx.snapshot()
try:
    _bd.declare({"parts": {"R1": {"fp": "NOPE"}}, "nets": {}, "constraints": []})
    raise AssertionError("should have raised")
except KeyError:
    pass
assert "R1" not in _bd.parts and "R9" in _bd.parts
assert _bd.ctx.snapshot() == _s0, "declare must roll back"
# patch carries attrs both ways (lcsc/dnp/class were unreachable via patch)
_bp = Board("t3", 40, 30)
_sp3 = _bp.ctx.snapshot()
agent.apply_patch(_bp, [
    {"op": "add_part", "ref": "R1", "fp": "R0805", "value": "10k",
     "attrs": {"lcsc": "C1", "dnp": "1"}},
    {"op": "connect", "net": "HV", "ref": "R1", "pin": "1", "attrs": {"class": "hv"}},
    {"op": "connect", "net": "HV", "ref": "R1", "pin": "2"},
])
assert _bp.parts["R1"].attrs == {"lcsc": "C1", "dnp": "1"}
assert _bp.nets["HV"].attrs == {"class": "hv"}
assert "dnp=1" in agent.dumps(_bp) and "class=hv" in agent.dumps(_bp)
_bp.ctx.rollback(_sp3)
assert "R1" not in _bp.parts and "HV" not in _bp.nets
# fuzz: seeded random ops always roll back to identical dumps (no state leaks)
import random as _rng
for _seed in (1337, 7331):
    _fz = _rng.Random(_seed)
    _fb = agent.loads("board fz 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                      "net N: R1.1 C1.2\nnet GND: R1.2 C1.1\n", base=EX)
    _fz0, _fz_text = _fb.ctx.snapshot(), agent.dumps(_fb)
    _refs = ["R1", "C1", "QX"]
    for _i in range(60):
        _r = _fz.choice(_refs)
        _k = _fz.randrange(9)
        try:
            if _k == 0:
                _fb.add_part(_r, "R0805", "1k")
            elif _k == 1:
                _fb.connect(_fz.choice(["N", "GND", "QN"]), _r, str(_fz.choice([1, 2])))
            elif _k == 2:
                _fb.constrain({"t": "near", "a": _r, "b": _fz.choice(_refs), "w": 1.0})
            elif _k == 3:
                _fb.move_part(_r, _fz.uniform(0, 40), _fz.uniform(0, 30))
            elif _k == 4:
                _fb.remove_part(_r)
            elif _k == 5:
                _fb.declare({"parts": {_r: {"fp": "R0805"}},
                             "nets": {"QN": [f"{_r}.1"]}, "constraints": []})
            elif _k == 6:
                _fb.set_board(_fz.uniform(20, 60), _fz.uniform(20, 60))
            elif _k == 7:
                _fb.use("placer", _fz.choice(["diffusion", "compact"]))
            else:
                _fb.place(seeds=1, iters=5)
                _fb.route_board("lroute")
        except (KeyError, ValueError, AssertionError):
            pass
    _fb.ctx.rollback(_fz0)
    assert agent.dumps(_fb) == _fz_text, f"undo fuzz leaked state (seed {_seed})"

# coarse route's temp constraint removes out-of-band; undo must not crash
_bc = agent.loads(open(os.path.join(EX, "blinky_555.ocd")).read(), base=EX)
_bc.place(seeds=2, iters=100)
_bc.route_board("coarse")
_bc.ctx.undo(2)
assert len(_bc.traces) == 0
# NL constraints
c0 = agent.parse_constraint("keep U1 near C1")
assert c0 is not None and c0["t"] == "near"
# near pulls in cost space: same layout cheaper when the pair is close
from ocdcircuit.solver import cost as _cost
_nc = agent.loads("board t 40x30\npart R1 R0805 10k\npart R2 R0805 10k\n"
                   "net N: R1.1 R2.1\nkeep R1 near R2 5\n", base=EX)
_nc.parts["R1"].x, _nc.parts["R1"].y = 5, 5
_nc.parts["R2"].x, _nc.parts["R2"].y = 10, 5
_c_near = _cost(_nc)
_nc.parts["R2"].x, _nc.parts["R2"].y = 35, 25
assert _cost(_nc) > _c_near
c1 = agent.parse_constraint("fix J1 at 3 10")
assert c1 is not None and c1["t"] == "fixed"
# duplicate position sources: later fix wins over part-line x=/y=
_fx = agent.loads("board t 40x30 2L\npart R1 R0805 10k x=3 y=5\nnet N: R1.1 R1.2\n"
                  "fix R1 at 30 25\n", base=EX)
_fx.place(seeds=1, iters=20)
assert (round(_fx.parts["R1"].x), round(_fx.parts["R1"].y)) == (30, 25)
# same rule, both directions: net-line attrs beat earlier route/trace
_nl = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "route N on 1\ntrace N 0.6\nN L0 w0.3 :: R1.1 C1.2\nGND :: R1.2 C1.1\n", base=EX)
_nl.place(seeds=1, iters=20)
_nl.route_board()
assert (_nl.nets["N"].layer, _nl.nets["N"].width) == (0, 0.3)
assert agent.parse_constraint("route GND on bottom") == {"t": "layer", "net": "GND", "layer": 1}
wc = agent.parse_constraint("trace VCC 0.5")
assert wc is not None and wc["width"] == 0.5
cc = agent.parse_constraint("class highvolt width=0.8 clearance=0.5 note=x")
assert cc is not None and cc["t"] == "class" and cc["width"] == 0.8 \
    and cc["clearance"] == 0.5 and cc["note"] == "x"
# codec fixpoint: every grammar production dumps→parses→dumps identically
_pre = ("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
        "net N: R1.1 C1.2\nnet GND: R1.2 C1.1\n")
for _line in ["keep R1 near C1 3", "fix R1 at 3 5", "route N on 1", "trace N 0.6",
              "route-grid 0.2", "route-penalty bend 3 via 20", "power N GND", "class hv width=0.8",
              "match N GND", "diff N GND gap 0.5", "silk 2", "nc R1.1",
              "pour GND on 0", "keepout 20 15 6x6", "keepout 20 15 d6",
              "keepout near R1 d4", "cutout 20 15 6x6", "hole 20 15 1.2",
              "bend 20 15 10x10 r2", "stiffener 20 15 10x6 FR4 0.4",
              "sim vcc N 5", "sim sine N 1 1 1000", "sim isrc N 0.01",
              "sim tran 0.01 100", "sim probe N", "sim clk N 2",
              "sim expect N == 5", "sim r R1 10k", "sim op N V 0 5",
              "sim lib x.lib", "sim ac 10 1000 5"]:
    _cb2 = agent.loads(_pre + _line + "\n", base=EX)
    _rt = agent.dumps(_cb2)
    assert agent.parse_constraint(_line) is not None, _line
    assert agent.dumps(agent.loads(_rt, base=EX)) == _rt, _line

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
# includes carry part/net attrs + pours across (lcsc/dnp/class drive fab/widths)
with tempfile.TemporaryDirectory() as _td:
    open(os.path.join(_td, "sub.ocd"), "w").write(
        "board sub 30x20 2L\npart R1 R0805 10k lcsc=C9 dnp=1\npart C1 C0805 100n\n"
        "HV class=hv :: R1.1 C1.1\nLV :: R1.2 C1.2\npour HV on 0\n")
    _inc = agent.loads("board t 60x40 2L\nuse sub.ocd as S\npart X1 R0805 1k\n"
                       "net Q: X1.1 X1.2\n", base=_td)
    assert _inc.parts["S_R1"].attrs == {"lcsc": "C9", "dnp": "1"}
    assert _inc.nets["S_HV"].attrs == {"class": "hv"}
    assert ("S_HV", 0) in [(c.get("net"), c.get("layer")) for c in _inc.constraints
                           if isinstance(c, dict) and c.get("t") == "pour"]
    assert agent.dumps(agent.loads(agent.dumps(_inc), base=_td)) == agent.dumps(_inc)
import shutil
shutil.rmtree(os.path.join(EX, "tmp_inc"))

# CLI builds the committed file (exit 0 = DRC clean)
proc = subprocess.run([sys.executable, "-m", "apps.ocd",
                       os.path.join(EX, "blinky_555.ocd")],
                      capture_output=True, text=True, cwd=os.path.join(HERE, ".."))
assert proc.returncode == 0, proc.stdout + proc.stderr

# solver frames stream (animation API)
pf: list[dict[str, object]] = []
rf: list[dict[str, object]] = []
bo.place(seeds=1, iters=30, frames=pf, every=10)
bo.route_board(frames=rf)
assert len(pf) >= 2 and "pos" in pf[-1]
assert len(rf) >= 1 and "segs" in rf[0]
# power constraints widen routed copper (blinky: VCC/GND 0.5 vs 0.3 signal)
assert {s.width for s in bo.traces if s.net in ("VCC", "GND")} == {0.5}
assert {s.width for s in bo.traces if s.net not in ("VCC", "GND")} == {0.3}
# route constraint forces the layer (blinky: GND stays on 1)
assert {s.layer for s in bo.traces if s.net == "GND"} == {1}
# trace constraint sets routed width (same funnel as power)
_tw = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\ntrace N 0.6\n", base=EX)
_tw.place(seeds=1, iters=30)
_tw.route_board("lroute")
assert {s.width for s in _tw.traces if s.net == "N"} == {0.6}
assert {s.width for s in _tw.traces if s.net == "GND"} == {0.3}

# JSON wire IR round-trips
bj = agent.from_json(agent.to_json(bo))
assert {p.ref for p in bj.parts.values()} == set(bo.parts)
assert agent.to_json(bj).startswith("{")
# JSON IR preserves part + net attrs (dnp/class drive fab/widths — silent loss breaks builds)
_ja = agent.loads("board t 40x30 2L\npart R1 R0805 10k dnp=1 lcsc=C1\npart C1 C0805 100n\n"
                  "HV class=highvolt :: R1.1 C1.1\nLV :: R1.2 C1.2\nclass highvolt width=0.8\n", base=EX)
assert agent.dumps(agent.from_json(agent.to_json(_ja))) == agent.dumps(_ja)
assert cast(str, bj.render("svg")).startswith("<svg")
assert cast(str, bj.render("stl")).startswith("solid")
assert cast(bytes, bj.render("png"))[:8] == b"\x89PNG\r\n\x1a\n"
# pours render: flooded PNG differs from bare, studio state carries planes
_pb = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.1 C1.2\nnet GND: R1.2 C1.1\npour GND on 0\n", base=EX)
_pb.place(seeds=1, iters=30)
_pb.route_board()
_bare = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                    "net N: R1.1 C1.2\nnet GND: R1.2 C1.1\n", base=EX)
_bare.place(seeds=1, iters=30)
_bare.route_board()
assert cast(bytes, _pb.render("png")) != cast(bytes, _bare.render("png"))
# plane flood insets by fab edge clearance (0.3), in Gerber + KiCad + state
_pgd = tempfile.mkdtemp()
_pgt = open([f for f in _pb.export("jlc", outdir=_pgd) if f.endswith(".GTL.gbr")][0]).read()
assert "X0.3000Y0.3000D02*" in _pgt, _pgt[:200]
_pkp = open([f for f in _pb.export("kicad", outdir=_pgd) if f.endswith(".kicad_pcb")][0]).read()
assert "(xy 0.3000 0.3000)" in _pkp
# stranded pour pad: keepout cuts the plane AND routers skip poured nets
_ps = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.1 C1.2\nnet GND: R1.2 C1.1\npour GND on 0\n"
                  "fix R1 at 20 15\nfix C1 at 30 15\nkeepout 20 15 6x6\n", base=EX)
_ps.place(seeds=1, iters=30)
_ps.route_board()
assert any("pour-isolated GND R1.2" in e for e in cast(list[str], _ps.check()["errors"]))
assert "<canvas" in cast(str, bj.render("html3d"))
_sch = cast(str, bj.render("sch"))
assert _sch.startswith("<svg") and "GND" in _sch and "U1" in _sch
assert _sch.count("<circle") >= sum(len(n.pins) for n in bj.nets.values())
_ez = cast(str, bj.render("easyeda"))
assert _ez.startswith("<svg") and "U1" in _ez and "#FFFF00" in _ez
_ra = bj.render_all(tempfile.mkdtemp())
assert len(_ra) == len(bj.plugins().list("renderer")) - 1  # "all" excluded
assert any(f.endswith(".easyeda.svg") for f in _ra)
# assembly drawing X's out DNP parts (hand-assembly: do not place)
_asm = agent.loads("board t 40x30 2L\npart R1 R0805 10k dnp=1\npart C1 C0805 100n\n"
                   "N :: R1.1 C1.1\nGND :: R1.2 C1.2\n", base=EX)
_asm.place(seeds=1, iters=20)
_asvg = cast(str, _asm.render("assembly"))
assert _asvg.count("stroke-dasharray") == 1 and "<line" in _asvg, _asvg[:300]
_allr = cast(list[str], bj.render("all", outdir=tempfile.mkdtemp(), keys=["svg", "png"]))
assert sorted(f.split(".")[-1] for f in _allr) == ["png", "svg"]
import shutil as _sh2
if _sh2.which("kicad-cli") is not None:
    assert cast(bytes, bj.render("kicad"))[:8] == b"\x89PNG\r\n\x1a\n"
if _sh2.which("pcbdraw") is not None:
    assert cast(str, bj.render("pcbdraw")).startswith("<")

# fab profiles: oshpark is stricter than jlc on drills; jlc-flex is ENIG-only FPC
from ocdcircuit import fab
assert fab.get("oshpark")["min_drill"] == 0.508
assert fab.get("jlc-flex")["layers"] == (1, 2, 4)
assert fab.get("jlc-flex")["finishes"] == ("ENIG",)
# all eleven profiles load and run DRC through the generic drc:fab path
assert len(fab.list_fabs()) == 11, fab.list_fabs()
_bfab = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                    "net N: R1.1 C1.2\nnet GND: R1.2 C1.1\n", base=EX)
_bfab.place(seeds=1, iters=30)
for _ff in fab.list_fabs():
    _fr = _bfab.check("fab", fab=_ff)
    assert isinstance(_fr["errors"], list) and _fr["fab"] == _ff, _ff
assert _bfab.check("fab", fab="eurocircuits")["errors"] == []
# flex: bend/stiffener round-trip, DRC, no maze vias in dynamic bends
_fb = agent.loads("board f 60x20 2L\npart J1 PINHD4\npart U1 SOIC8 X\n"
                  "net A: J1.1 U1.1\nnet B: J1.2 U1.2\n"
                  "bend 30 10 6x20 r5\nstiffener 5 10 10x12 FR4 0.4\n"
                  "fix J1 at 8 10\nfix U1 at 50 10\n", base=EX)
assert agent.dumps(agent.loads(agent.dumps(_fb), base=EX)) == agent.dumps(_fb)
# save-after-solve keeps route intent: constraints beat runtime assignment
# (assign_layers may park GND on L0; the route line must survive dumps)
_rb = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "VCC :: R1.1\nGND :: R1.2 C1.1 C1.2\nroute GND on 1\n", base=EX)
_rb.place(seeds=1, iters=20)
_rb.route_board()
_rt = agent.dumps(_rb)
assert "GND L1" in _rt, _rt
_rb2 = agent.loads(_rt, base=EX)
assert all(c["layer"] == 1 for c in _rb2.constraints
           if c.get("t") == "layer" and c.get("net") == "GND")
assert agent.dumps(_rb2) == _rt  # fixpoint
_fb.fab = "jlc-flex"
_fb.place(seeds=1, iters=30)
_fb.route_board("maze")
assert _fb.check("jlc-flex")["errors"] == [], _fb.check("jlc-flex")["errors"]
assert not [t for t in _fb.traces if getattr(t, "via", False) and 27 <= t.x1 <= 33]
_tb = agent.loads("board t 40x30\npart R1 R0805 1k\nbend 20 15 10x10 r1\nfix R1 at 20 15\n", base=EX)
errs = cast(list[str], _tb.check("jlc-flex")["errors"])
assert any(e.startswith("bend-part") for e in errs) and any(e.startswith("bend-radius") for e in errs)
# planes crack in dynamic bends: pour + dynamic bend errors (static is fine)
_bp = agent.loads("board t 60x20 2L\npart J1 PINHD4\npart U1 SOIC8 X\n"
                  "net A: J1.1 U1.1\nnet B: J1.2 U1.2\npour A on 0\n"
                  "bend 30 10 6x20 r5\nfix J1 at 8 10\nfix U1 at 50 10\n", base=EX)
assert any(e.startswith("bend-pour A") for e in cast(list[str], _bp.check()["errors"]))
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
# placer honors keepouts: cost cliff steers seed selection (no dynamics
# push — that fights packing on dense boards; measured +4..6 overlaps)
from ocdcircuit import solver as _sv
from ocdcircuit.drc import in_zone as _inzone
_kp = agent.loads("board t 40x30 2L\npart R1 R0805 1k\npart C1 C0805 100n\n"
                  "net N :: R1.1 <--> C1.1\nkeepout 20 15 d10\n", base=EX)
_kp.place(seeds=4, iters=200)
_zs = _sv._keepouts(_kp)
assert not [r for r, p in _kp.parts.items()
            if any(_inzone(z, p.x, p.y, (p.wh()[0] / 2, p.wh()[1] / 2)) for z in _zs)]
assert _sv._keepout_cost(_kp) == 0.0
# cutout blocks maze routing; hole lands in the Excellon drill file
_ch = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "fix R1 at 5 5\nfix C1 at 35 25\n"
                  "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n"
                  "cutout 20 15 6x6\nhole 30 8 2\n", base=EX)
_ch.place(seeds=1, iters=30)
_ch.route_board("maze")
assert _ch.check()["errors"] == [], _ch.check()["errors"]
assert not [s for s in _ch.traces
            if 17 <= (s.x1 + s.x2) / 2 <= 23 and 12 <= (s.y1 + s.y2) / 2 <= 18]
_drl = open([f for f in _ch.export("jlc", outdir=tempfile.mkdtemp())
             if f.endswith(".TXT")][0]).read()
assert "X30.000Y8.000" in _drl, _drl
_gko = open([f for f in _ch.export("jlc", outdir=tempfile.mkdtemp())
             if f.endswith(".GKO.gbr")][0]).read()
assert "X17.0000" in _gko and "X23.0000" in _gko, _gko
# footprint keepouts ride the part: .fp keepout lines, kicad_mod zones +
# courtyard art all parse; maze/DRC/export follow through placement+rot
from ocdcircuit import footprint as _fp0, foreign as _frn
_fpk, _fpm = _fp0.loads("footprint K1 4x4\npad 1 -1 0 1 1\npad 2 1 0 1 1\n"
                        "keepout 0 3 4x2\nkeepout 0 -3 d2\n")
assert _fpk == "K1" and len(cast(list[object], _fpm["keepouts"])) == 2
_kn, _kfp = _frn.kicad_mod(
    open(os.path.join(EX, "bme690", "fp", "PinHeader_1x07_P2.54mm_Vertical.kicad_mod")).read())
assert _kfp["w"] == 4.54 and _kfp["h"] == 19.8  # courtyard, not pad bbox
_mn, _mfp = _frn.kicad_mod(
    open(os.path.join(EX, "breath_ketone", "fp", "Raytac_MDBT50Q.kicad_mod")).read())
assert len(cast(list[object], _mfp["keepouts"])) == 2  # antenna zones
_kb = agent.loads("board t 40x30 2L\npart R1 R0805 10k\n"
                  "fix R1 at 5 5\nnet GND: R1.1\n", base=EX)
_kb.add_footprint("K1X", {"w": 4.0, "h": 4.0,
                          "pads": {"1": (-1.0, 0.0, 1.0, 1.0), "2": (1.0, 0.0, 1.0, 1.0)},
                          "keepouts": [{"dx": 0.0, "dy": 5.0, "w": 6.0, "h": 4.0, "layers": []}]})
# lib cache: merged once, invalidated by add_footprint (do + undo)
_lib0 = _kb._lib()
assert _kb._lib() is _lib0  # same object, no re-merge
_kb.add_footprint("K2X", {"w": 2.0, "h": 2.0, "pads": {}, "keepouts": []})
assert _kb._lib() is not _lib0 and "K2X" in _kb._lib()
_kb.ctx.undo()  # undo the K2X registration
assert "K2X" not in _kb._lib()
_kb.add_part("K1", "K1X", "", 20, 15)
_kb.constrain({"t": "fixed", "ref": "K1", "x": 20, "y": 15})
_kb.connect("N", "K1", "1")
_kb.connect("N", "R1", "1")
_kb.connect("GND", "K1", "2")
_kb.connect("GND", "R1", "2")
_kb.place(seeds=1, iters=30)
_kb.route_board("maze")
assert _kb.check()["errors"] == [], _kb.check()["errors"]
assert not [s for s in _kb.traces  # nothing routes through the K1 north zone
            if 17 <= (s.x1 + s.x2) / 2 <= 23 and 18 <= (s.y1 + s.y2) / 2 <= 22]
from ocdcircuit.drc import fp_keepouts as _fk, in_zone as _iz
assert len(_fk(_kb, "K1")) == 1 and _fk(_kb, "K1")[0]["x"] == 20.0
assert _iz(_fk(_kb, "K1")[0], 20, 20) and not _iz(_fk(_kb, "K1")[0], 20, 10)
_kb.move_part("K1", 10, 10)
assert _fk(_kb, "K1")[0]["x"] == 10.0  # zone follows the part
_kb.parts["K1"].attrs["rot"] = "90"
_rz = _fk(_kb, "K1")[0]  # offset (0,5)->(-5,0), w/h swap 6x4->4x6
assert (_rz["x"], _rz["y"], _rz["w"], _rz["h"]) == (5.0, 10.0, 4.0, 6.0)
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
# drc:all merges siblings with key prefixes; keys= subsets
_all = bo.check("all")
assert set(cast(list[str], _all["ran"])) >= {"fab", "erc"}
assert all(":" in str(e) for e in cast(list[str], _all["errors"]) + cast(list[str], _all["warnings"]))
_sub = bo.check("all", keys=["erc"])
assert cast(list[str], _sub["ran"]) == ["erc"]
assert bo.check_all()["ran"] == cast(list[str], _all["ran"])
# config:toml applies board.toml, missing file → {}
with tempfile.TemporaryDirectory() as _td:
    open(os.path.join(_td, "board.toml"), "w").write(
        'fab = "oshpark"\nplacer = "compact"\ndrc = ["erc"]\nmask = "blue"\n')
    _tc = agent.loads(ocd, base=EX)
    assert _tc.configure("toml", base=_td) == {
        "fab": "oshpark", "placer": "compact", "drc": ["erc"], "mask": "blue"}
    assert _tc.fab == "oshpark" and _tc.meta["mask"] == "blue"
    assert _tc.proj["drc"] == ["erc"]
    assert _tc.configure("toml", base=EX) == {}
    open(os.path.join(_td, "board.toml"), "w").write('placer = "compact"\n')
    assert _tc.configure("toml", base=_td) == {"placer": "compact"}  # retry works
    # config values are validated at load, not at solve: typos fail here,
    # unfenced (ValueError = fixable input — retry works without re-arm)
    for _bad_toml, _frag in [
        ('placer = "difusion"\n', "unknown placer"),
        ('router = "maz"\n', "unknown router"),
        ('fab = "acme"\n', "unknown fab"),
        ('drc = ["nope"]\n', "unknown drc"),
    ]:
        open(os.path.join(_td, "board.toml"), "w").write(_bad_toml)
        try:
            _tc.configure("toml", base=_td)
            raise AssertionError(f"should have raised: {_bad_toml!r}")
        except ValueError as e:
            assert _frag in str(e), str(e)
    open(os.path.join(_td, "board.toml"), "w").write('placer = "compact"\n')
    assert _tc.configure("toml", base=_td) == {"placer": "compact"}  # retry works

# layer/width are runtime caches: removing the constraint releases them
# on next route (no stale assignment)
_al = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "N :: R1.1 C1.2\nroute N on 1\n", base=EX)
_al.place(seeds=1, iters=10)
_al.route_board()
assert _al.nets["N"].layer == 1
_al.unconstrain({"t": "layer", "net": "N", "layer": 1})
_al.route_board()
assert _al.nets["N"].layer == 0, _al.nets["N"].layer
_aw = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n"
                  "power N\n", base=EX)
_aw.route_board()
assert _aw.nets["N"].width == 0.5
_aw.unconstrain({"t": "power", "nets": ["N"]})
_aw.route_board()
assert _aw.nets["N"].width == 0.3, _aw.nets["N"].width

# mix-and-match: every placer × every router × every silk resolves + runs
for pl in ["diffusion", "compact", "thermal"]:
    for rt in ["lroute", "maze", "coarse", "wiremask"]:
        bm = agent.loads(ocd, base=EX)
        bm.place(pl, seeds=2, iters=100)
        bm.route_board(rt, **({"pop": 2, "gen": 1} if rt == "wiremask" else {}))
        assert not cast(list[str], bm.check()["errors"]), (pl, rt)
# loads-only farm: every committed board parses (grammar regressions
# surface here, not in the slow full-solve farm)
import glob as _glob
_farm = sorted(_glob.glob(os.path.join(EX, "*.ocd"))
               + _glob.glob(os.path.join(EX, "*", "*.ocd")))
_farm = [f for f in _farm if "/out/" not in f]
assert len(_farm) >= 8, _farm
for _ff2 in _farm:
    _bf2 = agent.loads(open(_ff2).read(), base=os.path.dirname(_ff2))
    assert _bf2.parts, _ff2
# route-grid grammar: parses, dumps round-trips, maze honors it
_bg = agent.loads("board t 20x10\npart R1 R0805 1k\nN :: R1.1 R1.2\nroute-grid 0.2\n", base=EX)
assert _bg.constraints[-1] == {"t": "route-grid", "grid": 0.2}
assert agent.dumps(agent.loads(agent.dumps(_bg), base=EX)) == agent.dumps(_bg)
assert agent.parse_constraint("route-grid 0.2") == {"t": "route-grid", "grid": 0.2}
# route-penalty grammar: parses, round-trips, maze pricing follows it
_bp = agent.loads("board t 20x10 2L\npart R1 R0805 1k x=3 y=5\npart C1 C0805 100n x=17 y=5\n"
                  "N :: R1.1 C1.2\nroute-penalty bend 3 via 20\n", base=EX)
assert _bp.constraints[-1] == {"t": "route-penalty", "bend": 3.0, "via": 20.0}
assert agent.dumps(agent.loads(agent.dumps(_bp), base=EX)) == agent.dumps(_bp)
from ocdcircuit import maze as _mz
assert _mz._constraints(_bp) == {"grid": 0.25, "bend": 3.0, "via": 20.0}
_bp.place(seeds=1, iters=50)
_bp.route_board("maze")
assert _bp.check()["errors"] == [], _bp.check()["errors"]
# wiremask evals must not pollute undo (pop*gen phantom entries); final
# maze legitimately emits 2 (layer assignment + route). Coarse emits 2
# (route-grid constrain + maze) for the same reason: real effects, not phantoms.
_bw = agent.loads(ocd, base=EX)
_bw.place(seeds=2, iters=100)
_snap = _bw.ctx.snapshot()
_bw.route_board("wiremask", pop=2, gen=1)
assert _bw.ctx.snapshot() - _snap == 2, "wiremask undo pollution"
# wiremask mid-eval exception restores layer assignment (evals write
# net.layer directly, invisible to undo — finally must cover them)
_bx = agent.loads("board t 60x40 2L\nblock ch\npart R R0805 10k\npart C C0805 100n\n"
                  "net RC: R.1 C.1\nend\ninstance ch as A\ninstance ch as B join GND\n"
                  "net X: A_R.1 B_R.1\nnet GND: A_R.2 B_R.2\n", base=EX)
_bx.place(seeds=1, iters=10)
_bx.route_board("lroute")
_lay0 = {n: (net.layer, net.width) for n, net in _bx.nets.items()}
from ocdcircuit import maze as _mzx
_orig_maze = _mzx.maze
_calls = {"n": 0}
from ocdcircuit.circuit import Board as _Board
from ocdcircuit.types import Frame as _Frame


def _boom_maze(board: _Board, frames: list[_Frame] | None = None) -> int:
    _calls["n"] += 1
    if _calls["n"] == 3:
        raise RuntimeError("synthetic maze failure")
    return _orig_maze(board, frames)


_mzx.maze = _boom_maze
try:
    _bx.route_board("wiremask", pop=3, gen=2)
    raise AssertionError("should have raised")
except RuntimeError:
    pass
finally:
    _mzx.maze = _orig_maze
assert {n: (net.layer, net.width) for n, net in _bx.nets.items()} == _lay0
# thermal spreads big bodies: min pairwise separation beats diffusion's
import itertools as _it
_sep = {}
for pl in ["diffusion", "thermal"]:
    _tb = agent.loads(open(os.path.join(EX, "pico_tmc2209", "pico_tmc2209.ocd")).read(),
                      base=os.path.join(EX, "pico_tmc2209"))
    _tb.place(pl, seeds=4, iters=400)
    _th_big = sorted(_tb.parts.values(), key=lambda p: p.wh()[0] * p.wh()[1], reverse=True)[:5]
    _ds = [((a.x - c.x) ** 2 + (a.y - c.y) ** 2) ** 0.5 for a, c in _it.combinations(_th_big, 2)]
    _sep[pl] = min(_ds)
assert _sep["thermal"] > _sep["diffusion"], _sep
# repair clears edge violations too (e2e J2 sat exactly on the rim at seed 0)
_be = agent.loads(open(os.path.join(EX, "e2e_driver4", "e2e_driver4.ocd")).read(),
                  base=os.path.join(EX, "e2e_driver4"))
_be.place(seeds=2, iters=100)
assert _be.check()["errors"] == [], _be.check()["errors"]
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
b.constrain(cast(Constraint, {"t": "power", "nets": ["VCC", "GND"]}))
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
    import re as _re
    _gtl = open([f for f in files if f.endswith(".GTL.gbr")][0]).read()
    _codes = _re.findall(r"%ADD(\d+)[A-Z]", _gtl)
    assert len(_codes) == len(set(_codes)), "dup Gerber D-codes"  # power/signal widths
    assert "%ADD11C,0.500" in _gtl  # 0.5 power traces keep their aperture
    _gtp = open([f for f in files if f.endswith(".GTP.gbr")][0]).read()
    assert _gtp.count("D03*") > 0  # paste covers SMD pads (never starved)
    _pm = [_re.search(r"%ADD1\dC,([\d.]+)", l) for l in _gtp.splitlines()
           if l.startswith("%ADD11")]
    assert any(m and float(m.group(1)) > 0.4 for m in _pm)  # sized, not blind
    _gts = open([f for f in files if f.endswith(".GTS.gbr")][0]).read()
    assert _gts.count("D03*") > 0  # mask openings over pads (never empty)
    _mc = _re.findall(r"%ADD(\d+)[A-Z]", _gts)
    assert len(_mc) == len(set(_mc)), "dup mask D-codes"
    _mm = [_re.search(r"%ADD\d+C,([\d.]+)", l) for l in _gts.splitlines()
           if l.startswith("%ADD11")]
    assert any(m and float(m.group(1)) > 0.5 for m in _mm)  # sized to pads
    _gto = open([f for f in files if f.endswith(".GTO.gbr")][0]).read()
    assert _gto.count("D01*") > 0  # silk outlines (never empty)
    import zipfile as _zf
    _zb = b.export("bundle", outdir=d)[0]
    assert _zb.endswith("-fab.zip")
    _zn = _zf.ZipFile(_zb).namelist()
    assert any(n.endswith(".GTL.gbr") for n in _zn) and any(n.endswith(".CPL.csv") for n in _zn)
    assert any(n.endswith(".kicad_sch") for n in _zn) and any(n.endswith(".brd") for n in _zn)
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
    # part rotations reach the CPL (bme690 J3/J4 carry rot=180 upstream)
    _bb = agent.loads(open(os.path.join(EX, "bme690", "bme690_carrier.ocd")).read(),
                      base=os.path.join(EX, "bme690"))
    _bb.place(seeds=1, iters=30)
    _cpl = open([f for f in _bb.export("jlc", outdir=tempfile.mkdtemp())
                 if f.endswith(".CPL.csv")][0]).read()
    assert "J3," in _cpl and ",180" in _cpl, _cpl
    # BOM groups by (value, fp) with LCSC (ne555: 21× R0603 share C21190)
    _nb = agent.loads(open(os.path.join(EX, "ne555", "ne555_discrete.ocd")).read(),
                      base=os.path.join(EX, "ne555"))
    _bom = open([f for f in _nb.export("jlc", outdir=tempfile.mkdtemp())
                 if f.endswith(".BOM.csv")][0]).read()
    assert '"J11' in _bom and _bom.count("C21190") == 1, _bom
    # net class + DNP: class width floor routes copper, clearance gates DRC,
    # DNP splits the BOM row and exempts ERC pins
    _cb = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart R2 R0805 10k dnp=1\n"
                      "part C1 C0805 100n\nHV class=highvolt :: R1.1 C1.1\n"
                      "LV :: R1.2 C1.2\nclass highvolt width=0.8 clearance=0.5\n", base=EX)
    assert _cb.nets["HV"].attrs == {"class": "highvolt"}
    assert agent.dumps(agent.loads(agent.dumps(_cb), base=EX)) == agent.dumps(_cb)
    _cb.place(seeds=1, iters=30)
    _cb.route_board()
    assert _cb.nets["HV"].width == 0.8 and _cb.nets["LV"].width == 0.3
    assert _cb.check("erc")["errors"] == [], _cb.check("erc")["errors"]
    # custom power rails shorted at a pin flag like AUTO_JOIN rails do
    _ps = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                      "net VBUS: R1.1 C1.1\nnet VSYS: R1.1 C1.2\npower VBUS VSYS\n", base=EX)
    assert any("power-short VSYS/VBUS at R1.1" in e
               for e in cast(list[str], _ps.check("erc")["errors"]))
    _cbom = open([f for f in _cb.export("jlc", outdir=tempfile.mkdtemp())
                  if f.endswith(".BOM.csv")][0]).read()
    assert "10k (DNP),\"R2\"" in _cbom and _cbom.count("10k") == 2, _cbom
    _ccpl = open([f for f in _cb.export("jlc", outdir=tempfile.mkdtemp())
                  if f.endswith(".CPL.csv")][0]).read()
    assert "R2," not in _ccpl and "R1," in _ccpl  # DNP never reaches PnP
    # same value+fp with different LCSC never merges (JLC orders per row)
    _lc = agent.loads("board t 40x30 2L\npart R1 R0805 10k lcsc=C1\n"
                      "part R2 R0805 10k lcsc=C2\nnet N: R1.1 R2.1\nnet GND: R1.2 R2.2\n", base=EX)
    _lbom = open([f for f in _lc.export("jlc", outdir=tempfile.mkdtemp())
                  if f.endswith(".BOM.csv")][0]).read()
    assert ",C1" in _lbom and ",C2" in _lbom and _lbom.count("10k") == 2, _lbom
    from ocdcircuit.circuit import Seg as _Seg
    _cb.traces = [_Seg("HV", 5, 5, 15, 5, 0, 0.3), _Seg("LV", 5, 5.3, 15, 5.3, 0, 0.3)]
    assert any("clearance HV-LV" in w for w in  # 0.3mm gap < class 0.5
               cast(list[str], _cb.check()["warnings"]))
    # X-crossings short: caught; distant via-points are not crossings
    from ocdcircuit.drc import _seg_dist as _sd
    assert _sd((0, 0, 10, 10), (0, 10, 10, 0)) == 0.0
    assert _sd((20, 9.5, 20, 9.5), (14.5, 16, 14.5, 16)) > 8.0
    _xx = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                      "A :: R1.1 R1.2\nB :: C1.1 C1.2\n", base=EX)
    _xx.place(seeds=1, iters=20)
    _xx.traces = [_Seg("A", 5, 15, 35, 15, 0, 0.3), _Seg("B", 20, 5, 20, 25, 0, 0.3)]
    assert any("clearance A-B" in w for w in cast(list[str], _xx.check()["warnings"]))
    kc = open([f for f in files if f.endswith(".kicad_pcb")][0]).read()
    assert kc.startswith("(kicad_pcb") and "(segment" in kc and "(footprint" in kc
    assert '(net 0 "")' in kc  # KiCad requires the unconnected net declared
    # DNP parts carry (attr dnp) in KiCad too (excluded from BOM/PnP there)
    _kdd = agent.loads("board t 40x30 2L\npart R1 R0805 10k dnp=1\npart C1 C0805 100n\n"
                       "N :: R1.1 C1.1\nGND :: R1.2 C1.2\n", base=EX)
    _kdd.place(seeds=1, iters=20)
    _kdd.route_board()
    _kk = open([f for f in _kdd.export("kicad", outdir=tempfile.mkdtemp())
                if f.endswith(".kicad_pcb")][0]).read()
    assert _kk.count("(attr dnp)") == 1, _kk.count("(attr dnp)")
    # net classes reach KiCad (net_class with clearance/width + member nets)
    _kc = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                      "HV class=highvolt :: R1.1 C1.1\nLV :: R1.2 C1.2\n"
                      "class highvolt width=0.8 clearance=0.5\n", base=EX)
    _kc.place(seeds=1, iters=20)
    _kc.route_board()
    _kk2 = open([f for f in _kc.export("kicad", outdir=tempfile.mkdtemp())
                 if f.endswith(".kicad_pcb")][0]).read()
    assert '(net_class "highvolt"' in _kk2 and '(add_net "HV")' in _kk2, _kk2[-500:]

# MCP stdio server: initialize → list → load → solve → patch → check
import json as _json
mcp = subprocess.Popen([sys.executable, "-m", "apps.mcp"],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       cwd=os.path.join(HERE, ".."))
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
assert len(cast(list[object], cast(dict[str, object], _rpc("tools/list")["result"])["tools"])) == 27
assert len(cast(list[object], _call("footprints", {})["footprints"])) >= 100
assert all(f["name"] == "R0805" for f in cast(list[dict[str, object]],
           _call("footprints", {"q": "R0805"})["footprints"]))
assert len(cast(dict[str, object], _call("fabs", {})["fabs"])) == 11
assert _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})["parts"] == 10
assert cast(float, _call("place", {})["cost"]) >= 0  # every tool invoked live
assert cast(int, _call("route", {})["segments"]) >= 0
assert _call("use_plugin", {"kind": "placer", "key": "compact"}) == {"active": "compact"}
assert _call("use_plugin", {"kind": "placer", "key": "diffusion"}) == {"active": "diffusion"}
with tempfile.TemporaryDirectory() as _md:
    assert len(cast(list[object], _call("export", {"key": "jlc", "outdir": _md})["files"])) >= 10
assert len(cast(str, _call("render", {"key": "svg"})["data"])) > 1000
# malformed stdio frames don't kill the server: garbage header bytes,
# bogus length, and non-object bodies are dropped; server keeps answering
assert mcp.stdin is not None and mcp.stdout is not None
mcp.stdin.write(b"\xff\xfe bad\r\n\r\n")
mcp.stdin.write(b"Content-Length: bogus\r\n\r\n")
mcp.stdin.flush()
_badbody = _json.dumps([1, 2]).encode()
mcp.stdin.write(f"Content-Length: {len(_badbody)}\r\n\r\n".encode() + _badbody)
mcp.stdin.flush()
_midr = _mid[0] + 1
_head = b""
while not _head.endswith(b"\r\n\r\n"):
    _head += mcp.stdout.read(1)
_n = int([ln for ln in _head.decode().split("\r\n")
          if ln.lower().startswith("content-length:")][0].split(":")[1])
assert _json.loads(mcp.stdout.read(_n))["error"]["code"] == -32700
assert _call("lint", {})["errors"] == []  # still alive
# hung subprocesses time out clean (in-process: mock the run call —
# the MCP server is a separate process, mocks don't cross it)
import subprocess as _sp2
from unittest import mock as _mock
_tbto = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", base=EX)
with _mock.patch("subprocess.run", side_effect=_sp2.TimeoutExpired("kicad-cli", 300)):
    try:
        _tbto.render("kicad")
        raise AssertionError("should have raised")
    except _sp2.TimeoutExpired:
        pass
    # render_all skips the timed-out renderer, keeps the rest
    _rl = _tbto.render_all(tempfile.mkdtemp(), keys=["svg", "kicad"])
    assert any(f.endswith(".svg") for f in _rl) and not any("kicad" in f for f in _rl)
assert _call("apply_patch", {"ops": [{"op": "constrain",
        "c": {"t": "near", "a": "U1", "b": "R1", "w": 1}}]})["applied"] == 1
assert _call("undo", {})["undone"] == 1  # patch reverted, board intact
# malformed patch returns a clean error, not a 500 (asserts are fixable input)
_badp = _call("apply_patch", {"ops": [{"op": "add_part", "ref": "RX",
                                       "fp": "R0805", "attrs": "nope"}]})
assert _badp["applied"] == 0 and "error" in _badp, _badp
assert _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})["parts"] == 10
assert _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})["proj"] == {}
assert _call("get_state", {})["proj"] == {}
# large payloads survive stdio framing: 300KB monster board loads intact
_mtext = open(os.path.join(EX, "..", "benches", "monster6502",
                           "monster6502.ocd")).read()
_mload = _call("load_board", {"text": _mtext, "base": os.path.join(
    EX, "..", "benches", "monster6502")})
assert _mload["parts"] == 5420 and _mload["nets"] == 9493, _mload
assert _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})["parts"] == 10
assert len(cast(list[object], _call("context", {})["fibers"])) >= 0  # fiber ledger
assert _call("context", {"op": "get", "key": "plugins"})["value"] is not None
assert _call("lint", {})["errors"] == []
assert _call("doctor", {})["ok"] is True
assert _call("import_footprint", {"key": "fp",
    "path": os.path.join(EX, "usb_c_edge.fp")})["name"] == "USB_C_EDGE_GCT"
assert "coverage" in _call("score", {})
assert cast(float, _call("score", {"tidy": False})["total"]) >= 0
_ext = cast(dict[str, object], _call("score", {"tidy": False})["extent"])
assert 0 < cast(float, _ext["fill"]) <= 1.0, _ext
_sh = cast(list[float], _ext["shrink"])
assert len(_sh) == 2 and all(v > 0 for v in _sh), _ext  # shrink suggestion rides extent
assert _call("diff", {"text": open(os.path.join(EX, "blinky_555.ocd")).read(),
                       "base": EX}) == {"diff": ""}
from ocdcircuit import diff as _diffmod
_da = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", base=EX)
_db = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.1 R1.2\nnet GND: R1.2 C1.2\npour GND on 0\nroute-grid 0.2\n", base=EX)
_dd = _diffmod.diff(_da, _db)
assert "+ part C1" in _dd and "+ pour" in _dd and "route-grid" in _dd, _dd
solved = _call("solve", {"placer": "compact", "router": "maze"})
assert solved["errors"] == [] and solved["warnings"] == [], solved
assert solved["placer"] == "compact" and solved["router"] == "maze", solved
_dsolve = _call("solve", {})
assert _dsolve["placer"] == "diffusion" and _dsolve["router"] == "lroute", _dsolve
# MCP honors board.toml picks (load configures, tools fall back to proj)
with tempfile.TemporaryDirectory() as _md2:
    shutil.copy(os.path.join(EX, "psu.ocd"), os.path.join(_md2, "psu.ocd"))
    open(os.path.join(_md2, "board.toml"), "w").write('placer = "compact"\nrouter = "maze"\n')
    assert _call("load_board", {"path": os.path.join(_md2, "psu.ocd")})["parts"] == 3
    _psolve = _call("solve", {})
    assert _psolve["placer"] == "compact" and _psolve["router"] == "maze", _psolve
    assert _psolve["errors"] == [], _psolve
    assert _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})["parts"] == 10
_g = _call("candidates", {"n": 2, "seed": 3, "seeds": 1, "iters": 30})
_gc = cast(list[object], _g["candidates"])
_gf = cast(dict[str, dict[str, object]], _g["feasible"])
assert len(_gc) == 2 and "feasible" in _g, _g
assert _gf["2"]["ok"] is True, _gf
assert _call("apply_candidate", {"index": 0, "n": 2, "seed": 3,
                                 "iters": 30})["applied"] is True
assert _call("apply_candidate", {"index": 9, "n": 2, "seed": 3,
                                 "iters": 30})["applied"] is False
assert cast(dict[str, dict[str, object]],
            _call("feasible", {})["feasible"])["2"]["ok"] is True
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
_ssa = _call("set_state", {"parts": {"QX": {"fp": "R0805", "value": "1k",
                                            "attrs": {"lcsc": "C9"}}},
                           "nets": {"QN": {"pins": ["QX.1", "QX.2"],
                                            "attrs": {"class": "hv"}}},
                           "constraints": []})
assert cast(dict[str, object], _ssa["applied"])["updated"] == 1, _ssa
_sta = _call("get_state", {})
_ap = [p for p in cast(list[dict[str, object]],
       cast(dict[str, object], _sta["ir"])["parts"]) if p["ref"] == "QX"][0]
assert _ap["attrs"] == {"lcsc": "C9"}, _ap  # attrs survive IR round-trip
_st = _call("get_state", {})
assert any(p["ref"] == "QX" for p in cast(list[dict[str, object]],
           cast(dict[str, object], _st["ir"])["parts"]))  # QX survived rollback
assert _call("parse_constraint", {"text": "keep U1 near C1"})["constraint"] == {
    "t": "near", "a": "U1", "b": "C1", "w": 2.0}
assert _call("check", {})["errors"] == []
# per-call fab override (one-shot, not persisted — CLI --fab semantics)
assert _call("check", {"fab": "eurocircuits"})["fab"] == "eurocircuits"
_rfab = _rpc("tools/call", {"name": "check", "arguments": {"fab": "acme"}})
assert "unknown fab" in str(_rfab.get("error")), _rfab
# Board.fab validates on every assignment (typo fails here, not deep in DRC)
from ocdcircuit.circuit import Board as _Board2
_bb2 = _Board2("t", 40, 30)
try:
    _bb2.fab = "jcl"
    raise AssertionError("should have raised")
except ValueError as e:
    assert "unknown fab" in str(e), str(e)
assert _bb2.fab == "jlc"  # failed set doesn't stick
_bb2.fab = "oshpark"
assert _bb2.fab == "oshpark"
# Board.name validates too (export filenames derive from it — no traversal)
try:
    _bb2.name = "../evil"
    raise AssertionError("should have raised")
except ValueError as e:
    assert "bad board name" in str(e), str(e)
assert _bb2.name == "t"  # failed set doesn't stick (name untouched)
# check-all falls back to board.toml drc picks when keys omitted
with tempfile.TemporaryDirectory() as _md3:
    shutil.copy(os.path.join(EX, "psu.ocd"), os.path.join(_md3, "psu.ocd"))
    open(os.path.join(_md3, "board.toml"), "w").write('drc = ["erc"]\n')
    _call("load_board", {"path": os.path.join(_md3, "psu.ocd")})
    assert _call("check", {"key": "all"})["ran"] == ["erc"]
    assert _call("check", {"key": "all", "keys": ["fab"]})["ran"] == ["fab"]
    _call("load_board", {"path": os.path.join(EX, "blinky_555.ocd")})
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
# fixable inputs bypass the fence: missing file retries clean, no re-arm
_bf = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", base=EX)
try:
    _bf.import_fp("fp", path="nonexistent.fp")
    raise AssertionError("should have raised")
except OSError:
    pass
_bf.import_fp("fp", path=os.path.join(EX, "usb_c_edge.fp"))  # retry works, unfenced
# wrong kwarg type (AssertionError inside run) also retries clean
try:
    _bf.export("jlc", outdir=123)
    raise AssertionError("should have raised")
except AssertionError:
    pass
_bf.export("jlc", outdir=tempfile.mkdtemp())
assert "importer:eagle-brd" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "importer:easyeda" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "exporter:easyeda" in cast(list[str], _call("list_plugins", {})["plugins"])
assert "simulate:ngspice" in cast(list[str], _call("list_plugins", {})["plugins"])
assert cast(float, _call("calc", {"what": "divider", "vin": 9, "rtop": 10000,
                                  "rbot": 4700})["vout"]) > 2.8
assert "error" in _rpc("tools/call", {"name": "nope", "arguments": {}})
mcp.kill()
print("MCP OK")
# studio build pipeline headless (no HTTP): same place/route/check/tidy
import sys as _sys
_sys.argv = ["studio"]
from apps import studio as _studio
_st = _studio.H._build(open(os.path.join(EX, "psu.ocd")).read(), False,
                       {"placer": "diffusion", "router": "lroute"})
assert _st["errors"] == [], _st["errors"]
assert cast(dict[str, object], _st["tidy"])["coverage"] == "13/15", _st["tidy"]
assert set(_studio.SLOTS.report("view")) >= {"editor", "pcb", "sch"}
assert "fab_dl" in _studio.SLOTS.render("toolbar", None)  # export button
_spp = _studio.H._build("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                        "net N: R1.1 C1.2\nnet GND: R1.2 C1.1\npour GND on 0\n", False, {})
assert cast(dict[str, object], _spp["pours"]) == {"GND": [0]}
assert len(cast(dict[str, list[object]], _spp["cuts"])["0"]) > 0  # planes ride state
assert cast(dict[int, dict[str, object]], _st["feasible"])[2]["ok"] is True  # badge
assert _st["sim_problems"] == []  # psu has no sim lines
_svm = _studio.H._build("board t 40x30\npart R1 R0805 10k\npart R2 R0805 4k7\n"
                       "net VIN: R1.1\nnet VO: R1.2 R2.1\nnet GND: R2.2\n"
                       "sim vcc VIN 9\nsim expect VO == 5\n", False, {})
assert _svm["sim_problems"] == ["sim VO=2.878V, want == 5V"], _svm["sim_problems"]
# ocd status solves then writes STATUS.md next to the file (temp copy keeps
# the tree clean); exit 0 = DRC clean
import tempfile as _tf
from apps import ocd as _ocd
with _tf.TemporaryDirectory() as _td:
    _sp = os.path.join(_td, "psu.ocd")
    shutil.copy(os.path.join(EX, "psu.ocd"), _sp)
    assert _ocd.cmd_status(_ocd._boot(), [_sp]) == 0
    _sm = open(os.path.join(_td, "STATUS.md")).read()
    assert "tidy (13/15" in _sm, _sm[:200]
    assert "shrink →" in _sm, _sm[-300:]
    assert "solved: diffusion/lroute @ jlc" in _sm, _sm[-500:]
    # status/score honor board.toml picks (same as run) + CLI flags win
    open(os.path.join(_td, "board.toml"), "w").write('placer = "compact"\n')
    assert _ocd.cmd_status(_ocd._boot(), [_sp]) == 0
    assert "solved: compact/" in open(os.path.join(_td, "STATUS.md")).read()
    assert _ocd.cmd_status(_ocd._boot(), ["--placer", "thermal", _sp]) == 0
    assert "solved: thermal/" in open(os.path.join(_td, "STATUS.md")).read()
    assert _ocd.cmd_score(_ocd._boot(), ["--placer", "compact", _sp]) == 0
    assert _ocd.cmd_score(_ocd._boot(), ["--placer", "bogus", _sp]) == 1
    os.remove(os.path.join(_td, "board.toml"))
    # STATUS.md reports pour planes (mitox GND on 0,3)
    shutil.copytree(os.path.join(EX, "mitox"), os.path.join(_td, "mitox"))
    assert _ocd.cmd_status(_ocd._boot(), [os.path.join(_td, "mitox", "mitox.ocd")]) == 0
    assert "planes: GND on 0,3" in open(os.path.join(_td, "mitox", "STATUS.md")).read()
    # STATUS.md surfaces sim expect verdicts (not just voltages)
    open(os.path.join(_td, "simstat.ocd"), "w").write(
        "board t 40x30 2L\npart J1 PINHD2 5V\npart R1 R0805 10k\npart R2 R0805 10k\n"
        "net VIN: J1.1 R1.1\nnet VO: R1.2 R2.1\nnet GND: J1.2 R2.2\n"
        "sim vcc VIN 9\nsim expect VO == 5\n")
    assert _ocd.cmd_status(_ocd._boot(), [os.path.join(_td, "simstat.ocd")]) == 0
    assert "sim FAIL:" in open(os.path.join(_td, "STATUS.md")).read()
    # ocd new scaffolds + ocd diff spots the delta + plugins lists kinds
    _np = os.path.join(_td, "newproj")
    assert _ocd.cmd_new([_np]) == 0
    _toml_txt = open(os.path.join(_np, "board.toml")).read()
    assert "ocd plugins [kind]" in _toml_txt  # scaffold documents valid picks
    assert _ocd.cmd_diff(_ocd._boot(), [_sp, os.path.join(_np, "newproj.ocd")]) == 0
    # scaffold solves clean out of the box (funnel promise: new → run works)
    assert _ocd.cmd_run(_ocd._boot(), [os.path.join(_np, "newproj.ocd")]) == 0
    # main() dispatch: shorthand, flags, help, usage errors (README quickstart)
    assert _ocd.main(["ocd", os.path.join(_np, "newproj.ocd")]) == 0
    assert _ocd.main(["ocd", "run", "--placer", "compact", "--router", "maze",
                      os.path.join(_np, "newproj.ocd")]) == 0
    assert _ocd.main(["ocd", "--help"]) == 0
    assert _ocd.main(["ocd"]) == 1
    assert _ocd.main(["ocd", "frobnicate"]) == 1
    # every file-taking command answers --help (not "No such file")
    assert _ocd.cmd_status(_ocd._boot(), ["--help"]) == 1
    assert _ocd.cmd_score(_ocd._boot(), ["--help"]) == 1
    assert _ocd.cmd_diff(_ocd._boot(), ["--help", "b.ocd"]) == 1
    assert _ocd.cmd_plugins(_ocd._boot(), ["--help"]) == 1
    assert _ocd.main(["ocd", "doctor", "--help"]) == 1
    assert _ocd.main(["ocd", "plugins", "--help"]) == 1
    # flags parse leading or trailing (GNU either way); last wins; dangling stays
    assert _ocd._flags(["--fab", "jlc", "b.ocd"]) == ("jlc", None, None, None, ["b.ocd"])
    assert _ocd._flags(["b.ocd", "--fab", "jlc"]) == ("jlc", None, None, None, ["b.ocd"])
    assert _ocd._flags(["b.ocd", "--fab"]) == (None, None, None, None, ["b.ocd", "--fab"])
    assert _ocd.main(["ocd", "run", os.path.join(_np, "newproj.ocd"),
                      "--placer", "compact"]) == 0
    assert _ocd.cmd_plugins(_ocd._boot(), ["placer"]) == 0
    assert _ocd.cmd_plugins(_ocd._boot(), ["bogus"]) == 1
    # score + lint commands exit 0 on the scaffold; usage errors exit 1
    assert _ocd.cmd_score(_ocd._boot(), [os.path.join(_np, "newproj.ocd")]) == 0
    assert _ocd.cmd_score(_ocd._boot(), []) == 1
    assert _ocd.cmd_lint(_ocd._boot(), [os.path.join(_np, "newproj.ocd")]) == 0
    assert _ocd.cmd_lint(_ocd._boot(), []) == 1
    assert _ocd.cmd_lint(_ocd._boot(), [os.path.join(_td, "nope.ocd")]) == 1
    # malformed input prints ocd: ... exit 1 (no traceback — asserts included)
    open(os.path.join(_td, "bad.ocd"), "w").write(
        "board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\nN pour=bogus :: R1.1\n")
    for _cmd in [_ocd.cmd_run, _ocd.cmd_lint, _ocd.cmd_score]:
        assert _cmd(_ocd._boot(), [os.path.join(_td, "bad.ocd")]) == 1
    assert _ocd.cmd_diff(_ocd._boot(), [os.path.join(_td, "bad.ocd"),
                                        os.path.join(_td, "bad.ocd")]) == 1
    # ocd run --sim dc: passing expects exit 0, failed expects exit 2 (CI ships)
    _simbase = ("board t 40x30 2L\npart J1 PINHD2 5V\npart R1 R0805 10k\npart R2 R0805 10k\n"
                "net VCC: J1.1 R1.1\nnet OUT: R1.2 R2.1\nnet GND: J1.2 R2.2\nsim vcc VCC 5\n")
    open(os.path.join(_td, "simpass.ocd"), "w").write(_simbase + "sim expect OUT == 2.5 tol 0.2\n")
    open(os.path.join(_td, "simfail.ocd"), "w").write(_simbase + "sim expect OUT == 4.9 tol 0.1\n")
    assert _ocd.cmd_run(_ocd._boot(), ["--sim", "dc", os.path.join(_td, "simpass.ocd")]) == 0
    assert _ocd.cmd_run(_ocd._boot(), ["--sim", "dc", os.path.join(_td, "simfail.ocd")]) == 2

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
# connect rejects phantom pins (dumps must always reload)
_bcn = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", base=EX)
for _bargs in [("N", "GHOST", "9"), ("N", "R1", "99")]:
    try:
        _bcn.connect(*_bargs)
        raise AssertionError(f"should have raised: {_bargs}")
    except ValueError:
        pass
assert _bcn.nets["N"].pins == [("R1", "1"), ("R1", "2")]
# file order is free: nets may precede parts (unlike immediate connect)
_bfwd = agent.loads("board t 40x30 2L\nnet N: R1.1 R1.2\npart R1 R0805 10k\n", base=EX)
assert _bfwd.nets["N"].pins == [("R1", "1"), ("R1", "2")]
# constrain rejects unknown types (they'd silently vanish on dumps)
_bct = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", base=EX)
try:
    _bct.constrain({"t": "fxi", "ref": "R1"})
    raise AssertionError("should have raised")
except ValueError as e:
    assert "unknown constraint type" in str(e), str(e)
assert _bct.constraints == []
# CONSTRAINT_TYPES matches dumps arms exactly (new kinds must land in both
# or they silently vanish on save — the class this guards)
import re as _re2
_arms = set(_re2.findall(r'elif t == "([a-z-]+)"',
                         open(os.path.join(EX, "..", "ocdcircuit",
                                           "agent.py")).read()))
_arms |= {"near", "fixed", "near-group", "layer", "width", "pour"}
from ocdcircuit.circuit import CONSTRAINT_TYPES as _CT
assert _arms == set(_CT), (_arms ^ set(_CT))
# Part rotation: rot parses + clamps, wh swaps on 90/270, rot_xy rotates offsets
from ocdcircuit.circuit import Part as _Part
_rp = _Part("R1", "R0805", "", 10, 10, 2.0, 1.0, attrs={"rot": "90"})
assert (_rp.rot, _rp.wh(), _rp.rot_xy(1, 0)) == (90, (1.0, 2.0), (0, 1))
_rp.attrs["rot"] = "180"
assert (_rp.rot, _rp.wh(), _rp.rot_xy(1, 0)) == (180, (2.0, 1.0), (-1, 0))
_rp.attrs["rot"] = "270"
assert (_rp.rot, _rp.wh(), _rp.rot_xy(1, 0)) == (270, (1.0, 2.0), (0, -1))
_rp.attrs["rot"] = "45"
assert (_rp.rot, _rp.wh(), _rp.rot_xy(1, 0)) == (45, (2.0, 1.0), (1, 0))
_rp.attrs["rot"] = "bogus"
assert _rp.rot == 0  # unparseable falls back, never raises
bu.place(seeds=2, iters=100)
bu.route_board()
assert bu.check()["errors"] == [], bu.check()["errors"]  # edge overhang exempt
assert any(f.endswith(".kicad_pcb") for f in bu.export("kicad", outdir=tempfile.mkdtemp()))
# calculators (IPC-2221 etc.): rule-of-thumb values
from ocdcircuit import calc
assert abs(calc.trace_width(1.0) - 0.3) < 0.05
assert abs(calc.trace_amps(calc.trace_width(1.0)) - 1.0) < 0.01  # inverse round-trips
assert abs(calc.divider(9, 10000, 4700) - 2.88) < 0.05
assert abs(calc.divider_pick(9, 5) - 8000) < 1
assert 0 < calc.via_amps(0.3) < calc.via_amps(0.6)  # monotone in drill
assert abs(calc.via_amps(0.3, 40.0) / calc.via_amps(0.3, 10.0) - 2.0) < 0.01  # sqrt rise
# parse_value: suffixes, embedded multipliers, case (1m≠1M), errors
from ocdcircuit.sim import parse_value as _pv
for _vs, _vwant in [("10k", 1e4), ("4k7", 4700.0), ("4R7", 4.7), ("47R", 47.0),
                    ("100n", 1e-7), ("10u", 1e-5), ("1m", 1e-3), ("1M", 1e6),
                    ("0.11", 0.11), ("10", 10.0), ("2.2k", 2200.0),
                    ("1G", 1e9), ("5p", 5e-12), ("1K", 1000.0)]:
    assert abs(_pv(_vs) - _vwant) / max(1e-15, abs(_vwant)) < 1e-9, (_vs, _pv(_vs))
for _vbad in ("", "abc"):
    try:
        _pv(_vbad)
        raise AssertionError(f"should have raised: {_vbad!r}")
    except ValueError:
        pass
# non-finite values rejected (nan/inf poison geometry + MNA silently)
for _vinf in ("inf", "-inf", "1e999", "1e999k"):
    try:
        _pv(_vinf)
        raise AssertionError(f"should have raised: {_vinf!r}")
    except ValueError as e:
        assert "non-finite" in str(e), str(e)
# KiCad footprint aliases land on stdlib (bare + Lib: prefix); unknown stays loud
from ocdcircuit.parts import resolve_fp, KICAD_ALIASES, FOOTPRINTS
assert resolve_fp("Resistor_SMD:R_0603_1608Metric") == "R0603"
assert resolve_fp("SOT-23") == "SOT23" and resolve_fp("R0805") == "R0805"
assert resolve_fp("Nope:Foo_Bar") == "Nope:Foo_Bar"
assert all(v in FOOTPRINTS for v in KICAD_ALIASES.values()), "dangling alias target"
_ab = Board("talias", 40, 30)
_ab.add_part("R1", "Resistor_SMD:R_0805_2012Metric", "10k")
assert _ab.parts["R1"].fp == "R0805"

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
# tsx porter pure fns (no upstream project needed): fp map + tsx parts
from tools import tscircuit as _tsc
assert _tsc.map_fp("0805", "resistor") == "R0805"
assert _tsc.map_fp("0805", "capacitor") == "C0805"
assert _tsc._guess_fp("R7") == "R0805" and _tsc._guess_fp("J2") == "PINHD4"
assert _tsc.tsx_parts(
    '<resistor name="R1" footprint="0805" resistance="10k" />') == {
    "R1": {"kind": "resistor", "fp": "0805", "value": "10k"}}
try:
    _tsc.map_fp("QFN-99", "chip")
    raise AssertionError("should have raised")
except ValueError:
    pass
# atopile porter pure fns (no upstream project needed): main/parts/wires
from tools import atopile as _ato
assert _ato.parse_main(
    "signal VCC\nsignal GND\nj1 = new Conn\nj1.p1 ~ VCC; j1.p2 ~ GND\n") == (
    ["VCC", "GND"], {"j1": "Conn"},
    [("j1.p1", "~", "VCC"), ("j1.p2", "~", "GND")])
assert _ato.parse_parts(
    'component R1:\n  footprint="R_0805"\n  supplier_partno="C1"\n'
    "  signal a ~ pin 1\n") == {
    "R1": {"fp": "R_0805", "lcsc": "C1", "pins": {"a": "1"}}}
assert _ato._wire_stmts("a ~ b; c > d") == [("a", "~", "b"), ("c", ">", "d")]
# mitox porter pure fns (no upstream project needed): elements + fallback
from tools import mitox as _mitox
assert _mitox.tsx_elements(
    '<resistor name="R3" footprint="0402" resistance="1k" '
    'pcbX="3mm" pcbY="4mm" pcbRotation="90deg" />') == {
    "R3": {"kind": "resistor", "fpvar": "0402", "lcsc": "", "value": "1k",
           "x": "3", "y": "4", "rot": "90"}}
assert _mitox._std_fallback("C9") == "C0402"
try:
    _mitox._std_fallback("X9")
    raise AssertionError("should have raised")
except ValueError:
    pass
# atopile positions_from_pcb: KiCad Y-flip, gr_line board size, lib split
from tools import atopile as _ato2
_pcb = ('(kicad_pcb (version 20221001)\n'
        '  (footprint "R_0805" (at 10 20) (attr smd)'
        ' (property "Reference" "R1"))\n'
        '  (footprint "Lib:C_0805" (at 30 40) (attr smd)'
        ' (property "Reference" "C1"))\n'
        '  (gr_line (start 0 0) (end 40 50) (layer Edge.Cuts))\n'
        ')\n')
with tempfile.NamedTemporaryFile("w", suffix=".kicad_pcb", delete=False) as _pf:
    _pf.write(_pcb)
    _pf.flush()
    _pos, _pw, _ph, _pfps = _ato2.positions_from_pcb(_pf.name)
assert (_pos, _pw, _ph, _pfps) == (
    {"R1": (10.0, 30.0), "C1": (30.0, 10.0)}, 40.0, 50.0,
    {"R1": "R_0805", "C1": "C_0805"})
# monster bench helpers: fix-path golden on a toy board; layout.json path
# + overlap counter on the real 5420-part netlist (no solving — fast)
from benches.monster6502.bench import golden as _mgolden
from benches.monster6502.bench import apply_golden as _mapply
from benches.monster6502.bench import overlaps as _mov
_tb2 = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                   "net N: R1.1 C1.2\nfix R1 at 3 5\nfix C1 at 8 5\n", base=EX)
assert _mgolden(_tb2) == {"R1": (3.0, 5.0), "C1": (8.0, 5.0)}
_mbiz = agent.loads(open(os.path.join(EX, "..", "benches", "monster6502",
                                      "monster6502.ocd")).read(),
                    base=os.path.join(EX, "..", "benches", "monster6502"))
assert len(_mbiz.parts) == 5420, len(_mbiz.parts)
assert len(_mgolden(_mbiz)) == 8875, len(_mgolden(_mbiz))
_mapply(_mbiz, {"R1": (1.0, 1.0)})  # unknown refs ignored
_mapply(_mbiz, _mgolden(_mbiz))  # die-true positions: golden overlap floor
assert _mov(_mbiz) == 957, _mov(_mbiz)
# monster converter is deterministic: regenerate → byte-identical .ocd
import hashlib as _hl
from benches.monster6502 import convert as _mconv
_mocd = os.path.join(EX, "..", "benches", "monster6502", "monster6502.ocd")
_before = _hl.sha256(open(_mocd, "rb").read()).hexdigest()
import io as _io
import contextlib as _cl
with _cl.redirect_stdout(_io.StringIO()):
    _mconv.main()
assert _hl.sha256(open(_mocd, "rb").read()).hexdigest() == _before
# easyeda_live CDP framing vs a fake server: upgrade handshake, masked
# client frame, unmasked reply matched by id, 16-bit length branch, close
import socket as _sock
import struct as _struct
import threading as _thr
from tools import easyeda_live as _ezl2


def _frame(payload: bytes, opcode: int = 0x1) -> bytes:
    n = len(payload)
    hdr = (bytes([0x80 | opcode, n]) if n < 126
           else bytes([0x80 | opcode, 126]) + _struct.pack(">H", n))
    return hdr + payload


_srv = _sock.socket()
_srv.bind(("127.0.0.1", 0))
_srv.listen(1)
_port = _srv.getsockname()[1]


def _serve() -> None:
    import socket as _sock2

    def _recvn(conn: _sock2.socket, n: int) -> bytes:
        d = b""
        while len(d) < n:
            d += conn.recv(n - len(d))
        return d

    conn, _ = _srv.accept()
    req = b""
    while not req.endswith(b"\r\n\r\n"):
        req += conn.recv(1024)
    assert b"Upgrade: websocket" in req
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                 b"Connection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n")
    import json as _js4
    for big in (False, True):
        h = _recvn(conn, 2)
        ln = h[1] & 0x7F
        assert h[1] & 0x80, "client must mask"
        if ln == 126:
            ln = _struct.unpack(">H", _recvn(conn, 2))[0]
        mask = _recvn(conn, 4)
        data = _recvn(conn, ln)
        q = _js4.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        res: dict[str, object] = {"id": q["id"], "result": {"echo": q["method"]}}
        if big:
            res["result"] = {"echo": q["method"], "pad": "x" * 200}
        conn.sendall(_frame(_js4.dumps(res).encode()))
        if not big:
            # garbage frames must not kill the read loop: non-JSON text
            # + wrong opcode are skipped, the next call still answers
            conn.sendall(_frame(b"not json{{{"))
            conn.sendall(_frame(b"\x00\x01", opcode=0x2))
    conn.sendall(_frame(b"", opcode=0x8))  # close → _loop exits
    conn.close()


_thr.Thread(target=_serve, daemon=True).start()
_cdp = _ezl2.CDP(f"ws://127.0.0.1:{_port}/devtools/page/1")
assert _cdp.call("Test.ping", {"a": 1}, timeout=5.0) == {"echo": "Test.ping"}
assert _cdp.call("Test.big", {"pad": "y" * 200}, timeout=5.0)["echo"] == "Test.big"
import time as _time
_time.sleep(0.3)
assert _cdp.events == []  # reply consumed, close drained
_srv.close()
# easyeda_live discovery vs a stub DevTools server: page-type filter,
# wait_ready poll, no-page StopIteration
import http.server as _hs


class _Tg(_hs.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        import json as _js5
        body = _js5.dumps([
            {"type": "background_page", "webSocketDebuggerUrl": "ws://x/bg"},
            {"type": "page", "webSocketDebuggerUrl": "ws://x/page1"},
        ]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


_hsrv = _hs.HTTPServer(("127.0.0.1", 0), _Tg)
_hport = _hsrv.server_address[1]
import threading as _thr2
_thr2.Thread(target=_hsrv.serve_forever, daemon=True).start()
assert _ezl2.page_ws(_hport) == "ws://x/page1"
assert _ezl2.wait_ready(_hport, timeout=10.0) == "ws://x/page1"
_hsrv.shutdown()
# no page open: page_ws raises StopIteration, wait_ready polls → TimeoutError
import json as _js7


class _Tg2(_hs.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _js7.dumps([{"type": "background_page",
                            "webSocketDebuggerUrl": "ws://x/bg"}]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


_hsrv2 = _hs.HTTPServer(("127.0.0.1", 0), _Tg2)
_hport2 = _hsrv2.server_address[1]
_thr2.Thread(target=_hsrv2.serve_forever, daemon=True).start()
try:
    _ezl2.page_ws(_hport2)
    raise AssertionError("should have raised")
except StopIteration:
    pass
try:
    _ezl2.wait_ready(_hport2, timeout=0.1)
    raise AssertionError("should have raised")
except TimeoutError:
    pass
_hsrv2.shutdown()
# launch_client returns (proc, profile dir) so callers can clean up
from unittest import mock as _mock2
with _mock2.patch("subprocess.Popen") as _pop:
    _proc, _home = _ezl2.launch_client(9999)
    assert _pop.called and os.path.isdir(_home) and "ezlive" in _home
    import shutil as _sh4
    _sh4.rmtree(_home, ignore_errors=True)
    assert not os.path.exists(_home)
# monster6502 converter pure fns: net sanitizer + block finder on the
# real netlist (counts pinned — structural change should be deliberate)
from benches.monster6502.convert import _safe_net, _find_blocks, _block_members
assert (_safe_net("VDD!"), _safe_net(""), _safe_net("A0")) == ("VDD_", "N", "A0")
import json as _js6
_raw = _js6.load(open(os.path.join(EX, "..", "benches", "monster6502",
                                   "netlist.json")))
_inv, _psg = _find_blocks(_raw["components"])
assert (len(_inv), len(_psg)) == (947, 778), (len(_inv), len(_psg))
_ren, _internal = _block_members(_raw["components"])
assert (len(_ren), len(_internal)) == (3450, 778), (len(_ren), len(_internal))
# mitox convert end-to-end on a synthetic project: tsx + circuit.json →
# harvested .fp + .ocd → loads, solves clean
import json as _js2
from tools import mitox as _mitox2
_td3 = tempfile.mkdtemp()
_tsx = ('<board width="24mm" height="56mm" layers={4}>\n'
        '<resistor name="R3" footprint="0402" resistance="1k" />\n'
        '<capacitor name="C4" footprint="0402" capacitance="100n" />\n'
        "</board>\n")
_cj = [
    {"type": "source_component", "source_component_id": "s1", "name": "R3"},
    {"type": "source_component", "source_component_id": "s2", "name": "C4"},
    {"type": "pcb_component", "pcb_component_id": "p1",
     "source_component_id": "s1",
     "center": {"x": 0, "y": 0}, "rotation": 0, "width": 2.0, "height": 1.2},
    {"type": "pcb_component", "pcb_component_id": "p2",
     "source_component_id": "s2",
     "center": {"x": 5, "y": 0}, "rotation": 0, "width": 2.0, "height": 1.2},
    {"type": "source_port", "source_port_id": "sp1",
     "source_component_id": "s1", "pin_number": "1"},
    {"type": "source_port", "source_port_id": "sp2",
     "source_component_id": "s1", "pin_number": "2"},
    {"type": "source_port", "source_port_id": "sp3",
     "source_component_id": "s2", "pin_number": "1"},
    {"type": "source_port", "source_port_id": "sp4",
     "source_component_id": "s2", "pin_number": "2"},
    {"type": "pcb_port", "pcb_port_id": "pp1", "source_port_id": "sp1"},
    {"type": "pcb_port", "pcb_port_id": "pp2", "source_port_id": "sp2"},
    {"type": "pcb_port", "pcb_port_id": "pp3", "source_port_id": "sp3"},
    {"type": "pcb_port", "pcb_port_id": "pp4", "source_port_id": "sp4"},
    {"type": "pcb_smtpad", "pcb_component_id": "p1", "pcb_port_id": "pp1",
     "x": -0.5, "y": 0, "width": 0.8, "height": 0.9, "shape": "rect"},
    {"type": "pcb_smtpad", "pcb_component_id": "p1", "pcb_port_id": "pp2",
     "x": 0.5, "y": 0, "width": 0.8, "height": 0.9, "shape": "rect"},
    {"type": "pcb_smtpad", "pcb_component_id": "p2", "pcb_port_id": "pp3",
     "x": 4.5, "y": 0, "width": 0.8, "height": 0.9, "shape": "rect"},
    {"type": "pcb_smtpad", "pcb_component_id": "p2", "pcb_port_id": "pp4",
     "x": 5.5, "y": 0, "width": 0.8, "height": 0.9, "shape": "rect"},
    {"type": "source_net", "source_net_id": "n1", "name": "GND"},
    {"type": "source_trace", "connected_source_port_ids": ["sp2", "sp3"],
     "connected_source_net_ids": ["n1"]},
]
open(os.path.join(_td3, "index.circuit.tsx"), "w").write(_tsx)
open(os.path.join(_td3, "index.circuit.circuit.json"), "w").write(
    _js2.dumps(_cj))
_fn4 = _mitox2.convert(_td3, os.path.join(_td3, "out"))
assert _fn4.endswith("mitox.ocd")
_mb = agent.loads(open(_fn4).read(), base=os.path.join(_td3, "out"))
assert sorted(_mb.parts) == ["C4", "R3"] and "GND" in _mb.nets
assert os.path.isfile(os.path.join(_td3, "out", "fp", "FP_R3.fp"))  # harvested
_mb.place(seeds=1, iters=20)
_mb.route_board()
assert _mb.check()["errors"] == [], _mb.check()["errors"]
# malformed porter input exits 1 with a clean message (no traceback)
_badm = tempfile.mkdtemp()
open(os.path.join(_badm, "index.circuit.tsx"), "w").write(
    '<board width="10mm" height="10mm"></board>')
open(os.path.join(_badm, "index.circuit.circuit.json"), "w").write(
    _js2.dumps([{"type": "source_component"}]))
import sys as _sys9
_sys9.argv = ["mitox", _badm, tempfile.mkdtemp()]
try:
    _mitox2.main()
    raise AssertionError("should have exited 1")
except SystemExit as e:
    assert e.code == 1, e.code
# hostile foreign names fail fast (no traversal, no unloadable output)
_badh = tempfile.mkdtemp()
open(os.path.join(_badh, "index.circuit.tsx"), "w").write(
    '<board width="10mm" height="10mm">'
    '<resistor name="R3" footprint="0402" resistance="1k" '
    'supplierPartNumbers={{jlcpcb: ["../../x"]}} /></board>')
open(os.path.join(_badh, "index.circuit.circuit.json"), "w").write(
    _js2.dumps([
        {"type": "source_component", "source_component_id": "s1", "name": "R3"},
        {"type": "pcb_component", "pcb_component_id": "p1",
         "source_component_id": "s1", "center": {"x": 0, "y": 0},
         "rotation": 0, "width": 2.0, "height": 1.2},
        {"type": "source_port", "source_port_id": "sp1",
         "source_component_id": "s1", "pin_number": "1"},
        {"type": "pcb_port", "pcb_port_id": "pp1", "source_port_id": "sp1"},
        {"type": "pcb_smtpad", "pcb_component_id": "p1", "pcb_port_id": "pp1",
         "x": 0, "y": 0, "width": 0.8, "height": 0.9, "shape": "rect"}]))
_outh = tempfile.mkdtemp()
_fnh = _mitox2.convert(_badh, _outh)
assert sorted(os.listdir(os.path.join(_outh, "fp"))) == ["FP_.._.._x.fp"]
_badh2 = tempfile.mkdtemp()
open(os.path.join(_badh2, "index.circuit.tsx"), "w").write(
    '<board width="10mm" height="10mm"></board>')
open(os.path.join(_badh2, "index.circuit.circuit.json"), "w").write(
    _js2.dumps([{"type": "source_component", "source_component_id": "s1",
                 "name": "../../../tmp/pwned"}]))
try:
    _mitox2.convert(_badh2, tempfile.mkdtemp())
    raise AssertionError("should have raised")
except ValueError as e:
    assert "bad component name" in str(e), str(e)
# tscircuit convert rejects them too (same unloadable-output class)
from tools import tscircuit as _tsc3
_badt = tempfile.mkdtemp()
os.makedirs(os.path.join(_badt, "dist", "index"))
open(os.path.join(_badt, "index.circuit.tsx"), "w").write(
    '<board width="10mm" height="10mm"></board>')
open(os.path.join(_badt, "dist", "index", "circuit.json"), "w").write(
    _js2.dumps([{"type": "source_component", "source_component_id": "s1",
                 "name": "../../../tmp/pwn"}]))
try:
    _tsc3.convert(_badt)
    raise AssertionError("should have raised")
except ValueError as e:
    assert "bad component name" in str(e), str(e)
# entry points answer --help without side effects (no regeneration)
import subprocess as _sp9
for _mod, _usage in [
        ("tools.tscircuit", "tools.tscircuit"),
        ("benches.monster6502.convert", "monster6502.convert"),
        ("benches.monster6502.bench", "monster6502.bench"),
        ("ocdcircuit.raster", "preview.png"),
        ("ocdcircuit.view3d", "preview3d.html")]:
    _hr = _sp9.run([sys.executable, "-m", _mod, "--help"], capture_output=True,
                   timeout=60, cwd=os.path.join(EX, ".."))
    assert _usage in (_hr.stdout.decode() + _hr.stderr.decode()), (_mod, _hr)
# tscircuit convert end-to-end: tsx + dist circuit.json → .ocd text →
# loads, solves clean (pcb centers are board-centered in tscircuit output)
import json as _js3
from tools import tscircuit as _tsc2
_td4 = tempfile.mkdtemp()
os.makedirs(os.path.join(_td4, "dist", "index"))
open(os.path.join(_td4, "index.circuit.tsx"), "w").write(
    '<board width="30mm" height="20mm">\n'
    '<resistor name="R1" footprint="0805" resistance="10k" />\n'
    '<capacitor name="C1" footprint="0805" capacitance="100n" />\n'
    "</board>\n")
_cj2 = [
    {"type": "source_component", "source_component_id": "s1", "name": "R1"},
    {"type": "source_component", "source_component_id": "s2", "name": "C1"},
    {"type": "source_port", "source_port_id": "p1",
     "source_component_id": "s1", "pin_number": "1"},
    {"type": "source_port", "source_port_id": "p2",
     "source_component_id": "s1", "pin_number": "2"},
    {"type": "source_port", "source_port_id": "p3",
     "source_component_id": "s2", "pin_number": "1"},
    {"type": "source_port", "source_port_id": "sp4",
     "source_component_id": "s2", "pin_number": "2"},
    {"type": "source_net", "source_net_id": "n1", "name": "GND"},
    {"type": "source_trace", "connected_source_port_ids": ["p2", "p3"],
     "connected_source_net_ids": ["n1"], "display_name": ""},
    {"type": "pcb_component", "source_component_id": "s1",
     "center": {"x": -5, "y": 0}},
    {"type": "pcb_component", "source_component_id": "s2",
     "center": {"x": 5, "y": 0}},
]
open(os.path.join(_td4, "dist", "index", "circuit.json"), "w").write(
    _js3.dumps(_cj2))
_tx = _tsc2.convert(_td4)
_tb = agent.loads(_tx, base=_td4)
assert sorted(_tb.parts) == ["C1", "R1"] and "GND" in _tb.nets
assert any(c == {"t": "fixed", "ref": "R1", "x": 10.0, "y": 10.0}
           for c in _tb.constraints)  # centered (-5,0) + (15,10)
_tb.place(seeds=1, iters=20)
_tb.route_board()
assert _tb.check()["errors"] == [], _tb.check()["errors"]
# atopile convert end-to-end on a synthetic project: parse → elaborate →
# emit .ocd → loads, solves clean
import tempfile as _tf2
from tools import atopile as _ato3
_td2 = _tf2.mkdtemp()
os.makedirs(os.path.join(_td2, "proj", "atopile", "parts"))
open(os.path.join(_td2, "proj", "atopile", "main.ato"), "w").write(
    "signal VCC\nsignal GND\n"
    "r1 = new Resistor\nr1.package = \"R0805\"\n"
    "c1 = new Capacitor\nc1.package = \"C0805\"\n"
    "r1.p1 ~ VCC\nr1.p2 ~ c1.p1\nc1.p2 ~ GND\nr1.p1 ~ GND\n")
open(os.path.join(_td2, "proj", "atopile", "parts", "r.ato"), "w").write(
    "component Resistor:\n  signal p1 ~ pin 1\n  signal p2 ~ pin 2\n"
    "component Capacitor:\n  signal p1 ~ pin 1\n  signal p2 ~ pin 2\n")
_fn3 = _ato3.convert(os.path.join(_td2, "proj"), os.path.join(_td2, "out"))
assert _fn3.endswith("proj.ocd")
_ab = agent.loads(open(_fn3).read(), base=os.path.join(_td2, "out"))
assert {p.fp for p in _ab.parts.values()} == {"R0805", "C0805"}
_ab.place(seeds=1, iters=20)
_ab.route_board()
assert _ab.check()["errors"] == [], _ab.check()["errors"]
# easyeda_live pro_source is pure (no client needed): NET + PRIMITIVE +
# per-trace LINE/GEOM pairs, sequential tickets, 1-indexed layers
import json as _js
from tools import easyeda_live as _ezl
_eb = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.1 C1.2\n", base=EX)
_eb.place(seeds=1, iters=20)
_eb.route_board()
_out = _ezl.pro_source(_eb, "BLANK|")
assert _out.startswith("BLANK||") and _out.endswith("|")
_recs = [_js.loads(r) for r in _out[len("BLANK||"):-1].split("||")]
assert [r.get("type", "GEOM") for r in _recs] == (
    ["NET", "PRIMITIVE"] + ["LINE", "GEOM"] * len(_eb.traces))
assert [r["ticket"] for r in _recs if "ticket" in r] == list(
    range(200, 200 + len([r for r in _recs if "ticket" in r])))
_geoms = [r for r in _recs if "netName" in r]
assert {r["netName"] for r in _geoms} == {"N"} and len(_geoms) == len(_eb.traces)
assert {r["layerId"] for r in _geoms} == {s.layer + 1 for s in _eb.traces}
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
# eagle .brd export round-trips through our own importer (refs + nets)
import xml.etree.ElementTree as _ET
_egf = _ebb.export("eagle", outdir=tempfile.mkdtemp())[0]
assert _egf.endswith(".brd") and _ET.parse(_egf) is not None
_egrt = agent.from_ir(foreign.eagle_brd(open(_egf).read()))
assert sorted(_egrt.parts) == ["R1", "R2"] and sorted(_egrt.nets) == ["GND", "N"]
# eagle pours export as solid polygons (mitox GND on 0,3 → 2 polygons)
_mit = agent.loads(open(os.path.join(EX, "mitox", "mitox.ocd")).read(),
                  base=os.path.join(EX, "mitox"))
_mit.place(seeds=1, iters=30)
_mit.route_board()
_mef = _mit.export("eagle", outdir=tempfile.mkdtemp())[0]
_mep = _ET.parse(_mef).getroot().findall(".//polygon")
assert len(_mep) == 2 and all(p.get("pour") == "solid" for p in _mep)
# kicad .sch export: same picture as the canvas, ERC-clean per kicad-cli
_ksf = _ebb.export("kicad-sch", outdir=tempfile.mkdtemp())[0]
assert _ksf.endswith(".kicad_sch") and "(global_label" in open(_ksf).read()
if shutil.which("kicad-cli") is not None:
    import glob as _glob
    _ercd = tempfile.mkdtemp()
    subprocess.run(["kicad-cli", "sch", "erc", _ksf],
                   capture_output=True, cwd=_ercd)
    _erct = open(_glob.glob(os.path.join(_ercd, "*-erc.rpt"))[0]).read()
    _ercsum = next(l for l in _erct.splitlines() if "ERC messages" in l)
    assert "Errors 0" in _ercsum, _ercsum

# textured 3D: glTF materials + shared mesh builder
import json as _jj
_g = _jj.loads(cast(str, bo.render("gltf")))
assert {m["name"] for m in _g["materials"]} >= {"mask", "copper", "chip"}
assert len(_g["meshes"]) == len(_g["materials"])
for _m in _g["meshes"]:
    _at = _m["primitives"][0]["attributes"]
    assert set(_at) >= {"POSITION", "NORMAL", "TEXCOORD_0"}, _at
assert len(_g["images"]) == len(_g["materials"])
assert all(_t["sampler"] == 0 for _t in _g["textures"])
assert all(_m["pbrMetallicRoughness"]["baseColorFactor"] == [1, 1, 1, 1]
           for _m in _g["materials"])  # texture IS the color (no double-dark)
assert all(_m.get("alphaMode", "OPAQUE") == "OPAQUE" for _m in _g["materials"])
# multilayer stack: copper planes inside the slab, not floating above
from ocdcircuit.geom3d import build as _build3d
_bo4 = agent.loads("board t4 40x30 4L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                   "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n", base=EX)
_bo4.place(seeds=1, iters=30)
_bo4.route_board("lroute")
_cu_z = sorted({round(min(a[2], b[2], c[2]), 2)
                for a, b, c, m in _build3d(_bo4) if m == "copper"})
assert min(_cu_z) >= -0.05 and max(_cu_z) <= 1.66, _cu_z

# blocks: repeatable units — stamp 3x, join, round-trip exactly
_bb = agent.loads("board t 60x40\nblock ch\npart U QFN28\npart C C0805 100n\n"
                  "net N: U.3 C.2\nnet GND: U.1 C.1\nend\n"
                  "instance ch as A\ninstance ch as B join GND\n")
assert sorted(_bb.parts) == ["A_C", "A_U", "B_C", "B_U"]
assert ("B_C", "1") in _bb.nets["GND"].pins and ("B_U", "1") in _bb.nets["GND"].pins
assert ("A_C", "1") in _bb.nets["GND"].pins  # GND auto-joins even unlisted
assert "block ch" in agent.dumps(_bb) and "instance ch as B join GND" in agent.dumps(_bb)
assert agent.dumps(agent.loads(agent.dumps(_bb))) == agent.dumps(_bb)
# hostile instance prefixes stay contained (dumps still reloads)
_bhp = agent.loads("board t 60x40 2L\nblock ch\npart R R0805 10k\npart C C0805 100n\n"
                   "net RC: R.1 C.1\nend\ninstance ch as ../../x\n", base=EX)
assert agent.dumps(agent.loads(agent.dumps(_bhp), base=EX)) == agent.dumps(_bhp)
# block constraints remap on stamp (pour/route/trace survive, joins stay global)
_bc = agent.loads("board t 60x40 2L\nblock ch\npart R R0805 10k\npart C C0805 100n\n"
                  "net N: R.1 C.1\nnet GND: R.2 C.2\npour GND on 0\nroute N on 1\ntrace N 0.6\nend\n"
                  "instance ch as A\ninstance ch as B join N GND\n", base=EX)
assert ("GND", 0) in [(c.get("net"), c.get("layer")) for c in _bc.constraints
                      if isinstance(c, dict) and c.get("t") == "pour"]
assert ("N", 1) in [(c.get("net"), c.get("layer")) for c in _bc.constraints
                    if isinstance(c, dict) and c.get("t") == "layer"]
assert ("N", 0.6) in [(c.get("net"), c.get("width")) for c in _bc.constraints
                      if isinstance(c, dict) and c.get("t") == "width"]
assert agent.dumps(agent.loads(agent.dumps(_bc), base=EX)) == agent.dumps(_bc)
# mermaid nets + net attrs stamp too (:: lines used to die in blocks)
_bm = agent.loads("board t 60x40 2L\nclass hv width=0.8\nblock ch\npart R R0805 10k\n"
                  "part C C0805 100n\nHV class=hv :: R.1 C.1\nLV :: R.2 C.2\nend\n"
                  "instance ch as A\n", base=EX)
assert _bm.nets["A_HV"].attrs == {"class": "hv"}
_bm.place(seeds=1, iters=30)
_bm.route_board()
assert _bm.nets["A_HV"].width == 0.8
assert agent.dumps(agent.loads(agent.dumps(_bm), base=EX)) == agent.dumps(_bm)
for _bbad, _bfrag in [
    ("board t 10x10\nblock a\npart R1 R0805\nblock b\n", "nested blocks"),
    ("board t 10x10\nend\n", "end without block"),
    ("board t 10x10\nblock a\npart R1 R0805\nend\nblock a\npart R2 R0805\nend\n", "duplicate block"),
    ("board t 10x10\ninstance nope as X\n", "unknown block"),
    ("board t 10x10\nblock a\nuse x.ocd\nend\n", "not allowed inside block"),
    ("board t 0x10 2L\n", "must be positive"),
    ("board t 40x30 0L\n", "≥1 layer"),
    ("board t 40x30 2L\npart R1 R0805 10k x=nan y=5\nnet N: R1.1 R1.2\n", "bad x=/y="),
    ("board ../evil 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", "bad board name"),
]:
    try:
        agent.loads(_bbad)
        raise AssertionError(f"should have raised: {_bbad!r}")
    except ValueError as e:
        assert _bfrag in str(e), f"{_bfrag!r} not in {e}"
# set_board/declare reject non-positive sizes too (same guard, API path)
_sb2 = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", base=EX)
try:
    _sb2.declare({"board": {"w": 0, "h": 10}})
    raise AssertionError("should have raised")
except ValueError as e:
    assert "must be positive" in str(e), str(e)
assert (_sb2.width, _sb2.height) == (40.0, 30.0)  # failed resize doesn't stick
try:
    _sb2.declare({"board": {"w": 1e999, "h": 10}})
    raise AssertionError("should have raised")
except ValueError as e:
    assert "must be positive" in str(e), str(e)
assert (_sb2.width, _sb2.height) == (40.0, 30.0)
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
assert set(cast(dict[str, object], _tt["T11_copper_balance"])) == {"tile_sigma", "layer_delta"}
assert _tt["T12_acid_traps"] == 0  # Manhattan routing makes no acute wedges
# T12 fires on genuinely acute joins: V with 58° inner wedge scores 1;
# a 45° direction turn (135° inner copper) is not a trap
from ocdcircuit.circuit import Seg as _Seg0
_wb = agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.1 R1.2\n")
_wb.traces = [_Seg0("N", 0, 0, 10, 0, 0, 0.3), _Seg0("N", 10, 0, 5, 8, 0, 0.3)]
assert _wb.score(tidy=True)["T12_acid_traps"] == 1
_wb.traces = [_Seg0("N", 5, 5, 10, 5, 0, 0.3), _Seg0("N", 10, 5, 13, 8, 0, 0.3)]
assert _wb.score(tidy=True)["T12_acid_traps"] == 0
assert agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.2\n"
                   ).score(tidy=True)["T12_acid_traps"] is None  # unrouted
assert cast(dict[str, object], _tt["T13_schematic"])["jogs"] == 0
assert isinstance(cast(dict[str, object], _tt["T13_schematic"])["crossings"], int)
assert _tt["T15_silk_consistency"] == 1.0
assert isinstance(_tt["coverage"], str)
# tidy-GA placer: improves placement-owned fitness, one effect, clean undo
from ocdcircuit import tidy_ga as _ga
_tg = agent.loads(open(os.path.join(EX, "blinky_555.ocd")).read(), base=EX)
_tg.place(seeds=2, iters=100)
_tg.route_board()
_f0 = _ga.fitness(_tg)
_snap = _tg.ctx.snapshot()
_tidy_fit = _tg.place("tidy", pop=4, gen=2, seed=1, iters=30)
assert _tidy_fit <= _f0, f"tidy regressed: {_f0:.1f} -> {_tidy_fit:.1f}"
assert _tg.ctx.snapshot() - _snap == 1, "tidy must leave one effect"
assert _tg.check()["errors"] == [], _tg.check()["errors"]
_tg.ctx.undo()
assert abs(_ga.fitness(_tg) - _f0) < 1e-6, "tidy undo must restore fitness"
_tu = agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.2\n").score(tidy=True)
assert _tu["T1_crossings"] is None and _tu["T3_orthogonality"] is None
assert _tu["T4_vias"] is None and _tu["T5_headroom"] is None
assert _sb.diff(_sb) == ""
_drep = _sb.diff(agent.loads("board t 40x30\npart R1 R0805 10k\n"
                             "net N: R1.2\nnet GND: R1.1\n"))
assert "- part C1" in _drep
# diff branches: size, part change, move (>0.05), net add, constraint delta
from ocdcircuit import diff as _diffmod
_da = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet N: R1.1 R1.2\n", base=EX)
_db = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.1 R1.2\nnet GND: R1.2 C1.2\npour GND on 0\nroute-grid 0.2\n", base=EX)
_dd = _diffmod.diff(_da, _db)
assert "+ part C1" in _dd and "+ pour" in _dd and "route-grid" in _dd, _dd
_dc = agent.loads("board t 44x30 2L\npart R1 R0805 4k7\nnet N: R1.1 R1.2\n"
                  "fix R1 at 3 5\n", base=EX)
_dd2 = _diffmod.diff(_da, _dc)
assert "size: 40x30 2L → 44x30 2L" in _dd2 and "~ part R1" in _dd2, _dd2
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
# sim expect: divider VO≈2.88 — pass, fail, round-trip, studio flag
from ocdcircuit import sim as _sim
_sime = agent.loads("board t 40x30\npart R1 R0805 10k\npart R2 R0805 4k7\n"
                    "net VIN: R1.1\nnet VO: R1.2 R2.1\nnet GND: R2.2\nsim vcc VIN 9\n"
                    "sim expect VO ~ 2.88\nsim expect VIN == 9\nsim expect VO == 5\n")
assert _sim.expect(_sime) == ["sim VO=2.878V, want == 5V"], _sim.expect(_sime)
assert agent.dumps(agent.loads(agent.dumps(_sime), base=EX)) == agent.dumps(_sime)
# sim r/c/l overrides beat part values in the solve
_simo = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart R2 R0805 10k\n"
                    "net VIN: R1.1\nnet VO: R1.2 R2.1\nnet GND: R2.2\n"
                    "sim vcc VIN 9\nsim r R2 4k7\n", base=EX)
assert abs(cast(dict[str, float], _simo.simulate()["nets"])["VO"] - 2.878) < 0.01
assert agent.parse_constraint("sim expect VO ~ 2.88 tol 1%") == {
    "t": "sim", "kind": "expect", "net": "VO", "op": "~", "value": "2.88", "tol": "1%"}
# gates plugin: NAND truth + DFF divide + clk grammar + round-trip
_simg = agent.loads("board t 40x30\npart U1 SOIC14 NAND logic=NAND\n"
                    "net A: U1.1\nnet B: U1.2\nnet Y: U1.3\n"
                    "sim vcc A 1\nsim vcc B 0\n")
assert cast(dict[str, int], _simg.simulate("gates")["nets"])["Y"] == 1
_simg2 = agent.loads("board t 40x30\npart U1 SOIC14 DFF logic=DFF\n"
                     "net D: U1.1\nnet CLK: U1.2\nnet Q: U1.3\nnet QN: U1.4\n"
                     "sim vcc D 1\nsim clk CLK 4\nsim tran 0.01 100\n")
_gr = _simg2.simulate("gates", ticks=10)
assert cast(dict[str, int], _gr["nets"])["Q"] == 1
assert cast(list[int], cast(dict[str, object], _gr["waves"])["Q"]) == [1] * 10
assert agent.parse_constraint("sim clk CLK 4") == {
    "t": "sim", "kind": "clk", "net": "CLK", "period": 4.0, "duty": 0.5}
# gates truth: every combinational kind + JK toggle on rising edge
from ocdcircuit import gates as _gates
for _gk, _gins, _gwant in [
        ("NAND", [1, 1], 0), ("NAND", [1, 0], 1),
        ("NOR", [0, 0], 1), ("NOR", [1, 0], 0),
        ("AND", [1, 1], 1), ("AND", [1, 0], 0),
        ("OR", [0, 0], 0), ("OR", [0, 1], 1),
        ("XOR", [1, 1], 0), ("XOR", [1, 0], 1),
        ("INV", [0], 1), ("INV", [1], 0)]:
    assert _gates._gate_fn(_gk, _gins) == _gwant, (_gk, _gins)
_jk = agent.loads("board t 40x30\npart U1 SOIC14 JK logic=JK\n"
                  "net J: U1.1\nnet K: U1.2\nnet CLK: U1.3\nnet Q: U1.4\n"
                  "sim vcc J 1\nsim vcc K 1\nsim clk CLK 4\nsim tran 0.01 100\n")
assert cast(list[int], cast(dict[str, object],
             _jk.simulate("gates", ticks=8)["waves"])["Q"]) == [1] * 4 + [0] * 4
assert agent.dumps(agent.loads(agent.dumps(_simg2))) == agent.dumps(_simg2)
# ngspice plugin: same shape as mna + analog mna cannot do (skip if no binary)
import shutil as _sh3
# netlist text pins without the binary: element lines, source, terminator
from ocdcircuit import spice as _spice
_nlb = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart R2 R0805 4k7\n"
                   "part C1 C0805 100n\nnet VIN: R1.1\nnet VO: R1.2 R2.1 C1.1\n"
                   "net GND: R2.2 C1.2\nsim vcc VIN 9\n", base=EX)
_nlt = _spice.netlist(_nlb).splitlines()
assert _nlt[0] == "* ocdcircuit: t" and _nlt[-1] == ".end"
assert "RR1 VIN VO 10000" in _nlt and "CC1 VO 0 1e-07 ic=0" in _nlt
assert "V1 VIN 0 dc 9" in _nlt
# spice name/probe helpers: GND→0, sanitizer, explicit-else-all-non-GND
assert (_spice._norm("GND"), _spice._norm("VSS"), _spice._norm("N-OUT!"),
        _spice._norm("")) == ("0", "0", "N-OUT_", "N")
_ppb = agent.loads("board t 40x30 2L\npart R1 R0805 10k\nnet A: R1.1\nnet B: R1.2\n"
                   "net GND: R1.1\nsim probe A\n", base=EX)
assert _spice._probes(_ppb) == ["A"]
assert _spice._probes(agent.loads(
    "board t 40x30 2L\npart R1 R0805 10k\nnet A: R1.1\nnet B: R1.2\n",
    base=EX)) == ["A", "B"]
if _sh3.which("ngspice") is not None:
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
# nc on ghost part/pin errors (typo'd exemptions silently cover nothing)
_lnc = agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.1 R1.2\nnc R1.1 Q9.1\n", base=EX)
assert any("nc on unknown part Q9.1" in e for e in cast(list[str], _lnc.lint()["errors"]))
_lnp = agent.loads("board t 40x30\npart R1 R0805 10k\nnet N: R1.1 R1.2\nnc R1.9\n", base=EX)
assert any("nc on unknown pin R1.9" in e for e in cast(list[str], _lnp.lint()["errors"]))
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
# pour renders copper now: maze skips poured legs, Gerber plots planes
_lp = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                  "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\npour GND on 0\n"
                  "pour N on 9\n", base=EX)
assert not [w for w in cast(list[str], _lp.lint()["warnings"]) if "not rendered" in w]
assert any("pour N on layer 9" in e for e in cast(list[str], _lp.lint()["errors"]))
_lp.place(seeds=1, iters=30)
_lp.route_board("maze")
assert not [t for t in _lp.traces if t.net == "GND" and t.layer == 0]
assert any(t.net == "N" for t in _lp.traces)  # unpoured net still routes
from ocdcircuit.export import plane_plots as _pp
assert _pp(_lp) and all(_pp(_lp)[ll] for ll in _pp(_lp))
_lp2 = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                   "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n", base=EX)
assert _pp(_lp2) == {}  # no pours, no plots
# match/diff constraints: T6 reports routed skew, estimates when unrouted
_mt = agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart R2 R0805 10k\n"
                  "part C1 C0805 100n\nnet A: R1.1 R2.1\nnet B: R1.2 C1.1\n"
                  "net GND: R2.2 C1.2\nmatch A B\ndiff A B gap 0.5\n", base=EX)
_mt.place(seeds=2, iters=100)
_t6e = cast(dict[str, dict[str, object]], _mt.score(tidy=True)["T6_skew"])
assert _t6e["match:A+B"]["estimated"] is True, _t6e
_mt.route_board()
_t6 = cast(dict[str, dict[str, object]], _mt.score(tidy=True)["T6_skew"])
assert _t6["match:A+B"]["estimated"] is False, _t6
assert _t6["diff:A/B"]["estimated"] is False, _t6
# meta lines: title/rev/desc round-trip, flow into IR + KiCad title
_mb = agent.loads("board t 40x30\nmeta title Blinky 555\nmeta rev A\n"
                  "meta desc demo\npart R1 R0805 10k\nnet N: R1.1 R1.2\n")
assert _mb.meta == {"title": "Blinky 555", "rev": "A", "desc": "demo"}, _mb.meta
assert agent.dumps(agent.loads(agent.dumps(_mb))) == agent.dumps(_mb)
import json as _jm
assert _jm.loads(agent.to_json(_mb))["board"]["meta"] == {"title": "Blinky 555", "rev": "A", "desc": "demo"}
_kd = _mb.export("kicad", outdir=tempfile.mkdtemp())[0]
_kdt = open(_kd).read()
assert '(title "Blinky 555")' in _kdt and '(rev "A")' in _kdt and '(comment 1 "demo")' in _kdt
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
# router auto-select: default below 1000 parts, coarse at/above
Board._run = _spy_run  # type: ignore[method-assign]
try:
    _lb.route_board()
except RuntimeError:
    pass
assert _seen == {"kind": "router", "key": None}, _seen
try:
    _big.route_board()
except RuntimeError:
    pass
assert _seen == {"kind": "router", "key": "coarse"}, _seen
Board._run = _orig_run  # type: ignore[method-assign]
# multilevel runs for real (not just dispatched): pico places DRC-clean
_ml = agent.loads(open(os.path.join(EX, "pico_tmc2209", "pico_tmc2209.ocd")).read(),
                  base=os.path.join(EX, "pico_tmc2209"))
_ml.place("multilevel", seeds=1, iters=30)
_ml.route_board("lroute")
assert _ml.check()["errors"] == [], _ml.check()["errors"]
# gallery candidates: N distinct seeds, sorted, one undoable effect
from ocdcircuit import solver as _solver
_gal = agent.loads(open(os.path.join(EX, "psu.ocd")).read(), base=EX)
_snap0 = _gal.ctx.snapshot()
_cands = _solver.candidates(_gal, n=3, seeds=1, iters=30)
_costs = [float(cast(float, c["cost"])) for c in _cands]
assert len(_cands) == 3 and len({_c["seed"] for _c in _cands}) == 3
assert _costs == sorted(_costs)
assert _gal.ctx.snapshot() - _snap0 == 1  # inner placements rolled back
# pick worst, verify restore is exact, undo returns to start
_solver.restore_candidate(_gal, _cands[-1])
assert round(_solver.cost(_gal), 1) == _costs[-1]
_gal.ctx.undo()
assert _gal.ctx.snapshot() == _snap0 + 1  # candidates' own effect remains
# chain: fix a part, re-run a different engine, others re-arrange
_gal2 = agent.loads(open(os.path.join(EX, "psu.ocd")).read(), base=EX)
_c0 = _solver.candidates(_gal2, n=1, key="diffusion", seeds=1, iters=30)[0]
_solver.restore_candidate(_gal2, _c0)
before = {r: (p.x, p.y) for r, p in _gal2.parts.items()}
_gal2.constrain({"t": "fixed", "ref": "J1", "x": 3.0, "y": 15.0})
_gal2.place("compact", seeds=1, iters=30)
assert _gal2.parts["J1"].x == 3.0 and _gal2.parts["J1"].y == 15.0
assert any((p.x, p.y) != before[r] for r, p in _gal2.parts.items() if r != "J1")
# feasibility hint: lroute wirelength shrinks with layer count (congestion
# comparison, not a maze verdict — ok means "routed", DRC owns "clean")
_fb = agent.loads(open(os.path.join(EX, "blinky_555.ocd")).read(), base=EX)
_fb.place(seeds=2, iters=100)
_feas = _solver.feasible(_fb)
assert int(cast(int, _feas[2]["segs"])) > 0
assert float(cast(float, _feas[2]["wirelength"])) <= float(cast(float, _feas[1]["wirelength"]))
assert len(_fb.traces) == 0  # probe leaves the board untouched
# symbols: stdlib resolve + .sym file + sym= attr + sch bodies + undo
from ocdcircuit import symbol as _sym
assert _sym.resolve("R0805").get("zigzag") is True
assert cast(dict[str, object], _sym.resolve("SOIC8")["pins"])["1"] == ("left", 0, "")
_symb = agent.loads("board sy 20x10\npart R1 R0805 1k\npart U1 SOIC8 NE555\n"
                    "net N: R1.1 U1.2\nnet GND: R1.2 U1.3\n")
assert _symb.symbol_of("R1").get("zigzag") is True
assert cast(dict[str, object], _symb.symbol_of("U1")["pins"])["1"] == ("left", 0, "")
_svg = cast(str, _symb.render("sch"))
assert "<polyline" in _svg and _svg.count("<circle") >= 4  # zigzag + stubs
with tempfile.TemporaryDirectory() as _d:
    _symfp = os.path.join(_d, "op.sym")
    open(_symfp, "w").write("symbol OPX\npin 1 left IN+\npin 2 left IN-\n"
                         "pin 3 right OUT\nlabel {ref} {value}\nnotch\n")
    _sb = agent.loads(f"board s2 20x10\nsym {_symfp}\npart U1 SOIC8 TL072 sym=OPX pin2=VFB\n"
                      "net A: U1.1\nnet B: U1.2\n")
    assert cast(dict[str, object], _sb.symbol_of("U1")["pins"])["3"] == ("right", 0, "OUT")
    _ssvg = cast(str, _sb.render("sch"))
    assert "U1 TL072" in _ssvg and "VFB" in _ssvg  # label template + pin override
    assert "sym " in agent.dumps(_sb)  # round-trips
    _sb2 = agent.loads("board s3 20x10\npart U1 SOIC8 TL072\nnet A: U1.1\n")
    _s0 = _sb2.ctx.snapshot()
    _sb2.import_sym(path=_symfp)
    assert "OPX" in _sb2.custom_sym
    _sb2.ctx.rollback(_s0)
    assert "OPX" not in _sb2.custom_sym
# doctor: registry healthy on a live board
_doc = _lb.plugins().get("doctor", "std")
assert isinstance(_doc, Plugin)
_docr = cast(dict[str, object], _doc.run(_lb))
assert _docr["ok"] is True, _docr
assert any(str(c.get("name")) == "plugin:lint"
           and c.get("ok") for c in cast(list[dict[str, object]], _docr["checks"]))
assert any(str(c.get("name")) == "plugin:score"
           and c.get("ok") for c in cast(list[dict[str, object]], _docr["checks"]))
assert any(str(c.get("name")) == "plugin:diff"
           and c.get("ok") for c in cast(list[dict[str, object]], _docr["checks"]))
assert any(str(c.get("name")) == "kicad-cli"
           and c.get("ok") for c in cast(list[dict[str, object]], _docr["checks"]))
print("ALL OK")
