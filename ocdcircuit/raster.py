"""Stdlib PNG rasterizer: 2D board top view → PNG bytes. No deps
(zlib + struct). Painter: bg, traces (layer colors), pads, silk refs.
Text = 3x5 blocks per char (no font files)."""
from __future__ import annotations
import struct
import zlib
from typing import TYPE_CHECKING

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

    def line(self, x1: float, y1: float, x2: float, y2: float,
             hmm: float, wmm: float, c: tuple[int, int, int]) -> None:
        import math
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length * self.s))
        hw = max(1, int(wmm * self.s / 2))
        for i in range(steps + 1):
            x = x1 + (x2 - x1) * i / steps
            y = y1 + (y2 - y1) * i / steps
            cx, cy = self.X(x), self.Y(y, hmm)
            for yy in range(cy - hw, cy + hw + 1):
                for xx in range(cx - hw, cx + hw + 1):
                    if 0 <= xx < self.w and 0 <= yy < self.h:
                        o = (yy * self.w + xx) * 3
                        self.px[o:o + 3] = bytes(c)

    def text(self, s: str, x: float, y: float, hmm: float,
             c: tuple[int, int, int]) -> None:
        cx, cy = self.X(x), self.Y(y, hmm)
        for ci, ch in enumerate(s.upper()[:24]):
            for ry, row in enumerate(FONT.get(ch, FONT[" "])):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        xx, yy = cx + (ci * 4 + rx) - len(s) * 2, cy + ry - 2
                        if 0 <= xx < self.w and 0 <= yy < self.h:
                            o = (yy * self.w + xx) * 3
                            self.px[o:o + 3] = bytes(c)

    def bytes(self) -> bytes:
        return _png(self.w, self.h, self.px)


def render_top(board: Board, pxmm: float = 10.0, theme: str = "dark") -> bytes:
    """Top-down PNG: mask bg, copper traces/pads, white silk refs."""
    from .parts import pads_of, pad_size
    c = Canvas(board.width, board.height, pxmm)
    cols = [(231, 76, 60), (52, 152, 219), (46, 204, 113), (155, 89, 182)]
    lib = board._lib()
    for t in board.traces:
        c.line(t.x1, t.y1, t.x2, t.y2, board.height, max(0.2, t.width),
               cols[t.layer % 4])
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            dx, dy = board.pad_pos(p.ref, pin)
            pw, ph = pad_size(p.fp, pin, lib)
            c.rect(dx - pw / 2, dy - ph / 2, dx + pw / 2, dy + ph / 2,
                   board.height, (217, 168, 50))
    for p in board.parts.values():
        _w, ph = p.wh()
        c.text(p.ref, p.x, p.y + ph / 2 + 0.8, board.height, (245, 245, 245))
    _ = theme
    return c.bytes()


if __name__ == "__main__":
    import sys
    from ocdcircuit import agent
    b = agent.loads(open(sys.argv[1]).read())
    b.place()
    b.route_board()
    open("preview.png", "wb").write(render_top(b))
    print("preview.png written")
