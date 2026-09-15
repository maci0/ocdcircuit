"""Board recommendations: add protection, cut waste. Report-only.

Looks at parts/nets as loaded (no place/route). Each hit is a dict with
`id`/`kind`/`severity`/`msg` plus `ops` — the same agent.apply_patch list
an agent would run. Applying is the caller's job; this never mutates.

# ponytail: no series-insert (fuse / LED resistor) — apply_patch can't
# disconnect a pin. Add a disconnect op if that starts mattering.
"""
from __future__ import annotations
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board, Part

_RAIL = re.compile(r"^(GND|VSS|VCC|VDD|VIN|VBUS|VBAT|5V|3V3|1V8|12V|9V)$", re.I)
_GND = re.compile(r"^(GND|VSS|AGND|DGND|PGND)$", re.I)
_CONN = ("PINHD", "USB_", "JST", "BARREL", "TERMINAL", "SDCARD", "HDR")
_PASSIVE = ("R", "C", "L", "D_", "LED", "IND_", "ELEC_", "XTAL", "OSC")
_ZERO_R = re.compile(r"^0(?:\s*r|\s*ohm)?$|^0r0$|^0\.0$", re.I)
_ZERO_C = re.compile(r"^0(?:f|p|n|u)?$", re.I)


def _power_nets(board: Board) -> set[str]:
    names = {n for n in board.nets if _RAIL.match(n)}
    for c in board.constraints:
        if c.get("t") == "power":
            names.update(str(x) for x in cast(list[object], c.get("nets", [])))
    return names


def _gnd_nets(board: Board) -> set[str]:
    names = {n for n in board.nets if _GND.match(n)}
    if "GND" in board.nets:
        names.add("GND")
    return names


def _is_ic(p: Part) -> bool:
    fp = p.fp.upper()
    if any(fp.startswith(x) for x in _CONN) or fp.startswith("MOUNT"):
        return False
    return not any(fp.startswith(x) for x in _PASSIVE)


def _is_cap(p: Part) -> bool:
    fp = p.fp.upper()
    return fp.startswith("ELEC_") or (fp.startswith("C") and fp[1:2].isdigit())


def _is_res(p: Part) -> bool:
    fp = p.fp.upper()
    return fp.startswith("R") and fp[1:2].isdigit()


def _is_diode(p: Part) -> bool:
    fp = p.fp.upper()
    return fp.startswith("D_") or any(x in fp for x in ("SOD", "SMA", "SMB", "SMC"))


def _is_usb(p: Part) -> bool:
    return "USB" in p.fp.upper()


def _is_bulk(p: Part) -> bool:
    return p.fp.upper().startswith("ELEC_") or "u" in p.value.lower()


