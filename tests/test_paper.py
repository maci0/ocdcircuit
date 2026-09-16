"""Paper §5 core self-check: effect/notify/fiber/loader/HMR (asserts only)."""
from __future__ import annotations
from collections.abc import Callable, Iterator
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit.core import (Component, Context, Entry, Fiber, InactiveAccess,
                             Registry, UiSlots, UndeclaredAccess, classify,
                             execute, stale_entries)

# Alg 1: effect folds yielded inverses LIFO, dispose fires once
ctx = Context()
log: list[str] = []


def _cb() -> object:
    def _gen() -> Iterator[Callable[[], None]]:
        log.append("do1")
        yield lambda: log.append("undo1")
        log.append("do2")
        yield lambda: log.append("undo2")
    return _gen()


d = ctx.effect(_cb)
assert log == ["do1", "do2"]
d()
assert log == ["do1", "do2", "undo2", "undo1"]
d()  # second fire is a no-op
assert log == ["do1", "do2", "undo2", "undo1"]

# guard trips mid-iteration: in-flight step finishes, later steps never run
armed = {"on": True}
started: list[str] = []


def _cb2() -> object:
    def _gen() -> Iterator[Callable[[], None]]:
        started.append("a")
        yield lambda: started.append("ua")
        started.append("b")
        armed["on"] = False
        yield lambda: started.append("ub")
        started.append("c")  # never reached: guard trips at the boundary
        yield lambda: started.append("uc")
    return _gen()


rec = execute(_cb2, lambda: armed["on"])
rec()
assert started == ["a", "b", "ub", "ua"], started

# Alg 2/6: get/set + proxy access
c = Context()
assert c.get("vcc") is None
c.set("vcc", 9)
assert c.get("vcc") == 9
try:
    c["vcc"]
    raise AssertionError("proxy should reject undeclared")
except UndeclaredAccess:
    pass

# fiber lifecycle: inactive until satisfied, unloads on withdrawal
root = Context()
root.set("db", "sqlite")
seen: list[str] = []
comp_events: list[str] = []


def _mk() -> Component:
    m = Component("w")
    orig_mount = m.mount

    def _mount(ctx: Context, *a: object, **k: object) -> None:
        orig_mount(ctx)
        seen.append("mounted")

    def _unmount(ctx: Context) -> None:
        comp_events.append("down")
    m.mount = _mount  # type: ignore[method-assign]
    m.unmount = _unmount  # type: ignore[method-assign]
    return m


f = Fiber(root, ("db",), lambda fctx: (_ for _ in ()).throw(AssertionError("unused")))
# manual fiber via Loader path instead: entry with inject satisfied by set()
from ocdcircuit.core import Loader
ld = Loader(root)


def _fac() -> Component:
    return _mk()


ent = Entry("w", _fac, url="w", inject=("db",))
root._fibers.clear()
fib = ld._spawn(ent)
assert fib.state == Fiber.ACTIVE, fib.state
assert fib.ctx["db"] == "sqlite"  # proxy reads committed view
root.unset("db")
root.notify(["db"])
assert fib.state == Fiber.INACTIVE, fib.state
try:
    fib.ctx["db"]
    raise AssertionError("should be inactive")
except InactiveAccess:
    pass

# reactive: provider swap reactivates dependents (provider uid digest)
root2 = Context()
ld2 = Loader(root2)
made: list[str] = []


def _db1() -> Component:
    m = Component("db1")
    orig = m.mount

    def _m(ctx: Context, *a: object, **k: object) -> None:
        orig(ctx)
        ctx.set("db", "one")
    m.mount = _m  # type: ignore[method-assign]
    made.append("db1")
    return m


def _app() -> Component:
    m = Component("app")
    orig = m.mount

    def _m(ctx: Context, *a: object, **k: object) -> None:
        orig(ctx)
    m.mount = _m  # type: ignore[method-assign]
    made.append("app")
    return m


ld2.declare([{"id": "db", "factory": _db1, "url": "db"},
             {"id": "app", "factory": _app, "url": "app", "inject": ("db",)}])
