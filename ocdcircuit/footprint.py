"""Custom footprints (.fp files): exotic parts without touching Python.

Format (one fact per line, # comments, mm):
  footprint NAME WxH [edge]
  pad PIN dx dy w h        # SMD pad (repeat)
  hole PIN dx dy drill     # PTH hole (repeat)
  body box w h z [at dx dy ...]   # 3D box, z = base height above board
  body cyl r z                    # 3D cylinder

`fp PATH` in .ocd loads it (relative to the file). `edge` flag = part may
overhang the board outline (edge-mount plugs).
"""
from __future__ import annotations
import os
from .types import Footprint


def loads(text: str) -> tuple[str, Footprint]:
    name = ""
    w = h = 0.0
    edge = False
    pads: dict[str, tuple[float, float, float, float]] = {}
    holes: dict[str, tuple[float, float, float]] = {}
    bodies: list[Footprint] = []
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        def err(msg: object) -> ValueError:
            return ValueError(f"line {ln}: {msg}: {line!r}")

        kw = line.split(None, 1)[0].lower()
        if kw == "footprint":
            import re
            m = re.match(r"^footprint\s+(\S+)\s+([\d.]+)x([\d.]+)(\s+edge)?$", line, re.I)
            if not m:
                raise err("want: footprint NAME WxH [edge]")
            name, w, h = m.group(1), float(m.group(2)), float(m.group(3))
            edge = bool(m.group(4))
        elif kw == "pad":
            toks = line.split()
            if len(toks) != 6:
                raise err("want: pad PIN dx dy w h")
            _, pin, dx, dy, pw, ph = toks
            pads[pin] = (float(dx), float(dy), float(pw), float(ph))
        elif kw == "hole":
            toks = line.split()
            if len(toks) != 5:
                raise err("want: hole PIN dx dy drill")
            _, pin, dx, dy, dr = toks
            holes[pin] = (float(dx), float(dy), float(dr))
        elif kw == "body":
            toks = line.split()
            if len(toks) >= 5 and toks[1].lower() == "box":
                _, _, bw, bh, z, *rest = toks
                at: list[list[float]] = []
                for i in range(0, len(rest), 3):
                    if rest[i].lower() == "at" and i + 2 < len(rest):
                        at.append([float(rest[i + 1]), float(rest[i + 2])])
                bodies.append({"box": (float(bw), float(bh), float(z)),
                               "at": at or [(0.0, 0.0)]})
            elif len(toks) == 4 and toks[1].lower() == "cyl":
                _, _, r, z = toks
                bodies.append({"cyl": (float(r), float(z))})
            else:
                raise err("want: body box w h z [at dx dy ...] | body cyl r z")
        else:
            raise err("unknown statement")
    if not name or w <= 0 or h <= 0:
        raise ValueError("missing/invalid footprint header")
    fp: Footprint = {"w": w, "h": h, "pads": pads, "holes": holes,
                     "bodies": bodies}
    if edge:
        fp["edge"] = True
    return name, fp


def load_file(path: str) -> tuple[str, Footprint]:
    with open(os.path.abspath(path)) as f:
        return loads(f.read())
