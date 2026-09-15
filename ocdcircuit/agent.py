"""Agent-first API: structured patches (undoable) + NL constraint fallback."""
from __future__ import annotations
from .util import as_float as _f, as_int as _i, as_str as _s
import gc
import json
import math
import os
import re
import shlex
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from .types import Constraint, Undo


def _q(v: object) -> str:
    """Attr value for dumps: quote iff it carries whitespace/quotes so
    the line still reloads (shlex.split on parse)."""
    s = str(v)
    if any(ch.isspace() or ch in "\"'" for ch in s):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _split(tail: str) -> list[str]:
    """shlex-split an attr-bearing segment (inverse of _q quoting)."""
    return shlex.split(tail)

if TYPE_CHECKING:
    from .circuit import Board

ErrFn = Callable[[object], ValueError]


def apply_patch(board: Board, ops: list[dict[str, object]]) -> int:
    """Apply ops atomically: a mid-list failure rolls everything back."""
    snap = board.ctx.snapshot()
    try:
        return _apply_patch_inner(board, ops)
    except Exception:
        board.ctx.rollback(snap)
        raise


def _apply_patch_inner(board: Board, ops: list[dict[str, object]]) -> int:
    n = 0
    for op in ops:
        k = op.get("op")
        if k == "add_part":
            attrs = op.get("attrs", None)
            assert attrs is None or isinstance(attrs, dict)
            board.add_part(str(op["ref"]), str(op["fp"]), str(op.get("value", "")),
                           _opt_float(op.get("x")), _opt_float(op.get("y")),
                           attrs={str(k): str(v) for k, v in attrs.items()} if attrs else None)
        elif k == "move_part":
            board.move_part(_s(op["ref"]), _f(op["x"]), _f(op["y"]))
        elif k == "remove_part":
            board.remove_part(str(op["ref"]))
        elif k == "connect":
            board.connect(_s(op["net"]), _s(op["ref"]), _s(op["pin"]))
            nattrs = op.get("attrs", None)
            assert nattrs is None or isinstance(nattrs, dict)
            if nattrs:
                net = board.nets[_s(op["net"])]
                new = {str(k): str(v) for k, v in nattrs.items()}
                old = {k: net.attrs.get(k) for k in new}

                def _do(_n: str = _s(op["net"]), _w: dict[str, str] = new) -> None:
                    board.nets[_n].attrs.update(_w)

                def _undo(_n: str = _s(op["net"]),
                          _o: dict[str, str | None] = old) -> None:
                    for k, v in _o.items():
                        if v is None:
                            board.nets[_n].attrs.pop(k, None)
                        else:
                            board.nets[_n].attrs[k] = v

                board.emit(_do, _undo)
        elif k == "constrain":
            c = op["c"]
            assert isinstance(c, dict)
            board.constrain(c)
        elif k == "set_board":
            board.set_board(_f(op["w"]), _f(op["h"]))
        elif k == "route":
            board.route_board()
        elif k == "optimize":
            board.place(seeds=_i(op.get("seeds"), 4),
                        iters=_i(op.get("iters"), 400), seed=_i(op.get("seed"), 0))
        elif k == "check":
            op["result"] = board.check(
                None if op.get("key") is None else str(op.get("key")))
        elif k == "export":
            args = op.get("args", {})
            assert isinstance(args, dict)
            op["result"] = board.export(
                None if op.get("key") is None else str(op.get("key")), **args)
        elif k == "render":
            args = op.get("args", {})
            assert isinstance(args, dict)
            op["result"] = board.render(
                None if op.get("key") is None else str(op.get("key")), **args)
        elif k == "use":
            board.use(str(op["kind"]), str(op["key"]))
        elif k == "plugin":
            from .core import Plugin, Registry
            reg = board.ctx.get("plugins")
            assert isinstance(reg, Registry)
            args = op.get("args", {})
            assert isinstance(args, dict)
            inst = reg.get(_s(op["kind"]), _s(op["key"]))
            assert isinstance(inst, Plugin)
            inst.run(board, **args)
        else:
            raise ValueError(f"unknown op {k}")
        n += 1
    return n


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    out = _f(v)
    if not math.isfinite(out):
        raise ValueError(f"non-finite value {v!r}")
    return out