app_fib = ld2.entries["app"].fiber
assert app_fib is not None and app_fib.state == Fiber.ACTIVE
assert app_fib.ctx["db"] == "one"
# disable provider → dependent drains first, then provider
ld2.declare([{"id": "db", "factory": _db1, "url": "db", "disabled": True},
             {"id": "app", "factory": _app, "url": "app", "inject": ("db",)}])
_app_fib2 = ld2.entries["app"].fiber
assert _app_fib2 is not None and _app_fib2.state == Fiber.INACTIVE

# isolate: derived scope, independent binding, implicit recovery
iso = Context()
iso.set("clk", "a")
ch = iso.isolate("clk")
ch.set("clk", "b")
assert iso.get("clk") == "a" and ch.get("clk") == "b"
assert len(iso._undos) == 1  # isolate pushes no inverse; discard = recover

# intercept is consulted at read time: hidden masks the subtree until
# the child re-exposes (child table takes priority, paper §5.1.2)
iso.intercept("clk", {"hidden": True})
assert iso.get("clk") is None and ch.get("clk") is None
ch.intercept("clk", {"hidden": False})  # child re-exposes its own binding
assert ch.get("clk") == "b" and iso.get("clk") is None

# ctx.use: O-Insert tracked in parent; undo cascades to children
prt = Context()
events: list[str] = []


def _child_apply(fctx: Context) -> object:
    events.append("child-up")
    return lambda: events.append("child-down")


child = prt.use((), _child_apply)
assert child.state == Fiber.ACTIVE and child.uid in prt.registry
uid = child.uid
prt.undo()  # revert the O-Insert: retire + O-Remove (uid cleared)
assert child.state == Fiber.INACTIVE and uid not in prt.registry
assert events == ["child-up", "child-down"]

# board fiber chain is journal-backed: rollback trims it (no stale
# replays on unload), unload still reverts all domain state
from ocdcircuit import agent as _agent
_bb = _agent.loads("board t 40x30 2L\npart R1 R0805 10k\npart C1 C0805 100n\n"
                   "net N :: R1.1 <--> C1.1\n", base="boards")
_bb.place(seeds=1, iters=10)
_snap = _bb.ctx.snapshot()
_bb.route_board()
_bb.ctx.rollback(_snap)  # route emits rolled back AND trimmed
assert all(d <= _snap for d, _ in _bb._chain), "stale chain survives rollback"
assert [d for d, _ in _bb._chain] == sorted(d for d, _ in _bb._chain)
_bb.route_board()  # re-route so unload has something to revert
assert len(_bb.parts) == 2
_bb._fiber.retire()
_bb._fiber._insert()
assert len(_bb.parts) == 0, "board unload must revert domain state"
# load-path state unloads too (comments/meta/blocks/includes/owners are
# written outside emit — the load journal covers them, paper Alg 4)
_lb2 = _agent.loads("# hi\nboard t 20x10\nmeta rev A\npart R1 R0805 1k\n"
                    "net N :: R1.1 <--> R1.2\n", base="boards")
_lb2._fiber.retire()
_lb2._fiber._insert()
assert (len(_lb2.parts), len(_lb2.nets), _lb2.comments, _lb2.meta,
        _lb2.constraints) == (0, 0, [], {}, []), "load state survives unload"
# include merge unloads: owned parts, joined nets, use provenance all revert
_ib2 = _agent.loads("board t 40x30 2L\nuse psu.ocd as P\npart R1 R0805 1k\n"
                    "net N :: R1.1 <--> R1.2\n", base="boards")
assert "P_J1" in _ib2.parts and _ib2.includes
_ib2._fiber.retire()
_ib2._fiber._insert()
assert (len(_ib2.parts), len(_ib2.nets), _ib2.includes,
        _ib2.constraints) == (0, 0, [], []), "include survives unload"
# the block/instance parser stamps into a scratch board, which is an owner:
# its parse effects must not outlive their use, so it is unloaded (retire +
# O-Remove) once the parts are copied into the parent
import ocdcircuit.circuit as _circuit

_made: list[_circuit.Board] = []
_real_board = _circuit.Board


def _rec(*a: object, **k: object) -> _circuit.Board:
    b = _real_board(*a, **k)  # type: ignore[arg-type]
    _made.append(b)
    return b


