"""Agent-first API: structured patches (undoable) + NL constraint fallback."""
from __future__ import annotations
import os
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from .types import Constraint

if TYPE_CHECKING:
    from .circuit import Board

ErrFn = Callable[[object], ValueError]


def apply_patch(board: Board, ops: list[dict[str, object]]) -> int:
    n = 0
    for op in ops:
        k = op.get("op")
        if k == "add_part":
            board.add_part(str(op["ref"]), str(op["fp"]), str(op.get("value", "")),
                           _opt_float(op.get("x")), _opt_float(op.get("y")))
        elif k == "move_part":
            board.move_part(_s(op["ref"]), _f(op["x"]), _f(op["y"]))
        elif k == "remove_part":
            board.remove_part(str(op["ref"]))
        elif k == "connect":
            board.connect(_s(op["net"]), _s(op["ref"]), _s(op["pin"]))
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
            reg = board.ctx.require("plugins")
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


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)


def _i(v: object, default: int = 0) -> int:
    if v is None:
        return default
    assert isinstance(v, (int, str))
    return int(v)


def _s(v: object) -> str:
    assert isinstance(v, str)
    return v


def _opt_float(v: object) -> float | None:
    return None if v is None else _f(v)


def parse_constraint(text: str) -> Constraint | None:
    t = text.strip()
    m = re.match(r"keep (\w+) near (\w+)(?: (\d+(?:\.\d+)?))?$", t, re.I)
    if m:
        return {"t": "near", "a": m.group(1), "b": m.group(2),
                "w": float(m.group(3) or 2.0)}
    m = re.match(r"fix (\w+) at ([\d.]+) ([\d.]+)$", t, re.I)
    if m:
        return {"t": "fixed", "ref": m.group(1), "x": float(m.group(2)), "y": float(m.group(3))}
    m = re.match(r"route (\w+) on (top|bottom|\d+)$", t, re.I)
    if m:
        layer = {"top": 0, "bottom": 1}[m.group(2).lower()] if m.group(2).lower() in ("top", "bottom") else int(m.group(2))
        return {"t": "layer", "net": m.group(1), "layer": layer}
    m = re.match(r"trace (\w+) ([\d.]+)$", t, re.I)
    if m:
        return {"t": "width", "net": m.group(1), "width": float(m.group(2))}
    m = re.match(r"power ([\w ]+)$", t, re.I)
    if m:
        return {"t": "power", "nets": m.group(1).split()}
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
    m = re.match(r"keepout ([\d.\-]+) ([\d.\-]+) ([\d.]+)x([\d.]+)(?: on ([\w,]+))?$", t, re.I)
    if m:
        return {"t": "keepout", "x": float(m.group(1)), "y": float(m.group(2)),
                "w": float(m.group(3)), "h": float(m.group(4)),
                "layers": m.group(5).split(",") if m.group(5) else []}
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
    m = re.match(r"sim\s+vcc\s+(\w+)\s+([\d.]+)(?:\s+([\d.]+))?$", t, re.I)
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
    m = re.match(r"sim\s+([rcl])\s+(\w+)\s+(\S+)$", t, re.I)
    if m:
        return {"t": "sim", "kind": m.group(1), "ref": m.group(2), "value": m.group(3)}
    m = re.match(r"board ([\d.]+) ?x ([\d.]+)$", t, re.I)
    if m:
        return {"t": "board", "w": float(m.group(1)), "h": float(m.group(2))}
    return None


