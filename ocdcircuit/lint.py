"""Static lint for .ocd sources: fast, no place/route. Pure checks over the
parsed board (parts/nets/constraints as loaded) — style, hygiene, and
likely-silly before the solvers ever run. DRC/ERC own geometry/electrics.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board

# ref-bearing keys, resolved structurally (no per-kind table to desync
# when new constraint kinds land): part refs error if dangling...
_PARTKEYS = ("ref", "a", "b")
# ...net refs warn (solvers skip unknown nets silently)
_NETKEYS = ("net", "nets", "p", "n")
# ... with numeric ranges worth a second glance
_RANGES = {"width": ("width", 0.05, 3.0), "bend": ("r", 0.5, 50.0),
           "hole": ("d", 0.1, 10.0), "keepout": ("d", 0.2, 200.0),
           "stiffener": ("th", 0.05, 3.0)}


def _strs(v: object) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def lint(board: Board) -> dict[str, object]:
    """{"errors": [...], "warnings": [...] }. Errors = will fail downstream
    (bad refs, dupes); warnings = smell (unused, shadowing, waste)."""
    errors: list[str] = []
    warnings: list[str] = []
    seen_e: set[str] = set()
    seen_w: set[str] = set()

    def err(s: str) -> None:
        if s not in seen_e:
            seen_e.add(s)
            errors.append(s)

    def warn(s: str) -> None:
        if s not in seen_w:
            seen_w.add(s)
            warnings.append(s)

    try:
        lib = board._lib()
    except (KeyError, ValueError) as e:
        return {"errors": [f"unreadable parts library: {e}"], "warnings": []}

    connected: set[tuple[str, str]] = set()
    for net in board.nets.values():
        for r, q in net.pins:
            connected.add((r, str(q)))
    ncs: set[str] = set()
    for c in board.constraints:
        if isinstance(c, dict) and c.get("t") == "nc":
            ncs.update(_strs(c.get("pins", [])))
    # nc entries must resolve: a typo'd exemption silently covers nothing
    for rp in sorted(ncs):
        ref, dot, pin = rp.partition(".")
        if not dot or ref not in board.parts:
            err(f"nc on unknown part {rp}")
            continue
        try:
            pins = set(board.parts[ref].pins_of(lib))
        except (KeyError, ValueError):
            continue  # unreadable footprint already errored above
        if pin not in pins:
            err(f"nc on unknown pin {rp}")
    for ref, p in board.parts.items():
        if p.fp not in lib:
            err(f"unknown footprint {p.fp} on {ref}")
            continue
        try:
            pins = set(p.pins_of(lib))
        except (KeyError, ValueError):
            err(f"unreadable footprint {p.fp} on {ref}")
            continue
        for pin in pins:
            if ((ref, pin) not in connected and f"{ref}.{pin}" not in ncs
                    and not pin.startswith("NC")):
                warn(f"unconnected {ref}.{pin}")

    for name, net in board.nets.items():
        if len([1 for r, _ in net.pins if r in board.parts]) == 1:
            warn(f"single-pin net {name}")
        for ref, pin in net.pins:
            if ref not in board.parts:
                err(f"unknown part {ref} on net {name}")
                continue
            try:
                pins = set(board.parts[ref].pins_of(lib))
            except (KeyError, ValueError):
                continue
            if str(pin) not in pins:
                err(f"unknown pin {ref}.{pin} on net {name}")

    for c in board.constraints:
        if not isinstance(c, dict):
            continue
        t = str(c.get("t", ""))
        if t == "near-group":
            pre = str(c.get("prefix", ""))
            if pre and not any(r.startswith(pre) for r in board.parts):
                warn(f"near-group matches no parts with prefix {pre}")
        for k in _PARTKEYS:
            if k not in c:
                continue
            for v in _strs(c.get(k, "")):
                if v and v not in board.parts:
                    err(f"{t} on unknown part {v}")
        for k in _NETKEYS:
            if k not in c:
                continue
            for v in _strs(c.get(k, "")):
                if v and v not in board.nets:
                    # kind disambiguates (sim probe vs sim vcc on one board)
                    kind = f" {c['kind']}" if "kind" in c else ""
                    warn(f"{t}{kind} on unknown net {v}")
        if t in _RANGES:
            k, lo, hi = _RANGES[t]
            try:
                num = float(cast(float, c.get(k, lo)))
            except (TypeError, ValueError):
                err(f"{t} has non-numeric {k} {c.get(k)!r}")
                continue
            if not lo <= num <= hi:
                warn(f"{t} {k}={num:g} outside sane range [{lo:g}..{hi:g}]")
        if t == "pour" and str(c.get("net", "")) in board.nets:
            try:
                ll = int(cast(int, c.get("layer", -1)))
            except (TypeError, ValueError):
                err(f"pour {c.get('net')} has non-numeric layer")
                continue
            if not 0 <= ll < board.layers:
                err(f"pour {c.get('net')} on layer {ll} (board has {board.layers}L)")
        if t == "fixed" and str(c.get("ref", "")) in board.parts:
            p = board.parts[str(c.get("ref"))]
            pw, ph = p.wh()
            try:
                x, y = float(cast(float, c.get("x", 0))), float(cast(float, c.get("y", 0)))
            except (TypeError, ValueError):
                err(f"fix {c.get('ref')} has non-numeric position")
                continue
            if not (pw / 2 <= x <= board.width - pw / 2
                    and ph / 2 <= y <= board.height - ph / 2):
                warn(f"fix {c.get('ref')} off-board")
        if t in ("hole", "cutout", "bend", "stiffener") or (
                t == "keepout" and c.get("ref") is None):
            # geometry with explicit x/y: same off-board smell as fix
            # (keepout-near-ref anchors to its part, always on-board)
            try:
                gx, gy = float(cast(float, c.get("x", 0))), float(cast(float, c.get("y", 0)))
            except (TypeError, ValueError):
                err(f"{t} has non-numeric position")
                continue
            if not (0 <= gx <= board.width and 0 <= gy <= board.height):
                warn(f"{t} off-board")
        elif t == "layer" and str(c.get("net", "")) in board.nets:
            try:
                ll = int(cast(int, c.get("layer", 0)))
            except (TypeError, ValueError):
                err(f"route {c.get('net')} has non-numeric layer")
                continue
            if not 0 <= ll < board.layers:
                err(f"route {c.get('net')} on layer {ll} (board has {board.layers}L)")

    from .export import MASK_COLORS
    mask = str(board.meta.get("mask", "green")).lower()
    if mask not in MASK_COLORS:
        # renderers silently fall back to green — flag the typo here instead
        warn(f"unknown mask {board.meta.get('mask')!r} (have {sorted(MASK_COLORS)})")
    from .gates import GATES
    for ref, p in board.parts.items():
        logic = p.attrs.get("logic", "")
        # the gates simulator silently skips unknown kinds — flag it here
        if logic and logic.upper() not in GATES:
            warn(f"unknown logic {logic!r} on {ref} (have {sorted(GATES)})")
        sym = p.attrs.get("sym", "")
        # unknown sym= crashes the schematic renderer — flag it here
        # (svg uses footprints, so the typo hides until sch export)
        if sym and sym not in board.custom_sym:
            from .symbol import SYMBOLS
            if sym not in SYMBOLS:
                warn(f"unknown sym {sym!r} on {ref} (have {sorted(SYMBOLS)})")
    if not board.parts:
        warn("no parts")
    return {"errors": errors, "warnings": warnings}
