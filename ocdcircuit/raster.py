"""Stdlib PNG rasterizer + codec: 2D board top view → PNG bytes, and PNG →
pixels. No deps (zlib + struct). Painter: bg, traces (layer colors), pads,
silk refs. Text = 3x5 blocks per char (no font files)."""
from __future__ import annotations
import struct
import zlib
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board

FONT: dict[str, list[str]] = {
    "A": ["010", "101", "111", "101", "101"], "B": ["110", "101", "110", "101", "110"],
    "C": ["011", "100", "100", "100", "011"], "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "110", "100", "111"], "F": ["111", "100", "110", "100", "100"],
    "G": ["011", "100", "101", "101", "011"], "H": ["101", "101", "111", "101", "101"],
    "I": ["111", "010", "010", "010", "111"], "J": ["001", "001", "001", "101", "010"],
    "K": ["101", "101", "110", "101", "101"], "L": ["100", "100", "100", "100", "111"],
    "M": ["101", "111", "111", "101", "101"], "N": ["110", "101", "101", "101", "101"],
    "O": ["010", "101", "101", "101", "010"], "P": ["110", "101", "110", "100", "100"],
    "Q": ["010", "101", "101", "110", "011"], "R": ["110", "101", "110", "101", "101"],
    "S": ["011", "100", "010", "001", "110"], "T": ["111", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "111"], "V": ["101", "101", "101", "101", "010"],
    "W": ["101", "101", "111", "111", "101"], "X": ["101", "101", "010", "101", "101"],
    "Y": ["101", "101", "010", "010", "010"], "Z": ["111", "001", "010", "100", "111"],
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["110", "001", "010", "100", "111"], "3": ["110", "001", "010", "001", "110"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "110", "001", "110"],
    "6": ["011", "100", "110", "101", "010"], "7": ["111", "001", "010", "010", "010"],
    "8": ["010", "101", "010", "101", "010"], "9": ["010", "101", "011", "001", "110"],
    "_": ["000", "000", "000", "000", "111"], "-": ["000", "000", "111", "000", "000"],
    ".": ["000", "000", "000", "000", "010"], " ": ["000", "000", "000", "000", "000"],
}


def _png(w: int, h: int, px: bytearray) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += px[y * w * 3:(y + 1) * w * 3]
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


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


class Canvas:
    def __init__(self, wmm: float, hmm: float, pxmm: float = 10.0) -> None:
        self.s = pxmm
        self.w = max(1, int(wmm * pxmm))
        self.h = max(1, int(hmm * pxmm))
        self.px = bytearray([11, 61, 11] * self.w * self.h)  # soldermask green

    def X(self, x: float) -> int:
        return int(x * self.s)

    def Y(self, y: float, hmm: float) -> int:
        return int((hmm - y) * self.s)

    def rect(self, x0: float, y0: float, x1: float, y1: float,
             hmm: float, c: tuple[int, int, int]) -> None:
        for yy in range(max(0, self.Y(y1, hmm)), min(self.h, self.Y(y0, hmm) + 1)):
            for xx in range(max(0, self.X(x0)), min(self.w, self.X(x1) + 1)):
                o = (yy * self.w + xx) * 3
                self.px[o:o + 3] = bytes(c)

    def disc(self, x: float, y: float, hmm: float, rmm: float,
             c: tuple[int, int, int]) -> None:
        cx, cy, r = self.X(x), self.Y(y, hmm), max(1, int(rmm * self.s))
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r:
                    if 0 <= xx < self.w and 0 <= yy < self.h:
                        o = (yy * self.w + xx) * 3
                        self.px[o:o + 3] = bytes(c)

    def line(self, x1: float, y1: float, x2: float, y2: float,
             hmm: float, wmm: float, c: tuple[int, int, int]) -> None:
        import math
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length * self.s))
        hw = max(1.0, wmm * self.s / 2)
        for i in range(steps + 1):
            x = x1 + (x2 - x1) * i / steps
            y = y1 + (y2 - y1) * i / steps
            self.disc(x, y, hmm, hw / self.s, c)

    def text(self, s: str, x: float, y: float, hmm: float,
             c: tuple[int, int, int], big: int = 2) -> None:
        cx, cy = self.X(x), self.Y(y, hmm)
        for ci, ch in enumerate(s.upper()[:24]):
            for ry, row in enumerate(FONT.get(ch, FONT[" "])):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        for dy in range(big):
                            for dx in range(big):
                                xx = cx + (ci * 4 + rx) * big + dx - len(s) * 2 * big
                                yy = cy + ry * big + dy - 2 * big
                                if 0 <= xx < self.w and 0 <= yy < self.h:
                                    o = (yy * self.w + xx) * 3
                                    self.px[o:o + 3] = bytes(c)

    def bytes(self) -> bytes:
        return _png(self.w, self.h, self.px)