def recommend(board: Board) -> dict[str, object]:
    """{"items": [...] }. Never mutates. ops are apply_patch-ready."""
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    taken = set(board.parts)

    def fresh(prefix: str) -> str:
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        ref = f"{prefix}{n}"
        taken.add(ref)
        return ref

    def add(item: dict[str, object]) -> None:
        i = str(item["id"])
        if i in seen:
            return
        seen.add(i)
        items.append(item)

    pin_net: dict[tuple[str, str], str] = {}
    for n, net in board.nets.items():
        for ref, pin in net.pins:
            pin_net[(ref, str(pin))] = n

    pins: dict[str, list[str]] = {}
    lib = board._lib()
    for ref, p in board.parts.items():
        try:
            pins[ref] = list(p.pins_of(lib))
        except (KeyError, ValueError):
            pins[ref] = []

    def nets_of(ref: str) -> set[str]:
        return {pin_net[k] for pin in pins[ref]
                if (k := (ref, pin)) in pin_net}

    def bridges(pred: Callable[[Part], bool]) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for ref, p in board.parts.items():
            if not pred(p):
                continue
            ns = [n for n in nets_of(ref) if n]
            for i, a in enumerate(ns):
                for b in ns[i + 1:]:
                    out.add((a, b))
                    out.add((b, a))
        return out

    cap_xy = bridges(_is_cap)
    diode_xy = bridges(_is_diode)
    bulk_xy = bridges(lambda p: _is_cap(p) and _is_bulk(p))

    power = _power_nets(board)
    gnds = _gnd_nets(board)
    gnd = next(iter(sorted(gnds)), None)

    def part_rails(ref: str) -> set[str]:
        found: set[str] = set()
        for pin in pins[ref]:
            n = pin_net.get((ref, pin))
            if n is not None and n in power and n not in gnds:
                found.add(n)
        return found

    for ref, p in board.parts.items():
        if not _is_ic(p) or gnd is None:
            continue
        for rail in sorted(part_rails(ref)):
            if (rail, gnd) in cap_xy:
                continue
            cref = fresh("C")
            add({
                "id": f"decouple:{ref}:{rail}",
                "kind": "add",
                "severity": "warn",
                "msg": f"add 100n decoupling on {rail} next to {ref}",
                "ops": [
                    {"op": "add_part", "ref": cref, "fp": "C0805", "value": "100n"},
                    {"op": "connect", "net": rail, "ref": cref, "pin": "1"},
                    {"op": "connect", "net": gnd, "ref": cref, "pin": "2"},
                    {"op": "constrain", "c": {"t": "near", "a": cref, "b": ref, "w": 3}},
                ],
            })

    if gnd is not None:
        ic_rails: set[str] = set()
        for r, p in board.parts.items():
            if _is_ic(p):
                ic_rails.update(part_rails(r))
        for rail in sorted(ic_rails):
            if (rail, gnd) in bulk_xy:
                continue
            cref = fresh("C")
            add({
                "id": f"bulk:{rail}",
                "kind": "add",
                "severity": "info",
                "msg": f"add bulk cap on {rail} (10u electrolytic)",
                "ops": [
                    {"op": "add_part", "ref": cref, "fp": "ELEC_6MM", "value": "10u"},
                    {"op": "connect", "net": rail, "ref": cref, "pin": "+"},
                    {"op": "connect", "net": gnd, "ref": cref, "pin": "-"},
                ],
            })

    usb = [r for r, p in board.parts.items() if _is_usb(p)]
    if usb and gnd is not None:
        vbus = next((n for n in board.nets if n.upper() in ("VBUS", "VCC", "5V")), None)
        if vbus and (vbus, gnd) not in diode_xy:
            dref = fresh("D")
            host = usb[0]
            add({
                "id": f"tvs:{vbus}",
                "kind": "add",
                "severity": "warn",
                "msg": f"add TVS on {vbus} at {host} (ESD)",
                "ops": [
                    {"op": "add_part", "ref": dref, "fp": "D_SOD323", "value": "TVS"},
                    {"op": "connect", "net": vbus, "ref": dref, "pin": "1"},
                    {"op": "connect", "net": gnd, "ref": dref, "pin": "2"},
                    {"op": "constrain", "c": {"t": "near", "a": dref, "b": host, "w": 3}},
                ],
            })

    if gnd is not None:
        for ref, p in board.parts.items():
            fp = p.fp.upper()
            if not (fp.startswith("BARREL") or fp.startswith("TERMINAL")):
                continue
            hits = sorted(part_rails(ref))
            if len(hits) != 1 or (hits[0], gnd) in diode_xy:
                continue
            dref = fresh("D")
            add({
                "id": f"revpol:{ref}",
                "kind": "add",
                "severity": "info",
                "msg": f"add reverse-polarity diode at {ref} on {hits[0]}",
                "ops": [
                    {"op": "add_part", "ref": dref, "fp": "D_SOD123", "value": "SS14"},
                    {"op": "connect", "net": hits[0], "ref": dref, "pin": "1"},
                    {"op": "connect", "net": gnd, "ref": dref, "pin": "2"},
                    {"op": "constrain", "c": {"t": "near", "a": dref, "b": ref, "w": 3}},
                ],
            })

    for ref, p in board.parts.items():
        if p.attrs.get("dnp") in ("1", "true", "yes"):
            add({
                "id": f"dnp:{ref}",
                "kind": "cut",
                "severity": "info",
                "msg": f"cut {ref} (marked DNP)",
                "ops": [{"op": "remove_part", "ref": ref}],
            })
            continue
        val = p.value.strip()
        if _is_res(p) and _ZERO_R.match(val):
            add({
                "id": f"zero:{ref}",
                "kind": "cut",
                "severity": "info",
                "msg": f"cut {ref} (0Ω jumper — short the net instead)",
                "ops": [{"op": "remove_part", "ref": ref}],
            })
            continue
        if _is_cap(p) and val and _ZERO_C.match(val):
            add({
                "id": f"zeroc:{ref}",
                "kind": "cut",
                "severity": "info",
                "msg": f"cut {ref} (0-value capacitor)",
                "ops": [{"op": "remove_part", "ref": ref}],
            })
            continue
        ps = pins[ref]
        if len(ps) == 2:
            n1, n2 = pin_net.get((ref, ps[0])), pin_net.get((ref, ps[1]))
            if n1 and n1 == n2:
                add({
                    "id": f"shorted:{ref}",
                    "kind": "cut",
                    "severity": "warn",
                    "msg": f"cut {ref} (both pins on {n1})",
                    "ops": [{"op": "remove_part", "ref": ref}],
                })

    items.sort(key=lambda x: ({"warn": 0, "info": 1}.get(str(x["severity"]), 9),
                               str(x["id"])))
    return {"items": items}
