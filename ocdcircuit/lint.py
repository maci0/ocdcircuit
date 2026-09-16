"""Static lint for .ocd sources: fast, no place/route. Pure checks over the
parsed board (parts/nets/constraints as loaded) — style, hygiene, and
likely-silly before the solvers ever run. DRC/ERC own geometry/electrics.
"""
from __future__ import annotations
from bisect import bisect_left
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
           "stiffener": ("th", 0.05, 3.0), "edge": ("margin", 0.0, 20.0),
           "route-grid": ("grid", 0.05, 5.0)}


def _strs(v: object) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def _has_prefix(refs: list[str], pre: str) -> bool:
    """Does any ref start with `pre`? Sorted refs make this a bisect instead of
    a scan: a block/instance board carries one `near-group` per instance, and
    scanning every part for each was 6.37M startswith calls — 0.6s of a 0.6s
    lint on the 5420-part test board."""
    if not pre:
        return True
    i = bisect_left(refs, pre)
    return i < len(refs) and refs[i].startswith(pre)


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

    # pin names per footprint: pins_of() rebuilds the pad dict and a big
    # board validates 33.5k pins (~40ms of lint). `lib` is fixed for this
    # call, so one lookup per footprint is the same answer.
    pins_cache: dict[str, set[str]] = {}
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
            p = board.parts[ref]
            cached = pins_cache.get(p.fp)
            if cached is None:
                try:
                    cached = set(p.pins_of(lib))
                except (KeyError, ValueError):
                    continue  # not cached: a failing lib stays failing
                pins_cache[p.fp] = cached
            if str(pin) not in cached:
                err(f"unknown pin {ref}.{pin} on net {name}")

    sorted_refs = sorted(board.parts)  # once, for the prefix probes below
    for c in board.constraints:
        if not isinstance(c, dict):
            continue
        t = str(c.get("t", ""))
        if t == "near-group":
            pre = str(c.get("prefix", ""))
            if pre and not _has_prefix(sorted_refs, pre):
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
            if t == "route-grid" and (not (num > 0) or num != num):
                # zero/NaN divides every maze cell index; warn-range is not enough
                err(f"route-grid grid={num!r} must be positive")
            elif not lo <= num <= hi:
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
        try:
            pins = set(p.pins_of(board._lib()))
        except (KeyError, ValueError):
            pins = set()
        for k in p.attrs:
            # pinN= labels a footprint pin — a typo'd N labels nothing
            if k.startswith("pin") and k[3:] and k[3:] not in pins:
                warn(f"unknown pin label {k}={p.attrs[k]!r} on {ref}")
    for ins in board.instances:
        blk = board.blocks.get(str(ins["block"]))
        if blk is None or not blk.ports:
            continue  # unknown/loose blocks stamp verbatim — nothing to check
        join = ins.get("join")
        joins = {str(j) for j in join} if isinstance(join, list) else set()
        pre = str(ins["prefix"]) + "_"
        for port in blk.ports:
            # a port's job is inward-to-outward: stamped net with pins only
            # inside this instance is an island (three private GNDs, ...).
            stamped = port if port in joins else pre + port
            pnet = board.nets.get(stamped, board.nets.get(port))
            ppins = pnet.pins if pnet is not None else []
            if ppins and all(r.startswith(pre) for r, _ in ppins):
                warn(f"instance {ins['prefix']} leaves port {port} unjoined")
    if not board.parts:
        warn("no parts")
    return {"errors": errors, "warnings": warnings}
