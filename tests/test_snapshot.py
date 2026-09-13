"""Snapshot golden test (tscircuit-style): seeded place+route must reproduce
bit-identical geometry. Regenerate: SNAP=1 python tests/test_snapshot.py"""
from __future__ import annotations
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit import agent

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")
GOLD = os.path.join(HERE, "golden.json")


def fingerprint() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for f, pl, rt, ex, nl in [("blinky_555.ocd", "diffusion", "lroute", EX, None),
                      ("blinky_555.ocd", "compact", "maze", EX, None),
                      ("psu.ocd", "diffusion", "lroute", EX, None),
                      ("pico_tmc2209.ocd", "compact", "maze",
                       os.path.join(EX, "pico_tmc2209"), None),
                      ("blinky_555.ocd", "diffusion", "maze", EX, 1),
                      ("blinky_555.ocd", "compact", "maze", EX, 4),
                      ("blinky_555.ocd", "compact", "maze", EX, 8)]:
        text = open(os.path.join(ex, f)).read()
        if nl is not None:
            import re
            text = re.sub(r"^board (\S+) [\d.]+x[\d.]+ \d+L$",
                          rf"board \1 40x30 {nl}L", text, flags=re.M)
        b = agent.loads(text, base=ex)
        b.place(pl, seeds=3, iters=200)
        b.route_board(rt)
        r = b.check()
        assert r["errors"] == [], (f, nl, r["errors"])
        assert all(0 <= s.layer < (nl or 2) for s in b.traces), (f, nl)
        snap = {
            "parts": {r: [round(p.x, 3), round(p.y, 3)] for r, p in b.parts.items()},
            "segs": [[s.net, round(s.x1, 3), round(s.y1, 3), round(s.x2, 3),
                      round(s.y2, 3), s.layer] for s in b.traces],
        }
        h = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()[:16]
        out[f"{f}/{pl}/{rt}/{nl or 2}L"] = {"hash": h, "parts": len(snap["parts"]),
                                 "segs": len(snap["segs"])}
    return out


def main() -> None:
    fp = fingerprint()
    if os.environ.get("SNAP"):
        json.dump(fp, open(GOLD, "w"), indent=1, sort_keys=True)
        print("golden rewritten:", fp)
        return
    gold = json.load(open(GOLD))
    assert fp == gold, f"solver drift!\n got: {fp}\n want: {gold}"
    print("SNAPSHOT OK:", {k: v["hash"] for k, v in fp.items()})


if __name__ == "__main__":
    main()