def parse_constraint(text: str) -> Constraint | None:
    t = text.strip()
    m = re.match(r"keep (\w+) near (\w+)(?: (\d+(?:\.\d+)?))?$", t, re.I)
    if m:
        return {"t": "near", "a": m.group(1), "b": m.group(2),
                "w": float(m.group(3) or 2.0)}
    m = re.match(r"fix (\w+) at (-?[\d.]+) (-?[\d.]+)$", t, re.I)
    if m:
        return {"t": "fixed", "ref": m.group(1), "x": float(m.group(2)), "y": float(m.group(3))}
    m = re.match(r"route (\w+) on (top|bottom|\d+)$", t, re.I)
    if m:
        layer = {"top": 0, "bottom": 1}[m.group(2).lower()] if m.group(2).lower() in ("top", "bottom") else int(m.group(2))
        return {"t": "layer", "net": m.group(1), "layer": layer}
    m = re.match(r"trace (\w+) ([\d.]+)$", t, re.I)
    if m:
        return {"t": "width", "net": m.group(1), "width": float(m.group(2))}
    m = re.match(r"route-grid ([\d.]+)$", t, re.I)
    if m:
        return {"t": "route-grid", "grid": float(m.group(1))}
    m = re.match(r"route-penalty bend ([\d.]+) via ([\d.]+)$", t, re.I)
    if m:
        return {"t": "route-penalty", "bend": float(m.group(1)),
                "via": float(m.group(2))}
    m = re.match(r"power ([\w ]+)$", t, re.I)
    if m:
        return {"t": "power", "nets": m.group(1).split()}
    m = re.match(r"class (\w+)((?:\s+\w+=(?:\"[^\"]*\"|[\w.]+))*)$", t, re.I)
    if m:
        cc: Constraint = {"t": "class", "name": m.group(1)}
        for tok in _split(m.group(2)):
            k, _, v = tok.partition("=")
            try:
                cc[k] = float(v)
            except ValueError:
                cc[k] = v
        return cc
    m = re.match(r"match ([\w ]+)$", t, re.I)
    if m:
        return {"t": "match", "nets": m.group(1).split()}
    m = re.match(r"diff (\w+) (\w+) gap ([\d.]+)$", t, re.I)
    if m:
        return {"t": "diff", "p": m.group(1), "n": m.group(2), "gap": float(m.group(3))}
    m = re.match(r"silk ([0-3])$", t, re.I)
    if m:
        return {"t": "silk", "level": int(m.group(1))}
    m = re.match(r"nc ((?:\w+\.\w+ ?)+)$", t, re.I)
    if m:
        return {"t": "nc", "pins": m.group(1).split()}
    m = re.match(r"pour (\w+) on (top|bottom|\d+)$", t, re.I)
    if m:
        layer = {"top": 0, "bottom": 1}[m.group(2).lower()] if m.group(2).lower() in ("top", "bottom") else int(m.group(2))
        return {"t": "pour", "net": m.group(1), "layer": layer}
    m = re.match(r"keepout ([\d.\-]+) ([\d.\-]+) (?:([\d.]+)x([\d.]+)|d([\d.]+))(?: on ([\w,]+))?$", t, re.I)
    if m:
        kd: Constraint = {"t": "keepout", "x": float(m.group(1)), "y": float(m.group(2)),
                          "layers": m.group(6).split(",") if m.group(6) else []}
        if m.group(5) is not None:
            kd["d"] = float(m.group(5))  # round: keepout x y dN
        else:
            kd["w"], kd["h"] = float(m.group(3)), float(m.group(4))
        return kd
    # deadzone follows a part: keepout near REF [dN | WxH] (fiducials et al.)
    m = re.match(r"keepout near (\S+)(?: (?:([\d.]+)x([\d.]+)|d([\d.]+)))?(?: on ([\w,]+))?$", t, re.I)
    if m:
        z: Constraint = {"t": "keepout", "ref": m.group(1),
                         "layers": m.group(5).split(",") if m.group(5) else []}
        if m.group(4) is not None:
            z["d"] = float(m.group(4))
        elif m.group(2) is not None:
            z["w"], z["h"] = float(m.group(2)), float(m.group(3))
        else:
            z["d"] = 4.0  # fiducial default: 4mm clear circle
        return z
    m = re.match(r"cutout ([\d.\-]+) ([\d.\-]+) ([\d.]+)x([\d.]+)$", t, re.I)
    if m:
        return {"t": "cutout", "x": float(m.group(1)), "y": float(m.group(2)),
                "w": float(m.group(3)), "h": float(m.group(4))}
    m = re.match(r"hole ([\d.\-]+) ([\d.\-]+) ([\d.]+)$", t, re.I)
    if m:
        return {"t": "hole", "x": float(m.group(1)), "y": float(m.group(2)),
                "d": float(m.group(3))}
    m = re.match(r"bend ([\d.\-]+) ([\d.\-]+) ([\d.]+)x([\d.]+) r([\d.]+)( static)?$", t, re.I)
    if m:
        return {"t": "bend", "x": float(m.group(1)), "y": float(m.group(2)),
                "w": float(m.group(3)), "h": float(m.group(4)),
                "r": float(m.group(5)), "dynamic": not m.group(6)}
    m = re.match(r"stiffener ([\d.\-]+) ([\d.\-]+) ([\d.]+)x([\d.]+) (PI|FR4|steel) ([\d.]+)$",
                 t, re.I)
    if m:
        return {"t": "stiffener", "x": float(m.group(1)), "y": float(m.group(2)),
                "w": float(m.group(3)), "h": float(m.group(4)),
                "mat": m.group(5), "th": float(m.group(6))}
    m = re.match(r"sim\s+vcc\s+(\w+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?$", t, re.I)
    if m:
        c: Constraint = {"t": "sim", "kind": "vcc", "net": m.group(1), "v0": float(m.group(2))}
        if m.group(3) is not None:
            c["v1"] = float(m.group(3))
        return c
    m = re.match(r"sim\s+sine\s+(\w+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": "sine", "net": m.group(1),
                "off": float(m.group(2)), "amp": float(m.group(3)), "freq": float(m.group(4))}
    m = re.match(r"sim\s+isrc\s+(\w+)\s+([\d.]+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": "isrc", "net": m.group(1), "value": float(m.group(2))}
    m = re.match(r"sim\s+tran\s+([\d.]+)\s+(\d+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": "tran", "t_end": float(m.group(1)), "steps": int(m.group(2))}
    m = re.match(r"sim\s+probe\s+(\w+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": "probe", "net": m.group(1)}
    m = re.match(r"sim\s+clk\s+(\w+)\s+([\d.]+)(?:\s+([\d.]+))?$", t, re.I)
    if m:
        return {"t": "sim", "kind": "clk", "net": m.group(1),
                "period": float(m.group(2)),
                "duty": float(m.group(3)) if m.group(3) else 0.5}
    m = re.match(r"sim\s+expect\s+(\w+)\s*(==|!=|<=|>=|<|>|~)\s*(\S+)(?:\s+tol\s+(\S+))?$", t, re.I)
    if m:
        e: Constraint = {"t": "sim", "kind": "expect", "net": m.group(1),
                         "op": m.group(2), "value": m.group(3)}
        if m.group(4) is not None:
            e["tol"] = m.group(4)
        return e
    m = re.match(r"sim\s+([rcldq])\s+(\w+)\s+(\S+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": m.group(1), "ref": m.group(2), "value": m.group(3)}
    m = re.match(r"sim\s+op\s+(\w+)\s+(\S+)\s+([\d ]+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": "op", "ref": m.group(1),
                "value": f"{m.group(2)} {m.group(3)}"}
    m = re.match(r"sim\s+lib\s+(\S+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": "lib", "path": m.group(1)}
    m = re.match(r"sim\s+ac\s+(\S+)\s+(\S+)(?:\s+(\d+))?$", t, re.I)
    if m:
        from .sim import parse_value as _pv
        try:
            f0, f1 = _pv(m.group(1)), _pv(m.group(2))
        except ValueError:
            return None
        ac: Constraint = {"t": "sim", "kind": "ac", "f0": f0, "f1": f1}
        if m.group(3) is not None:
            ac["npts"] = int(m.group(3))
        return ac
    m = re.match(r"board ([\d.]+) ?x ([\d.]+)$", t, re.I)
    if m:
        return {"t": "board", "w": float(m.group(1)), "h": float(m.group(2))}
    return None