def dumps(board: Board) -> str:
    """The human-readable circuit language (.ocd): one fact per line.
    Python (Board API) builds it, JSON is the wire IR, this is what
    humans read/write. Round-trips through loads(): boards with includes
    dump `use` lines + local content only; reload re-merges identically."""
    owned = {p.ref for p in board.parts.values() if p.owner}
    L = [f"board {board.name} {board.width:g}x{board.height:g} {board.layers}L"]
    for bname, block in board.blocks.items():
        L.append(f"block {bname}")
        L.extend(f"  {ln}" for ln in block.lines)
        L.append("end")
    for inc in board.includes:
        L.append(f"use {inc['path']}" + (f" as {inc['prefix']}" if inc.get("prefix") else "") +
                 (f" join {' '.join(cast(list[str], inc['join']))}" if inc.get("join") else ""))
    for ins in board.instances:
        L.append(f"instance {ins['block']} as {ins['prefix']}" +
                 (f" join {' '.join(cast(list[str], ins['join']))}" if ins.get("join") else ""))
    for p in board.parts.values():
        if p.owner:
            continue  # owned by an include/instance — dumped as use/instance
        attrs = "".join(f" {k}={v}" for k, v in sorted(p.attrs.items()))
        L.append(f"part {p.ref} {p.fp}{(' ' + p.value) if p.value else ''}{attrs}")
    # fp lines up front: footprints must exist before parts use them
    fps = [f"fp {board.fp_src[name]}" for name in board.custom_fp if name in board.fp_src]
    L[1:1] = fps
    for n, net in board.nets.items():
        pins = [(r, pin) for r, pin in net.pins if r not in owned]
        if not pins and any(net.pins):
            continue  # fully owned by an include — comes back via `use`
        attrs = ""
        if net.layer is not None:
            attrs += f" L{net.layer}"
        if net.width != 0.3:
            attrs += f" w{net.width:g}"
        L.append(f"net {n}{attrs}: " + " ".join(f"{r}.{pin}" for r, pin in pins))
    seen_power: list[list[str]] = []
    for c in board.constraints:
        t = c.get("t")
        if t in ("near", "fixed", "near-group") and c.get("owner"):
            continue  # owned by an include
        if t == "near":
            L.append(f"keep {c['a']} near {c['b']} {_f(c.get('w', 2)):g}")
        elif t == "fixed":
            L.append(f"fix {c['ref']} at {_f(c['x']):g} {_f(c['y']):g}")
        elif t == "layer" and str(c["net"]) in board.nets and not any(
                r not in owned for r, _ in board.nets[str(c["net"])].pins):
            continue  # layer of a fully-included net — comes back via `use`
        elif t == "layer":
            L.append(f"route {c['net']} on {c['layer']}")
        elif t == "width" and str(c["net"]) in board.nets and not any(
                r not in owned for r, _ in board.nets[str(c["net"])].pins):
            continue
        elif t == "width":
            L.append(f"trace {c['net']} {_f(c['width']):g}")
        elif t == "silk":
            L.append(f"silk {c['level']}")
        elif t == "nc":
            L.append(f"nc {' '.join(cast(list[str], c['pins']))}")
        elif t == "pour":
            L.append(f"pour {c['net']} on {c['layer']}")
        elif t == "keepout":
            ly = f" on {','.join(cast(list[str], c['layers']))}" if c.get("layers") else ""
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
    return f"sim {k} {c.get('ref', '')} {c.get('value', '')}"


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
    return _loads(text, basedir, stack=(), top=True)