setattr(_circuit, "Board", _rec)  # the parser imports Board from this module
try:
    _ib3 = _agent.loads("board t 20x10 2L\nblock blk\npart R1 R0805 1k\n"
                        "net N :: R1.1 R1.2\nend\ninstance blk as Z1\n", base="boards")
finally:
    setattr(_circuit, "Board", _real_board)
_scratch = [m for m in _made if m is not _ib3]
assert _scratch, "the scratch board was not recorded"
for _m in _scratch:
    assert _m._fiber.state == Fiber.INACTIVE, _m._fiber.state
    assert not _m.parts, sorted(_m.parts)
assert sorted(_ib3.parts) == ["Z1_R1"] and _ib3._fiber.state == Fiber.ACTIVE

# coarse temp constraint is a tracked effect: removal is undoable, a second
# undo still clears traces (no out-of-band state surgery, paper Alg 1)
_rb2 = _agent.loads("board t 40x30 2L\npart R1 R0805 1k\npart C1 C0805 100n\n"
                    "net N :: R1.1 <--> C1.1\n", base="boards")
_rb2.route_board("coarse")
assert not [c for c in _rb2.constraints if c.get("t") == "route-grid"]
_rb2.ctx.undo(2)
assert len(_rb2.traces) == 0, "coarse undo must clear traces"

# loader entries run through ctx.use: drop removes uid, re-add reissues
ldt = Context()
ld4 = Loader(ldt)
ld4.declare([{"id": "w", "factory": _fac, "url": "w"}])
wf = ld4.entries["w"].fiber
assert wf is not None and wf.uid in ldt.registry
wuid = wf.uid
ld4.declare([])
assert wuid not in ldt.registry  # O-Remove clears; stale views resolve nothing

# managed realms: local (True) is per-entry, global (str) is shared,
# both discarded when unnamed; local survives entry respawn (tagged by id)
from ocdcircuit.core import Component as _C


def _prov(val: str) -> Component:
    m = _C("p-" + val)
    orig = m.mount

    def _m(ctx: Context, *a: object, **k: object) -> None:
        orig(ctx)
        ctx.set("bus", val)
    m.mount = _m  # type: ignore[method-assign]
    return m


rctx = Context()
rld = Loader(rctx)
rld.declare([{"id": "a", "factory": lambda: _prov("A"), "url": "pa",
              "isolate": {"bus": True}},
             {"id": "b", "factory": lambda: _prov("B"), "url": "pb",
              "isolate": {"bus": True}}])
fa, fb = rld.entries["a"].fiber, rld.entries["b"].fiber
assert fa is not None and fb is not None
# independent local bindings despite the same key
assert fa.ctx.get("bus") == "A" and fb.ctx.get("bus") == "B"
assert "a" in rld._realms and "b" in rld._realms
# global realm: both entries share one binding (last writer wins, same symbol)
rld.declare([{"id": "a", "factory": lambda: _prov("A"), "url": "pa",
              "isolate": {"bus": "g"}},
             {"id": "b", "factory": lambda: _prov("B"), "url": "pb",
              "isolate": {"bus": "g"}}])
assert "a" not in rld._realms and "g" in rld._realms
rld.declare([])
assert "g" not in rld._realms and "b" not in rld._realms  # discarded

# Alg 10: multi-entry reload is transactional — failed reimport
# restores every swapped entry, never half-reloaded
hctx = Context()
hld = Loader(hctx)
calls: list[str] = []


def _ok(name: str) -> Callable[[], Component]:
    def _f() -> Component:
        m = Component(name)
        orig = m.mount

        def _m(ctx: Context, *a: object, **k: object) -> None:
            orig(ctx)
            calls.append(name)
        m.mount = _m  # type: ignore[method-assign]
        return m
    return _f


hld.declare([{"id": "x", "factory": _ok("x"), "url": "x"},
             {"id": "y", "factory": _ok("y"), "url": "y"}])
hld.reload(list(hld.entries.values()))  # clean reload, same factories
_xf = hld.entries["x"].fiber
assert _xf is not None and _xf.state == Fiber.ACTIVE
assert hld.entries["y"].fiber is not None


def _reimport(e: object) -> Callable[[], Component]:
    assert isinstance(e, Entry)
    if e.id == "y":
        raise RuntimeError("import boom")
    return _ok(e.id + "-v2")