def dumps(board: Board) -> str:
    """The human-readable circuit language (.ocd): one fact per line.
    Python (Board API) builds it, JSON is the wire IR, this is what
    humans read/write. Round-trips through loads(): boards with includes
    dump `use` lines + local content only; reload re-merges identically.
    Comments survive the round trip (they head the file)."""
    owned = {p.ref for p in board.parts.values() if p.owner}
    L = list(board.comments)  # `#` lines first, exactly as they were read
    L.append(f"board {board.name} {board.width:g}x{board.height:g} {board.layers}L")
    for k in sorted(board.meta):
        L.append(f"meta {k} {board.meta[k]}")
    for bname in sorted(board.blocks):
        if bname in board.block_src:
            continue  # imported via `use` — comes back on reload
        bl = board.blocks[bname]
        L.append(f"block {bname}" + (" ports " + " ".join(bl.ports) if bl.ports else ""))
        L.extend(f"  {ln}" for ln in bl.lines)
        L.append("end")
    for inc in board.includes:
        L.append(f"use {inc['path']}" + (f" as {inc['prefix']}" if inc.get("prefix") else "") +
                 (f" join {' '.join(cast(list[str], inc['join']))}" if inc.get("join") else ""))
    for ins in board.instances:
        L.append(f"instance {ins['block']} as {ins['prefix']}" +
                 (f" join {' '.join(cast(list[str], ins['join']))}" if ins.get("join") else ""))
    for p in sorted(board.parts.values(), key=lambda q: q.ref):
        if p.owner:
            continue  # owned by an include/instance — dumped as use/instance
        # x=/y= are input syntax (≡ fix); placement persists via `fix`
        # lines below, so they never emit here (a reload would freeze
        # unpinned parts the API merely positioned).
        attrs = "".join(f" {k}={_q(v)}" for k, v in sorted(p.attrs.items())
                        if k not in ("x", "y"))
        L.append(f"part {p.ref} {p.fp}{(' ' + p.value) if p.value else ''}{attrs}")
    # fp/sym lines go directly after the board header: they must exist before
    # parts use them, and the parser wants the header first (comments head the
    # file, so index 1 is no longer "after the header" once comments exist).
    fps = [f"fp {board.fp_src[name]}" for name in sorted(board.custom_fp) if name in board.fp_src]
    syms = [f"sym {board.sym_src[name]}" for name in sorted(board.custom_sym) if name in board.sym_src]
    at = next((i for i, ln in enumerate(L) if ln.startswith("board ")), 0) + 1
    L[at:at] = fps + syms
    # fold layer/width constraints onto the net line (first wins on dupes —
    # but conflicting dupes stay unfolded as route/trace lines, otherwise
    # dumps would flip last-wins runtime resolution)
    lay: dict[str, object] = {}
    wid: dict[str, object] = {}
    lay_bad: set[str] = set()
    wid_bad: set[str] = set()
    for c in board.constraints:
        if c.get("t") == "layer":
            n = str(c["net"])
            if n in lay and lay[n] != c["layer"]:
                lay_bad.add(n)
            else:
                lay[n] = c["layer"]
        if c.get("t") == "width":
            n = str(c["net"])
            if n in wid and wid[n] != c["width"]:
                wid_bad.add(n)
            else:
                wid[n] = c["width"]
    for n in sorted(board.nets):
        net = board.nets[n]
        pins = sorted((r, str(pin)) for r, pin in net.pins if r not in owned)
        if not pins and any(net.pins):
            continue  # fully owned by an include — comes back via `use`
        attrs = "".join(f" {k}={_q(v)}" for k, v in sorted(net.attrs.items()))
        # constraints win over runtime assignment: net.layer/width are
        # solver scratch (assign_layers), the constraint is the source.
        # Otherwise save-after-solve silently rewrites route intent.
        layer = None if n in lay_bad else (lay.get(n) if n in lay else net.layer)
        w0 = None if n in wid_bad else (wid.get(n) if n in wid else net.width)
        # scratch emits only when reloadable: out-of-range layers or
        # non-positive widths (direct writes, never solver output) would
        # come back as constraints and crash routers — drop them here.
        if layer is not None and not (isinstance(layer, int) and 0 <= layer < board.layers):
            layer = None
        width: object = w0
        if layer is not None:
            attrs += f" L{layer}"
        if (isinstance(width, (int, float)) and not isinstance(width, bool)
                and math.isfinite(width) and width != 0.3 and width > 0):
            attrs += f" w{width:g}"
        L.append(f"{n}{attrs} :: " + " <--> ".join(f"{r}.{pin}" for r, pin in pins))
    seen_power: list[list[str]] = []
    for c in board.constraints:
        t = c.get("t")
        if t in ("near", "fixed", "near-group") and c.get("owner"):
            continue  # owned by an include
        if t == "near":
            L.append(f"keep {c['a']} near {c['b']} {_f(c.get('w', 2)):g}")
        elif t == "fixed":
            # placement persists only here (x=/y= never emit on part lines)
            L.append(f"fix {c['ref']} at {_f(c['x']):g} {_f(c['y']):g}")
        elif t == "layer" and str(c["net"]) in board.nets and str(c["net"]) not in lay_bad:
            continue  # folded onto the net line above (or via `use`)
        elif t == "layer" and c.get("owner"):
            continue  # stamped by an instance — comes back on re-stamp
        elif t == "layer":
            L.append(f"route {c['net']} on {c['layer']}")
        elif t == "width" and str(c["net"]) in board.nets and str(c["net"]) not in wid_bad:
            continue  # folded onto the net line above (or via `use`)
        elif t == "width" and c.get("owner"):
            continue  # stamped by an instance — comes back on re-stamp
        elif t == "width":
            L.append(f"trace {c['net']} {_f(c['width']):g}")
        elif t == "route-grid":
            L.append(f"route-grid {_f(c['grid']):g}")
        elif t == "route-penalty":
            L.append(f"route-penalty bend {_f(c['bend']):g} via {_f(c['via']):g}")
        elif t == "silk":
            L.append(f"silk {c['level']}")
        elif t == "nc":
            L.append(f"nc {' '.join(cast(list[str], c['pins']))}")
        elif t == "pour" and c.get("owner"):
            continue  # stamped by an instance — comes back on re-stamp
        elif t == "pour":
            L.append(f"pour {c['net']} on {c['layer']}")
        elif t == "keepout":
            ly = f" on {','.join(cast(list[str], c['layers']))}" if c.get("layers") else ""
            if c.get("ref") is not None:
                sh = f"d{_f(c['d']):g}" if c.get("d") is not None else f"{_f(c['w']):g}x{_f(c['h']):g}"
                L.append(f"keepout near {c['ref']} {sh}{ly}")
            elif c.get("d") is not None:
                L.append(f"keepout {_f(c['x']):g} {_f(c['y']):g} d{_f(c['d']):g}{ly}")
            else:
                L.append(f"keepout {_f(c['x']):g} {_f(c['y']):g} {_f(c['w']):g}x{_f(c['h']):g}{ly}")
        elif t == "cutout":
            L.append(f"cutout {_f(c['x']):g} {_f(c['y']):g} {_f(c['w']):g}x{_f(c['h']):g}")
        elif t == "hole":
            L.append(f"hole {_f(c['x']):g} {_f(c['y']):g} {_f(c['d']):g}")
        elif t == "bend":
            dyn = "" if c.get("dynamic") else " static"
            L.append(f"bend {_f(c['x']):g} {_f(c['y']):g} {_f(c['w']):g}x{_f(c['h']):g}"
                     f" r{_f(c['r']):g}{dyn}")
        elif t == "stiffener":
            L.append(f"stiffener {_f(c['x']):g} {_f(c['y']):g} {_f(c['w']):g}x{_f(c['h']):g}"
                     f" {c['mat']} {_f(c['th']):g}")
        elif t == "sim":
            L.append(_dump_sim(c))
        elif t == "match":
            L.append(f"match {' '.join(cast(list[str], c['nets']))}")
        elif t == "diff":
            L.append(f"diff {c['p']} {c['n']} gap {_f(c['gap']):g}")
        elif t == "power":
            if c.get("owner"):
                continue  # contributed by an include — comes back via `use`
            nets = sorted(cast(list[str], c["nets"]))
            if nets in seen_power:
                continue
            seen_power.append(nets)
            L.append(f"power {' '.join(cast(list[str], c['nets']))}")
        elif t == "class":
            rest = " ".join(f"{k}={_f(v):g}" if isinstance(v, float) else f"{k}={_q(v)}"
                            for k, v in sorted(c.items()) if k not in ("t", "name"))
            L.append(f"class {c.get('name')}{(' ' + rest) if rest else ''}")
    return "\n".join(L) + "\n"


