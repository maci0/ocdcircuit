"""Studio CI smoke gate: boot, build budget, endpoints, headless render.

Stdlib only (asserts, no framework): subprocess + urllib + zlib PNG parse.
Run: python tests/test_studio.py  (also wired into `make test`).
Skips the browser half when no chromium binary is found (server half still runs).
"""
from __future__ import annotations
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOARD = os.path.join(ROOT, "boards", "blinky_555.ocd")
BUILD_BUDGET = 0.5  # seconds; measured ~0.07 local, headroom for loaded CI
PCB_BRIGHT_MIN = 0.02  # healthy shot = 0.08, black-PCB shot = 0.0000


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def post(base: str, path: str, body: dict[str, object]) -> dict[str, object]:
    q = urllib.request.Request(base + path, json.dumps(body).encode(),
                               {"Content-Type": "application/json"})
    out = urllib.request.urlopen(q, timeout=30).read()
    res = json.loads(out)
    assert isinstance(res, dict)
    return res


def png_brightness(path: str, x0: int, y0: int, x1: int, y1: int,
                   thr: int = 80) -> tuple[float, int, int]:
    """Bright-pixel fraction in a crop. Minimal PNG reader (non-interlaced
    truecolor); raises on anything else — screenshots are that shape."""
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, w, h, ctype, idat = 8, 0, 0, 0, b""
    while pos < len(d):
        (ln,) = struct.unpack(">I", d[pos:pos + 4])
        typ = d[pos + 4:pos + 8]
        if typ == b"IHDR":
            w, h, _bd, ctype, _cp, _fl, _iv = struct.unpack(">IIBBBBB", d[pos + 8:pos + 21])
            assert ctype in (2, 6), f"ctype {ctype}"
        elif typ == b"IDAT":
            idat += d[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    ch = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = w * ch
    n = t = 0
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        prev = line
        if y0 <= y < y1:
            for x in range(x0, x1):
                o = x * ch  # line is the current row, not the image
                t += 1
                if max(line[o:o + 3]) > thr:
                    n += 1
    # walk remaining rows without storing the whole image
    return (n / t if t else 0.0), w, h


def main() -> None:
    port = free_port()
    base = f"http://localhost:{port}"
    env = dict(os.environ, OCD_PORT=str(port))
    board_orig = open(BOARD).read()  # studio saves to disk; restore after
    srv = subprocess.Popen([sys.executable, "-m", "apps.studio", BOARD],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/slots", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("studio did not boot")

        text = open(BOARD).read()
        t = time.time()
        d = post(base, "/build", {"text": text, "placer": "diffusion",
                                 "router": "maze"})
        dt = time.time() - t
        assert not d.get("error"), d.get("error")
        assert dt < BUILD_BUDGET, f"quick build {dt:.2f}s over {BUILD_BUDGET}s"
        from typing import cast
        parts = cast(dict[str, object], d["parts"])
        score = cast(dict[str, object], d["score"])
        lint = cast(dict[str, object], d["lint"])
        assert len(parts) == 10, len(parts)
        assert cast(float, score["total"]) > 0, score
        assert isinstance(lint["errors"], list), lint
        print(f"build {dt:.2f}s score={score['total']} ok")

        for key in ("svg", "sch"):
            r = post(base, "/render", {"key": key})
            assert not r.get("error"), (key, r.get("error"))
            assert len(cast(str, r["data"])) > 1000, (key, len(cast(str, r["data"])))
        print("render svg+sch ok")

        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})
        import re
        m = re.search(r"fix (\S+) at ([\d.]+) ([\d.]+)", text)
        assert m, "blinky needs a fix line for the diff check"
        moved = text.replace(m.group(0),
                             f"fix {m.group(1)} at {float(m.group(2)) + 1} {m.group(3)}", 1)
        d2 = post(base, "/build", {"text": moved, "placer": "diffusion",
                                   "router": "maze"})
        assert not d2.get("error"), d2.get("error")
        dd = post(base, "/diff_prev", {})
        assert m.group(1) in str(dd.get("diff")), dd
        print("diff_prev ok:", dd["diff"])

        no_sim = post(base, "/simulate", {"what": "dc"})
        assert "error" in no_sim, no_sim  # blinky has no sim lines
        rc = ("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
              "net VIN: R1.1\nnet VO: R1.2 C1.1\nnet GND: C1.2\n"
              "sim vcc VIN 0 5\nsim tran 0.005 500\nsim probe VO\n")
        post(base, "/build", {"text": rc, "placer": "diffusion",
                              "router": "maze"})
        dc = post(base, "/simulate", {"what": "dc"})
        assert not dc.get("error"), dc
        tran = cast(dict[str, object], post(base, "/simulate", {"what": "tran"})["tran"])
        tr = cast(list[object], tran["VO"])
        assert len(tr) == 500 and abs(cast(float, tr[-1]) - 5.0) < 0.05, tr[-3:]
        print(f"simulate dc+tran ok (VO final={tr[-1]}V)")
        # rebuild blinky: /build saves to disk, screenshot must see blinky
        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})

        pico = open(os.path.join(ROOT, "boards", "pico_tmc2209",
                                 "pico_tmc2209.ocd")).read()
        dp = post(base, "/build", {"text": pico, "placer": "compact",
                                   "router": "lroute"})
        assert not dp.get("error"), dp.get("error")
        pparts = cast(dict[str, dict[str, object]], dp["parts"])
        owners = {str(p.get("owner", "")) for p in pparts.values()}
        assert {"Z1_", "Z2_", "Z3_"} <= owners, owners  # instance groups exposed
        print(f"instances ok (owners={sorted(o for o in owners if o)})")
        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})

        chrom = (shutil.which("chromium") or shutil.which("chromium-browser")
                 or shutil.which("google-chrome") or shutil.which("chrome"))
        if not chrom:
            print("no chromium: browser half skipped")
            return
        with tempfile.TemporaryDirectory() as td:
            shot = os.path.join(td, "shot.png")
            log = os.path.join(td, "console.log")
            with open(log, "wb") as lf:
                p = subprocess.run(
                    [chrom, "--headless=new", "--no-sandbox", "--disable-gpu",
                     f"--window-size=1280,900", "--virtual-time-budget=12000",
                     f"--screenshot={shot}", "--enable-logging=stderr",
                     base + "/"], capture_output=False, stderr=lf, timeout=120)
            assert os.path.isfile(shot), "no screenshot"
            frac, w, h = png_brightness(shot, 420, 120, 860, 750)
            assert (w, h) == (1280, 900), (w, h)
            assert frac > PCB_BRIGHT_MIN, f"PCB black? bright={frac:.4f}"
            errlog = open(log, "rb").read().decode("utf8", "replace")
            bad = [ln for ln in errlog.splitlines()
                   if "CONSOLE" in ln and ("Uncaught" in ln or "ERROR" in ln)]
            assert not bad, bad[:3]
            print(f"screenshot pcb-bright={frac:.4f} console-clean ok")
    finally:
        srv.terminate()
        with open(BOARD, "w") as f:
            f.write(board_orig)
    print("STUDIO OK")


if __name__ == "__main__":
    main()
