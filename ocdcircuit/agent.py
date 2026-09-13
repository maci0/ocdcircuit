"""Agent-first API: structured patches (undoable) + NL constraint fallback."""
from __future__ import annotations
import re


def apply_patch(board, ops: list[dict]) -> int:
    n = 0
    for op in ops:
        k = op.get("op")
        if k == "add_part":
            board.add_part(op["ref"], op["fp"], op.get("value", ""),
                           op.get("x"), op.get("y"))
        elif k == "move_part":
            board.move_part(op["ref"], op["x"], op["y"])
        elif k == "remove_part":
            board.remove_part(op["ref"])
        elif k == "connect":
            board.connect(op["net"], op["ref"], op["pin"])
        elif k == "constrain":
            board.constrain(op["c"])
        elif k == "set_board":
            board.set_board(op["w"], op["h"])
        elif k == "route":
            board.route_board()
        elif k == "optimize":
            board.place(seeds=op.get("seeds", 4),
                        iters=op.get("iters", 400), seed=op.get("seed", 0))
        elif k == "check":
            op["result"] = board.check(op.get("key"))
        elif k == "export":
            op["result"] = board.export(op.get("key"), **op.get("args", {}))
        elif k == "render":
            op["result"] = board.render(op.get("key"), **op.get("args", {}))
        elif k == "use":
            board.use(op["kind"], op["key"])
        elif k == "plugin":
            board.ctx.require("plugins").get(op["kind"], op["key"]).run(
                board, **op.get("args", {}))
        else:
            raise ValueError(f"unknown op {k}")
        n += 1
    return n


def parse_constraint(text: str) -> dict | None:
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
    m = re.match(r"board ([\d.]+) ?x ([\d.]+)$", t, re.I)
    if m:
        return {"t": "board", "w": float(m.group(1)), "h": float(m.group(2))}
    return None


def dumps(board) -> str:
    """The human-readable circuit language (.ocd): one fact per line.
    Python (Board API) builds it, JSON is the wire IR, this is what
    humans read/write. Round-trips through loads()."""
    L = [f"board {board.name} {board.width:g}x{board.height:g} {board.layers}L"]
    for p in board.parts.values():
        L.append(f"part {p.ref} {p.fp}{(' ' + p.value) if p.value else ''}")
    for n, net in board.nets.items():
        attrs = ""
        if net.layer is not None:
            attrs += f" L{net.layer}"
        if net.width != 0.3:
            attrs += f" w{net.width:g}"
        pins = " ".join(f"{r}.{pin}" for r, pin in net.pins)
        L.append(f"net {n}{attrs}: {pins}")
    for c in board.constraints:
        t = c.get("t")
        if t == "near":
            L.append(f"keep {c['a']} near {c['b']} {c.get('w', 2):g}")
        elif t == "fixed":
            L.append(f"fix {c['ref']} at {c['x']:g} {c['y']:g}")
        elif t == "layer":
            L.append(f"route {c['net']} on {c['layer']}")
        elif t == "width":
            L.append(f"trace {c['net']} {c['width']:g}")
        elif t == "power":
            L.append(f"power {' '.join(c['nets'])}")
    return "\n".join(L) + "\n"


def loads(text: str) -> "Board":
    """Parse .ocd text (dumps output, comments with #). Constraints reuse
    parse_constraint — one grammar, no separate parser. Errors name the line."""
    import re as _re
    from .circuit import Board
    b = None
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        def err(msg):
            return ValueError(f"line {ln}: {msg}: {line!r}")

        kw = line.split(None, 1)[0].lower()
        if kw == "board":
            m = _re.match(r"^board\s+(\S+)\s+([\d.]+)x([\d.]+)(?:\s+(\d+)L)?$",
                          line, _re.I)
            if m:
                if b is not None:
                    raise err("duplicate board header (one board per file)")
                b = Board(m.group(1), float(m.group(2)), float(m.group(3)),
                          int(m.group(4)) if m.group(4) else 2)
                continue
            # else: bare "board WxH" resize form → handled as constraint below
        if b is None:
            raise err("board header first")
        if kw == "part":
            toks = line.split(None, 3)
            if len(toks) < 3:
                raise err("want: part REF FOOTPRINT [value]")
            _, ref, fp, *val = toks
            try:
                b.add_part(ref, fp, val[0] if val else "")
            except (KeyError, ValueError) as e:
                raise err(e)
        elif kw == "net":
            head, _, pins = line.partition(":")
            toks = head.split()
            if len(toks) < 2:
                raise err("want: net NAME [L<n> w<n>]: REF.PIN ...")
            name = toks[1]
            for a in toks[2:]:
                if a[0] in "Ll" and a[1:].isdigit():
                    b.constrain({"t": "layer", "net": name, "layer": int(a[1:])})
                else:
                    try:
                        assert a[0] in "Ww"
                        w = float(a[1:])
                    except (AssertionError, ValueError, IndexError):
                        raise err(f"bad net attribute {a!r} (want L<n> or w<n>)")
                    b.constrain({"t": "width", "net": name, "width": w})
            for tok in pins.split():
                ref, dot, pin = tok.partition(".")
                if not dot or not ref or not pin:
                    raise err(f"bad pin {tok!r} (want REF.PIN)")
                b.connect(name, ref, pin)
        else:
            c = parse_constraint(line)
            if c is None:
                raise err("unknown statement")
            if c["t"] == "board":
                b.set_board(c["w"], c["h"])
            else:
                b.constrain(c)
    if b is None:
        raise ValueError("empty circuit")
    lib = b._lib()  # every pin must resolve — fail at load, not mid-route
    for n, net in b.nets.items():
        for ref, pin in net.pins:
            if ref not in b.parts:
                raise ValueError(f"net {n}: unknown part {ref!r}")
            if str(pin) not in lib[b.parts[ref].fp]["pins"]:
                raise ValueError(f"net {n}: {ref} has no pin {pin!r}")
    return b


def ir(board) -> dict:
    """Structured snapshot for LLM agents. Also the circuit language:
    circuits are Python (Board API) or this JSON — no custom parser.
    Answers 'what language': JSON (from_ir/to_json) + Python builder."""
    from .plugins import ir_of
    return ir_of(board)


def to_json(board) -> str:
    import json
    return json.dumps(ir(board), indent=1)


def from_ir(doc) -> "Board":
    from .plugins import from_ir as _f
    return _f(doc)


def from_json(text: str) -> "Board":
    import json
    return from_ir(json.loads(text))


def to_ocd(board) -> str:
    """Alias: dumps is the human-readable language (see dumps docstring)."""
    return dumps(board)


def from_ocd(text: str) -> "Board":
    return loads(text)
