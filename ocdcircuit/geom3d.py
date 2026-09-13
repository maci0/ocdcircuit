"""Shared 3D geometry: materials + mesh builder for STL/glTF/studio.

Materials: soldermask green, copper gold, silk white, chip black,
tantalum yellow, electrolytic silver-blue, LED red-tinted, USB steel.
Bodies carry optional "mat" (else inferred from footprint prefix).
"""
from __future__ import annotations
import math
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .circuit import Board

V3 = tuple[float, float, float]
Tri = tuple[V3, V3, V3, str]  # (a, b, c, material)

MASK = "mask"
COPPER = "copper"
SILK = "silk"
CHIP = "chip"
TANT = "tant"
ELEC = "elec"
LEDC = "led"
STEEL = "steel"
PLASTIC = "plastic"

COLORS: dict[str, tuple[float, float, float, float]] = {
    MASK: (0.05, 0.35, 0.08, 1.0),
    COPPER: (0.85, 0.62, 0.15, 1.0),
    SILK: (0.95, 0.95, 0.95, 1.0),
    CHIP: (0.08, 0.08, 0.09, 1.0),
    TANT: (0.85, 0.65, 0.10, 1.0),
    ELEC: (0.55, 0.60, 0.70, 1.0),
    LEDC: (0.80, 0.12, 0.12, 1.0),
    STEEL: (0.70, 0.72, 0.75, 1.0),
    PLASTIC: (0.12, 0.12, 0.14, 1.0),
}


def body_material(fp: str, body: dict[str, object]) -> str:
    """Per-body override wins, else footprint family heuristic."""
    m = body.get("mat")
    if isinstance(m, str) and m in COLORS:
        return m
    if fp.startswith("LED"):
        return LEDC
    if fp.startswith(("C_TANT", "TANT")):
        return TANT
    if fp.startswith("ELEC"):
        return ELEC
    if fp.startswith(("USB", "BARREL", "MOUNT", "FIDUCIAL", "PINHD", "JST", "TERMINAL", "SDCARD")):
        return STEEL
    return CHIP


