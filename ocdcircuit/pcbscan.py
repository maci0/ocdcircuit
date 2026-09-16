"""Photo scan: N phone photos of a PCB (both sides, any angle/zoom) →
registered stitch + enhancement stack + parallax height field + a 3D
gaussian splat, packaged for a vision LLM to reverse-engineer.

Pipeline (one pass per side):
  load    JPEG via Pillow, PNG via the stdlib decoder already in xray.py
  register  each photo against the sharpest one: brute-force scale x rotation
            grid, translation by FFT phase correlation (subpixel peak)
  stitch  warp all into the reference frame, per-pixel nanmedian — the median
          is what kills specular glare and fingers, no masking needed
  enhance tiled-CLAHE contrast, Sobel edges, silk isolation, copper isolation
  height  residual parallax: after plane alignment, what still moves between
          views moves because it stands off the board. Magnitude = standoff.
  splat   height field + stitched colour → 3DGS .ply (viewers read it directly)

Everything is deterministic and file-based; `analyse()` is the only step that
talks to a model, and it only reads what the steps above wrote.

numpy is required here (megapixel arrays; the scalar path would be minutes per
photo). Pillow is optional — without it, PNG only.

cordis-boundary: reads photo paths and writes artifacts under outdir —
outside-context emission (§6.1), withheld until scan() is called.
"""
from __future__ import annotations

import json
import math
import os
import struct
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .circuit import Board

MAXDIM = 1600          # working resolution of the stitched canvas
REG = 512              # registration works on this, ~40x fewer pixels
SCALES = tuple(2.0 ** (i / 8.0) for i in range(-12, 13))  # 0.35x .. 2.83x
ROTS = tuple(range(0, 360, 10))
COARSE = 96            # pyramid base: basins this wide swallow a grid step
TOPK = 5               # grid candidates carried into the refine
TILE = 64              # parallax tile size, canvas pixels
SIDES = ("top", "bottom")


def _numpy() -> Any:
    from .util import numpy as _handle
    np = _handle()
    if np is None:
        raise RuntimeError(
            "pcb photo scan needs numpy (pip install numpy); everything else "
            "in ocdcircuit stays stdlib-only")
    return np


# ---------------------------------------------------------------- loading


def load(path: str) -> Any:
    """Photo → (h, w, 3) uint8 RGB. Pillow if present (JPEG/HEIC/TIFF),
    otherwise the stdlib PNG decoder."""
    np = _numpy()
    pil: Any = None
    try:
        from PIL import Image
        pil = Image
    except ImportError:
        pass
    if pil is not None:
        with pil.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    if not path.lower().endswith(".png"):
        raise RuntimeError(
            f"{os.path.basename(path)} is not a PNG and Pillow is not "
            "installed (pip install Pillow for JPEG support)")
    from .xray import decode_png
    with open(path, "rb") as f:
        w, h, px = decode_png(f.read())
    return np.frombuffer(bytes(px), dtype=np.uint8).reshape(h, w, 3).copy()


def gray(img: Any) -> Any:
    """Luma, float32 0..255."""
    np = _numpy()
    if img.ndim == 2:
        return img.astype(np.float32)
    return (img[..., 0] * 0.299 + img[..., 1] * 0.587
            + img[..., 2] * 0.114).astype(np.float32)


def sharpness(g: Any) -> float:
    """Variance of the Laplacian — the standard focus measure. Picks which
    photo of a side earns the reference frame."""
    np = _numpy()
    lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
           - 4.0 * g[1:-1, 1:-1])
    return float(np.var(lap))


# ------------------------------------------------------- sampling / warping


def _sample(img: Any, xs: Any, ys: Any) -> Any:
    """Bilinear sample at float coords. Outside the source → NaN, so the
    stitch median never averages in an edge that was never photographed."""
    np = _numpy()
    h, w = img.shape[:2]
    src = img.astype(np.float32)
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    fx = (xs - x0).astype(np.float32)
    fy = (ys - y0).astype(np.float32)
    ok = (x0 >= 0) & (y0 >= 0) & (x0 < w - 1) & (y0 < h - 1)
    xc, yc = np.clip(x0, 0, w - 2), np.clip(y0, 0, h - 2)
    if src.ndim == 3:
        fx, fy, ok3 = fx[..., None], fy[..., None], ok[..., None]
    else:
        ok3 = ok
    top = src[yc, xc] * (1 - fx) + src[yc, xc + 1] * fx
    bot = src[yc + 1, xc] * (1 - fx) + src[yc + 1, xc + 1] * fx
    return np.where(ok3, top * (1 - fy) + bot * fy, np.nan)


def resize(img: Any, w2: int, h2: int) -> Any:
    """Bilinear resample to an exact size (no NaN: coords stay inside)."""
    np = _numpy()
    h, w = img.shape[:2]
    xs = np.linspace(0, w - 1.001, w2, dtype=np.float32)[None, :]
    ys = np.linspace(0, h - 1.001, h2, dtype=np.float32)[:, None]
    return np.nan_to_num(_sample(img, np.broadcast_to(xs, (h2, w2)),
                                 np.broadcast_to(ys, (h2, w2))))


def fit(img: Any, maxdim: int = MAXDIM) -> Any:
    """Downscale so the long edge is maxdim. Never upscales."""
    h, w = img.shape[:2]
    s = min(1.0, maxdim / float(max(h, w)))
    if s >= 1.0:
        return img.astype(_numpy().float32)
    return resize(img, max(1, int(w * s)), max(1, int(h * s)))


def warp(img: Any, scale: float, rot: float, dx: float, dy: float,
         out_w: int, out_h: int) -> Any:
    """Place img into an (out_h, out_w) frame under similarity transform
    (scale about the image centre, then rotate, then translate). Uncovered
    output pixels are NaN."""
    np = _numpy()
    h, w = img.shape[:2]
    yy, xx = np.meshgrid(np.arange(out_h, dtype=np.float32),
                         np.arange(out_w, dtype=np.float32), indexing="ij")
    # inverse map: canvas → source
    cx, cy = xx - out_w / 2.0 - dx, yy - out_h / 2.0 - dy
    a = math.radians(-rot)
    ca, sa = math.cos(a), math.sin(a)
    rx, ry = cx * ca - cy * sa, cx * sa + cy * ca
    return _sample(img, rx / scale + w / 2.0, ry / scale + h / 2.0)


# ----------------------------------------------------------- registration