try:
    hld.reload(list(hld.entries.values()), _reimport)
    raise AssertionError("should raise")
except RuntimeError:
    pass
# x swapped then restored; y never swapped — both ACTIVE on old factories
_xf2 = hld.entries["x"].fiber
_yf2 = hld.entries["y"].fiber
assert _xf2 is not None and _xf2.state == Fiber.ACTIVE
assert _yf2 is not None and _yf2.state == Fiber.ACTIVE
# phase-2 failure (mount raises mid-swap): the broken entry parks
# FAILED (paper §4.4) with its error recorded; the healthy swap stands —
# rolling back working code because a sibling's new code is broken would
# hide the diagnosis. Import-time failure (phase 1) stays transactional.
hld.reload(list(hld.entries.values()))  # healthy again


def _badf() -> Component:
    raise RuntimeError("mount boom")


def _reimport2(e: object) -> Callable[[], Component]:
    assert isinstance(e, Entry)
    return _badf if e.id == "y" else _ok(e.id + "-v3")


hld.reload(list(hld.entries.values()), _reimport2)
_xf3 = hld.entries["x"].fiber
assert _xf3 is not None and _xf3.state == Fiber.ACTIVE
yf = hld.entries["y"].fiber
assert yf is not None and yf.state == Fiber.FAILED
assert isinstance(yf.error, RuntimeError)
frt = Context()


def _boom() -> Component:
    m = Component("boom")
    orig = m.mount

    def _m(ctx: Context, *a: object, **k: object) -> None:
        orig(ctx)
        raise RuntimeError("bad apply")
    m.mount = _m  # type: ignore[method-assign]
    return m


fld = Loader(frt)
fld.declare([{"id": "bad", "factory": _boom, "url": "bad"}])
bad_fib = fld.entries["bad"].fiber
assert bad_fib is not None and bad_fib.state == Fiber.FAILED
assert isinstance(bad_fib.error, RuntimeError) and bad_fib.target is None
fld.declare([])  # reconcile survives the failed entry
assert "bad" not in fld.entries

# Alg 8/9: classify + stale detection
# Decline path: empty-import modules decline; dependents whose imports are
# all declined follow; modules unreachable from stashed stay unclassified.
acc, dec = classify({"a"}, {"ext"}, {"a": {"b", "c"}, "b": {"c"}, "c": set(), "z": {"ext"}})
assert acc == {"a"} and dec == {"b", "c", "ext"}, (acc, dec)
assert "z" not in acc | dec, (acc, dec)
# Accept path: a module is accepted once any import is already accepted.
acc2, dec2 = classify({"a"}, set(), {"a": {"b"}, "b": {"a"}})
assert acc2 == {"a", "b"} and dec2 == set(), (acc2, dec2)
st = stale_entries([Entry("e1", _fac, url="m1"), Entry("e2", _fac, url="m2")],
                   {"m1"}, set(), lambda u: {"m1"} if u == "m1" else set())
assert [e.id for e in st] == ["e1"]

# Loader.reload is the transactional path (covered above: phase-1 and
# phase-2 failures); the legacy single-entry remount is gone.

# loader fuzz: random declare/reload/retire interleavings quiesce —
# registry matches live fibers, realms drain, no FAILED, no dup uids
import random as _lrng
for _ls in (11, 77):
    _lr = _lrng.Random(_ls)
    _lc = Context()
    _ld = Loader(_lc)

    def _mkf(tag: str, key: str | None = None) -> Callable[[], Component]:
        def _f() -> Component:
            m = Component(f"{tag}")
            orig = m.mount

            def _m(ctx: Context, *a: object, **k: object) -> None:
                orig(ctx)
                if key is not None:
                    ctx.set(key, tag)
            m.mount = _m  # type: ignore[method-assign]
            return m
        return _f

    _ids = ["a", "b", "c"]
    for _i in range(40):
        _specs = []
        for _eid in _ids:
            if _lr.random() < 0.7:
                _iso: dict[str, object] | None = None
                _r = _lr.random()
                if _r < 0.25:
                    _iso = {"bus": True}
                elif _r < 0.5:
                    _iso = {"bus": "g"}
                _specs.append({"id": _eid, "factory": _mkf(_eid, "bus"),
                               "url": _eid, "isolate": _iso,
                               "disabled": _lr.random() < 0.2})
        _ld.declare(_specs)
        if _lr.random() < 0.3 and _ld.entries:
            _ld.reload(list(_ld.entries.values()))
    _ld.declare([{"id": _eid, "factory": _mkf(_eid, "bus"), "url": _eid}
                 for _eid in _ids])  # converge: all enabled, no scopes
    _live = [f for f in _lc._all_fibers()]
    assert len({f.uid for f in _live}) == len(_live), "dup uid"
    assert set(_lc.registry) == {f.uid for f in _live}, "registry drift"
    assert not _ld._realms, f"realm leak: {_ld._realms}"
    assert all(f.state == Fiber.ACTIVE for f in _live), \
        [f.state for f in _live]
    assert all(f.ctx.get("bus") is not None for f in _live)

