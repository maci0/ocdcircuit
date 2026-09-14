"""Schematic symbols: boxes with pin stubs, not vector art.

A symbol is a named body (default: plain box sized to its pins) with pins
placed on edges, in schematic grid units (1 unit = one pin pitch; the
renderer scales). Pins reference footprint pins by number; unlisted pins
get auto-stubs stacked on the right edge.

Format (one fact per line, # comments) — mirrors .fp:
  symbol NAME [WxH]      # body box; default sizes to pins
  pin NUM SIDE [LABEL]   # SIDE = left|right|top|bottom, stub position
  label TEXT             # body caption ({ref} {value} {fp} interpolate)
  notch                  # pin-1 / polarity dot marker (ICs)
  zigzag                 # draw resistor zigzag instead of box (R only)

`sym PATH` in .ocd loads it (relative to the file). Parts pick symbols via
the `sym=` attr; otherwise the footprint's default symbol applies.
"""
from __future__ import annotations

Side = str  # left|right|top|bottom
# Symbol = {"w": float, "h": float, "pins": {num: (side, order, label)},
#           "notch": bool, "zigzag": bool} — pins ordered per side by file order
Symbol = dict[str, object]


def loads(text: str) -> tuple[str, Symbol]:
    name = ""
    w = h = 0.0
    pins: dict[str, tuple[str, int, str]] = {}
    counts: dict[str, int] = {}
    label = ""
    notch = zigzag = False
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        def err(msg: object) -> ValueError:
            return ValueError(f"line {ln}: {msg}: {line!r}")

        kw = line.split(None, 1)[0].lower()
        if kw == "symbol":
            import re
            m = re.match(r"^symbol\s+(\S+)(?:\s+([\d.]+)x([\d.]+))?$", line, re.I)
            if not m:
                raise err("want: symbol NAME [WxH]")
            name = m.group(1)
            w, h = float(m.group(2) or 0), float(m.group(3) or 0)
        elif kw == "pin":
            toks = line.split()
            if len(toks) < 3 or toks[2].lower() not in ("left", "right", "top", "bottom"):
                raise err("want: pin NUM left|right|top|bottom [LABEL]")
            side = toks[2].lower()
            counts[side] = counts.get(side, 0) + 1
            pins[toks[1]] = (side, counts[side] - 1, " ".join(toks[3:]))
        elif kw == "notch":
            notch = True
        elif kw == "label":
            toks = line.split(None, 1)
            if len(toks) != 2 or not toks[1].strip():
                raise err("want: label TEXT (supports {ref} {value} {fp})")
            label = toks[1].strip()
        elif kw == "zigzag":
            zigzag = True
        else:
            raise err("unknown statement")
    if not name:
        raise ValueError("missing symbol header")
    return name, {"w": w, "h": h, "pins": pins, "notch": notch,
                   "zigzag": zigzag, "label": label}


def load_file(path: str) -> tuple[str, Symbol]:
    with open(path) as f:
        return loads(f.read())


def pin_pos(sym: Symbol, num: str, n_extra: int = 0) -> tuple[float, float, str]:
    """Stub anchor (x, y, side) in symbol units for pin NUM. Unlisted pins
    stack on the right edge after listed ones."""
    from typing import cast
    pins = cast(dict[str, tuple[str, int, str]], sym["pins"])
    w = float(cast(float, sym["w"]))
    h = float(cast(float, sym["h"]))
    if num in pins:
        side, order, _label = pins[num]
    else:
        listed = sum(1 for s, _o, _l in pins.values() if s == "right")
        side, order = "right", listed + n_extra
    x = {"left": 0.0, "right": w}.get(side, w / 2)
    y = {"top": 0.0, "bottom": h}.get(side, 1.0 + order * 2.0)
    if side in ("top", "bottom"):
        counts: dict[int, int] = {}
        for s, o, _l in pins.values():
            if s == side:
                counts[o] = counts.get(o, 0) + 1
        n = max(len([1 for s, _o, _l in pins.values() if s == side]), 1)
        x = w * (order + 1) / (n + 1)
    return (x, y, side)


def sized(sym: Symbol, n_pins: int) -> Symbol:
    """Fill in default body size from pin count (copy, never mutate)."""
    from typing import cast
    w, h = float(cast(float, sym["w"])), float(cast(float, sym["h"]))
    pins = cast(dict[str, tuple[str, int, str]], sym["pins"])
    if w <= 0 or h <= 0:
        # ponytail: rows = max per-side stub count (pins interleave L/R);
        # top/bottom pins ride free. Upgrade path: explicit row map.
        rows = max(
            max([o for s, o, _l in pins.values() if s == "left"] + [-1]) + 1,
            max([o for s, o, _l in pins.values() if s == "right"] + [-1]) + 1,
            2)
        w, h = (w or 6.0), (h or max(4.0, float(rows)))
    return {"w": w, "h": h, "pins": dict(pins),
            "notch": bool(sym["notch"]), "zigzag": bool(sym["zigzag"]),
            "label": str(sym.get("label", ""))}


