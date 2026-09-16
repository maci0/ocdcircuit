"""Custom footprints (.fp files): exotic parts without touching Python.

Format (one fact per line, # comments, mm):
  footprint NAME WxH [edge]
  pad PIN dx dy w h        # SMD pad (repeat)
  hole PIN dx dy drill     # PTH hole (repeat)
  body box w h z [at dx dy ...]   # 3D box, z = base height above board
  body cyl r z                    # 3D cylinder
  keepout dx dy WxH [layers]      # no-copper/no-part rect, footprint frame
  keepout dx dy dN [layers]       # no-copper/no-part circle, footprint frame

`fp PATH` in .ocd loads it (relative to the file). `edge` flag = part may
overhang the board outline (edge-mount plugs). keepouts ride the part:
maze walls + DRC warnings follow it through placement (rot-aware).
"""
from __future__ import annotations
import os
import re
from .types import Footprint


def loads(text: str) -> tuple[str, Footprint]:
    name = ""
    w = h = 0.0
    edge = False
    pads: dict[str, tuple[float, float, float, float]] = {}
    holes: dict[str, tuple[float, float, float]] = {}
    slots: dict[str, tuple[float, float, float, float]] = {}
    bodies: list[Footprint] = []
    keepouts: list[dict[str, object]] = []
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        def err(msg: object) -> ValueError:
            return ValueError(f"line {ln}: {msg}: {line!r}")

        kw = line.split(None, 1)[0].lower()
        if kw == "footprint":
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
        elif kw == "slot":
            toks = line.split()
            if len(toks) != 6:
                raise err("want: slot PIN dx dy w h")
            _, pin, dx, dy, sw, sh = toks
            slots[pin] = (float(dx), float(dy), float(sw), float(sh))
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
        elif kw == "keepout":
            m = re.match(r"^keepout\s+([\d.\-]+)\s+([\d.\-]+)\s+"
                         r"(?:([\d.]+)x([\d.]+)|d([\d.]+))(?:\s+([\w,]+))?$",
                         line, re.I)
            if not m:
                raise err("want: keepout dx dy WxH [layers] | keepout dx dy dN [layers]")
            kd: dict[str, object] = {"dx": float(m.group(1)), "dy": float(m.group(2)),
                                     "layers": m.group(6).split(",") if m.group(6) else []}
            if m.group(5) is not None:
                kd["d"] = float(m.group(5))
            else:
                kd["w"], kd["h"] = float(m.group(3)), float(m.group(4))
            keepouts.append(kd)
        else:
            raise err("unknown statement")
    if not name or w <= 0 or h <= 0:
        raise ValueError("missing/invalid footprint header")
    fp: Footprint = {"w": w, "h": h, "pads": pads, "holes": holes,
                     "bodies": bodies}
    if slots:
        fp["slots"] = slots
    if edge:
        fp["edge"] = True
    if keepouts:
        fp["keepouts"] = keepouts
    return name, fp


def dumps(name: str, fp: Footprint) -> str:
    """Footprint dict → .fp text (inverse of loads: round-trips).
    Lets exporters materialize in-memory customs as sidecar files."""
    from typing import cast

    def _n(v: object) -> float:
        assert isinstance(v, (int, float))
        return float(v)

    w, h = _n(fp["w"]), _n(fp["h"])
    L = [f"footprint {name} {w:g}x{h:g}" + (" edge" if fp.get("edge") else "")]
    for pin, (dx, dy, pw, ph) in sorted(cast(dict[str, tuple[float, float, float, float]], fp.get("pads", {})).items()):
        L.append(f"pad {pin} {_n(dx):g} {_n(dy):g} {_n(pw):g} {_n(ph):g}")
    for pin, (x, y, dr) in sorted(cast(dict[str, tuple[float, float, float]], fp.get("holes", {})).items()):
        L.append(f"hole {pin} {_n(x):g} {_n(y):g} {_n(dr):g}")
    for pin, (x, y, sw, sh) in sorted(cast(dict[str, tuple[float, float, float, float]], fp.get("slots", {})).items()):
        L.append(f"slot {pin} {_n(x):g} {_n(y):g} {_n(sw):g} {_n(sh):g}")
    for b in cast(list[dict[str, object]], fp.get("bodies", [])):
        if "box" in b:
            bw, bh, z = cast(tuple[float, float, float], b["box"])
            bw, bh, z = _n(bw), _n(bh), _n(z)
            ats = " ".join(f"at {_n(x):g} {_n(y):g}"
                           for x, y in cast(list[list[float]], b.get("at", [])))
            L.append(f"body box {bw:g} {bh:g} {z:g}" + (f" {ats}" if ats else ""))
        elif "cyl" in b:
            r, z = cast(tuple[float, float], b["cyl"])
            r, z = _n(r), _n(z)
            L.append(f"body cyl {r:g} {z:g}")
    for k in cast(list[dict[str, object]], fp.get("keepouts", [])):
        layers = f" {','.join(cast(list[str], k.get('layers', [])))}" if k.get("layers") else ""
        if k.get("d") is not None:
            L.append(f"keepout {_n(k['dx']):g} {_n(k['dy']):g} d{_n(k['d']):g}{layers}")
        else:
            L.append(f"keepout {_n(k['dx']):g} {_n(k['dy']):g} {_n(k['w']):g}x{_n(k['h']):g}{layers}")
    return "\n".join(L) + "\n"


def load_file(path: str) -> tuple[str, Footprint]:
    from .util import read_text
    return loads(read_text(os.path.abspath(path)))
