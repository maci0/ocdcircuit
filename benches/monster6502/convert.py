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

Usage: python -m benches.monster6502.convert  # writes monster6502.ocd (needs netlist.json + layout.json)
# ponytail: no cli args, single-purpose script — flags when reused.
"""
from __future__ import annotations
import json
import os
import re
from typing import cast

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


def _find_blocks(comps: list[dict[str, object]]
                 ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Repeated units: inverter (pullup R + pulldown Q, shared node),
    pass gate (pass_a + pass_b, shared origin). Returns (inv, passg) pairs."""
    node2pd: dict[str, str] = {}
    for c in comps:
        if c.get("role") == "pulldown":
            pins = cast(dict[str, object], c["pins"])
            node2pd.setdefault(str(pins["3"]), str(c["ref"]))
    inv: list[tuple[str, str]] = []
    used: set[str] = set()
    for c in comps:
        if c.get("role") == "pullup" and str(c["ref"]) not in used:
            pins = cast(dict[str, object], c["pins"])
            q = node2pd.get(str(pins["2"]))
            if q and q not in used:
                inv.append((str(c["ref"]), q))
                used.update((str(c["ref"]), q))
    org2a: dict[str, str] = {}
    for c in comps:
        if c.get("role") == "pass_a":
            org2a.setdefault(str(c["origin"]), str(c["ref"]))
    psg: list[tuple[str, str]] = []
    for c in comps:
        if c.get("role") == "pass_b" and str(c["ref"]) not in used:
            a = org2a.get(str(c["origin"]))
            if a and a not in used:
                psg.append((a, str(c["ref"])))
                used.update((a, str(c["ref"])))
    return inv, psg


def _block_members(comps: list[dict[str, object]]
                   ) -> tuple[dict[str, str], set[str]]:
    """ren: flat ref -> instance-local ref. internal: flat net names fully
    inside one instance (stamper owns them: OUT/MID/...)."""
    raw = json.load(open(os.path.join(HERE, "netlist.json")))
    pin2net: dict[tuple[str, str], str] = {}
    for net, pins in raw["nets"].items():
        for r, p in pins:
            pin2net[(str(r), str(p))] = str(net)
    inv, psg = _find_blocks(comps)
    ren: dict[str, str] = {}
    for i, (r, q) in enumerate(inv):
        ren[r], ren[q] = f"I{i}_R", f"I{i}_Q"
    for i, (a, b) in enumerate(psg):
        ren[a], ren[b] = f"P{i}_A", f"P{i}_B"
    # only suppress when the flat net is EXACTLY the instance pair —
    # fanout nodes (R.2/Q.3 + others) stay on the board
    netpins: dict[str, set[tuple[str, str]]] = {}
    for net, pins in raw["nets"].items():
        netpins[str(net)] = {(str(r), str(p)) for r, p in pins}
    internal: set[str] = set()
    for i, (r, q) in enumerate(inv):
        n = pin2net.get((r, "2"))
        if n and pin2net.get((q, "3")) == n and netpins.get(n) == {(r, "2"), (q, "3")}:
            internal.add(_safe_net(n))
    for i, (a, b) in enumerate(psg):
        n = pin2net.get((a, "2"))
        if n and pin2net.get((b, "2")) == n and netpins.get(n) == {(a, "2"), (b, "2")}:
            internal.add(_safe_net(n))
    return ren, internal


def main() -> None:
    import sys
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: python -m benches.monster6502.convert  # writes monster6502.ocd")
        return
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
    members = _block_members(comps)  # (ren, internal nets) — computed first
    ren, internal = members
    for c in comps:
        if str(c["ref"]) in _DROP_PARTS or str(c["ref"]) in ren:
            continue
        ref, fp = str(c["ref"]), FP[str(c["footprint"])]
        val = str(c.get("value", "") or "")
        L.append(f"part {ref} {fp} {val}".rstrip())
    n_fix = 0
    for c in comps:
        if str(c["ref"]) in _DROP_PARTS or str(c["ref"]) in ren:
            continue  # block members: placer owns instances, no flat fixes
        p = pos_of.get(str(c["ref"]))
        if p is None:
            continue  # unplaced (back-side decoupling) — placer decides
        L.append(f"fix {c['ref']} at {p[0]:.2f} {p[1]:.2f}")
        n_fix += 1
    inv, psg = _find_blocks(comps)
    L.append("block inv")
    L += ["part R CHIP0402 10k", "part Q FET_SOT323 BSS138K",
          "net VCC: R.1", "net OUT: R.2 Q.3", "net GND: Q.2", "net IN: Q.1",
          "end"]
    L.append("block passg")
    L += ["part A FET_SOT323 BSS138K", "part B FET_SOT323 BSS138K",
          "net S1: A.1", "net MID: A.2 B.2", "net S2: A.3",
          "net G: B.1", "net D: B.3", "end"]
    for i, (r, q) in enumerate(inv):
        L.append(f"instance inv as I{i} join vcc vss")
    for i, (a, b) in enumerate(psg):
        L.append(f"instance passg as P{i} join vcc vss")
    for net, pins in sorted(nets.items()):
        kept = [[r, p] for r, p in pins if r not in _DROP_PARTS]
        if len(kept) < 2:
            continue  # pico-private or orphaned single-pin net
        if _safe_net(net) in internal:
            continue  # instance stamper already owns it (OUT/MID/...)
        nm = _safe_net(net)
        ps = " ".join(f"{ren[r]}.{p}" if r in ren else f"{r}.{p}"
                      for r, p in kept)
        L.append(f"net {nm}: {ps}")
    L.append("power vcc vss")
    open(os.path.join(HERE, "monster6502.ocd"), "w").write("\n".join(L) + "\n")
    print(f"parts={len(comps)} nets={len(nets)} pins={sum(len(v) for v in nets.values())}")


if __name__ == "__main__":
    main()
