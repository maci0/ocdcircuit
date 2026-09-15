"""X-ray view + fab-scan compare. Stdlib only.

render(): every copper layer stacked on black (traces/pads/vias/outlines),
no mask, no bodies — what an x-ray sees. compare(): decode an uploaded fab
PNG back to a copper mask, resample it onto the design grid, and report
missing/extra copper regions plus an overlay SVG with the divergences
boxed. dx/dy/scale re-register a scan that doesn't sit exactly on the
design grid (real scans never do); thr sets the copper brightness cutoff.
"""
from __future__ import annotations
import struct
import zlib
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board

LAYERS = ["#ff5a4d", "#4da3ff", "#3ddc84", "#c07bff"]


def _blank(board: Board, pxmm: float) -> tuple[int, int, bytearray]:
    gw, gh = max(1, int(board.width * pxmm)), max(1, int(board.height * pxmm))
    return gw, gh, bytearray(gw * gh)


def _disc(g: bytearray, gw: int, gh: int, cx: float, cy: float,
          hmm: float, pxmm: float, rmm: float, v: int) -> None:
    x, y = int(cx * pxmm), int((hmm - cy) * pxmm)
    r = max(1, int(rmm * pxmm))
    for yy in range(y - r, y + r + 1):
        for xx in range(x - r, x + r + 1):
            if (xx - x) ** 2 + (yy - y) ** 2 <= r * r and 0 <= xx < gw and 0 <= yy < gh:
                g[yy * gw + xx] = v


def _line(g: bytearray, gw: int, gh: int, x1: float, y1: float,
          x2: float, y2: float, hmm: float, pxmm: float, wmm: float, v: int) -> None:
    import math as _math
    steps = max(1, int(_math.hypot(x2 - x1, y2 - y1) * pxmm))
    for i in range(steps + 1):
        _disc(g, gw, gh, x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps,
              hmm, pxmm, wmm / 2, v)


def _rect(g: bytearray, gw: int, gh: int, x0: float, y0: float,
          x1: float, y1: float, hmm: float, pxmm: float, v: int) -> None:
    for yy in range(max(0, int((hmm - y1) * pxmm)), min(gh, int((hmm - y0) * pxmm) + 1)):
        for xx in range(max(0, int(x0 * pxmm)), min(gw, int(x1 * pxmm) + 1)):
            g[yy * gw + xx] = v


def expected(board: Board, pxmm: float = 10.0) -> tuple[int, int, bytearray]:
    """Design copper mask at pxmm px/mm: pours, traces, pads, vias (drills cut)."""
    from .drc import pour_layers
    from .export import plane_plots
    from .fab import get as _fab_get
    from .parts import hole_drill, pad_size, pads_of
    gw, gh, g = _blank(board, pxmm)
    if pour_layers(board):
        edge = float(cast(float, _fab_get(board.fab).get("edge", 0.3)))
        _rect(g, gw, gh, edge, edge, board.width - edge, board.height - edge,
              board.height, pxmm, 1)
        for plots in plane_plots(board).values():
            for x0, y0, x1, y1 in plots:
                _rect(g, gw, gh, x0, y0, x1, y1, board.height, pxmm, 0)
    for t in board.traces:
        if t.via:
            _disc(g, gw, gh, t.x1, t.y1, board.height, pxmm, 0.4, 1)
            if t.drill > 0:
                _disc(g, gw, gh, t.x1, t.y1, board.height, pxmm, t.drill / 2, 0)
        else:
            _line(g, gw, gh, t.x1, t.y1, t.x2, t.y2, board.height, pxmm,
                  max(0.2, t.width), 1)
    lib = board._lib()
    for p in board.parts.values():
        for pin, (dx, dy) in pads_of(p.fp, lib).items():
            rx, ry = p.rot_xy(dx, dy)
            cx, cy = p.x + rx, p.y + ry
            pw, ph = pad_size(p.fp, pin, lib)
            if p.rot in (90, 270):
                pw, ph = ph, pw
            _rect(g, gw, gh, cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2,
                  board.height, pxmm, 1)
            if hole_drill(p.fp, pin, lib) > 0:
                _disc(g, gw, gh, cx, cy, board.height, pxmm,
                      hole_drill(p.fp, pin, lib) / 2, 0)
    return gw, gh, g