def _dump_sim(c: Constraint) -> str:
    k = str(c.get("kind", ""))
    if k == "vcc":
        s = f"sim vcc {c['net']} {_f(c.get('v0', 0)):g}"
        if c.get("v1") is not None:
            s += f" {_f(c['v1']):g}"
        return s
    if k == "sine":
        return (f"sim sine {c['net']} {_f(c.get('off', 0)):g} "
                f"{_f(c.get('amp', 1)):g} {_f(c.get('freq', 1000)):g}")
    if k == "isrc":
        return f"sim isrc {c['net']} {_f(c.get('value', 0)):g}"
    if k == "tran":
        return f"sim tran {_f(c.get('t_end', 0.01)):g} {_i(c.get('steps'), 1000)}"
    if k == "probe":
        return f"sim probe {c['net']}"
    if k == "clk":
        s = f"sim clk {c['net']} {_f(c.get('period', 1)):g}"
        if _f(c.get('duty', 0.5)) != 0.5:
            s += f" {_f(c.get('duty')):g}"
        return s
    if k == "expect":
        s = f"sim expect {c['net']} {c.get('op', '==')} {c.get('value', '0')}"
        if c.get("tol") is not None:
            s += f" tol {c['tol']}"
        return s
    if k == "op":
        return f"sim op {c.get('ref', '')} {c.get('value', '')}"
    if k == "lib":
        return f"sim lib {c.get('path', '')}"
    if k == "ac":
        s = f"sim ac {_f(c.get('f0', 1)):g} {_f(c.get('f1', 1e6)):g}"
        if c.get("npts") is not None:
            s += f" {_i(c.get('npts'), 50)}"
        return s
    return f"sim {k} {c.get('ref', '')} {c.get('value', '')}"


