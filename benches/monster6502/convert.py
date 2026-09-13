"""discrete6502 (MOnSter-inspired 6502) netlist -> .ocd benchmark converter.

Source: https://github.com/epatel/discrete6502 (CC BY-NC-SA 4.0, logic from
visual6502 reverse-engineered netlist). Raw files (committed, see
SOURCES.md): gen/netlist.json (components + nets), gen/layout.json
(authoritative true-mm positions).

Mapping: 4051 FETs -> FET_SOT323 (.fp, true 2.0x1.25); R/C0402 -> CHIP0402
(.fp); 55 LEDs -> LED0603 (stdlib); C0805 -> C0805; diodes -> D_SOD323;
36 testpoints -> TP1 (.fp); DNP Pico U1 dropped with its private nets.
Positions: layout.json mm (netlist.json pos units are NOT mil — ignored).
Board 291x322 6L. DNP ballast excluded.

Usage: python3 convert.py  # writes monster6502.ocd (needs netlist.json + layout.json)
# ponytail: no cli args, single-purpose script — flags when reused.
"""
from __future__ import annotations
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
MIL = 39.3701
FP = {
    "Package_TO_SOT_SMD:SOT-323_SC-70": "FET_SOT323",
    "Resistor_SMD:R_0402_1005Metric": "CHIP0402",
    "LED_SMD:LED_0603_1608Metric": "LED0603",  # stdlib (true 0603 size)
    "Capacitor_SMD:C_0402_1005Metric": "CHIP0402",
    "Capacitor_SMD:C_0805_2012Metric": "C0805",
    "Diode_SMD:D_SOD-323": "D_SOD323",
    "TestPoint:TestPoint_THTPad_4.0x4.0mm_Drill2.0mm": "TP1",
}
# DNP Pico site: 40-pin module with no ocd footprint; drop the part, keep its
# nets only where shared with placed parts (vcc/vss), drop pico-private nets.
_DROP_PARTS = {"U1"}
# testpoints/pico have more pins than PINHD2; keep first two connected pins.
_PIN_CAP = {"PINHD2": ("1", "2")}


def _safe_net(n: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", n) or "N"


def main() -> None:
    raw = json.load(open(os.path.join(HERE, "netlist.json")))
    lay = json.load(open(os.path.join(HERE, "layout.json")))
    # positions: layout.json is authoritative (true mm, matches board 290.8x322.1)
    pos_of = {it["ref"]: (float(it["x"]), float(it["y"])) for it in lay["items"]}
    comps: list[dict[str, object]] = raw["components"]
    nets: dict[str, list[list[str]]] = raw["nets"]
    used_fp: set[str] = set()
    for c in comps:
        if str(c["ref"]) in _DROP_PARTS:
            continue
        used_fp.add(str(c["footprint"]))
    assert set(used_fp) <= set(FP), f"unmapped: {set(used_fp) - set(FP)}"
    L = ["board monster6502 291x322 6L",
         "fp fet_sot323.fp", "fp chip0402.fp", "fp testpoint.fp"]
    for c in comps:
        if str(c["ref"]) in _DROP_PARTS:
            continue
        ref, fp = str(c["ref"]), FP[str(c["footprint"])]
        val = str(c.get("value", "") or "")
        L.append(f"part {ref} {fp} {val}".rstrip())
    n_fix = 0
    for c in comps:
        if str(c["ref"]) in _DROP_PARTS:
            continue
        p = pos_of.get(str(c["ref"]))
        if p is None:
            continue  # unplaced (back-side decoupling) — placer decides
        L.append(f"fix {c['ref']} at {p[0]:.2f} {p[1]:.2f}")
        n_fix += 1
    for net, pins in sorted(nets.items()):
        kept = [[r, p] for r, p in pins if r not in _DROP_PARTS]
        if len(kept) < 2:
            continue  # pico-private or orphaned single-pin net
        nm = _safe_net(net)
        ps = " ".join(f"{r}.{p}" for r, p in kept)
        L.append(f"net {nm}: {ps}")
    L.append("power vcc vss")
    open(os.path.join(HERE, "monster6502.ocd"), "w").write("\n".join(L) + "\n")
    print(f"parts={len(comps)} nets={len(nets)} pins={sum(len(v) for v in nets.values())}")


if __name__ == "__main__":
    main()