def _box(tris: list[Tri], x0: float, y0: float, z0: float,
         x1: float, y1: float, z1: float, mat: str) -> None:
    v: list[V3] = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                   (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    # outward-facing (CCW from outside): bottom -z, top +z, sides out
    for ai, bi, ci, di in [(0, 2, 1, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                           (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
        tris.append((v[ai], v[bi], v[ci], mat))
        tris.append((v[ai], v[ci], v[di], mat))


def _cyl(tris: list[Tri], cx: float, cy: float, z0: float,
         r: float, h: float, mat: str, seg: int = 12) -> None:
    for i in range(seg):
        a0 = 2 * math.pi * i / seg
        a1 = 2 * math.pi * (i + 1) / seg
        p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0))
        p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
        cc = (cx, cy)
        tris.append(((cc[0], cc[1], z0), (p0[0], p0[1], z0), (p1[0], p1[1], z0), mat))
        tris.append(((cc[0], cc[1], z0 + h), (p1[0], p1[1], z0 + h), (p0[0], p0[1], z0 + h), mat))
        tris.append(((p0[0], p0[1], z0), (p0[0], p0[1], z0 + h), (p1[0], p1[1], z0 + h), mat))
        tris.append(((p0[0], p0[1], z0 + h), (p1[0], p1[1], z0), (p1[0], p1[1], z0 + h), mat))


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)


def build(board: Board, thick: float = 1.6) -> list[Tri]:
    """Full board mesh: mask slab + copper pads/traces + silk + bodies."""
    from .parts import bodies_of, pads_of
    tris: list[Tri] = []
    lib = board._lib()
    _box(tris, 0, 0, 0, board.width, board.height, thick, MASK)
    # copper: pads as thin boxes + traces as thin boxes
    for p in board.parts.values():
        for pin in pads_of(p.fp, lib):
            from .parts import pad_size
            dx, dy = board.pad_pos(p.ref, pin)
            pw, ph = pad_size(p.fp, pin, lib)
            _box(tris, dx - pw / 2, dy - ph / 2, thick,
                 dx + pw / 2, dy + ph / 2, thick + 0.05, COPPER)
    for t in board.traces:
        x0, x1 = sorted((t.x1, t.x2))
        y0, y1 = sorted((t.y1, t.y2))
        w = t.width / 2
        z = thick + 0.05 + 0.3 * t.layer
        _box(tris, x0 - w, y0 - w, z, x1 + w, y1 + w, z + 0.05, COPPER)
    # silk refs as tiny white boxes (readable texture hint)
    from .silk import labels
    sk = labels(board)
    for tx in sk.texts:
        _box(tris, tx.x - 0.4, tx.y - 0.1, thick + 0.02,
             tx.x + 0.4, tx.y + 0.1, thick + 0.04, SILK)
    # part bodies with family materials (rotated into board frame)
    for p in board.parts.values():
        for body in bodies_of(p.fp, lib):
            z0 = thick + _f(body.get("z", 0))
            mat = body_material(p.fp, body)
            if "box" in body:
                w2, h2, bh = (_f(v) for v in cast(list[object], body["box"]))
                if p.rot in (90, 270):
                    w2, h2 = h2, w2
                ats = cast(list[list[object]], body.get("at", [[0.0, 0.0]]))
                for at in ats:
                    ax, ay = p.rot_xy(_f(at[0]), _f(at[1])) if len(at) >= 2 else (0.0, 0.0)
                    _box(tris, p.x + ax - w2 / 2, p.y + ay - h2 / 2, z0,
                         p.x + ax + w2 / 2, p.y + ay + h2 / 2, z0 + bh, mat)
            elif "cyl" in body:
                r, bh = (_f(v) for v in cast(list[object], body["cyl"]))
                _cyl(tris, p.x, p.y, z0, r, bh, mat)
    return tris


def _tex(c1: tuple[int, int, int], c2: tuple[int, int, int],
         n: int = 64) -> str:
    """Procedural checker PNG (base64 data URI): subtle two-tone weave so
    PBR surfaces read as textured, not flat plastic. Stdlib (zlib)."""
    import base64
    import io as _io
    import struct
    import zlib
    raw = bytearray()
    for y in range(n):
        raw.append(0)
        for x in range(n):
            c = c1 if (x // 8 + y // 8) % 2 == 0 else c2
            raw += bytes(c)
    ihdr = struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0)
    def chunk(t: bytes, d: bytes) -> bytes:
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


# per-material texture tones (base, weave) — soldermask weave is the
# visible one; metals get near-invisible grain
TEXTEX: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    MASK: ((13, 89, 20), (11, 75, 17)),
    COPPER: ((217, 158, 38), (205, 148, 34)),
    SILK: ((242, 242, 242), (232, 232, 232)),
    CHIP: ((20, 20, 23), (16, 16, 19)),
    TANT: ((217, 166, 26), (205, 156, 22)),
    ELEC: ((140, 153, 179), (130, 143, 169)),
    LEDC: ((204, 31, 31), (192, 27, 27)),
    STEEL: ((179, 184, 191), (169, 174, 181)),
    PLASTIC: ((30, 30, 34), (24, 24, 28)),
}