def _loads(text: str, base: str, stack: tuple[str, ...], top: bool = False) -> Board:
    from .circuit import Board
    b: Board | None = None
    for ln, raw in enumerate(text.splitlines(), 1):
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
        if b is None:
            raise err("board header first")
        if kw == "block":
            m = re.match(r"^block\s+(\S+)$", line, re.I)
            if not m:
                raise err("want: block NAME")
            if b is not None and b._block_open is not None:
                raise err("nested blocks not supported (flatten it)")
            b._block_open = m.group(1)
            b._block_lines = []
            continue
        if kw == "end":
            if b._block_open is None:
                raise err("end without block")
            from .circuit import Block as _Block
            name = b._block_open
            if name in b.blocks:
                raise err(f"duplicate block {name!r}")
            b.blocks[name] = _Block(name, b._block_lines or [])
            b._block_open = None
            b._block_lines = None
            continue
        if kw == "instance":
            m = re.match(r"^instance\s+(\S+)\s+as\s+(\S+)(?:\s+join\s+(.+))?$",
                         line, re.I)
            if not m:
                raise err("want: instance BLOCK as PREFIX [join NET ...]")
            _instance(b, m.group(1), m.group(2), m.group(3), err)
            continue
        if kw == "fp":
            toks = line.split(None, 1)
            if len(toks) != 2:
                raise err("want: fp PATH/to/part.fp")
            import os as _os
            from .footprint import load_file
            from .foreign import load_foreign
            fn = _os.path.normpath(_os.path.join(base, toks[1]))
            if fn in stack:
                raise err(f"footprint cycle: {toks[1]!r}")
            try:
                ext = _os.path.splitext(fn)[1].lower()
                pairs = load_foreign(fn) if ext != ".fp" else [load_file(fn)]
            except (OSError, ValueError) as e:
                raise err(e)
            for name, meta in pairs:
                if name in b._lib() and name not in b.custom_fp:
                    raise err(f"footprint {name!r} shadows std lib (rename it)")
                b.add_footprint(name, meta, toks[1])
        elif kw == "part":
            _exec_part(b, line, err)
        elif kw == "net":
            _exec_net(b, line, err)
        else:
            c = parse_constraint(line)
            if c is None:
                raise err("unknown statement")
            if c["t"] == "board":
                b.set_board(_f(c["w"]), _f(c["h"]))
            else:
                b.constrain(c)
    if b is None:
        raise ValueError("empty circuit")
    if top:
        _validate(b)
    return b


def _validate(b: Board) -> None:
    from .parts import pads_of
    lib = b._lib()
    for n, net in b.nets.items():
        for ref, pin in net.pins:
            if ref not in b.parts:
                raise ValueError(f"net {n}: unknown part {ref!r}")
            if str(pin) not in pads_of(b.parts[ref].fp, lib):
                raise ValueError(f"net {n}: {ref} has no pin {pin!r}")
    # ponytail: warn-only until pour has a consumer (LANDSCAPE defers pour
    # support in DRC/export) — parsing must never silently do nothing.


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
        parent.add_part(new, p.fp, p.value)
        parent.parts[new].owner = pre
    for n, net in child.nets.items():
        # explicit `join` wins; power-style nets auto-join; rest prefixed
        target = (n if (joins and n in joins) or (join is None and n in AUTO_JOIN)
                  else pre + n)
        for ref, pin in net.pins:
            parent.connect(target, pre + ref, pin)
        if net.layer is not None:
            parent.constrain({"t": "layer", "net": target, "layer": net.layer})
        if net.width != 0.3:
            parent.constrain({"t": "width", "net": target, "width": net.width})
    for c in child.constraints:
        t = c.get("t")
        if t == "fixed":
            continue  # child placement ignored — parent places everything
        if t == "near":
            parent.constrain({"t": "near", "a": pre + str(c["a"]), "b": pre + str(c["b"]),
                              "w": _f(c.get("w", 2.0)), "owner": pre})
        elif t == "power":
            nets = cast(list[str], c["nets"])
            merged = [n if (joins and n in joins) or (join is None and n in AUTO_JOIN)
                      else pre + n for n in nets]
            if not any(x.get("t") == "power"
                       and sorted(cast(list[str], x["nets"])) == sorted(merged)
                       for x in parent.constraints):
                parent.constrain({"t": "power", "nets": merged, "owner": pre})
    parent.includes.append({"path": path, "prefix": prefix or child.name,
                            "join": sorted(joins)})
    parent.constrain({"t": "near-group", "prefix": pre, "owner": pre})


def _exec_part(b: Board, line: str, err: ErrFn, ctx: str = "") -> None:
    """Shared part-line executor (top level + block stamping)."""
    toks = line.split(None, 3)
    if len(toks) < 3:
        raise err(f"{ctx}want: part REF FOOTPRINT [value] [k=v ...]")
    _, ref, fp, *val = toks
    value: str = ""
    attrs: dict[str, str] = {}
    if val:
        words = []
        for tok in val[0].split():
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


