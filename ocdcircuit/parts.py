"""Parametric footprint generator + 3D body models. Stdlib only.

Every footprint: courtyard w/h (for placement), SMD pads or PTH holes,
and 3D bodies (boxes/cylinders extruded in the STL renderer).

Conventions (mm): chip_<len>x<wid> body; pad pitch p along x for 2-pin.
SOT/SOIC/QFP/QFN/BGA generated from pitch/count/span params.
PTH: pin-1 at (0,0), rest step +2.54mm x; drill + annular ring.
"""
from __future__ import annotations
from .types import Footprint, HoleSpec, PadSpec, PinLike, XY


def chip(w: float, h: float, pw: float = 0.9, gap: float | None = None, h3d: float = 0.55) -> Footprint:
    """2-pin chip: pads of width pw, inner gap = width-2*pw clamp."""
    g = w - 2 * pw if gap is None else gap
    px = (g / 2 + pw / 2)
    return {"w": w + 1.2, "h": h + 0.6,
            "pads": {"1": (-px, 0, pw, h + 0.4), "2": (px, 0, pw, h + 0.4)},
            "bodies": [{"box": (w / 2, h / 2, h3d)}]}


def sot23(h3d: float = 1.1) -> Footprint:
    return {"w": 2.9 + 1.2, "h": 2.4 + 0.8,
            "pads": {"1": (-0.95, -0.95, 0.8, 0.9), "2": (-0.95, 0.95, 0.8, 0.9),
                     "3": (0.95, 0, 0.8, 0.9)},
            "bodies": [{"box": (1.45, 1.15, h3d)}]}


def sot223(h3d: float = 1.6) -> Footprint:
    return {"w": 7.0 + 1.2, "h": 6.5 + 0.8,
            "pads": {"1": (-2.3, -2.3, 1.2, 1.6), "2": (-2.3, 0, 1.2, 1.6),
                     "3": (-2.3, 2.3, 1.2, 1.6), "4": (2.3, 0, 2.5, 4.5)},
            "bodies": [{"box": (3.5, 3.25, h3d)}]}


def sot89(h3d: float = 1.5) -> Footprint:
    return {"w": 4.5 + 1.2, "h": 4.2 + 0.8,
            "pads": {"1": (-1.5, -1.5, 1.0, 1.2), "2": (-1.5, 1.5, 1.0, 1.2),
                     "3": (1.5, 0, 1.8, 3.5)},
            "bodies": [{"box": (2.3, 4.2, h3d)}]}


def sot363(h3d: float = 1.1) -> Footprint:
    pads = {}
    for i in range(3):
        y = 0.65 - i * 0.65
        pads[str(i + 1)] = (-0.65, y, 0.5, 0.35)
        pads[str(i + 4)] = (0.65, -y, 0.5, 0.35)
    return {"w": 2.5, "h": 2.5, "pads": pads,
            "bodies": [{"box": (1.25, 1.1, h3d)}]}


def dpak(h3d: float = 2.3) -> Footprint:
    return {"w": 10.0, "h": 7.0,
            "pads": {"1": (-2.3, -2.3, 1.4, 1.6), "2": (-2.3, 0, 1.4, 1.6),
                     "3": (-2.3, 2.3, 1.4, 1.6), "4": (2.0, 0, 3.5, 5.5)},
            "bodies": [{"box": (6.5, 6.0, h3d)}]}


def d2pak(h3d: float = 4.5) -> Footprint:
    return {"w": 12.0, "h": 11.0,
            "pads": {"1": (-2.3, -2.75, 1.6, 2.0), "2": (-2.3, 0, 1.6, 2.0),
                     "3": (-2.3, 2.75, 1.6, 2.0), "4": (2.5, 0, 4.5, 8.0)},
            "bodies": [{"box": (10.0, 9.0, h3d)}]}


def msop(n: int, pitch: float = 0.5, span: float = 4.9, h3d: float = 1.1) -> Footprint:
    return soic(n, pitch, span, h3d)