def decode_png(raw: bytes) -> tuple[int, int, bytearray]:
    """PNG → (w, h, RGB triplets). 8-bit gray/RGB(A), non-interlaced only;
    anything else is a ValueError (fixable input: re-export the scan)."""
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG (upload the fab x-ray as .png)")
    pos, w, h, ctype, idat = 8, 0, 0, 0, b""
    while pos < len(raw):
        if pos + 12 > len(raw):
            raise ValueError("truncated PNG (re-export the scan)")
        (ln,) = struct.unpack(">I", raw[pos:pos + 4])
        typ = raw[pos + 4:pos + 8]
        if pos + 12 + ln > len(raw):
            raise ValueError("truncated PNG (re-export the scan)")
        if typ == b"IHDR":
            if ln != 13:
                raise ValueError("bad PNG header (re-export the scan)")
            w, h, bd, ctype, _cp, _fl, iv = struct.unpack(">IIBBBBB", raw[pos + 8:pos + 21])
            if bd != 8 or iv != 0 or ctype not in (0, 2, 6):
                raise ValueError(
                    f"unsupported PNG (need 8-bit gray/RGB(A) non-interlaced, "
                    f"got depth={bd} type={ctype} interlace={iv})")
        elif typ == b"IDAT":
            idat += raw[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    if w == 0 or h == 0 or not idat:
        raise ValueError("empty PNG (no pixels — re-export the scan)")
    ch = {0: 1, 2: 3, 6: 4}[ctype]
    try:
        data = zlib.decompress(idat)
    except zlib.error as e:
        raise ValueError(f"bad PNG data ({e} — re-export the scan)")
    if len(data) < h * (1 + w * ch):
        raise ValueError("truncated PNG (re-export the scan)")
    px = bytearray(w * h * 3)
    prev = bytearray(w * ch)
    p = 0
    for y in range(h):
        if p + 1 + w * ch > len(data):
            raise ValueError("truncated PNG (re-export the scan)")
        f = data[p]
        p += 1
        line = bytearray(data[p:p + w * ch])
        p += w * ch
        if f == 1:
            for i in range(ch, w * ch):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(w * ch):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(w * ch):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(w * ch):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif f != 0:
            raise ValueError(f"bad PNG filter {f} (re-export the scan)")
        prev = line
        for x in range(w):
            o = x * ch
            if ch == 1:
                r = gg = bb = line[o]
            else:
                r, gg, bb = line[o], line[o + 1], line[o + 2]
            qq = (y * w + x) * 3
            px[qq], px[qq + 1], px[qq + 2] = r, gg, bb
    return w, h, px


def render(board: Board, scale: float = 10.0) -> str:
    """X-ray reference SVG: stacked copper on black, ghost outlines, dim refs."""
    from xml.sax.saxutils import escape
    from .parts import hole_drill, pad_size, pads_of
    s = scale
    wpx, hpx = board.width * s, board.height * s
    el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{wpx}" height="{hpx}" '
          f'viewBox="0 0 {wpx} {hpx}" font-family="monospace">',
          f'<title>{escape(board.name)} x-ray — {len(board.parts)} parts, '
          f'{len(board.traces)} segs, {board.layers}L</title>',
          f'<rect x="0" y="0" width="{wpx}" height="{hpx}" fill="#05070d" '
          f'stroke="#1c2940" stroke-width="2" rx="6"/>']
    for t in board.traces:
        if t.via:
            continue
        el.append(f'<line x1="{t.x1 * s}" y1="{hpx - t.y1 * s}" x2="{t.x2 * s}" '
                  f'y2="{hpx - t.y2 * s}" stroke="{LAYERS[t.layer % len(LAYERS)]}" '
                  f'stroke-width="{max(1.5, t.width * s)}" stroke-linecap="round" '
                  f'opacity="0.9"/>')
    for t in board.traces:
        if not t.via:
            continue
        el.append(f'<circle cx="{t.x1 * s:.1f}" cy="{hpx - t.y1 * s:.1f}" '
                  f'r="{0.4 * s:.1f}" fill="none" stroke="#ffd75e" stroke-width="1.5"/>')
    lib = board._lib()
    for p in board.parts.values():
        for pin, (dx, dy) in pads_of(p.fp, lib).items():
            rx, ry = p.rot_xy(dx, dy)
            cx, cy = (p.x + rx) * s, hpx - (p.y + ry) * s
            pw, ph = pad_size(p.fp, pin, lib)
            if p.rot in (90, 270):
                pw, ph = ph, pw
            el.append(f'<rect x="{cx - pw * s / 2:.1f}" y="{cy - ph * s / 2:.1f}" '
                      f'width="{pw * s:.1f}" height="{ph * s:.1f}" rx="1" '
                      f'fill="#ffd75e" stroke="#8a6d00" stroke-width="1"/>')
            if hole_drill(p.fp, pin, lib) > 0:
                el.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" '
                          f'r="{hole_drill(p.fp, pin, lib) * s / 2:.1f}" fill="#05070d"/>')
    for p in board.parts.values():
        pw, ph = p.wh()
        el.append(f'<rect x="{(p.x - pw / 2) * s:.1f}" y="{(hpx - (p.y + ph / 2) * s):.1f}" '
                  f'width="{pw * s:.1f}" height="{ph * s:.1f}" fill="none" '
                  f'stroke="#5a6b85" stroke-width="1" stroke-dasharray="4 2"/>'
                  f'<text x="{p.x * s:.1f}" y="{(hpx - p.y * s):.1f}" fill="#8fa1bd" '
                  f'font-size="{max(7.0, s * 0.9):.1f}" text-anchor="middle">'
                  f'{escape(p.ref)}</text>')
    el.append("</svg>")
    return "\n".join(el)