# --- CORDIS review: every inverse reverts what its effect did ---

# set/unset inverses notify: a restored binding must re-reconcile dependents,
# a reverted one must deactivate them (Alg 2 + Alg 3 are one step).
_nc = Context()
_nlog: list[str] = []

def _note(c: Context) -> object:
    _nlog.append("load")
    return lambda: _nlog.append("unload")

_nf = _nc.use(("k",), _note)
_nsnap = _nc.snapshot()
_nc.set("k", 1)
assert _nf.state == Fiber.ACTIVE
_nc.rollback(_nsnap)
assert _nf.state == Fiber.INACTIVE, "set inverse must deactivate dependents"

_uc = Context()
_uc.set("k", 1)
_uf = _uc.use(("k",), lambda c: lambda: None)
assert _uf.state == Fiber.ACTIVE
_usnap = _uc.snapshot()
_uc.unset("k")
assert _uf.state == Fiber.INACTIVE
_uc.rollback(_usnap)
assert _uf.state == Fiber.ACTIVE, "unset inverse must re-activate dependents"
assert _uc.get("k") == 1

# a raising apply parks FAILED with no live effects left behind
_pc = Context()
_plog: list[str] = []

def _on() -> object:
    _plog.append("on")
    return lambda: _plog.append("off")

def _half(c: Context) -> object:
    c.effect(_on)
    raise RuntimeError("half-applied")

_pc.set("k", 1)
_pf = _pc.use(("k",), _half)
assert _pf.state == Fiber.FAILED
assert _plog == ["on", "off"], f"partial apply leaked effects: {_plog}"

# registry failure memory dies with the entry it judged
_rg = Registry()
_rg._add("zz", "boom", object())
_rg.fail("zz", "boom", "RuntimeError: x")
_rg._drop("zz", "boom")
_rg._add("zz", "boom", object())
assert _rg.get("zz", "boom") is not None, "stale failure outlived the entry"

# UI slot: dispose clears the crash verdict, so a reload renders again
_slots = UiSlots()
_sd = _slots.register("toolbar", "x", lambda st: 1 / 0)
assert _slots.render("toolbar", None) == ""
_sd()
_slots.register("toolbar", "x", lambda st: "<ok>")
assert _slots.render("toolbar", None) == "<ok>", "crash verdict outlived entry"

# loader intercept converges on the spec: a dropped key leaves
class _Nop(Component):
    def mount(self, ctx: Context, *a: object, **k: object) -> None:
        pass

    def unmount(self, ctx: Context) -> None:
        pass

def _nop_factory() -> Component:
    return _Nop("nop")

_ic = Context()
_il = Loader(_ic)
_il.declare([{"id": "a", "factory": _nop_factory,
              "intercept": {"svc": {"hidden": True}}}])
_ie = _il.entries["a"]
assert _ie.fiber is not None and _ie.fiber.ctx._intercept == {"svc": {"hidden": True}}
_il.declare([{"id": "a", "factory": _nop_factory}])
assert _ie.fiber.ctx._intercept == {}, "dropped intercept survived its removal"
_il.declare([{"id": "a", "factory": _nop_factory,
              "intercept": {"svc": {"other": 1}}}])
assert _ie.fiber.ctx._intercept == {"svc": {"other": 1}}

print("CORE PAPER OK")