def _exec_net(b: Board, line: str, err: ErrFn, ctx: str = "") -> None:
    """Shared net-line executor (top level + block stamping)."""
    head, _, pins = line.partition(":")
    htoks = head.split()
    if len(htoks) < 2:
        raise err(f"{ctx}want: net NAME [L<n> w<n>]: REF.PIN ...")
    name = htoks[1]
    for a in htoks[2:]:
        if a[0] in "Ll" and a[1:].isdigit():
            b.constrain({"t": "layer", "net": name, "layer": int(a[1:])})
        else:
            w: float | None = None
            if a[0] in "Ww":
                try:
                    w = float(a[1:])
                except ValueError:
                    w = None
            if w is None:
                raise err(f"{ctx}bad net attribute {a!r} (want L<n> or w<n>)")
            b.constrain({"t": "width", "net": name, "width": w})
    for tok in pins.split():
        ref, dot, pin = tok.partition(".")
        if not dot or not ref or not pin:
            raise err(f"{ctx}bad pin {tok!r} (want REF.PIN)")
        b.connect(name, ref, pin)


def _instance(parent: Board, block: str, prefix: str, join: str | None,
              err: ErrFn) -> None:
    """Stamp a block template N times (repeatable units, logisim-style).
    Same merge rules as _include; parts get owner=prefix for rigid-body
    placement. Blocks keep no placement: parent places everything."""
    from .circuit import Board as _Board
    if block not in parent.blocks:
        raise err(f"unknown block {block!r}")
    joins = set(join.split()) if join else set()
    pre = prefix + "_"
    # parse block lines into a throwaway board, then merge like _include
    child = _Board("__block__")
    for raw in parent.blocks[block].lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        kw = line.split(None, 1)[0].lower()
        if kw == "part":
            _exec_part(child, line, err, ctx=f"in block {block}: ")
        elif kw == "net":
            _exec_net(child, line, err, ctx=f"in block {block}: ")
        else:
            c = parse_constraint(line)
            if c is None:
                raise err(f"in block {block}: unknown statement: {line!r}")
            child.constrain(c)
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
    for c in child.constraints:
        t = c.get("t")
        if t == "fixed":
            continue  # block-local placement ignored — two-level placer owns it
        if t == "near":
            parent.constrain({"t": "near", "a": pre + str(c["a"]), "b": pre + str(c["b"]),
                              "w": _f(c.get("w", 2.0)), "owner": pre})
        elif t == "power":
            nets = cast(list[str], c["nets"])
            merged = [x if (joins and x in joins) or (join is None and x in AUTO_JOIN)
                      else pre + x for x in nets]
            parent.constrain({"t": "power", "nets": merged, "owner": pre})
    parent.instances.append({"block": block, "prefix": prefix, "join": sorted(joins)})
    parent.constrain({"t": "near-group", "prefix": pre, "owner": pre})


def ir(board: Board) -> dict[str, object]:
    """Structured snapshot for LLM agents. Also the circuit language:
    circuits are Python (Board API) or this JSON — no custom parser.
    Answers 'what language': JSON (from_ir/to_json) + Python builder."""
    from .plugins import ir_of
    return ir_of(board)


def to_json(board: Board) -> str:
    import json
    return json.dumps(ir(board), indent=1)


def from_ir(doc: dict[str, object]) -> Board:
    from .plugins import from_ir as _f
    return _f(doc)


def from_json(text: str) -> Board:
    import json
    raw = json.loads(text)
    assert isinstance(raw, dict)
    return from_ir(raw)


def to_ocd(board: Board) -> str:
    """Alias: dumps is the human-readable language (see dumps docstring)."""
    return dumps(board)


def from_ocd(text: str, base: str | os.PathLike[str] | None = None) -> Board:
    return loads(text, base=base)