def overlay(board: Board, divs: list[dict[str, object]], scale: float = 10.0) -> str:
    """Reference view with each divergence boxed (red = missing, amber = extra)."""
    s = scale
    boxes: list[str] = []
    for d in divs:
        kind = d["kind"]
        assert kind in ("missing", "extra")
        x, y, w, h = d["x"], d["y"], d["w"], d["h"]
        assert isinstance(x, (int, float)) and isinstance(y, (int, float))
        assert isinstance(w, (int, float)) and isinstance(h, (int, float))
        col = "#ff4d4d" if kind == "missing" else "#ffb020"
        boxes.append(
            f'<rect x="{float(x) * s:.1f}" y="{(board.height - (float(y) + float(h))) * s:.1f}" '
            f'width="{float(w) * s:.1f}" height="{float(h) * s:.1f}" fill="{col}" '
            f'fill-opacity="0.25" stroke="{col}" stroke-width="2"/>')
    return render(board, scale).replace("</svg>", "\n".join(boxes) + "\n</svg>")


def compare(board: Board, raw: bytes, pxmm: float = 10.0, thr: int = 100,
            dx: float = 0.0, dy: float = 0.0, scale: float = 1.0,
            min_cells: int = 3, max_divs: int = 50) -> dict[str, object]:
    """Fab scan vs design: {score 0-100, divs [{kind,x,y,w,h,cells}], svg, overlay}.
    divs are flood-filled copper regions (mm bbox, y-up), biggest first."""
    if not isinstance(thr, int) or isinstance(thr, bool) or not 0 <= thr <= 255:
        raise ValueError(f"thr {thr!r} must be an int 0-255")
    if not isinstance(scale, (int, float)) or not scale > 0:
        raise ValueError(f"scale {scale!r} must be positive")
    if not isinstance(min_cells, int) or isinstance(min_cells, bool) or min_cells < 1:
        raise ValueError(f"min_cells {min_cells!r} must be an int ≥1")
    if not isinstance(max_divs, int) or isinstance(max_divs, bool) or max_divs < 1:
        raise ValueError(f"max_divs {max_divs!r} must be an int ≥1")
    if not isinstance(pxmm, (int, float)) or not pxmm > 0:
        raise ValueError(f"pxmm {pxmm!r} must be positive")
    if not isinstance(dx, (int, float)) or not isinstance(dy, (int, float)):
        raise ValueError(f"dx/dy {dx!r}/{dy!r} must be numbers")
    gw, gh, exp = expected(board, pxmm)
    ow, oh, rgb = decode_png(raw)
    w, h = board.width, board.height
    diff = bytearray(gw * gh)  # 1 = missing copper, 2 = extra copper
    miss = extra = 0
    for gy in range(gh):
        my = (gh - 0.5 - gy) / pxmm
        my2 = (my - h / 2) * scale + h / 2 + dy
        sy = min(oh - 1, max(0, int((h - my2) / h * oh)))
        for gx in range(gw):
            mx = (gx + 0.5) / pxmm
            mx2 = (mx - w / 2) * scale + w / 2 + dx
            sx = min(ow - 1, max(0, int(mx2 / w * ow)))
            q = (sy * ow + sx) * 3
            o = 1 if max(rgb[q], rgb[q + 1], rgb[q + 2]) > thr else 0
            e = exp[gy * gw + gx]
            if e and not o:
                diff[gy * gw + gx] = 1
                miss += 1
            elif o and not e:
                diff[gy * gw + gx] = 2
                extra += 1
    seen = bytearray(gw * gh)
    divs: list[dict[str, object]] = []
    for i in range(gw * gh):
        if not diff[i] or seen[i]:
            continue
        stack = [i]
        seen[i] = 1
        cells = m1 = m2 = 0
        x0, y0, x1, y1 = gw, gh, -1, -1
        while stack:
            j = stack.pop()
            cells += 1
            if diff[j] == 1:
                m1 += 1
            else:
                m2 += 1
            jx, jy = j % gw, j // gw
            x0, y0, x1, y1 = min(x0, jx), min(y0, jy), max(x1, jx), max(y1, jy)
            # 4-neighbours with row-edge guards (j-1/j+1 stay on the row)
            for n in ([j - 1] if jx > 0 else []):
                if diff[n] and not seen[n]:
                    seen[n] = 1
                    stack.append(n)
            for n in ([j + 1] if jx < gw - 1 else []):
                if diff[n] and not seen[n]:
                    seen[n] = 1
                    stack.append(n)
            for n in (j - gw, j + gw):
                if 0 <= n < gw * gh and diff[n] and not seen[n]:
                    seen[n] = 1
                    stack.append(n)
        if cells < min_cells:
            continue
        divs.append({"kind": "missing" if m1 >= m2 else "extra",
                     "x": round(x0 / pxmm, 2), "y": round((h - (y1 + 1) / pxmm), 2),
                     "w": round((x1 - x0 + 1) / pxmm, 2),
                     "h": round((y1 - y0 + 1) / pxmm, 2), "cells": cells})
    divs.sort(key=lambda d: int(cast(int, d["cells"])), reverse=True)
    divs = divs[:max_divs]  # ponytail: capped list, full count in missing/extra
    expn = sum(exp) or 1
    return {"score": max(0.0, round(100.0 * (1.0 - (miss + extra) / expn), 1)),
            "divs": divs, "missing": miss, "extra": extra, "expected": expn,
            "svg": render(board), "overlay": overlay(board, divs)}