def dfn(n: int = 8, pitch: float = 0.5, span: float = 3.3, h3d: float = 0.85, ep: float = 1.7) -> Footprint:
    pads, per = {}, n // 2
    for i in range(per):
        o = (per - 1) * pitch / 2 - i * pitch
        pads[str(1 + i)] = (o, span / 2, 0.3, 0.9)
        pads[str(1 + per + i)] = (-o, -span / 2, 0.3, 0.9)
    pads["EP"] = (0, 0, ep, ep)
    return {"w": (per - 1) * pitch + 2.0, "h": span + 1.8, "pads": pads,
            "bodies": [{"box": ((per - 1) * pitch + 1.2, span - 1.2, h3d)}]}


def soic(n: int, pitch: float = 1.27, span: float = 5.4, h3d: float = 1.75) -> Footprint:
    pads, per = {}, n // 2
    for i in range(1, per + 1):
        y = (per - 1) * pitch / 2 - (i - 1) * pitch
        pads[str(i)] = (-span / 2, y, 1.0, 0.6)
        pads[str(n - i + 1)] = (span / 2, y, 1.0, 0.6)
    return {"w": span + 2.2, "h": (per - 1) * pitch + 2.4, "pads": pads,
            "bodies": [{"box": (span - 2.0, (per - 1) * pitch + 1.0, h3d)}]}


def ssop(n: int, pitch: float = 0.65, span: float = 5.3, h3d: float = 1.5) -> Footprint:
    return soic(n, pitch, span, h3d)


def tssop(n: int, pitch: float = 0.65, span: float = 5.0, h3d: float = 1.2) -> Footprint:
    return soic(n, pitch, span, h3d)


def qfp(n: int, pitch: float = 0.5, span: float = 9.0, h3d: float = 1.4) -> Footprint:
    """Square QFP: n divisible by 4, pads on 4 sides."""
    pads, per = {}, n // 4
    for i in range(per):
        o = (per - 1) * pitch / 2 - i * pitch
        pads[str(1 + i)] = (-span / 2, o, 1.0, 0.3)
        pads[str(1 + per + i)] = (o, span / 2, 0.3, 1.0)
        pads[str(1 + 2 * per + i)] = (span / 2, -o, 1.0, 0.3)
        pads[str(1 + 3 * per + i)] = (-o, -span / 2, 0.3, 1.0)
    return {"w": span + 2.2, "h": span + 2.2, "pads": pads,
            "bodies": [{"box": (span - 2.0, span - 2.0, h3d)}]}


def qfn(n: int, pitch: float = 0.5, span: float = 5.0, h3d: float = 0.85, ep: float = 3.0) -> Footprint:
    """QFN + exposed thermal pad (pin 'EP')."""
    pads, per = {}, n // 4
    for i in range(per):
        o = (per - 1) * pitch / 2 - i * pitch
        pads[str(1 + i)] = (-span / 2, o, 0.9, 0.28)
        pads[str(1 + per + i)] = (o, span / 2, 0.28, 0.9)
        pads[str(1 + 2 * per + i)] = (span / 2, -o, 0.9, 0.28)
        pads[str(1 + 3 * per + i)] = (-o, -span / 2, 0.28, 0.9)
    pads["EP"] = (0, 0, ep, ep)
    return {"w": span + 1.8, "h": span + 1.8, "pads": pads,
            "bodies": [{"box": (span - 1.6, span - 1.6, h3d)}]}


def bga_rect(rows: int, cols: int, pitch: float = 0.8, h3d: float = 1.0) -> Footprint:
    """Rectangular BGA (e.g. 6x8=48)."""
    ex, ey = (cols - 1) * pitch / 2, (rows - 1) * pitch / 2
    pads = {str(1 + r * cols + c): (-ex + c * pitch, ey - r * pitch, 0.4, 0.4)
            for r in range(rows) for c in range(cols)}
    return {"w": 2 * ex + 2.0, "h": 2 * ey + 2.0, "pads": pads,
            "bodies": [{"box": (ex * 2 + 0.5, ey * 2 + 0.5, h3d)}]}