def _phase(a: Any, b: Any) -> tuple[float, float, float]:
    """Phase correlation: the (dy, dx) that slides b onto a, plus the peak
    height (how much to believe it). Subpixel by parabolic interpolation."""
    np = _numpy()
    h, w = a.shape
    win = (np.hanning(h)[:, None] * np.hanning(w)[None, :]).astype(np.float32)
    A = np.fft.rfft2(np.nan_to_num(a) * win)
    B = np.fft.rfft2(np.nan_to_num(b) * win)
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-9
    r = np.fft.irfft2(R, s=(h, w))
    iy, ix = cast(tuple[int, int],
                  np.unravel_index(int(np.argmax(r)), r.shape))
    peak = float(r[iy, ix])

    def _sub(i: int, n: int, axis: int) -> float:
        lo = r[(iy - 1) % h, ix] if axis == 0 else r[iy, (ix - 1) % w]
        hi = r[(iy + 1) % h, ix] if axis == 0 else r[iy, (ix + 1) % w]
        den = float(lo - 2 * peak + hi)
        off = 0.0 if abs(den) < 1e-12 else 0.5 * float(lo - hi) / den
        off = max(-1.0, min(1.0, off))
        return (i - n if i > n // 2 else i) + off

    return _sub(iy, h, 0), _sub(ix, w, 1), peak


def bandpass(g: Any) -> Any:
    """Illumination-invariant structure image: gradient magnitude, then
    local contrast normalisation.

    Registration must not see brightness. Handheld photos of the same board
    differ in exposure, white balance, and carry a specular glare blob that
    is brighter than anything real — correlating raw luma just aligns the
    glare. The gradient kills the exposure and the low-frequency lighting
    gradient; dividing by the local RMS stops one high-contrast region (the
    glare rim, a connector) from outvoting the whole rest of the board.
    """
    np = _numpy()
    f = np.nan_to_num(g).astype(np.float32)
    gx = np.zeros_like(f)
    gy = np.zeros_like(f)
    gx[:, 1:-1] = f[:, 2:] - f[:, :-2]
    gy[1:-1, :] = f[2:, :] - f[:-2, :]
    m = np.hypot(gx, gy)
    loc = m
    for _ in range(3):                      # box blur ~ local RMS scale
        p = np.pad(loc, 1, mode="edge")
        loc = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]
               + p[1:-1, 1:-1]) / 5.0
    return m / (loc + float(np.mean(m)) * 0.5 + 1e-6)


def _ncc(a: Any, b: Any, valid: Any) -> float:
    """Normalised cross-correlation over the overlap only. This is the score
    that ranks candidate transforms: phase-correlation peak height is NOT
    comparable between different rotations (a 180-degree flip of a board full
    of parallel traces scores just as high), so peak picks the cell and NCC
    picks the winner."""
    np = _numpy()
    n = float(np.count_nonzero(valid))
    if n < a.size * 0.15:
        return -1.0                 # too little overlap to mean anything
    av, bv = a[valid], b[valid]
    av = av - av.mean()
    bv = bv - bv.mean()
    den = float(np.sqrt(float(av @ av) * float(bv @ bv)))
    return 0.0 if den < 1e-9 else float(av @ bv) / den