class _quiet_gc:
    """Pause cyclic GC for an allocation-heavy parse, restore it after.

    A 5420-part board allocates ~680k GC-tracked objects — 28k undo entries,
    each a dict plus three closures, all kept alive by the undo stack — and the
    default gen0 threshold (every 2000 allocations) fired 469 collections
    inside one load. Measured: 1256ms with the default thresholds, 284ms with
    GC off, 331ms with the thresholds raised. Nothing here creates garbage that
    refcounting cannot free, cycles included: they wait for the next natural
    collection. Semantics are untouched — same objects, same undo stack, same
    lazy mount decisions. A caller that had GC off keeps it off."""

    def __enter__(self) -> None:
        self.armed = gc.isenabled()
        if self.armed:
            gc.disable()

    def __exit__(self, *exc: object) -> None:
        if self.armed:
            # No collect here: the board just built is *all* gen0, so an
            # explicit collect(0) walks every one of those ~680k objects and
            # cost 400ms — more than it saved. Cycles the parse left behind are
            # freed by the next natural collection, exactly as before.
            gc.enable()


def loads(text: str, base: str | os.PathLike[str] | None = None) -> Board:
    """Parse .ocd text (dumps output, comments with #). Constraints reuse
    parse_constraint — one grammar, no separate parser. Errors name the line.
    base: directory `use` paths resolve against (defaults to cwd).

    `use path [as PREFIX] [join NET...]`: include another .ocd board as a
    module (logisim-style). Child refs/nets gain PREFIX (default: child's
    board name + "_"); PREFIX_ nets listed in `join` merge into same-named
    parent nets (VCC/GND auto-join without listing). Child board size,
    layers and `fix` lines are ignored — the parent places everything.
    Cycles are an error."""
    basedir = os.path.abspath(base) if base is not None else os.getcwd()
    with _quiet_gc():
        return _loads(text, basedir, stack=(), top=True)


def _import_ref(b: Board, kw: str, line: str, base: str,
                stack: tuple[str, ...], err: ErrFn) -> None:
    """`fp`/`sym PATH`: import a footprint or symbol file into the board and
    remember the path as written (dumps must stay portable). Also used for
    lines that appeared before the board header, once the header is known."""
    toks = line.split(None, 1)
    if len(toks) != 2:
        raise err(f"want: {kw} PATH/to/part.{'fp' if kw == 'fp' else 'sym'}")
    fn = os.path.normpath(os.path.join(base, toks[1]))
    if kw == "fp":
        if fn in stack:
            raise err(f"footprint cycle: {toks[1]!r}")
        ext = os.path.splitext(fn)[1].lower()
        key = {".fp": "fp", ".kicad_mod": "kicad", ".pretty": "kicad",
               ".lbr": "eagle", ".brd": "pcb", ".json": "tscircuit"}.get(ext)
        if key is None:
            raise err(f"unknown footprint format {ext!r}")
        try:
            out = b.import_fp(key, path=fn)
        except (OSError, ValueError, KeyError, AssertionError) as e:
            raise err(e)
        names = out.get("names", [out.get("name")])
        for n in cast(list[object], names):
            if isinstance(n, str) and n in b.fp_src:
                b.fp_src[n] = toks[1]
        return
    try:
        sym = b.import_sym(path=fn)
    except (OSError, ValueError, KeyError, AssertionError) as e:
        raise err(e)
    name = sym.get("name")
    if isinstance(name, str) and name in b.sym_src:
        b.sym_src[name] = toks[1]