def bga(n: int, pitch: float = 0.8, h3d: float = 1.0) -> Footprint:
    """Square BGA n balls; courtyard = array + 1mm."""
    import math
    per = math.isqrt(n)
    assert per * per == n, "BGA count must be a square"
    ext = (per - 1) * pitch / 2
    pads = {str(1 + r * per + c): (-ext + c * pitch, ext - r * pitch, 0.4, 0.4)
            for r in range(per) for c in range(per)}
    return {"w": 2 * ext + 2.0, "h": 2 * ext + 2.0, "pads": pads,
            "bodies": [{"box": (ext * 2 + 0.5, ext * 2 + 0.5, h3d)}]}


def pinheader(n: int, pitch: float = 2.54, h3d: float = 8.5, hole: float = 1.0) -> Footprint:
    holes = {str(i + 1): (i * pitch, 0, hole) for i in range(n)}
    return {"w": (n - 1) * pitch + 2.0, "h": pitch + 1.6, "holes": holes,
            "bodies": [{"box": ((n - 1) * pitch + 1.0, 1.6, 2.5), "z": 0},
                       {"box": (0.64, 0.64, h3d), "at": [(i * pitch, 0) for i in range(n)], "z": 2.5}]}


def pinheader2x(n: int, pitch: float = 2.54, h3d: float = 8.5, hole: float = 1.0) -> Footprint:
    """2-row header: pins 1..n top row, n+1..2n bottom."""
    holes = {}
    for i in range(n):
        holes[str(i + 1)] = (i * pitch, pitch / 2, hole)
        holes[str(n + i + 1)] = (i * pitch, -pitch / 2, hole)
    return {"w": (n - 1) * pitch + 2.0, "h": 2 * pitch + 1.6, "holes": holes,
            "bodies": [{"box": ((n - 1) * pitch + 1.0, 2 * pitch, 2.5), "z": 0}]}


def usb_micro(h3d: float = 1.9) -> Footprint:
    pads = {str(i + 1): (-1.0 + i * 0.65, -1.2, 0.35, 1.0) for i in range(5)}
    pads.update({"S1": (-2.8, 0, 0.8, 3.0), "S2": (2.8, 0, 0.8, 3.0)})
    holes = {"M1": (-3.5, -2.0, 0.8), "M2": (3.5, -2.0, 0.8)}
    return {"w": 8.0, "h": 6.0, "pads": pads, "holes": holes,
            "bodies": [{"box": (7.5, 5.9, h3d)}]}


def usb_mini(h3d: float = 3.0) -> Footprint:
    pads = {str(i + 1): (-1.0 + i * 0.8, -1.4, 0.4, 1.1) for i in range(5)}
    pads.update({"S1": (-3.2, 0, 0.9, 3.4), "S2": (3.2, 0, 0.9, 3.4)})
    holes = {"M1": (-3.8, -2.2, 0.9), "M2": (3.8, -2.2, 0.9)}
    return {"w": 9.0, "h": 7.0, "pads": pads, "holes": holes,
            "bodies": [{"box": (8.0, 8.0, h3d)}]}


def jst(n: int, pitch: float = 2.5, h3d: float = 8.0, hole: float = 1.1) -> Footprint:
    holes = {str(i + 1): (i * pitch, 0, hole) for i in range(n)}
    return {"w": (n - 1) * pitch + 4.0, "h": 7.0, "holes": holes,
            "bodies": [{"box": ((n - 1) * pitch + 2.5, 5.8, h3d)}]}