def register(ref_g: Any, img_g: Any, *, scales: tuple[float, ...] = SCALES,
             rots: tuple[int, ...] = ROTS) -> dict[str, float]:
    """Find the similarity transform putting img into ref's frame.

    Coarse-to-fine over a resolution pyramid. The coarse grid alone cannot
    solve this: at 15-degree steps fine board texture has already
    decorrelated, so the true cell does not stand out and a refine started
    from the wrong cell converges confidently to nothing. Blurring to a low
    resolution first widens each basin until 15 degrees is inside it, then
    every level re-searches a shrinking window at higher resolution.

    Scoring is on `bandpass`, never raw luma, so exposure and glare cannot
    win a cell; translation comes from an FFT phase correlation per cell and
    the cell is ranked by NCC of the actually aligned overlap.

    # ponytail: pyramid + local refine, O(scales*rots) FFTs at the coarsest
    # level only. Fourier-Mellin would be O(1) in scale/rotation — swap it
    # in if photo counts reach the hundreds; at 20 photos this is seconds
    # and has no log-polar resampling bugs to debug at 3am.
    """
    np = _numpy()
    rh, rw = ref_g.shape[:2]
    k = REG / float(max(rh, rw))
    ih, iw = img_g.shape[:2]
    base = REG / float(max(ih, iw))

    def level(res: int) -> tuple[Any, Any, int, int]:
        f = res / float(REG)
        w2, h2 = max(8, int(rw * k * f)), max(8, int(rh * k * f))
        b = bandpass(resize(ref_g, w2, h2))
        return b, b - float(np.mean(b)), w2, h2

    cache: dict[int, tuple[Any, Any, int, int]] = {}

    def probe(s: float, rot: float, res: int) -> dict[str, float]:
        """Score one (scale, rotation) cell at one pyramid level."""
        if res not in cache:
            cache[res] = level(res)
        ref_b, ref_c, w2, h2 = cache[res]
        f = res / float(REG)
        sw = max(8, int(iw * base * s * f))
        sh = max(8, int(ih * base * s * f))
        if sw > w2 * 4 or sh > h2 * 4:
            return {"scale": s, "rot": rot, "dx": 0.0, "dy": 0.0, "peak": -1.0}
        cand = warp(bandpass(resize(img_g, sw, sh)), 1.0, rot, 0.0, 0.0, w2, h2)
        seen = np.isfinite(cand)
        filled = np.where(seen, cand, 0.0)
        dy, dx, _pk = _phase(ref_c, filled - float(np.mean(filled)))
        iy, ix = int(round(dy)), int(round(dx))
        rolled = np.roll(np.roll(filled, iy, axis=0), ix, axis=1)
        vmask = np.roll(np.roll(seen, iy, axis=0), ix, axis=1)
        return {"scale": s, "rot": rot, "dx": dx / f, "dy": dy / f,
                "peak": _ncc(ref_b, rolled, vmask)}

    grid = sorted((probe(s, float(rot), COARSE) for s in scales
                   for rot in rots),
                  key=lambda c: -c["peak"])
    # carry several candidates up: the coarse winner is usually right, but a
    # near-tie between two plausible orientations is exactly the case where
    # the higher-resolution levels have the evidence to decide.
    cands = grid[:TOPK]

    step_r0 = float(rots[1] - rots[0]) if len(rots) > 1 else 10.0
    res = COARSE
    while True:
        step_r = step_r0 * COARSE / res
        step_s = 0.09 * COARSE / res
        cands = [max([c] + [probe(c["scale"] * (1 + ds), c["rot"] + dr, res)
                            for ds in (-step_s, 0.0, step_s)
                            for dr in (-step_r, 0.0, step_r)
                            if (ds, dr) != (0.0, 0.0)],
                     key=lambda x: x["peak"])
                 for c in cands]
        if res >= REG:
            break
        res = min(REG, res * 2)
        cands = sorted((probe(c["scale"], c["rot"], res) for c in cands),
                       key=lambda c: -c["peak"])[:max(1, len(cands) // 2)]

    best = max(cands, key=lambda c: c["peak"])
    out = {"scale": float(base * best["scale"] / k), "rot": best["rot"] % 360,
           "dx": float(best["dx"] / k), "dy": float(best["dy"] / k),
           "peak": float(best["peak"])}

    return polish(ref_g, img_g, out)


def polish(ref_g: Any, img_g: Any, t: dict[str, float]) -> dict[str, float]:
    """Refine a transform at the frame's native resolution.

    Everything above is solved at REG px, so its residual error is scaled up
    on the way out: half a degree and half a percent are invisible at 512 px
    and are several pixels of slip across a real 1400 px board — exactly the
    smear that turns a median stitch into a blurrier picture than one photo.
    Rotation and scale have to be polished too, not just translation: a
    small rotation error cannot be undone by any shift, and it is the one
    that destroys fine traces at the edges of the frame.

    Coordinate descent on rotation and scale, translation re-solved by FFT
    for each trial (it is free and exact), scored by NCC on bandpassed
    structure. Never returns something worse than it was given.
    """
    np = _numpy()
    rh, rw = ref_g.shape[:2]
    ref_b = bandpass(ref_g)
    ref_c = ref_b - float(np.mean(ref_b))

    def score(scale: float, rot: float) -> dict[str, float]:
        cand = warp(img_g, scale, rot, 0.0, 0.0, rw, rh)
        seen = np.isfinite(cand)
        if np.count_nonzero(seen) < seen.size * 0.10:
            return {"scale": scale, "rot": rot, "dx": 0.0, "dy": 0.0,
                    "peak": -1.0}
        filled = np.where(seen, bandpass(np.where(seen, cand, 0.0)), 0.0)
        dy, dx, _pk = _phase(ref_c, filled - float(np.mean(filled)))
        iy, ix = int(round(dy)), int(round(dx))
        return {"scale": scale, "rot": rot, "dx": float(dx), "dy": float(dy),
                "peak": _ncc(ref_b,
                             np.roll(np.roll(filled, iy, axis=0), ix, axis=1),
                             np.roll(np.roll(seen, iy, axis=0), ix, axis=1))}

    best = score(t["scale"], t["rot"])
    if best["peak"] < 0:
        return t
    dr, ds = 0.5, 0.004          # half a degree, half a percent: the residue
    for _ in range(5):
        trial = [score(best["scale"] * (1 + s), best["rot"] + r)
                 for s in (-ds, 0.0, ds) for r in (-dr, 0.0, dr)
                 if (s, r) != (0.0, 0.0)]
        top = max(trial, key=lambda c: c["peak"])
        if top["peak"] > best["peak"]:
            best = top
        else:
            dr, ds = dr / 2.0, ds / 2.0
    return {"scale": best["scale"], "rot": best["rot"] % 360,
            "dx": best["dx"], "dy": best["dy"], "peak": best["peak"]}


# ------------------------------------------------------------- stitching


def _nanmedian(a: Any, axis: int = 0) -> Any:
    """nanmedian without the all-NaN RuntimeWarning. A pixel no view covered
    (or a tile no view could correlate) is an expected outcome here, not a
    numerical accident — the caller reads it off the coverage map."""
    import warnings
    np = _numpy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(a, axis=axis)


def stitch(imgs: list[Any], xforms: list[dict[str, float]],
           out_w: int, out_h: int) -> tuple[Any, Any]:
    """Warp every photo into the reference frame and take the per-pixel
    median. Median, not mean: glare, fingers and shadows are outliers in a
    handheld set, and the median simply ignores them.

    Returns (rgb uint8, coverage 0..1) — coverage says how many views saw
    each pixel, which is also how much the LLM should trust it.
    """
    np = _numpy()
    acc = np.full((len(imgs), out_h, out_w, 3), np.nan, dtype=np.float32)
    for i, (im, t) in enumerate(zip(imgs, xforms)):
        acc[i] = warp(im, t["scale"], t["rot"], t["dx"], t["dy"],
                      out_w, out_h)
    cover = np.mean(np.isfinite(acc[..., 0]), axis=0).astype(np.float32)
    out = np.zeros((out_h, out_w, 3), dtype=np.float32)
    for y0 in range(0, out_h, 256):          # row blocks: median is float64
        y1 = min(out_h, y0 + 256)
        out[y0:y1] = np.nan_to_num(_nanmedian(acc[:, y0:y1]))
    return np.clip(out, 0, 255).astype(np.uint8), cover


# ------------------------------------------------------------ enhancement


def clahe(g: Any, tiles: int = 8, clip: float = 3.0) -> Any:
    """Contrast-limited adaptive histogram equalisation. This is the step
    that makes silkscreen part numbers readable: phone photos always have a
    lighting gradient, and global equalisation just amplifies the gradient.
    Per-tile clipped CDFs, bilinearly blended between tile centres."""
    np = _numpy()
    h, w = g.shape
    th, tw = max(1, h // tiles), max(1, w // tiles)
    ny, nx = max(1, h // th), max(1, w // tw)
    q = np.clip(g, 0, 255).astype(np.uint8)
    luts = np.zeros((ny, nx, 256), dtype=np.float32)
    for ty in range(ny):
        for tx in range(nx):
            y1 = h if ty == ny - 1 else (ty + 1) * th
            x1 = w if tx == nx - 1 else (tx + 1) * tw
            cell = q[ty * th:y1, tx * tw:x1]
            hist = np.bincount(cell.ravel(), minlength=256).astype(np.float32)
            limit = clip * hist.sum() / 256.0
            excess = float(np.sum(np.maximum(hist - limit, 0)))
            hist = np.minimum(hist, limit) + excess / 256.0
            cdf = np.cumsum(hist)
            luts[ty, tx] = 255.0 * cdf / max(cdf[-1], 1e-6)
    # bilinear blend of the four surrounding tile LUTs
    yy = np.clip((np.arange(h, dtype=np.float32) - th / 2) / th, 0, ny - 1)
    xx = np.clip((np.arange(w, dtype=np.float32) - tw / 2) / tw, 0, nx - 1)
    y0, x0 = yy.astype(np.int64), xx.astype(np.int64)
    y1i = np.minimum(y0 + 1, ny - 1)
    x1i = np.minimum(x0 + 1, nx - 1)
    fy, fx = (yy - y0)[:, None], (xx - x0)[None, :]
    v = q.astype(np.int64)
    a = np.take_along_axis(luts[y0][:, x0], v[..., None], axis=2)[..., 0]
    b = np.take_along_axis(luts[y0][:, x1i], v[..., None], axis=2)[..., 0]
    c = np.take_along_axis(luts[y1i][:, x0], v[..., None], axis=2)[..., 0]
    d = np.take_along_axis(luts[y1i][:, x1i], v[..., None], axis=2)[..., 0]
    top = a * (1 - fx) + b * fx
    bot = c * (1 - fx) + d * fx
    return np.clip(top * (1 - fy) + bot * fy, 0, 255).astype(np.uint8)


def sobel(g: Any) -> Any:
    """Edge magnitude — trace and pad outlines, normalised to 0..255."""
    np = _numpy()
    gx = np.zeros_like(g, dtype=np.float32)
    gy = np.zeros_like(g, dtype=np.float32)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    m = np.hypot(gx, gy)
    return np.clip(m * (255.0 / max(float(np.percentile(m, 99)), 1e-6)),
                   0, 255).astype(np.uint8)


def unsharp(g: Any, amount: float = 1.5) -> Any:
    """Unsharp mask via a 3x3 box blur applied twice — cheap approximation
    of a gaussian, enough to lift fine silkscreen strokes."""
    np = _numpy()
    b = g.astype(np.float32)
    for _ in range(2):
        p = np.pad(b, 1, mode="edge")
        b = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]
             + p[1:-1, 1:-1]) / 5.0
    return np.clip(g + amount * (g - b), 0, 255).astype(np.uint8)


def isolate(rgb: Any, what: str) -> Any:
    """Pull one material out of the stitch as a mask.

    silk   bright and unsaturated — white/yellow legend on any mask colour
    copper warm and saturated — exposed pads, gold fingers, tinned traces
    mask   the dominant saturated background hue — the soldermask itself,
           so its complement is "everything that is not bare board"
    """
    np = _numpy()
    f = rgb.astype(np.float32)
    mx, mn = f.max(axis=2), f.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    if what == "silk":
        v = (mx / 255.0) * (1.0 - sat)
    elif what == "copper":
        warm = (f[..., 0] - f[..., 2]) / 255.0
        v = np.clip(warm, 0, 1) * sat
    elif what == "mask":
        v = sat * (1.0 - np.clip((f[..., 0] - f[..., 2]) / 255.0, 0, 1))
    else:
        raise ValueError(f"unknown isolate {what!r} (silk|copper|mask)")
    lo, hi = float(np.percentile(v, 2)), float(np.percentile(v, 98))
    return np.clip((v - lo) * (255.0 / max(hi - lo, 1e-6)),
                   0, 255).astype(np.uint8)


def enhance(rgb: Any) -> dict[str, Any]:
    """Every view of the stitch worth handing a model. Different questions
    read off different images, so all of them get written."""
    g = gray(rgb)
    c = clahe(g)
    return {"contrast": c, "sharp": unsharp(c.astype(_numpy().float32)),
            "edges": sobel(g), "silk": isolate(rgb, "silk"),
            "copper": isolate(rgb, "copper"), "mask": isolate(rgb, "mask")}


# ------------------------------------------------------ parallax → height


def height_field(imgs: list[Any], xforms: list[dict[str, float]],
                 out_w: int, out_h: int, tile: int = TILE) -> Any:
    """Standoff height per tile from residual parallax.

    A similarity transform can only align one plane — the board. Anything at
    a different height keeps a residual displacement between views, growing
    with standoff and with how far the camera moved. Correlate each aligned
    view against the reference per tile and the residual magnitude is a
    monotone stand-in for component height. Median across views, so one bad
    view cannot invent a component.

    # ponytail: relative height only (no calibrated baselines), median over
    # views for robustness. Real metric depth needs camera intrinsics and a
    # bundle adjust — add COLMAP poses if millimetres ever have to be true.
    """
    np = _numpy()
    ref = np.nan_to_num(warp(gray(imgs[0]), xforms[0]["scale"],
                             xforms[0]["rot"], xforms[0]["dx"],
                             xforms[0]["dy"], out_w, out_h))
    ny, nx = max(1, out_h // tile), max(1, out_w // tile)
    res = np.full((max(1, len(imgs) - 1), ny, nx), np.nan, dtype=np.float32)
    for i in range(1, len(imgs)):
        t = xforms[i]
        cur = np.nan_to_num(warp(gray(imgs[i]), t["scale"], t["rot"],
                                 t["dx"], t["dy"], out_w, out_h))
        for ty in range(ny):
            for tx in range(nx):
                ys, xs = ty * tile, tx * tile
                a = ref[ys:ys + tile, xs:xs + tile]
                b = cur[ys:ys + tile, xs:xs + tile]
                if a.shape != (tile, tile) or float(np.std(b)) < 1.0:
                    continue          # flat tile: correlation is noise
                dy, dx, peak = _phase(a, b)
                if peak > 0.02 and abs(dy) < tile / 3 and abs(dx) < tile / 3:
                    res[i - 1, ty, tx] = float(math.hypot(dy, dx))
    hm = np.nan_to_num(_nanmedian(res))
    # Subtract the noise floor, do NOT rescale to full range. Residual
    # misregistration puts a baseline wobble on every tile, including the
    # bare laminate; normalising by a high percentile would stretch that
    # wobble to full scale and paint a flat board as if it were covered in
    # tall parts. The low quantile IS the board plane, and what survives
    # above it by more than the plane's own spread is a real standoff.
    floor = float(np.percentile(hm, 25))
    spread = float(np.percentile(hm, 75)) - floor
    hm = np.maximum(hm - (floor + spread), 0.0)
    hi = float(np.percentile(hm, 99))
    if hi > 1e-6:
        hm = np.clip(hm / hi, 0, 1)
    return resize(hm[..., None], out_w, out_h)[..., 0]


# ------------------------------------------------------- gaussian splat


def splat_ply(rgb: Any, height: Any, mm_per_px: float,
              max_mm: float = 5.0, step: int = 4) -> bytes:
    """Height field + colour → 3D gaussian splat .ply (INRIA 3DGS layout:
    xyz, SH DC colour, opacity, log scales, quaternion). Every `step`-th
    pixel becomes one gaussian sized to its own footprint, so a standard
    splat viewer shows the board as geometry rather than a flat photo.

    # ponytail: splats are baked straight from the height field, not
    # optimised against the views. Photometric refinement needs an
    # autograd stack; this already gives a viewer-ready 3D model of where
    # the components stand.
    """
    np = _numpy()
    h, w = height.shape
    ys, xs = np.mgrid[0:h:step, 0:w:step]
    z = height[::step, ::step] * max_mm
    col = rgb[::step, ::step].astype(np.float32) / 255.0
    n = xs.size
    px = (xs.ravel() * mm_per_px).astype(np.float32)
    py = ((h - 1 - ys).ravel() * mm_per_px).astype(np.float32)
    pz = z.ravel().astype(np.float32)
    sh = ((col.reshape(-1, 3) - 0.5) / 0.28209479177387814).astype(np.float32)
    scale = math.log(max(step * mm_per_px * 0.6, 1e-4))
    rows = np.zeros((n, 14), dtype=np.float32)
    rows[:, 0], rows[:, 1], rows[:, 2] = px, py, pz
    rows[:, 5] = 1.0                       # normal +z
    rows[:, 6:9] = sh
    rows[:, 9] = 6.0                       # opacity logit ~ 0.998
    rows[:, 10:13] = scale
    rows[:, 13] = 1.0                      # quaternion w (identity)
    # 3DGS property order: x y z nx ny nz f_dc_0..2 opacity scale_0..2 rot_0..3
    out = np.zeros((n, 17), dtype=np.float32)
    out[:, :13] = rows[:, :13]
    out[:, 13] = 1.0                       # rot_0 (w)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        "property float f_dc_0\nproperty float f_dc_1\nproperty float f_dc_2\n"
        "property float opacity\n"
        "property float scale_0\nproperty float scale_1\nproperty float scale_2\n"
        "property float rot_0\nproperty float rot_1\nproperty float rot_2\n"
        "property float rot_3\n"
        "end_header\n").encode()
    return header + bytes(out.astype("<f4").tobytes())


# ------------------------------------------------------------- PNG output


def write_png(path: str, img: Any) -> str:
    """Save a gray or RGB numpy array as PNG, reusing the repo's encoder."""
    np = _numpy()
    a = np.asarray(img)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    a = np.clip(np.nan_to_num(a), 0, 255).astype(np.uint8)
    from .raster import _png
    h, w = a.shape[:2]
    with open(path, "wb") as f:
        f.write(_png(w, h, bytearray(a.tobytes())))
    return path


# ---------------------------------------------------------------- driver


def _side_paths(photos: dict[str, list[str]] | list[str]) -> dict[str, list[str]]:
    """Accept {'top': [...], 'bottom': [...]} or one flat list split by a
    `top`/`bot` hint in the filename (everything unhinted is top)."""
    if isinstance(photos, dict):
        out = {s: [str(p) for p in photos.get(s, [])] for s in SIDES}
    else:
        out = {"top": [], "bottom": []}
        for p in photos:
            name = os.path.basename(str(p)).lower()
            side = "bottom" if ("bot" in name or "back" in name
                                or "_b." in name) else "top"
            out[side].append(str(p))
    if not any(out.values()):
        raise ValueError("no photos given (photos=[...] or "
                         "photos={'top': [...], 'bottom': [...]})")
    return out


def scan_side(paths: list[str], outdir: str, side: str,
              board_mm: float | None = None,
              maxdim: int = MAXDIM) -> dict[str, object]:
    """One side end to end. Returns the manifest fragment for that side."""
    np = _numpy()
    os.makedirs(outdir, exist_ok=True)
    raws = [fit(load(p), maxdim) for p in paths]
    sharp = [sharpness(gray(r)) for r in raws]
    ref = int(max(range(len(raws)), key=lambda i: sharp[i]))
    order = [ref] + [i for i in range(len(raws)) if i != ref]
    imgs = [raws[i] for i in order]
    names = [os.path.basename(paths[i]) for i in order]
    ref_g = gray(imgs[0])
    out_h, out_w = imgs[0].shape[:2]
    xforms: list[dict[str, float]] = [
        {"scale": 1.0, "rot": 0.0, "dx": 0.0, "dy": 0.0, "peak": 1.0}]
    for im in imgs[1:]:
        xforms.append(register(ref_g, gray(im)))
    # peak is an NCC (-1..1) on bandpassed structure after polish. Measured
    # on a real board (NComputing L130 photos, simulated handheld shoot):
    # correct locks scored >= 0.37, genuine mismatches <= 0.22. A frame that
    # lands in the gap is the one case worth being strict about — a
    # misregistered photo does not just add noise, it drags the median into
    # a double-exposed blur that no amount of enhancement recovers.
    keep = [i for i, t in enumerate(xforms) if t["peak"] >= 0.30]
    dropped = [names[i] for i in range(len(names)) if i not in keep]
    imgs = [imgs[i] for i in keep]
    xforms = [xforms[i] for i in keep]

    rgb, cover = stitch(imgs, xforms, out_w, out_h)
    files: dict[str, str] = {}
    files["stitch"] = write_png(os.path.join(outdir, f"{side}-stitch.png"), rgb)
    files["coverage"] = write_png(os.path.join(outdir, f"{side}-coverage.png"),
                                  cover * 255.0)
    for key, im in enhance(rgb).items():
        files[key] = write_png(os.path.join(outdir, f"{side}-{key}.png"), im)

    hm = height_field(imgs, xforms, out_w, out_h)
    files["height"] = write_png(os.path.join(outdir, f"{side}-height.png"),
                                hm * 255.0)
    mm_px = (board_mm / float(out_w)) if board_mm else 0.1
    ply = os.path.join(outdir, f"{side}-splat.ply")
    with open(ply, "wb") as f:
        f.write(splat_ply(rgb, hm, mm_px))
    files["splat"] = ply
    return {
        "side": side, "photos": len(paths), "used": len(imgs),
        "dropped": dropped, "reference": names[0],
        "canvas": [int(out_w), int(out_h)], "mm_per_px": round(mm_px, 5),
        "coverage_mean": round(float(np.mean(cover)), 3),
        "relief": round(float(np.mean(hm)), 4),
        "files": files,
        "transforms": [
            {"photo": n, **{k: round(v, 3) for k, v in t.items()}}
            for n, t in zip(names, xforms)],
    }


def scan(photos: dict[str, list[str]] | list[str], outdir: str = "scan",
         board_mm: float | None = None,
         maxdim: int = MAXDIM) -> dict[str, object]:
    """Full deterministic pass: every side stitched, enhanced, splatted, and
    a manifest.json listing every artifact for the analysis step.

    `maxdim` is the stitch canvas resolution, and it is the ceiling on every
    downstream detail: a 0.2 mm trace is ~3 px at the 1600 default and ~5 px
    at 2400. Raising it costs memory quadratically — the stitch holds every
    frame at once (10 photos: 0.3 GB at 1600, 0.7 GB at 2400) — so it is a
    knob, not a default."""
    sides = _side_paths(photos)
    os.makedirs(outdir, exist_ok=True)
    man: dict[str, object] = {"outdir": outdir, "sides": {}}
    for side, paths in sides.items():
        if paths:
            cast(dict[str, object], man["sides"])[side] = scan_side(
                paths, outdir, side, board_mm, maxdim)
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(man, f, indent=2)
    man["manifest"] = os.path.join(outdir, "manifest.json")
    return man


# ------------------------------------------------------------- analysis

PROMPT = """You are reverse-engineering a physical PCB from a photo scan.

For each side you get the same board rendered several ways — use all of them,
they answer different questions:
  stitch    median composite of every photo: true colour, glare removed
  contrast  adaptive histogram equalisation: READ THE SILKSCREEN HERE
  sharp     unsharp-masked contrast: faint or worn part markings
  edges     Sobel magnitude: trace routes, pad outlines, board outline
  silk      legend isolated from the mask: reference designators, polarity
  copper    exposed copper isolated: pads, vias, fingers, test points
  height    parallax standoff, bright = stands off the board: which
            footprints are tall parts (electrolytics, connectors, cans)

Images marked ZOOM are native-resolution crops of one quadrant of the same
board (they overlap, so a trace crossing a seam is whole in one of them).
The whole-board views tell you where things are; the ZOOM crops are the only
images where individual traces and small part numbers are actually resolved,
so trace copper and read fine markings in those.

Work in this order and say what you actually see, never what a board like
this usually has:

Never list a designator you cannot actually read, and never continue a
sequence (R1, R2, R3...) past what is visible: an invented run of parts is
worse than a short honest list, and it wastes the whole answer. Prefer the
ICs and connectors you can identify over exhaustively counting passives.

1. INVENTORY. Every reference designator you can read, its side, its package
   (0402/0603/0805/SOT-23/SOIC-8/QFN/TO-220/...) and any marking on the body.
   Mark anything you are guessing as `?`.
2. PART IDENTIFICATION. Decode the markings into real parts. SMD codes are
   ambiguous: give the candidates and what would disambiguate them.
3. NETLIST. Trace the copper. Name the power rails and ground, then the
   signal nets, pin by pin. Note where a trace disappears under a part or to
   the other side through a via — an honest gap beats an invented net.
4. FUNCTION. What is this circuit? Name the blocks (supply, regulation,
   MCU, sensing, driver, interface, protection) and how they chain.
5. BOARD. Dimensions, layer count evidence, mounting holes, connectors.
6. CONFIDENCE. What is solid, what is a guess, and which extra photo would
   settle each open question.

Then write a complete .ocd source for the reconstructed board:

  board NAME 100x80 4L      <- literally "<width>x<height>" then "<n>L";
                               "board X 100 80 4" is NOT valid syntax
  part REF FOOTPRINT [VALUE] [x=.. y=..]
  net NAME :: REF.PIN <--> REF.PIN
  power NET [NET...]        <- e.g. "power 3V3 GND"
  route NET on LAYER        <- LAYER is a number: "route GND on 1"
  silk LEVEL                <- LEVEL is 0-3: "silk 2" ("silk top" is invalid)

Net names are bare words: `3V3`, `VCC`, `GND`, `USB_DP`. No leading `+` and
no `-` (`+3V3` and `VCC-5` do not parse); write `power 3V3 GND` with the
same spelling used in the `net` lines.

FOOTPRINT must come from the list below — it is the whole vocabulary, and an
invented name (a KiCad-style `Barrel_Jack`, a guessed `QFP208`) makes the
file unloadable. Pick the closest available package and put what you really
saw in the VALUE and a `#` comment.

A minimal complete example:

  board demo 40x30 2L
  part U1 SOIC8 LM358 x=10 y=15
  part R1 R0805 10k x=22 y=15
  net VCC :: U1.8 <--> R1.1
  power VCC

Put it in one fenced ```ocd block, with `#` comments marking every part of
the reconstruction you are unsure about. Only include nets you actually
traced.

Finally, if answers from the person who took the photos would change your
conclusions, ask for them: put the questions in one fenced ```questions
block, one per line, most valuable first, at most five. Ask only what the
images cannot settle - what equipment the board came out of, what a
connector mates with, a part number you can see is printed but cannot
resolve, a voltage or a measurement. Never ask what the images already
answer, and leave the block out entirely if nothing is worth asking.
"""


def footprint_menu() -> str:
    """The footprint names the parts library actually serves.

    Read live rather than hardcoded: the library grows, and a stale list in
    a prompt sends the model back to inventing names. Measured cause of
    unloadable drafts — deepseek-flash guessed KiCad-style `Barrel_Jack` and
    `QFP208` when nothing told it the vocabulary.
    """
    from .parts import FOOTPRINTS
    return ("\n\nAvailable FOOTPRINT names (use these exactly):\n  "
            + ", ".join(sorted(FOOTPRINTS)))


def read_doc(path: str, limit: int = 20000) -> str:
    """Text of a user-supplied manual, datasheet or note.

    PDFs go through `pdftotext -layout` (poppler); everything else is read as
    text. Truncated to `limit` chars: a 200-page datasheet would swamp the
    images, and the opening pages carry the part number, block diagram and
    pin table that actually help.
    """
    import shutil
    import subprocess
    if not os.path.isfile(path):
        raise ValueError(f"no such document: {path}")
    if path.lower().endswith(".pdf"):
        exe = shutil.which("pdftotext")
        if exe is None:
            raise ValueError(
                f"{os.path.basename(path)} is a PDF and pdftotext is not on "
                "PATH (install poppler-utils, or pass the text instead)")
        r = subprocess.run([exe, "-layout", path, "-"],
                           capture_output=True, timeout=180)
        if r.returncode != 0:
            raise ValueError(f"pdftotext failed on {os.path.basename(path)}: "
                             f"{r.stderr.decode(errors='replace')[:200]}")
        body = r.stdout.decode("utf-8", errors="replace")
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            body = f.read()
    body = body.strip()
    if len(body) > limit:
        body = body[:limit] + f"\n[... truncated at {limit} chars]"
    return body


def context_block(note: str = "", docs: list[str] | None = None,
                  answers: dict[str, str] | None = None) -> str:
    """Everything the user told us, as one prompt section.

    Kept separate from PROMPT because it is evidence, not instruction, and
    the model is told exactly that: a manual narrows what a marking can mean,
    but it must never overrule what the photographs actually show.
    """
    out: list[str] = []
    if note:
        out.append(f"What the owner says this board is:\n{note}")
    for d in docs or []:
        out.append(f"--- supplied document: {os.path.basename(d)} ---\n"
                   f"{read_doc(d)}")
    for q, a in (answers or {}).items():
        out.append(f"Q: {q}\nA: {a}")
    if not out:
        return ""
    return ("\n\nSupplied context. Use it to resolve ambiguity - a datasheet "
            "pin table or a manual block diagram can settle what a marking "
            "means. It does not overrule the photographs: where the context "
            "and the board disagree, believe the board and say so.\n\n"
            + "\n\n".join(out))


def extract_questions(reply: str) -> list[str]:
    """The ```questions block, if the model asked anything back."""
    import re
    m = re.search(r"```questions\n(.*?)(?:```|\Z)", reply, re.S)
    if not m:
        return []
    out: list[str] = []
    for line in m.group(1).splitlines():
        q = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", line).strip()
        if q:
            out.append(q)
    return out[:5]


def _b64_arr(img: Any) -> str:
    """uint8 array -> PNG data URI."""
    import base64
    np = _numpy()
    from .raster import _png
    a = np.clip(np.nan_to_num(np.asarray(img)), 0, 255).astype(np.uint8)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    h, w = a.shape[:2]
    return ("data:image/png;base64,"
            + base64.b64encode(_png(w, h, bytearray(a.tobytes()))).decode())


def _b64_png(path: str, maxdim: int = 1024) -> str:
    """Artifact → data URI, downscaled so the overview images fit a context
    window."""
    return _b64_arr(fit(load(path), maxdim))


def tile_uris(path: str, grid: int = 2, maxdim: int = 1024,
              overlap: float = 0.08) -> list[tuple[str, str]]:
    """Cut an artifact into `grid`x`grid` overlapping crops at native
    resolution: [(label, data-uri), ...].

    This is what makes traces traceable. A 3000 px board scaled to one
    1024 px image puts a 0.2 mm trace at ~2 px — enough to say "there is
    copper", not enough to say "this pad connects to that pin". The same
    board as 2x2 crops keeps ~6 px per trace, and 3x3 keeps ~10.

    Tiles overlap so a net crossing a seam is whole in at least one crop;
    labels carry the quadrant so the model can say where it looked.
    """
    np = _numpy()
    img = np.asarray(load(path))
    h, w = img.shape[:2]
    out: list[tuple[str, str]] = []
    ov = max(0.0, min(0.3, overlap))
    for gy in range(grid):
        for gx in range(grid):
            x0 = max(0, int((gx / grid - ov) * w))
            x1 = min(w, int(((gx + 1) / grid + ov) * w))
            y0 = max(0, int((gy / grid - ov) * h))
            y1 = min(h, int(((gy + 1) / grid + ov) * h))
            crop = img[y0:y1, x0:x1]
            if crop.size == 0:
                continue
            if grid == 1:
                where = "whole"
            else:
                where = (("top" if gy == 0 else
                          "bottom" if gy == grid - 1 else f"row{gy + 1}")
                         + "-" + ("left" if gx == 0 else
                                  "right" if gx == grid - 1 else f"col{gx + 1}"))
            out.append((where, _b64_arr(fit(crop, maxdim))))
    return out


VIEWS = ("stitch", "contrast", "edges", "silk", "copper", "height")
ZOOM = ("contrast", "copper")   # the views worth sending at native pixels


def analyse(manifest: dict[str, object], *, views: tuple[str, ...] = VIEWS,
            note: str = "", docs: list[str] | None = None,
            answers: dict[str, str] | None = None,
            zoom: int = 2, timeout: float = 600.0) -> str:
    """Hand the scan to a vision model and get the reverse-engineering
    report plus a reconstructed .ocd back. Needs a vision-capable model at
    OCD_LLM_BASE/OCD_LLM_MODEL.

    `note` is what the owner says the board is, `docs` are manuals or
    datasheets (PDF or text) to read alongside the images, and `answers`
    carries replies to questions a previous pass asked.

    `zoom` is the tile grid for the detail views: every side sends whole-board
    overviews plus zoom^2 native-resolution crops, because a downscaled
    overview cannot resolve a trace (measured: ~2 px per trace at 1024 px,
    ~6 px as 2x2 tiles). zoom=1 disables tiling."""
    from . import llm
    images: list[str] = []
    lines: list[str] = []
    sides = cast(dict[str, object], manifest.get("sides", {}))
    for side in SIDES:
        s = sides.get(side)
        if not isinstance(s, dict):
            continue
        files = cast(dict[str, str], s.get("files", {}))
        mmpx = s.get("mm_per_px")
        lines.append(
            f"{side}: {s.get('used')}/{s.get('photos')} photos registered, "
            f"canvas {s.get('canvas')} px at {mmpx} mm/px, "
            f"mean coverage {s.get('coverage_mean')}")
        for v in views:
            if v in files:
                images.append(_b64_png(files[v]))
                lines.append(f"  image {len(images)}: {side} {v} (whole board)")
        for v in ZOOM:
            if v in files and zoom > 1:
                for where, uri in tile_uris(files[v], zoom):
                    images.append(uri)
                    lines.append(f"  image {len(images)}: {side} {v} "
                                 f"ZOOM {where} (native resolution)")
    if not images:
        raise ValueError("nothing to analyse — run scan() first")
    text = (PROMPT + footprint_menu() + "\n\nScan report:\n"
            + "\n".join(lines) + context_block(note, docs, answers))
    return llm.vision(text, images, timeout=timeout)


def extract_ocd(reply: str) -> str:
    """Pull the ```ocd block out of a model reply (any fence label works).

    Tolerates an unterminated block: a model that hit its token limit
    mid-answer still wrote real lines, and discarding them over a missing
    closing fence turns a partial result into no result. Also drops the
    runaway repetition small models fall into (`part R1..R400`, all
    identical), which is usually what ate the token budget in the first
    place — measured on qwen2.5vl:7b reading a real board.
    """
    import re
    for m in re.finditer(r"```(\w*)\n(.*?)(?:```|\Z)", reply, re.S):
        body = m.group(2)
        if m.group(1).lower() in ("ocd", "") and "board " in body:
            return _dedupe(body).strip() + "\n"
    raise ValueError(
        "no ```ocd block in the reply (the model may have run out of tokens, "
        "or cannot see images \u2014 check OCD_LLM_MODEL)")


def _dedupe(body: str, run: int = 8) -> str:
    """Drop consecutive same-shaped lines past `run` repeats. A degenerate
    decoder emits hundreds of `part R<n> 0402 100R` lines differing only by
    an incrementing number; keeping the first few preserves the intent
    without letting a stuck model define the board."""
    import re
    out: list[str] = []
    shape: str | None = None
    n = 0
    for line in body.splitlines():
        s = re.sub(r"\d+", "#", line.strip())
        if s and s == shape:
            n += 1
            if n >= run:
                continue
        else:
            shape, n = s, 0
        out.append(line)
    return "\n".join(out)


def reverse(photos: dict[str, list[str]] | list[str], outdir: str = "scan",
            *, board_mm: float | None = None, note: str = "",
            docs: list[str] | None = None,
            answers: dict[str, str] | None = None,
            maxdim: int = MAXDIM, zoom: int = 2,
            llm_analysis: bool = True) -> dict[str, object]:
    """Photos in, scan artifacts + analysis + a draft .ocd out.

    `note`/`docs` are what the owner knows (a description, a manual, a
    datasheet); `answers` feeds back replies to questions an earlier pass
    asked, so a second call is strictly better informed than the first.

    The .ocd is written only if it parses — a draft that cannot be loaded is
    a worse deliverable than the report that explains why. Questions the
    model asked come back under `questions` either way: they are the most
    useful output when the reconstruction is thin.
    """
    man = scan(photos, outdir, board_mm, maxdim)
    if not llm_analysis:
        return man
    report = analyse(man, note=note, docs=docs, answers=answers, zoom=zoom)
    rp = os.path.join(outdir, "analysis.md")
    with open(rp, "w") as f:
        f.write(report)
    man["analysis"] = rp
    asked = extract_questions(report)
    if asked:
        man["questions"] = asked
    try:
        src = extract_ocd(report)
    except ValueError as e:
        man["draft_error"] = str(e)
        return man
    dp = os.path.join(outdir, "draft.ocd")
    with open(dp, "w") as f:
        f.write(src)
    man["draft"] = dp
    try:
        from . import agent
        b = agent.loads(src, base=outdir)
        man["draft_parts"] = len(b.parts)
        man["draft_nets"] = len(b.nets)
    except (ValueError, KeyError, AssertionError, OSError) as e:
        man["draft_error"] = f"draft does not parse: {e}"
    return man


def demo() -> None:
    """Self-check on a synthetic board: three views of the same generated
    'PCB' at different scales, rotations and crops. Registration has to put
    them back together, the stitch has to look like the source, the splat
    has to be a readable .ply."""
    np = _numpy()
    rng = np.random.default_rng(7)
    h, w = 400, 520
    src = np.full((h, w, 3), 24, dtype=np.uint8)
    src[..., 1] = 92                                   # mask green
    for y0, x0, y1, x1 in ((60, 40, 76, 300), (140, 40, 156, 460),
                           (220, 120, 236, 500), (300, 60, 316, 380)):
        src[y0:y1, x0:x1] = (196, 150, 60)             # copper traces
    for cy, cx in ((90, 350), (180, 120), (260, 300), (330, 430)):
        src[cy - 22:cy + 22, cx - 34:cx + 34] = (30, 30, 30)   # bodies
        src[cy - 8:cy + 8, cx - 30:cx - 20] = (225, 225, 225)  # silk
    src = np.clip(src + rng.normal(0, 3, src.shape), 0, 255).astype(np.uint8)

    views = [src.astype(np.float32)]
    for s, r, dx, dy in ((0.72, 12.0, 18.0, -9.0), (1.31, -21.0, -25.0, 14.0)):
        v = warp(src.astype(np.float32), s, r, dx, dy, w, h)
        views.append(np.nan_to_num(v, nan=18.0))

    ref_g = gray(views[0])
    xf = [{"scale": 1.0, "rot": 0.0, "dx": 0.0, "dy": 0.0, "peak": 1.0}]
    for v in views[1:]:
        t = register(ref_g, gray(v))
        xf.append(t)
        assert t["peak"] > 0.30, f"registration failed to lock on: {t}"

    # each recovered transform must invert the one we applied
    for t, (s, r) in zip(xf[1:], ((0.72, 12.0), (1.31, -21.0))):
        assert abs(t["scale"] - 1 / s) < 0.03 * (1 / s), \
            f"scale off: got {t['scale']:.3f} want {1 / s:.3f}"
        got = (t["rot"] - (-r)) % 360
        assert min(got, 360 - got) <= 2.0, f"rotation off: {t['rot']} vs {-r}"

    rgb, cover = stitch(views, xf, w, h)
    assert rgb.shape == (h, w, 3) and rgb.dtype == np.uint8
    assert float(np.mean(cover)) > 0.5, "stitch covered almost nothing"
    err = float(np.mean(np.abs(rgb[80:320, 80:440].astype(np.float32)
                               - src[80:320, 80:440].astype(np.float32))))
    assert err < 42.0, f"stitch does not match the source (mean |err| {err:.1f})"

    g = gray(src)
    c = clahe(g)
    assert c.shape == g.shape and c.dtype == np.uint8
    assert float(np.std(c)) > float(np.std(g)) * 0.9, "clahe killed contrast"
    silk = isolate(src, "silk")
    cu = isolate(src, "copper")
    assert silk[82:98, 316:326].mean() > silk.mean(), "silk mask missed legend"
    assert cu[60:76, 40:300].mean() > cu.mean(), "copper mask missed traces"
    assert sobel(g)[60, 100:200].max() > 40, "sobel found no trace edge"

    hm = height_field(views, xf, w, h)
    assert hm.shape == (h, w) and 0.0 <= float(hm.min()) and float(hm.max()) <= 1.0

    # height must answer the question it claims to: parts that stand off the
    # board read high, a flat board reads flat. Shoot a board whose parts
    # parallax-shift with the camera while the substrate stays put.
    ph, pw = 300, 400
    flat = np.full((ph, pw, 3), 40, dtype=np.float32)
    flat[..., 1] = 100
    for i in range(8):
        flat[20 + i * 34:26 + i * 34, 30:370] = (190, 150, 60)
    flat = np.clip(flat + rng.normal(0, 3, flat.shape), 0, 255)
    boxes = [(80, 110, 26, 38), (210, 280, 24, 34)]
    onmask = np.zeros((ph, pw), dtype=bool)
    for cy, cx, hh, hw_ in boxes:
        onmask[cy - hh:cy + hh, cx - hw_:cx + hw_] = True

    def _shoot(shift: int) -> Any:
        im = flat.copy()
        for cy, cx, hh, hw_ in boxes:   # standoff -> parallax shift
            x0, x1 = max(0, cx - hw_ + shift), min(pw, cx + hw_ + shift)
            if x1 > x0:
                im[cy - hh:cy + hh, x0:x1] = (30, 30, 32)
                im[cy - hh + 6:cy - hh + 16, x0 + 5:x0 + 26] = (228, 228, 224)
        return np.clip(im + rng.normal(0, 3, im.shape), 0, 255)

    ident = {"scale": 1.0, "rot": 0.0, "dx": 0.0, "dy": 0.0, "peak": 1.0}
    relief = height_field([_shoot(s) for s in (0, -6, 6, -11, 11)],
                          [dict(ident) for _ in range(5)], pw, ph)
    on = float(relief[onmask].mean())
    off = float(relief[~onmask].mean())
    assert on > off * 2.5, f"standoff not detected (parts {on:.3f} vs board {off:.3f})"

    same = [np.nan_to_num(warp(flat, 1.0, 0.0, dx, dy, pw, ph), nan=20.0)
            for dx, dy in ((0, 0), (3, -2), (-4, 3))]
    flatm = height_field(same, [{"scale": 1.0, "rot": 0.0, "dx": -dx,
                                 "dy": -dy, "peak": 1.0}
                                for dx, dy in ((0, 0), (3, -2), (-4, 3))],
                         pw, ph)
    assert float(flatm.mean()) < 0.2, (
        f"flat board reported relief {float(flatm.mean()):.3f} — the height "
        "normalisation is stretching registration noise into fake parts")

    ply = splat_ply(rgb, hm, 0.1, step=8)
    assert ply.startswith(b"ply\nformat binary_little_endian")
    n = int(ply.split(b"element vertex ")[1].split(b"\n")[0])
    body = ply.split(b"end_header\n", 1)[1]
    assert len(body) == n * 17 * 4, "ply body does not match its header"
    assert n == len(range(0, h, 8)) * len(range(0, w, 8))

    assert _side_paths(["a_top.jpg", "b_bottom.jpg", "c.jpg"]) == {
        "top": ["a_top.jpg", "c.jpg"], "bottom": ["b_bottom.jpg"]}
    assert extract_ocd("hi\n```ocd\nboard x 10x10 2\n```\n") == "board x 10x10 2\n"
    try:
        extract_ocd("no block here")
    except ValueError:
        pass
    else:
        raise AssertionError("extract_ocd accepted a reply with no board")
    # truncated reply (model hit its token cap mid-block): keep what it wrote
    assert extract_ocd("```ocd\nboard x 10x10 2\npart R1 R0805 1k") == (
        "board x 10x10 2\npart R1 R0805 1k\n")
    # degenerate repetition (observed on qwen2.5vl:7b): keep a few, drop the run
    runaway = ("```ocd\nboard x 10x10 2\n"
               + "".join(f"part R{i} R0805 1k\n" for i in range(1, 300)) + "```")
    kept = extract_ocd(runaway)
    assert "board x 10x10 2" in kept, "dedupe dropped the board line"
    assert 5 <= kept.count("part R") <= 12, (
        f"dedupe kept {kept.count('part R')} repeated lines")
    # distinct lines must survive: dedupe keys on shape, not on similarity
    varied = ("```ocd\nboard x 10x10 2\n"
              + "".join(f"part U{i} SOIC8 c{i}\nnet N{i} :: U{i}.1 <--> U{i}.2\n"
                        for i in range(1, 30)) + "```")
    assert extract_ocd(varied).count("part U") == 29, "dedupe ate distinct parts"

    # questions back to the user: numbered, bulleted or bare, capped at five
    assert extract_questions("no block") == []
    qs = extract_questions(
        "text\n```questions\n1. What equipment is this from?\n"
        "- What mates with CN2?\n\n* Voltage on the barrel jack?\n```\n")
    assert qs == ["What equipment is this from?", "What mates with CN2?",
                  "Voltage on the barrel jack?"], qs
    assert len(extract_questions("```questions\n"
                                 + "".join(f"q{i}?\n" for i in range(9))
                                 + "```")) == 5, "question cap not applied"
    # unterminated questions block (token cap) still yields what was asked
    assert extract_questions("```questions\nOnly one?") == ["Only one?"]

    # zoom tiles: native-resolution crops that cover the board with overlap.
    # This is the only path by which a trace is more than a couple of pixels
    # wide by the time a model sees it, so the contract is worth pinning.
    import tempfile as _tf2
    _big = np.zeros((600, 800, 3), dtype=np.uint8)
    _big[::40, :] = 255                       # horizontal rules to find
    _tp = os.path.join(_tf2.mkdtemp(), "big.png")
    write_png(_tp, _big)
    _tiles = tile_uris(_tp, 2, maxdim=1024)
    assert len(_tiles) == 4, f"2x2 grid gave {len(_tiles)} tiles"
    assert [w for w, _ in _tiles] == ["top-left", "top-right",
                                      "bottom-left", "bottom-right"]
    assert all(u.startswith("data:image/png;base64,") for _, u in _tiles)
    # each tile must be a real crop: more than a quarter of the board
    # (overlap) but not the whole thing
    _t0 = _tf2.NamedTemporaryFile(suffix=".png", delete=False)
    import base64 as _b64m
    _t0.write(_b64m.b64decode(_tiles[0][1].split(",", 1)[1]))
    _t0.close()
    _th, _tw = load(_t0.name).shape[:2]
    assert 400 <= _tw < 800, f"tile width {_tw} is not a crop of 800"
    assert 300 <= _th < 600, f"tile height {_th} is not a crop of 600"
    # tiling must not silently downscale below the overview's own detail
    assert _tw / 800 > 0.5, "tile keeps less than half the source width"
    assert [w for w, _ in tile_uris(_tp, 1)] == ["whole"]
    assert len(tile_uris(_tp, 3)) == 9
    os.unlink(_t0.name)
    os.unlink(_tp)

    # the prompt must name real footprints, read from the live library
    menu = footprint_menu()
    from .parts import FOOTPRINTS as _FPS
    assert "SOIC8" in menu and "BARREL" in menu and "PINHD2X5" in menu
    assert all(f in menu for f in _FPS), "footprint menu is not the full library"

    # supplied context: note, document text, and answers to earlier questions
    assert context_block() == ""
    import tempfile as _tf
    with _tf.NamedTemporaryFile("w", suffix=".txt", delete=False) as _fh:
        _fh.write("MAX1234 regulator, pin 1 is VIN")
        _docp = _fh.name
    ctx = context_block("scope PSU board", [_docp], {"Voltage?": "12V"})
    assert "scope PSU board" in ctx and "MAX1234" in ctx, ctx
    assert "Q: Voltage?\nA: 12V" in ctx
    assert "believe the board" in ctx, "context must not outrank the photos"
    assert read_doc(_docp).startswith("MAX1234")
    assert read_doc(_docp, limit=10).endswith("truncated at 10 chars]")
    os.unlink(_docp)
    try:
        read_doc("/nonexistent/manual.pdf")
    except ValueError:
        pass
    else:
        raise AssertionError("read_doc accepted a missing file")
    print("pcbscan demo ok")


if __name__ == "__main__":
    demo()