def _loads(text: str, base: str, stack: tuple[str, ...], top: bool = False) -> Board:
    from .circuit import Board
    b: Board | None = None
    # `#` lines are comments, not data: keep them so dumps() can re-emit them.
    # ponytail: flat list, emitted at the top — comments mid-file migrate up
    # (dumps already reorders the file). A source-map layer if position matters.
    comments: list[str] = []
    # fp/sym lines that arrive before the header: imported as soon as it does
    pending: list[tuple[str, str]] = []
    for ln, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            # a comment inside a block body belongs to the block, which keeps
            # its own lines — do not lift it to the file header
            if not (b is not None and b._block_open is not None):
                comments.append(raw.rstrip())
            continue
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        def err(msg: object) -> ValueError:
            return ValueError(f"line {ln}: {msg}: {line!r}")

        toks0 = line.split(None, 1)
        kw = toks0[0].lower() if toks0 else ""
        if b is not None and b._block_open is not None and kw != "end":
            if kw == "block":
                raise err("nested blocks not supported (flatten it)")
            if kw in ("board", "use", "fp", "instance"):
                raise err(f"{kw} not allowed inside block (keep blocks portable)")
            assert b._block_lines is not None
            b._block_lines.append(line)
            continue
        if kw == "use":
            if b is None:
                raise err("board header first")
            m = re.match(r"^use\s+(\S+)(?:\s+as\s+(\S+))?(?:\s+join\s+(.+))?$",
                         line, re.I)
            if not m:
                raise err("want: use PATH [as PREFIX] [join NET ...]")
            _include(b, m.group(1), m.group(2), m.group(3), base, stack, err)
            continue
        if kw == "board":
            m = re.match(r"^board\s+(\S+)\s+([\d.]+)x([\d.]+)(?:\s+(\d+)L)?$",
                         line, re.I)
            if m:
                if b is not None:
                    raise err("duplicate board header (one board per file)")
                b = Board(m.group(1), float(m.group(2)), float(m.group(3)),
                          int(m.group(4)) if m.group(4) else 2)
                continue
            # else: bare "board WxH" resize form → handled as constraint below
        if b is not None and pending:  # queued fp/sym lines, header now known
            for kw0, line0 in list(pending):
                pending.remove((kw0, line0))
                _import_ref(b, kw0, line0, base, stack, err)
        if b is None:
            if kw in ("fp", "sym"):
                pending.append((kw, line))  # applied once the header arrives
                continue
            raise err("board header first")
        if kw == "block":
            m = re.match(r"^block\s+(\S+)(?:\s+ports\s+(.+))?$", line, re.I)
            if not m:
                raise err("want: block NAME [ports A B ...]")
            if b._block_open is not None:
                raise err("nested blocks not supported (flatten it)")
            ports = m.group(2).split() if m.group(2) else []
            if len(set(ports)) != len(ports):
                raise err(f"duplicate port in {ports!r}")
            for p in ports:
                if not re.fullmatch(r"\w+", p):
                    raise err(f"bad port name {p!r} (want \\w+)")
            b._block_open = m.group(1)
            b._block_ports = ports
            b._block_lines = []
            continue
        if kw == "end":
            if b._block_open is None:
                raise err("end without block")
            from .circuit import Block as _Block
            name = b._block_open
            if name in b.blocks:
                raise err(f"duplicate block {name!r}")
            b.blocks[name] = _Block(name, b._block_lines or [], b._block_ports)
            b._block_open = None
            b._block_ports = []
            b._block_lines = None
            continue
        if kw == "instance":
            m = re.match(r"^instance\s+(\S+)\s+as\s+(\S+)(?:\s+join\s+(.+))?$",
                         line, re.I)
            if not m:
                raise err("want: instance BLOCK as PREFIX [join NET ...]")
            _instance(b, m.group(1), m.group(2), m.group(3), err)
            continue
        if kw in ("fp", "sym"):
            _import_ref(b, kw, line, base, stack, err)
            continue
        if kw == "part":
            _exec_part(b, line, err)
        elif kw == "meta":
            toks = line.split(None, 2)
            if len(toks) != 3 or not toks[1]:
                raise err("want: meta KEY value...")
            b.meta[toks[1]] = toks[2]
        elif kw == "net" or "::" in line:
            _exec_net(b, line, err)
        else:
            c = parse_constraint(line)
            if c is None:
                raise err("unknown statement")
            if c["t"] == "board":
                b.set_board(_f(c["w"]), _f(c["h"]))
            else:
                # raw: lint owns junk-reporting; Board.constrain validates
                b._constrain_raw(c)
    if b is None:
        raise ValueError("empty circuit")
    b.comments[:] = comments
    # load-path writes land as one journal entry on the board fiber: a
    # parsed board unloads like any other domain state (paper Alg 4 —
    # unload reverts all of it, not just the emitted parts).
    b._load_journal(log_load(b))
    if pending:  # fp/sym lines but never a board header to import them into
        raise ValueError(f"line 1: board header first: {pending[0][1]!r}")
    if top:
        _validate(b)
    return b


def log_load(b: Board) -> Undo:
    """Inverse for the load-path writes a fresh parse performed outside
    emit (comments/meta/blocks/includes/owner tags — all the merges _loads,
    _include and _instance do directly). A fresh board starts empty, so the
    inverse clears that state; the per-edit undos (parts/nets/constraints)
    already on the journal remove the rest. Returns it so the board fiber
    can journal it; loading then unloads like any edit."""

    def _undo() -> None:
        b.comments[:] = []
        b.meta.clear()
        b.blocks.clear()
        b.block_src.clear()
        b.includes[:] = []
        b.instances[:] = []
        for p in b.parts.values():
            p.owner = None

    return _undo



def _validate(b: Board) -> None:
    from .parts import pads_of
    lib = b._lib()
    for n, net in b.nets.items():
        for ref, pin in net.pins:
            if ref not in b.parts:
                raise ValueError(f"net {n}: unknown part {ref!r}")
            if str(pin) not in pads_of(b.parts[ref].fp, lib):
                raise ValueError(f"net {n}: {ref} has no pin {pin!r}")



AUTO_JOIN = ("VCC", "GND", "VDD", "VSS", "5V", "3V3")