def sdcard(h3d: float = 1.9) -> Footprint:
    pads = {str(i + 1): (-4.4 + i * 1.1, -2.5, 0.7, 1.6) for i in range(9)}
    pads.update({"S1": (-6.5, 0, 1.0, 5.0), "S2": (6.5, 0, 1.0, 5.0)})
    return {"w": 15.0, "h": 15.0, "pads": pads,
            "bodies": [{"box": (14.0, 14.5, h3d)}]}


def osc4(h3d: float = 1.0) -> Footprint:
    return {"w": 5.0 + 1.2, "h": 3.2 + 0.8,
            "pads": {"1": (-1.55, -0.8, 1.0, 0.9), "2": (1.55, -0.8, 1.0, 0.9),
                     "3": (1.55, 0.8, 1.0, 0.9), "4": (-1.55, 0.8, 1.0, 0.9)},
            "bodies": [{"box": (5.0, 3.2, h3d)}]}


def inductor(body: tuple[float, float], h: float, h3d: float = 1.2) -> Footprint:
    w, hh = body
    return chip(w, hh, h3d=h3d)


def fiducial(d: float = 1.0) -> Footprint:
    return {"w": d + 2.0, "h": d + 2.0, "pads": {"1": (0, 0, d, d)}, "bodies": []}


def usb_c(h3d: float = 3.3) -> Footprint:
    pads = {f"A{i + 1}": (-2.75 + i * 0.5, -1.6, 0.3, 1.0) for i in range(12)}
    pads.update({f"B{i + 1}": (-2.75 + i * 0.5, 1.6, 0.3, 1.0) for i in range(12)})
    pads.update({"S1": (-4.2, 0, 1.0, 3.0), "S2": (4.2, 0, 1.0, 3.0)})
    holes = {"M1": (-4.5, -2.2, 0.8), "M2": (4.5, -2.2, 0.8),
             "M3": (-4.5, 2.2, 0.8), "M4": (4.5, 2.2, 0.8)}
    return {"w": 10.2, "h": 7.0, "pads": pads, "holes": holes,
            "bodies": [{"box": (8.9, 7.5, h3d)}]}


def crystal(h3d: float = 0.6) -> Footprint:
    return {"w": 3.2 + 1.2, "h": 2.5 + 0.6,
            "pads": {"1": (-1.1, 0, 1.0, 1.2), "2": (1.1, 0, 1.0, 1.2)},
            "bodies": [{"box": (3.2, 2.5, h3d)}]}


def electrolytic(d: float = 6.3, h3d: float = 7.7, hole: float = 0.8) -> Footprint:
    return {"w": d + 2.0, "h": d + 2.0,
            "holes": {"+": (-d / 4, 0, hole), "-": (d / 4, 0, hole)},
            "bodies": [{"cyl": (d / 2, h3d)}]}


def barrel_jack(h3d: float = 11.0) -> Footprint:
    return {"w": 14.0, "h": 12.0,
            "holes": {"1": (-3.5, 0, 1.2), "2": (0, 0, 1.2), "3": (3.5, 0, 1.2)},
            "bodies": [{"box": (9.0, 14.0, h3d)}]}


def terminal2(h3d: float = 9.0, pitch: float = 5.08) -> Footprint:
    return {"w": pitch + 4.0, "h": 9.0,
            "holes": {"1": (-pitch / 2, 0, 1.3), "2": (pitch / 2, 0, 1.3)},
            "bodies": [{"box": (pitch + 2.5, 8.0, h3d)}]}


def mounting_hole(d: float = 3.2) -> Footprint:
    return {"w": d + 2.0, "h": d + 2.0, "holes": {"1": (0, 0, d)}, "bodies": []}


# pad-stack defaults per family for fab export
PTH_DRILL = 0.8
PTH_ANNULAR = 0.35