def to_gltf(board: Board, thick: float = 1.6) -> str:
    """glTF 2.0 asset: one mesh per material (flat shaded), each with
    procedural baseColorTexture + planar box UVs."""
    import base64
    import io
    import json
    import struct
    tris = build(board, thick)
    by_mat: dict[str, list[Tri]] = {}
    for t in tris:
        by_mat.setdefault(t[3], []).append(t)
    materials = sorted(by_mat)
    views: list[dict[str, object]] = []
    accessors: list[dict[str, object]] = []
    meshes: list[dict[str, object]] = []
    def _normal(a: V3, b: V3, c: V3) -> V3:
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        return (nx / ln, ny / ln, nz / ln)

    buf = io.BytesIO()
    off = 0
    for mi, mat in enumerate(materials):
        pos: list[float] = []
        nrm: list[float] = []
        lo = [1e9, 1e9, 1e9]
        hi = [-1e9, -1e9, -1e9]
        uv: list[float] = []
        for a, b, c, _m in by_mat[mat]:
            n = _normal(a, b, c)
            for v in (a, b, c):
                pos += [v[0], v[1], v[2]]
                nrm += [n[0], n[1], n[2]]
                # planar box UV: dominant normal axis picks the projection
                ax = abs(n[0]), abs(n[1]), abs(n[2])
                if ax[0] >= ax[1] and ax[0] >= ax[2]:
                    uv += [v[1] / 4.0, v[2] / 4.0]
                elif ax[1] >= ax[2]:
                    uv += [v[0] / 4.0, v[2] / 4.0]
                else:
                    uv += [v[0] / 4.0, v[1] / 4.0]
                for i in range(3):
                    lo[i] = min(lo[i], v[i])
                    hi[i] = max(hi[i], v[i])
        praw = struct.pack(f"<{len(pos)}f", *pos)
        nraw = struct.pack(f"<{len(nrm)}f", *nrm)
        uraw = struct.pack(f"<{len(uv)}f", *uv)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(praw)})
        off += len(praw)
        buf.write(praw)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(nraw)})
        off += len(nraw)
        buf.write(nraw)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(uraw)})
        off += len(uraw)
        buf.write(uraw)
        accessors.append({"bufferView": 3 * mi, "componentType": 5126,
                          "count": len(pos) // 3, "type": "VEC3",
                          "max": hi, "min": lo})
        accessors.append({"bufferView": 3 * mi + 1, "componentType": 5126,
                          "count": len(nrm) // 3, "type": "VEC3",
                          "max": [1.0, 1.0, 1.0], "min": [-1.0, -1.0, -1.0]})
        accessors.append({"bufferView": 3 * mi + 2, "componentType": 5126,
                          "count": len(uv) // 2, "type": "VEC2",
                          "max": [10.0, 10.0], "min": [0.0, 0.0]})
        meshes.append({"primitives": [{"attributes": {"POSITION": 3 * mi,
                                                      "NORMAL": 3 * mi + 1,
                                                      "TEXCOORD_0": 3 * mi + 2},
                                       "material": mi}]})
    images = []
    textures = []
    samplers = [{"magFilter": 9729, "minFilter": 9986, "wrapS": 10497, "wrapT": 10497}]
    for mi, mat in enumerate(materials):
        c1, c2 = TEXTEX.get(mat, ((128, 128, 128), (118, 118, 118)))
        raw = base64.b64decode(_tex(c1, c2).split(",", 1)[1])
        images.append({"name": f"{mat}_tex", "mimeType": "image/png",
                       "bufferView": len(views)})
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(raw)})
        off += len(raw)
        buf.write(raw)
        textures.append({"source": mi, "sampler": 0})
    doc = {
        "asset": {"version": "2.0", "generator": "ocdcircuit"},
        "materials": [{"name": m, "pbrMetallicRoughness": {
            "baseColorFactor": list(COLORS[m]),
            "baseColorTexture": {"index": mi},
            "metallicFactor": 0.9 if m == COPPER else 0.1,
            "roughnessFactor": 0.35 if m == COPPER else 0.8}} for mi, m in enumerate(materials)],
        "buffers": [{"byteLength": off, "uri": "data:application/octet-stream;base64," +
                     base64.b64encode(buf.getvalue()).decode()}],
        "bufferViews": views,
        "accessors": accessors,
        "images": images,
        "textures": textures,
        "samplers": samplers,
        "meshes": meshes,
        "nodes": [{"mesh": i, "name": materials[i]} for i in range(len(meshes))],
        "scenes": [{"nodes": list(range(len(meshes)))}],
        "scene": 0,
    }
    return json.dumps(doc)