def _box(pins: list[str], notch: bool = False) -> Symbol:
    d: dict[str, tuple[str, int, str]] = {}
    left, right = pins[::2], pins[1::2]
    for i, p in enumerate(left):
        d[p] = ("left", i, "")
    for i, p in enumerate(right):
        d[p] = ("right", i, "")
    return {"w": 0.0, "h": 0.0, "pins": d, "notch": notch, "zigzag": False, "label": ""}


SYMBOLS: dict[str, Symbol] = {
    # 2-pin passives: zigzag R, plain C/L/D/LED boxes
    "R": {"w": 4.0, "h": 2.0, "pins": {"1": ("left", 0, ""), "2": ("right", 0, "")},
          "notch": False, "zigzag": True, "label": ""},
    "C": {"w": 2.0, "h": 2.0, "pins": {"1": ("left", 0, ""), "2": ("right", 0, "")},
          "notch": False, "zigzag": False, "label": ""},
    "L": {"w": 4.0, "h": 2.0, "pins": {"1": ("left", 0, ""), "2": ("right", 0, "")},
          "notch": False, "zigzag": False, "label": ""},
    "D": {"w": 3.0, "h": 2.0, "pins": {"1": ("left", 0, ""), "2": ("right", 0, "")},
          "notch": False, "zigzag": False, "label": ""},
    "Q3": {"w": 4.0, "h": 4.0, "pins": {"1": ("left", 0, "B"), "2": ("left", 1, "E"),
                                        "3": ("right", 0, "C")},
           "notch": False, "zigzag": False, "label": ""},
    "OPAMP": {"w": 6.0, "h": 6.0, "pins": {"1": ("left", 0, "OUT"), "2": ("left", 1, "IN-"),
                                           "3": ("left", 2, "IN+"), "4": ("top", 0, "V+"),
                                           "5": ("bottom", 0, "V-")},
              "notch": False, "zigzag": False, "label": ""},
    "IC8": _box([str(i) for i in range(1, 9)], notch=True),
    "IC14": _box([str(i) for i in range(1, 15)], notch=True),
    "IC16": _box([str(i) for i in range(1, 17)], notch=True),
}

# footprint → default symbol (prefix match, longest first at lookup)
FP_SYMBOLS: list[tuple[str, str]] = [
    ("PINHD", "IC8"), ("SOIC", "IC8"), ("SSOP", "IC14"), ("TSSOP", "IC14"),
    ("MSOP", "IC8"), ("DFN", "IC8"), ("QFP", "IC16"), ("QFN", "IC16"),
    ("BGA", "IC16"), ("SOT23", "Q3"), ("SOT363", "Q3"), ("SOT89", "Q3"),
    ("SOT223", "Q3"), ("LED", "D"), ("D_SOD", "D"), ("D_SMA", "D"),
    ("D_SMB", "D"), ("D_SMC", "D"), ("IND_SM", "L"), ("L0", "L"), ("L1", "L"),
    ("L2", "L"), ("C0", "C"), ("C1", "C"), ("C2", "C"), ("R0", "R"),
    ("R1", "R"), ("R2", "R"),
]


def _stdlib(name: str) -> Symbol | None:
    return SYMBOLS.get(name)


def resolve(fp: str, sym_attr: str = "",
            lib: dict[str, Symbol] | None = None) -> Symbol:
    """Symbol for a part: explicit `sym=` attr (custom or stdlib) wins,
    else longest footprint-prefix match, else plain pin-count box."""
    if sym_attr and lib is not None and sym_attr in lib:
        return lib[sym_attr]
    if sym_attr and sym_attr in SYMBOLS:
        return SYMBOLS[sym_attr]
    if sym_attr:
        raise KeyError(f"unknown symbol {sym_attr!r}")
    for prefix, sname in sorted(FP_SYMBOLS, key=lambda t: -len(t[0])):
        if fp.startswith(prefix):
            return SYMBOLS[sname]
    return {"w": 0.0, "h": 0.0, "pins": {}, "notch": False, "zigzag": False, "label": ""}


if __name__ == "__main__":
    from typing import cast as _cast
    s, m = loads("symbol T1 4x4\npin 1 left B\npin 2 left E\npin 3 right C\nnotch\n")
    assert (s, m["w"], _cast(dict[str, object], m["pins"])["3"]) == ("T1", 4.0, ("right", 0, "C"))
    assert pin_pos(SYMBOLS["R"], "1") == (0.0, 1.0, "left")
    assert resolve("R0805").get("zigzag") is True
    assert _cast(dict[str, object], resolve("SOIC8")["pins"])["1"] == ("left", 0, "")
    assert _cast(dict[str, object], resolve("X7", sym_attr="OPAMP")["pins"])["4"] == ("top", 0, "V+")
    assert sized(SYMBOLS["IC8"], 8)["h"] == 4.0
    print("SYM OK")