FOOTPRINTS = {
    # chips: R/C/L/LED/diode (0201/0402/0603/0805/1206/1210/2512)
    "R0201": chip(0.6, 0.3, h3d=0.25), "R0402": chip(1.0, 0.5),
    "R0603": chip(1.6, 0.8), "R0805": chip(2.0, 1.25),
    "R1206": chip(3.2, 1.6), "R1210": chip(3.2, 2.5, h3d=0.7),
    "R2512": chip(6.4, 3.2, h3d=0.7),
    "C0201": chip(0.6, 0.3, h3d=0.3), "C0402": chip(1.0, 0.5, h3d=0.5),
    "C0603": chip(1.6, 0.8, h3d=0.8), "C0805": chip(2.0, 1.25, h3d=0.9),
    "C1206": chip(3.2, 1.6, h3d=1.2), "C1210": chip(3.2, 2.5, h3d=1.4),
    "LED0201": chip(0.6, 0.3, h3d=0.25), "LED0402": chip(1.0, 0.5, h3d=0.4),
    "LED0603": chip(1.6, 0.8, h3d=0.6), "LED0805": chip(2.0, 1.25, h3d=0.7),
    "LED1206": chip(3.2, 1.6, h3d=0.8),
    "D_SOD323": chip(1.7, 1.25, h3d=0.9), "D_SOD123": chip(2.7, 1.6, h3d=1.0),
    "D_SMA": chip(4.6, 2.6, h3d=2.0), "D_SMB": chip(5.3, 3.6, h3d=2.2),
    "D_SMC": chip(7.0, 5.9, h3d=2.2),
    "L0805": chip(2.0, 1.25, h3d=1.0), "L1206": chip(3.2, 1.6, h3d=1.2),
    "IND_SM0805": chip(2.0, 1.25, h3d=1.2), "IND_SM1206": chip(3.2, 1.6, h3d=2.0),
    # transistors
    "SOT23": sot23(), "SOT363": sot363(), "SOT89": sot89(), "SOT223": sot223(),
    "DPAK": dpak(), "D2PAK": d2pak(),
    # ICs
    "SOIC8": soic(8), "SOIC14": soic(14), "SOIC16": soic(16),
    "SOIC20": soic(20), "SOIC28": soic(28),
    "SSOP16": ssop(16), "SSOP20": ssop(20), "SSOP28": ssop(28),
    "TSSOP14": tssop(14), "TSSOP16": tssop(16), "TSSOP20": tssop(20),
    "TSSOP28": tssop(28), "MSOP8": msop(8), "MSOP10": msop(10),
    "DFN8": dfn(),
    "QFP32": qfp(32), "QFP44": qfp(44), "QFP48": qfp(48),
    "QFP64": qfp(64, span=12.0), "QFP100": qfp(100, span=14.0),
    "QFP128": qfp(128, span=16.0),
    "QFN16": qfn(16), "QFN20": qfn(20), "QFN24": qfn(24), "QFN28": qfn(28),
    "QFN32": qfn(32), "QFN40": qfn(40), "QFN48": qfn(48),
    "BGA48": bga_rect(6, 8), "BGA49": bga(49),
    "BGA64": bga(64), "BGA100": bga(100), "BGA256": bga(256),
    # connectors / PTH
    "PINHD2": pinheader(2), "PINHD3": pinheader(3), "PINHD4": pinheader(4),
    "PINHD5": pinheader(5), "PINHD6": pinheader(6), "PINHD8": pinheader(8),
    "PINHD10": pinheader(10),
    "PINHD2X2": pinheader2x(2), "PINHD2X3": pinheader2x(3),
    "PINHD2X4": pinheader2x(4), "PINHD2X5": pinheader2x(5),
    "PINHD2X10": pinheader2x(10),
    "JST2": jst(2), "JST3": jst(3), "JST4": jst(4),
    "USB_C": usb_c(), "USB_MICRO": usb_micro(), "USB_MINI": usb_mini(),
    "BARREL": barrel_jack(), "TERMINAL2": terminal2(),
    "TERMINAL3": {"w": 2 * 5.08 + 4.0, "h": 9.0,
                  "holes": {"1": (-5.08, 0, 1.3), "2": (0, 0, 1.3), "3": (5.08, 0, 1.3)},
                  "bodies": [{"box": (2 * 5.08 + 2.5, 8.0, 9.0)}]},
    "SDCARD": sdcard(),
    # passives / clock / mounting
    "ELEC_5MM": electrolytic(5.0, 5.0), "ELEC_6MM": electrolytic(),
    "ELEC_8MM": electrolytic(8.0, 10.0), "ELEC_10MM": electrolytic(10.0, 10.0),
    "XTAL_3225": crystal(), "XTAL_5032": {"w": 5.0 + 1.2, "h": 3.2 + 0.8,
        "pads": {"1": (-1.6, 0, 1.2, 1.4), "2": (1.6, 0, 1.2, 1.4)},
        "bodies": [{"box": (5.0, 3.2, 0.8)}]},
    "OSC4": osc4(),
    "MOUNT_M2": mounting_hole(2.2), "MOUNT_M25": mounting_hole(2.7),
    "MOUNT_M3": mounting_hole(), "FIDUCIAL": fiducial(),
}

