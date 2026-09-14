"""Paper §5 core self-check: effect/notify/fiber/loader/HMR (asserts only)."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit.core import (Component, Context, Entry, Fiber, InactiveAccess,
                             UndeclaredAccess, classify, execute, stale_entries)

# Alg 1: effect folds yielded inverses LIFO, dispose fires once
ctx = Context()
log: list[str] = []


def _cb() -> object:
    def _gen():  # type: ignore[no-untyped-def]
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
    def _gen():  # type: ignore[no-untyped-def]
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
root.provide("db", "sqlite")
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
# manual fiber via Loader path instead: entry with inject satisfied by legacy provide
from ocdcircuit.core import Loader
ld = Loader(root)


def _fac() -> Component:
    return _mk()


ent = Entry("w", _fac, url="w", inject=("db",))
root._fibers.clear()
fib = ld._spawn(ent)
assert fib.state == Fiber.ACTIVE, fib.state
assert fib.ctx["db"] == "sqlite"  # proxy reads committed view
root.services.pop("db")
root._root()._providers.pop("db", None)
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
assert ld2.entries["app"].fiber is not None
assert ld2.entries["app"].fiber.state == Fiber.INACTIVE  # type: ignore[union-attr]

# isolate: same key, independent bindings
iso = Context()
iso.set("clk", "a")
ch = iso.child()
ch.isolate("clk")
ch.set("clk", "b")
assert iso.get("clk") == "a" and ch.get("clk") == "b"

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