def render_top(board: Board, pxmm: float = 10.0) -> bytes:
    """Top-down PNG: mask bg, copper traces/pads, bodies, white silk refs."""
    from .parts import bodies_of, hole_drill, pads_of, pad_size
    c = Canvas(board.width, board.height, pxmm)
    cols = [(231, 76, 60), (52, 152, 219), (46, 204, 113), (155, 89, 182)]
    lib = board._lib()
    from .drc import pour_layers
    from .export import plane_plots
    poured = pour_layers(board)
    if 0 in {ll for lls in poured.values() for ll in lls}:
        # top pour: copper flood (fab edge inset, like Gerber), cutouts out
        from .fab import get as _fab_get
        _edge = float(cast(float, _fab_get(board.fab).get("edge", 0.3)))
        c.rect(_edge, _edge, board.width - _edge, board.height - _edge,
               board.height, (185, 120, 40))
        for x0, y0, x1, y1 in plane_plots(board).get(0, []):
            c.rect(x0, y0, x1, y1, board.height, (11, 61, 11))
    for t in board.traces:
        if t.via:
            continue
        c.line(t.x1, t.y1, t.x2, t.y2, board.height, max(0.2, t.width),
               cols[t.layer % 4])
    for t in board.traces:
        if t.via:
            c.disc(t.x1, t.y1, board.height, 0.4, (217, 168, 50))
            c.disc(t.x1, t.y1, board.height, 0.2, (11, 61, 11))
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            dx, dy = board.pad_pos(p.ref, pin)
            pw, ph = pad_size(p.fp, pin, lib)
            c.rect(dx - pw / 2, dy - ph / 2, dx + pw / 2, dy + ph / 2,
                   board.height, (217, 168, 50))
            dr = hole_drill(p.fp, pin, lib)
            if dr > 0:
                c.disc(dx, dy, board.height, dr / 2, (11, 61, 11))
    for p in board.parts.values():
        for body in bodies_of(p.fp, lib):
            box = body.get("box")
            if not isinstance(box, (list, tuple)) or len(box) < 2:
                continue
            w2, h2 = float(box[0]), float(box[1])
            c.rect(p.x - w2 / 2, p.y - h2 / 2, p.x + w2 / 2, p.y + h2 / 2,
                   board.height, (24, 24, 28))
    for p in board.parts.values():
        _w, ph = p.wh()
        c.text(p.ref, p.x, p.y + ph / 2 + 0.8, board.height, (245, 245, 245))
    return c.bytes()


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        print("usage: python -m ocdcircuit.raster <board.ocd>  # writes preview.png")
        raise SystemExit(0)
    if len(sys.argv) != 2:
        print("usage: python -m ocdcircuit.raster <board.ocd>  # writes preview.png",
              file=sys.stderr)
        raise SystemExit(1)
    from . import agent
    from .util import read_text
    b = agent.loads(read_text(sys.argv[1]))
    b.place()
    b.route_board()
    open("preview.png", "wb").write(render_top(b))
    print("preview.png written")