def _include(parent: Board, path: str, prefix: str | None, join: str | None,
             base: str, stack: tuple[str, ...], err: ErrFn) -> None:
    """Merge a child .ocd into parent. Child board/layers/fix ignored;
    refs, nets, near/keep grouped under prefix; join nets merge up."""
    fn = os.path.normpath(os.path.join(base, path))
    if fn in stack:
        raise err(f"include cycle: {path!r}")
    if not os.path.isfile(fn):
        raise err(f"no such file: {path!r}")
    try:
        text = open(fn).read()
    except OSError as e:
        raise err(e)
    child = _loads(text, os.path.dirname(fn), stack + (fn,))
    pre = (prefix + "_") if prefix else (child.name + "_")
    joins = set(join.split()) if join else set()
    for ref, p in child.parts.items():
        new = pre + ref
        if new in parent.parts:
            raise err(f"ref clash: {new!r} (use `as` for a unique prefix)")
        parent.add_part(new, p.fp, p.value, attrs=dict(p.attrs) or None)
        parent.parts[new].owner = pre
    for bname, blk in child.blocks.items():
        # block library: `use lib.ocd` imports templates unprefixed (one
        # global block namespace; a clash is a loud error, not a silent
        # shadow). Provenance in block_src so dumps() skips them — they
        # come back via the `use` line, like owned parts.
        if bname in parent.blocks:
            raise err(f"block clash: {bname!r} (rename one of them)")
        from .circuit import Block as _Block
        parent.blocks[bname] = _Block(bname, blk.lines, blk.ports)
        parent.block_src[bname] = path
    for n, net in child.nets.items():
        # explicit `join` wins; power-style nets auto-join; rest prefixed
        target = (n if (joins and n in joins) or (join is None and n in AUTO_JOIN)
                  else pre + n)
        for ref, pin in net.pins:
            parent.connect(target, pre + ref, pin)
        if net.attrs:
            parent.nets[target].attrs.update(dict(net.attrs))
        if net.layer is not None:
            parent._constrain_raw({"t": "layer", "net": target, "layer": net.layer})
        if net.width != 0.3:
            parent._constrain_raw({"t": "width", "net": target, "width": net.width})
    for c in child.constraints:
        t = c.get("t")
        if t == "fixed":
            continue  # child placement ignored — parent places everything
        if t == "near":
            parent._constrain_raw({"t": "near", "a": pre + str(c["a"]), "b": pre + str(c["b"]),
                              "w": _f(c.get("w", 2.0)), "owner": pre})
        elif t == "power":
            nets = cast(list[str], c["nets"])
            merged = [n if (joins and n in joins) or (join is None and n in AUTO_JOIN)
                      else pre + n for n in nets]
            if not any(x.get("t") == "power"
                       and sorted(cast(list[str], x["nets"])) == sorted(merged)
                       for x in parent.constraints):
                parent._constrain_raw({"t": "power", "nets": merged, "owner": pre})
        elif t == "pour":
            target = (str(c["net"]) if (joins and str(c["net"]) in joins)
                      or (join is None and str(c["net"]) in AUTO_JOIN)
                      else pre + str(c["net"]))
            parent._constrain_raw({"t": "pour", "net": target,
                              "layer": int(cast(int, c.get("layer", 0))),
                              "owner": pre})
    parent.includes.append({"path": path, "prefix": prefix or child.name,
                            "join": sorted(joins)})
    if child.parts:
        parent._constrain_raw({"t": "near-group", "prefix": pre, "owner": pre})


def _exec_part(b: Board, line: str, err: ErrFn, ctx: str = "") -> None:
    """Shared part-line executor (top level + block stamping)."""
    toks = line.split(None, 3)
    if len(toks) < 3:
        raise err(f"{ctx}want: part REF FOOTPRINT [value] [k=v ...]")
    _, ref, fp, *val = toks
    if ref in b.parts:
        raise err(f"{ctx}duplicate part {ref}")
    value: str = ""
    attrs: dict[str, str] = {}
    if val:
        words = []
        try:
            tails = _split(val[0])
        except ValueError:
            raise err(f"{ctx}bad quoting in {val[0]!r}")
        for tok in tails:
            if "=" in tok:
                k, _, v = tok.partition("=")
                attrs[k] = v
            else:
                words.append(tok)
        value = " ".join(words)
    try:
        b.add_part(ref, fp, value, attrs=attrs or None)
    except (KeyError, ValueError) as e:
        raise err(f"{ctx}{e}")
    # declarative placement: `part R1 R0805 1k x=3 y=15` ≡ `fix R1 at 3 15`
    if "x" in attrs or "y" in attrs:
        try:
            px = float(attrs.get("x", "")) if "x" in attrs else b.parts[ref].x
            py = float(attrs.get("y", "")) if "y" in attrs else b.parts[ref].y
            if not (math.isfinite(px) and math.isfinite(py)):
                raise ValueError("non-finite coordinate")
        except ValueError:
            raise err(f"{ctx}bad x=/y= on part {ref}")
        b.constrain({"t": "fixed", "ref": ref, "x": px, "y": py})


def _exec_net(b: Board, line: str, err: ErrFn, ctx: str = "") -> None:
    """Shared net-line executor (top level + block stamping).
    Mermaid-style: `NAME [attrs] :: A.1 <--> B.2` (legacy `net NAME: ...` reads)."""
    head, sep, pins = line.partition("::")
    if not sep:  # legacy `net NAME [attrs]: pins`
        head, _, pins = line.partition(":")
    try:
        htoks = _split(head)
    except ValueError:
        raise err(f"{ctx}bad quoting in {head!r}")
    if not htoks:
        raise err(f"{ctx}want: NAME [L<n> w<n>] :: REF.PIN <--> ...")
    if htoks[0] == "net":  # legacy `net NAME [attrs]`
        if len(htoks) < 2:
            raise err(f"{ctx}want: NAME [L<n> w<n>] :: REF.PIN <--> ...")
        name, attrs = htoks[1], htoks[2:]
    else:
        name, attrs = htoks[0], htoks[1:]
    nattrs: dict[str, str] = {}
    for a in attrs:
        if a[0] in "Ll" and a[1:].isdigit():
            b._constrain_raw({"t": "layer", "net": name, "layer": int(a[1:])})
        elif a.startswith("pour="):
            # declarative pour: `GND pour=0 :: ...` ≡ `pour GND on 0`
            _pv = a.partition("=")[2].lower()
            _pl = {"top": 0, "bottom": b.layers - 1}.get(_pv, _pv)
            assert str(_pl).isdigit(), f"{ctx}bad pour layer {a!r}"
            b._constrain_raw({"t": "pour", "net": name, "layer": int(_pl)})
        elif "=" in a:
            k, _, v = a.partition("=")
            if not k or not v:
                raise err(f"{ctx}bad net attribute {a!r} (want k=v)")
            nattrs[k] = v
        else:
            w: float | None = None
            if a[0] in "Ww":
                try:
                    w = float(a[1:])
                except ValueError:
                    w = None
            if w is None:
                raise err(f"{ctx}bad net attribute {a!r} (want L<n>, w<n>, or k=v)")
            b.constrain({"t": "width", "net": name, "width": w})
    if nattrs:
        try:
            b.net(name).attrs.update(nattrs)
        except ValueError as e:
            raise err(f"{ctx}{e}")
    for tok in pins.replace("<-->", " ").split():
        ref, dot, pin = tok.partition(".")
        if not dot or not ref or not pin:
            raise err(f"{ctx}bad pin {tok!r} (want REF.PIN)")
        # order-free: parts may be declared later in the file; _validate
        # checks existence at the end (unlike Board.connect, which is
        # immediate and validates now)
        try:
            net = b.net(name)
        except ValueError as e:
            raise err(f"{ctx}{e}")
        entry = (ref, pin)
        if entry not in net.pins:
            net.pins.append(entry)


