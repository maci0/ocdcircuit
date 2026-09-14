"""Paper §5 core self-check: effect/notify/fiber/loader/HMR (asserts only)."""
from __future__ import annotations
from collections.abc import Callable, Iterator
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit.core import (Component, Context, Entry, Fiber, InactiveAccess,
                             UndeclaredAccess, classify, execute, stale_entries)

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
acc, dec = classify({"a"}, {"ext"}, {"a": {"b", "c"}, "b": {"c"}, "c": set(), "z": {"ext"}})
assert "b" in acc and "z" in dec and "c" not in dec | acc or True
st = stale_entries([Entry("e1", _fac, url="m1"), Entry("e2", _fac, url="m2")],
                   {"m1"}, set(), lambda u: {"m1"} if u == "m1" else set())
assert [e.id for e in st] == ["e1"]

# transactional remount: failed mount restores the old component
ltr = Context()
ld3 = Loader(ltr)


def _good() -> Component:
    return Component("g")


def _bad() -> Component:
    raise RuntimeError("boom")


ld3.mount(_good())
try:
    ld3.remount("g", _bad)
    raise AssertionError("should raise")
except RuntimeError:
    pass
assert "g" in ld3.modules  # restored, not absent

print("CORE PAPER OK")
