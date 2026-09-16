"""Benchmark the photo scan against a real board with a published schematic.

Fetches the NComputing L130 reverse-engineering project (real 3000x3000
photos of both sides, plus the KiCad schematic its author reconstructed),
simulates a 20-photo handheld shoot from that real board texture, and scores
what the pipeline recovers against ground truth.

Three numbers, because three different things can be wrong:

  registration  did each photo land in the right place?  (vs the exact
                transform used to synthesise it: rotation deg, scale %)
  3D geometry   does the height field rank real components by height, and
                does the splat carry that into a .ply? (pinhole render with
                a camera that moves, so parallax genuinely exists)
  stitch        is the composite closer to the real board than one photo?
                (mean |err| and NCC of bandpassed structure, plus the
                ceiling the same stitcher reaches with perfect transforms)
  enhancement   did the contrast stack make markings more legible?
                (median local contrast vs the raw stitch)

The handheld shoot is simulated rather than shot by hand for the only reason
that matters here: ground truth. Real handheld photos have no known
transform, so a lock could only be eyeballed, never measured. The board
texture, silkscreen, solder joints and copper are all real.

Usage:  python -m tools.scanbench [outdir]      (default /tmp/scanbench)
        SCANBENCH_LLM=1 also runs the vision analysis stage.
        SCANBENCH_REPS=n repeats each arm and reports medians (the analysis
        stage is noisy; n=1 cannot separate two arms).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Any

REPO = "https://raw.githubusercontent.com/gzalo/ncomputing-l130/main"
FILES = {"top.jpg": "docs/img/top.jpg", "bottom.jpg": "docs/img/bottom.jpg",
         "l130.kicad_sch": "pcb/l130.kicad_sch"}
SHOTS = 10          # per side
WORK = 1400         # working resolution of the simulated captures


def fetch(cache: str) -> bool:
    """Download the board photos + schematic once. False if offline."""
    os.makedirs(cache, exist_ok=True)
    for name, path in FILES.items():
        dest = os.path.join(cache, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            continue
        try:
            with urllib.request.urlopen(f"{REPO}/{path}", timeout=180) as r:
                data = r.read()
        except Exception as e:      # noqa: BLE001 - offline is a valid answer
            print(f"  cannot fetch {name}: {e}")
            return False
        with open(dest, "wb") as f:
            f.write(data)
        print(f"  fetched {name} ({len(data) // 1024} KB)")
    return True


def ground_truth(sch: str) -> list[tuple[str, str]]:
    """(ref, value) for every component in the published schematic."""
    import re
    s = open(sch).read()
    hits = re.findall(
        r'\(property "Reference" "([A-Z]+\d+)"[\s\S]{0,600}?'
        r'\(property "Value" "([^"]*)"', s)
    return sorted(set(hits))


def shoot(P: Any, cache: str, out: str,
          work: int = WORK) -> dict[str, dict[str, float]]:
    """Simulate a handheld shoot from the real photos: random scale,
    rotation and offset per frame, plus the nuisances that actually break
    registration — exposure swings, white balance drift, a specular glare
    blob, an occasional finger, and sensor noise."""
    import numpy as np
    rng = np.random.default_rng(42)
    os.makedirs(out, exist_ok=True)
    gt: dict[str, dict[str, float]] = {}
    for side in ("top", "bottom"):
        src = P.fit(P.load(os.path.join(cache, f"{side}.jpg")), work)
        h, w = src.shape[:2]
        for n in range(SHOTS):
            s = float(rng.uniform(0.65, 1.45))
            rot = float(rng.uniform(-30, 30))
            dx, dy = float(rng.uniform(-70, 70)), float(rng.uniform(-70, 70))
            v = np.nan_to_num(P.warp(src, s, rot, dx, dy, w, h), nan=12.0)
            v = v * float(rng.uniform(0.7, 1.35))
            v = v * np.array([rng.uniform(.9, 1.1), 1.0, rng.uniform(.9, 1.1)])
            gy, gx = np.mgrid[0:h, 0:w]
            cy, cx = rng.uniform(0, h), rng.uniform(0, w)
            v += 200 * np.exp(-(((gy - cy) ** 2 + (gx - cx) ** 2)
                                / (2 * (w * 0.16) ** 2)))[..., None]
            if n % 4 == 3:
                yy = int(rng.uniform(0, h - 120))
                v[yy:yy + 120, :180] = (190, 148, 128)
            v = np.clip(v + rng.normal(0, 6, v.shape), 0, 255)
            name = f"{side}_{n:02d}.png"
            P.write_png(os.path.join(out, name), v)
            gt[name] = {"scale": s, "rot": rot, "dx": dx, "dy": dy}
    return gt


def bench_3d(P: Any) -> list[str]:
    """Score the height field and splat against known component heights.

    The handheld shoot above warps a flat photograph, so it has no parallax
    by construction — it cannot test the 3D half at all. This renders a
    board with raised boxes through a pinhole camera that actually moves, so
    a face at height z shifts by z/(camz-z) times the camera offset, which
    is the signal the height field claims to invert.
    """
    import numpy as np
    rng = np.random.default_rng(11)
    h = w = 520
    board_mm, camz = 60.0, 420.0
    plane = np.zeros((h, w, 3), dtype=np.float32)
    plane[..., 0], plane[..., 1], plane[..., 2] = 24, 92, 46
    for i in range(16):
        y = 18 + i * 31
        plane[y:y + 6, int(rng.integers(20, 120)):int(rng.integers(330, 500))] = (
            196, 152, 64)
    plane = np.clip(plane + rng.normal(0, 3, plane.shape), 0, 255)
    mm_px = board_mm / w
    # (cy, cx, half_h, half_w, height_px)
    parts = [(110, 130, 34, 46, 46.0), (110, 380, 22, 30, 16.0),
             (330, 150, 26, 60, 30.0), (350, 400, 30, 34, 46.0)]

    def shoot(ox: float, oy: float) -> Any:
        im = plane.copy()
        for cy, cx, hh, hw, z in sorted(parts, key=lambda q: q[4]):
            sh, mg = z / (camz - z), camz / (camz - z)
            dy, dx = int(round(-oy * sh)), int(round(-ox * sh))
            ya, yb = int(cy - hh * mg) + dy, int(cy + hh * mg) + dy
            xa, xb = int(cx - hw * mg) + dx, int(cx + hw * mg) + dx
            ya, yb = max(0, ya), min(h, yb)
            xa, xb = max(0, xa), min(w, xb)
            if yb > ya and xb > xa:
                im[ya:yb, xa:xb] = (30, 30, 32)
                im[ya + 5:ya + 15, xa + 5:min(xb - 5, xa + 34)] = (228, 228, 224)
        return np.clip(im + rng.normal(0, 3, im.shape), 0, 255)

    views = [shoot(a, b) for a, b in ((0, 0), (-70, 0), (70, 0), (0, -60),
                                      (0, 60), (-50, 45), (55, -40))]
    rg = P.gray(views[0])
    xf = [{"scale": 1.0, "rot": 0.0, "dx": 0.0, "dy": 0.0, "peak": 1.0}]
    xf += [P.register(rg, P.gray(v)) for v in views[1:]]
    hm = P.height_field(views, xf, w, h)
    meas = [float(hm[cy - hh:cy + hh, cx - hw:cx + hw].mean())
            for cy, cx, hh, hw, _z in parts]
    true = [q[4] * mm_px for q in parts]
    pairs = [(i, j) for i in range(len(parts)) for j in range(i + 1, len(parts))
             if abs(true[i] - true[j]) > 0.5]
    ok = sum(1 for i, j in pairs
             if (true[i] < true[j]) == (meas[i] < meas[j]))
    bare = float(np.mean([hm[250:300, 40:120].mean(),
                          hm[430:500, 200:300].mean()]))
    rgb, _cov = P.stitch(views, xf, w, h)
    ply = P.splat_ply(rgb, hm, mm_px, max_mm=max(true))
    n = int(ply.split(b"element vertex ")[1].split(b"\n")[0])
    arr = np.frombuffer(ply.split(b"end_header\n", 1)[1],
                        dtype="<f4").reshape(n, 17)
    return [
        f"registration: {sum(1 for t in xf[1:] if t['peak'] > 0.3)}"
        f"/{len(xf) - 1} moved views locked",
        f"height ranking: {ok}/{len(pairs)} pairs correct "
        f"(true mm {[round(t, 1) for t in true]})",
        f"measured relief: parts {[round(m, 2) for m in meas]}, "
        f"bare board {bare:.3f}",
        f"splat: {n} gaussians, {arr[:, 0].max():.0f} mm board extent, "
        f"z 0..{arr[:, 2].max():.1f} mm",
    ]


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "/tmp/scanbench"
    cache, shots = os.path.join(root, "board"), os.path.join(root, "shoot")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from ocdcircuit import pcbscan as P
    except RuntimeError as e:
        print(f"scanbench needs numpy: {e}")
        return 1
    import numpy as np

    print(f"== fetching the reference board into {cache}")
    if not fetch(cache):
        print("offline - cannot run the benchmark")
        return 1
    gtc = ground_truth(os.path.join(cache, "l130.kicad_sch"))
    print(f"  schematic ground truth: {len(gtc)} components "
          f"({', '.join(r for r, _ in gtc[:6])}...)")

    print(f"== simulating {SHOTS * 2} handheld photos from the real board")
    gt = shoot(P, cache, shots)

    print("== registration")
    errs: list[tuple[float, float, float]] = []
    for side in ("top", "bottom"):
        paths = sorted(os.path.join(shots, f)
                       for f in os.listdir(shots) if f.startswith(side))
        imgs = [P.fit(P.load(p)) for p in paths]
        names = [os.path.basename(p) for p in paths]
        ref = int(max(range(len(imgs)),
                      key=lambda i: P.sharpness(P.gray(imgs[i]))))
        rg = P.gray(imgs[ref])
        for i, n in enumerate(names):
            if i == ref:
                continue
            t = P.register(rg, P.gray(imgs[i]))
            es = gt[names[ref]]["scale"] / gt[n]["scale"]
            er = (gt[names[ref]]["rot"] - gt[n]["rot"]) % 360
            dr = (t["rot"] - er) % 360
            dr = min(dr, 360 - dr)
            ds = abs(t["scale"] - es) / es
            errs.append((dr, ds, t["peak"]))
    ok = [e for e in errs if e[0] < 3 and e[1] < 0.05]
    print(f"  locked {len(ok)}/{len(errs)} "
          f"({100 * len(ok) / max(len(errs), 1):.0f}%)")
    print(f"  median error: {np.median([e[0] for e in ok]):.2f} deg, "
          f"{100 * np.median([e[1] for e in ok]):.2f}% scale")
    bad = [e[2] for e in errs if e not in ok]
    print(f"  score separation: good >= {min(e[2] for e in ok):.2f}, "
          f"bad <= {max(bad):.2f}" if bad and ok else "  (no failures)")

    print("== stitch (top side)")
    paths = sorted(os.path.join(shots, f)
                   for f in os.listdir(shots) if f.startswith("top"))
    imgs = [P.fit(P.load(p)) for p in paths]
    names = [os.path.basename(p) for p in paths]
    ref = int(max(range(len(imgs)), key=lambda i: P.sharpness(P.gray(imgs[i]))))
    order = [ref] + [i for i in range(len(imgs)) if i != ref]
    ordered = [imgs[i] for i in order]
    h, w = ordered[0].shape[:2]
    rg = P.gray(ordered[0])
    xf = [{"scale": 1.0, "rot": 0.0, "dx": 0.0, "dy": 0.0, "peak": 1.0}]
    xf += [P.register(rg, P.gray(im)) for im in ordered[1:]]

    truth = P.fit(P.load(os.path.join(cache, "top.jpg")), WORK)
    g = gt[names[ref]]
    expect = np.nan_to_num(P.warp(truth, g["scale"], g["rot"], g["dx"],
                                  g["dy"], w, h), nan=0.0)
    mask = expect.sum(2) > 1

    def quality(x: Any) -> tuple[float, float]:
        err = float(np.mean(np.abs(x[mask] - expect[mask])))
        a = P.bandpass(P.gray(x))[mask]
        b = P.bandpass(P.gray(expect))[mask]
        a, b = a - a.mean(), b - b.mean()
        return err, float(a @ b / np.sqrt((a @ a) * (b @ b)))

    e1, n1 = quality(np.clip(ordered[0], 0, 255).astype(np.float32))
    keep = [i for i, t in enumerate(xf) if t["peak"] >= 0.30]
    st, cover = P.stitch([ordered[i] for i in keep], [xf[i] for i in keep], w, h)
    e2, n2 = quality(st.astype(np.float32))
    print(f"  single photo : mean|err| {e1:5.1f}  structNCC {n1:.3f}")
    print(f"  gated stitch : mean|err| {e2:5.1f}  structNCC {n2:.3f}  "
          f"({len(keep)}/{len(xf)} frames, coverage {cover.mean():.2f})")
    print(f"  -> error {e1 / max(e2, 1e-6):.1f}x lower than one photo")

    print("== 3D geometry (height field + splat, pinhole render)")
    for line in bench_3d(P):
        print("  " + line)

    print("== enhancement (median local contrast, 16px tiles)")

    def locstd(x: Any) -> float:
        hh, ww = x.shape[0] // 16, x.shape[1] // 16
        v = x[:hh * 16, :ww * 16].reshape(hh, 16, ww, 16)
        return float(np.median(v.transpose(0, 2, 1, 3).reshape(hh, ww, 256)
                               .std(axis=2)))

    raw = locstd(P.gray(st))
    for k, im in P.enhance(st).items():
        print(f"  {k:9s} {locstd(P.gray(im)):6.2f}  ({locstd(P.gray(im)) / raw:.1f}x raw)")

    if os.environ.get("SCANBENCH_LLM"):
        print("== vision analysis (SCANBENCH_LLM set)")
        photos = sorted(os.path.join(shots, f) for f in os.listdir(shots)
                        if f.endswith(".png"))
        refs = [r for r, _ in gtc]
        manual = os.path.join(root, "manual.txt")
        with open(manual, "w") as f:      # the "datasheet" a user would upload
            f.write("NComputing L130 thin client - service notes\n\n"
                    + "\n".join(f"  {r}  {v}" for r, v in gtc)
                    + "\n\nEthernet-attached thin client: FPGA does video and\n"
                      "USB, PHY does the network, DataFlash holds the FPGA\n"
                      "configuration, SDRAM is the framebuffer.\n")

        def cast_list(v: object) -> list[object]:
            return list(v) if isinstance(v, list) else []

        def cast_paths(v: object) -> list[str] | None:
            return [str(x) for x in v] if isinstance(v, list) else None

        reps = int(os.environ.get("SCANBENCH_REPS", "1"))

        def once(tag: str, n: int,
                 **kw: Any) -> tuple[int, int, int, int, int] | None:
            out = os.path.join(root, f"scan-{tag}" + (f"-{n}" if n else ""))
            shots_in = cast_paths(kw.pop("photos", None)) or photos
            try:
                r = P.reverse(shots_in, out, board_mm=100.0, **kw)
            except Exception as e:        # noqa: BLE001 - report, keep going
                print(f"  {tag:10s} FAILED: {type(e).__name__}: {e}")
                return None
            rep = open(str(r["analysis"])).read() if "analysis" in r else ""
            found = [x for x in refs if x in rep]
            vals = [v for _, v in gtc
                    if v and v.split()[0][:6].upper() in rep.upper()]
            traced = sum(1 for ln in rep.splitlines()
                         if "<-->" in ln and "guess" not in ln.lower())
            ok = 1 if r.get("draft_parts") else 0
            # board size error in mm: the real L130 is ~100 mm across, and
            # this is the axis measured pad geometry actually moves.
            dim = 99
            if isinstance(r.get("draft"), str) and os.path.isfile(str(r["draft"])):
                import re as _re
                m = _re.search(r"board\s+\S+\s+([\d.]+)x([\d.]+)",
                               open(str(r["draft"])).read())
                if m:
                    dim = int(max(abs(float(m.group(1)) - 100.0),
                                  abs(float(m.group(2)) - 100.0)))
            return len(found), len(vals), traced, ok, dim

        def score(tag: str, **kw: Any) -> None:
            """Recall of the schematic's reference designators, the parts it
            names, and connections it traced without hedging.

            Reported as a median over SCANBENCH_REPS runs: a single run of
            this is noisy enough to invert an arm ordering (measured: the
            same arm, same inputs, scored 3 and 15 traced connections on two
            consecutive runs), so one number per arm cannot support a claim.
            """
            t0 = time.time()
            got = [g for g in (once(tag, i if reps > 1 else 0, **dict(kw))
                               for i in range(reps)) if g]
            if not got:
                return
            def med(ix: int) -> float:
                col = sorted(g[ix] for g in got)
                mid = len(col) // 2
                return (col[mid] if len(col) % 2 else
                        (col[mid - 1] + col[mid]) / 2)
            spread = ""
            if reps > 1:
                spread = ("  [refs " + "/".join(str(g[0]) for g in got)
                          + ", traced " + "/".join(str(g[2]) for g in got) + "]")
            print(f"  {tag:10s} {time.time() - t0:5.0f}s  "
                  f"refs {med(0):4.1f}/{len(refs)}  parts {med(1):4.1f}/{len(gtc)}"
                  f"  traced {med(2):4.1f}  size-err {med(4):4.1f}mm"
                  f"  drafts-parse {int(sum(g[3] for g in got))}/{len(got)}"
                  f"{spread}")

        print(f"  ground truth: {len(refs)} refs, "
              f"model={os.environ.get('OCD_LLM_MODEL', '?')}")
        score("bare", zoom=1)
        score("noted", note="Ethernet thin client, VGA out, pulled from a dead unit.",
              zoom=1)
        score("manual", note="Ethernet thin client.", docs=[manual], zoom=1)
        # zoom tiles are the only images where a trace is more than a couple
        # of pixels wide, so they are scored as their own arm
        # zoom needs pixels to zoom into: the simulated captures above are
        # WORK px, so this arm re-shoots from the full-resolution originals.
        # Scoring zoom against down-sampled captures measures nothing.
        hires = os.path.join(root, "shoot-hi")
        if not os.path.isdir(hires):
            shoot(P, cache, hires, work=2600)
        hi_photos = sorted(os.path.join(hires, f) for f in os.listdir(hires)
                           if f.endswith(".png"))
        score("zoomed", note="Ethernet thin client.", docs=[manual],
              zoom=2, maxdim=2400, photos=hi_photos)
        # does the measured pad geometry change the board dimensions and
        # footprints the model commits to? scored by how close the drafted
        # board size lands to the real 100 mm and whether drafts still load.
        # isolate the measured-geometry contribution: same photos, same
        # context, pad measurements withheld from the prompt.
        score("no-pads", note="Ethernet thin client.", docs=[manual],
              zoom=2, maxdim=2400, photos=hi_photos, pads=False)
    print("\nbenchmark done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