def _instance(parent: Board, block: str, prefix: str, join: str | None,
              err: ErrFn) -> None:
    """Stamp a block template N times (repeatable units, logisim-style).
    Same merge rules as _include; parts get owner=prefix for rigid-body
    placement. Blocks keep no placement: parent places everything."""
    from .circuit import Board as _Board
    if block not in parent.blocks:
        raise err(f"unknown block {block!r}")
    joins = set(join.split()) if join else set()
    blk = parent.blocks[block]
    if blk.ports:
        bad = sorted(j for j in joins if j not in blk.ports)
        if bad:
            raise err(f"instance {block} joins non-port {bad} (ports: {' '.join(blk.ports)})")
    pre = prefix + "_"
    # parse block lines into a throwaway board, then merge like _include.
    # dispatch=False: this board never runs a plugin, and mounting the ~59
    # defaults for it was 1.5s of a 2.8s load on a 1726-instance board
    # (101k plugin mounts, each notifying the whole fiber tree).
    child = _Board("__block__", dispatch=False)
    child.custom_fp.update(parent.custom_fp)  # blocks may use parent's `fp` files
    for raw in parent.blocks[block].lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        kw = line.split(None, 1)[0].lower()
        if kw == "part":
            _exec_part(child, line, err, ctx=f"in block {block}: ")
        elif kw == "net" or "::" in line:
            _exec_net(child, line, err, ctx=f"in block {block}: ")
        else:
            c = parse_constraint(line)
            if c is None:
                raise err(f"in block {block}: unknown statement: {line!r}")
            child._constrain_raw(c)
    if blk.ports:
        have = set(child.nets)
        missing = sorted(p for p in blk.ports if p not in have)
        if missing:
            raise err(f"block {block} declares ports {missing} with no such net")
    for ref, p in child.parts.items():
        new = pre + ref
        if new in parent.parts:
            raise err(f"ref clash: {new!r} (instance prefixes must differ)")
        parent.add_part(new, p.fp, p.value, attrs=dict(p.attrs) or None)
        parent.parts[new].owner = pre
    for n, net in child.nets.items():
        target = (n if (joins and n in joins) or (join is None and n in AUTO_JOIN)
                  else pre + n)
        for ref, pin in net.pins:
            parent.connect(target, pre + ref, pin)
        if net.attrs:
            parent.nets[target].attrs.update(dict(net.attrs))

    def _remap(n: str) -> str:
        return n if (joins and n in joins) or (join is None and n in AUTO_JOIN) else pre + n

    for c in child.constraints:
        t = c.get("t")
        if t == "fixed":
            continue  # block-local placement ignored — two-level placer owns it
        # remapped: near/power/layer/width/pour. Dropped: class/match/diff/
        # keepout/sim/... — board-global or position-dependent; put them at
        # top level (a block has no position to anchor them to).
        if t == "near":
            parent._constrain_raw({"t": "near", "a": pre + str(c["a"]), "b": pre + str(c["b"]),
                              "w": _f(c.get("w", 2.0)), "owner": pre})
        elif t == "power":
            nets = cast(list[str], c["nets"])
            merged = [_remap(x) for x in nets]
            parent._constrain_raw({"t": "power", "nets": merged, "owner": pre})
        elif t in ("layer", "width", "pour"):
            cc = dict(c)
            cc["net"] = _remap(str(c["net"]))
            cc["owner"] = pre
            parent._constrain_raw(cc)
    parent.instances.append({"block": block, "prefix": prefix, "join": sorted(joins)})
    parent._constrain_raw({"t": "near-group", "prefix": pre, "owner": pre})


def ir(board: Board) -> dict[str, object]:
    """Structured snapshot for LLM agents. Also the circuit language:
    circuits are Python (Board API) or this JSON — no custom parser.
    Answers 'what language': JSON (from_ir/to_json) + Python builder."""
    from .plugins import ir_of
    return ir_of(board)


def to_json(board: Board) -> str:
    return json.dumps(ir(board), indent=1)


def from_ir(doc: dict[str, object]) -> Board:
    from .plugins import from_ir as _f
    return _f(doc)


def from_json(text: str) -> Board:
    raw = json.loads(text)
    assert isinstance(raw, dict)
    return from_ir(raw)