# backwards-compat: pin name → (dx, dy) for the 6 legacy footprints
_LEGACY_PINS: dict[str, dict[str, XY]] = {
    "R0805": {"1": (-0.95, 0), "2": (0.95, 0)},
    "C0805": {"1": (-0.95, 0), "2": (0.95, 0)},
    "LED0805": {"1": (-0.95, 0), "2": (0.95, 0)},
    "SOIC8": {"1": (-2.55, -1.905), "2": (-2.55, -0.635), "3": (-2.55, 0.635),
              "4": (-2.55, 1.905), "5": (2.55, 1.905), "6": (2.55, 0.635),
              "7": (2.55, -0.635), "8": (2.55, -1.905)},
    "PINHD2": {"1": (-1.27, 0), "2": (1.27, 0)},
    "SOT23": {"1": (-0.95, -0.65), "2": (-0.95, 0.65), "3": (0.95, 0)},
}


def pads_of(fp: str) -> dict[str, XY]:
    """{pin: (dx, dy)} pad centers — what solver/DRC/export need."""
    from typing import cast
    meta = FOOTPRINTS[fp]
    if "pads" in meta:
        pads = cast(dict[str, PadSpec], meta["pads"])
        return {k: (v[0], v[1]) for k, v in pads.items()}
    if "holes" in meta:
        holes = cast(dict[str, HoleSpec], meta["holes"])
        return {k: (v[0], v[1]) for k, v in holes.items()}
    return {}


def pad_size(fp: str, pin: PinLike) -> tuple[float, float]:
    from typing import cast
    meta = FOOTPRINTS[fp]
    pads = cast(dict[str, PadSpec], meta.get("pads", {}))
    if str(pin) in pads:
        return (pads[str(pin)][2], pads[str(pin)][3])
    return (1.0, 1.0)


def hole_drill(fp: str, pin: PinLike) -> float:
    from typing import cast
    meta = FOOTPRINTS[fp]
    holes = cast(dict[str, HoleSpec], meta.get("holes", {}))
    if str(pin) in holes:
        return holes[str(pin)][2]
    return 0.0


def pin_offset(fp: str, pin: PinLike) -> XY:
    """Legacy (dx, dy) API — exact old values for the 6 legacy footprints."""
    if fp in _LEGACY_PINS and str(pin) in _LEGACY_PINS[fp]:
        return _LEGACY_PINS[fp][str(pin)]
    return pads_of(fp)[str(pin)]


def bodies_of(fp: str) -> list[Footprint]:
    from typing import cast
    return cast(list[Footprint], FOOTPRINTS[fp].get("bodies", []))


def courtyard(fp: str) -> tuple[float, float]:
    from typing import cast
    m = FOOTPRINTS[fp]
    return (cast(float, m["w"]), cast(float, m["h"]))
